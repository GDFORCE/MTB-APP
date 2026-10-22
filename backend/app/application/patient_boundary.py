"""The one typed application boundary for a patient's schedule (PDF P12-04).

Every reader and writer of patient schedule data on the repository-port plane - HTTP adapters,
workers, jobs, legacy adapters - goes through ``PatientScheduleBoundary``. It owns no logic of
its own: each method forwards, with an explicit signature, to the focused service that does
(evaluation and occurrences: ``PatientScheduleService``; typed commands:
``PatientCommandService``; amendments: ``AdoptionService``; committed projection:
``ProjectionSnapshotService``). Authority, capability and idempotency are therefore decided in
exactly the places they already are (``authority.py``, the authorization guard, the services'
scoped idempotency); nothing here can bypass them, and an adapter that imports only this
module cannot write generated schedule data directly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable
from uuid import UUID

from .amendment_adoption import AdoptionService
from .patient_commands import CommandImpact, CommandOutcome, PatientCommandService
from .patient_schedule import PatientScheduleService
from .patients import CalendarEntry, PatientEventRecord
from .ports import UnitOfWork
from .projection_snapshot import ProjectionSnapshotService


class PatientScheduleBoundary:
    def __init__(self, uow: UnitOfWork, *, clock: Callable[[], date] = date.today):
        self._uow = uow
        self._schedules = PatientScheduleService(uow)
        self._commands = PatientCommandService(uow)
        self._amendments = AdoptionService(uow, clock=clock)
        self._projections = ProjectionSnapshotService(uow)

    # ----------------------------------------------------------------------- queries
    def calendar(self, patient_id: UUID, *, organization_id: UUID) -> list[CalendarEntry]:
        return self._schedules.current_calendar(patient_id, organization_id=organization_id)

    def current_events(self, patient_id: UUID, schedule_version_id: UUID) -> list[PatientEventRecord]:
        return self._uow.read(
            lambda tx: self._schedules.current_events(tx, patient_id, schedule_version_id))

    def projection(self, *, organization_id: UUID, trial_id: UUID,
                   external_patient_id: str) -> dict[str, Any]:
        return self._projections.get(
            organization_id=organization_id, trial_id=trial_id,
            external_patient_id=external_patient_id)

    def command_history(self, patient_id: UUID, *, organization_id: UUID,
                        profile: str = "STAFF") -> list[dict[str, Any]]:
        return self._commands.ledger(patient_id, organization_id=organization_id, profile=profile)

    def amendment_status(self, *, organization_id: UUID, patient_id: UUID) -> dict[str, Any]:
        return self._amendments.patient_status(organization_id=organization_id, patient_id=patient_id)

    # ------------------------------------------------------------------- evaluation
    def evaluate(self, patient_id: UUID, *, organization_id: UUID, horizon: date,
                 schedule_version_id: UUID | None = None, idempotency_key: str | None = None,
                 actor_id: UUID | None = None, reason: str | None = None,
                 today: date | None = None):
        return self._schedules.evaluate(
            patient_id, organization_id=organization_id, horizon=horizon,
            schedule_version_id=schedule_version_id, idempotency_key=idempotency_key,
            actor_id=actor_id, reason=reason, today=today)

    # ------------------------------------------------------------------- occurrences
    def record_occurrence(self, patient_id: UUID, **kwargs: Any):
        """Actual visit / treatment / missed fact (see ``record_actual_occurrence``)."""
        return self._schedules.record_actual_occurrence(patient_id, **kwargs)

    def record_unscheduled_visit(self, patient_id: UUID, *, organization_id: UUID, event_code: str,
                                 occurred_on: date, reason: str, visit_mode: str | None = None,
                                 actor_id: UUID | None = None):
        return self._schedules.create_unscheduled_visit(
            patient_id, organization_id=organization_id, event_code=event_code,
            occurred_on=occurred_on, reason=reason, visit_mode=visit_mode, actor_id=actor_id)

    def record_activity(self, patient_id: UUID, *, organization_id: UUID, event_code: str,
                        occurrence_index: int, activity_code: str, status: str,
                        actual_time: datetime | None, actor_id: UUID | None,
                        reason: str | None = None):
        return self._schedules.record_activity(
            patient_id, organization_id=organization_id, event_code=event_code,
            occurrence_index=occurrence_index, activity_code=activity_code, status=status,
            actual_time=actual_time, actor_id=actor_id, reason=reason)

    # ----------------------------------------------------------------------- commands
    def preview_command(self, patient_id: UUID, **kwargs: Any) -> CommandImpact:
        return self._commands.preview(patient_id, **kwargs)

    def confirm_command(self, proposal_id: UUID, **kwargs: Any) -> CommandOutcome:
        return self._commands.confirm(proposal_id, **kwargs)

    def cancel_command(self, proposal_id: UUID, *, organization_id: UUID, actor_id: UUID, reason: str):
        return self._commands.cancel(
            proposal_id, organization_id=organization_id, actor_id=actor_id, reason=reason)

    # --------------------------------------------------------------------- amendments
    def simulate_adoption(self, **kwargs: Any) -> dict[str, Any]:
        return self._amendments.simulate(**kwargs)

    def confirm_adoption(self, **kwargs: Any) -> dict[str, Any]:
        return self._amendments.confirm(**kwargs)

    def execute_adoption(self, **kwargs: Any) -> dict[str, Any]:
        return self._amendments.execute(**kwargs)

    def cancel_adoption(self, **kwargs: Any) -> dict[str, Any]:
        return self._amendments.cancel(**kwargs)
