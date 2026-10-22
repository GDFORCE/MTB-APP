"""READ-ONLY adapter over the legacy operational collections (``patients``, ``visit_instances``).

Used only by the migration job (P11-02). It issues ``find`` calls and nothing else, so running a
migration can never alter the legacy store it is reading.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pymongo.database import Database

from app.application.legacy_migration import LegacyPatient

VISIT_FIELDS = {"_id": 0, "patient_id": 1, "name": 1, "scheduled_date": 1, "window_start": 1, "window_end": 1,
                "visit_number": 1, "seq": 1}


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


class LegacyMongoSource:
    def __init__(self, database: Database, *, trial_key: str = "trial_id"):
        self._db, self._trial_key = database, trial_key

    def list_patients(self, external_trial_id: str, *, after: str | None, limit: int) -> list[LegacyPatient]:
        query: dict[str, Any] = {self._trial_key: external_trial_id}
        if after is not None:
            query["id"] = {"$gt": after}
        documents = list(self._db["patients"].find(query, {"_id": 0}).sort("id", 1).limit(limit))
        ids = [document["id"] for document in documents]
        visits: dict[str, list[dict[str, Any]]] = {key: [] for key in ids}
        if ids:
            for row in self._db["visit_instances"].find({"patient_id": {"$in": ids}}, VISIT_FIELDS):
                visits[row["patient_id"]].append({
                    "name": row.get("name"), "visit_number": row.get("visit_number") or row.get("seq"),
                    "scheduled_date": _date(row.get("scheduled_date")),
                    "window_start": _date(row.get("window_start")), "window_end": _date(row.get("window_end"))})
        return [LegacyPatient(
            legacy_id=str(document["id"]), subject_id=document.get("subject_id"),
            baseline_date=_date(document.get("baseline_date")),
            schedule_definition_id=_uuid(document.get("schedule_definition_id")),
            schedule_version_id=_uuid(document.get("schedule_version_id")),
            arm_label=document.get("arm_label"), cohort_label=document.get("cohort_label"),
            population_label=document.get("population_label"),
            dimension_values=document.get("dimension_values") or {},
            anchor_dates={key: parsed for key, value in (document.get("anchor_dates") or {}).items()
                          if (parsed := _date(value)) is not None},
            anchor_date_sources=document.get("anchor_date_sources") or {},
            visits=tuple(sorted(visits[str(document["id"])], key=lambda item: str(item["name"]))))
            for document in documents]
