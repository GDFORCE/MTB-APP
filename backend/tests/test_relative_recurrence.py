from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Activity, Anchor, Event, PatientContext, RecurrenceRule, RecurrenceTermination,
    ScheduleMetadata, UniversalSchedule,
)
from app.domain.schedule.validator import ScheduleValidator


def schedule_for(*, inclusive=True, end_unit="WEEK", end_value=8, end_code="BASELINE", start=2, interval=2):
    event = Event(code="LABS", protocol_label="Labs", display_name="Labs", event_type="ASSESSMENT",
        timing={"type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"},
            "offset": {"value": start, "unit": "WEEK"}},
        recurrence=RecurrenceRule(interval={"value": interval, "unit": "WEEK"},
            start_reference={"kind": "ANCHOR", "code": "BASELINE"},
            start_offset={"value": start, "unit": "WEEK"}, include_start=True,
            termination=RecurrenceTermination(type="RELATIVE", end_reference={"code": end_code},
                end_offset={"value": end_value, "unit": end_unit}, inclusive=inclusive)))
    return UniversalSchedule(schedule_metadata=ScheduleMetadata(name="Test", status="APPROVED"), events=[event],
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE", status="RESOLVED")])


def evaluate(schedule, baseline=date(2026, 1, 1), horizon=date(2027, 1, 1)):
    from test_uctsm_rolling_horizon import approved
    # Supply the same traceability requirements as a real approved schedule.
    traced = approved(schedule.events)
    traced.anchors = schedule.anchors
    schedule = traced
    context = PatientContext(patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"BASELINE": baseline})
    return ScheduleEvaluator().evaluate(schedule, context, horizon=horizon, today=baseline)


@pytest.mark.parametrize("inclusive,expected", [
    (True, [date(2026, 1, 15), date(2026, 1, 29), date(2026, 2, 12), date(2026, 2, 26)]),
    (False, [date(2026, 1, 15), date(2026, 1, 29), date(2026, 2, 12)]),
])
def test_relative_phase_boundary_without_inventing_a_visit_count(inclusive, expected):
    schedule = schedule_for(inclusive=inclusive)
    restored = UniversalSchedule.model_validate_json(schedule.model_dump_json())
    assert restored.events[0].recurrence.termination.count is None
    assert [event.timing.nominal_start for event in evaluate(restored).events] == expected


def test_calendar_month_end_is_not_a_thirty_day_approximation():
    schedule = schedule_for(end_unit="MONTH", end_value=1, start=0, interval=1)
    assert [event.timing.nominal_start for event in evaluate(schedule, date(2026, 1, 31)).events] == [
        date(2026, 1, 31), date(2026, 2, 7), date(2026, 2, 14), date(2026, 2, 21), date(2026, 2, 28)]


def test_missing_end_anchor_blocks_validation_and_waits_for_a_patient_value():
    schedule = schedule_for(end_code="LAST_DOSE")
    assert any(issue.issue_code == "UNKNOWN_ANCHOR" for issue in ScheduleValidator().validate(schedule))
    schedule.anchors.append(Anchor(code="LAST_DOSE", display_name="Last dose", anchor_type="LAST_DOSE", status="RESOLVED"))
    result = evaluate(schedule).events
    assert len(result) == 1 and result[0].status.value == "WAITING_FOR_ANCHOR"
    assert result[0].explanation["waiting_for_anchor"] == "LAST_DOSE"


def test_horizon_still_limits_a_relative_phase():
    result = evaluate(schedule_for(), horizon=date(2026, 2, 1))
    assert [event.timing.nominal_start for event in result.events] == [date(2026, 1, 15), date(2026, 1, 29)]


def test_relative_boundary_requires_explicit_inclusion_and_offset():
    for missing in ["inclusive", "end_offset", "end_reference"]:
        values = dict(type="RELATIVE", end_reference={"code": "BASELINE"},
            end_offset={"value": 6, "unit": "MONTH"}, inclusive=True)
        values.pop(missing)
        with pytest.raises(ValidationError):
            RecurrenceTermination.model_validate(values)


def test_conditional_activity_requires_a_rule_not_only_a_label():
    schedule = schedule_for()
    schedule.events[0].activities = [Activity(code="TEST", protocol_label="Test", display_name="Test",
        activity_type="ASSESSMENT", requiredness="CONDITIONAL")]
    assert any(issue.issue_code == "AMBIGUOUS_CONDITION" for issue in ScheduleValidator().validate(schedule))


def test_two_cadence_phases_do_not_overlap_or_continue_past_their_boundaries():
    schedule = schedule_for(end_value=8)
    later = schedule_for(end_value=24, start=12, interval=4).events[0]
    later.code = "LATER_LABS"
    schedule.events.append(later)
    results = evaluate(schedule).events
    assert [(event.timing.nominal_start - date(2026, 1, 1)).days // 7 for event in results] == [
        2, 4, 6, 8, 12, 16, 20, 24]


@pytest.mark.parametrize("state,expected", [({}, "WAITING_FOR_CONDITION"),
    ({"testing_indicated": False}, "NOT_APPLICABLE"), ({"testing_indicated": True}, "RESOLVED")])
def test_patient_condition_changes_only_its_assessment(state, expected):
    from app.domain.schedule.evaluator import evaluate_activities
    schedule = schedule_for()
    event = schedule.events[0]
    event.activities = [Activity(code="TEST", protocol_label="Test", display_name="Test",
        activity_type="ASSESSMENT", requiredness="CONDITIONAL", conditions=[{
            "type": "COMPARISON", "left": {"source": "FIELD", "field": "testing_indicated"},
            "operator": "EQUALS", "right": {"source": "LITERAL", "value": True}}])]
    context = PatientContext(patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id, state=state)
    assert evaluate_activities(event, context, occurrence_index=0)[0].status.value == expected
    assert event.conditions == [] and event.applicability == []
