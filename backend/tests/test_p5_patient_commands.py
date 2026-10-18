"""P5-03/P5-04 on the repository-port plane (memory AND real MongoDB).

BASELINE = 1 Jan 2026. Tracks: TREATMENT (C1D1 +7 d, weekly DOSE from +14 d,
MANUAL_REBASE, pausable/stoppable), LABS (7 d after the ACTUAL TREATMENT date of
C1D1) and IMAGING (baseline +30 d, fixed). Synthetic data only.
"""

from __future__ import annotations

import sys
import threading
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.errors import IdempotencyConflictError
from app.application.patient_schedule import PatientScheduleService
from app.application.patient_commands import (
    CommandRefusedError, PatientCommandService, StaleProposalError,
)
from app.domain.schedule.commands import (
    CancelOccurrence, CreateUnscheduled, MarkMissed, OverrideOccurrence, PauseTrack, RebaseTrack,
    RecordActual, RescheduleAppointment, ResumeTrack, ReverseCommand, StopTrack,
)
from app.domain.schedule.models import HoldResumePolicy, OccurrenceFactKind, ScheduleStatus
from test_p5_tracks_domain import program
from test_uctsm_p1_occurrence_recalculation import ORG, Env

pytest_plugins = ["tests.contract.conftest"]

BASELINE = date(2026, 1, 1)
TODAY = date(2026, 3, 1)
ACTOR = uuid4()


def make(uow, **kwargs):
    schedule = program(**kwargs)
    schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
    env = Env(uow, schedule, baseline=BASELINE)
    env.evaluate()
    return env, PatientCommandService(uow)


def by_key(rows):
    return {row["logical_key"]: row for row in rows}


def go(service, env, payload, *, effective=date(2026, 1, 20), reason="clinical decision",
       authority=None, key=None):
    preview = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR, payload=payload, effective_at=effective,
        reason=reason, authority=authority, horizon=date(2026, 12, 31), today=TODAY)
    outcome = service.confirm(
        preview.proposal_id, organization_id=ORG, actor_id=ACTOR, idempotency_key=key, today=TODAY)
    return preview, outcome


def counts(uow, env):
    def read(tx):
        return {
            "ledger": len(tx.ledger.list_for_patient(ORG, env.patient_id)),
            "occurrences": len(tx.facts.list_occurrences(env.patient_id)),
            "evaluations": len(tx.evaluations.list_evaluations(env.patient_id, env.version_id)),
            "outbox": len(tx.outbox.list_pending(limit=1000)),
            "unscheduled": len(tx.facts.list_unscheduled_visits(env.patient_id)),
        }
    return uow.read(read)


# ==================================================== headline: treatment delay

def test_treatment_delay_changes_labs_and_leaves_imaging_fixed_with_reasons(uow):
    env, service = make(uow)
    first = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8),
                             treatment_date=date(2026, 1, 15)),
        effective_at=date(2026, 1, 15), reason="C1D1 given a week late", today=TODAY,
        horizon=date(2026, 12, 31))
    rows = by_key(first.changes)
    lab, imaging = rows["LAB_AFTER_TX:0"], rows["IMAGING_BASELINE:0"]
    assert lab["changed"] and lab["change_kind"] in ("PROTOCOL_TARGET", "ADDED")
    assert lab["protocol"]["new_target"] == "2026-01-22"
    assert lab["track"]["code"] == "LABS" and lab["track"]["movement"] == "FOLLOWS_ACTUAL"
    assert "actual treatment of C1D1" in lab["reason"]
    assert imaging["changed"] is False and imaging["protocol"]["new_target"] == "2026-01-31"
    assert imaging["track"]["rebase_policy"] == "FIXED_FROM_ANCHOR"
    assert "no actual date of another visit moves it" in imaging["reason"]
    assert first.summary["unchanged"] >= 1 and first.summary["changed"] >= 1   # BOTH are returned


def test_a_preview_writes_no_clinical_fact_evaluation_or_notification(uow):
    env, service = make(uow)
    before = counts(uow, env)
    service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8),
                             treatment_date=date(2026, 1, 15)),
        effective_at=date(2026, 1, 15), reason="late", today=TODAY, horizon=date(2026, 12, 31))
    assert counts(uow, env) == before
    pending = uow.read(lambda tx: tx.impacts.list_for_patient(env.patient_id, status="PENDING"))
    assert len(pending) == 1                                  # only the proposal itself


