"""The operations surface (dead-letter replay, cohort cutover, notification preferences) and the worker cycle."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from app.application.operations_worker import run_cycle
from app.application.outbox_delivery import OutboxDelivery
from app.application.records import OutboxRecord
from app.observability.metrics import METRICS
from test_p6_amendment_api import C, client_for
from test_pdf_cohort_cutover import SNAP, reconciled  # noqa: F401  (fixture)
from test_uctsm_authz_http import FakeOperationalAccess
from test_uctsm_p1_api import make_env
from _p6_world import World

pytest_plugins = ["tests.contract.conftest"]
NOW = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)


def fresh():
    """A snapshot report taken an hour ago, on the real clock the HTTP service uses."""
    return {**SNAP, "created_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
BASE = "/api/uctsm/mongo-plane"


def make_dead(uow, org):
    row = OutboxRecord(organization_id=org, aggregate_type="PATIENT", aggregate_id=uuid4(), event_type="X",
                       dedupe_key=uuid4().hex, created_at=NOW, max_attempts=1)
    uow.run(lambda tx: tx.outbox.add(row))

    def boom(_):
        raise RuntimeError("patient payload must not leak")
    assert OutboxDelivery(uow, clock=lambda: NOW).run_once(boom)["status"] == "DEAD"
    return row


def test_dead_letters_are_listed_and_replayed_only_with_the_grant_and_a_reason(uow):
    org = uuid4(); row = make_dead(uow, org)
    with client_for(uow, org, [], role="pi") as nobody:
        assert nobody.get(f"{BASE}/operations/dead-letters").status_code == 403
        assert nobody.post(f"{BASE}/operations/dead-letters/{row.id}/replay", json={"reason": "retry"}).status_code == 403
    with client_for(uow, org, [C.OUTBOX_REPLAY], role="pi") as operator:
        listed = operator.get(f"{BASE}/operations/dead-letters").json()["messages"]
        assert [item["id"] for item in listed] == [str(row.id)] and "payload" not in str(listed)
        assert operator.post(f"{BASE}/operations/dead-letters/{row.id}/replay", json={"reason": "x"}).status_code == 422
        assert operator.post(f"{BASE}/operations/dead-letters/{row.id}/replay", json={"reason": "provider fixed", "actor": "x"}).status_code == 422
        done = operator.post(f"{BASE}/operations/dead-letters/{row.id}/replay", json={"reason": "provider fixed"})
        assert done.status_code == 200 and done.json()["status"] == "PENDING"
        assert operator.post(f"{BASE}/operations/dead-letters/{row.id}/replay", json={"reason": "again please"}).status_code == 404
        assert operator.get(f"{BASE}/operations/dead-letters", params={"limit": 0}).status_code == 422
    with client_for(uow, uuid4(), [C.OUTBOX_REPLAY], role="pi") as stranger:
        assert stranger.get(f"{BASE}/operations/dead-letters").json()["messages"] == []


def test_cohort_cutover_over_http_needs_the_grant_two_people_and_ready_members(reconciled):  # noqa: F811
    world, _ = reconciled
    base = f"{BASE}/trials/{world.trial_id}/cohorts"
    maker, checker = uuid4(), uuid4()
    body = {"name": "pilot-http", "external_patient_ids": ["LEG-1"]}
    with client_for(world.uow, world.org, [], role="sponsor", user_id=maker) as denied:
        assert denied.post(base, json=body).status_code == 403
    with client_for(world.uow, world.org, [C.COHORT_CUTOVER], role="sponsor", user_id=maker) as one:
        bad = one.post(base, json={**body, "external_patient_ids": ["NOT-MIGRATED"]})
        assert bad.status_code == 409 and bad.json()["detail"]["code"] == "COHORT_MEMBERS_INVALID"
        created = one.post(base, json=body)
        assert created.status_code == 200 and created.json()["state"] == "SHADOW"
        cohort = created.json()["id"]
        assert one.get(f"{base}/{cohort}/readiness").json()["ready"] is True
        same = one.post(f"{base}/{cohort}/promote", json={"reason": "self approval", "snapshot": fresh()})
        assert same.status_code == 409 and same.json()["detail"]["code"] == "MAKER_CHECKER_SAME_PERSON"
        assert one.post(f"{base}/{cohort}/promote", json={"reason": "no snapshot given"}).status_code == 422
        assert one.post(base.replace(str(world.trial_id), str(uuid4())), json=body).status_code == 404
    with client_for(world.uow, world.org, [C.COHORT_CUTOVER], role="sponsor", user_id=checker) as two:
        promoted = two.post(f"{base}/{cohort}/promote", json={"reason": "parity matched and approved", "snapshot": fresh()})
        assert promoted.status_code == 200 and promoted.json()["state"] == "PILOT"
        rolled = two.post(f"{base}/{cohort}/rollback", json={"reason": "rehearsal"})
        assert rolled.status_code == 200 and rolled.json()["state"] == "ROLLED_BACK"


def test_notification_preferences_are_recorded_for_a_linked_patient_and_validated(uow):
    env = make_env(uow)
    access = FakeOperationalAccess()
    url = f"{BASE}/trials/{env.trial_id}/patients/{env.external_patient_id}/notification-preferences"
    client = client_for(uow, env_org := __import__("test_uctsm_p1_occurrence_recalculation", fromlist=["ORG"]).ORG,
                        [], role="pi", access=access)
    access.link({"id": str(client.user_id), "role": "pi"}, patient=env.external_patient_id)
    good = {"consent": "GRANTED", "timezone": "Europe/London", "delivery_time": "09:00", "lead_days": 2}
    assert client.get(url).json() == {"preferences": None}
    for bad in ({**good, "timezone": "Mars/Base"}, {**good, "delivery_time": "9am"}, {**good, "lead_days": 99},
                {**good, "quiet_start": "08:00"}, {**good, "extra": 1}):
        assert client.put(url, json=bad).status_code == 422
    saved = client.put(url, json=good)
    assert saved.status_code == 200 and saved.json() == {"consent": "GRANTED", "revision": 1}
    assert client.put(url, json={**good, "consent": "WITHDRAWN"}).json()["revision"] == 2
    assert client.get(url).json()["preferences"]["consent"] == "WITHDRAWN"
    stranger = client_for(uow, env_org, [], role="pi", access=FakeOperationalAccess())
    assert stranger.put(url, json=good).status_code in {403, 404}


def test_the_worker_cycle_syncs_granted_patients_isolates_failures_and_publishes_gauges(uow):
    from app.application.reminder_sync import ReminderSyncService
    world = World(uow)
    first, second = world.patient_ids[0], world.patient_ids[1]
    service = ReminderSyncService(uow)
    for patient in (first, second):
        service.record_preferences(world.org, patient, actor_id=world.pi, consent="GRANTED",
                                   timezone_name="Europe/London", delivery_time="09:00", lead_days=1)
    summary = run_cycle(uow, now=NOW, today=date(2026, 1, 1))
    assert summary["patients"] == 2 and summary["created"] > 0 and summary["failed"] == 0
    again = run_cycle(uow, now=NOW, today=date(2026, 1, 1))
    assert again["created"] == 0 and again["withdrawn"] == 0              # idempotent
    assert METRICS.queue_depth.value(queue="outbox", state="pending") >= summary["created"]
    delivered = []
    final = run_cycle(uow, now=NOW, today=date(2026, 1, 1), deliver=delivered.append)
    assert final["delivered"] >= summary["created"] and len(delivered) >= summary["created"]
    assert METRICS.queue_depth.value(queue="outbox", state="pending") == 0


def test_one_failing_patient_is_counted_and_does_not_stop_the_others(uow):
    from unittest.mock import patch
    from app.application.reminder_sync import ReminderSyncService
    world = World(uow)
    service = ReminderSyncService(uow)
    for patient in world.patient_ids[:2]:
        service.record_preferences(world.org, patient, actor_id=world.pi, consent="GRANTED",
                                   timezone_name="Europe/London", delivery_time="09:00", lead_days=1)
    real = ReminderSyncService.sync
    broken = min(world.patient_ids[:2], key=str)

    def flaky(self, organization_id, patient_id, **kwargs):
        if patient_id == broken:
            raise KeyError("synthetic failure with a patient name inside")
        return real(self, organization_id, patient_id, **kwargs)
    before = METRICS.job_failures.value(job="reminder_sync", code="KeyError")
    with patch.object(ReminderSyncService, "sync", flaky):
        summary = run_cycle(uow, now=NOW, today=date(2026, 1, 1))
    assert summary["failed"] == 1 and summary["patients"] == 2 and summary["created"] > 0
    assert METRICS.job_failures.value(job="reminder_sync", code="KeyError") == before + 1
