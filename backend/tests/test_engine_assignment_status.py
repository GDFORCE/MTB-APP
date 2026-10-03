"""Change 7 - WAITING_FOR_ASSIGNMENT (case H2, late randomisation).

Before randomisation every arm visit is waiting for an assignment, not for a
clinical condition. Like every waiting status it has no date, no reminder, can
never be overdue, and is NOT_ASSESSABLE for deviation.
"""

from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _engine_cases import anchor, approved, context, evaluate, only, visit

from app.domain.schedule.deviation import DeviationType, assess_visit
from app.domain.schedule.models import (
    ApplicabilityRule, GenericDimension, PatientEventStatus, StudyDimension,
)
from app.domain.schedule.timing import AnchorReference, ProtocolDayTiming
from app.services.operational_projection import _operational_status
from app.services.schedule_projection import ProjectedEvent, project

REGISTRATION = date(2026, 10, 1)
RANDOMISATION = date(2026, 10, 29)


def day(code: str, anchor_code: str, number: int, **changes):
    return visit(code, ProtocolDayTiming(
        reference=AnchorReference(code=anchor_code), day=number), **changes)


def arm(code: str) -> list[ApplicabilityRule]:
    return [ApplicabilityRule(dimension="ARM", operator="IN", values=[code])]


def run_in_then_randomise():
    """4-week run-in from registration; arm visits from the randomisation date."""
    events = [
        day("RUN_IN_D1", "REGISTRATION", 1),
        day("RUN_IN_D15", "REGISTRATION", 15),
        # Arm A (paclitaxel): Day 1 every 3 weeks.
        day("A_C1D1", "RANDOMISATION", 1, applicability=arm("A")),
        day("A_C2D1", "RANDOMISATION", 22, applicability=arm("A")),
        day("A_C3D1", "RANDOMISATION", 43, applicability=arm("A")),
        # Arm B (nab-paclitaxel): Days 1, 8, 15; next cycle Day 1.
        day("B_C1D1", "RANDOMISATION", 1, applicability=arm("B")),
        day("B_C1D8", "RANDOMISATION", 8, applicability=arm("B")),
        day("B_C1D15", "RANDOMISATION", 15, applicability=arm("B")),
        day("B_C2D1", "RANDOMISATION", 22, applicability=arm("B")),
    ]
    return approved(events, anchors=[anchor("REGISTRATION"), anchor("RANDOMISATION")], arms=[
        StudyDimension(code="A", protocol_label="Arm A", display_name="Arm A"),
        StudyDimension(code="B", protocol_label="Arm B", display_name="Arm B"),
    ])


ARM_CODES = ("A_C1D1", "A_C2D1", "A_C3D1", "B_C1D1", "B_C1D8", "B_C1D15", "B_C2D1")


def test_h2_before_randomisation_arm_visits_wait_for_assignment():
    schedule = run_in_then_randomise()
    result = evaluate(schedule, context(schedule, anchors={"REGISTRATION": REGISTRATION}))

    assert only(result, "RUN_IN_D1").timing.nominal_start == date(2026, 10, 1)
    assert only(result, "RUN_IN_D15").timing.nominal_start == date(2026, 10, 15)
    for code in ARM_CODES:
        item = only(result, code)
        assert item.status == PatientEventStatus.WAITING_FOR_ASSIGNMENT, code
        assert item.timing is None
        assert item.explanation["waiting_for_assignment"] == ["ARM"]


def test_h2_randomised_to_arm_b_dates_arm_b_and_drops_arm_a():
    schedule = run_in_then_randomise()
    result = evaluate(schedule, context(schedule, arm_code="B", anchors={
        "REGISTRATION": REGISTRATION, "RANDOMISATION": RANDOMISATION}))

    assert [only(result, code).timing.nominal_start for code in (
        "B_C1D1", "B_C1D8", "B_C1D15", "B_C2D1")] == [
        date(2026, 10, 29), date(2026, 11, 5), date(2026, 11, 12), date(2026, 11, 19)]
    for code in ("A_C1D1", "A_C2D1", "A_C3D1"):
        assert only(result, code).status == PatientEventStatus.NOT_APPLICABLE


