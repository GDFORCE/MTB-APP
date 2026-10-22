"""Schedule reports built from the committed projection and the immutable records (P10-05).

One patient = one committed snapshot (the same ``ProjectionSnapshotService`` the calendar uses), so a report
row can only differ from the calendar if this module changes a value; ``reconciles`` checks exactly that.
Sponsor/CRO profiles never receive operational, command-authority or reason detail.
"""
from __future__ import annotations

import csv
import io
from typing import Any
from uuid import UUID

from .patients import CalendarEntry, PatientRecord
from .ports import UnitOfWork
from .projection_snapshot import PROJECTION_VERSION, ProjectionSnapshotService

STAFF, SPONSOR = "STAFF", "SPONSOR"
COLUMNS_SPONSOR = ["patient_code", "visit", "status", "target_date", "window_earliest", "window_latest",
                   "actual_visit_date", "deviation_days", "outside_window", "schedule_version_id", "evaluation_id",
                   "amendment_state"]
COLUMNS_STAFF = COLUMNS_SPONSOR[:6] + ["appointment_date", "actual_visit_date", "actual_treatment_date",
                                        "deviation_days", "outside_window", "override_target", "cancelled_reason",
                                        "commands_applied"] + COLUMNS_SPONSOR[9:]
_INJECTION = ("=", "+", "-", "@", "\t", "\r")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def deviation(entry: CalendarEntry) -> tuple[int | None, bool | None]:
    """Days between the actual visit and the protocol target, and whether it fell outside the stated window.
    Both are None when either date is unknown: a missing fact is never reported as zero or as compliant."""
    if entry.actual_visit_date is None or entry.planned_start is None:
        return None, None
    days = (entry.actual_visit_date - entry.planned_start).days
    if not entry.window_known or entry.earliest is None or entry.latest is None:
        return days, None
    return days, not (entry.earliest <= entry.actual_visit_date <= entry.latest)


class ScheduleReportService:
    def __init__(self, uow: UnitOfWork):
        self._uow = uow
        self._snapshots = ProjectionSnapshotService(uow)

    def patient_report(self, organization_id: UUID, trial_id: UUID, patient: PatientRecord, *, profile: str) -> dict[str, Any]:
        snapshot = self._snapshots.get(organization_id=organization_id, trial_id=trial_id,
                                       external_patient_id=patient.external_patient_id or "")
        def facts(tx):
            commands = tx.ledger.list_for_patient(organization_id, patient.id)
            decisions = tx.amendments.list_decisions_for_patient(organization_id, patient.id)
            names = {}
            if snapshot["schedule_version_id"]:
                names = {e.code: e.display_name for e in tx.schedules.get(snapshot["schedule_version_id"]).events}
            return commands, decisions, names
        commands, decisions, names = self._uow.read(facts)
        applied: dict[str, int] = {}
        for command in commands:
            if command.logical_key:
                applied[command.logical_key] = applied.get(command.logical_key, 0) + 1
        amendment = getattr(decisions[-1].status, "value", decisions[-1].status) if decisions else None
        rows = []
        for entry in snapshot["calendar"]:
            days, outside = deviation(entry)
            row = {"patient_code": patient.patient_code, "visit": names.get(entry.event_code, entry.event_code),
                   "logical_key": entry.logical_key, "status": entry.status,
                   "target_date": _iso(entry.planned_start), "window_earliest": _iso(entry.earliest),
                   "window_latest": _iso(entry.latest), "actual_visit_date": _iso(entry.actual_visit_date),
                   "deviation_days": days, "outside_window": outside,
                   "schedule_version_id": str(snapshot["schedule_version_id"] or ""),
                   "evaluation_id": str(snapshot["evaluation_id"] or ""), "amendment_state": amendment}
            if profile == STAFF:
                row.update({"appointment_date": _iso(entry.appointment_date),
                            "actual_treatment_date": _iso(entry.actual_treatment_date),
                            "override_target": _iso(entry.override_target),
                            "cancelled_reason": entry.cancelled_reason,
                            "commands_applied": applied.get(entry.logical_key, 0)})
            rows.append(row)
        return {"patient_code": patient.patient_code, "evaluation_id": str(snapshot["evaluation_id"] or ""),
                "projection_version": PROJECTION_VERSION, "rows": rows}

    @staticmethod
    def reconciles(report: dict[str, Any], calendar: list[CalendarEntry]) -> list[str]:
        """Empty when every calendar visit appears in the report with identical target, window, actual and status."""
        by_key = {row["logical_key"]: row for row in report["rows"]}
        problems = [f"MISSING_IN_REPORT:{entry.logical_key}" for entry in calendar if entry.logical_key not in by_key]
        problems += [f"NOT_IN_CALENDAR:{key}" for key in by_key if key not in {e.logical_key for e in calendar}]
        for entry in calendar:
            row = by_key.get(entry.logical_key)
            if row is None:
                continue
            expected = {"target_date": _iso(entry.planned_start), "window_earliest": _iso(entry.earliest),
                        "window_latest": _iso(entry.latest), "actual_visit_date": _iso(entry.actual_visit_date),
                        "status": entry.status}
            problems += [f"MISMATCH:{entry.logical_key}:{field}" for field, value in expected.items()
                         if row[field] != value]
        return problems


def to_csv(rows: list[dict[str, Any]], *, profile: str, provenance: dict[str, str]) -> str:
    """Deterministic CSV: provenance comment lines, role-specific columns, spreadsheet-formula injection neutralised."""
    columns = COLUMNS_STAFF if profile == STAFF else COLUMNS_SPONSOR
    out = io.StringIO()
    for key in sorted(provenance):
        out.write(f"# {key}: {provenance[key]}\n")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column)
            text = "" if value is None else str(value)
            cells.append("'" + text if text.startswith(_INJECTION) else text)
        writer.writerow(cells)
    return out.getvalue()
