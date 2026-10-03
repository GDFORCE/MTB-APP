"""Change 6 - rule scope (case I8, regression matrix row 18).

"Fatal toxicity: accrual is suspended and the DSMC reviews" is a trial-level
rule. The patient engine must never let a SITE/COUNTRY/TRIAL rule add, cancel,
pause or stop a patient's visits, even if its condition is recorded as active.
"""

from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _engine_cases import anchor, approved, by_code, context, evaluate, only, visit

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.conditional import build_conditional_plan
from app.domain.schedule.models import (
    ConditionalAction, ConditionalDefinition, ConditionState, PatientConditionStatus,
    PatientEventStatus, RecurrenceRule, RecurrenceTermination, RepeatBlock, RuleScope,
)
from app.domain.schedule.timing import (
    AnchorReference, OffsetTiming, PositiveTemporalAmount, ProtocolDayTiming,
    TemporalAmount, TimeUnit,
)
from app.domain.schedule.validator import ScheduleValidator

FIRST_DOSE = date(2026, 10, 5)
EVENT_DATE = date(2026, 10, 20)

FATAL_TOXICITY = ComparisonCondition(
    operator="EQUALS", left=FieldOperand(field="fatal_toxicity"),
    right=LiteralOperand(value=True))


def schedule_with(scope: RuleScope | None):
    treatment = visit("TREATMENT", ProtocolDayTiming(
        reference=AnchorReference(code="FIRST_DOSE"), day=1,
    ), event_type="TREATMENT", recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
        start_reference=AnchorReference(code="FIRST_DOSE"),
        termination=RecurrenceTermination(type="COUNT", count=4),
    ))
    ecg = visit("ECG", OffsetTiming(
        reference=AnchorReference(code="FIRST_DOSE"),
        offset=TemporalAmount(value=14, unit=TimeUnit.DAY)))
    review = visit("SAFETY_REVIEW", OffsetTiming(
        reference=AnchorReference(code="FIRST_DOSE"),
        offset=TemporalAmount(value=0, unit=TimeUnit.DAY)))
    values = {
        "code": "FATAL_TOXICITY", "protocol_label": "Fatal toxicity",
        "display_name": "Fatal toxicity - accrual suspended, DSMC review",
        "condition": FATAL_TOXICITY,
        "actions": [
            ConditionalAction(action_type="STOP_BLOCK", target_code="TREATMENT_BLOCK",
                              condition=FATAL_TOXICITY),
            ConditionalAction(action_type="CANCEL_EVENT", target_code="ECG",
                              condition=FATAL_TOXICITY),
            ConditionalAction(action_type="ADD_EVENT", target_code="SAFETY_REVIEW",
                              condition=FATAL_TOXICITY),
        ],
    }
    if scope is not None:
        values["scope"] = scope
    return approved(
        [treatment, ecg, review], anchors=[anchor("FIRST_DOSE")],
        conditionals=[ConditionalDefinition(**values)],
        repeat_blocks=[RepeatBlock(
            code="TREATMENT_BLOCK", protocol_label="Treatment", display_name="Treatment",
            event_codes=["TREATMENT"], dependency_mode="NOMINAL")],
    )


def active_context(schedule):
    return context(schedule, anchors={"FIRST_DOSE": FIRST_DOSE}, conditions={
        "FATAL_TOXICITY": PatientConditionStatus(
            condition_code="FATAL_TOXICITY", state=ConditionState.ACTIVE,
            occurrence_date=EVENT_DATE)})


@pytest.mark.parametrize("scope", [RuleScope.TRIAL, RuleScope.SITE, RuleScope.COUNTRY])
def test_i8_trial_level_rule_never_changes_a_patient_schedule(scope):
    schedule = schedule_with(scope)
    ctx = active_context(schedule)

    plan = build_conditional_plan(schedule, ctx)
    assert plan.activations == {}
    assert plan.cancelled_event_codes == {}
    assert plan.block_directives == {}
    assert plan.refused_rules == {"FATAL_TOXICITY": scope.value}

    result = evaluate(schedule, ctx)
    treatment = by_code(result)["TREATMENT"]
    assert [item.status for item in treatment] == [PatientEventStatus.RESOLVED] * 4
    assert [item.timing.nominal_start for item in treatment] == [
        date(2026, 10, 5), date(2026, 10, 26), date(2026, 11, 16), date(2026, 12, 7)]
    assert only(result, "ECG").status == PatientEventStatus.RESOLVED
    # The visit a trial rule would add stays undated - never a normal scheduled visit.
    review = only(result, "SAFETY_REVIEW")
    assert review.status == PatientEventStatus.WAITING_FOR_CONDITION
    assert review.timing is None
    assert review.explanation["refused_trial_level_rule"] == {
        "rule": "FATAL_TOXICITY", "scope": scope.value}


@pytest.mark.parametrize("scope", [None, RuleScope.PATIENT, RuleScope.ARM, RuleScope.COHORT])
def test_patient_scoped_rules_still_act(scope):
    schedule = schedule_with(scope)
    result = evaluate(schedule, active_context(schedule))

    treatment = by_code(result)["TREATMENT"]
    assert [item.status for item in treatment] == [
        PatientEventStatus.RESOLVED, PatientEventStatus.CANCELLED,
        PatientEventStatus.CANCELLED, PatientEventStatus.CANCELLED]
    assert only(result, "ECG").status == PatientEventStatus.CANCELLED
    assert only(result, "SAFETY_REVIEW").timing.nominal_start == FIRST_DOSE


def test_default_scope_is_patient_for_stored_definitions():
    stored = schedule_with(None).conditional_definitions[0].model_dump(mode="json")
    stored.pop("scope")
    assert ConditionalDefinition.model_validate(stored).scope == RuleScope.PATIENT


def test_validator_says_a_trial_rule_will_not_act_on_patients():
    issues = ScheduleValidator().validate(schedule_with(RuleScope.TRIAL))
    found = [item for item in issues
             if item.issue_code == "TRIAL_LEVEL_RULE_NOT_APPLIED_TO_PATIENTS"]
    assert len(found) == 1
    assert found[0].blocking is False
    assert found[0].details["scope"] == "TRIAL"
    assert found[0].details["action_targets"] == ["ECG", "SAFETY_REVIEW", "TREATMENT_BLOCK"]
    assert "CANCEL_EVENT" in found[0].message

    patient = ScheduleValidator().validate(schedule_with(RuleScope.PATIENT))
    assert not [item for item in patient
                if item.issue_code == "TRIAL_LEVEL_RULE_NOT_APPLIED_TO_PATIENTS"]
