"""Capability registry and unsupported-operator gating (RL-01, RL-12, WP2).

Synthetic schedules only. The UNSUPPORTED placeholder nodes stand in for what
extraction emits when a source requirement uses an operator the language
cannot execute yet.
"""

from __future__ import annotations

import typing

import pytest

from _engine_cases import anchor, approved, context, evaluate, visit
from test_engine_constraints import (
    A_DATE, all_of, at01_schedule, blocking_codes, constrained, lower, ref, schedule_with, shift,
)

from app.domain.schedule import capabilities
from app.domain.schedule.condition import ConditionExpression, UnsupportedCondition
from app.domain.schedule.models import (
    ConditionalAction,
    ConditionalDefinition,
    IssueStatus,
    ValidationIssue,
    Severity,
)
from app.domain.schedule.timing import (
    AnchorReference,
    Constraint,
    EventValue,
    OccurrenceSelector,
    OffsetTiming,
    ProtocolDefinedTiming,
    TemporalAmount,
    TemporalValue,
    TimingExpression,
    UnsupportedConstraint,
)
from app.domain.schedule.validator import ScheduleValidator


def _union_types(annotated) -> list[str]:
    union = typing.get_args(annotated)[0]
    return [typing.get_args(member.model_fields["type"].annotation)[0] for member in typing.get_args(union)]


def test_every_language_node_is_declared_in_the_registry_with_a_version():
    declared = {(item["family"], item["operator"]) for item in capabilities.supported_operators()["operators"]}
    for family, annotated in (("TIMING", TimingExpression), ("TEMPORAL_VALUE", TemporalValue)):
        for operator in _union_types(annotated):
            assert (family, operator) in declared, (family, operator)
    for operator in _union_types(Constraint):
        family = "CONSTRAINT"
        if operator != "UNSUPPORTED":
            assert capabilities.is_executable(family, operator)
    for operator in _union_types(ConditionExpression):
        if operator != "UNSUPPORTED":
            assert capabilities.is_executable("CONDITION", operator)
    for operator in typing.get_args(ConditionalAction.model_fields["action_type"].annotation):
        assert capabilities.is_executable("CONDITIONAL_ACTION", operator)
    registry = capabilities.supported_operators()
    assert registry["registry_version"] == capabilities.REGISTRY_VERSION
    assert all(item["version"] for item in registry["operators"])
    # UNSUPPORTED is never executable, in any family.
    assert not capabilities.is_executable("CONSTRAINT", "UNSUPPORTED")
    assert not capabilities.is_executable("RECURRENCE", "WEEKDAY")


def _chain_schedule(gap_inside_all: bool = True):
    """X has a capability gap; Y is dated from X's derived anchor; Z from Y."""
    gap = UnsupportedConstraint(
        id="gap-1", family="RECURRENCE", source_operator="WEEKDAYS_MON_WED_FRI",
        source_text="radiotherapy on Mon/Wed/Fri", evidence_refs=[])
    constraint = all_of(lower(ref("A_END"), "x-lo"), gap) if gap_inside_all else gap
    x = visit("X", constrained(constraint))
    y = visit("Y", OffsetTiming(reference=AnchorReference(code="X_DONE"),
                                offset=TemporalAmount(value=7, unit="DAY")))
    z = visit("Z", constrained(lower(shift(ref("A_END"), 1), "z-lo")),
              constraints=lower(EventValue(
                  event_code="Y", occurrence=OccurrenceSelector(type="FIRST"),
                  accepts="PLANNED_OR_ACTUAL"), "z-after-y"))
    independent = visit("W", OffsetTiming(reference=AnchorReference(code="A_END"),
                                          offset=TemporalAmount(value=3, unit="DAY")))
    schedule = schedule_with([x, y, z, independent], anchors=[
        anchor("A_END"), anchor("X_DONE", source_event_code="X")])
    evidence = str(schedule.evidence[0].id)
    gap.evidence_refs = [evidence]
    return schedule, evidence


def test_nested_unsupported_operator_blocks_with_evidence_and_transitive_dependents():
    schedule, evidence = _chain_schedule()
    issues = ScheduleValidator().validate(schedule)
    [gap] = [item for item in issues if item.issue_code == "UNSUPPORTED_OPERATOR"]
    assert gap.blocking and gap.entity_type == "EVENT"
    assert gap.details["family"] == "RECURRENCE"
    assert gap.details["source_operator"] == "WEEKDAYS_MON_WED_FRI"
    assert gap.details["source_text"] == "radiotherapy on Mon/Wed/Fri"
    assert gap.details["evidence_refs"] == [evidence]
    assert gap.details["path"] == "timing/constraint/items/1"
    assert gap.details["rule_id"] == "gap-1"
    assert gap.details["affected_dependents"] == ["X", "Y", "Z"]  # W is independent
    assert gap.details["acknowledgeable"] is False
    dependents = sorted(item.details["event_code"] for item in issues
                        if item.issue_code == "DEPENDS_ON_UNSUPPORTED_OPERATOR")
    assert dependents == ["Y", "Z"]
    with pytest.raises(ValueError):
        evaluate(schedule, context(schedule, anchors={"A_END": A_DATE}))


