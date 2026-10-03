"""Change 2 - treatment cycles with addressable starts (cases F1-F5).

21-day cycles, Cycle 1 Day 1 = Mon 05-Oct-2026. Nominal grid: C2 26-Oct,
C3 16-Nov, C4 07-Dec. Cycle 2 is actually given 02-Nov (7 days late).
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _engine_cases import anchor, approved, by_code, context, evaluate, nominal_dates, visit

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand, MembershipCondition
from app.domain.schedule.evaluator import ScheduleEvaluator, add_amount
from app.domain.schedule.models import (
    Activity, ClaimEvidence, CycleDependencyMode, CyclePlan, DependencyMode,
    PatientEventStatus, RecurrenceTermination, RepeatBlock, RollingHorizon,
)
from app.domain.schedule.timing import (
    AnchorAcceptance, AnchorReference, CycleDayTiming, CycleSelector, OffsetTiming,
    PositiveTemporalAmount, TemporalAmount, TimeUnit,
)
from app.domain.schedule.validator import ScheduleValidator

C1D1 = date(2026, 10, 5)

_BLOCK_MODE = {
    CycleDependencyMode.NOMINAL: DependencyMode.NOMINAL,
    CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START: DependencyMode.ACTUAL_PREVIOUS_EVENT,
    CycleDependencyMode.MANUAL: DependencyMode.MANUAL,
    CycleDependencyMode.UNCLEAR: DependencyMode.UNCLEAR,
}


def cycle_day(day: int, number: int | None = None) -> CycleDayTiming:
    return CycleDayTiming(
        cycle_reference="CYCLE",
        cycle=CycleSelector(type="NUMBER", number=number) if number else CycleSelector(type="CURRENT"),
        day=day,
    )


def cycles(
    mode: CycleDependencyMode,
    *,
    termination: RecurrenceTermination | None = None,
    days: tuple[int, ...] = (1,),
    length: PositiveTemporalAmount | None = None,
    extra=(),
):
    events = [visit(f"DAY_{day}", cycle_day(day)) for day in days]
    events.extend(extra)
    block = RepeatBlock(
        code="TREATMENT", protocol_label="Treatment", display_name="Treatment",
        event_codes=[f"DAY_{day}" for day in days],
        dependency_mode=_BLOCK_MODE[mode],
        cycle_plan=CyclePlan(
            code="CYCLE", start_reference=AnchorReference(code="C1D1"),
            length=length or PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
            dependency_mode=mode, start_event_code="DAY_1",
            termination=termination or RecurrenceTermination(type="COUNT", count=6),
        ),
    )
    return approved(events, anchors=[anchor("C1D1")], repeat_blocks=[block])


def dated(result, code: str) -> list[date]:
    return [item.timing.nominal_start for item in by_code(result)[code] if item.timing]


def run(schedule, *given: date, **changes):
    values = {"anchors": {"C1D1": C1D1}}
    if given:
        values["actual_event_values"] = {"DAY_1": list(given)}
    values.update(changes)
    return evaluate(schedule, context(schedule, **values))


# --- F1: nominal grid ----------------------------------------------------------

def test_f1_nominal_grid_delayed_c2_does_not_move_c3():
    result = run(cycles(CycleDependencyMode.NOMINAL), C1D1, date(2026, 11, 2))
    assert nominal_dates(result, "DAY_1") == [
        date(2026, 10, 5), date(2026, 10, 26), date(2026, 11, 16),
        date(2026, 12, 7), date(2026, 12, 28), date(2027, 1, 18),
    ]
    c3 = by_code(result)["DAY_1"][2]
    assert c3.explanation["cycle"]["number"] == 3
    assert c3.explanation["cycle"]["anchor"] == "CYCLE_3_START"
    assert c3.explanation["cycle"]["start_basis"] == "NOMINAL"
    assert c3.dependency_result["mode"] == "NOMINAL"


# --- F2: next cycle follows the actual previous cycle ----------------------------

def test_f2_actual_c2_delay_moves_c3_and_c4_waits_for_c3():
    result = run(cycles(CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START), C1D1, date(2026, 11, 2))
    items = by_code(result)["DAY_1"]
    assert [item.timing.nominal_start for item in items[:3]] == [
        date(2026, 10, 5), date(2026, 11, 2), date(2026, 11, 23),
    ]
    assert items[2].explanation["cycle"]["start_basis"] == "FROM_ACTUAL_PREVIOUS_CYCLE"
    c4 = items[3]
    assert c4.status == PatientEventStatus.BLOCKED
    assert c4.timing is None
    assert c4.occurrence_index == 3
    assert c4.dependency_result["waiting_for_actual"] == ["CYCLE_3_START"]
    assert len(items) == 4, "no cycle after the first undatable one is listed"


def test_f2_c4_is_dated_once_c3_is_actually_given():
    result = run(
        cycles(CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START),
        C1D1, date(2026, 11, 2), date(2026, 11, 23))
    items = by_code(result)["DAY_1"]
    assert items[3].timing.nominal_start == date(2026, 12, 14)
    assert items[3].status == PatientEventStatus.RESOLVED
    assert items[4].status == PatientEventStatus.BLOCKED
    assert items[4].dependency_result["waiting_for_actual"] == ["CYCLE_4_START"]


def test_f2_before_c2_is_given_only_c2_is_dated():
    items = by_code(run(cycles(CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START), C1D1))["DAY_1"]
    assert items[1].timing.nominal_start == date(2026, 10, 26)
    assert items[2].status == PatientEventStatus.BLOCKED
    assert items[2].dependency_result["waiting_for_actual"] == ["CYCLE_2_START"]


def test_f2_days_inside_a_delayed_cycle_count_from_its_actual_start():
    schedule = cycles(CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START, days=(1, 8))
    result = run(schedule, C1D1, date(2026, 11, 2), date(2026, 11, 23))
    assert dated(result, "DAY_8") == [
        date(2026, 10, 12), date(2026, 11, 9), date(2026, 11, 30), date(2026, 12, 21),
    ]


def test_f2_a_recorded_cycle_start_anchor_wins_over_the_day_1_visit():
    result = run(
        cycles(CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START), C1D1,
        anchors={"C1D1": C1D1, "CYCLE_2_START": date(2026, 11, 2)})
    assert dated(result, "DAY_1") == [
        date(2026, 10, 5), date(2026, 11, 2), date(2026, 11, 23),
    ]


def test_cycle_start_anchors_are_addressable_and_respect_planned_vs_actual():
    """A rule counting from CYCLE_3_START waits for Cycle 3 unless it accepts a plan."""
    actual_only = visit("RESTAGING", OffsetTiming(
        reference=AnchorReference(code="CYCLE_3_START"),
        offset=TemporalAmount(value=14, unit=TimeUnit.DAY)))
    planned_ok = visit("PRE_C3_LABS", OffsetTiming(
        reference=AnchorReference(code="CYCLE_3_START", accepts=AnchorAcceptance.PLANNED_OR_ACTUAL),
        offset=TemporalAmount(value=-3, unit=TimeUnit.DAY)))
    schedule = cycles(CycleDependencyMode.NOMINAL, extra=(actual_only, planned_ok))
    assert not ScheduleValidator.blocking(ScheduleValidator().validate(schedule))

    before = run(schedule, C1D1, date(2026, 10, 26))
    assert by_code(before)["RESTAGING"][0].status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert nominal_dates(before, "PRE_C3_LABS") == [date(2026, 11, 13)]

    after = run(schedule, C1D1, date(2026, 10, 26), date(2026, 11, 18))
    assert nominal_dates(after, "RESTAGING") == [date(2026, 12, 2)]


# --- F3: mode not stated -------------------------------------------------------

def test_f3_unclear_cycle_mode_blocks_approval_and_is_never_assumed():
    schedule = cycles(CycleDependencyMode.UNCLEAR)
    blocking = ScheduleValidator.blocking(ScheduleValidator().validate(schedule))
    assert "UNCLEAR_DEPENDENCY_MODE" in {issue.issue_code for issue in blocking}
    with pytest.raises(ValueError):
        run(schedule, C1D1)


def test_cycle_and_block_modes_that_disagree_block_approval():
    schedule = cycles(CycleDependencyMode.NOMINAL)
    schedule.repeat_blocks[0].dependency_mode = DependencyMode.ACTUAL_PREVIOUS_EVENT
    codes = {issue.issue_code for issue in ScheduleValidator.blocking(ScheduleValidator().validate(schedule))}
    assert "CONFLICTING_SOURCE" in codes


def test_manual_cycle_mode_waits_for_each_entered_start():
    result = run(
        cycles(CycleDependencyMode.MANUAL), C1D1,
        planned_anchors={"CYCLE_2_START": date(2026, 10, 28)})
    items = by_code(result)["DAY_1"]
    assert [item.timing.nominal_start for item in items[:2]] == [C1D1, date(2026, 10, 28)]
    assert items[2].status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert items[2].dependency_result["waiting_for_manual_date"] == ["CYCLE_3_START"]


# --- F4 / F5 -------------------------------------------------------------------

def test_f4_days_1_8_15_of_each_cycle():
    result = run(cycles(CycleDependencyMode.NOMINAL, days=(1, 8, 15)), C1D1)
    grouped = by_code(result)
    assert [grouped[f"DAY_{day}"][0].timing.nominal_start for day in (1, 8, 15)] == [
        date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19),
    ]
    assert grouped["DAY_15"][1].timing.nominal_start == date(2026, 11, 9)


def test_f5_activity_on_some_cycles_uses_the_cycle_number():
    schedule = cycles(CycleDependencyMode.NOMINAL)
    mri = Activity(
        code="MRI", protocol_label="MRI", display_name="MRI", activity_type="IMAGING",
        conditions=[MembershipCondition(
            operator="IN", value=FieldOperand(field="occurrence_number"), values=[2, 4, 6])],
        evidence_refs=[schedule.evidence[0].id],
    )
    schedule.events[0].activities = [mri]
    schedule.claim_evidence.append(ClaimEvidence(
        evidence_id=schedule.evidence[0].id, claim_type="ACTIVITY",
        claim_entity_type="ACTIVITY", claim_entity_id=mri.id, confidence=0.9))
    items = by_code(run(schedule, C1D1))["DAY_1"]
    statuses = [item.activities[0].status.value for item in items[:5]]
    assert statuses == ["NOT_APPLICABLE", "RESOLVED", "NOT_APPLICABLE", "RESOLVED", "NOT_APPLICABLE"]


def test_single_cycle_visit_in_cycle_3():
    schedule = cycles(CycleDependencyMode.NOMINAL, extra=(visit("PK_C3", cycle_day(1, number=3)),))
    assert nominal_dates(run(schedule, C1D1), "PK_C3") == [date(2026, 11, 16)]


def test_single_cycle_visit_beyond_the_stated_count_is_not_applicable():
    schedule = cycles(CycleDependencyMode.NOMINAL, extra=(visit("C8_VISIT", cycle_day(1, number=8)),))
    assert by_code(run(schedule, C1D1))["C8_VISIT"][0].status == PatientEventStatus.NOT_APPLICABLE


def test_single_cycle_visit_waits_with_its_blocked_cycle():
    schedule = cycles(
        CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START,
        extra=(visit("PK_C4", cycle_day(1, number=4)),))
    item = by_code(run(schedule, C1D1, date(2026, 11, 2)))["PK_C4"][0]
    assert item.status == PatientEventStatus.BLOCKED
    assert item.dependency_result["waiting_for_actual"] == ["CYCLE_3_START"]


# --- bounds --------------------------------------------------------------------

def test_cycles_bounded_by_count():
    assert len(nominal_dates(run(cycles(CycleDependencyMode.NOMINAL), C1D1), "DAY_1")) == 6


def test_cycles_bounded_by_condition_stop_from_its_effective_date():
    stop = RecurrenceTermination(type="CONDITION", condition=ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="progression"),
        right=LiteralOperand(value=True)))
    schedule = cycles(CycleDependencyMode.NOMINAL, termination=stop, days=(1, 8))
    result = run(
        schedule, C1D1, state={"progression": True},
        state_effective_at={"cycles:CYCLE": datetime(2026, 11, 20, tzinfo=timezone.utc)})
    assert nominal_dates(result, "DAY_1") == [date(2026, 10, 5), date(2026, 10, 26), date(2026, 11, 16)]
    assert nominal_dates(result, "DAY_8") == [date(2026, 10, 12), date(2026, 11, 2)]


def test_condition_bound_not_yet_known_gives_no_dates():
    stop = RecurrenceTermination(type="CONDITION", condition=ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="progression"),
        right=LiteralOperand(value=True)))
    items = by_code(run(cycles(CycleDependencyMode.NOMINAL, termination=stop), C1D1))["DAY_1"]
    assert [item.status for item in items] == [PatientEventStatus.WAITING_FOR_CONDITION]


def test_open_ended_cycles_report_that_they_continue():
    schedule = cycles(CycleDependencyMode.NOMINAL, termination=RecurrenceTermination(type="HORIZON"))
    result = ScheduleEvaluator().evaluate(
        schedule, context(schedule, anchors={"C1D1": C1D1}), horizon=date(2027, 6, 30),
        rolling=RollingHorizon(upcoming_limit=3, as_of=date(2026, 10, 1)), today=date(2026, 10, 1))
    assert len(by_code(result)["DAY_1"]) == 3
    summary = next(item for item in result.repeat_summaries if item.event_code == "DAY_1")
    assert summary.continues and summary.cadence == "every cycle (21 days)"
    assert summary.next_unmaterialized == date(2026, 12, 7)


def test_waiting_for_cycle_1_anchor():
    schedule = cycles(CycleDependencyMode.NOMINAL)
    items = by_code(evaluate(schedule, context(schedule)))["DAY_1"]
    assert [item.status for item in items] == [PatientEventStatus.WAITING_FOR_ANCHOR]


def test_legacy_state_based_cycle_length_still_works():
    event = visit("C2D1", CycleDayTiming(
        cycle_reference="C1D1", cycle=CycleSelector(type="NUMBER", number=2), day=1))
    schedule = approved([event], anchors=[anchor("C1D1")])
    result = evaluate(schedule, context(
        schedule, anchors={"C1D1": C1D1}, state={"cycle_lengths": {"C1D1": 21}}))
    assert nominal_dates(result, "C2D1") == [date(2026, 10, 26)]


def test_cycle_plan_round_trips_through_its_stored_json():
    block = cycles(CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START).repeat_blocks[0]
    restored = RepeatBlock.model_validate(block.model_dump(mode="json"))
    assert restored.cycle_plan == block.cycle_plan


# --- properties ------------------------------------------------------------------

def _starts():
    base = date(2024, 1, 1)
    special = [date(2024, 2, 29), date(2027, 1, 31), date(2026, 8, 31), date(2025, 12, 31)]
    return special + [base + timedelta(days=step * 37) for step in range(40)]


@pytest.mark.parametrize("length", [
    PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
    PositiveTemporalAmount(value=4, unit=TimeUnit.WEEK),
    PositiveTemporalAmount(value=1, unit=TimeUnit.MONTH),
])
def test_cycle_dates_increase_are_unique_and_deterministic(length):
    for mode in (CycleDependencyMode.NOMINAL, CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START):
        schedule = cycles(mode, length=length, termination=RecurrenceTermination(type="COUNT", count=12),
                          days=(1, 8))
        for start in _starts():
            ctx = context(schedule, anchors={"C1D1": start})
            if mode == CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START:
                # Every cycle given 3 days late.
                given, value = [start], start
                for _ in range(11):
                    value = add_amount(value, length) + timedelta(days=3)
                    given.append(value)
                ctx.actual_event_values = {"DAY_1": given}
            first = evaluate(schedule, ctx)
            dates = [item.timing.nominal_start for item in by_code(first)["DAY_1"]]
            assert len(dates) == 12
            assert all(left < right for left, right in zip(dates, dates[1:])), (mode, start, dates)
            assert [item.model_dump(exclude={"evaluated_at"}) for item in first.events] == [
                item.model_dump(exclude={"evaluated_at"}) for item in evaluate(schedule, ctx).events]
