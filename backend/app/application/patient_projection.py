"""The patient-safe calendar: an ALLOWLIST serializer over the shared committed projection (P10-03).

Staff and sponsor views hide fields by exception; a patient view must instead name exactly what a patient may
see. Every ``CalendarEntry`` field is classified below and a test fails when a new field is not, so adding a
staff-only field can never leak to a patient by default. Dates are calendar dates (ISO ``YYYY-MM-DD``, never
shifted by a timezone); an undated or undecidable visit is reported as TO_BE_CONFIRMED rather than invented.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from .patients import CalendarEntry
from .ports import UnitOfWork

PATIENT_VERSION = "uctsm-patient-calendar.v1"
PATIENT, STAFF = "PATIENT", "STAFF"

#: Every CalendarEntry field and who may see it. STAFF fields never reach a patient.
CLASSIFICATION: dict[str, str] = {
    "logical_key": PATIENT, "event_code": STAFF, "occurrence_index": STAFF, "logical_occurrence_id": STAFF,
    "evaluation_id": STAFF, "status": PATIENT, "completed": PATIENT, "planned_start": PATIENT,
    "earliest": PATIENT, "latest": PATIENT, "original_planned": STAFF, "actual_visit_date": PATIENT,
    "actual_treatment_date": STAFF, "schedule_version_id": STAFF, "window_known": PATIENT,
    "appointment_date": PATIENT, "appointment_outside_window": STAFF, "override_target": STAFF,
    "override_window_earliest": STAFF, "override_window_latest": STAFF, "override_outside_window": STAFF,
    "override_authority": STAFF, "override_reason": STAFF, "cancelled": PATIENT, "cancelled_reason": STAFF,
    "track_code": STAFF, "track_name": STAFF, "track_rebase_policy": STAFF, "track_movement": STAFF,
    "provisional": PATIENT, "last_command_id": STAFF, "rule_handling": PATIENT,
}
#: Not a visit the patient will ever have.
NEVER_SHOWN = {"NOT_APPLICABLE"}
UNDATED = {"WAITING_FOR_ANCHOR", "WAITING_FOR_CONDITION", "WAITING_FOR_ASSIGNMENT", "UNRESOLVED", "BLOCKED",
           "AVAILABLE_ON_DEMAND"}


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def patient_status(entry: CalendarEntry, today: date) -> str:
    if entry.cancelled or entry.status == "CANCELLED":
        return "CANCELLED"
    if entry.completed or entry.status == "COMPLETED":
        return "DONE"
    if entry.status == "MISSED":
        return "MISSED"
    if entry.status == "PAUSED":
        return "ON_HOLD"
    if (entry.planned_start is None or entry.status in UNDATED or entry.provisional or entry.rule_handling):
        return "TO_BE_CONFIRMED"
    if entry.latest is not None and entry.latest < today:
        return "OVERDUE"
    if entry.earliest is not None and entry.earliest <= today <= (entry.latest or entry.earliest):
        return "DUE_NOW"
    return "UPCOMING"


def _label(title: str, status: str, entry: CalendarEntry) -> str:
    if status == "DONE":
        return f"{title}: done" + (f" on {_iso(entry.actual_visit_date)}" if entry.actual_visit_date else "")
    if status in {"TO_BE_CONFIRMED", "CANCELLED", "MISSED", "ON_HOLD"}:
        return f"{title}: {status.replace('_', ' ').lower()}"
    parts = [f"{title}: planned {_iso(entry.planned_start)}"]
    if entry.window_known and entry.earliest and entry.latest:
        parts.append(f"between {_iso(entry.earliest)} and {_iso(entry.latest)}")
    if entry.appointment_date:
        parts.append(f"your appointment is {_iso(entry.appointment_date)}")
    parts.append(status.replace("_", " ").lower())
    return ", ".join(parts)


def patient_calendar(calendar: list[CalendarEntry], display_names: dict[str, str], *, today: date) -> dict[str, Any]:
    visits = []
    for entry in calendar:
        if entry.status in NEVER_SHOWN:
            continue
        status = patient_status(entry, today)
        title = display_names.get(entry.event_code) or "Study visit"
        dated = status not in {"TO_BE_CONFIRMED", "CANCELLED", "ON_HOLD"}
        visits.append({
            "id": entry.logical_key, "title": title, "status": status,
            "planned_date": _iso(entry.planned_start) if dated else None,
            "window": ({"earliest": _iso(entry.earliest), "latest": _iso(entry.latest)}
                       if dated and entry.window_known and entry.earliest and entry.latest else None),
            "appointment_date": _iso(entry.appointment_date) if status not in {"CANCELLED", "DONE"} else None,
            "completed_on": _iso(entry.actual_visit_date) if status == "DONE" else None,
            "arranged_with_care_team": bool(entry.override_target),
            "accessible_label": _label(title, status, entry)})
    visits.sort(key=lambda row: (row["planned_date"] is None, row["planned_date"] or "", row["id"]))
    return {"projection_version": PATIENT_VERSION, "date_policy": "calendar-date, no timezone shift",
            "as_of": today.isoformat(), "visits": visits}


def display_names(uow: UnitOfWork, organization_id: UUID, schedule_version_id: UUID | None) -> dict[str, str]:
    if schedule_version_id is None:
        return {}
    return {event.code: event.display_name for event in uow.read(lambda tx: tx.schedules.get(schedule_version_id)).events}
