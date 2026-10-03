"""Scenario D1-D5 end to end: neoadjuvant -> surgery -> adjuvant (matrix 10-12).

docs/schedule-engine-case-handling.md section D, through persistence, review,
enrolment, anchor candidates confirmed via impact preview, the reminder
projection and the deviation report. Last neoadjuvant dose Mon 16-Nov-2026.
"""

from datetime import date

import pytest

from _engine_cases import anchor, approved, visit
from _scenario_harness import (
    check_invariants, new_trial, reminder_keys, statuses,
)

from app.domain.schedule.anchor_status import AnchorStatus
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.deviation import DeviationType
from app.domain.schedule.models import (
    ConditionalAction, ConditionalDefinition, ConditionState,
)
from app.domain.schedule.timing import (
    AnchorAcceptance, AnchorReference, OffsetTiming, PositiveTemporalAmount,
    RangeTiming, TemporalAmount, TimeUnit, WithinTiming,
)
from app.services.anchor_status_service import AnchorStatusService

LAST_NEOADJ_DOSE = date(2026, 11, 16)
PLANNED_SURGERY = date(2026, 12, 14)
PRE_OP_DONE = date(2026, 12, 10)
ACTUAL_SURGERY = date(2026, 12, 17)
DELAYED_SURGERY = date(2027, 1, 4)
PROGRESSION = date(2026, 12, 1)

BRANCH = ("SURGERY:0", "POST_OP:0", "ADJUVANT_START:0")

PROGRESSED = ComparisonCondition(
    operator="EQUALS", left=FieldOperand(field="disease_status"),
    right=LiteralOperand(value="PROGRESSION"))


def weeks(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit=TimeUnit.WEEK)


def pathway():
    """The rule table under section D, including the progression branch."""
    events = [
        visit("SURGERY", RangeTiming(
            reference=AnchorReference(code="LAST_NEOADJ_DOSE"), start=weeks(3), end=weeks(6),
        ), event_type="PROCEDURE"),
        visit("PRE_OP", WithinTiming(
            reference=AnchorReference(code="SURGERY_DATE", accepts=AnchorAcceptance.PLANNED_OR_ACTUAL),
            duration=PositiveTemporalAmount(value=14, unit=TimeUnit.DAY), direction="BEFORE",
        ), event_type="ASSESSMENT"),
        visit("POST_OP", OffsetTiming(
            reference=AnchorReference(code="SURGERY_DATE"),
            offset=TemporalAmount(value=30, unit=TimeUnit.DAY),
        )),
        visit("ADJUVANT_START", RangeTiming(
            reference=AnchorReference(code="SURGERY_DATE"), start=weeks(4), end=weeks(8),
        ), event_type="TREATMENT"),
        visit("SURVIVAL_FOLLOW_UP", OffsetTiming(
            reference=AnchorReference(code="PROGRESSION_DATE"),
            offset=TemporalAmount(value=30, unit=TimeUnit.DAY),
        ), event_type="TELEPHONE_CONTACT", visit_mode="TELEPHONE"),
    ]
    progression = ConditionalDefinition(
        code="PROGRESSION", protocol_label="Progression before surgery",
        display_name="Progression before surgery", condition=PROGRESSED,
        actions=[
            *(ConditionalAction(action_type="CANCEL_EVENT", target_code=code, condition=PROGRESSED)
              for code in ("SURGERY", "POST_OP", "ADJUVANT_START")),
            ConditionalAction(action_type="ADD_EVENT", target_code="SURVIVAL_FOLLOW_UP",
                              condition=PROGRESSED),
        ],
    )
    return approved(events, anchors=[
        anchor("LAST_NEOADJ_DOSE"),
        anchor("SURGERY_DATE", source_event_code="SURGERY"),
        anchor("PROGRESSION_DATE", anchor_type="CONDITION", source_condition_code="PROGRESSION"),
    ], conditionals=[progression])


@pytest.fixture()
def trial():
    trial, engine = new_trial()
    trial.publish(pathway())
    yield trial
    trial.session.close()
    engine.dispose()


def anchor_view(trial, patient, code: str):
    views = AnchorStatusService(trial.session).statuses(
        patient.id, organization_id=trial.organization_id)
    return next(item for item in views if item.anchor_code == code)


def window(row) -> tuple[date | None, date | None]:
    return row.earliest_date, row.latest_date


