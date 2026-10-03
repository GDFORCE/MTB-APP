"""Change 5 - phased follow-up as one segmented plan (cases E4, E9).

"Every 3 months for 2 years, then every 6 months to year 5, then yearly",
counted from end of therapy Mon 07-Dec-2026.
"""

import calendar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _engine_cases import anchor, approved, by_code, context, evaluate, nominal_dates, visit

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.evaluator import ScheduleEvaluator, add_amount, plan_cadence_text
from app.domain.schedule.models import (
    Event, PatientEventStatus, RecurrencePlan, RecurrenceSegment, RecurrenceTermination,
    RecurrenceRule, RollingHorizon,
)
from app.domain.schedule.timing import (
    AnchorReference, NonNegativeTemporalAmount, OffsetTiming, PositiveTemporalAmount,
    TemporalAmount, TimeUnit, Window,
)
from app.domain.schedule.validator import ScheduleValidator

EOT = date(2026, 12, 7)


def months(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit=TimeUnit.MONTH)


def every(value: int, unit: TimeUnit = TimeUnit.MONTH) -> PositiveTemporalAmount:
    return PositiveTemporalAmount(value=value, unit=unit)


def e9_plan(*, first_inclusive: bool | None = True, second_inclusive: bool | None = True,
            include_start: bool | None = False,
            termination: RecurrenceTermination | None = None) -> RecurrencePlan:
    return RecurrencePlan(
        start_reference=AnchorReference(code="END_OF_THERAPY"),
        include_start=include_start,
        segments=[
            RecurrenceSegment(interval=every(3), end_offset=months(24), end_inclusive=first_inclusive),
            RecurrenceSegment(interval=every(6), end_offset=months(60), end_inclusive=second_inclusive),
            RecurrenceSegment(interval=every(1, TimeUnit.YEAR)),
        ],
        termination=termination or RecurrenceTermination(type="HORIZON"),
    )


def follow_up(plan: RecurrencePlan, **changes):
    return approved([visit("FOLLOW_UP", OffsetTiming(
        reference=AnchorReference(code="END_OF_THERAPY"), offset=months(3),
    ), recurrence_plan=plan, **changes)], anchors=[anchor("END_OF_THERAPY")])


def run(schedule, *, horizon: date = date(2033, 12, 31), start: date = EOT):
    return evaluate(schedule, context(schedule, anchors={"END_OF_THERAPY": start}), horizon=horizon)


def test_e9_dates_segment_by_segment_with_each_boundary_once():
    dates = nominal_dates(run(follow_up(e9_plan())), "FOLLOW_UP")
    segment_1 = [date(2027, 3, 7), date(2027, 6, 7), date(2027, 9, 7), date(2027, 12, 7),
                 date(2028, 3, 7), date(2028, 6, 7), date(2028, 9, 7), date(2028, 12, 7)]
    segment_2 = [date(2029, 6, 7), date(2029, 12, 7), date(2030, 6, 7), date(2030, 12, 7),
                 date(2031, 6, 7), date(2031, 12, 7)]
    segment_3 = [date(2032, 12, 7), date(2033, 12, 7)]
    assert dates == segment_1 + segment_2 + segment_3
    assert dates.count(date(2028, 12, 7)) == 1
    assert dates.count(date(2031, 12, 7)) == 1


def test_e9_occurrences_are_numbered_continuously_and_name_their_segment():
    items = by_code(run(follow_up(e9_plan())))["FOLLOW_UP"]
    assert [item.occurrence_index for item in items] == list(range(len(items)))
    boundary = next(item for item in items if item.timing.nominal_start == date(2028, 12, 7))
    assert boundary.explanation["recurrence"]["segment"] == 1
    first_6_monthly = next(item for item in items if item.timing.nominal_start == date(2029, 6, 7))
    assert first_6_monthly.explanation["recurrence"]["segment"] == 2
    assert first_6_monthly.explanation["recurrence"]["cadence"] == "every 6 months"


def test_e9_exclusive_boundary_moves_the_visit_to_the_next_segment():
    dates = nominal_dates(run(follow_up(e9_plan(first_inclusive=False))), "FOLLOW_UP")
    assert date(2028, 9, 7) in dates
    assert dates.count(date(2028, 12, 7)) == 1
    assert date(2029, 6, 7) in dates
    index = dates.index(date(2028, 12, 7))
    items = by_code(run(follow_up(e9_plan(first_inclusive=False))))["FOLLOW_UP"]
    assert items[index].explanation["recurrence"]["segment"] == 2


