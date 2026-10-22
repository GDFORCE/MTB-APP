"""Builds the evaluation context (facts, states, calendars) a stored patient is evaluated against.

Extracted from ``PatientScheduleService`` (P12-02)."""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select

from app.db import models as db
from app.domain.schedule.anchor_status import record_is_actual
from app.domain.schedule.models import (AssignmentFact, HolidayCalendar, OccurrenceFact, OccurrenceFactKind, PatientConditionStatus, PatientContext, ProcessTransition, UnscheduledOccurrence)
from app.services.schedule_facts import (ASSIGNMENT_FACT_STATE_CODE, PROCESS_TRANSITION_STATE_CODE, SITE_HOLIDAY_CALENDAR_STATE_CODE, SUPERSEDES_KEY, occurrence_fact_from_row)


from app.services.patient_schedule_reads import PatientScheduleReads


class PatientScheduleContext(PatientScheduleReads):
    def _context(
        self,
        patient: db.Patient,
        schedule_version_id: UUID,
        *,
        condition_overrides: dict[str, PatientConditionStatus] | None = None,
        anchor_overrides: dict[str, date | datetime] | None = None,
        state_overrides: dict[str, tuple[object, datetime]] | None = None,
        planned_anchor_overrides: dict[str, date | datetime] | None = None,
        process_transition_overrides: list[ProcessTransition] | None = None,
        assignment_fact_overrides: list[AssignmentFact] | None = None,
    ) -> PatientContext:
        # Doc C6: planned and actual anchor values are kept apart. Merging them
        # let a planned surgery date date the post-op visits and send reminders
        # as if surgery had happened. Which dict a record lands in is decided by
        # the same rule the Patient Profile uses (anchor_status.record_is_actual).
        anchors: dict[str, date | datetime] = {}
        planned_anchors: dict[str, date | datetime] = {}
        rows = self.session.execute(
            select(
                db.Anchor.code, db.Anchor.derivation_rule,
                db.PatientAnchor.value_date, db.PatientAnchor.value_datetime,
                db.PatientAnchor.status, db.PatientAnchor.source_type,
            )
            .join(db.PatientAnchor, db.PatientAnchor.anchor_definition_id == db.Anchor.id)
            .where(
                db.PatientAnchor.patient_id == patient.id,
                db.PatientAnchor.status.in_(["CONFIRMED", "PROVISIONAL"]),
            )
            .order_by(db.PatientAnchor.recorded_at)
        )
        for code, derivation_rule, value_date, value_datetime, status, source_type in rows:
            value = value_datetime or value_date
            target = anchors if record_is_actual(status, source_type) else planned_anchors
            selection = str((derivation_rule or {}).get("selection", "LAST")).upper()
            if selection == "FIRST" and code in target:
                continue
            target[code] = value
        anchors.update(anchor_overrides or {})
        planned_anchors.update(planned_anchor_overrides or {})
        actual_event_values: dict[str, list[date | datetime]] = {}
        occurrence_rows = self.session.execute(
            select(db.Event.code, db.PatientEvent.occurrence_index, db.PatientEventOccurrence)
            .join(db.PatientEvent, db.PatientEvent.id == db.PatientEventOccurrence.patient_event_id)
            .join(db.PatientSchedule, db.PatientSchedule.id == db.PatientEvent.patient_schedule_id)
            .join(db.Event, db.Event.id == db.PatientEvent.event_definition_id)
            .where(db.PatientSchedule.patient_id == patient.id)
            .order_by(db.PatientEventOccurrence.recorded_at)
        ).all()
        # PT-02: a correction supersedes by id. The corrected row stays in the
        # table (history); only the acting value feeds evaluation.
        superseded = {
            str((row.metadata_json or {}).get(SUPERSEDES_KEY))
            for _, _, row in occurrence_rows
            if (row.metadata_json or {}).get(SUPERSEDES_KEY)
        }
        occurrence_facts: list[OccurrenceFact] = []
        for code, occurrence_index, row in occurrence_rows:
            fact = occurrence_fact_from_row(row, code, occurrence_index)
            if fact is not None:
                occurrence_facts.append(fact)
            if row.actual_date is None or str(row.id) in superseded:
                continue
            if fact is not None and fact.kind != OccurrenceFactKind.PERFORMED:
                continue
            actual_event_values.setdefault(code, []).append(row.actual_date)
        for values in actual_event_values.values():
            values.sort()
        activity_actuals: dict[str, date | datetime] = {}
        activity_statuses: dict[str, str] = {}
        for record in self.session.scalars(select(db.PatientActivityRecord).where(
            db.PatientActivityRecord.patient_id == patient.id,
            db.PatientActivityRecord.superseded_by_id.is_(None),
        ).order_by(db.PatientActivityRecord.recorded_at)):
            activity_statuses[record.logical_key] = record.status
            if record.actual_time is not None:
                activity_actuals[record.logical_key] = record.actual_time
            else:
                activity_actuals.pop(record.logical_key, None)
        state: dict[str, object] = {}
        state_effective_at: dict[str, datetime] = {}
        process_transitions: list[ProcessTransition] = []
        assignment_history: list[AssignmentFact] = []
        site_calendars: dict[str, HolidayCalendar] = {}
        for row in self.session.scalars(select(db.PatientState).where(
            db.PatientState.patient_id == patient.id,
        ).order_by(db.PatientState.effective_at, db.PatientState.created_at)):
            if row.state_code == PROCESS_TRANSITION_STATE_CODE:
                process_transitions.append(ProcessTransition.model_validate(row.state_value))
                continue
            if row.state_code == ASSIGNMENT_FACT_STATE_CODE:
                assignment_history.append(AssignmentFact.model_validate(row.state_value))
                continue
            if row.state_code == SITE_HOLIDAY_CALENDAR_STATE_CODE:
                calendar = HolidayCalendar.model_validate(row.state_value)
                site_calendars[calendar.code] = calendar
                continue
            state[row.state_code] = row.state_value
            state_effective_at[row.state_code] = row.effective_at
        process_transitions.extend(process_transition_overrides or [])
        assignment_history.extend(assignment_fact_overrides or [])
        for code, (value, effective_at) in (state_overrides or {}).items():
            state[code] = value
            state_effective_at[code] = effective_at
        # Doc 10 s14: unscheduled visits the site created are INPUTS to
        # evaluation, so regeneration reproduces them rather than erasing them.
        unscheduled = [
            UnscheduledOccurrence(
                event_code=row.event_code, occurred_on=row.occurred_on,
                reason=row.reason, visit_mode=row.visit_mode,
                created_by=row.created_by, recorded_at=row.recorded_at,
            )
            for row in self.session.scalars(select(db.PatientUnscheduledVisit).where(
                db.PatientUnscheduledVisit.patient_id == patient.id,
            ).order_by(db.PatientUnscheduledVisit.occurred_on))
        ]
        arm_code = self.session.get(db.Arm, patient.arm_id).code if patient.arm_id else None
        cohort_code = self.session.get(db.Cohort, patient.cohort_id).code if patient.cohort_id else None
        population_code = self.session.get(db.Population, patient.population_id).code if patient.population_id else None
        return PatientContext(
            patient_id=patient.id, schedule_version_id=schedule_version_id,
            anchors=anchors, planned_anchors=planned_anchors,
            event_values={key: list(values) for key, values in actual_event_values.items()},
            actual_event_values=actual_event_values,
            activity_actuals=activity_actuals, activity_statuses=activity_statuses,
            conditions=self.patient_conditions(patient.id, overrides=condition_overrides),
            unscheduled_occurrences=unscheduled,
            state=state,
            state_effective_at=state_effective_at, arm_code=arm_code,
            cohort_code=cohort_code, population_code=population_code,
            dimension_values=patient.dimension_values or {},
            occurrence_facts=occurrence_facts,
            process_transitions=process_transitions,
            assignment_history=assignment_history,
            holiday_calendars=list(site_calendars.values()),
        )
