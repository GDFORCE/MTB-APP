"""Running the parity gate, and the cutover it gates, on the repository ports.

Behaviour-preserving port of ``app.services.parity_service``. The rule is
unchanged: a trial's visit reads move to the engine ONLY when a parity run for
every patient, against the schedule version they are pinned to, matched (or was
explicitly accepted with a reason). Moving back to LEGACY needs no gate.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from app.domain.schedule.identity import event_logical_key
from app.domain.schedule.models import EvaluatedEvent, utc_now
from app.domain.schedule.parity import (
    ParityReport, ParityVerdict, compare_schedules, to_comparable,
)
from app.application.operational_projection import bridge_visit_documents

from .errors import RecordNotFoundError
from .operational_bridge import BridgeEvent
from .patient_schedule import PatientScheduleService
from .patients import ParityRunRecord, PatientRecord
from .ports import UnitOfWork
from .records import AuditRecord

READ_MODE_LEGACY = "LEGACY"
READ_MODE_ENGINE = "ENGINE"

T = TypeVar("T")


def _as_date(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


class ParityService:
    def __init__(self, uow: UnitOfWork):
        self._uow = uow
        self._schedules = PatientScheduleService(uow)

    # --- running the comparison ----------------------------------------------------

    def compare_patient(
        self, patient_id: UUID, *, organization_id: UUID, legacy_visits: list[dict[str, object]],
        horizon: date | None = None, actor_id: UUID | None = None, record: bool = True,
    ) -> tuple[ParityReport, ParityRunRecord | None]:
        """Compare one linked patient's schedule across both systems."""

        def work(tx: UnitOfWork):
            try:
                patient = tx.patients.get(organization_id, patient_id)
            except RecordNotFoundError as error:
                raise KeyError("patient not found") from error
            if patient.current_schedule_version_id is None:
                report = ParityReport(
                    verdict=ParityVerdict.NOT_COMPARABLE,
                    note="the patient is not pinned to an approved schedule version")
                return report, self._record(tx, patient, report, actor_id) if record else None
            # AD-10: a parity CHECK never moves dates; evaluate_result writes nothing.
            result = self._schedules.evaluate_result(
                tx, patient, patient.current_schedule_version_id,
                horizon=horizon or (date.today() + timedelta(days=730)))
            schedule = tx.schedules.get(patient.current_schedule_version_id)
            definitions = {item.id: item for item in schedule.events}
            engine_rows = self._engine_rows(patient, result.events, definitions)
            report = compare_schedules(
                to_comparable(legacy_visits, key_fields=("name",), number_field="visit_number"),
                to_comparable(engine_rows, key_fields=("name",), number_field="visit_number"))
            return report, self._record(tx, patient, report, actor_id) if record else None

        return self._uow.run(work)

    @staticmethod
    def _engine_rows(
        patient: PatientRecord, events: list[EvaluatedEvent], definitions: dict,
    ) -> list[dict[str, object]]:
        bridge_events = []
        for item in events:
            definition = definitions.get(item.event_definition_id)
            if definition is None:
                continue
            bridge_events.append(BridgeEvent(
                patient_event_id=uuid4(), logical_occurrence_id=uuid4(),
                logical_key=event_logical_key(item.event_code, item.occurrence_index),
                event_definition_id=item.event_definition_id, event_code=definition.code,
                name=definition.display_name, event_type=definition.event_type,
                status=item.status.value,
                nominal_date=_as_date(item.timing.nominal_start) if item.timing else None,
                earliest_date=_as_date(item.timing.earliest) if item.timing else None,
                latest_date=_as_date(item.timing.latest) if item.timing else None,
                activities=()))
        return bridge_visit_documents(
            patient_id=str(patient.id), trial_id=str(patient.trial_id),
            schedule_version_id=str(patient.current_schedule_version_id),
            evaluation_id="parity-check", events=bridge_events, generated_at=utc_now())

    @staticmethod
    def _record(
        tx: UnitOfWork, patient: PatientRecord, report: ParityReport, actor_id: UUID | None,
    ) -> ParityRunRecord:
        run = ParityRunRecord(
            organization_id=patient.organization_id, trial_id=patient.trial_id,
            patient_id=patient.id, schedule_version_id=patient.current_schedule_version_id,
            verdict=report.verdict.value, compared=report.compared, matched=report.matched,
            differences=[item.model_dump(mode="json") for item in report.differences],
            note=report.note, ran_by=actor_id)
        tx.parity.add(run)
        return run

    # --- the gate ------------------------------------------------------------------

    def readiness(self, trial_id: UUID, *, organization_id: UUID) -> dict[str, object]:
        return self._uow.read(lambda tx: self._readiness(tx, trial_id, organization_id))

    @staticmethod
    def _readiness(tx: UnitOfWork, trial_id: UUID, organization_id: UUID) -> dict[str, object]:
        """Whether this trial's reads may move to the engine, and why not if not.

        Every patient pinned to a schedule version needs a MATCHING run against
        THAT version, or a DIFFERENCES_FOUND run someone explicitly ACCEPTED.
        """
        try:
            trial = tx.authoring.get_trial(organization_id, trial_id)
        except RecordNotFoundError as error:
            raise KeyError("trial not found") from error
        patients = [item for item in tx.patients.list_for_trial(organization_id, trial_id)
                    if item.current_schedule_version_id is not None]
        latest: dict[tuple[UUID, UUID], ParityRunRecord] = {}
        for run in tx.parity.list_for_trial(organization_id, trial_id):
            if run.patient_id is None or run.schedule_version_id is None:
                continue
            latest[(run.patient_id, run.schedule_version_id)] = run

        unchecked: list[str] = []
        failing: list[dict[str, object]] = []
        accepted: list[dict[str, object]] = []
        for patient in patients:
            run = latest.get((patient.id, patient.current_schedule_version_id))
            if run is None:
                unchecked.append(patient.patient_code)
                continue
            if run.verdict == ParityVerdict.MATCH.value:
                continue
            if run.verdict == ParityVerdict.DIFFERENCES_FOUND.value and run.accepted_at is not None:
                accepted.append({
                    "patient_code": patient.patient_code, "parity_run_id": str(run.id),
                    "accepted_by": str(run.accepted_by) if run.accepted_by else None,
                    "accepted_reason": run.accepted_reason,
                    "accepted_at": run.accepted_at.isoformat()})
                continue
            failing.append({
                "patient_code": patient.patient_code, "parity_run_id": str(run.id),
                "verdict": run.verdict,
                "differences": (run.compared - run.matched
                                if run.verdict == ParityVerdict.DIFFERENCES_FOUND.value else 0)})

        ready = bool(patients) and not unchecked and not failing
        reasons: list[str] = []
        if not patients:
            reasons.append(
                "no patient in this trial is pinned to an approved schedule version, "
                "so there is nothing to compare")
        if unchecked:
            reasons.append(
                f"{len(unchecked)} patient(s) have not been compared against the "
                f"version they are pinned to: {', '.join(sorted(unchecked)[:10])}")
        if failing:
            names = sorted(str(item["patient_code"]) for item in failing)
            reasons.append(
                f"{len(failing)} patient(s) have differences that need a decision - "
                "either fixed and re-checked, or explicitly accepted with a reason: "
                f"{', '.join(names[:10])}")
        return {
            "trial_id": str(trial_id), "read_mode": trial.schedule_read_mode, "ready": ready,
            "patients": len(patients), "checked": len(patients) - len(unchecked),
            "unchecked": sorted(unchecked),
            "with_differences": sorted(failing, key=lambda item: str(item["patient_code"])),
            "accepted": sorted(accepted, key=lambda item: str(item["patient_code"])),
            "reasons": reasons,
        }

    def accept_run(
        self, run_id: UUID, *, organization_id: UUID, actor_id: UUID, reason: str,
    ) -> ParityRunRecord:
        """Sign off one DIFFERENCES_FOUND run so the gate can pass on it (AD-1a)."""
        if not reason or not reason.strip():
            raise ValueError("accepting a parity difference needs a reason")

        def work(tx: UnitOfWork) -> ParityRunRecord:
            try:
                run = tx.parity.get(organization_id, run_id)
            except RecordNotFoundError as error:
                raise KeyError("parity run not found") from error
            if run.verdict != ParityVerdict.DIFFERENCES_FOUND.value:
                raise ValueError(
                    "only a run with differences can be accepted - a MATCH run needs no "
                    "sign-off, and NOT_COMPARABLE means nothing was actually compared")
            accepted = run.model_copy(update={
                "accepted_at": utc_now(), "accepted_by": actor_id,
                "accepted_reason": reason.strip()})
            tx.parity.save(accepted)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PARITY_RUN_ACCEPTED", entity_type="PARITY_RUN", entity_id=run.id,
                before={"accepted": False},
                after={"accepted": True, "reason": accepted.accepted_reason,
                       "verdict": run.verdict}))
            return accepted

        return self._uow.run(work)

    def set_read_mode(
        self, trial_id: UUID, *, organization_id: UUID, mode: str, actor_id: UUID, reason: str,
    ) -> dict[str, object]:
        """Move a trial's visit reads between the operational store and the engine."""
        if mode not in {READ_MODE_LEGACY, READ_MODE_ENGINE}:
            raise ValueError(f"read mode must be {READ_MODE_LEGACY} or {READ_MODE_ENGINE}")
        if not reason or not reason.strip():
            raise ValueError("changing where visit dates are read from needs a reason")

        def work(tx: UnitOfWork) -> dict[str, object]:
            try:
                trial = tx.authoring.get_trial(organization_id, trial_id)
            except RecordNotFoundError as error:
                raise KeyError("trial not found") from error
            if mode == READ_MODE_ENGINE:
                # Same transaction as the write below: a patient enrolled or a run
                # recorded concurrently makes this transaction conflict and retry.
                status = self._readiness(tx, trial_id, organization_id)
                if not status["ready"]:
                    raise ValueError(
                        "parity has not been proven for this trial: "
                        + "; ".join(map(str, status["reasons"])))
            before = trial.schedule_read_mode
            tx.authoring.set_trial_read_mode(organization_id, trial_id, mode)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="OPERATIONAL_READ_MODE_CHANGED", entity_type="TRIAL", entity_id=trial.id,
                before={"schedule_read_mode": before},
                after={"schedule_read_mode": mode, "reason": reason.strip()}))
            return {"trial_id": str(trial_id), "read_mode": mode, "previous": before}

        return self._uow.run(work)


def read_mode_for_trial(uow: UnitOfWork, organization_id: UUID, external_trial_id: str) -> str:
    """Where an operational caller should read this trial's visit dates from.

    Answers LEGACY for anything not linked or not cut over, so a caller that
    forgets to handle a new mode keeps the behaviour it has today.
    """
    if not external_trial_id:
        return READ_MODE_LEGACY
    trial = uow.read(
        lambda tx: tx.authoring.find_trial_by_external_id(organization_id, external_trial_id))
    if trial is None:
        return READ_MODE_LEGACY
    return trial.schedule_read_mode or READ_MODE_LEGACY
