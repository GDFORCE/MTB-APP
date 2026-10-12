"""Idempotent boundary between operational IDs and canonical UCTSM IDs.

A behaviour-preserving port of ``app.services.operational_bridge`` on the
repository ports. Enrolment is ONE unit of work: the patient, its assignment
history, its anchors and its first evaluation commit together or not at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, TypeVar
from uuid import UUID

from app.domain.schedule.models import schedule_origin_anchor

from .errors import DuplicateRecordError, StaleRevisionError
from .patient_schedule import PatientScheduleService
from .patients import (
    AssignmentRecord, PatientAnchorRecord, PatientRecord,
)
from .ports import UnitOfWork
from .read_services import dimension_id, enrollment_anchors
from .records import AuditRecord
from .schedule_resolution import resolve_current_schedule_version

T = TypeVar("T")
_RETRIES = 3


@dataclass(frozen=True)
class BridgeActivity:
    """One activity within one patient visit occurrence (doc s12)."""

    code: str | None
    display_name: str
    status: str
    requiredness: str
    planned_time: datetime | None
    earliest_time: datetime | None
    latest_time: datetime | None
    actual_time: datetime | None


@dataclass(frozen=True)
class BridgeEvent:
    patient_event_id: UUID
    logical_occurrence_id: UUID
    logical_key: str
    event_definition_id: UUID
    event_code: str
    name: str
    event_type: str
    status: str
    nominal_date: date | None
    earliest_date: date | None
    latest_date: date | None
    activities: tuple[str, ...]
    activity_details: tuple[BridgeActivity, ...] = ()


@dataclass(frozen=True)
class BridgeEnrollment:
    patient_id: UUID
    schedule_version_id: UUID
    evaluation_id: UUID
    events: tuple[BridgeEvent, ...]


class OperationalBridgeService:
    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    def _run(self, work: Callable[[UnitOfWork], T]) -> T:
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._uow.run(work)
            except (StaleRevisionError, DuplicateRecordError) as error:
                last = error
        assert last is not None
        raise last

    def validate_trial_link(
        self, *, organization_id: UUID, external_trial_id: str,
        external_schedule_definition_id: str | None, trial_id: UUID, schedule_definition_id: UUID,
    ) -> dict[str, str]:
        def work(tx: UnitOfWork) -> dict[str, str]:
            try:
                trial = tx.authoring.get_trial(organization_id, trial_id)
            except KeyError as error:
                raise KeyError("canonical trial not found") from error
            try:
                definition = tx.authoring.get_definition(schedule_definition_id)
            except KeyError:
                definition = None
            if definition is None or definition.trial_id != trial.id:
                raise KeyError("canonical schedule definition does not belong to the trial")
            approved = resolve_current_schedule_version(
                tx, organization_id, trial.id, definition.id)
            tx.authoring.set_trial_external_id(organization_id, trial.id, external_trial_id)
            tx.authoring.set_definition_external_id(
                definition.id, external_schedule_definition_id or external_trial_id)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, action="OPERATIONAL_TRIAL_LINKED",
                entity_type="TRIAL", entity_id=trial.id, before=None,
                after={"external_trial_id": external_trial_id,
                       "schedule_definition_id": str(definition.id),
                       "approved_schedule_version_id": str(approved.id)}))
            return {"trial_id": str(trial.id), "schedule_definition_id": str(definition.id),
                    "schedule_version_id": str(approved.id)}

        return self._run(work)

    def enroll_patient(
        self, *, organization_id: UUID, actor_id: UUID, canonical_trial_id: UUID,
        schedule_definition_id: UUID, external_patient_id: str, patient_code: str,
        baseline_date: date, horizon: date, arm_label: str | None = None,
        cohort_label: str | None = None, population_label: str | None = None,
        dimension_values: dict[str, list[str]] | None = None,
        anchor_dates: dict[str, date] | None = None, expected_version_id: UUID | None = None,
        anchor_date_sources: dict[str, str] | None = None,
    ) -> BridgeEnrollment:
        def work(tx: UnitOfWork) -> BridgeEnrollment:
            try:
                trial = tx.authoring.get_trial(organization_id, canonical_trial_id)
            except KeyError as error:
                raise KeyError("canonical trial not found") from error
            header = resolve_current_schedule_version(
                tx, organization_id, trial.id, schedule_definition_id)
            if expected_version_id and header.id != expected_version_id:
                raise ValueError("The approved schedule changed. Reload and preview it again.")
            schedule = tx.schedules.get(header.id)
            dates = enrollment_anchors(schedule, baseline_date, anchor_dates or {})
            existing = tx.patients.find_by_external_id(
                organization_id, trial.id, external_patient_id)
            if existing is not None:
                if existing.current_schedule_version_id != header.id:
                    raise ValueError(
                        "existing linked patient is pinned to another schedule; "
                        "use impact preview/confirm")
                patient = existing
            else:
                arm_id = dimension_id(schedule.arms, arm_label)
                cohort_id = dimension_id(schedule.cohorts, cohort_label)
                population_id = dimension_id(schedule.populations, population_label)
                patient = PatientRecord(
                    organization_id=organization_id, trial_id=trial.id, patient_code=patient_code,
                    external_patient_id=external_patient_id,
                    current_schedule_version_id=header.id, arm_id=arm_id, cohort_id=cohort_id,
                    population_id=population_id,
                    arm_code=next((i.code for i in schedule.arms if i.id == arm_id), None),
                    cohort_code=next((i.code for i in schedule.cohorts if i.id == cohort_id), None),
                    population_code=next(
                        (i.code for i in schedule.populations if i.id == population_id), None),
                    dimension_values=dimension_values or {})
                tx.patients.add(patient)
                tx.impacts.add_assignment(AssignmentRecord(
                    organization_id=organization_id, patient_id=patient.id,
                    schedule_version_id=header.id, assignment_type="ENROLMENT",
                    reason="Operational patient enrolment bridge", assigned_by=actor_id))
                tx.audit.append(AuditRecord(
                    organization_id=organization_id, actor_id=actor_id,
                    action="OPERATIONAL_PATIENT_LINKED", entity_type="PATIENT",
                    entity_id=patient.id, before=None,
                    after={"external_patient_id": external_patient_id,
                           "schedule_version_id": str(header.id)}))
            # Doc s4: the origin is whichever anchor the schedule was BUILT against.
            baseline_anchor = schedule_origin_anchor(schedule)
            if baseline_anchor is None:
                raise ValueError(
                    "the approved schedule does not state which anchor its Day 0 is "
                    "measured from, so a patient cannot be dated against it")

            def current(anchor_code: str, definition_id: UUID):
                rows = [item for item in tx.facts.list_anchors(
                            patient.id, statuses={"CONFIRMED", "PROVISIONAL"})
                        if item.anchor_definition_id == definition_id
                        or item.anchor_code == anchor_code]
                return rows[-1] if rows else None

            current_anchor = current(baseline_anchor.code, baseline_anchor.id)
            if current_anchor is None:
                tx.facts.add_anchor(PatientAnchorRecord(
                    organization_id=organization_id, patient_id=patient.id,
                    anchor_definition_id=baseline_anchor.id, anchor_code=baseline_anchor.code,
                    value_date=baseline_date, status="CONFIRMED",
                    source_type="OPERATIONAL_ENROLMENT",
                    source_reference={"external_patient_id": external_patient_id},
                    recorded_by=actor_id))
            elif current_anchor.value_date != baseline_date:
                raise ValueError(
                    "linked patient's baseline differs; use anchor impact preview/confirm")
            for anchor in schedule.anchors:
                if anchor.id == baseline_anchor.id or anchor.code not in dates:
                    continue
                existing_anchor = current(anchor.code, anchor.id)
                if existing_anchor is not None:
                    if existing_anchor.value_date != dates[anchor.code]:
                        raise ValueError(
                            f"{anchor.code} changed; use anchor impact preview/confirm")
                    continue
                # PT-01: a date the site marked PLANNED is stored as planned.
                planned = str((anchor_date_sources or {}).get(anchor.code, "")).upper() == "PLANNED"
                tx.facts.add_anchor(PatientAnchorRecord(
                    organization_id=organization_id, patient_id=patient.id,
                    anchor_definition_id=anchor.id, anchor_code=anchor.code,
                    value_date=dates[anchor.code], status="CONFIRMED",
                    source_type="PLANNED" if planned else "OPERATIONAL_ENROLMENT",
                    recorded_by=actor_id,
                    source_reference={"external_patient_id": external_patient_id,
                                      "entered_as": "PLANNED" if planned else "ACTUAL"}))
            evaluation, rows = PatientScheduleService(self._uow).evaluate_in(
                tx, patient.id, organization_id=organization_id, horizon=horizon,
                schedule_version_id=header.id,
                idempotency_key=f"operational-enrolment:{external_patient_id}:{header.id}",
                actor_id=actor_id, reason="Operational patient enrolment bridge")
            definitions = {item.code: item for item in schedule.events}
            activity_defs = {
                activity.id: activity for event_def in schedule.events
                for activity in event_def.activities}

            def activities_of(row) -> tuple[BridgeActivity, ...]:
                ordered = sorted(
                    row.activities, key=lambda item: (item.sequence_number is None,
                                                      item.sequence_number or 0))
                return tuple(
                    BridgeActivity(
                        code=item.activity_code,
                        display_name=(activity_defs[item.activity_definition_id].display_name
                                      if item.activity_definition_id in activity_defs
                                      else (item.activity_code or "Activity")),
                        status=item.status, requiredness=item.requiredness,
                        planned_time=item.planned_time, earliest_time=item.earliest_time,
                        latest_time=item.latest_time, actual_time=item.actual_time)
                    for item in ordered)

            events = []
            for row in rows:
                definition = definitions.get(row.event_code)
                details = activities_of(row)
                events.append(BridgeEvent(
                    patient_event_id=row.id, logical_occurrence_id=row.logical_occurrence_id,
                    logical_key=row.logical_key, event_definition_id=row.event_definition_id,
                    event_code=definition.code if definition else row.logical_key.rsplit(":", 1)[0],
                    name=definition.display_name if definition else row.logical_key,
                    event_type=definition.event_type if definition else "VISIT",
                    status=row.status, nominal_date=row.nominal_start_date,
                    earliest_date=row.earliest_date, latest_date=row.latest_date,
                    activities=tuple(item.display_name for item in details
                                     if item.status not in ("NOT_APPLICABLE", "CANCELLED")),
                    activity_details=details))
            return BridgeEnrollment(
                patient_id=patient.id, schedule_version_id=header.id,
                evaluation_id=evaluation.id, events=tuple(events))

        return self._run(work)
