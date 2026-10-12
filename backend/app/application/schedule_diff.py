"""Before/after diff for one schedule evaluation (the single place that decides
what "before/after" means - AD-10), over projection records instead of ORM rows.
"""

from __future__ import annotations

from datetime import datetime

from pydantic_core import to_jsonable_python

from app.domain.schedule.models import EvaluationResult

from .patients import PatientEventRecord


def compute_schedule_diff(
    prior: list[PatientEventRecord], result: EvaluationResult,
    protected_keys: set[tuple[str, int]],
) -> dict[str, object]:
    prior_by_key = {item.logical_key: item for item in prior}
    proposed = {f"{item.event_code}:{item.occurrence_index}": item for item in result.events}
    items: list[dict[str, object]] = []
    counts = {key: 0 for key in (
        "UNCHANGED", "ADDED", "MOVED", "STATUS_CHANGED", "CANCELLED", "PROTECTED")}
    for key in sorted(prior_by_key.keys() | proposed.keys()):
        before = prior_by_key.get(key)
        after = proposed.get(key)
        before_date = before.nominal_start_date if before else None
        timing = getattr(after, "timing", None) if after is not None else None
        value = getattr(timing, "nominal_start", None) if timing else None
        after_date = value.date() if isinstance(value, datetime) else value
        before_status = before.status if before else None
        after_status = after.status.value if after else "CANCELLED"
        if before is None:
            change = "ADDED"
        elif (before.event_code, before.occurrence_index) in protected_keys:
            change = "PROTECTED"
            after_date = before_date
            after_status = before_status
        elif after is None:
            change = "CANCELLED"
        elif before_date != after_date:
            change = "MOVED"
        elif before_status != after_status:
            change = "STATUS_CHANGED"
        else:
            change = "UNCHANGED"
        counts[change] += 1
        items.append({
            "logical_key": key, "change": change,
            "before": {"date": before_date, "status": before_status} if before else None,
            "after": {"date": after_date, "status": after_status} if after else None,
            "protected_history": change == "PROTECTED",
        })
    return to_jsonable_python({"summary": counts, "events": items})