def test_confirm_commits_fact_ledger_evaluation_and_outbox_together(uow):
    env, service = make(uow)
    before = counts(uow, env)
    preview, outcome = go(
        service, env,
        RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8), treatment_date=date(2026, 1, 15)),
        effective=date(2026, 1, 15), reason="given a week late")
    after = counts(uow, env)
    assert after["ledger"] == before["ledger"] + 1
    assert after["occurrences"] == before["occurrences"] + 1
    assert after["evaluations"] == before["evaluations"] + 1
    assert after["outbox"] == before["outbox"] + 1
    assert outcome.digest == preview.digest                     # preview == commit, semantically
    assert outcome.sequence == 1 and not outcome.replayed
    calendar = env.calendar()
    assert calendar["LAB_AFTER_TX"].planned_start == date(2026, 1, 22)
    assert calendar["IMAGING_BASELINE"].planned_start == date(2026, 1, 31)
    rows = uow.read(lambda tx: tx.ledger.list_for_patient(ORG, env.patient_id))
    assert rows[0].command_type == "RECORD_ACTUAL" and rows[0].actor_id == ACTOR
    assert rows[0].proposal_id == preview.proposal_id and rows[0].fact_refs["occurrence_id"]


def test_replaying_the_confirmation_returns_the_stored_result_without_new_writes(uow):
    env, service = make(uow)
    preview, first = go(
        service, env, MarkMissed(logical_key="IMAGING_BASELINE:0"), effective=date(2026, 2, 1),
        key="missed-1")
    snapshot = counts(uow, env)
    replay = service.confirm(
        preview.proposal_id, organization_id=ORG, actor_id=ACTOR, idempotency_key="missed-1",
        today=TODAY)
    assert replay.replayed and replay.command_id == first.command_id
    assert counts(uow, env) == snapshot


def test_same_key_for_a_different_command_conflicts_without_writes(uow):
    env, service = make(uow)
    go(service, env, MarkMissed(logical_key="IMAGING_BASELINE:0"), effective=date(2026, 2, 1), key="k")
    other = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=CancelOccurrence(logical_key="DOSE:3"), effective_at=date(2026, 1, 10),
        reason="x", today=TODAY, horizon=date(2026, 12, 31))
    snapshot = counts(uow, env)
    with pytest.raises(IdempotencyConflictError):
        service.confirm(other.proposal_id, organization_id=ORG, actor_id=ACTOR,
                        idempotency_key="k", today=TODAY)
    assert counts(uow, env) == snapshot


def test_a_preview_goes_stale_when_anything_it_was_calculated_from_changes(uow):
    env, service = make(uow)
    stale = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=OverrideOccurrence(logical_key="IMAGING_BASELINE:0",
                                   target_date=date(2026, 2, 20), authority="PI Dr A"),
        effective_at=date(2026, 1, 12), reason="scanner down", today=TODAY, horizon=date(2026, 12, 31))
    go(service, env, MarkMissed(logical_key="DOSE:3"), effective=date(2026, 2, 6))   # new fact between
    snapshot = counts(uow, env)
    with pytest.raises(StaleProposalError):
        service.confirm(stale.proposal_id, organization_id=ORG, actor_id=ACTOR, today=TODAY)
    assert counts(uow, env) == snapshot                      # no clinical write
    refreshed = uow.read(lambda tx: tx.impacts.get(stale.proposal_id))
    assert refreshed.status == "STALE"
    with pytest.raises(CommandRefusedError):                 # a stale preview cannot be reused
        service.confirm(stale.proposal_id, organization_id=ORG, actor_id=ACTOR, today=TODAY)


def test_an_expired_preview_is_refused(uow):
    env, service = make(uow)
    expired = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=MarkMissed(logical_key="IMAGING_BASELINE:0"), effective_at=date(2026, 2, 1),
        reason="x", today=TODAY, horizon=date(2026, 12, 31), expires_in_minutes=-1)
    with pytest.raises(StaleProposalError):
        service.confirm(expired.proposal_id, organization_id=ORG, actor_id=ACTOR, today=TODAY)


def test_cancelling_a_preview_never_applies_it(uow):
    env, service = make(uow)
    preview = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=MarkMissed(logical_key="IMAGING_BASELINE:0"), effective_at=date(2026, 2, 1),
        reason="x", today=TODAY, horizon=date(2026, 12, 31))
    snapshot = counts(uow, env)
    service.cancel(preview.proposal_id, organization_id=ORG, actor_id=ACTOR, reason="changed my mind")
    with pytest.raises(CommandRefusedError):
        service.confirm(preview.proposal_id, organization_id=ORG, actor_id=ACTOR, today=TODAY)
    assert counts(uow, env) == snapshot


# ======================================================= operational vs protocol

def test_appointment_inside_the_window_changes_only_the_booking(uow):
    env, service = make(uow, imaging_window=True)
    _, outcome = go(
        service, env,
        RescheduleAppointment(logical_key="IMAGING_BASELINE:0", new_date=date(2026, 2, 2)),
        effective=date(2026, 1, 20))
    row = by_key(outcome.changes)["IMAGING_BASELINE:0"]
    appointment = row["operational"]["after"]["appointment"]
    assert appointment["date"] == "2026-02-02" and appointment["outside_protocol_window"] is False
    assert row["protocol"]["old_target"] == row["protocol"]["new_target"] == "2026-01-31"
    assert row["protocol"]["old_window"] == row["protocol"]["new_window"] == {
        "earliest": "2026-01-28", "latest": "2026-02-03"}
    assert row["change_kind"] == "OPERATIONAL" and row["status"]["new"] == row["status"]["old"]
    assert env.calendar()["IMAGING_BASELINE"].actual_visit_date is None      # no actual fabricated


