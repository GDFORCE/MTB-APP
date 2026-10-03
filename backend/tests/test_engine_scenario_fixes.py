"""Unit tests for the engine gaps the end-to-end scenarios exposed.

* diff.py did not compare repeat blocks, so changing a cycle plan between
  versions was invisible to the amendment reviewer.
* A pause did not cover its own start day (doc I1: C2 due on the hold date
  must be PAUSED).
* Same-day / out-of-order block transitions depended on rule listing order (I7).
* A window-only rule (WITHIN ... BEFORE) was never reminded about and never
  assessed for deviation (doc A7/A8/D2).
* A confirmed PLANNED anchor replaced by the actual date read as CORRECTED.
"""

from datetime import date, datetime, timezone

import pytest

from _engine_cases import anchor, approved, by_code, context, evaluate, visit

from app.domain.schedule.anchor_status import AnchorRecord, AnchorStatus, resolve_anchor_status
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.conditional import build_conditional_plan
from app.domain.schedule.deviation import DeviationType, assess_visit
from app.domain.schedule.diff import ChangeType, Significance, compare_schedule_versions
from app.domain.schedule.models import (
    BlockState, ConditionalAction, ConditionalDefinition, ConditionState, CycleDependencyMode,
    CyclePlan, DependencyMode, PatientConditionStatus, PatientEventStatus, RecurrenceRule,
    RecurrenceTermination, RepeatBlock,
)
from app.domain.schedule.timing import (
    AnchorReference, CycleDayTiming, CycleSelector, PositiveTemporalAmount, ProtocolDayTiming,
    TimeUnit,
)
from app.services.schedule_projection import ProjectedEvent, project


# --- diff: repeat blocks ------------------------------------------------------------

def cycle_block(mode: CycleDependencyMode, *, count: int = 6, resume_mode: str = "UNCLEAR"):
    return RepeatBlock(
        code="TREATMENT", protocol_label="Treatment", display_name="Treatment",
        event_codes=["DAY_1"], resume_mode=resume_mode,
        dependency_mode={
            CycleDependencyMode.NOMINAL: DependencyMode.NOMINAL,
            CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START: DependencyMode.ACTUAL_PREVIOUS_EVENT,
        }[mode],
        cycle_plan=CyclePlan(
            code="CYCLE", start_reference=AnchorReference(code="C1D1"),
            length=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
            dependency_mode=mode, start_event_code="DAY_1",
            termination=RecurrenceTermination(type="COUNT", count=count)),
    )


def cycle_version(*blocks: RepeatBlock):
    return approved([visit("DAY_1", CycleDayTiming(
        cycle_reference="CYCLE", cycle=CycleSelector(type="CURRENT"), day=1))],
        anchors=[anchor("C1D1")], repeat_blocks=list(blocks))


def test_diff_reports_a_changed_cycle_plan():
    before = cycle_version(cycle_block(CycleDependencyMode.NOMINAL))
    after = cycle_version(cycle_block(CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START, count=8))
    changes = [item for item in compare_schedule_versions(before, after).typed_changes
               if item.entity_type == "REPEAT_BLOCK"]
    assert [item.change_type for item in changes] == [ChangeType.REPEAT_BLOCK_CHANGED]
    change = changes[0]
    assert change.entity_code == "TREATMENT"
    assert change.significance == Significance.CLINICAL
    assert set(change.before) == {"cycle_plan", "dependency_mode"}
    assert change.before["cycle_plan"]["dependency_mode"] == "NOMINAL"
    assert change.after["cycle_plan"]["dependency_mode"] == "ACTUAL_PREVIOUS_CYCLE_START"
    assert change.after["cycle_plan"]["termination"]["count"] == 8
    assert "cycle_plan" in change.summary


def test_diff_reports_a_changed_resume_mode_only():
    before = cycle_version(cycle_block(CycleDependencyMode.NOMINAL, resume_mode="NOMINAL"))
    after = cycle_version(cycle_block(CycleDependencyMode.NOMINAL, resume_mode="ACTUAL_RESUME"))
    change = next(item for item in compare_schedule_versions(before, after).typed_changes
                  if item.change_type == ChangeType.REPEAT_BLOCK_CHANGED)
    assert change.before == {"resume_mode": "NOMINAL"}
    assert change.after == {"resume_mode": "ACTUAL_RESUME"}