@pytest.mark.parametrize("changes", [
    {"first_inclusive": None},
    {"second_inclusive": None},
    {"include_start": None},
])
def test_e4_e9_unstated_boundary_blocks_approval(changes):
    schedule = follow_up(e9_plan(**changes))
    codes = {issue.issue_code for issue in ScheduleValidator.blocking(ScheduleValidator().validate(schedule))}
    assert "UNRESOLVED_RECURRENCE_BOUNDARY" in codes


def test_open_final_segment_without_a_plan_termination_blocks_approval():
    plan = e9_plan()
    plan.termination = None
    codes = {issue.issue_code for issue in ScheduleValidator.blocking(
        ScheduleValidator().validate(follow_up(plan)))}
    assert "PHASED_SEGMENT_UNBOUNDED" in codes


def test_middle_segment_without_an_end_blocks_approval():
    plan = e9_plan()
    plan.segments[1] = RecurrenceSegment(interval=every(6))
    codes = {issue.issue_code for issue in ScheduleValidator.blocking(
        ScheduleValidator().validate(follow_up(plan)))}
    assert "PHASED_SEGMENT_UNBOUNDED" in codes


def test_segments_that_do_not_advance_are_conflicting():
    plan = e9_plan()
    plan.segments[1] = RecurrenceSegment(interval=every(6), end_offset=months(12), end_inclusive=True)
    codes = {issue.issue_code for issue in ScheduleValidator.blocking(
        ScheduleValidator().validate(follow_up(plan)))}
    assert "CONFLICTING_SOURCE" in codes


def test_valid_plan_passes_validation():
    assert not ScheduleValidator.blocking(ScheduleValidator().validate(follow_up(e9_plan())))


def test_an_event_repeats_by_one_rule_only():
    with pytest.raises(ValueError):
        Event(
            code="X", protocol_label="X", display_name="X", event_type="SITE_VISIT",
            timing=OffsetTiming(reference=AnchorReference(code="END_OF_THERAPY"), offset=months(0)),
            recurrence=RecurrenceRule(
                interval=every(3), start_reference=AnchorReference(code="END_OF_THERAPY"),
                termination=RecurrenceTermination(type="HORIZON")),
            recurrence_plan=e9_plan(),
        )


def test_window_applies_to_every_occurrence_in_every_segment():
    window = Window(before=NonNegativeTemporalAmount(value=14, unit=TimeUnit.DAY),
                    after=NonNegativeTemporalAmount(value=14, unit=TimeUnit.DAY))
    items = by_code(run(follow_up(e9_plan(), window=window)))["FOLLOW_UP"]
    for item in items:
        assert item.timing.earliest == item.timing.nominal_start - timedelta(days=14)
        assert item.timing.latest == item.timing.nominal_start + timedelta(days=14)


def test_count_segments_chain_without_month_end_drift():
    plan = RecurrencePlan(
        start_reference=AnchorReference(code="END_OF_THERAPY"), include_start=False,
        segments=[
            RecurrenceSegment(interval=every(1), count=2),
            RecurrenceSegment(interval=every(2), count=2),
        ],
    )
    dates = nominal_dates(run(follow_up(plan), start=date(2027, 1, 31)), "FOLLOW_UP")
    # Feb clamps to the 28th, but March and later return to the month end.
    assert dates == [date(2027, 2, 28), date(2027, 3, 31), date(2027, 5, 31), date(2027, 7, 31)]


def test_plan_stops_on_its_termination_condition():
    stop = RecurrenceTermination(type="CONDITION", condition=ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="progression"), right=LiteralOperand(value=True)))
    schedule = follow_up(e9_plan(termination=stop))
    result = evaluate(schedule, context(
        schedule, anchors={"END_OF_THERAPY": EOT}, state={"progression": True},
        state_effective_at={"recurrence:FOLLOW_UP": datetime(2027, 10, 1, tzinfo=timezone.utc)}))
    assert nominal_dates(result, "FOLLOW_UP") == [date(2027, 3, 7), date(2027, 6, 7), date(2027, 9, 7)]


def test_waits_for_end_of_therapy():
    schedule = follow_up(e9_plan())
    items = by_code(evaluate(schedule, context(schedule)))["FOLLOW_UP"]
    assert [item.status for item in items] == [PatientEventStatus.WAITING_FOR_ANCHOR]