def test_outside_window_appointment_keeps_the_deviation_and_never_becomes_the_target(uow):
    env, service = make(uow, imaging_window=True)
    _, outcome = go(
        service, env,
        RescheduleAppointment(logical_key="IMAGING_BASELINE:0", new_date=date(2026, 3, 25)),
        effective=date(2026, 1, 20), reason="patient travelling")
    row = by_key(outcome.changes)["IMAGING_BASELINE:0"]
    appointment = row["operational"]["after"]["appointment"]
    assert appointment["date"] == "2026-03-25"
    assert appointment["outside_protocol_window"] is True
    assert appointment["flag"] == "BOOKED_OUTSIDE_PERMITTED"      # the deviation stays visible
    assert row["protocol"]["new_target"] == "2026-01-31"           # never rebased to the appointment
    assert row["protocol"]["new_window"] == {"earliest": "2026-01-28", "latest": "2026-02-03"}


def test_override_is_beside_the_protocol_target_with_authority_and_deviation(uow):
    env, service = make(uow, imaging_window=True)
    _, outcome = go(
        service, env,
        OverrideOccurrence(logical_key="IMAGING_BASELINE:0", target_date=date(2026, 2, 20),
                           authority="PI Dr A"),
        effective=date(2026, 1, 12), reason="scanner unavailable", authority="PI Dr A")
    row = by_key(outcome.changes)["IMAGING_BASELINE:0"]
    assert row["protocol"]["new_target"] == "2026-01-31"
    override = row["operational"]["after"]["override"]
    assert override["target_date"] == "2026-02-20" and override["authority"] == "PI Dr A"
    assert override["outside_protocol_window"] is True
    assert row["operational"]["after"]["effective_date"] == "2026-02-20"


def test_cancel_a_future_occurrence_keeps_history_and_target(uow):
    env, service = make(uow)
    _, outcome = go(service, env, CancelOccurrence(logical_key="DOSE:3"),
                    effective=date(2026, 1, 20), reason="patient stopped dosing")
    row = by_key(outcome.changes)["DOSE:3"]
    assert row["status"]["new"] == "CANCELLED" and row["protocol"]["new_target"] == "2026-02-05"
    assert any(item["action"] == "CANCEL" and item["logical_key"] == "DOSE:3"
               for item in outcome.notifications)


def test_a_cancellation_of_a_past_planned_occurrence_is_refused(uow):
    env, service = make(uow)
    with pytest.raises(CommandRefusedError) as raised:
        service.preview(
            env.patient_id, organization_id=ORG, actor_id=ACTOR,
            payload=CancelOccurrence(logical_key="C1D1:0"), effective_at=date(2026, 2, 10),
            reason="x", today=TODAY, horizon=date(2026, 12, 31))
    assert raised.value.code == "OCCURRENCE_NOT_IN_FUTURE"


def test_unscheduled_visit_command_is_a_ledger_entry_and_a_fact(uow):
    env, service = make(uow, unscheduled=True)
    with pytest.raises(Exception):         # DOSE is a scheduled visit: only ON_DEMAND may be created
        service.preview(
            env.patient_id, organization_id=ORG, actor_id=ACTOR,
            payload=CreateUnscheduled(event_code="DOSE", occurred_on=date(2026, 2, 2)),
            effective_at=date(2026, 2, 2), reason="x", today=TODAY, horizon=date(2026, 12, 31))
    before = counts(uow, env)
    go(service, env, CreateUnscheduled(event_code="UNPLANNED_VISIT", occurred_on=date(2026, 2, 2)),
       effective=date(2026, 2, 2), reason="adverse event follow-up")
    after = counts(uow, env)
    assert after["unscheduled"] == before["unscheduled"] + 1
    assert after["ledger"] == before["ledger"] + 1 and after["evaluations"] == before["evaluations"] + 1
    assert service.ledger(env.patient_id, organization_id=ORG)[-1]["command_type"] == "CREATE_UNSCHEDULED"


# ============================================================ track commands

def test_pause_resume_stop_through_commands(uow):
    env, service = make(uow)
    _, paused = go(service, env, PauseTrack(track_code="TREATMENT"), effective=date(2026, 1, 20),
                   reason="toxicity")
    assert by_key(paused.changes)["DOSE:1"]["status"]["new"] == "PAUSED"
    assert by_key(paused.changes)["IMAGING_BASELINE:0"]["changed"] is False
    _, resumed = go(service, env, ResumeTrack(track_code="TREATMENT"), effective=date(2026, 2, 3),
                    reason="recovered")
    assert by_key(resumed.changes)["DOSE:1"]["protocol"]["new_target"] == "2026-02-05"   # shifted 14 d
    with pytest.raises(CommandRefusedError) as again:
        service.preview(env.patient_id, organization_id=ORG, actor_id=ACTOR,
                        payload=ResumeTrack(track_code="TREATMENT"), effective_at=date(2026, 2, 4),
                        reason="x", today=TODAY, horizon=date(2026, 12, 31))
    assert again.value.code == "TRACK_NOT_HELD"
    _, stopped = go(service, env, StopTrack(track_code="TREATMENT"), effective=date(2026, 2, 10),
                    reason="progression")
    assert by_key(stopped.changes)["DOSE:3"]["status"]["new"] in ("CANCELLED", "PAUSED")


