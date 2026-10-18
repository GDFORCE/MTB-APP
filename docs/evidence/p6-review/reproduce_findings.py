"""Read-only source audit reproductions; writes only isolated synthetic test databases.

Run from the repository root:
    python docs/evidence/p6-review/reproduce_findings.py --store both

These assertions document the defects PRESENT on 2026-10-16. They are not acceptance
tests and must be converted into assertions of safe behaviour when the defects are fixed.
The HTTP case substitutes authentication/access with a provider granting ONLY trial A.
Mongo uses the project's own client, migrations and unit of work on a local replica set.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]

from _p6_world import HORIZON, TODAY, World
from test_p6_approval_hash import Draft, REVIEWER
from test_p6_amendment_api import C, client_for
from app.api.uctsm_amendment_worker import process_due_once
from app.application.amendment_plan import trigger_state
from app.application.authoring import TrialRecord
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint
from app.domain.schedule.models import OccurrenceFactKind
from app.persistence.memory import InMemoryUnitOfWork


@contextmanager
def isolated_uow(store):
    if store == "memory":
        yield InMemoryUnitOfWork()
        return
    from app.persistence.mongodb.client import build_client
    from app.persistence.mongodb.migrate import run_migrations
    from app.persistence.mongodb.settings import MongoSettings, require_local
    from app.persistence.mongodb.unit_of_work import MongoUnitOfWork

    name = "uctsm_p6_review_" + uuid4().hex[:12]
    settings = require_local(MongoSettings(
        url="mongodb://127.0.0.1:27017/?replicaSet=rs0", database=name))
    client = build_client(settings, server_selection_timeout_ms=2000)
    try:
        client.admin.command("ping")
        assert client.admin.command("hello").get("setName"), "Replica set required"
        run_migrations(client[name])
        yield MongoUnitOfWork(client, client[name])
    finally:
        # Only this invocation's explicitly named random database is removed.
        client.drop_database(name)
        client.close()


def cross_trial_writes(uow):
    org = uuid4()
    forbidden = TrialRecord(organization_id=org, external_trial_id="forbidden-B")
    uow.run(lambda tx: tx.authoring.add_trial(forbidden))
    parent = Draft(uow, org=org, trial=forbidden)
    uow.run(lambda tx: tx.schedules.mark_approved(
        parent.version, reviewer_id=REVIEWER,
        approved_at=datetime.now(timezone.utc), effective_from=None))
    child = Draft(uow, org=parent.org, trial=parent.trial,
                  protocol=parent.protocol, label="2.0")
    allowed = TrialRecord(organization_id=parent.org, external_trial_id="allowed-A")
    uow.run(lambda tx: tx.authoring.add_trial(allowed))

    class OnlyTrialA:
        async def can_access_trial(self, user, external):
            return external == "allowed-A"

        async def can_access_patient(self, user, external):
            return False

        async def is_own_patient_record(self, user, external):
            return False

    with client_for(uow, parent.org, [C.SCHEDULE_READ, C.SCHEDULE_AUTHOR, C.SCHEDULE_REVIEW],
                    role="sponsor", access=OnlyTrialA()) as client:
        base = f"/api/uctsm/mongo-plane/trials/{allowed.id}/amendments"
        direct_status = client.get(
            f"/api/uctsm/mongo-plane/trials/{forbidden.id}/amendments").status_code
        assert direct_status == 404
        reply = client.post(base + "/lineage", json={
            "schedule_version_id": str(child.version),
            "based_on_schedule_version_id": str(parent.version),
            "reason": "wrong URL trial"})
        changed = child.header().based_on_schedule_version_id == parent.version
        assert reply.status_code == 201 and changed
        uow.run(lambda tx: tx.schedules.mark_approved(
            child.version, reviewer_id=REVIEWER,
            approved_at=datetime.now(timezone.utc), effective_from=None))
        pair_reply = client.post(base + "/pairs", json={
            "from_schedule_version_id": str(parent.version),
            "to_schedule_version_id": str(child.version)})
        written = uow.read(lambda tx: tx.amendments.find_pair(
            parent.org, parent.version, child.version)) is not None
        assert pair_reply.status_code == 404 and written
        return {"direct_other_trial_status": direct_status,
                "lineage_status": reply.status_code, "other_trial_lineage_changed": changed,
                "pair_status": pair_reply.status_code, "pair_written_despite_404": written}


def deferred_preview_commit(uow):
    world = World(uow, reviewed=True)
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1))
    preview = world.simulate(point=point)
    decision = world.confirm(preview)
    shown = next(row for row in preview["visits"] if row["to_key"] == "C1D1:0")
    result = world.adoption.execute(
        organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]),
        as_of=date(2027, 3, 1))
    committed = next(row for row in world.events() if row.logical_key == "C1D1:0")
    assert result["status"] == "APPLIED"
    assert shown["new_target"] == "2026-01-11"
    assert committed.nominal_start_date == date(2026, 1, 8)
    return {"preview_target": shown["new_target"],
            "committed_target": str(committed.nominal_start_date),
            "committed_protected": committed.protected_history}


def trigger_withdrawn_after_claim(uow):
    world = World(uow, reviewed=True)
    patient_id = world.patient_ids[0]
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT,
                          event_code="C1D1", occurrence_index=0)
    decision = world.confirm(world.simulate(point=point))
    decision_id = UUID(decision["id"])
    performed = world.record(patient_id, "C1D1:0", date(2026, 1, 8))
    claim = world.adoption._run(lambda tx: world.adoption._claim(
        tx, world.org, decision_id, TODAY))
    world.patients.record_actual_occurrence(
        patient_id, organization_id=world.org, logical_key="C1D1:0",
        kind=OccurrenceFactKind.MISSED, effective_at=date(2026, 1, 8),
        supersedes_occurrence_id=performed.occurrence_id, actor_id=world.pi,
        reason="visit was actually missed", horizon=HORIZON, today=TODAY)
    current_trigger = uow.read(lambda tx: trigger_state(
        tx, world.patient(patient_id), tx.schedules.get(world.v1), world.v1, point, TODAY))
    result = world.adoption._run(lambda tx: world.adoption._execute_claimed(
        tx, world.org, world.pi, decision_id, claim["lease_token"], TODAY, ()))
    moved = world.patient().current_schedule_version_id == world.v2
    assert current_trigger["due"] is False and result["status"] == "APPLIED" and moved
    return {"trigger_due_after_correction": current_trigger["due"],
            "execution_status": result["status"], "patient_moved_to_v2": moved}


def worker_starvation(uow):
    world = World(uow, reviewed=True)
    for patient_id, on in zip(world.patient_ids, [date(2099, 1, 1), date(2027, 3, 1)]):
        world.confirm(world.simulate(patient_id=patient_id, point=AdoptionPoint(
            mode=AdoptionMode.ADOPT_ON_DATE, on_date=on)))

    async def authorised(decision, patient):
        return True

    passes = [asyncio.run(process_due_once(
        uow, authorised, as_of=date(2027, 3, 1), limit=1)) for _ in range(2)]
    statuses = [[row["status"] for row in rows] for rows in passes]
    still_waiting = world.patient(world.patient_ids[1]).current_schedule_version_id == world.v1
    assert statuses == [["WAITING"], ["WAITING"]] and still_waiting
    return {"batch_limit": 1, "pass_statuses": statuses, "due_patient_still_on_v1": still_waiting,
            "note": "Small batch reproduces the same prefix starvation as the default batch of 100."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", choices=["memory", "mongo", "both"], default="both")
    args = parser.parse_args()
    for store in (["memory", "mongo"] if args.store == "both" else [args.store]):
        for name, reproduce in [
            ("F1_cross_trial_writes", cross_trial_writes),
            ("F2_deferred_preview_commit", deferred_preview_commit),
            ("F3_trigger_withdrawn_after_claim", trigger_withdrawn_after_claim),
            ("F4_worker_starvation", worker_starvation),
        ]:
            with isolated_uow(store) as uow:
                print(json.dumps({"store": store, "finding": name, **reproduce(uow)}), flush=True)


if __name__ == "__main__":
    main()