def test_d1_to_d4_neoadjuvant_surgery_adjuvant_chain(trial):
    patient = trial.enrol("P-SURG-1", anchors={"LAST_NEOADJ_DOSE": LAST_NEOADJ_DOSE})

    # D1 - surgery not yet scheduled: only the surgery window is shown.
    rows = trial.rows(patient)
    assert rows["SURGERY:0"].status == "RESOLVED"
    assert window(rows["SURGERY:0"]) == (date(2026, 12, 7), date(2026, 12, 28))
    assert statuses(rows, "PRE_OP:0", "POST_OP:0", "ADJUVANT_START:0") == ["WAITING_FOR_ANCHOR"] * 3
    assert rows["SURVIVAL_FOLLOW_UP:0"].status == "WAITING_FOR_CONDITION"
    assert anchor_view(trial, patient, "SURGERY_DATE").status == AnchorStatus.AWAITING_EVENT
    today = date(2026, 11, 20)
    assert reminder_keys(trial.projection(patient, today)) == {"SURGERY:0"}
    history = check_invariants(trial, patient, today=today)

    # D2 - surgery PLANNED for 14-Dec: the preview says it is a plan, pre-op is
    # dated from it, post-op and adjuvant still wait for the real date.
    proposal, rows = trial.record_anchor(
        patient, "SURGERY_DATE", PLANNED_SURGERY, "PLANNED_PROCEDURE")
    assert proposal.impact["anchor_change"]["planned"] is True
    changes = {item["logical_key"]: item for item in proposal.impact["events"]}
    assert changes["PRE_OP:0"]["after"]["status"] == "RESOLVED"
    assert changes["POST_OP:0"]["change"] == "UNCHANGED"
    assert changes["ADJUVANT_START:0"]["change"] == "UNCHANGED"
    assert rows["PRE_OP:0"].status == "RESOLVED"
    assert window(rows["PRE_OP:0"]) == (date(2026, 11, 30), date(2026, 12, 14))
    assert rows["PRE_OP:0"].generation_reason["anchor_basis"] == {"SURGERY_DATE": "PLANNED"}
    for key in ("POST_OP:0", "ADJUVANT_START:0"):
        assert rows[key].status == "WAITING_FOR_ANCHOR", key
        assert rows[key].nominal_start_date is None
        assert rows[key].generation_reason["waiting_for_actual_anchor"] == ["SURGERY_DATE"]
    assert anchor_view(trial, patient, "SURGERY_DATE").status == AnchorStatus.PLANNED
    today = date(2026, 12, 1)
    projection = trial.projection(patient, today)
    # "Reminders on" for the pre-op window; none for the post-surgery visits.
    assert reminder_keys(projection) == {"SURGERY:0", "PRE_OP:0"}
    pre_op = next(item for item in projection.reminders if item.logical_key == "PRE_OP:0")
    assert pre_op.nominal_date == date(2026, 11, 30)  # the window start (doc A7)
    assert pre_op.policy_state == "DUE_WINDOW"
    history = check_invariants(trial, patient, today=today, completed_before=history)

    # Pre-op assessment done 10-Dec, inside its window.
    trial.record_actual(patient, "PRE_OP:0", PRE_OP_DONE)
    history = check_invariants(trial, patient, today=date(2026, 12, 11), completed_before=history)
    assert "PRE_OP:0" in history

    # D3 - surgery actually happens 17-Dec.
    proposal, rows = trial.record_anchor(
        patient, "SURGERY_DATE", ACTUAL_SURGERY, "PROCEDURE_RECORD")
    assert proposal.impact["anchor_change"]["planned"] is False
    assert rows["POST_OP:0"].status == "RESOLVED"
    assert rows["POST_OP:0"].nominal_start_date == date(2027, 1, 16)
    assert rows["POST_OP:0"].generation_reason["anchor_basis"] == {"SURGERY_DATE": "ACTUAL"}
    assert rows["ADJUVANT_START:0"].status == "RESOLVED"
    assert window(rows["ADJUVANT_START:0"]) == (date(2027, 1, 14), date(2027, 2, 11))
    # The completed pre-op keeps its own window - it is never re-dated from surgery.
    assert rows["PRE_OP:0"].status == "COMPLETED"
    assert window(rows["PRE_OP:0"]) == (date(2026, 11, 30), date(2026, 12, 14))
    # The planned value was replaced by what happened: actual, not a "correction".
    assert anchor_view(trial, patient, "SURGERY_DATE").status == AnchorStatus.CONFIRMED
    today = date(2026, 12, 18)
    assert {"POST_OP:0", "ADJUVANT_START:0"} <= reminder_keys(trial.projection(patient, today))
    history = check_invariants(trial, patient, today=today, completed_before=history)

    # D4 - the surgery date is recorded again as 04-Jan-2027: preview shows what
    # moves, confirm applies it, the completed pre-op does not move.
    candidate = trial.anchor_candidate(patient, "SURGERY_DATE", DELAYED_SURGERY, "PROCEDURE_RECORD")
    proposal = trial.preview(patient, anchor_candidate=candidate, reason="surgery date corrected")
    changes = {item["logical_key"]: item for item in proposal.impact["events"]}
    assert changes["POST_OP:0"]["change"] == "MOVED"
    assert changes["POST_OP:0"]["before"]["date"] == "2027-01-16"
    assert changes["POST_OP:0"]["after"]["date"] == "2027-02-03"
    assert changes["ADJUVANT_START:0"]["change"] == "MOVED"
    assert changes["PRE_OP:0"]["change"] == "PROTECTED"
    assert changes["SURGERY:0"]["change"] == "UNCHANGED"
    assert proposal.impact["anchor_change"]["previous_value"] == "2026-12-17"
    # Nothing moved until confirmation.
    assert trial.rows(patient)["POST_OP:0"].nominal_start_date == date(2027, 1, 16)
    rows = trial.confirm(proposal)
    assert rows["POST_OP:0"].nominal_start_date == date(2027, 2, 3)
    assert window(rows["ADJUVANT_START:0"]) == (date(2027, 2, 1), date(2027, 3, 1))
    assert rows["PRE_OP:0"].status == "COMPLETED"
    assert rows["PRE_OP:0"].protected_history is True
    assert anchor_view(trial, patient, "SURGERY_DATE").status == AnchorStatus.CORRECTED
    changed = trial.audit("PATIENT_ANCHOR_CHANGED")
    assert changed[-1].after["value"] == "2027-01-04"
    assert changed[-1].after["previous_value"] == "2026-12-17"
    history = check_invariants(trial, patient, today=date(2027, 1, 5), completed_before=history)

    # The operation itself was performed outside the protocol surgery window.
    trial.record_actual(patient, "SURGERY:0", DELAYED_SURGERY)
    trial.evaluate(patient)
    report = trial.deviations(patient, today=date(2027, 1, 5))
    assert report["SURGERY:0"].deviation_type == DeviationType.LATE
    assert report["SURGERY:0"].outside_window_days == 7
    assert report["PRE_OP:0"].deviation_type == DeviationType.NONE
    assert report["PRE_OP:0"].within_window is True
    check_invariants(trial, patient, today=date(2027, 1, 5), completed_before=history)