def test_manual_rebase_moves_only_the_selected_track_and_is_refused_elsewhere(uow):
    env, service = make(uow)
    _, outcome = go(service, env, RebaseTrack(track_code="TREATMENT", anchor_code="BASELINE",
                                              new_value=date(2026, 2, 1)),
                    effective=date(2026, 1, 5), reason="delayed randomisation")
    rows = by_key(outcome.changes)
    assert rows["C1D1:0"]["protocol"]["new_target"] == "2026-02-08"
    assert rows["IMAGING_BASELINE:0"]["protocol"]["new_target"] == "2026-01-31"
    with pytest.raises(CommandRefusedError) as raised:
        service.preview(env.patient_id, organization_id=ORG, actor_id=ACTOR,
                        payload=RebaseTrack(track_code="IMAGING", anchor_code="BASELINE",
                                            new_value=date(2026, 3, 1)),
                        effective_at=date(2026, 1, 25), reason="x", today=TODAY,
                        horizon=date(2026, 12, 31))
    assert raised.value.code == "REBASE_NOT_ALLOWED"


def test_a_resume_policy_left_to_a_person_must_be_chosen(uow):
    env, service = make(uow, hold=HoldResumePolicy.WAIT_FOR_DECISION)
    go(service, env, PauseTrack(track_code="TREATMENT"), effective=date(2026, 1, 20), reason="r")
    with pytest.raises(CommandRefusedError) as raised:
        service.preview(env.patient_id, organization_id=ORG, actor_id=ACTOR,
                        payload=ResumeTrack(track_code="TREATMENT"), effective_at=date(2026, 2, 3),
                        reason="x", today=TODAY, horizon=date(2026, 12, 31))
    assert raised.value.code == "RESUME_POLICY_REQUIRED"
    _, ok = go(service, env, ResumeTrack(track_code="TREATMENT", resume_policy=HoldResumePolicy.NOMINAL),
               effective=date(2026, 2, 3), reason="nominal grid")
    assert ok.sequence == 2


def test_an_unconfirmed_track_policy_cannot_be_commanded():
    schedule = program(confirmed=False)
    schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
    # an unconfirmed schedule is not approvable; the command layer still refuses by itself
    from app.domain.schedule.commands import validate_track_command
    assert [p["code"] for p in validate_track_command(schedule, [], PauseTrack(track_code="TREATMENT"))] \
        == ["TRACK_POLICY_UNRESOLVED"]


# ============================================================== reversal

def test_reversing_a_reschedule_restores_the_prior_booking_without_touching_actuals(uow):
    env, service = make(uow)
    _, first = go(service, env, RescheduleAppointment(logical_key="IMAGING_BASELINE:0",
                                                     new_date=date(2026, 2, 3)), effective=date(2026, 1, 20))
    _, second = go(service, env, RescheduleAppointment(logical_key="IMAGING_BASELINE:0",
                                                      new_date=date(2026, 2, 10)), effective=date(2026, 1, 21))
    # first is now followed by a newer command on the same occurrence
    with pytest.raises(CommandRefusedError) as blocked:
        service.preview(env.patient_id, organization_id=ORG, actor_id=ACTOR,
                        payload=ReverseCommand(reverses_command_id=str(first.command_id)),
                        effective_at=date(2026, 1, 22), reason="undo", today=TODAY,
                        horizon=date(2026, 12, 31))
    assert blocked.value.code == "NEWER_COMMAND_DEPENDS"
    _, reversed_second = go(service, env, ReverseCommand(reverses_command_id=str(second.command_id)),
                            effective=date(2026, 1, 22), reason="booked wrong day")
    row = by_key(reversed_second.changes)["IMAGING_BASELINE:0"]
    assert row["operational"]["after"]["appointment"]["date"] == "2026-02-03"     # restored
    ledger = service.ledger(env.patient_id, organization_id=ORG)
    assert [item["command_type"] for item in ledger] == [
        "RESCHEDULE_APPOINTMENT", "RESCHEDULE_APPOINTMENT", "REVERSE"]
    assert ledger[1]["reversed_by"] == ledger[2]["id"]                            # history kept
    with pytest.raises(CommandRefusedError) as twice:
        service.preview(env.patient_id, organization_id=ORG, actor_id=ACTOR,
                        payload=ReverseCommand(reverses_command_id=str(second.command_id)),
                        effective_at=date(2026, 1, 23), reason="again", today=TODAY,
                        horizon=date(2026, 12, 31))
    assert twice.value.code == "ALREADY_REVERSED"