def test_rolling_horizon_describes_the_whole_plan():
    schedule = follow_up(e9_plan())
    result = ScheduleEvaluator().evaluate(
        schedule, context(schedule, anchors={"END_OF_THERAPY": EOT}), horizon=date(2040, 12, 31),
        rolling=RollingHorizon(upcoming_limit=4, as_of=date(2026, 12, 8)), today=date(2026, 12, 8))
    assert len(by_code(result)["FOLLOW_UP"]) == 4
    summary = next(item for item in result.repeat_summaries if item.event_code == "FOLLOW_UP")
    assert summary.cadence == "every 3 months to year 2, then every 6 months to year 5, then yearly"
    assert summary.segments == ["every 3 months to year 2", "every 6 months to year 5", "yearly"]
    assert summary.continues
    assert summary.next_unmaterialized == date(2028, 3, 7)
    assert plan_cadence_text(e9_plan()) == summary.cadence


def test_plan_round_trips_through_stored_json():
    event = follow_up(e9_plan()).events[0]
    restored = Event.model_validate(event.model_dump(mode="json"))
    assert restored.recurrence_plan == event.recurrence_plan


def _starts():
    special = [date(2024, 2, 29), date(2027, 1, 31), date(2026, 8, 31), date(2025, 12, 31), date(2028, 2, 29)]
    return special + [date(2023, 1, 1) + timedelta(days=step * 41) for step in range(40)]


def test_property_plan_dates_increase_unique_and_deterministic():
    schedule = follow_up(e9_plan())
    for start in _starts():
        first = run(schedule, start=start, horizon=date(2040, 12, 31))
        dates = nominal_dates(first, "FOLLOW_UP")
        assert all(left < right for left, right in zip(dates, dates[1:])), (start, dates)
        year_5 = add_amount(start, months(60))
        assert len([value for value in dates if value <= year_5]) == 14, start
        again = nominal_dates(run(schedule, start=start, horizon=date(2040, 12, 31)), "FOLLOW_UP")
        assert again == dates
        # Every segment keeps the start's day of month wherever that month has it.
        for value in dates:
            assert value.day == min(start.day, calendar.monthrange(value.year, value.month)[1])


def test_plan_and_cycle_plan_survive_the_relational_store():
    """Both ride existing JSON paths: the plan in uctsm_event_recurrence.rule
    (recognised by type SEGMENTED), the cycle plan inside repeat_blocks."""
    from uuid import uuid4

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import models as db
    from app.db.base import Base
    from app.db.repositories import ScheduleRepository
    from app.domain.schedule.models import (
        CycleDependencyMode, CyclePlan, DependencyMode, RepeatBlock, ScheduleStatus,
    )
    from app.domain.schedule.timing import CycleDayTiming, CycleSelector

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    trial_id, protocol_id, version_id, definition_id = uuid4(), uuid4(), uuid4(), uuid4()
    schedule = follow_up(e9_plan())
    schedule.events.append(visit("DAY_1", CycleDayTiming(
        cycle_reference="CYCLE", cycle=CycleSelector(type="CURRENT"), day=1)))
    schedule.repeat_blocks.append(RepeatBlock(
        code="TREATMENT", protocol_label="T", display_name="T", event_codes=["DAY_1"],
        dependency_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT,
        cycle_plan=CyclePlan(
            code="CYCLE", start_reference=AnchorReference(code="END_OF_THERAPY"),
            length=every(21, TimeUnit.DAY),
            dependency_mode=CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START,
            start_event_code="DAY_1", termination=RecurrenceTermination(type="COUNT", count=6))))
    schedule.schedule_metadata.status = ScheduleStatus.DRAFT
    schedule.schedule_metadata.protocol_version_id = version_id
    with Session(engine) as session:
        session.add_all([
            db.Trial(id=trial_id, organization_id=uuid4()),
            db.Protocol(id=protocol_id, trial_id=trial_id, protocol_number="P-1"),
            db.ProtocolVersion(id=version_id, protocol_id=protocol_id, version_label="1",
                               document_name="p.pdf", document_uri="private://p.pdf", document_hash="a" * 64),
            db.ScheduleDefinition(id=definition_id, protocol_version_id=version_id, name="Primary"),
        ])
        session.flush()
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition_id)
        session.commit()
        loaded = ScheduleRepository(session).get(schedule.schedule_version_id)
    events = {item.code: item for item in loaded.events}
    assert events["FOLLOW_UP"].recurrence_plan == schedule.events[0].recurrence_plan
    assert events["FOLLOW_UP"].recurrence is None
    assert loaded.repeat_blocks[0].cycle_plan == schedule.repeat_blocks[0].cycle_plan