def test_acknowledgement_cannot_make_an_unsupported_operator_executable():
    schedule, _ = _chain_schedule()
    issues = ScheduleValidator().validate(schedule)
    for item in issues:
        item.status = IssueStatus.ACCEPTED
    remaining = {item.issue_code for item in ScheduleValidator.blocking(issues)}
    assert remaining == {"UNSUPPORTED_OPERATOR", "DEPENDS_ON_UNSUPPORTED_OPERATOR"}
    # An extraction-origin capability gap that someone marked ACCEPTED is still carried.
    ok = at01_schedule()
    ok.validation_issues.append(ValidationIssue(
        issue_code="UNSUPPORTED_OPERATOR", severity=Severity.ERROR, blocking=True,
        message="extraction found an unsupported rule", status=IssueStatus.DISMISSED,
        details={"origin": "EXTRACTION", "source_operator": "HOLIDAY_SHIFT"}))
    assert "UNSUPPORTED_OPERATOR" in blocking_codes(ok)


def test_unsupported_condition_in_a_conditional_definition_blocks_its_targets():
    target = visit("RESCUE", OffsetTiming(reference=AnchorReference(code="A_END"),
                                          offset=TemporalAmount(value=1, unit="DAY")))
    definition = ConditionalDefinition(
        code="TOX", protocol_label="Toxicity", display_name="Toxicity",
        condition=UnsupportedCondition(source_operator="CTCAE_TREND"),
        actions=[ConditionalAction(action_type="ADD_EVENT", target_code="RESCUE",
                                   condition=UnsupportedCondition(source_operator="CTCAE_TREND"))])
    schedule = approved([target], anchors=[anchor("A_END")], conditionals=[definition])
    gaps = [item for item in ScheduleValidator().validate(schedule)
            if item.issue_code == "UNSUPPORTED_OPERATOR"]
    assert {item.details["path"] for item in gaps} == {"condition", "actions/0/condition"}
    assert all(item.entity_type == "CONDITION" for item in gaps)
    assert all(item.details["affected_dependents"] == ["RESCUE"] for item in gaps)


def test_unknown_calendar_policy_is_an_unsupported_operator():
    schedule = at01_schedule(lower(shift(ref("A_END"), 1, "MONTH", "BUSINESS_DAY_ROLL.v9"), "m"))
    [gap] = [item for item in ScheduleValidator().validate(schedule)
             if item.issue_code == "UNSUPPORTED_OPERATOR"]
    assert gap.details["family"] == "CALENDAR_POLICY"
    assert gap.details["operator"] == "BUSINESS_DAY_ROLL.v9"
    assert blocking_codes(at01_schedule(lower(shift(ref("A_END"), 1, "MONTH", "MONTH_END_CLAMP.v1"), "m"))) == []


def test_fully_supported_schedules_report_executable_and_need_no_name_branch():
    report = capabilities.capability_report(at01_schedule())
    assert report["fully_executable"] is True and report["unsupported"] == []
    assert report["operators_used"]["CONSTRAINT:LOWER_BOUND"] == 2
    schedule, _ = _chain_schedule()
    report = capabilities.capability_report(schedule)
    assert report["fully_executable"] is False
    assert report["unsupported"][0]["affected_dependents"] == ["X", "Y", "Z"]


def test_legacy_non_executable_timings_keep_their_existing_gating_nf08():
    on_demand = visit("UNSCHED", ProtocolDefinedTiming(), event_type="UNSCHEDULED", activation="ON_DEMAND")
    schedule = approved([on_demand], anchors=[anchor("A_END")])
    codes = {item.issue_code for item in ScheduleValidator().validate(schedule)}
    assert "UNSUPPORTED_OPERATOR" not in codes
    scheduled = visit("LATER", ProtocolDefinedTiming())
    codes = blocking_codes(approved([scheduled], anchors=[anchor("A_END")]))
    assert "UNSUPPORTED_TIMING" in codes and "UNSUPPORTED_OPERATOR" not in codes


def test_unsupported_placeholder_reaching_the_evaluator_is_unresolved_not_dated():
    """Defence in depth: bypass validation and the node still cannot be executed."""
    from app.domain.schedule.evaluator import apply_event_constraints
    from app.domain.schedule.models import EvaluatedEvent, PatientEventStatus, ResolvedTiming

    schedule, _ = _chain_schedule(gap_inside_all=False)
    event = schedule.events[0]
    item = EvaluatedEvent(event_definition_id=event.id, event_code="X",
                          status=PatientEventStatus.RESOLVED,
                          timing=ResolvedTiming(precision="CONSTRAINT"))
    [result] = apply_event_constraints(event, [item], context(schedule, anchors={"A_END": A_DATE}), {})
    assert result.status == PatientEventStatus.UNRESOLVED and result.timing is None
    assert "WEEKDAYS_MON_WED_FRI" in result.explanation["reason"]
