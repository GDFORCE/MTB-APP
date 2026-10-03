"""Calendar-aware temporal arithmetic shared by the evaluator and the
constraint language (moved verbatim from evaluator.py, which re-exports it)."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

from .timing import TemporalAmount, TimeUnit

Temporal = date | datetime


def _is_aware(value: Temporal) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None


def _normalise_wall_clock(value: Temporal) -> Temporal:
    """Settle a zoned wall-clock time onto a time that exists in its zone.

    Calendar arithmetic can land inside a spring-forward gap (01:30 on the
    day London's clocks jump). Round-tripping through UTC moves it to the real
    instant it denotes (02:30 BST) instead of keeping a time no clock shows.
    """
    if not _is_aware(value):
        return value
    return value.astimezone(timezone.utc).astimezone(value.tzinfo)


def add_amount(value: Temporal, amount: TemporalAmount, multiplier: int = 1) -> Temporal:
    """Add a protocol amount to a date or date-time.

    Doc K3: hours and minutes are ELAPSED time. On a zoned date-time they are
    added in UTC and converted back, so "4 hours after a 00:30 dose" on the
    night the clocks change is four real hours later (05:30 BST), not a
    wall-clock 04:30 that is only three. Days, weeks, months and years are
    calendar amounts and keep the local wall-clock time. Naive values keep
    plain arithmetic exactly as before.
    """
    count = amount.value * multiplier
    if amount.unit in (TimeUnit.MINUTE, TimeUnit.HOUR):
        delta = timedelta(minutes=count) if amount.unit == TimeUnit.MINUTE else timedelta(hours=count)
        if _is_aware(value):
            return (value.astimezone(timezone.utc) + delta).astimezone(value.tzinfo)
        return value + delta
    if amount.unit == TimeUnit.DAY:
        return _normalise_wall_clock(value + timedelta(days=count))
    if amount.unit == TimeUnit.WEEK:
        return _normalise_wall_clock(value + timedelta(weeks=count))
    months = count if amount.unit == TimeUnit.MONTH else count * 12
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return _normalise_wall_clock(value.replace(year=year, month=month, day=day))
