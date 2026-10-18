"""P6 repairs F1-F4 as safe-behaviour regressions (memory fake AND real MongoDB).

They replace the defect-documenting assertions of ``docs/evidence/p6-review/reproduce_findings.py``
(kept there as the historical reproduction)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.api.uctsm_amendment_worker import process_due_once
from app.application.amendment_plan import trigger_state
from app.application.authoring import TrialRecord
from app.application.errors import StaleRevisionError
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint
from app.domain.schedule.models import OccurrenceFactKind

from _p6_world import HORIZON, TODAY, World
from test_p6_amendment_api import C, client_for
from test_p6_approval_hash import Draft, REVIEWER

pytest_plugins = ["tests.contract.conftest"]

MARCH = date(2027, 3, 1)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def yes(decision, patient):
    return True


def on_date(day):
    return AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=day)


def at_event():
    return AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="C1D1", occurrence_index=0)


@pytest.fixture()
def world(uow):
    return World(uow, reviewed=True)


# ------------------------------------------------------------------------------- F1
class OnlyTrialA:
    async def can_access_trial(self, user, external):
        return external == "allowed-A"

    async def can_access_patient(self, user, external):
        return False

    async def is_own_patient_record(self, user, external):
        return False


def audit_count(uow, org):
    return len(uow.read(lambda tx: tx.audit.list_for_organization(org, limit=500)))


def other_trial_setup(uow):
    org = uuid4()
    forbidden = TrialRecord(organization_id=org, external_trial_id="forbidden-B")
    uow.run(lambda tx: tx.authoring.add_trial(forbidden))
    parent = Draft(uow, org=org, trial=forbidden)
    uow.run(lambda tx: tx.schedules.mark_approved(
        parent.version, reviewer_id=REVIEWER,
        approved_at=datetime.now(timezone.utc), effective_from=None))
    child = Draft(uow, org=parent.org, trial=parent.trial, protocol=parent.protocol, label="2.0")
    allowed = TrialRecord(organization_id=parent.org, external_trial_id="allowed-A")
    uow.run(lambda tx: tx.authoring.add_trial(allowed))
    return forbidden, allowed, parent, child


def test_a_lineage_request_cannot_write_another_trials_versions(uow):
    forbidden, allowed, parent, child = other_trial_setup(uow)
    with client_for(uow, parent.org, [C.SCHEDULE_READ, C.SCHEDULE_AUTHOR, C.SCHEDULE_REVIEW],
                    role="sponsor", access=OnlyTrialA()) as client:
        assert client.get(f"/api/uctsm/mongo-plane/trials/{forbidden.id}/amendments").status_code == 404
        before = audit_count(uow, parent.org)
        reply = client.post(f"/api/uctsm/mongo-plane/trials/{allowed.id}/amendments/lineage", json={
            "schedule_version_id": str(child.version),
            "based_on_schedule_version_id": str(parent.version), "reason": "wrong URL trial"})
        assert reply.status_code == 404
        assert child.header().based_on_schedule_version_id is None          # nothing written
        assert audit_count(uow, parent.org) == before


def test_a_pair_request_cannot_write_another_trials_comparison_even_on_replay(uow):
    forbidden, allowed, parent, child = other_trial_setup(uow)
    # the other trial's own legitimate work, done by someone entitled to it
    uow.run(lambda tx: tx.schedules.set_lineage(child.version, parent.version))
    uow.run(lambda tx: tx.schedules.mark_approved(
        child.version, reviewer_id=REVIEWER, approved_at=datetime.now(timezone.utc),
        effective_from=None))
    body = {"from_schedule_version_id": str(parent.version),
            "to_schedule_version_id": str(child.version)}
    url = f"/api/uctsm/mongo-plane/trials/{allowed.id}/amendments/pairs"
    with client_for(uow, parent.org, [C.SCHEDULE_READ, C.SCHEDULE_AUTHOR, C.SCHEDULE_REVIEW],
                    role="sponsor", access=OnlyTrialA()) as client:
        before = audit_count(uow, parent.org)
        assert client.post(url, json=body).status_code == 404
        assert uow.read(lambda tx: tx.amendments.find_pair(
            parent.org, parent.version, child.version)) is None            # no pair, no mapping set
        assert audit_count(uow, parent.org) == before
    # a pair that already exists is not disclosed to a caller scoped to another trial either
    from app.application.amendment_service import AmendmentService
    AmendmentService(uow).prepare_pair(
        organization_id=parent.org, actor_id=REVIEWER, from_schedule_version_id=parent.version,
        to_schedule_version_id=child.version, expected_trial_id=forbidden.id)
    with client_for(uow, parent.org, [C.SCHEDULE_READ, C.SCHEDULE_AUTHOR, C.SCHEDULE_REVIEW],
                    role="sponsor", access=OnlyTrialA()) as client:
        assert client.post(url, json=body).status_code == 404


def test_the_service_refuses_versions_of_a_different_trial_before_any_write(uow):
    forbidden, allowed, parent, child = other_trial_setup(uow)
    from app.application.amendment_service import AmendmentService
    with pytest.raises(KeyError):
        AmendmentService(uow).declare_lineage(
            organization_id=parent.org, actor_id=REVIEWER, schedule_version_id=child.version,
            based_on_schedule_version_id=parent.version, reason="not this trial",
            expected_trial_id=allowed.id)
    assert child.header().based_on_schedule_version_id is None


# ------------------------------------------------------------------------------- F2
def test_a_deferred_preview_shows_what_the_commit_persists(world):
    preview = world.simulate(point=on_date(MARCH))
    shown = next(row for row in preview["visits"] if row["to_key"] == "C1D1:0")
    decision = world.confirm(preview)
    result = world.adoption.execute(
        organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]),
        as_of=MARCH)
    committed = next(row for row in world.events() if row.logical_key == "C1D1:0")
    assert result["status"] == "APPLIED"
    assert shown["new_target"] == str(committed.nominal_start_date) == "2026-01-08"
    assert committed.protected_history is True
    assert shown["category"] == "PROTECTED"


def test_every_previewed_target_equals_the_committed_target(world):
    preview = world.simulate(point=on_date(MARCH))
    decision = world.confirm(preview)
    world.adoption.execute(organization_id=world.org, actor_id=world.pi,
                           decision_id=UUID(decision["id"]), as_of=MARCH)
    committed = {row.logical_key: row for row in world.events()}
    for row in preview["visits"]:
        if row["to_key"] and row["category"] != "REMOVED" and row["to_key"] in committed:
            assert row["new_target"] == (str(committed[row["to_key"]].nominal_start_date)
                                         if committed[row["to_key"]].nominal_start_date else None), row


def test_an_event_triggered_preview_is_provisional_until_the_event_happens(world):
    preview = world.simulate(point=at_event())
    assert preview["provisional"] is True and preview["effective_date"] is None
    assert preview["provisional_note"]
    assert preview["as_of"] == TODAY.isoformat()


def test_the_comparison_baseline_is_kept_apart_from_the_stored_calendar(world):
    preview = world.simulate()
    assert preview["baseline"]["computed"] is True
    assert preview["baseline"]["as_of"] == TODAY.isoformat()


# ------------------------------------------------------------------------------- F3
def claim(world, decision_id, as_of=TODAY):
    return world.adoption._run(lambda tx: world.adoption._claim(tx, world.org, decision_id, as_of))


def execute_claimed(world, decision_id, token, as_of=TODAY):
    return world.adoption._run(lambda tx: world.adoption._execute_claimed(
        tx, world.org, world.pi, decision_id, token, as_of, ()))


def withdraw_trigger(world, patient_id, performed):
    world.patients.record_actual_occurrence(
        patient_id, organization_id=world.org, logical_key="C1D1:0",
        kind=OccurrenceFactKind.MISSED, effective_at=date(2026, 1, 8),
        supersedes_occurrence_id=performed.occurrence_id, actor_id=world.pi,
        reason="visit was actually missed", horizon=HORIZON, today=TODAY)


def test_a_trigger_withdrawn_after_the_claim_does_not_apply_the_adoption(world):
    patient_id = world.patient_ids[0]
    decision = world.confirm(world.simulate(point=at_event()))
    decision_id = UUID(decision["id"])
    performed = world.record(patient_id, "C1D1:0", date(2026, 1, 8))
    token = claim(world, decision_id)["lease_token"]
    assert token
    withdraw_trigger(world, patient_id, performed)
    assert world.uow.read(lambda tx: trigger_state(
        tx, world.patient(patient_id), tx.schedules.get(world.v1), world.v1, at_event(), TODAY))["due"] is False

    evaluations_before = world.uow.read(lambda tx: tx.evaluations.count_evaluations(patient_id, world.v2))
    result = execute_claimed(world, decision_id, token)
    stored = world.uow.read(lambda tx: tx.amendments.get_decision(world.org, decision_id))
    assert result["status"] == "WAITING" and result["executed"] is False
    assert stored.status.value == "WAITING" and stored.is_open and stored.lease_token is None
    assert [b["code"] for b in stored.blockers] == ["TRIGGER_WITHDRAWN"]
    assert world.patient(patient_id).current_schedule_version_id == world.v1
    assert world.uow.read(lambda tx: tx.evaluations.count_evaluations(patient_id, world.v2)) == evaluations_before == 0
    assert world.uow.read(lambda tx: [a for a in tx.impacts.list_assignments(patient_id)
                                      if a.schedule_version_id == world.v2]) == []
    audits = world.uow.read(lambda tx: tx.audit.list_for_organization(
        world.org, action="PATIENT_AMENDMENT_TRIGGER_WITHDRAWN"))
    assert len(audits) == 1


def test_a_withdrawn_trigger_that_happens_again_is_applied_with_the_date_that_is_true_now(world):
    patient_id = world.patient_ids[0]
    decision_id = UUID(world.confirm(world.simulate(point=at_event()))["id"])
    performed = world.record(patient_id, "C1D1:0", date(2026, 1, 8))
    token = claim(world, decision_id)["lease_token"]
    withdraw_trigger(world, patient_id, performed)
    assert execute_claimed(world, decision_id, token)["status"] == "WAITING"
    world.record(patient_id, "C1D1:0", date(2026, 1, 9))                 # performed after all
    applied = world.adoption.execute(
        organization_id=world.org, actor_id=world.pi, decision_id=decision_id, as_of=TODAY)
    assert applied["status"] == "APPLIED" and applied["effective_date"] == "2026-01-09"
    assert world.patient(patient_id).current_schedule_version_id == world.v2


def test_a_correction_between_read_and_commit_conflicts_instead_of_slipping_through(world):
    """The write-skew guard: a transaction that decided from the inputs it read cannot commit
    after a clinical input of the same patient changed."""
    patient_id = world.patient_ids[0]
    read = world.patient(patient_id)
    world.record(patient_id, "C1D1:0", date(2026, 1, 8))                 # an input write
    assert world.patient(patient_id).input_revision > read.input_revision
    with pytest.raises(StaleRevisionError):
        world.uow.run(lambda tx: tx.patients.guard_inputs(
            world.org, patient_id, read.input_revision))
    fresh = world.patient(patient_id)
    world.uow.run(lambda tx: tx.patients.guard_inputs(world.org, patient_id, fresh.input_revision))


def test_saving_a_stale_patient_copy_never_rewinds_the_input_sequence(world):
    patient_id = world.patient_ids[0]
    stale = world.patient(patient_id)
    world.record(patient_id, "C1D1:0", date(2026, 1, 8))
    after_input = world.patient(patient_id)
    # a writer holding the pre-input copy at the same row revision may still save it
    world.uow.run(lambda tx: tx.patients.save(
        stale.model_copy(update={"revision": after_input.revision})))
    assert world.patient(patient_id).input_revision == after_input.input_revision


# ------------------------------------------------------------------------------- F4
def test_a_due_decision_behind_a_waiting_one_is_reached_with_a_batch_of_one(world):
    world.confirm(world.simulate(patient_id=world.patient_ids[0], point=on_date(date(2099, 1, 1))))
    world.confirm(world.simulate(patient_id=world.patient_ids[1], point=on_date(MARCH)))
    first = run(process_due_once(world.uow, yes, as_of=MARCH, limit=1))
    second = run(process_due_once(world.uow, yes, as_of=MARCH, limit=1))
    assert [row["status"] for row in first] == ["WAITING"]
    assert [row["status"] for row in second] == ["APPLIED"]
    assert world.patient(world.patient_ids[1]).current_schedule_version_id == world.v2
    assert world.patient(world.patient_ids[0]).current_schedule_version_id == world.v1


def seed_waiting(world, count, *, organization_id=None, start=0):
    open_rows = world.uow.read(lambda tx: tx.amendments.list_open_decisions(world.org))
    if not open_rows:
        world.confirm(world.simulate(patient_id=world.patient_ids[0], point=on_date(date(2099, 1, 1))))
        open_rows = world.uow.read(lambda tx: tx.amendments.list_open_decisions(world.org))
    template = open_rows[0]
    created = []
    for index in range(count):
        row = template.model_copy(update={
            "id": uuid4(), "patient_id": uuid4(), "idempotency_key": None, "simulation_id": None,
            "organization_id": organization_id or world.org, "last_checked_at": None,
            "created_at": template.created_at + timedelta(seconds=start + index + 1),
            "revision": 1}, deep=True)
        world.uow.run(lambda tx, row=row: tx.amendments.add_decision(row))
        created.append(row)
    return created


def test_more_than_a_page_of_waiting_decisions_cannot_starve_a_due_one(world):
    world.confirm(world.simulate(patient_id=world.patient_ids[0], point=on_date(date(2099, 1, 1))))
    seed_waiting(world, 5)
    due = world.confirm(world.simulate(patient_id=world.patient_ids[1], point=on_date(MARCH)))
    seen = set()
    for _ in range(8):                                        # more passes than waiting rows
        for row in world.uow.read(lambda tx: tx.amendments.list_pending_execution(limit=2)):
            seen.add(row.id)
            world.uow.run(lambda tx, row=row: tx.amendments.save_decision(
                tx.amendments.get_decision(row.organization_id, row.id).model_copy(
                    update={"last_checked_at": datetime.now(timezone.utc)}),
                expected_revision=tx.amendments.get_decision(row.organization_id, row.id).revision))
    assert UUID(due["id"]) in seen and len(seen) == 7


def test_the_default_page_is_bounded_in_the_query(world):
    seed_waiting(world, 105)
    page = world.uow.read(lambda tx: tx.amendments.list_pending_execution(limit=100))
    assert len(page) == 100


def test_decisions_of_several_organizations_are_all_reached(world):
    seed_waiting(world, 3, organization_id=uuid4())
    seed_waiting(world, 3)
    page = world.uow.read(lambda tx: tx.amendments.list_pending_execution(limit=50))
    assert len({row.organization_id for row in page}) == 2


def test_a_held_lease_is_not_offered_but_an_expired_one_is(world):
    decision_id = UUID(world.confirm(world.simulate(point=on_date(MARCH)))["id"])
    token = claim(world, decision_id, as_of=MARCH)["lease_token"]
    assert token
    assert world.uow.read(lambda tx: tx.amendments.list_pending_execution(limit=10)) == []
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    offered = world.uow.read(lambda tx: tx.amendments.list_pending_execution(limit=10, now=later))
    assert [row.id for row in offered] == [decision_id]


def test_a_failing_authority_check_rotates_the_decision_instead_of_blocking_the_pass(world):
    world.confirm(world.simulate(patient_id=world.patient_ids[0], point=on_date(MARCH)))
    world.confirm(world.simulate(patient_id=world.patient_ids[1], point=on_date(MARCH)))
    calls = []

    async def broken_then_fine(decision, patient):
        calls.append(decision.id)
        if len(calls) == 1:
            raise RuntimeError("authorisation provider unavailable")
        return True

    first = run(process_due_once(world.uow, broken_then_fine, as_of=MARCH, limit=1))
    second = run(process_due_once(world.uow, broken_then_fine, as_of=MARCH, limit=1))
    assert [row["status"] for row in first] == ["ERROR"]
    assert [row["status"] for row in second] == ["APPLIED"]
    assert calls[0] != calls[1]
