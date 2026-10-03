"""Change 1 - repeats counted from the original start date (cases E6, E7, E8).

Adding the interval to the previous occurrence lets a month-end clamp stick
(31-Jan -> 28-Feb -> 28-Mar ...). Every occurrence must instead be counted from
the start, so the protocol's day of the month comes back.
"""

from datetime import date
from pathlib import Path
import random
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _engine_cases import anchor, approved, context, evaluate, nominal_dates, visit

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.evaluator import add_amount, nth_occurrence
from app.domain.schedule.models import (
    ConditionalAction, ConditionalDefinition, ConditionState, PatientConditionStatus,
    RecurrenceRule, RecurrenceTermination,
)
from app.domain.schedule.timing import (
    AnchorReference, OffsetTiming, PositiveTemporalAmount, TemporalAmount, TimeUnit,
)


def repeating(start: date, value: int, unit: TimeUnit, count: int, *,
              include_start: bool = False, start_offset: TemporalAmount | None = None):
    event = visit("FOLLOW_UP", OffsetTiming(
        reference=AnchorReference(code="START"),
        offset=TemporalAmount(value=0, unit=TimeUnit.DAY),
    ), recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=value, unit=unit),
        start_reference=AnchorReference(code="START"),
        start_offset=start_offset,
        include_start=include_start,
        termination=RecurrenceTermination(type="COUNT", count=count),
    ))
    schedule = approved([event], anchors=[anchor("START")])
    return nominal_dates(evaluate(schedule, context(schedule, anchors={"START": start})), "FOLLOW_UP")


def test_e6_monthly_from_31_january_returns_to_month_end():
    assert repeating(date(2027, 1, 31), 1, TimeUnit.MONTH, 4) == [
        date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31),
    ]


def test_e6_a_stated_first_offset_does_not_introduce_its_own_clamp():
    """'Monthly, first visit one month after 31-Jan' is the same series."""
    assert repeating(
        date(2027, 1, 31), 1, TimeUnit.MONTH, 4, include_start=True,
        start_offset=TemporalAmount(value=1, unit=TimeUnit.MONTH),
    ) == [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)]


def test_e8_yearly_from_leap_day_returns_to_29_february():
    assert repeating(date(2028, 2, 29), 1, TimeUnit.YEAR, 4) == [
        date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29),
    ]


def test_every_3_months_from_a_month_end():
    """Regression matrix row 3."""
    assert repeating(date(2026, 8, 31), 3, TimeUnit.MONTH, 4) == [
        date(2026, 11, 30), date(2027, 2, 28), date(2027, 5, 31), date(2027, 8, 31),
    ]


def test_e7_every_4_weeks_is_fixed_28_days_not_monthly():
    assert repeating(date(2027, 1, 31), 4, TimeUnit.WEEK, 3) == [
        date(2027, 2, 28), date(2027, 3, 28), date(2027, 4, 25),
    ]


def test_including_the_start_keeps_the_start_as_occurrence_zero():
    assert repeating(date(2027, 1, 31), 1, TimeUnit.MONTH, 3, include_start=True) == [
        date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31),
    ]


def test_conditional_monthly_repeat_is_counted_from_the_condition_date():
    """The same drift existed in repeat-until-resolved (doc 2 s17) loops."""
    active = ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="flag"), right=LiteralOperand(value=True))
    repeat = visit("REPEAT_LABS", OffsetTiming(
        reference=AnchorReference(code="START"),
        offset=TemporalAmount(value=0, unit=TimeUnit.DAY),
    ))
    rule = ConditionalDefinition(
        code="FLAG", protocol_label="Flag", display_name="Flag", condition=active,
        resolution_condition=active,
        actions=[ConditionalAction(
            action_type="REPEAT_EVENT", target_code="REPEAT_LABS", condition=active,
            parameters={"interval": {"value": 1, "unit": "MONTH"}},
        )],
    )
    schedule = approved([repeat], anchors=[anchor("START")], conditionals=[rule])
    ctx = context(schedule, anchors={"START": date(2027, 1, 1)}, conditions={
        "FLAG": PatientConditionStatus(
            condition_code="FLAG", state=ConditionState.ACTIVE,
            occurrence_date=date(2027, 1, 31)),
    })
    dates = nominal_dates(evaluate(schedule, ctx, horizon=date(2027, 5, 31)), "REPEAT_LABS")
    assert dates == [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)]


@pytest.mark.parametrize("offset,interval,n,expected", [
    (TemporalAmount(value=2, unit=TimeUnit.WEEK), PositiveTemporalAmount(value=3, unit=TimeUnit.DAY), 2, date(2027, 2, 20)),
    (TemporalAmount(value=1, unit=TimeUnit.YEAR), PositiveTemporalAmount(value=6, unit=TimeUnit.MONTH), 1, date(2028, 7, 31)),
    (TemporalAmount(value=14, unit=TimeUnit.DAY), PositiveTemporalAmount(value=1, unit=TimeUnit.MONTH), 1, date(2027, 3, 14)),
])
def test_nth_occurrence_mixed_units(offset, interval, n, expected):
    assert nth_occurrence(date(2027, 1, 31), interval, n, offset) == expected


def test_randomised_series_are_ordered_unique_and_anchored_to_the_start():
    """Part 6: many random starts - dates in order, no duplicates, and each one
    exactly start + n x interval, so re-running cannot drift."""
    rng = random.Random(20260929)
    for _ in range(200):
        start = date(2026, 1, 1) + (date(2030, 12, 31) - date(2026, 1, 1)) * rng.random()
        unit = rng.choice([TimeUnit.DAY, TimeUnit.WEEK, TimeUnit.MONTH, TimeUnit.YEAR])
        interval = PositiveTemporalAmount(value=rng.randint(1, 4), unit=unit)
        series = [nth_occurrence(start, interval, n) for n in range(1, 13)]
        assert series == sorted(series)
        assert len(set(series)) == len(series)
        assert series == [add_amount(start, interval, n) for n in range(1, 13)]