def test_d5_progression_before_surgery_cancels_the_surgical_branch(trial):
    patient = trial.enrol("P-SURG-2", anchors={"LAST_NEOADJ_DOSE": LAST_NEOADJ_DOSE})
    history = check_invariants(trial, patient, today=date(2026, 11, 25))

    # A pending progression changes nothing.
    proposal, rows = trial.condition(
        patient, "PROGRESSION", ConditionState.PENDING_CONFIRMATION, PROGRESSION)
    assert proposal.impact["summary"]["MOVED"] == 0
    assert proposal.impact["summary"]["STATUS_CHANGED"] == 0
    assert rows["SURGERY:0"].status == "RESOLVED"
    assert rows["SURVIVAL_FOLLOW_UP:0"].status == "WAITING_FOR_CONDITION"

    # The PI confirms progression on 01-Dec-2026.
    proposal, rows = trial.condition(patient, "PROGRESSION", ConditionState.ACTIVE, PROGRESSION)
    changes = {item["logical_key"]: item for item in proposal.impact["events"]}
    assert changes["SURGERY:0"]["after"]["status"] == "CANCELLED"
    assert changes["SURVIVAL_FOLLOW_UP:0"]["after"] == {"date": "2026-12-31", "status": "RESOLVED"}
    for key in BRANCH:
        assert rows[key].status == "CANCELLED", key
        assert rows[key].nominal_start_date is None
        assert rows[key].generation_reason["cancelled_by_condition"] == "PROGRESSION"
    assert rows["SURVIVAL_FOLLOW_UP:0"].status == "RESOLVED"
    assert rows["SURVIVAL_FOLLOW_UP:0"].nominal_start_date == date(2026, 12, 31)
    # Pre-op never had a surgery date to count from, so it stays undated.
    assert rows["PRE_OP:0"].status == "WAITING_FOR_ANCHOR"
    audit = trial.audit("PATIENT_CONDITION_STATE_CHANGED")
    assert [item.after["state"] for item in audit] == ["PENDING_CONFIRMATION", "ACTIVE"]

    # Nothing in the cancelled branch is reminded about or ever overdue - even
    # long after the surgery window has closed.
    for today in (date(2026, 12, 2), date(2027, 6, 1)):
        projection = trial.projection(patient, today)
        assert reminder_keys(projection) & set(BRANCH) == set()
        report = trial.deviations(patient, today=today)
        for key in (*BRANCH, "PRE_OP:0"):
            assert report[key].deviation_type == DeviationType.NOT_ASSESSABLE, key
    projection = trial.projection(patient, date(2026, 12, 2))
    survival = next(item for item in projection.reminders
                    if item.logical_key == "SURVIVAL_FOLLOW_UP:0")
    assert survival.requires_attendance is False
    assert survival.policy_state == "UPCOMING"
    check_invariants(trial, patient, today=date(2026, 12, 2), completed_before=history)
