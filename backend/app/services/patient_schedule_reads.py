"""Read side of a stored patient schedule: current events, protected events, condition status.

Extracted from ``PatientScheduleService`` (P12-02)."""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.identity import event_logical_key
from app.domain.schedule.models import (AssignmentFact, ConditionState, EvaluationResult, PatientConditionStatus, ProcessTransition, RollingHorizon)


class PatientScheduleReads:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ScheduleRepository(session)

    @staticmethod
    def result_date(item: object | None) -> date | None:
        if item is None:
            return None
        timing = getattr(item, "timing", None)
        value = getattr(timing, "nominal_start", None) if timing else None
        return value.date() if isinstance(value, datetime) else value

    def logical_key_for_row(self, row: db.PatientEvent) -> str:
        if row.logical_key:
            return row.logical_key
        event = self.session.get(db.Event, row.event_definition_id)
        return event_logical_key(
            event.code if event else str(row.event_definition_id), row.occurrence_index)

    def current_events(self, patient_id: UUID, schedule_version_id: UUID) -> list[db.PatientEvent]:
        patient_schedule = self.session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient_id,
            db.PatientSchedule.schedule_version_id == schedule_version_id,
            db.PatientSchedule.current_evaluation_id.is_not(None),
        ).order_by(db.PatientSchedule.generated_at.desc()))
        if patient_schedule is None:
            return []
        return list(self.session.scalars(select(db.PatientEvent).where(
            db.PatientEvent.schedule_evaluation_id == patient_schedule.current_evaluation_id,
        )))

    def protected_event_ids(self, events: list[db.PatientEvent]) -> set[UUID]:
        protected = {
            item.id for item in events
            if item.status in {"COMPLETED", "MISSED"} or item.protected_history
        }
        ids = [item.id for item in events]
        if ids:
            protected.update(self.session.scalars(select(db.PatientEventOccurrence.patient_event_id).where(
                db.PatientEventOccurrence.patient_event_id.in_(ids),
                db.PatientEventOccurrence.actual_date.is_not(None),
            )))
        return protected

    def evaluate_result(
        self,
        patient: db.Patient,
        schedule_version_id: UUID,
        *,
        horizon: date,
        condition_overrides: dict[str, PatientConditionStatus] | None = None,
        anchor_overrides: dict[str, date | datetime] | None = None,
        state_overrides: dict[str, tuple[object, datetime]] | None = None,
        rolling: RollingHorizon | None = None,
        planned_anchor_overrides: dict[str, date | datetime] | None = None,
        process_transition_overrides: list[ProcessTransition] | None = None,
        assignment_fact_overrides: list[AssignmentFact] | None = None,
    ) -> EvaluationResult:
        schedule = self.repository.get(schedule_version_id)
        context = self._context(
            patient, schedule_version_id,
            condition_overrides=condition_overrides,
            anchor_overrides=anchor_overrides,
            state_overrides=state_overrides,
            planned_anchor_overrides=planned_anchor_overrides,
            process_transition_overrides=process_transition_overrides,
            assignment_fact_overrides=assignment_fact_overrides,
        )
        return ScheduleEvaluator().evaluate(
            schedule, context, horizon=horizon, rolling=rolling)

    def patient_conditions(
        self, patient_id: UUID,
        *,
        overrides: dict[str, PatientConditionStatus] | None = None,
    ) -> dict[str, PatientConditionStatus]:
        """Current condition state per code, newest non-superseded record wins."""
        conditions: dict[str, PatientConditionStatus] = {}
        for row in self.session.scalars(select(db.PatientCondition).where(
            db.PatientCondition.patient_id == patient_id,
            db.PatientCondition.superseded_by_id.is_(None),
        ).order_by(db.PatientCondition.recorded_at)):
            conditions[row.condition_code] = PatientConditionStatus(
                condition_code=row.condition_code, state=ConditionState(row.state),
                occurrence_date=row.occurrence_date, resolution_date=row.resolution_date,
                occurrence_index=row.occurrence_index,
            )
        conditions.update(overrides or {})
        return conditions
