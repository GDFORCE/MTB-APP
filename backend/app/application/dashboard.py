"""PI/CRC dashboard and reminder projection on the repository ports.

Behaviour-preserving ports of ``app.services.dashboard_service`` and
``app.services.reminder_delivery.compute_patient_projection``. The wording rules
of the dashboard are unchanged: it returns ACTIONS (what a person must confirm,
resolve or chase today), never the whole schedule.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from app.domain.schedule.deviation import DeviationType
from app.services.dashboard_service import (
    PRIORITY, UPCOMING_WINDOW_DAYS, ActionCategory, DashboardAction, DashboardResult,
    _humanize, _visit_mode,
)
from app.services.schedule_projection import (
    ProjectedConfinement, ProjectedEvent, ProjectionResult, feasibility_from_explanation, project,
)

from .patient_schedule import PatientScheduleService
from .patients import PatientEventRecord, PatientRecord
from .ports import UnitOfWork
from .read_services import DeviationService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


class DashboardService:
    def __init__(self, uow: UnitOfWork):
        self._uow = uow
        self.deviations = DeviationService(uow)
        self._schedules = PatientScheduleService(uow)

    def actions(
        self, *, organization_id: UUID, trial_id: UUID | None = None, today: date | None = None,
    ) -> DashboardResult:
        today = today or date.today()
        result = DashboardResult(
            trial_id=str(trial_id) if trial_id else None, generated_on=today)
        patients = self._uow.read(
            lambda tx: tx.patients.list_for_organization(organization_id, trial_id=trial_id))
        for patient in patients:
            result.actions.extend(self._for_patient(patient, organization_id, today))
        result.actions.sort(key=lambda item: (item.priority, item.due_date or today))
        return result

    def _for_patient(
        self, patient: PatientRecord, organization_id: UUID, today: date,
    ) -> list[DashboardAction]:
        actions: list[DashboardAction] = []

        def add(category: ActionCategory, message: str, **extra) -> None:
            actions.append(DashboardAction(
                patient_id=str(patient.id), patient_code=patient.patient_code, category=category,
                message=message, priority=PRIORITY[category], **extra))

        def gather(tx: UnitOfWork) -> None:
            # A recorded anchor or condition waiting on a human is the most
            # blocking thing on the board: dependent visits cannot be dated.
            for anchor in tx.facts.list_anchors(patient.id, statuses={"PENDING_CONFIRMATION"}):
                add(ActionCategory.ANCHOR_CONFIRMATION,
                    f"{_humanize(anchor.anchor_code)} recorded - confirm to update dependent visits",
                    target_type="PATIENT_ANCHOR", target_id=str(anchor.id), due_date=anchor.value_date)
            for row in tx.facts.list_current_conditions(patient.id):
                if row.state == "PENDING_CONFIRMATION":
                    add(ActionCategory.CONDITION_CONFIRMATION,
                        f"{_humanize(row.condition_code)} awaiting confirmation",
                        target_type="PATIENT_CONDITION", target_id=str(row.id),
                        due_date=row.occurrence_date)
                elif row.state == "ACTIVE":
                    add(ActionCategory.CONDITION_ACTIVE,
                        f"{_humanize(row.condition_code)} active - resolve when the patient recovers",
                        target_type="PATIENT_CONDITION", target_id=str(row.id),
                        due_date=row.occurrence_date)
            for proposal in tx.impacts.list_for_patient(patient.id, status="PENDING"):
                expires = proposal.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= _utc_now():
                    continue
                add(ActionCategory.IMPACT_CONFIRMATION,
                    "Schedule change previewed - confirm or cancel before it expires",
                    target_type="SCHEDULE_IMPACT_PROPOSAL", target_id=str(proposal.id),
                    due_date=expires.date())
            self._schedule_actions(tx, patient, organization_id, today, add=add)

        self._uow.read(gather)
        return actions

    def _schedule_actions(
        self, tx: UnitOfWork, patient: PatientRecord, organization_id: UUID, today: date, *, add,
    ) -> None:
        """Due, overdue, deviated, and incomplete work - never the whole schedule."""
        version_id = patient.current_schedule_version_id
        pointer = tx.evaluations.get_pointer(patient.id, version_id) if version_id else None
        if pointer is None or pointer.status != "ACTIVE" or pointer.current_evaluation_id is None:
            return
        schedule = tx.schedules.get(version_id)
        definitions = {item.code: item for item in schedule.events}
        horizon = today + timedelta(days=UPCOMING_WINDOW_DAYS)
        completed = {(row.event_code, row.occurrence_index)
                     for row in tx.facts.list_occurrences(patient.id) if row.actual_date is not None}
        events = [
            item for item in self._schedules.current_events(tx, patient.id, version_id)
            if item.status == "RESOLVED" and item.nominal_start_date is not None
            # Bounded on purpose: an open-ended protocol must not put every
            # future cycle on the board (doc 3 section 28).
            and item.nominal_start_date <= horizon]
        events.sort(key=lambda item: (item.nominal_start_date, item.occurrence_index))
        for event in events:
            if (event.event_code, event.occurrence_index) in completed:
                continue
            definition = definitions.get(event.event_code)
            name = definition.display_name if definition else event.event_code
            event_type = definition.event_type if definition else ""
            deadline = event.latest_date or event.nominal_start_date
            if deadline is not None and today > deadline:
                add(ActionCategory.VISIT_OVERDUE, f"{name} was due on {deadline.isoformat()}",
                    target_type="PATIENT_EVENT", target_id=str(event.id), due_date=deadline)
            elif (event.earliest_date or event.nominal_start_date) <= today:
                add(ActionCategory.VISIT_DUE, f"{name} ({_visit_mode(event_type)}) is due",
                    target_type="PATIENT_EVENT", target_id=str(event.id),
                    due_date=event.nominal_start_date)
            elif event.nominal_start_date <= horizon:
                add(ActionCategory.VISIT_DUE, f"{name} ({_visit_mode(event_type)}) is approaching",
                    target_type="PATIENT_EVENT", target_id=str(event.id),
                    due_date=event.nominal_start_date)
            incomplete = self._incomplete_required_activities(event, today)
            if incomplete:
                add(ActionCategory.ACTIVITY_INCOMPLETE,
                    f"{name}: {incomplete} required activity(ies) not recorded",
                    target_type="PATIENT_EVENT", target_id=str(event.id),
                    due_date=event.nominal_start_date)
        try:
            report = self.deviations.report(
                patient.id, organization_id=organization_id, today=today)
        except (KeyError, ValueError):
            return
        for finding in report.deviations():
            if finding.deviation_type == DeviationType.MISSED:
                continue  # already surfaced as overdue
            add(ActionCategory.DEVIATION, f"{finding.display_name}: {finding.reason}",
                target_type="PATIENT_EVENT", due_date=finding.actual_date)

    @staticmethod
    def _incomplete_required_activities(event: PatientEventRecord, today: date) -> int:
        """Count required activities still unrecorded on a visit day that has passed."""
        if event.nominal_start_date is None or event.nominal_start_date > today:
            return 0
        return sum(
            1 for activity in event.activities
            if activity.requiredness == "REQUIRED" and activity.actual_time is None
            and activity.status not in {"NOT_APPLICABLE", "NOT_DONE", "COMPLETED"})


def compute_patient_projection(
    uow: UnitOfWork, patient: PatientRecord, *, today: date | None = None,
) -> ProjectionResult:
    """The projection ``GET /patients/{id}/schedule/projection`` exposes, factored
    out so the reminder worker and that endpoint cannot disagree."""
    service = PatientScheduleService(uow)

    def work(tx: UnitOfWork) -> ProjectionResult:
        version_id = patient.current_schedule_version_id
        pointer = tx.evaluations.get_pointer(patient.id, version_id) if version_id else None
        if pointer is None or pointer.status != "ACTIVE" or pointer.current_evaluation_id is None:
            return ProjectionResult()
        schedule = tx.schedules.get(version_id)
        definitions = {item.code: item for item in schedule.events}
        projected = []
        for event in tx.evaluations.list_events(pointer.current_evaluation_id):
            definition = definitions.get(event.event_code)
            event_type = definition.event_type if definition else ""
            # WP2/WP3: the occurrence's persisted feasibility, so a disjoint or
            # incomplete constraint set is never flattened into one window.
            feasibility, windows = feasibility_from_explanation(event.generation_reason)
            projected.append(ProjectedEvent(
                logical_key=event.logical_key, event_code=event.event_code, event_type=event_type,
                visit_mode=definition.visit_mode if definition else None,
                display_name=definition.display_name if definition else event.event_code,
                status=event.status, nominal_date=event.nominal_start_date,
                earliest_date=event.earliest_date, latest_date=event.latest_date,
                is_unscheduled=str(event_type).upper() == "UNSCHEDULED",
                feasibility=feasibility, feasible_windows=windows))
        confinements: list[ProjectedConfinement] = []
        result = service.evaluate_result(
            tx, patient, version_id, horizon=(today or date.today()) + timedelta(days=365))
        for episode in result.confinements:
            definition = next(
                (item for item in schedule.confinement_episodes if item.code == episode.episode_code),
                None)
            members = ([definition.admission_event_code, *definition.dose_event_codes,
                        definition.discharge_event_code] if definition else [])
            confinements.append(ProjectedConfinement(
                episode_code=episode.episode_code, display_name=episode.display_name,
                status=episode.status.value,
                start_date=_as_date(episode.actual_admission or episode.planned_admission),
                end_date=_as_date(episode.actual_discharge or episode.planned_discharge),
                member_event_codes=members))
        return project(projected, today=today or date.today(), confinements=confinements)

    return uow.read(work)