def test_reversing_the_only_booking_withdraws_it(uow):
    env, service = make(uow)
    _, booked = go(service, env, RescheduleAppointment(logical_key="IMAGING_BASELINE:0",
                                                      new_date=date(2026, 2, 3)), effective=date(2026, 1, 20))
    _, undone = go(service, env, ReverseCommand(reverses_command_id=str(booked.command_id)),
                   effective=date(2026, 1, 21), reason="not needed")
    row = by_key(undone.changes)["IMAGING_BASELINE:0"]
    assert "appointment" not in row["operational"]["after"]
    assert row["protocol"]["new_target"] == "2026-01-31"


def test_reversal_of_an_override_recomputes_and_facts_are_not_reversible(uow):
    env, service = make(uow)
    _, override = go(
        service, env,
        OverrideOccurrence(logical_key="IMAGING_BASELINE:0", target_date=date(2026, 2, 20), authority="PI"),
        effective=date(2026, 1, 12), authority="PI")
    _, fact = go(service, env, MarkMissed(logical_key="DOSE:3"), effective=date(2026, 2, 6))
    with pytest.raises(CommandRefusedError) as refused:
        service.preview(env.patient_id, organization_id=ORG, actor_id=ACTOR,
                        payload=ReverseCommand(reverses_command_id=str(fact.command_id)),
                        effective_at=date(2026, 2, 7), reason="x", today=TODAY,
                        horizon=date(2026, 12, 31))
    assert refused.value.code == "FACT_NOT_REVERSIBLE"
    _, undone = go(service, env, ReverseCommand(reverses_command_id=str(override.command_id)),
                   effective=date(2026, 1, 13), reason="scanner back")
    row = by_key(undone.changes)["IMAGING_BASELINE:0"]
    assert "override" not in row["operational"]["after"]


def test_a_reversal_cannot_target_another_patients_command(uow):
    env, service = make(uow)
    other_env, _ = make(uow)
    _, theirs = go(service, other_env, MarkMissed(logical_key="IMAGING_BASELINE:0"),
                   effective=date(2026, 2, 1))
    with pytest.raises(CommandRefusedError) as refused:
        service.preview(env.patient_id, organization_id=ORG, actor_id=ACTOR,
                        payload=ReverseCommand(reverses_command_id=str(theirs.command_id)),
                        effective_at=date(2026, 2, 2), reason="x", today=TODAY,
                        horizon=date(2026, 12, 31))
    assert refused.value.code == "REVERSAL_TARGET_UNKNOWN"


# ====================================================== atomicity and concurrency

def test_a_failure_while_evaluating_leaves_no_fact_no_ledger_row_no_outbox(uow, monkeypatch):
    env, service = make(uow)
    preview = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8),
                             treatment_date=date(2026, 1, 15)),
        effective_at=date(2026, 1, 15), reason="late", today=TODAY, horizon=date(2026, 12, 31))
    before = counts(uow, env)

    def boom(*args, **kwargs):
        raise RuntimeError("evaluation failed")

    monkeypatch.setattr(service.patient_schedules, "evaluate_in", boom)
    with pytest.raises(RuntimeError):
        service.confirm(preview.proposal_id, organization_id=ORG, actor_id=ACTOR, today=TODAY)
    assert counts(uow, env) == before
    assert uow.read(lambda tx: tx.impacts.get(preview.proposal_id)).status == "PENDING"


