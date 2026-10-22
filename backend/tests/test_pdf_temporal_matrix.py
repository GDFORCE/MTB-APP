"""P7-02: hand-computed boundary matrix for months, years, leap days, windows and date-versus-instant timing."""
from datetime import date, datetime, timezone

import pytest

from _engine_cases import anchor, approved, context, evaluate, visit
from app.domain.schedule.models import Window
from app.domain.schedule.timing import (
    AnchorReference, NonNegativeTemporalAmount, OffsetTiming, TemporalAmount, TimeUnit)
from app.domain.schedule.validator import ScheduleValidator

HORIZON = date(2035, 12, 31)


def run(start, value, unit, *, before=None, after=None, horizon=HORIZON):
    window = None
    if before is not None or after is not None:     # the model requires both sides; one side may be zero
        window = Window(before=NonNegativeTemporalAmount(value=before or 0, unit=TimeUnit.DAY),
                        after=NonNegativeTemporalAmount(value=after or 0, unit=TimeUnit.DAY))
    event = visit("V", OffsetTiming(reference=AnchorReference(code="START"),
                                    offset=TemporalAmount(value=value, unit=unit)), window=window)
    schedule = approved([event], anchors=[anchor("START")])
    result = evaluate(schedule, context(schedule, anchors={"START": start}), horizon=horizon)
    [row] = [item for item in result.events if item.event_code == "V"]
    return row


@pytest.mark.parametrize("start, value, unit, target", [
    (date(2027, 1, 31), 1, TimeUnit.MONTH, date(2027, 2, 28)),          # month-end clamps, non-leap
    (date(2028, 1, 31), 1, TimeUnit.MONTH, date(2028, 2, 29)),          # ... and in a leap year
    (date(2028, 2, 29), 1, TimeUnit.YEAR, date(2029, 2, 28)),           # leap day plus a year
    (date(2028, 2, 29), 4, TimeUnit.YEAR, date(2032, 2, 29)),           # leap day lands on a leap day
    (date(2027, 8, 31), 6, TimeUnit.MONTH, date(2028, 2, 29)),          # six months across a leap February
    (date(2027, 12, 31), 1, TimeUnit.DAY, date(2028, 1, 1)),            # year boundary
    (date(2027, 1, 1), 2, TimeUnit.WEEK, date(2027, 1, 15)),
    (date(2027, 3, 31), -1, TimeUnit.MONTH, date(2027, 2, 28)),         # negative month offsets clamp too
    (date(2100, 1, 31), 1, TimeUnit.MONTH, date(2100, 2, 28)),          # 2100 is NOT a leap year
])
def test_calendar_arithmetic_matches_hand_computed_dates(start, value, unit, target):
    row = run(start, value, unit)
    assert row.timing.nominal_start == target and row.status.value == "RESOLVED"


@pytest.mark.parametrize("before, after, earliest, latest", [
    (3, 3, date(2027, 3, 7), date(2027, 3, 13)),       # symmetric
    (2, 0, date(2027, 3, 8), date(2027, 3, 10)),       # nothing is allowed after the target
    (0, 4, date(2027, 3, 10), date(2027, 3, 14)),      # nothing is allowed before the target
    (0, 0, date(2027, 3, 10), date(2027, 3, 10)),      # an explicit zero-width window
])
def test_window_bounds_are_exactly_what_the_protocol_states(before, after, earliest, latest):
    row = run(date(2027, 3, 1), 9, TimeUnit.DAY, before=before, after=after)
    assert (row.timing.nominal_start, row.timing.earliest, row.timing.latest) == (date(2027, 3, 10), earliest, latest)


def test_an_unstated_window_stays_unstated_and_is_not_filled_with_the_target():
    row = run(date(2027, 3, 1), 9, TimeUnit.DAY)
    assert row.timing.nominal_start == date(2027, 3, 10)
    assert row.timing.earliest in (None, row.timing.nominal_start) and row.timing.latest in (None, row.timing.nominal_start)


def test_a_window_crosses_a_month_and_year_boundary_by_calendar_days():
    row = run(date(2027, 12, 20), 12, TimeUnit.DAY, before=3, after=3)           # target 1 Jan 2028
    assert (row.timing.earliest, row.timing.nominal_start, row.timing.latest) == (
        date(2027, 12, 29), date(2028, 1, 1), date(2028, 1, 4))


def test_an_instant_anchor_keeps_its_time_and_rolls_the_date_over_midnight():
    start = datetime(2027, 1, 1, 23, 30, tzinfo=timezone.utc)
    event = visit("V", OffsetTiming(reference=AnchorReference(code="START"),
                                    offset=TemporalAmount(value=90, unit=TimeUnit.MINUTE)))
    schedule = approved([event], anchors=[anchor("START")])
    result = evaluate(schedule, context(schedule, anchors={"START": start}), horizon=HORIZON)
    row = next(item for item in result.events if item.event_code == "V")
    assert row.timing.nominal_start == datetime(2027, 1, 2, 1, 0, tzinfo=timezone.utc)


def test_a_sub_day_offset_waits_for_the_missing_clock_instead_of_flooring_to_a_date():
    """The rule is valid; the patient's missing clock is required input, not a guessed midnight."""
    event = visit("V", OffsetTiming(reference=AnchorReference(code="START"),
                                    offset=TemporalAmount(value=36, unit=TimeUnit.HOUR)))
    schedule = approved([event], anchors=[anchor("START")])
    assert [row.issue_code for row in ScheduleValidator().validate(schedule) if row.blocking] == []
    row = evaluate(schedule, context(schedule, anchors={"START": date(2027, 1, 1)}), horizon=HORIZON).events[0]
    assert row.timing is None and row.status.value == "WAITING_FOR_ANCHOR"
    assert row.explanation["reason_code"] == "TIME_OF_DAY_REQUIRED"
