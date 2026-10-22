"""Records what happened to a patient: unscheduled visits, activities, occurrence and patient facts.

Extracted from ``PatientScheduleService`` (P12-02)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.db import models as db
from app.domain.schedule.identity import activity_logical_key
from app.domain.schedule.models import OccurrenceFactKind
from app.services.schedule_facts import (BOOKED_KEY, EFFECTIVE_AT_KEY, END_KEY, FACT_KIND_KEY, RESERVED_STATE_CODES, SUPERSEDES_KEY)


from app.services.patient_schedule_persistence import PatientSchedulePersistence


class PatientScheduleRecording(PatientSchedulePersistence):
    def create_unscheduled_visit(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        event_code: str,
        occurred_on: date,
        reason: str,
        visit_mode: str | None = None,
        actor_id: UUID | None = None,
    ) -> db.PatientUnscheduledVisit:
        """Create one occurrence of a protocol-defined unscheduled visit.

        Doc 10 s13-s15. The definition must already exist in the patient's own
        approved schedule and be marked ON_DEMAND: this creates an OCCURRENCE of
        something the protocol allows, never a new protocol requirement, so the
        master schedule and every other patient are untouched.
        """
        if not reason or not reason.strip():
            raise ValueError(
                "an unscheduled visit needs a reason; without one it cannot be "
                "reviewed against the protocol later"
            )
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            raise ValueError("patient has no active schedule version")
        definition = self.session.scalar(select(db.Event).where(
            db.Event.schedule_version_id == patient.current_schedule_version_id,
            db.Event.code == event_code,
        ))
        if definition is None:
            raise KeyError(f"event {event_code} is not defined in this patient's schedule")
        if (definition.activation or "SCHEDULED") != "ON_DEMAND":
            raise ValueError(
                f"{event_code} is a scheduled protocol visit; only a protocol-defined "
                "unscheduled visit can be created on demand"
            )
        allowed = list(definition.allowed_visit_modes or [])
        if visit_mode is not None and allowed and visit_mode not in allowed:
            raise ValueError(
                f"visit mode {visit_mode!r} is not permitted for {event_code}; "
                f"the protocol allows {', '.join(sorted(allowed))}"
            )
        record = db.PatientUnscheduledVisit(
            patient_id=patient.id, event_definition_id=definition.id,
            event_code=event_code, occurred_on=occurred_on, reason=reason.strip(),
            visit_mode=visit_mode, created_by=actor_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def record_activity(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        event_code: str,
        occurrence_index: int,
        activity_code: str,
        status: str,
        actual_time: datetime | None,
        actor_id: UUID | None,
        reason: str | None = None,
    ) -> db.PatientActivityRecord:
        """Record an intra-day actual time or completion decision.

        Corrections supersede rather than overwrite, so the execution history a
        reviewer sees stays append-only and auditable.
        """
        if status not in {"COMPLETED", "NOT_DONE", "PENDING"}:
            raise ValueError("activity status must be COMPLETED, NOT_DONE, or PENDING")
        if status == "COMPLETED" and actual_time is None:
            raise ValueError("a completed activity requires its actual time")
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        logical_key = activity_logical_key(event_code, occurrence_index, activity_code)
        record = db.PatientActivityRecord(
            patient_id=patient.id, event_code=event_code, occurrence_index=occurrence_index,
            activity_code=activity_code, logical_key=logical_key, status=status,
            actual_time=actual_time, recorded_by=actor_id, reason=reason,
        )
        self.session.add(record)
        self.session.flush()
        superseded = [
            item for item in self.session.scalars(select(db.PatientActivityRecord).where(
                db.PatientActivityRecord.patient_id == patient.id,
                db.PatientActivityRecord.logical_key == logical_key,
                db.PatientActivityRecord.superseded_by_id.is_(None),
                db.PatientActivityRecord.id != record.id,
            ))
        ]
        for item in superseded:
            item.superseded_by_id = record.id
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_ACTIVITY_RECORDED", entity_type="PATIENT", entity_id=patient.id,
            before=({
                "status": superseded[-1].status,
                "actual_time": superseded[-1].actual_time.isoformat() if superseded[-1].actual_time else None,
            } if superseded else None),
            after={
                "logical_key": logical_key, "status": status,
                "actual_time": actual_time.isoformat() if actual_time else None,
            },
            reason=reason,
        ))
        return record

    def record_occurrence_fact(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        logical_key: str,
        kind: OccurrenceFactKind,
        value: date | None = None,
        end_value: date | None = None,
        effective_at: date | None = None,
        supersedes_occurrence_id: UUID | None = None,
        actor_id: UUID | None = None,
        reason: str | None = None,
        source: str | None = None,
    ) -> db.PatientEventOccurrence:
        """Record a performed / missed / booked fact for one logical occurrence.

        PT-02: append-only. A correction names the row it supersedes; that row
        stays as history (its PatientEvent stays protected) and the new value is
        what later evaluations use. PT-04: a booking never sets an actual date
        and never changes the rule. PT-07: nothing here is derived from a date
        passing - a performed fact is only what a person records.
        """
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            raise ValueError("patient has no active schedule version")
        row = next((item for item in self.current_events(
            patient.id, patient.current_schedule_version_id) if item.logical_key == logical_key), None)
        if row is None:
            raise KeyError(f"occurrence {logical_key} is not in the patient's current plan")
        if kind in {OccurrenceFactKind.PERFORMED, OccurrenceFactKind.BOOKED} and value is None:
            raise ValueError(f"a {kind.value.lower()} fact needs its date")
        previous = None
        if supersedes_occurrence_id is not None:
            previous = self.session.get(db.PatientEventOccurrence, supersedes_occurrence_id)
            if previous is None:
                raise KeyError("the occurrence record being corrected does not exist")
            previous_event = self.session.get(db.PatientEvent, previous.patient_event_id)
            if previous_event is None or previous_event.logical_key != logical_key:
                raise ValueError("a correction must supersede a record of the same occurrence")
        metadata: dict[str, object] = {FACT_KIND_KEY: kind.value}
        if supersedes_occurrence_id is not None:
            metadata[SUPERSEDES_KEY] = str(supersedes_occurrence_id)
        if effective_at is not None:
            metadata[EFFECTIVE_AT_KEY] = effective_at.isoformat()
        if end_value is not None:
            metadata[END_KEY] = end_value.isoformat()
        if source is not None:
            metadata["source"] = source
        if kind == OccurrenceFactKind.BOOKED:
            metadata[BOOKED_KEY] = value.isoformat()
        occurrence = db.PatientEventOccurrence(
            patient_event_id=row.id,
            occurrence_type="BOOKING" if kind == OccurrenceFactKind.BOOKED else "VISIT",
            scheduled_date=row.nominal_start_date,
            actual_date=value if kind == OccurrenceFactKind.PERFORMED else None,
            status={
                OccurrenceFactKind.PERFORMED: "COMPLETED",
                OccurrenceFactKind.MISSED: "MISSED",
                OccurrenceFactKind.BOOKED: "BOOKED",
            }[kind],
            recorded_by=actor_id, recorded_at=datetime.now(timezone.utc),
            metadata_json=metadata,
        )
        self.session.add(occurrence)
        if kind == OccurrenceFactKind.PERFORMED:
            row.status = "COMPLETED"
        elif kind == OccurrenceFactKind.MISSED:
            row.status = "MISSED"
        self.session.flush()
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action=("PATIENT_OCCURRENCE_FACT_CORRECTED" if previous is not None
                    else "PATIENT_OCCURRENCE_FACT_RECORDED"),
            entity_type="PATIENT_EVENT", entity_id=row.id,
            before=({
                "occurrence_id": str(previous.id), "actual_date": (
                    previous.actual_date.isoformat() if previous.actual_date else None),
                "status": previous.status, "metadata": previous.metadata_json,
            } if previous is not None else None),
            after={
                "occurrence_id": str(occurrence.id), "logical_key": logical_key,
                "kind": kind.value, "value": value.isoformat() if value else None,
                "metadata": metadata,
            },
            reason=reason,
        ))
        return occurrence

    def record_patient_fact(
        self,
        patient: db.Patient,
        *,
        state_code: str,
        value: dict[str, object],
        effective_at: date | datetime,
        actor_id: UUID | None,
        source_reference: dict[str, object] | None = None,
    ) -> db.PatientState:
        """Append one reserved-code WP3 fact (process transition, assignment
        fact, site calendar) to the patient's append-only state history."""
        if state_code not in RESERVED_STATE_CODES:
            raise ValueError(f"{state_code!r} is not a typed WP3 fact")
        effective = (effective_at if isinstance(effective_at, datetime)
                     else datetime.combine(effective_at, datetime.min.time(), tzinfo=timezone.utc))
        row = db.PatientState(
            patient_id=patient.id, state_code=state_code, state_value=value,
            effective_at=effective, recorded_by=actor_id,
            source_reference=source_reference or {},
        )
        self.session.add(row)
        self.session.flush()
        return row
