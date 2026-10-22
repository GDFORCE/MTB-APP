"""Reserved state codes, occurrence metadata keys and the row -> typed fact mapping.

Extracted from ``schedule_service`` (P12-02); ``schedule_service`` re-exports every name."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.db import models as db
from app.domain.schedule.models import OccurrenceFact, OccurrenceFactKind

#: WP3 patient facts live in the append-only ``uctsm_patient_states`` table
#: under these reserved state codes (``state_value`` = the typed fact as JSON,
#: ``effective_at`` = its effective time, ``created_at`` = its recorded time).
#: They are typed inputs, never condition fields: ``_context`` keeps them out
#: of ``PatientContext.state``.
PROCESS_TRANSITION_STATE_CODE = "uctsm:process_transition"
ASSIGNMENT_FACT_STATE_CODE = "uctsm:assignment_fact"
#: A holiday calendar supplied for the patient's site (until a site store exists).
SITE_HOLIDAY_CALENDAR_STATE_CODE = "uctsm:site_holiday_calendar"
RESERVED_STATE_CODES = frozenset({
    PROCESS_TRANSITION_STATE_CODE, ASSIGNMENT_FACT_STATE_CODE, SITE_HOLIDAY_CALENDAR_STATE_CODE,
})

#: ``PatientEventOccurrence.metadata`` keys for WP3 facts. A row without them
#: keeps its previous meaning (actual_date = performed; status MISSED = missed).
FACT_KIND_KEY = "fact_kind"
SUPERSEDES_KEY = "supersedes_occurrence_id"
EFFECTIVE_AT_KEY = "effective_at"
BOOKED_KEY = "booked_date"
END_KEY = "end_date"


def occurrence_fact_from_row(
    row: "db.PatientEventOccurrence", event_code: str, occurrence_index: int,
) -> OccurrenceFact | None:
    """The typed fact one occurrence row records, or None for a row that
    records nothing the engine acts on."""
    metadata = row.metadata_json or {}
    kind = metadata.get(FACT_KIND_KEY)
    value: date | datetime | None = row.actual_date
    if kind == OccurrenceFactKind.BOOKED.value or row.occurrence_type == "BOOKING":
        kind = OccurrenceFactKind.BOOKED.value
        booked = metadata.get(BOOKED_KEY) or (row.scheduled_date.isoformat() if row.scheduled_date else None)
        value = date.fromisoformat(str(booked)[:10]) if booked else None
    elif kind is None:
        if row.actual_date is not None:
            kind = OccurrenceFactKind.PERFORMED.value
        elif row.status == "MISSED":
            kind = OccurrenceFactKind.MISSED.value
        else:
            return None
    if kind in {OccurrenceFactKind.PERFORMED.value, OccurrenceFactKind.BOOKED.value} and value is None:
        return None
    end = metadata.get(END_KEY)
    effective = metadata.get(EFFECTIVE_AT_KEY)
    recorded = row.recorded_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
    return OccurrenceFact(
        id=str(row.id), event_code=event_code, occurrence_index=occurrence_index,
        kind=OccurrenceFactKind(kind), value=value,
        end_value=date.fromisoformat(str(end)[:10]) if end else None,
        effective_at=date.fromisoformat(str(effective)[:10]) if effective else value,
        recorded_at=recorded,
        supersedes=metadata.get(SUPERSEDES_KEY),
        source=metadata.get("source"),
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