def test_two_commands_prepared_on_the_same_base_cannot_both_commit(uow, request):
    if request.node.callspec.params.get('uow') == 'memory':
        pytest.skip('the in-memory store is single-threaded; real transactions are proven on Mongo')
    env, service = make(uow)
    previews = [service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=MarkMissed(logical_key=key), effective_at=date(2026, 2, 6), reason="r",
        today=TODAY, horizon=date(2026, 12, 31)) for key in ("DOSE:2", "DOSE:3")]
    results: list[str] = []
    barrier = threading.Barrier(2)

    def confirm(index):
        barrier.wait()
        try:
            service.confirm(previews[index].proposal_id, organization_id=ORG, actor_id=ACTOR,
                            today=TODAY)
            results.append("committed")
        except StaleProposalError:
            results.append("stale")
        except Exception as error:      # noqa: BLE001
            results.append(type(error).__name__)

    threads = [threading.Thread(target=confirm, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["committed", "stale"], results
    assert counts(uow, env)["ledger"] == 1


def test_identical_concurrent_confirmations_create_one_business_result(uow, request):
    if request.node.callspec.params.get('uow') == 'memory':
        pytest.skip('the in-memory store is single-threaded; real transactions are proven on Mongo')
    env, service = make(uow)
    preview = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=MarkMissed(logical_key="DOSE:2"), effective_at=date(2026, 2, 6), reason="r",
        today=TODAY, horizon=date(2026, 12, 31))
    outcomes: list[object] = []
    barrier = threading.Barrier(3)

    def confirm():
        barrier.wait()
        try:
            outcomes.append(service.confirm(
                preview.proposal_id, organization_id=ORG, actor_id=ACTOR,
                idempotency_key="same-key", today=TODAY))
        except Exception as error:      # noqa: BLE001
            outcomes.append(error)

    threads = [threading.Thread(target=confirm) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert counts(uow, env)["ledger"] == 1 and counts(uow, env)["evaluations"] == 2
    results = [item for item in outcomes if hasattr(item, "command_id")]
    assert len({item.command_id for item in results}) == 1
    assert sum(1 for item in results if not item.replayed) == 1


# ================================================ the older entry point shares the ledger

def test_the_older_actual_route_writes_the_same_ledger_and_replays_once(uow):
    env, _ = make(uow)
    def record():
        return env.service.record_actual_occurrence(
            env.patient_id, organization_id=ORG, logical_key="C1D1:0",
            kind=OccurrenceFactKind.PERFORMED, visit_date=date(2026, 1, 8),
            treatment_date=date(2026, 1, 15), idempotency_key="legacy-1", today=TODAY,
            reason="test", actor_id=ACTOR)
    first, again = record(), record()
    assert again.replayed and again.occurrence_id == first.occurrence_id
    rows = uow.read(lambda tx: tx.ledger.list_for_patient(ORG, env.patient_id))
    assert [item.command_type for item in rows] == ["RECORD_ACTUAL"]
    assert rows[0].fact_refs["via"] == "record_actual_occurrence"
    assert counts(uow, env)["occurrences"] == 1


# ===================== audit finding 4: canonical replay through a duplicate preview

def test_same_key_through_a_second_preview_replays_the_original_committed_outcome(uow):
    env, service = make(uow)
    payload = RescheduleAppointment(logical_key="IMAGING_BASELINE:0", new_date=date(2026, 2, 2))
    _, first = go(service, env, payload, effective=date(2026, 1, 20), key="same")
    duplicate = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR, payload=payload,
        effective_at=date(2026, 1, 20), reason="clinical decision", today=TODAY,
        horizon=date(2026, 12, 31))
    snapshot = counts(uow, env)
    again = service.confirm(duplicate.proposal_id, organization_id=ORG, actor_id=ACTOR,
                            idempotency_key="same", today=TODAY)
    assert again.replayed and again.command_id == first.command_id and again.sequence == first.sequence
    assert again.evaluation_id == first.evaluation_id and again.evaluation_id is not None
    assert again.digest == first.digest and again.summary == first.summary
    assert again.changes == first.changes and again.notifications == first.notifications
    assert again.summary["changed"] == 1
    assert counts(uow, env) == snapshot                  # no second command / evaluation
    linked = uow.read(lambda tx: tx.impacts.get(duplicate.proposal_id))
    assert linked.status == "CONFIRMED" and linked.impact["duplicate_of"] == str(first.proposal_id)


def test_replay_after_newer_commands_still_returns_the_original_result(uow):
    env, service = make(uow)
    payload = RescheduleAppointment(logical_key="IMAGING_BASELINE:0", new_date=date(2026, 2, 2))
    _, first = go(service, env, payload, effective=date(2026, 1, 20), key="k1")
    go(service, env, MarkMissed(logical_key="DOSE:3"), effective=date(2026, 2, 6))   # newer command
    duplicate = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR, payload=payload,
        effective_at=date(2026, 1, 20), reason="clinical decision", today=TODAY,
        horizon=date(2026, 12, 31))
    again = service.confirm(duplicate.proposal_id, organization_id=ORG, actor_id=ACTOR,
                            idempotency_key="k1", today=TODAY)
    assert again.digest == first.digest and again.evaluation_id == first.evaluation_id


def test_same_key_with_a_different_effective_date_conflicts(uow):
    env, service = make(uow)
    payload = RescheduleAppointment(logical_key="IMAGING_BASELINE:0", new_date=date(2026, 2, 2))
    go(service, env, payload, effective=date(2026, 1, 20), key="k2")
    other = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR, payload=payload,
        effective_at=date(2026, 1, 21), reason="x", today=TODAY, horizon=date(2026, 12, 31))
    before = counts(uow, env)
    with pytest.raises(IdempotencyConflictError):
        service.confirm(other.proposal_id, organization_id=ORG, actor_id=ACTOR,
                        idempotency_key="k2", today=TODAY)
    assert counts(uow, env) == before


# ====================== audit finding 5: the calendar itself carries the operational layers

