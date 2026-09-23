"""Before/after diff for one schedule evaluation, shared by every AD-10
chokepoint that commits a date move.

Extracted from `impact_service.py`'s own `_impact()` (unchanged logic) so a
second call site (`evaluate_patient`'s audit event) does not hand-roll a
second, potentially drifting, diff - "every date move writes an AuditEvent
with before/after" only means one thing if there is exactly one place that
computes what "before/after" means.
"""

from __future__ import annotations

from pydantic_core import to_jsonable_python

from app.db import models as db
from app.domain.schedule.models import EvaluationResult


def compute_schedule_diff(
    patient_schedules, prior: list[db.PatientEvent], result: EvaluationResult,
) -> dict[str, object]:
    """`patient_schedules` is a `PatientScheduleService` - typed loosely here
    to avoid a circular import (schedule_service.py does not import this
    module; callers already hold a PatientScheduleService instance).
    """
    prior_by_key = {patient_schedules.logical_key_for_row(row): row for row in prior}
    proposed = {f"{item.event_code}:{item.occurrence_index}": item for item in result.events}
    protected_ids = patient_schedules.protected_event_ids(prior)
    items: list[dict[str, object]] = []
    counts = {key: 0 for key in ("UNCHANGED", "ADDED", "MOVED", "STATUS_CHANGED", "CANCELLED", "PROTECTED")}
    for key in sorted(prior_by_key.keys() | proposed.keys()):
        before = prior_by_key.get(key)
        after = proposed.get(key)
        before_date = before.nominal_start_date if before else None
        after_date = patient_schedules.result_date(after) if after else None
        before_status = before.status if before else None
        after_status = after.status.value if after else "CANCELLED"
        if before is None:
            change = "ADDED"
        elif before.id in protected_ids:
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
            "logical_key": key,
            "change": change,
            "before": {"date": before_date, "status": before_status} if before else None,
            "after": {"date": after_date, "status": after_status} if after else None,
            "protected_history": change == "PROTECTED",
        })
    return to_jsonable_python({"summary": counts, "events": items})
