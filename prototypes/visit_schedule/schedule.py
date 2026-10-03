"""Turn an extracted visit plan into dated patient visits (pure logic)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional


@dataclass
class Row:
    visit: str
    study_day: Optional[int]
    target: Optional[date]
    earliest: Optional[date]
    latest: Optional[date]
    activities: List[str]
    note: str = ""


def day_to_date(day1: date, day: int, has_day_zero: bool) -> date:
    # Without a Day 0, Day -1 is the day before Day 1.
    offset = day - 1 if (day >= 1 or has_day_zero) else day
    return day1 + timedelta(days=offset)


def _row(name, day, wb, wa, acts, note, day1, has_day_zero) -> Row:
    if day is None:
        return Row(name, None, None, None, None, acts, note or "Event-driven: no fixed study day")
    target = day_to_date(day1, day, has_day_zero)
    return Row(name, day, target,
               target - timedelta(days=wb or 0), target + timedelta(days=wa or 0), acts, note or "")


def build(plan: dict, day1: date, open_ended_repeats: int) -> List[Row]:
    hdz = bool(plan.get("has_day_zero"))
    rows = [_row(v["name"], v.get("day"), v.get("window_before"), v.get("window_after"),
                 v.get("activities") or [], v.get("note"), day1, hdz)
            for v in plan.get("visits", [])]

    for r in plan.get("repeating", []):
        count = r.get("occurrences")
        note = ""
        if not count:
            count = open_ended_repeats
            note = f"Repeats {r.get('until_text') or 'with no stated end'}; first {count} shown"
        for n in range(1, count + 1):
            day = r["first_day"] + (n - 1) * r["every_days"]
            name = r["name_template"].replace("{n}", str(n + (r.get("number_start") or 1) - 1))
            rows.append(_row(name, day, r.get("window_before"), r.get("window_after"),
                             r.get("activities") or [], note, day1, hdz))

    fixed = sorted((x for x in rows if x.study_day is not None), key=lambda x: x.study_day or 0)
    return fixed + [x for x in rows if x.study_day is None]