def test_calendar_reload_keeps_protocol_target_appointment_override_and_actuals_apart(uow):
    env, service = make(uow, imaging_window=True)
    go(service, env, RescheduleAppointment(logical_key="IMAGING_BASELINE:0", new_date=date(2026, 2, 2)),
       effective=date(2026, 1, 20))
    go(service, env, OverrideOccurrence(logical_key="IMAGING_BASELINE:0", target_date=date(2026, 2, 20),
                                        window_earliest=date(2026, 2, 18), window_latest=date(2026, 2, 25),
                                        authority="PI Dr A"),
       effective=date(2026, 1, 21), reason="scanner down", authority="PI Dr A")
    go(service, env, RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8),
                                  treatment_date=date(2026, 1, 15)), effective=date(2026, 1, 15))
    entries = {item.logical_key: item for item in
               service.patient_schedules.current_calendar(env.patient_id, organization_id=ORG)}
    imaging = entries["IMAGING_BASELINE:0"]
    assert imaging.planned_start == date(2026, 1, 31)                  # protocol target
    assert (imaging.earliest, imaging.latest) == (date(2026, 1, 28), date(2026, 2, 3))
    assert imaging.window_known is True
    assert imaging.appointment_date == date(2026, 2, 2) and imaging.appointment_outside_window is False
    assert imaging.override_target == date(2026, 2, 20)
    assert (imaging.override_window_earliest, imaging.override_window_latest) == (
        date(2026, 2, 18), date(2026, 2, 25))
    assert imaging.override_outside_window is True and imaging.override_authority == "PI Dr A"
    assert imaging.track_code == "IMAGING" and imaging.track_rebase_policy == "FIXED_FROM_ANCHOR"
    assert imaging.actual_visit_date is None
    c1d1 = entries["C1D1:0"]
    assert c1d1.actual_visit_date == date(2026, 1, 8) and c1d1.actual_treatment_date == date(2026, 1, 15)
    assert c1d1.appointment_date is None


def test_an_unknown_window_is_not_a_zero_width_window(uow):
    env, _ = make(uow)
    entries = {item.logical_key: item for item in
               PatientScheduleService(uow).current_calendar(env.patient_id, organization_id=ORG)}
    assert entries["IMAGING_BASELINE:0"].window_known is False
    assert entries["IMAGING_BASELINE:0"].earliest is None


# ============ R5: an unknown window is not a zero-width window (override AND appointment)

def test_override_and_appointment_report_unknown_not_outside_when_no_window_is_stated(uow):
    env, service = make(uow)                              # IMAGING has no stated window
    _, appointment = go(service, env, RescheduleAppointment(
        logical_key="IMAGING_BASELINE:0", new_date=date(2026, 2, 2)), effective=date(2026, 1, 20))
    booked = by_key(appointment.changes)["IMAGING_BASELINE:0"]["operational"]["after"]["appointment"]
    assert booked["outside_protocol_window"] is None          # cannot be evaluated
    _, override = go(service, env, OverrideOccurrence(
        logical_key="IMAGING_BASELINE:0", target_date=date(2026, 2, 20), authority="PI"),
        effective=date(2026, 1, 21), authority="PI")
    layer = by_key(override.changes)["IMAGING_BASELINE:0"]["operational"]["after"]["override"]
    assert layer["outside_protocol_window"] is None and layer["protocol_window"] == "UNKNOWN"
    entry = {item.logical_key: item for item in service.patient_schedules.current_calendar(
        env.patient_id, organization_id=ORG)}["IMAGING_BASELINE:0"]
    assert entry.window_known is False
    assert entry.appointment_outside_window is None and entry.override_outside_window is None


def test_a_stated_window_still_flags_a_true_deviation_even_when_the_override_is_authorised(uow):
    env, service = make(uow, imaging_window=True)
    _, outcome = go(service, env, OverrideOccurrence(
        logical_key="IMAGING_BASELINE:0", target_date=date(2026, 2, 20), authority="PI"),
        effective=date(2026, 1, 21), authority="PI")
    layer = by_key(outcome.changes)["IMAGING_BASELINE:0"]["operational"]["after"]["override"]
    assert layer["outside_protocol_window"] is True and layer["protocol_window"] == "KNOWN"
    row = by_key(outcome.changes)["IMAGING_BASELINE:0"]
    assert row["protocol"]["new_window"] == {"earliest": "2026-01-28", "latest": "2026-02-03"}


# ===================== R6: the independent actual-treatment contract

def test_treatment_only_actual_is_its_own_fact_and_never_a_visit_date(uow):
    env, service = make(uow)
    _, outcome = go(service, env, RecordActual(logical_key="C1D1:0", treatment_date=date(2026, 1, 15)),
                    effective=date(2026, 1, 15), reason="dose given; visit not yet documented")
    rows = by_key(outcome.changes)
    assert rows["LAB_AFTER_TX:0"]["protocol"]["new_target"] == "2026-01-22"   # treatment-relative rule moved
    assert rows["IMAGING_BASELINE:0"]["changed"] is False
    entry = {item.logical_key: item for item in service.patient_schedules.current_calendar(
        env.patient_id, organization_id=ORG)}["C1D1:0"]
    assert entry.actual_treatment_date == date(2026, 1, 15)
    assert entry.actual_visit_date is None and entry.completed is False      # nothing fabricated
    assert entry.status != "COMPLETED"


