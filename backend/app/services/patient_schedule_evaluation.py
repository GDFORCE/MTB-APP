"""Runs the evaluator for one patient and stores the result.

Extracted from ``PatientScheduleService`` (P12-02)."""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db import models as db
from app.domain.schedule.identity import event_logical_key
from app.domain.schedule.models import RollingHorizon
from app.services.schedule_facts import utc_now


from app.services.patient_schedule_recording import PatientScheduleRecording


class PatientScheduleEvaluation(PatientScheduleRecording):
    def evaluate(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        horizon: date,
        schedule_version_id: UUID | None = None,
        idempotency_key: str | None = None,
        prior_events_override: list[db.PatientEvent] | None = None,
        rolling: RollingHorizon | None = None,
        actor_id: UUID | None = None,
        reason: str | None = None,
    ) -> tuple[db.ScheduleEvaluation, list[db.PatientEvent]]:
        """Evaluate one patient against a schedule version.

        AD-4d: ``schedule_version_id`` lets a caller that already resolved the
        target version (``impact_service.confirm()``, which is in the middle of
        MOVING a patient to a new version) pass it explicitly instead of relying
        on a just-written ``patient.current_schedule_version_id`` being visible
        to a second, independent read inside this method. When omitted, the
        patient's currently pinned version is used -- resolved exactly once,
        here, and threaded through the rest of this call rather than re-read
        later.
        """
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        resolved_version_id = (
            schedule_version_id if schedule_version_id is not None
            else patient.current_schedule_version_id
        )
        if resolved_version_id is None:
            raise ValueError("patient is not pinned to an approved schedule")
        if idempotency_key:
            existing = self.session.scalar(select(db.ScheduleGenerationRun).where(
                db.ScheduleGenerationRun.patient_id == patient_id,
                db.ScheduleGenerationRun.idempotency_key == idempotency_key,
            ))
            if existing:
                evaluation = self.session.scalar(select(db.ScheduleEvaluation).where(
                    db.ScheduleEvaluation.generation_run_id == existing.id,
                ))
                assert evaluation is not None
                events = list(self.session.scalars(select(db.PatientEvent).where(
                    db.PatientEvent.schedule_evaluation_id == evaluation.id,
                )))
                return evaluation, events
        schedule = self.repository.get(resolved_version_id)
        prior_events = (
            prior_events_override if prior_events_override is not None
            else self.current_events(patient.id, schedule.schedule_version_id)
        )
        started = utc_now()
        # Doc 3 s7-s8: an open-ended protocol persists a bounded rolling set of
        # upcoming occurrences, not every cycle the date horizon would allow.
        result = self.evaluate_result(
            patient, schedule.schedule_version_id, horizon=horizon, rolling=rolling)
        run = db.ScheduleGenerationRun(
            patient_id=patient.id, schedule_version_id=schedule.schedule_version_id,
            idempotency_key=idempotency_key, evaluator_version=result.evaluator_version,
            status="COMPLETED", started_at=started, completed_at=utc_now(),
        )
        self.session.add(run)
        self.session.flush()
        patient_schedule = self.session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient.id,
            db.PatientSchedule.schedule_version_id == schedule.schedule_version_id,
        ))
        if patient_schedule is None:
            patient_schedule = db.PatientSchedule(
                patient_id=patient.id, schedule_version_id=schedule.schedule_version_id,
            )
            self.session.add(patient_schedule)
            self.session.flush()
        # Captured before it is overwritten below, so confinement reconciliation
        # can find the episodes the OUTGOING evaluation produced.
        prior_evaluation_id = patient_schedule.current_evaluation_id
        patient_schedule.status = "ACTIVE"
        evaluation = db.ScheduleEvaluation(
            patient_schedule_id=patient_schedule.id, generation_run_id=run.id,
            input_snapshot=result.input_snapshot,
            output_summary={"statuses": [item.status.value for item in result.events], "count": len(result.events)},
            evaluator_version=result.evaluator_version, evaluated_at=result.evaluated_at,
        )
        self.session.add(evaluation)
        self.session.flush()
        persisted = []
        prior_by_key = {self.logical_key_for_row(item): item for item in prior_events}
        protected_ids = self.protected_event_ids(prior_events)
        reconciliation = {key: 0 for key in ("UNCHANGED", "ADDED", "MOVED", "STATUS_CHANGED", "CANCELLED", "PROTECTED")}
        for item in result.events:
            timing = item.timing
            def as_date(value: date | datetime | None) -> date | None:
                return value.date() if isinstance(value, datetime) else value
            logical_key = event_logical_key(item.event_code, item.occurrence_index)
            previous = prior_by_key.pop(logical_key, None)
            protected = previous is not None and previous.id in protected_ids
            status = previous.status if protected else item.status.value
            nominal_start = previous.nominal_start_date if protected else (as_date(timing.nominal_start) if timing else None)
            nominal_end = previous.nominal_end_date if protected else (as_date(timing.nominal_end) if timing else None)
            earliest = previous.earliest_date if protected else (as_date(timing.earliest) if timing else None)
            latest = previous.latest_date if protected else (as_date(timing.latest) if timing else None)
            if previous is None:
                change = "ADDED"
            elif protected:
                change = "PROTECTED"
            elif previous.nominal_start_date != nominal_start:
                change = "MOVED"
            elif previous.status != status:
                change = "STATUS_CHANGED"
            else:
                change = "UNCHANGED"
            reconciliation[change] += 1
            explanation = dict(item.explanation)
            explanation["reconciliation"] = {
                "change": change,
                "supersedes_patient_event_id": str(previous.id) if previous else None,
            }
            row = db.PatientEvent(
                patient_schedule_id=patient_schedule.id, schedule_evaluation_id=evaluation.id,
                event_definition_id=item.event_definition_id, occurrence_index=item.occurrence_index,
                logical_occurrence_id=(previous.logical_occurrence_id if previous and previous.logical_occurrence_id else uuid4()),
                logical_key=logical_key,
                supersedes_patient_event_id=previous.id if previous else None,
                protected_history=protected,
                status=status,
                nominal_start_date=nominal_start, nominal_end_date=nominal_end,
                earliest_date=earliest, latest_date=latest,
                timing_resolution=(previous.timing_resolution if protected and previous else (timing.model_dump(mode="json") if timing else {})),
                applicability_result={"value": item.applicability_result},
                condition_result={"value": item.condition_result},
                dependency_result=item.dependency_result,
                generation_reason=explanation,
                visit_mode=item.visit_mode,
                unscheduled_reason=item.unscheduled_reason,
                schedule_version_id=schedule.schedule_version_id,
                protocol_version_id=schedule.schedule_metadata.protocol_version_id,
            )
            self.session.add(row)
            self._persist_activities(
                row, item, schedule_version_id=schedule.schedule_version_id,
                protocol_version_id=schedule.schedule_metadata.protocol_version_id)
            persisted.append(row)
        for logical_key, previous in prior_by_key.items():
            protected = previous.id in protected_ids
            change = "PROTECTED" if protected else "CANCELLED"
            reconciliation[change] += 1
            row = db.PatientEvent(
                patient_schedule_id=patient_schedule.id, schedule_evaluation_id=evaluation.id,
                event_definition_id=previous.event_definition_id,
                occurrence_index=previous.occurrence_index,
                logical_occurrence_id=previous.logical_occurrence_id or uuid4(),
                logical_key=logical_key, supersedes_patient_event_id=previous.id,
                protected_history=protected,
                status=previous.status if protected else "CANCELLED",
                nominal_start_date=previous.nominal_start_date,
                nominal_end_date=previous.nominal_end_date,
                earliest_date=previous.earliest_date, latest_date=previous.latest_date,
                timing_resolution=previous.timing_resolution,
                applicability_result=previous.applicability_result,
                condition_result=previous.condition_result,
                dependency_result=previous.dependency_result,
                generation_reason={
                    **(previous.generation_reason or {}),
                    "reconciliation": {
                        "change": change,
                        "supersedes_patient_event_id": str(previous.id),
                    },
                },
                schedule_version_id=schedule.schedule_version_id,
                protocol_version_id=schedule.schedule_metadata.protocol_version_id,
            )
            self.session.add(row)
            persisted.append(row)
        self._persist_confinements(
            patient_schedule.id, evaluation.id, result.confinements,
            prior_evaluation_id=prior_evaluation_id,
            schedule_version_id=schedule.schedule_version_id,
            protocol_version_id=schedule.schedule_metadata.protocol_version_id,
        )
        self.session.flush()
        evaluation.output_summary = {
            **evaluation.output_summary,
            "persisted_count": len(persisted),
            "reconciliation": reconciliation,
        }
        patient_schedule.current_evaluation_id = evaluation.id
        patient_schedule.generation_run_id = run.id
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_SCHEDULE_EVALUATED",
            entity_type="PATIENT", entity_id=patient.id, before=None,
            after={
                "evaluation_id": str(evaluation.id), "event_count": len(persisted),
                "reconciliation": reconciliation,
            },
            reason=reason,
            metadata_json={"evaluator_version": result.evaluator_version},
        ))
        return evaluation, persisted