def test_h2_arm_a_comparison_dates():
    schedule = run_in_then_randomise()
    result = evaluate(schedule, context(schedule, arm_code="A", anchors={
        "REGISTRATION": REGISTRATION, "RANDOMISATION": RANDOMISATION}))
    assert [only(result, code).timing.nominal_start for code in (
        "A_C1D1", "A_C2D1", "A_C3D1")] == [
        date(2026, 10, 29), date(2026, 11, 19), date(2026, 12, 10)]


def test_an_unknown_clinical_attribute_still_waits_for_a_condition():
    """H5: an unknown age is a clinical fact, not an assignment."""
    events = [
        day("ECG_65", "REGISTRATION", 1, applicability=[
            ApplicabilityRule(dimension="AGE", operator="GTE", values=["65"])]),
        day("ARM_A_ECG_65", "REGISTRATION", 1, applicability=[
            *arm("A"), ApplicabilityRule(dimension="AGE", operator="GTE", values=["65"])]),
    ]
    schedule = approved(events, anchors=[anchor("REGISTRATION")], arms=[
        StudyDimension(code="A", protocol_label="Arm A", display_name="Arm A")])
    result = evaluate(schedule, context(schedule, anchors={"REGISTRATION": REGISTRATION}))
    assert only(result, "ECG_65").status == PatientEventStatus.WAITING_FOR_CONDITION
    # Randomisation alone would not settle it, so it is not claimed as assignment-only.
    assert only(result, "ARM_A_ECG_65").status == PatientEventStatus.WAITING_FOR_CONDITION


def test_a_generic_study_dimension_is_an_assignment():
    """H7: a crossover sequence is assigned, like an arm."""
    events = [day("PERIOD_1_DRUG_A", "REGISTRATION", 1, applicability=[
        ApplicabilityRule(dimension="SEQUENCE", operator="IN", values=["AB"])])]
    schedule = approved(events, anchors=[anchor("REGISTRATION")], dimensions=[
        GenericDimension(code="AB", protocol_label="AB", display_name="Sequence AB",
                         dimension_type="SEQUENCE"),
        GenericDimension(code="BA", protocol_label="BA", display_name="Sequence BA",
                         dimension_type="SEQUENCE"),
    ])
    result = evaluate(schedule, context(schedule, anchors={"REGISTRATION": REGISTRATION}))
    item = only(result, "PERIOD_1_DRUG_A")
    assert item.status == PatientEventStatus.WAITING_FOR_ASSIGNMENT
    assert item.explanation["waiting_for_assignment"] == ["SEQUENCE"]

    assigned = evaluate(schedule, context(
        schedule, anchors={"REGISTRATION": REGISTRATION},
        dimension_values={"SEQUENCE": ["AB"]}))
    assert only(assigned, "PERIOD_1_DRUG_A").timing.nominal_start == REGISTRATION


def test_waiting_for_assignment_is_never_reminded_about():
    result = project([ProjectedEvent(
        logical_key="B_C1D1:0", event_code="B_C1D1", display_name="Arm B C1D1",
        status="WAITING_FOR_ASSIGNMENT", event_type="SITE_VISIT", nominal_date=None,
    )], today=date(2026, 10, 20))
    assert result.reminders == []
    assert result.calendar == []
    assert any("WAITING_FOR_ASSIGNMENT" in item.reason for item in result.suppressed)


@pytest.mark.parametrize("today", [date(2026, 10, 20), date(2027, 6, 1)])
def test_waiting_for_assignment_is_not_assessable_and_never_missed(today):
    deviation = assess_visit(
        logical_key="B_C1D1:0", event_code="B_C1D1", display_name="Arm B C1D1",
        status="WAITING_FOR_ASSIGNMENT", planned_date=None, earliest_date=None,
        latest_date=None, actual_date=None, today=today,
    )
    assert deviation.deviation_type == DeviationType.NOT_ASSESSABLE
    assert "assigned" in deviation.reason


def test_operational_projection_sends_it_to_review_not_to_the_calendar():
    state, reason = _operational_status("WAITING_FOR_ASSIGNMENT")
    assert state == "manual_review"
    assert reason == "Waiting For Assignment"