def test_diff_reports_added_and_removed_blocks_and_ignores_regenerated_ids():
    base = cycle_block(CycleDependencyMode.NOMINAL)
    same = cycle_version(cycle_block(CycleDependencyMode.NOMINAL))  # new id, same meaning
    assert not [item for item in compare_schedule_versions(cycle_version(base), same).typed_changes
                if item.entity_type == "REPEAT_BLOCK"]
    extra = RepeatBlock(code="MAINTENANCE", protocol_label="M", display_name="Maintenance",
                        event_codes=["DAY_1"], dependency_mode=DependencyMode.NOMINAL)
    added = compare_schedule_versions(cycle_version(base), cycle_version(base, extra))
    assert [(item.change_type, item.entity_code) for item in added.typed_changes
            if item.entity_type == "REPEAT_BLOCK"] == [(ChangeType.REPEAT_BLOCK_ADDED, "MAINTENANCE")]
    removed = compare_schedule_versions(cycle_version(base, extra), cycle_version(base))
    assert [(item.change_type, item.entity_code) for item in removed.typed_changes
            if item.entity_type == "REPEAT_BLOCK"] == [(ChangeType.REPEAT_BLOCK_REMOVED, "MAINTENANCE")]


# --- pause boundaries and rule-order independence -------------------------------------

def flag(name: str) -> ComparisonCondition:
    return ComparisonCondition(operator="EQUALS", left=FieldOperand(field=name),
                               right=LiteralOperand(value=True))


def block_rule(code: str, action: str) -> ConditionalDefinition:
    return ConditionalDefinition(
        code=code, protocol_label=code, display_name=code, condition=flag(code),
        actions=[ConditionalAction(action_type=action, target_code="TREATMENT_BLOCK",
                                   condition=flag(code))])


def treatment_schedule(*rules: ConditionalDefinition):
    return approved([visit("TREATMENT", ProtocolDayTiming(
        reference=AnchorReference(code="FIRST_DOSE"), day=1), recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
        start_reference=AnchorReference(code="FIRST_DOSE"),
        termination=RecurrenceTermination(type="COUNT", count=4)))],
        anchors=[anchor("FIRST_DOSE")], conditionals=list(rules),
        repeat_blocks=[RepeatBlock(code="TREATMENT_BLOCK", protocol_label="T", display_name="T",
                                   event_codes=["TREATMENT"], dependency_mode="NOMINAL",
                                   resume_mode="NOMINAL")])


def active(**dates: date) -> dict[str, PatientConditionStatus]:
    return {code: PatientConditionStatus(condition_code=code, state=ConditionState.ACTIVE,
                                         occurrence_date=value) for code, value in dates.items()}


def test_a_pause_covers_the_visit_on_its_own_start_day():
    schedule = treatment_schedule(block_rule("HOLD", "PAUSE_BLOCK"))
    result = evaluate(schedule, context(schedule, anchors={"FIRST_DOSE": date(2026, 10, 5)},
                                        conditions=active(HOLD=date(2026, 10, 26))))
    assert [item.status for item in by_code(result)["TREATMENT"]] == [
        PatientEventStatus.RESOLVED] + [PatientEventStatus.PAUSED] * 3


@pytest.mark.parametrize("stop_first", [True, False])
def test_an_earlier_pause_survives_a_stop_listed_before_it(stop_first):
    rules = [block_rule("STOP", "STOP_BLOCK"), block_rule("HOLD", "PAUSE_BLOCK")]
    if not stop_first:
        rules.reverse()
    schedule = treatment_schedule(*rules)
    ctx = context(schedule, anchors={"FIRST_DOSE": date(2026, 10, 5)},
                  conditions=active(HOLD=date(2026, 10, 26), STOP=date(2026, 11, 20)))
    plan = build_conditional_plan(schedule, ctx)
    directive = plan.block_directives["TREATMENT_BLOCK"]
    assert [change.state for change in directive.changes] == [BlockState.PAUSED, BlockState.STOPPED]
    assert [item.status for item in by_code(evaluate(schedule, ctx))["TREATMENT"]] == [
        PatientEventStatus.RESOLVED, PatientEventStatus.PAUSED, PatientEventStatus.PAUSED,
        PatientEventStatus.CANCELLED]


@pytest.mark.parametrize("stop_first", [True, False])
def test_same_day_pause_and_stop_have_one_order(stop_first):
    rules = [block_rule("STOP", "STOP_BLOCK"), block_rule("HOLD", "PAUSE_BLOCK")]
    if not stop_first:
        rules.reverse()
    schedule = treatment_schedule(*rules)
    ctx = context(schedule, anchors={"FIRST_DOSE": date(2026, 10, 5)},
                  conditions=active(HOLD=date(2026, 10, 26), STOP=date(2026, 10, 26)))
    assert [item.status for item in by_code(evaluate(schedule, ctx))["TREATMENT"]] == [
        PatientEventStatus.RESOLVED, PatientEventStatus.PAUSED,
        PatientEventStatus.CANCELLED, PatientEventStatus.CANCELLED]


# --- window-only rules: reminders and deviation -------------------------------------------

def pre_op(**changes) -> ProjectedEvent:
    values = {"logical_key": "PRE_OP:0", "event_code": "PRE_OP", "event_type": "ASSESSMENT",
              "display_name": "Pre-op", "status": "RESOLVED", "nominal_date": None,
              "earliest_date": date(2026, 11, 30), "latest_date": date(2026, 12, 14)}
    values.update(changes)
    return ProjectedEvent(**values)


