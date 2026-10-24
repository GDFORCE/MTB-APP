"""Run the real schedule engine on a compiled schedule; nothing here re-implements it.

The protocol table comes from ``project_schedule`` and the date table from
``ScheduleEvaluator``, the same code the patient screens use. The lab only supplies
what a patient record would: anchor dates typed in by the reviewer.
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from . import bootstrap  # noqa: F401
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import PatientContext, ScheduleStatus, UniversalSchedule
from app.domain.schedule.projection import project_schedule
from app.domain.schedule.validator import ScheduleValidator


def load_schedule(data: dict) -> UniversalSchedule:
    return UniversalSchedule.model_validate(data)


def anchor_codes(schedule: UniversalSchedule) -> list[tuple[str, str]]:
    """(code, label) for every anchor a reviewer may give a date for."""
    return [(a.code, a.display_name) for a in schedule.anchors]


def protocol_rows(schedule: UniversalSchedule) -> list[dict]:
    """The Protocol table: Visit, Visit Name, Timing, Window, Type, Activities, Status, Notes."""
    rows = []
    for number, item in enumerate(project_schedule(schedule), 1):
        notes = [text for text in (item.condition_display, item.constraints_display) if text]
        if item.requires_review:
            notes.insert(0, "Review required")
        rows.append({"Visit": number, "Visit Name": item.title, "Timing": item.timing_display,
                     "Window": item.window_display or "Not stated", "Type": item.event_type_display,
                     "Activities": ", ".join(item.activities_display) or "No activities extracted",
                     "Status": item.status, "Notes": "; ".join(notes)})
    return rows


def _day(value) -> str:
    if value is None:
        return ""
    return value.strftime("%d %b %Y %H:%M") if isinstance(value, datetime) else value.strftime("%d %b %Y")


def _window(timing) -> str:
    if timing is None or (timing.earliest is None and timing.latest is None):
        return "Not stated"
    return f"{_day(timing.earliest) or 'open'} to {_day(timing.latest) or 'open'}"


def patient_rows(schedule: UniversalSchedule, anchors: dict[str, date | datetime], *,
                 horizon: date, today: date | None = None) -> tuple[list[dict], dict]:
    """The Patient table: Visit, Visit Name, Expected Date, Allowed Window, Activities, Status.

    A visit whose inputs are missing keeps its WAITING_* status and no date: nothing is guessed.
    Returns ([], {"blocked_by": [...]}) when the schedule has blocking integrity findings.
    The schedule is approved on an in-memory copy only, because the evaluator refuses anything
    else; no schedule is saved and no patient exists.
    """
    approved = schedule.model_copy(deep=True)
    approved.schedule_metadata.status = ScheduleStatus.APPROVED
    blocking = ScheduleValidator.blocking(ScheduleValidator().validate(approved, evaluating_approved=True))
    if blocking:
        # The engine refuses to date a schedule that fails its own integrity checks; say why.
        return [], {"blocked_by": [f"{issue.issue_code}: {issue.message}" for issue in blocking]}
    context = PatientContext(patient_id=uuid4(), schedule_version_id=approved.schedule_version_id,
                             anchors=dict(anchors))
    result = ScheduleEvaluator().evaluate(approved, context, horizon=horizon, today=today or date.today())
    names = {event.code: event.display_name for event in approved.events}
    order = {event.code: number for number, event in enumerate(approved.events, 1)}
    rows = []
    for item in result.events:
        timing = item.timing
        occurrence = f" #{item.occurrence_index + 1}" if item.occurrence_index else ""
        rows.append({"Visit": f"{order.get(item.event_code, '')}{occurrence}",
                     "Visit Name": names.get(item.event_code, item.event_code),
                     "Expected Date": _day(timing.nominal_start) if timing else "",
                     "Allowed Window": _window(timing),
                     "Activities": ", ".join(a.display_name for a in item.activities),
                     "Status": item.status.value,
                     "Why": str(item.explanation.get("reason") or item.explanation.get("reason_code") or "")})
    return rows, {"evaluator": result.evaluator_version, "events": len(result.events)}