def test_visit_only_leaves_treatment_unknown_and_both_keep_different_dates(uow):
    env, service = make(uow)
    go(service, env, RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8)),
       effective=date(2026, 1, 8))
    entry = {item.logical_key: item for item in service.patient_schedules.current_calendar(
        env.patient_id, organization_id=ORG)}
    assert entry["C1D1:0"].actual_visit_date == date(2026, 1, 8)
    assert entry["C1D1:0"].actual_treatment_date is None
    assert entry["LAB_AFTER_TX:0"].status in ("BLOCKED", "WAITING_FOR_ANCHOR")  # waits for treatment
    go(service, env, RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8),
                                  treatment_date=date(2026, 1, 15)), effective=date(2026, 1, 15))
    entry = {item.logical_key: item for item in service.patient_schedules.current_calendar(
        env.patient_id, organization_id=ORG)}["C1D1:0"]
    assert (entry.actual_visit_date, entry.actual_treatment_date) == (date(2026, 1, 8), date(2026, 1, 15))


def test_a_record_actual_with_neither_date_is_refused_at_the_typed_boundary():
    with pytest.raises(Exception) as raised:
        RecordActual(logical_key="C1D1:0")
    assert "visit date, the treatment date" in str(raised.value)


# ===================== R6 reopened: independent facts fold in either recording order

def _entry(service, env, key):
    return {item.logical_key: item for item in service.patient_schedules.current_calendar(
        env.patient_id, organization_id=ORG)}[key]


@pytest.mark.parametrize("treatment_first", [True, False])
def test_visit_and_treatment_recorded_separately_both_survive_in_either_order(uow, treatment_first):
    env, service = make(uow)
    treatment = RecordActual(logical_key="C1D1:0", treatment_date=date(2026, 1, 15))
    visit = RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8))
    first, second = (treatment, visit) if treatment_first else (visit, treatment)
    go(service, env, first, effective=date(2026, 1, 15))
    _, last = go(service, env, second, effective=date(2026, 1, 15))
    entry = _entry(service, env, "C1D1:0")
    assert entry.actual_visit_date == date(2026, 1, 8)
    assert entry.actual_treatment_date == date(2026, 1, 15)             # not erased by omission
    rows = by_key(last.changes)
    assert _entry(service, env, "LAB_AFTER_TX:0").planned_start == date(2026, 1, 22)
    assert _entry(service, env, "IMAGING_BASELINE:0").planned_start == date(2026, 1, 31)
    assert rows["IMAGING_BASELINE:0"]["changed"] is False


def test_treatment_only_then_visit_keeps_the_lab_at_22_january_and_visit_unknown_in_between(uow):
    env, service = make(uow)
    go(service, env, RecordActual(logical_key="C1D1:0", treatment_date=date(2026, 1, 15)),
       effective=date(2026, 1, 15))
    middle = _entry(service, env, "C1D1:0")
    assert middle.actual_visit_date is None and middle.actual_treatment_date == date(2026, 1, 15)
    assert _entry(service, env, "LAB_AFTER_TX:0").planned_start == date(2026, 1, 22)
    go(service, env, RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8)),
       effective=date(2026, 1, 15))
    assert _entry(service, env, "LAB_AFTER_TX:0").planned_start == date(2026, 1, 22)


def test_an_explicit_correction_supersedes_the_combined_fact_whole(uow):
    env, service = make(uow)
    go(service, env, RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8),
                                  treatment_date=date(2026, 1, 15)), effective=date(2026, 1, 15))
    first = uow.read(lambda tx: [r for r in tx.facts.list_occurrences(env.patient_id)])[0]
    go(service, env, RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 9),
                                  treatment_date=date(2026, 1, 16),
                                  supersedes_occurrence_id=str(first.id)), effective=date(2026, 1, 16))
    entry = _entry(service, env, "C1D1:0")
    assert (entry.actual_visit_date, entry.actual_treatment_date) == (date(2026, 1, 9), date(2026, 1, 16))
    assert _entry(service, env, "LAB_AFTER_TX:0").planned_start == date(2026, 1, 23)


# ============ R7: authority is enforced at the backend boundary, not only in the UI

def test_a_patient_whose_authority_is_another_store_refuses_commands_at_preview_and_confirm(uow):
    env, service = make(uow)
    preview = service.preview(
        env.patient_id, organization_id=ORG, actor_id=ACTOR,
        payload=MarkMissed(logical_key="IMAGING_BASELINE:0"), effective_at=date(2026, 2, 1),
        reason="x", today=TODAY, horizon=date(2026, 12, 31))

    def mark(tx):
        patient = tx.patients.get(ORG, env.patient_id)
        tx.patients.save(patient.model_copy(update={"authority_plane": "SQL"}))
    uow.run(mark)
    before = counts(uow, env)
    with pytest.raises(CommandRefusedError) as raised:
        service.confirm(preview.proposal_id, organization_id=ORG, actor_id=ACTOR, today=TODAY)
    assert raised.value.code == "PLANE_NOT_AUTHORITATIVE"
    with pytest.raises(CommandRefusedError):
        service.preview(env.patient_id, organization_id=ORG, actor_id=ACTOR,
                        payload=MarkMissed(logical_key="DOSE:3"), effective_at=date(2026, 2, 1),
                        reason="x", today=TODAY, horizon=date(2026, 12, 31))
    assert counts(uow, env) == before