def test_a_window_only_visit_is_reminded_from_the_window_start():
    result = project([pre_op()], today=date(2026, 11, 20))
    assert [(item.nominal_date, item.policy_state) for item in result.reminders] == [
        (date(2026, 11, 30), "UPCOMING")]
    assert result.reminders[0].key == "reminder:PRE_OP:0:2026-11-30"
    assert (result.calendar[0].start_date, result.calendar[0].end_date) == (
        date(2026, 11, 30), date(2026, 12, 14))
    assert project([pre_op()], today=date(2026, 12, 15)).reminders[0].policy_state == "OVERDUE"


def test_a_deadline_only_or_waiting_visit_is_still_not_reminded():
    assert project([pre_op(earliest_date=None)], today=date(2026, 11, 20)).reminders == []
    assert project([pre_op(status="WAITING_FOR_ANCHOR", earliest_date=None, latest_date=None)],
                   today=date(2026, 11, 20)).reminders == []


def assess(**changes):
    values = {"logical_key": "PRE_OP:0", "event_code": "PRE_OP", "display_name": "Pre-op",
              "status": "RESOLVED", "planned_date": None, "earliest_date": date(2026, 11, 30),
              "latest_date": date(2026, 12, 14), "actual_date": None, "today": date(2026, 12, 1)}
    values.update(changes)
    return assess_visit(**values)


def test_a_window_only_visit_is_assessed_against_its_window():
    inside = assess(actual_date=date(2026, 12, 10))
    assert (inside.deviation_type, inside.within_window) == (DeviationType.NONE, True)
    assert inside.delta_from_planned_days is None
    late = assess(actual_date=date(2026, 12, 16))
    assert (late.deviation_type, late.outside_window_days) == (DeviationType.LATE, 2)
    early = assess(actual_date=date(2026, 11, 27))
    assert (early.deviation_type, early.outside_window_days) == (DeviationType.EARLY, 3)
    missed = assess(today=date(2026, 12, 20))
    assert missed.deviation_type == DeviationType.MISSED
    assert assess().deviation_type == DeviationType.NONE
    deadline_only = assess(earliest_date=None, actual_date=date(2026, 12, 20))
    assert deadline_only.deviation_type == DeviationType.LATE


# --- planned -> actual is not a correction -----------------------------------------------

def record(value: date, source: str, superseded: bool) -> AnchorRecord:
    return AnchorRecord(value=value, record_status="CONFIRMED", source_type=source,
                        recorded_at=datetime.now(timezone.utc), superseded=superseded)


def test_the_actual_date_replacing_a_confirmed_plan_is_confirmed_not_corrected():
    view = resolve_anchor_status(anchor_code="SURGERY", display_name="Surgery", records=[
        record(date(2026, 12, 14), "PLANNED_PROCEDURE", True),
        record(date(2026, 12, 17), "PROCEDURE_RECORD", False)])
    assert view.status == AnchorStatus.CONFIRMED
    corrected = resolve_anchor_status(anchor_code="SURGERY", display_name="Surgery", records=[
        record(date(2026, 12, 14), "PLANNED_PROCEDURE", True),
        record(date(2026, 12, 17), "PROCEDURE_RECORD", True),
        record(date(2027, 1, 4), "PROCEDURE_RECORD", False)])
    assert corrected.status == AnchorStatus.CORRECTED


# --- validator: block dependency mode only matters when something repeats ------------

def _block_mode_issues(events):
    from app.domain.schedule.validator import ScheduleValidator

    block = RepeatBlock(
        code="SURGERY_BRANCH", protocol_label="Surgery branch", display_name="Surgery branch",
        event_codes=[event.code for event in events], dependency_mode=DependencyMode.UNCLEAR,
    )
    schedule = approved(events, anchors=[anchor("REGISTRATION")], repeat_blocks=[block])
    return [
        issue for issue in ScheduleValidator().validate(schedule)
        if issue.issue_code == "UNCLEAR_DEPENDENCY_MODE" and issue.entity_type == "REPEAT_BLOCK"
    ]


def test_a_stop_only_block_of_single_visits_does_not_demand_a_dependency_mode():
    """A block that exists only so a condition can cancel single visits has no
    later cycle to place, so an unstated mode must not block approval."""
    events = [visit("SURGERY", ProtocolDayTiming(reference=AnchorReference(code="REGISTRATION"), day=29))]
    assert _block_mode_issues(events) == []


def test_a_block_whose_visits_repeat_still_demands_a_dependency_mode():
    events = [visit(
        "WEEKLY_CBC", ProtocolDayTiming(reference=AnchorReference(code="REGISTRATION"), day=1),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=1, unit=TimeUnit.WEEK),
            start_reference=AnchorReference(code="REGISTRATION"),
            termination=RecurrenceTermination(type="COUNT", count=6),
        ),
    )]
    issues = _block_mode_issues(events)
    assert len(issues) == 1 and issues[0].blocking
