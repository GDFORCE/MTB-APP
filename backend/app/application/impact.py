"""Human-confirmed, stale-safe schedule amendment workflow on the repository ports.

A behaviour-preserving port of ``app.services.impact_service`` (preview ->
confirm -> cancel). Differences, all deliberate:

* a proposal found stale or expired is marked so AND that is committed before the
  error is reported, in every case (the SQL service relied on the HTTP layer to
  commit only for two of its three stale messages);
* projections are immutable, so "protected" and "status" come from the facts
  recorded against each logical visit, which is also what makes a fact recorded
  between preview and confirm invalidate the proposal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, TypeVar
from uuid import UUID

from pydantic_core import to_jsonable_python

from app.domain.schedule.anchor_status import record_is_actual
from app.domain.schedule.enrolment import validate_assignment
from app.domain.schedule.models import (
    AssignmentFact, ConditionState, EvaluationResult, PatientConditionStatus, ProcessTransition,
)

from .authority import require_command_plane_authority
from .errors import DuplicateRecordError, StaleRevisionError
from .patient_schedule import (
    ASSIGNMENT_FACT_STATE_CODE, PROCESS_TRANSITION_STATE_CODE, PatientScheduleService,
)
from .patients import (
    AssignmentRecord, EvaluationRecord, ImpactProposalRecord, PatientAnchorRecord,
    PatientConditionRecord, PatientEventRecord, PatientRecord, PatientStateRecord,
)
from .ports import UnitOfWork
from .records import AuditRecord, utc_now
from .schedule_diff import compute_schedule_diff

T = TypeVar("T")
_RETRIES = 3
NAMED_DIMENSIONS = ("ARM", "COHORT", "POPULATION")


def _as_date(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class _Refused:
    """A confirmation that must be reported as an error AFTER its state change
    (proposal marked STALE / EXPIRED) has been committed."""

    message: str


class PatientScheduleImpactService:
    terminal_statuses = {"COMPLETED", "MISSED", "CANCELLED"}

    def __init__(self, uow: UnitOfWork):
        self._uow = uow
        self.patient_schedules = PatientScheduleService(uow)

    def _run(self, work: Callable[[UnitOfWork], T]) -> T:
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._uow.run(work)
            except (StaleRevisionError, DuplicateRecordError) as error:
                last = error
        assert last is not None
        raise last

    # ------------------------------------------------------------------ preview
    def preview(
        self, patient_id: UUID, *, organization_id: UUID, target_schedule_version_id: UUID,
        horizon: date, actor_id: UUID, reason: str, expires_in_minutes: int = 30,
        condition_change: PatientConditionStatus | None = None,
        anchor_candidate: PatientAnchorRecord | None = None,
        state_change: dict[str, object] | None = None,
        assignment_change: dict[str, str | None] | None = None,
        assignment_effective_from: date | None = None,
        process_transition: ProcessTransition | None = None,
    ) -> ImpactProposalRecord:
        def work(tx: UnitOfWork) -> ImpactProposalRecord:
            patient = tx.patients.get(organization_id, patient_id)
            require_command_plane_authority(patient)
            source_id = patient.current_schedule_version_id
            if source_id is None:
                raise ValueError("patient is not pinned to an approved schedule")
            self._assert_target(tx, patient, target_schedule_version_id)
            overrides = (
                {condition_change.condition_code: condition_change} if condition_change else None)
            anchor_change = self._anchor_change(tx, patient, anchor_candidate)
            state_override = self._state_override(state_change)
            assignment = self._assignment_change(tx, patient, assignment_change)
            preview_patient = self._with_assignment(tx, patient, assignment)
            if assignment_effective_from is not None and not assignment:
                raise ValueError("an assignment effective date needs an assignment change")
            facts = self._assignment_facts(
                tx, patient, assignment, assignment_effective_from, recorded_at=utc_now(),
                proposal_id=None)
            transition = self._pending_transition(process_transition)
            result = self.patient_schedules.evaluate_result(
                tx, preview_patient, target_schedule_version_id, horizon=horizon,
                condition_overrides=overrides, **self._anchor_override_kwargs(anchor_change),
                state_overrides=state_override,
                process_transition_overrides=[transition] if transition else None,
                assignment_fact_overrides=facts or None,
                # The proposed move is the explicit mapping: the source version's
                # recorded visits carry into the target's evaluation.
                carry_from_versions=[source_id])
            prior = self.patient_schedules.current_events(tx, patient.id, source_id)
            protected = self.patient_schedules.protected_event_keys(tx, patient.id, prior)
            impact = compute_schedule_diff(prior, result, protected)
            if condition_change is not None:
                impact["condition_change"] = condition_change.model_dump(mode="json")
            if anchor_change is not None:
                impact["anchor_change"] = to_jsonable_python(anchor_change)
            if state_change is not None:
                impact["state_change"] = to_jsonable_python(state_change)
            if assignment is not None:
                impact["assignment_change"] = to_jsonable_python(assignment)
            if assignment_effective_from is not None:
                impact["assignment_effective_from"] = assignment_effective_from.isoformat()
            if transition is not None:
                impact["process_transition"] = transition.model_dump(mode="json")
            input_hash = self._fingerprint(
                patient, result, prior, protected, horizon, condition_change, anchor_change,
                state_change, assignment,
                wp3={"assignment_effective_from": impact.get("assignment_effective_from"),
                     "process_transition": _stable_transition(impact.get("process_transition"))})
            proposal = ImpactProposalRecord(
                organization_id=organization_id, patient_id=patient.id,
                from_schedule_version_id=source_id,
                to_schedule_version_id=target_schedule_version_id, horizon=horizon,
                input_hash=input_hash, impact=impact, status="PENDING", reason=reason,
                created_by=actor_id, expires_at=utc_now() + timedelta(minutes=expires_in_minutes))
            tx.impacts.add(proposal)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_SCHEDULE_IMPACT_PREVIEWED", entity_type="SCHEDULE_IMPACT_PROPOSAL",
                entity_id=proposal.id, before={"schedule_version_id": str(source_id)},
                after={"schedule_version_id": str(target_schedule_version_id),
                       "summary": impact["summary"], "input_hash": input_hash},
                reason=reason))
            return proposal

        return self._run(work)

    # ------------------------------------------------------------------ confirm
    def confirm(
        self, proposal_id: UUID, *, organization_id: UUID, actor_id: UUID, reason: str | None = None,
    ) -> tuple[ImpactProposalRecord, EvaluationRecord, list[PatientEventRecord]]:
        outcome = self._run(lambda tx: self._confirm(tx, proposal_id, organization_id, actor_id, reason))
        if isinstance(outcome, _Refused):
            raise ValueError(outcome.message)
        return outcome

    def _confirm(
        self, tx: UnitOfWork, proposal_id: UUID, organization_id: UUID, actor_id: UUID,
        reason: str | None,
    ):
        proposal = tx.impacts.get(proposal_id)
        patient = tx.patients.get(organization_id, proposal.patient_id)
        require_command_plane_authority(patient)
        if proposal.status != "PENDING":
            raise ValueError(f"impact proposal is {proposal.status.lower()}")
        expires_at = proposal.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utc_now():
            tx.impacts.save(proposal.model_copy(update={"status": "EXPIRED"}))
            return _Refused("impact proposal has expired")
        if patient.current_schedule_version_id != proposal.from_schedule_version_id:
            tx.impacts.save(proposal.model_copy(update={"status": "STALE"}))
            return _Refused("patient schedule assignment changed; impact proposal is stale")
        self._assert_target(tx, patient, proposal.to_schedule_version_id)
        impact = proposal.impact or {}
        condition_change = self._proposed_condition(proposal)
        anchor_change = self._proposed_anchor(tx, proposal, patient)
        state_change = impact.get("state_change")
        state_override = self._state_override(state_change)
        assignment = impact.get("assignment_change")
        effective_raw = impact.get("assignment_effective_from")
        assignment_effective_from = date.fromisoformat(effective_raw) if effective_raw else None
        transition_payload = impact.get("process_transition")
        transition = (
            ProcessTransition.model_validate(transition_payload) if transition_payload else None)
        overrides = (
            {condition_change.condition_code: condition_change} if condition_change else None)
        recorded_at = utc_now()
        facts = self._assignment_facts(
            tx, patient, assignment, assignment_effective_from, recorded_at=recorded_at,
            proposal_id=proposal.id)
        result = self.patient_schedules.evaluate_result(
            tx, self._with_assignment(tx, patient, assignment), proposal.to_schedule_version_id,
            horizon=proposal.horizon, condition_overrides=overrides,
            **self._anchor_override_kwargs(anchor_change), state_overrides=state_override,
            process_transition_overrides=[transition] if transition else None,
            assignment_fact_overrides=facts or None,
            carry_from_versions=[proposal.from_schedule_version_id])
        prior = self.patient_schedules.current_events(
            tx, patient.id, proposal.from_schedule_version_id)
        protected = self.patient_schedules.protected_event_keys(tx, patient.id, prior)
        current_hash = self._fingerprint(
            patient, result, prior, protected, proposal.horizon, condition_change, anchor_change,
            state_change, assignment,
            wp3={"assignment_effective_from": effective_raw,
                 "process_transition": _stable_transition(transition_payload)})
        if current_hash != proposal.input_hash:
            tx.impacts.save(proposal.model_copy(update={"status": "STALE"}))
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_SCHEDULE_IMPACT_STALE", entity_type="SCHEDULE_IMPACT_PROPOSAL",
                entity_id=proposal.id, before={"input_hash": proposal.input_hash},
                after={"input_hash": current_hash}))
            return _Refused("patient inputs changed; generate a new impact preview")

        reason_text = reason or proposal.reason
        working = patient
        if assignment:
            before = self._assignment_snapshot(working)
            working = self._set_assignment(tx, working, assignment)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_ASSIGNMENT_CHANGED", entity_type="PATIENT", entity_id=patient.id,
                before=before,
                after={**self._assignment_snapshot(working),
                       "impact_proposal_id": str(proposal.id), "reason": reason_text}))
        for fact in facts:
            # RL-11: dated history is appended, never rewritten.
            self.patient_schedules.record_patient_fact_in(
                tx, patient, state_code=ASSIGNMENT_FACT_STATE_CODE,
                value=fact.model_dump(mode="json"),
                effective_at=fact.effective_from or date(1970, 1, 1), actor_id=actor_id,
                source_reference={"impact_proposal_id": str(proposal.id)})
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_ASSIGNMENT_FACT_RECORDED", entity_type="PATIENT",
                entity_id=patient.id, before=None,
                after={**fact.model_dump(mode="json"), "proposal_id": str(proposal.id)},
                reason=reason_text))
        if transition is not None:
            confirmed = transition.model_copy(update={
                "status": "CONFIRMED", "recorded_at": recorded_at,
                "authority": transition.authority or str(actor_id)})
            self.patient_schedules.record_patient_fact_in(
                tx, patient, state_code=PROCESS_TRANSITION_STATE_CODE,
                value=confirmed.model_dump(mode="json"), effective_at=confirmed.effective_at,
                actor_id=actor_id, source_reference={"impact_proposal_id": str(proposal.id)})
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_PROCESS_TRANSITION_RECORDED", entity_type="PATIENT",
                entity_id=patient.id, before=None,
                after={**confirmed.model_dump(mode="json"), "proposal_id": str(proposal.id)},
                reason=reason_text))
        if condition_change is not None:
            self._persist_condition(
                tx, patient, condition_change, proposal_id=proposal.id,
                organization_id=organization_id, actor_id=actor_id, reason=reason_text)
        if anchor_change is not None:
            candidate = tx.facts.get_anchor(UUID(str(anchor_change["candidate_id"])))
            if candidate.status != "PENDING_CONFIRMATION":
                tx.impacts.save(proposal.model_copy(update={"status": "STALE"}))
                return _Refused("anchor candidate changed; generate a new impact preview")
            tx.facts.confirm_anchor(candidate.id, {
                **(candidate.metadata or {}), "impact_proposal_id": str(proposal.id),
                "confirmed_by": str(actor_id), "confirmed_at": utc_now().isoformat()})
            previous_value = anchor_change.get("previous_value")
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                # A correction and a first recording are different events.
                action=("PATIENT_ANCHOR_CHANGED" if previous_value is not None
                        else "PATIENT_ANCHOR_CONFIRMED"),
                entity_type="PATIENT_ANCHOR", entity_id=candidate.id,
                before={"status": "PENDING_CONFIRMATION", "anchor_code": anchor_change["anchor_code"],
                        "value": str(previous_value) if previous_value is not None else None},
                after={"status": "CONFIRMED", "anchor_code": anchor_change["anchor_code"],
                       "value": str(anchor_change["value"]),
                       "previous_value": str(previous_value) if previous_value is not None else None,
                       "proposal_id": str(proposal.id)},
                reason=reason_text))
        if state_change is not None:
            effective_at = datetime.fromisoformat(str(state_change["effective_at"]))
            tx.facts.append_state(PatientStateRecord(
                organization_id=organization_id, patient_id=patient.id,
                state_code=str(state_change["state_code"]),
                state_value=state_change.get("state_value"), effective_at=effective_at,
                recorded_by=actor_id,
                source_reference={**(state_change.get("source_reference") or {}),
                                  "impact_proposal_id": str(proposal.id)}))
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id, action="PATIENT_STATE_CHANGED",
                entity_type="PATIENT", entity_id=patient.id, before=None,
                after={"state_code": state_change["state_code"],
                       "state_value": state_change.get("state_value"),
                       "effective_at": str(state_change["effective_at"]),
                       "proposal_id": str(proposal.id)},
                reason=reason_text))
        previous_id = working.current_schedule_version_id
        if previous_id != proposal.to_schedule_version_id:
            tx.evaluations.supersede_active_pointers(patient.id)
        working = tx.patients.save(
            working.model_copy(update={"current_schedule_version_id": proposal.to_schedule_version_id}))
        tx.impacts.add_assignment(AssignmentRecord(
            organization_id=organization_id, patient_id=patient.id,
            schedule_version_id=proposal.to_schedule_version_id,
            previous_schedule_version_id=previous_id, impact_proposal_id=proposal.id,
            assignment_type=("AMENDMENT" if previous_id != proposal.to_schedule_version_id
                             else "INPUT_CHANGE"),
            reason=reason_text, assigned_by=actor_id))
        evaluation, events = self.patient_schedules.evaluate_in(
            tx, patient.id, organization_id=organization_id, horizon=proposal.horizon,
            # AD-4d: the target version is passed explicitly.
            schedule_version_id=proposal.to_schedule_version_id,
            idempotency_key=f"impact:{proposal.id}", prior_events_override=prior,
            actor_id=actor_id, reason=reason_text)
        confirmed_proposal = proposal.model_copy(update={
            "status": "CONFIRMED", "confirmed_by": actor_id, "confirmed_at": utc_now()})
        tx.impacts.save(confirmed_proposal)
        tx.audit.append(AuditRecord(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_SCHEDULE_VERSION_ASSIGNED", entity_type="PATIENT", entity_id=patient.id,
            before={"schedule_version_id": str(previous_id)},
            after={"schedule_version_id": str(proposal.to_schedule_version_id),
                   "proposal_id": str(proposal.id), "evaluation_id": str(evaluation.id)},
            reason=reason_text))
        return confirmed_proposal, evaluation, events

    # ------------------------------------------------------------------- cancel
    def cancel(
        self, proposal_id: UUID, *, organization_id: UUID, actor_id: UUID, reason: str,
    ) -> ImpactProposalRecord:
        def work(tx: UnitOfWork) -> ImpactProposalRecord:
            proposal = tx.impacts.get(proposal_id)
            tx.patients.get(organization_id, proposal.patient_id)
            if proposal.status != "PENDING":
                raise ValueError(f"impact proposal is {proposal.status.lower()}")
            cancelled = proposal.model_copy(update={"status": "CANCELLED"})
            tx.impacts.save(cancelled)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_SCHEDULE_IMPACT_CANCELLED", entity_type="SCHEDULE_IMPACT_PROPOSAL",
                entity_id=proposal.id, before={"status": "PENDING"},
                after={"status": cancelled.status}, reason=reason))
            return cancelled

        return self._run(work)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _assert_target(tx: UnitOfWork, patient: PatientRecord, schedule_version_id: UUID) -> None:
        try:
            header = tx.schedules.get_header(schedule_version_id)
        except KeyError:
            header = None
        if header is None or header.status != "APPROVED" or header.trial_id != patient.trial_id:
            raise ValueError("target must be an approved schedule version for the patient's trial")

    @staticmethod
    def _pending_transition(transition: ProcessTransition | None) -> ProcessTransition | None:
        """A proposed transition acts in the preview exactly as it will once confirmed."""
        if transition is None:
            return None
        if transition.status == "CANCELLED":
            raise ValueError("a cancelled transition cannot be proposed")
        return transition.model_copy(update={"status": "CONFIRMED"})

    def _assignment_facts(
        self, tx: UnitOfWork, patient: PatientRecord, assignment: dict[str, object] | None,
        effective_from: date | None, *, recorded_at: datetime, proposal_id: UUID | None,
    ) -> list[AssignmentFact]:
        """RL-11/AT-11: dated assignment facts for a confirmed reassignment."""
        if not assignment or effective_from is None:
            return []
        schedule = tx.schedules.get(patient.current_schedule_version_id)
        current = _current_assignment_codes(patient, schedule)
        context = self.patient_schedules.build_context(
            tx, patient, schedule, patient.current_schedule_version_id)
        history = {item.dimension for item in context.assignment_history}
        tag = str(proposal_id) if proposal_id else "preview"
        facts: list[AssignmentFact] = []
        for dimension, value in sorted(assignment.items()):
            if dimension not in history:
                facts.append(AssignmentFact(
                    id=f"assignment:{patient.id}:{dimension}:baseline:{tag}",
                    dimension=dimension, value=current.get(dimension), effective_from=None,
                    recorded_at=recorded_at, source="ASSIGNMENT_IN_FORCE_BEFORE_DATED_CHANGE"))
            facts.append(AssignmentFact(
                id=f"assignment:{patient.id}:{dimension}:{effective_from.isoformat()}:{tag}",
                dimension=dimension, value=None if value is None else str(value),
                effective_from=effective_from, recorded_at=recorded_at,
                source="IMPACT_CONFIRMATION"))
        return facts

    @staticmethod
    def _proposed_condition(proposal: ImpactProposalRecord) -> PatientConditionStatus | None:
        payload = (proposal.impact or {}).get("condition_change")
        return PatientConditionStatus.model_validate(payload) if payload else None

    def _anchor_change(
        self, tx: UnitOfWork, patient: PatientRecord, candidate: PatientAnchorRecord | None,
    ) -> dict[str, object] | None:
        if candidate is None:
            return None
        schedule = tx.schedules.get(patient.current_schedule_version_id)
        anchor = next((item for item in schedule.anchors
                       if item.id == candidate.anchor_definition_id), None)
        if anchor is None or candidate.patient_id != patient.id:
            raise ValueError("anchor candidate does not belong to the patient's schedule")
        if candidate.status != "PENDING_CONFIRMATION":
            raise ValueError("anchor candidate is not pending confirmation")
        value = candidate.value_datetime or candidate.value_date
        if value is None:
            raise ValueError("anchor candidate has no date")
        selection = str((anchor.derivation_rule or {}).get("selection", "LAST")).upper()
        # Doc 1 s21: an anchor change has to be auditable as a CHANGE.
        previous_rows = [
            item for item in tx.facts.list_anchors(
                patient.id, statuses={"CONFIRMED", "PROVISIONAL"})
            if item.anchor_definition_id == anchor.id]
        previous = previous_rows[-1] if previous_rows else None
        previous_value = (previous.value_datetime or previous.value_date) if previous else None
        return {
            "candidate_id": str(candidate.id), "anchor_code": anchor.code, "value": value,
            "selection": selection, "candidate_status": candidate.status,
            "previous_value": previous_value,
            "effective": not (selection == "FIRST" and previous is not None),
            # Doc C6: once confirmed, will this value count as the real date?
            "planned": not record_is_actual("CONFIRMED", candidate.source_type),
        }

    @staticmethod
    def _anchor_override_kwargs(
        anchor_change: dict[str, object] | None,
    ) -> dict[str, dict[str, object] | None]:
        if not anchor_change or not anchor_change["effective"]:
            return {"anchor_overrides": None}
        override = {anchor_change["anchor_code"]: anchor_change["value"]}
        if anchor_change.get("planned"):
            return {"anchor_overrides": None, "planned_anchor_overrides": override}
        return {"anchor_overrides": override}

    def _proposed_anchor(
        self, tx: UnitOfWork, proposal: ImpactProposalRecord, patient: PatientRecord,
    ) -> dict[str, object] | None:
        payload = (proposal.impact or {}).get("anchor_change")
        if not payload:
            return None
        candidate = tx.facts.get_anchor(UUID(str(payload["candidate_id"])))
        return self._anchor_change(tx, patient, candidate)

    @staticmethod
    def _state_override(
        state_change: dict[str, object] | None,
    ) -> dict[str, tuple[object, datetime]] | None:
        if not state_change:
            return None
        code = str(state_change.get("state_code") or "").strip()
        if not code:
            raise ValueError("state change requires state_code")
        try:
            effective_at = datetime.fromisoformat(str(state_change["effective_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("state change requires an ISO 8601 effective_at") from exc
        return {code: (state_change.get("state_value"), effective_at)}

    @staticmethod
    def _persist_condition(
        tx: UnitOfWork, patient: PatientRecord, change: PatientConditionStatus, *,
        proposal_id: UUID, organization_id: UUID, actor_id: UUID, reason: str | None,
    ) -> PatientConditionRecord:
        """Write the confirmed condition transition, superseding the prior record."""
        previous = [item for item in tx.facts.list_current_conditions(patient.id)
                    if item.condition_code == change.condition_code]
        row = PatientConditionRecord(
            organization_id=organization_id, patient_id=patient.id,
            condition_code=change.condition_code, state=change.state.value,
            occurrence_index=change.occurrence_index,
            occurrence_date=_as_date(change.occurrence_date),
            resolution_date=_as_date(change.resolution_date),
            impact_proposal_id=proposal_id, recorded_by=actor_id, reason=reason)
        tx.facts.add_condition(row)
        for item in previous:
            tx.facts.supersede_condition(item.id, row.id)
        tx.audit.append(AuditRecord(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_CONDITION_STATE_CHANGED", entity_type="PATIENT", entity_id=patient.id,
            before=({"state": previous[-1].state} if previous
                    else {"state": ConditionState.NOT_OCCURRED.value}),
            after={"condition_code": change.condition_code, "state": change.state.value,
                   "occurrence_date": (change.occurrence_date.isoformat()
                                       if change.occurrence_date else None),
                   "resolution_date": (change.resolution_date.isoformat()
                                       if change.resolution_date else None),
                   "proposal_id": str(proposal_id)},
            reason=reason))
        return row

    def _assignment_change(
        self, tx: UnitOfWork, patient: PatientRecord, requested: dict[str, str | None] | None,
    ) -> dict[str, object] | None:
        """Validate a proposed reassignment against the patient's own schedule.

        A value the schedule does not define is rejected rather than stored: an
        unknown group must not silently become "no group" (doc 8 s30).
        """
        if not requested:
            return None
        version_id = patient.current_schedule_version_id
        if version_id is None:
            raise ValueError("patient is not pinned to an approved schedule")
        schedule = tx.schedules.get(version_id)
        changes: dict[str, object] = {key.strip().upper(): value for key, value in requested.items()}
        resulting = {**_current_assignment_codes(patient, schedule), **changes}
        problems = validate_assignment(schedule, resulting)
        if problems:
            raise ValueError("; ".join(problems))
        return changes or None

    def _with_assignment(
        self, tx: UnitOfWork, patient: PatientRecord, assignment: dict[str, object] | None,
    ) -> PatientRecord:
        """A detached copy carrying the proposed assignment - never the live record."""
        if not assignment:
            return patient
        return self._set_assignment(tx, patient.model_copy(deep=True), assignment)

    @staticmethod
    def _set_assignment(
        tx: UnitOfWork, patient: PatientRecord, assignment: dict[str, object],
    ) -> PatientRecord:
        schedule = tx.schedules.get(patient.current_schedule_version_id)
        values = dict(patient.dimension_values or {})
        update: dict[str, object] = {}
        pools = {"ARM": schedule.arms, "COHORT": schedule.cohorts, "POPULATION": schedule.populations}
        for dimension, code in assignment.items():
            if dimension in pools:
                row = None
                if code is not None:
                    row = next((item for item in pools[dimension] if item.code == code), None)
                prefix = dimension.lower()
                update[f"{prefix}_id"] = row.id if row is not None else None
                update[f"{prefix}_code"] = row.code if row is not None else None
            if code is None:
                values.pop(dimension, None)
            else:
                values[dimension] = [str(code)]
        update["dimension_values"] = values
        return patient.model_copy(update=update)

    @staticmethod
    def _assignment_snapshot(patient: PatientRecord) -> dict[str, object]:
        return {
            "arm_id": str(patient.arm_id) if patient.arm_id else None,
            "cohort_id": str(patient.cohort_id) if patient.cohort_id else None,
            "population_id": str(patient.population_id) if patient.population_id else None,
            "dimension_values": to_jsonable_python(patient.dimension_values or {}),
        }

    def _fingerprint(
        self, patient: PatientRecord, result: EvaluationResult, prior: list[PatientEventRecord],
        protected: set[tuple[str, int]], horizon: date,
        condition_change: PatientConditionStatus | None = None,
        anchor_change: dict[str, object] | None = None,
        state_change: dict[str, object] | None = None,
        assignment_change: dict[str, object] | None = None,
        wp3: dict[str, object] | None = None,
    ) -> str:
        payload = {
            "patient_id": str(patient.id),
            "from_schedule_version_id": str(patient.current_schedule_version_id),
            "to_schedule_version_id": str(result.schedule_version_id),
            "horizon": horizon.isoformat(),
            "condition_change": (
                condition_change.model_dump(mode="json") if condition_change else None),
            "anchor_change": to_jsonable_python(anchor_change),
            "state_change": to_jsonable_python(state_change),
            "assignment_change": to_jsonable_python(assignment_change),
            # The assignment the patient is on TODAY is part of the fingerprint:
            # a reassignment between preview and confirm invalidates the proposal.
            "current_assignment": self._assignment_snapshot(patient),
            "input": _without_volatile(result.input_snapshot),
            **({"wp3": wp3} if wp3 and any(wp3.values()) else {}),
            "prior": [{
                "id": str(item.id), "logical_key": item.logical_key,
                "date": item.nominal_start_date.isoformat() if item.nominal_start_date else None,
                "status": item.status,
                "protected": (item.event_code, item.occurrence_index) in protected,
            } for item in prior],
        }
        encoded = json.dumps(to_jsonable_python(payload), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _current_assignment_codes(patient: PatientRecord, schedule) -> dict[str, str]:
    """The codes the patient is assigned to right now, keyed by dimension type."""
    codes: dict[str, str] = {}
    for dimension_type, dimension_id, pool in (
        ("ARM", patient.arm_id, schedule.arms),
        ("COHORT", patient.cohort_id, schedule.cohorts),
        ("POPULATION", patient.population_id, schedule.populations),
    ):
        if dimension_id is None:
            continue
        row = next((item for item in pool if item.id == dimension_id), None)
        if row is not None:
            codes[dimension_type] = row.code
    for dimension_type, values in (patient.dimension_values or {}).items():
        if values:
            codes.setdefault(dimension_type.upper(), str(values[0]))
    return codes


def _stable_transition(payload: dict[str, object] | None) -> dict[str, object] | None:
    """A proposed transition without its (preview-time) recording instant."""
    if not payload:
        return None
    return {key: value for key, value in payload.items() if key != "recorded_at"}


def _without_volatile(snapshot: dict[str, object]) -> dict[str, object]:
    """The input snapshot minus values that differ only by WHEN the preview or
    confirmation was computed."""
    output = dict(snapshot)
    output["process_transitions"] = [
        {key: value for key, value in item.items() if key not in {"recorded_at", "status"}}
        for item in snapshot.get("process_transitions") or []]
    output["assignment_history"] = [
        {key: value for key, value in item.items() if key not in {"recorded_at", "id"}}
        for item in snapshot.get("assignment_history") or []]
    return output
