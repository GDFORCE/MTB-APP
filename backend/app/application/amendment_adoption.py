"""Per-patient amendment adoption on the repository ports (P6-03/04/05).

* ``simulate``  - read-only calculation, frozen as a non-clinical provenance record;
* ``confirm``   - an authenticated decision bound to a simulation: ADOPT now, ADOPT at a
                  stated event / cycle / date (deferred), REMAIN on the current version, or
                  RETURN to the version the last adoption left;
* ``execute``   - run a deferred decision when its point has come, under a fenced lease;
* ``cancel``    - withdraw an open decision.

Every refusal that must leave a trace (a stale or expired simulation, a decision that is
due but no longer applies) is COMMITTED before the error is reported, as in the impact
workflow. A patient has at most one open decision, enforced by the store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, TypeVar
from uuid import UUID, uuid4

from app.domain.schedule.amendment import (
    AdoptionMode, AdoptionPoint, DEFERRED_MODES, canonical_json, sha256_text,
)
from app.domain.schedule.amendment_impact import (
    binding_differences, binding_hash, signature_difference,
)

from .amendment_common import load_version
from .amendment_plan import (
    DEFERRED_VOLATILE, Plan, blocker, build_plan, default_horizon, trigger_state,
    tx_uow_stub,
)
from .amendments import (
    AdoptionDecisionRecord, AmendmentSimulationRecord, DecisionStatus, DecisionType,
    FuturePolicy, OPEN_STATUSES, SimulationStatus,
)
from .errors import (
    AmendmentBlockedError, DuplicateRecordError, IdempotencyConflictError, RecordNotFoundError,
    StaleRevisionError,
)
from .patient_schedule import PatientScheduleService
from .patients import AssignmentRecord
from .ports import UnitOfWork
from .records import AuditRecord, CommandReceiptRecord, OutboxRecord, utc_now

T = TypeVar("T")
_RETRIES = 3
SIMULATION_TTL_MINUTES = 30
LEASE_MINUTES = 5


@dataclass(frozen=True)
class _Refused:
    """A refusal reported as an error AFTER its state change has been committed."""

    message: str
    blockers: list[dict[str, Any]]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def decision_view(record: AdoptionDecisionRecord, *, staff: bool = True) -> dict[str, Any]:
    data = {
        "id": str(record.id), "patient_id": str(record.patient_id), "pair_id": str(record.pair_id),
        "decision_type": record.decision_type.value, "mode": record.mode,
        "adoption_point": record.adoption_point, "status": record.status.value,
        "open": record.is_open, "from_schedule_version_id": str(record.from_schedule_version_id),
        "to_schedule_version_id": str(record.to_schedule_version_id),
        "simulation_id": str(record.simulation_id) if record.simulation_id else None,
        "future_policy": record.future_policy.value, "trigger_state": record.trigger_state,
        "effective_date": record.effective_date.isoformat() if record.effective_date else None,
        "linked_decision_id": str(record.linked_decision_id) if record.linked_decision_id else None,
        "result_evaluation_id": str(record.result_evaluation_id) if record.result_evaluation_id else None,
        "carried_fact_count": record.carried_fact_count, "blockers": record.blockers,
        "attempts": record.attempts, "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None}
    if staff:
        data.update({"reason": record.reason, "actor_id": str(record.actor_id),
                     "capabilities": record.capabilities})
    return data


class AdoptionService:
    def __init__(self, uow: UnitOfWork, *, clock: Callable[[], date] = date.today):
        self._uow = uow
        self._clock = clock

    def _run(self, work: Callable[[UnitOfWork], T]) -> T:
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._uow.run(work)
            except (StaleRevisionError, DuplicateRecordError) as error:
                last = error
        assert last is not None
        raise last

    # ------------------------------------------------------------------ simulate (read-only)
    def simulate(
        self, *, organization_id: UUID, actor_id: UUID, patient_id: UUID, direction: str,
        point: AdoptionPoint, to_schedule_version_id: UUID | None = None,
        horizon: date | None = None, as_of: date | None = None,
    ) -> dict[str, Any]:
        """Nothing clinical is written: no assignment, fact, projection or notification."""
        today = as_of or self._clock()
        use_horizon = horizon or default_horizon(today)
        if direction == DecisionType.RETURN.value and point.mode != AdoptionMode.ADOPT_IMMEDIATELY:
            raise AmendmentBlockedError(
                "a return to the earlier version takes effect now",
                [blocker("RETURN_IS_IMMEDIATE", "adoption_point", "returns are immediate")])

        def work(tx: UnitOfWork) -> dict[str, Any]:
            plan = build_plan(
                tx, organization_id=organization_id, patient_id=patient_id, direction=direction,
                to_schedule_version_id=to_schedule_version_id, point=point, horizon=use_horizon,
                as_of=today)
            now = utc_now()
            record = AmendmentSimulationRecord(
                organization_id=organization_id, trial_id=plan.patient.trial_id,
                patient_id=patient_id, pair_id=plan.pair.id,
                from_schedule_version_id=plan.from_version.id, to_schedule_version_id=plan.to_version.id,
                adoption_point=point.model_dump(mode="json"), as_of=today, horizon=use_horizon,
                bindings=plan.bindings, binding_hash=binding_hash(plan.bindings),
                result=plan.view(), blockers=plan.blockers, created_by=actor_id, created_at=now,
                expires_at=now + timedelta(minutes=SIMULATION_TTL_MINUTES))
            for open_simulation in tx.amendments.list_simulations_for_patient(organization_id, patient_id):
                if open_simulation.status == SimulationStatus.ACTIVE:
                    tx.amendments.set_simulation_status(
                        organization_id, open_simulation.id, SimulationStatus.SUPERSEDED.value,
                        expected={SimulationStatus.ACTIVE.value})
            tx.amendments.add_simulation(record)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_AMENDMENT_SIMULATED", entity_type="PATIENT", entity_id=patient_id,
                before=None,
                after={"simulation_id": str(record.id), "direction": direction,
                       "from": str(plan.from_version.id), "to": str(plan.to_version.id),
                       "summary": plan.summary, "blockers": len(plan.blockers)}))
            return {"simulation_id": str(record.id), "expires_at": record.expires_at.isoformat(),
                    "can_confirm": plan.ok, **plan.view()}

        return self._run(work)

    # ---------------------------------------------------------------------------- confirm
    def confirm(
        self, *, organization_id: UUID, actor_id: UUID, simulation_id: UUID, decision: str,
        reason: str, idempotency_key: str | None = None,
        future_policy: str = FuturePolicy.SAME_STRUCTURE.value,
        replaces_decision_id: UUID | None = None, capabilities: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if not reason or len(reason.strip()) < 3:
            raise AmendmentBlockedError("a reason is required",
                                        [blocker("REASON_REQUIRED", "reason", "state why")])
        try:
            policy = FuturePolicy(future_policy)
            kind = DecisionType(decision)
        except ValueError as error:
            raise AmendmentBlockedError(str(error), [blocker("DECISION_INVALID", "decision", str(error))]) from error
        outcome = self._run(lambda tx: self._confirm(
            tx, organization_id, actor_id, simulation_id, kind, reason.strip(), idempotency_key,
            policy, replaces_decision_id, capabilities))
        if isinstance(outcome, _Refused):
            raise AmendmentBlockedError(outcome.message, outcome.blockers)
        return outcome

    def _confirm(
        self, tx: UnitOfWork, org: UUID, actor_id: UUID, simulation_id: UUID, kind: DecisionType,
        reason: str, key: str | None, policy: FuturePolicy, replaces: UUID | None,
        capabilities: tuple[str, ...],
    ):
        sim = tx.amendments.get_simulation(org, simulation_id)
        point = AdoptionPoint.model_validate(sim.adoption_point)
        direction = str(sim.bindings.get("direction"))
        expected_kind = (DecisionType.RETURN if direction == DecisionType.RETURN.value
                         else DecisionType.REMAIN if point.mode == AdoptionMode.REMAIN_ON_V1
                         else DecisionType.ADOPT)
        if kind != expected_kind:
            return _Refused(
                f"this simulation is for a {expected_kind.value.lower()}, not a {kind.value.lower()}",
                [blocker("DECISION_MISMATCH", "decision", f"expected {expected_kind.value}")])
        idempotency = key or f"simulation:{sim.id}"
        material = _material(sim, point, kind, policy)
        existing = tx.amendments.find_decision_by_idempotency(org, sim.patient_id, idempotency)
        if existing is not None:
            if existing.material_hash != material:
                raise IdempotencyConflictError(
                    "that idempotency key was already used for a different amendment decision")
            return {**decision_view(existing), "replayed": True}
        now = utc_now()
        if sim.status != SimulationStatus.ACTIVE:
            return _Refused(f"the simulation is {sim.status.value.lower()}; run a new one",
                            [blocker("SIMULATION_NOT_ACTIVE", str(sim.id), sim.status.value)])
        if _utc(sim.expires_at) <= now:
            tx.amendments.set_simulation_status(
                org, sim.id, SimulationStatus.EXPIRED.value, expected={SimulationStatus.ACTIVE.value})
            return _Refused("the simulation has expired; run a new one",
                            [blocker("SIMULATION_EXPIRED", str(sim.id), "expired")])
        if sim.blockers:
            return _Refused("the simulation has blockers that must be resolved first", sim.blockers)
        today = self._clock()
        plan = build_plan(
            tx, organization_id=org, patient_id=sim.patient_id, direction=direction,
            to_schedule_version_id=(sim.to_schedule_version_id
                                    if direction == DecisionType.ADOPT.value else None),
            point=point, horizon=sim.horizon, as_of=today)
        changed = binding_differences(sim.bindings, plan.bindings)
        if changed or plan.blockers:
            tx.amendments.set_simulation_status(
                org, sim.id, SimulationStatus.SUPERSEDED.value,
                expected={SimulationStatus.ACTIVE.value})
            problems = [blocker("STALE_INPUT", name, f"{name} changed since the preview")
                        for name in changed] + plan.blockers
            return _Refused("the patient's inputs changed since the preview; run a new one", problems)
        # ---- the single open-decision slot --------------------------------------------------
        if plan.open_decision is not None and kind != DecisionType.REMAIN:
            if replaces != plan.open_decision.id:
                return _Refused(
                    "the patient already has an open amendment decision; cancel it or name it "
                    "as the one this replaces",
                    [blocker("OPEN_DECISION_EXISTS", str(plan.open_decision.id),
                             plan.open_decision.status.value)])
            self._close(tx, plan.open_decision, DecisionStatus.CANCELLED, actor_id,
                        f"replaced by a new decision: {reason}")
        capabilities_list = list(capabilities)
        record = AdoptionDecisionRecord(
            organization_id=org, trial_id=sim.trial_id, patient_id=sim.patient_id, pair_id=sim.pair_id,
            from_schedule_version_id=sim.from_schedule_version_id,
            to_schedule_version_id=sim.to_schedule_version_id, decision_type=kind,
            mode=point.mode.value, adoption_point=sim.adoption_point,
            status=DecisionStatus.EXECUTING, is_open=True, simulation_id=sim.id,
            bindings=sim.bindings, binding_hash=sim.binding_hash, material_hash=material,
            future_policy=policy, reason=reason, actor_id=actor_id, capabilities=capabilities_list,
            idempotency_key=idempotency,
            linked_decision_id=(plan.adoption_decision.id if plan.adoption_decision is not None else None))
        if kind == DecisionType.REMAIN:
            record = record.model_copy(update={
                "status": DecisionStatus.REMAINING, "is_open": False, "completed_at": now})
            tx.amendments.add_decision(record)
            self._consume(tx, org, sim.id, record.id)
            self._audit(tx, record, "PATIENT_AMENDMENT_REMAINING", None,
                        {"stays_on": str(record.from_schedule_version_id)})
            self._receipt(tx, record, material)
            return decision_view(record)
        if point.mode in DEFERRED_MODES:
            record = record.model_copy(update={
                "status": DecisionStatus.WAITING,
                "impact_signature": plan.signature(strict=policy == FuturePolicy.SAME_IMPACT),
                "trigger_state": trigger_state(
                    tx, plan.patient, plan.from_version.schedule, plan.from_version.id, point, today)})
            tx.amendments.add_decision(record)
            self._consume(tx, org, sim.id, record.id)
            self._audit(tx, record, "PATIENT_AMENDMENT_DEFERRED", None,
                        {"point": sim.adoption_point, "future_policy": policy.value})
            self._receipt(tx, record, material)
            return decision_view(record)
        tx.amendments.add_decision(record)
        applied = self._apply(tx, plan, record, actor_id, reason, effective_date=None)
        self._consume(tx, org, sim.id, applied.id)
        self._receipt(tx, applied, material)
        return decision_view(applied)

    # ------------------------------------------------------------------------ apply
    def _apply(
        self, tx: UnitOfWork, plan: Plan, record: AdoptionDecisionRecord, actor_id: UUID,
        reason: str, *, effective_date: date | None,
    ) -> AdoptionDecisionRecord:
        """Move the patient: carried facts, assignment, pointer, evaluation and projections,
        audit and notification intent - all in the caller's one transaction."""
        org, patient = record.organization_id, plan.patient
        to_id, from_id = plan.to_version.id, plan.from_version.id
        returning = plan.direction == DecisionType.RETURN.value
        for row in plan.carried:
            tx.facts.add_occurrence(row)
        tx.evaluations.supersede_active_pointers(patient.id)
        moved = tx.patients.save(patient.model_copy(update={
            "current_schedule_version_id": to_id,
            "arm_id": plan.dimension_ids.get("arm"), "cohort_id": plan.dimension_ids.get("cohort"),
            "population_id": plan.dimension_ids.get("population")}))
        assignment = AssignmentRecord(
            organization_id=org, patient_id=patient.id, schedule_version_id=to_id,
            previous_schedule_version_id=from_id, amendment_decision_id=record.id,
            assignment_type="AMENDMENT_RETURN" if returning else "AMENDMENT_ADOPTION",
            reason=reason, assigned_by=actor_id)
        tx.impacts.add_assignment(assignment)
        service = PatientScheduleService(tx_uow_stub())
        evaluation, _ = service.evaluate_in(
            tx, moved.id, organization_id=org, horizon=plan.horizon, schedule_version_id=to_id,
            idempotency_key=f"amendment:{record.id}", prior_events_override=plan.translated_prior,
            actor_id=actor_id, reason=reason, today=plan.as_of)
        closed = self._close(
            tx, record, DecisionStatus.APPLIED, actor_id, reason,
            effective_date=effective_date, result_evaluation_id=evaluation.id,
            result_assignment_id=assignment.id, carried_fact_count=len(plan.carried))
        self._audit(
            tx, closed, "PATIENT_SCHEDULE_RETURNED" if returning else "PATIENT_SCHEDULE_AMENDED",
            {"schedule_version_id": str(from_id)},
            {"schedule_version_id": str(to_id), "evaluation_id": str(evaluation.id),
             "carried_facts": len(plan.carried), "retained_in_earlier_version": len(plan.retained),
             "effective_date": effective_date.isoformat() if effective_date else None,
             "summary": plan.summary, "binding_hash": record.binding_hash,
             # who decided is the audit actor; who applied it (a deferred decision may be run
             # later by someone else, or by the worker acting for the decider) is recorded too
             "applied_by": str(actor_id)})
        tx.outbox.add(OutboxRecord(
            organization_id=org, aggregate_type="PATIENT", aggregate_id=patient.id,
            event_type="PATIENT_SCHEDULE_RETURNED" if returning else "PATIENT_SCHEDULE_AMENDED",
            payload={"decision_id": str(closed.id), "from": str(from_id), "to": str(to_id),
                     "evaluation_id": str(evaluation.id)},
            dedupe_key=f"amendment-decision:{closed.id}:applied"))
        return closed

    def _close(
        self, tx: UnitOfWork, record: AdoptionDecisionRecord, status: DecisionStatus,
        actor_id: UUID, reason: str, **changes: Any,
    ) -> AdoptionDecisionRecord:
        current = tx.amendments.get_decision(record.organization_id, record.id)
        updates: dict[str, Any] = {
            "status": status, "is_open": status in OPEN_STATUSES, "lease_token": None,
            "lease_expires_at": None, "completed_at": utc_now(), **changes}
        if status == DecisionStatus.CANCELLED:
            tx.audit.append(AuditRecord(
                organization_id=record.organization_id, actor_id=actor_id,
                action="PATIENT_AMENDMENT_DECISION_CANCELLED", entity_type="PATIENT",
                entity_id=record.patient_id, before={"status": current.status.value},
                after={"decision_id": str(record.id)}, reason=reason))
        return tx.amendments.save_decision(
            current.model_copy(update=updates), expected_revision=current.revision)

    @staticmethod
    def _consume(tx: UnitOfWork, org: UUID, simulation_id: UUID, decision_id: UUID) -> None:
        tx.amendments.set_simulation_status(
            org, simulation_id, SimulationStatus.CONSUMED.value,
            expected={SimulationStatus.ACTIVE.value}, consumed_by_decision_id=decision_id)

    @staticmethod
    def _audit(tx: UnitOfWork, record: AdoptionDecisionRecord, action: str,
               before: dict | None, after: dict) -> None:
        tx.audit.append(AuditRecord(
            organization_id=record.organization_id, actor_id=record.actor_id, action=action,
            entity_type="PATIENT", entity_id=record.patient_id, before=before,
            after={"decision_id": str(record.id), "decision_type": record.decision_type.value,
                   "mode": record.mode, **after},
            reason=record.reason, metadata={"capabilities": record.capabilities}))

    @staticmethod
    def _receipt(tx: UnitOfWork, record: AdoptionDecisionRecord, material: str) -> None:
        tx.commands.add_receipt(CommandReceiptRecord(
            organization_id=record.organization_id, patient_id=record.patient_id,
            idempotency_key=record.idempotency_key or f"amendment-decision:{record.id}",
            command_type=f"AMENDMENT_{record.decision_type.value}", payload_hash=material,
            result={"decision_id": str(record.id), "status": record.status.value}))

    # -------------------------------------------------------------- deferred execution
    def execute(
        self, *, organization_id: UUID, actor_id: UUID, decision_id: UUID,
        as_of: date | None = None, capabilities: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Run a deferred decision if its point has come. Two transactions: claim a fenced
        lease (committed), then apply under it. A crash between them leaves an expired
        lease that the next attempt reclaims; a worker that lost its lease cannot commit."""
        today = as_of or self._clock()
        claim = self._run(lambda tx: self._claim(tx, organization_id, decision_id, today))
        if claim["lease_token"] is None:
            return claim["view"]
        outcome = self._run(lambda tx: self._execute_claimed(
            tx, organization_id, actor_id, decision_id, claim["lease_token"], today, capabilities))
        if isinstance(outcome, _Refused):
            raise AmendmentBlockedError(outcome.message, outcome.blockers)
        return outcome

    def _claim(self, tx: UnitOfWork, org: UUID, decision_id: UUID, today: date) -> dict[str, Any]:
        record = tx.amendments.get_decision(org, decision_id)
        view = decision_view(record)
        now = utc_now()
        reclaimable = (record.status == DecisionStatus.EXECUTING and record.lease_expires_at is not None
                       and _utc(record.lease_expires_at) <= now)
        if record.status != DecisionStatus.WAITING and not reclaimable:
            return {"lease_token": None, "view": view}
        patient = tx.patients.get(org, record.patient_id)
        point = AdoptionPoint.model_validate(record.adoption_point)
        if patient.current_schedule_version_id == record.from_schedule_version_id:
            from_version = load_version(tx, org, record.from_schedule_version_id)
            state = trigger_state(tx, patient, from_version.schedule, from_version.id, point, today)
        else:
            state = {"due": True, "effective_date": None, "detail": "the patient moved"}
        if not state["due"]:
            # every look is recorded so the worker rotates past a decision that is not due
            saved = tx.amendments.save_decision(
                record.model_copy(update={"trigger_state": state, "last_checked_at": now}),
                expected_revision=record.revision)
            return {"lease_token": None, "view": decision_view(saved)}
        token = uuid4().hex
        saved = tx.amendments.save_decision(
            record.model_copy(update={
                "status": DecisionStatus.EXECUTING, "lease_token": token,
                "lease_expires_at": now + timedelta(minutes=LEASE_MINUTES),
                "attempts": record.attempts + 1, "trigger_state": state, "blockers": []}),
            expected_revision=record.revision)
        return {"lease_token": token, "view": decision_view(saved)}

    def _execute_claimed(
        self, tx: UnitOfWork, org: UUID, actor_id: UUID, decision_id: UUID, token: str,
        today: date, capabilities: tuple[str, ...],
    ):
        record = tx.amendments.get_decision(org, decision_id)
        if (record.status != DecisionStatus.EXECUTING or record.lease_token != token
                or record.lease_expires_at is None or _utc(record.lease_expires_at) <= utc_now()):
            raise StaleRevisionError("this worker no longer holds the lease on the decision")
        point = AdoptionPoint.model_validate(record.adoption_point)
        patient = tx.patients.get(org, record.patient_id)
        if patient.current_schedule_version_id != record.from_schedule_version_id:
            self._settle(tx, record, DecisionStatus.BLOCKED, [blocker(
                "PATIENT_MOVED", str(patient.id),
                "the patient is no longer on the version this decision was made for")])
            return self._refusal(tx, record)
        # Everything below decides from the inputs read in this transaction; join the patient's
        # input sequence now so a concurrent correction conflicts instead of slipping between
        # the trigger check and the commit (write skew).
        tx.patients.guard_inputs(org, patient.id, patient.input_revision)
        # The claim's trigger snapshot is only a hint: facts may have been corrected since. The
        # approved point is re-resolved from the effective facts inside THIS transaction, and
        # the date used is the one that is true now.
        from_version = load_version(tx, org, record.from_schedule_version_id)
        fresh = trigger_state(tx, patient, from_version.schedule, from_version.id, point, today)
        if not fresh["due"]:
            self._settle(tx, record, DecisionStatus.WAITING, [blocker(
                "TRIGGER_WITHDRAWN", str(record.id),
                "the event this adoption waits for is no longer recorded as having happened; "
                "it stays waiting and nothing was changed")], trigger_state=fresh,
                audit_action="PATIENT_AMENDMENT_TRIGGER_WITHDRAWN",
                audit_extra={"claimed_effective_date": record.trigger_state.get("effective_date")})
            return {**decision_view(tx.amendments.get_decision(org, decision_id)), "executed": False}
        effective = (date.fromisoformat(fresh["effective_date"])
                     if fresh.get("effective_date") else None)
        if effective is not None and effective != (
                date.fromisoformat(record.trigger_state["effective_date"])
                if record.trigger_state.get("effective_date") else None):
            record = tx.amendments.save_decision(
                record.model_copy(update={"trigger_state": fresh}), expected_revision=record.revision)
        try:
            plan = build_plan(
                tx, organization_id=org, patient_id=record.patient_id, direction="ADOPT",
                to_schedule_version_id=record.to_schedule_version_id, point=point,
                horizon=max(date.fromisoformat(str(record.bindings["horizon"])),
                            default_horizon(today)),
                as_of=today, effective_date=effective, at_execution=True)
        except Exception as error:                                   # noqa: BLE001
            self._settle(tx, record, DecisionStatus.BLOCKED, [
                blocker("EXECUTION_PRECONDITION", str(record.id), str(error))])
            return self._refusal(tx, record)
        stable = {key: value for key, value in record.bindings.items() if key not in DEFERRED_VOLATILE}
        now_stable = {key: value for key, value in plan.bindings.items() if key not in DEFERRED_VOLATILE}
        moved = binding_differences(stable, now_stable)
        if moved or plan.blockers:
            # approved inputs (versions, hashes, mapping, authority) changed, or something now
            # blocks it: a person must decide again against what is true now
            self._settle(tx, record, DecisionStatus.NEEDS_RECONFIRMATION, [
                *[blocker("INPUT_CHANGED", name, f"{name} changed since this was approved")
                  for name in moved], *plan.blockers])
            return self._refusal(tx, record)
        approved = record.impact_signature
        current = plan.signature(strict=record.future_policy == FuturePolicy.SAME_IMPACT)
        differing = signature_difference(approved, current)
        if differing:
            self._settle(tx, record, DecisionStatus.NEEDS_RECONFIRMATION, [
                blocker("IMPACT_CHANGED", key, "the affected visits are no longer what was approved")
                for key in differing[:50]])
            return self._refusal(tx, record)
        applied = self._apply(tx, plan, record, actor_id, record.reason, effective_date=effective)
        self._receipt_execution(tx, applied)
        return {**decision_view(applied), "executed": True}

    def _settle(self, tx: UnitOfWork, record: AdoptionDecisionRecord, status: DecisionStatus,
                blockers: list[dict[str, Any]], *, trigger_state: dict[str, Any] | None = None,
                audit_action: str | None = None, audit_extra: dict[str, Any] | None = None) -> None:
        current = tx.amendments.get_decision(record.organization_id, record.id)
        updates: dict[str, Any] = {
            "status": status, "is_open": status in OPEN_STATUSES, "blockers": blockers,
            "lease_token": None, "lease_expires_at": None, "last_checked_at": utc_now(),
            "completed_at": None if status in OPEN_STATUSES else utc_now()}
        if trigger_state is not None:
            updates["trigger_state"] = trigger_state
        tx.amendments.save_decision(current.model_copy(update=updates),
                                    expected_revision=current.revision)
        tx.audit.append(AuditRecord(
            organization_id=record.organization_id, actor_id=record.actor_id,
            action=audit_action or "PATIENT_AMENDMENT_" + status.value, entity_type="PATIENT",
            entity_id=record.patient_id, before=None,
            after={"decision_id": str(record.id), "blockers": [item["code"] for item in blockers],
                   **(audit_extra or {})}))

    @staticmethod
    def _refusal(tx: UnitOfWork, record: AdoptionDecisionRecord) -> _Refused:
        current = tx.amendments.get_decision(record.organization_id, record.id)
        return _Refused(
            "the decision could not be applied: " + ", ".join(
                sorted({item["code"] for item in current.blockers})), current.blockers)

    @staticmethod
    def _receipt_execution(tx: UnitOfWork, record: AdoptionDecisionRecord) -> None:
        tx.commands.add_receipt(CommandReceiptRecord(
            organization_id=record.organization_id, patient_id=record.patient_id,
            idempotency_key=f"amendment-execute:{record.id}",
            command_type="AMENDMENT_EXECUTE", payload_hash=record.material_hash,
            result={"decision_id": str(record.id), "status": record.status.value}))

    def scan_due(self, *, organization_id: UUID, trial_id: UUID | None = None,
                 as_of: date | None = None) -> list[dict[str, Any]]:
        """For a scheduler: try every open deferred decision. A refused one is reported,
        never retried in a loop."""
        decisions = self._uow.read(lambda tx: tx.amendments.list_open_decisions(
            organization_id, trial_id=trial_id))
        out: list[dict[str, Any]] = []
        for item in decisions:
            if item.status not in {DecisionStatus.WAITING, DecisionStatus.EXECUTING}:
                continue
            try:
                out.append(self.execute(
                    organization_id=organization_id, actor_id=item.actor_id,
                    decision_id=item.id, as_of=as_of))
            except AmendmentBlockedError as error:
                out.append({"id": str(item.id), "status": "REFUSED", "blockers": error.blockers})
        return out

    def pending_execution(self, *, limit: int = 100) -> list[AdoptionDecisionRecord]:
        """For the trusted deferred-execution worker: every organization's open decisions
        that are waiting (or running under an EXPIRED lease), least recently checked first."""
        return self._uow.read(lambda tx: tx.amendments.list_pending_execution(limit=limit))

    def mark_checked(self, *, organization_id: UUID, decision_id: UUID) -> None:
        """Record that the worker looked at (and could not finish) a decision, so a failing or
        unauthorised one moves to the back of the rotation instead of blocking the pass."""
        def work(tx: UnitOfWork) -> None:
            record = tx.amendments.get_decision(organization_id, decision_id)
            if record.is_open and record.status == DecisionStatus.WAITING:
                tx.amendments.save_decision(
                    record.model_copy(update={"last_checked_at": utc_now()}),
                    expected_revision=record.revision)

        try:
            self._run(work)
        except StaleRevisionError:
            pass                      # somebody else just changed it; its own write rotated it

    def refuse_deferred(self, *, organization_id: UUID, decision_id: UUID, code: str,
                        detail: str) -> dict[str, Any]:
        """Settle a due decision as BLOCKED without applying it (for example because the person
        who decided no longer holds the right). Committed and audited; nothing moves."""
        def work(tx: UnitOfWork) -> dict[str, Any]:
            record = tx.amendments.get_decision(organization_id, decision_id)
            if record.status not in {DecisionStatus.WAITING, DecisionStatus.EXECUTING}:
                return decision_view(record)
            self._settle(tx, record, DecisionStatus.BLOCKED, [blocker(code, str(record.id), detail)])
            return decision_view(tx.amendments.get_decision(organization_id, decision_id))

        return self._run(work)

    # ----------------------------------------------------------------------- cancel
    def cancel(self, *, organization_id: UUID, actor_id: UUID, decision_id: UUID,
               reason: str) -> dict[str, Any]:
        if not reason or len(reason.strip()) < 3:
            raise AmendmentBlockedError("a reason is required",
                                        [blocker("REASON_REQUIRED", "reason", "state why")])

        def work(tx: UnitOfWork) -> dict[str, Any]:
            record = tx.amendments.get_decision(organization_id, decision_id)
            if record.status not in {DecisionStatus.WAITING, DecisionStatus.NEEDS_RECONFIRMATION}:
                raise AmendmentBlockedError(
                    f"a {record.status.value.lower()} decision cannot be cancelled",
                    [blocker("NOT_CANCELLABLE", str(record.id), record.status.value)])
            return decision_view(self._close(
                tx, record, DecisionStatus.CANCELLED, actor_id, reason.strip()))

        return self._run(work)

    # ------------------------------------------------------------------------- status
    def patient_status(self, *, organization_id: UUID, patient_id: UUID,
                       staff: bool = True) -> dict[str, Any]:
        today = self._clock()

        def read(tx: UnitOfWork) -> dict[str, Any]:
            patient = tx.patients.get(organization_id, patient_id)
            current_id = patient.current_schedule_version_id
            out: dict[str, Any] = {
                "patient_id": str(patient.id), "trial_id": str(patient.trial_id),
                "authority_plane": patient.authority_plane, "current_version": None,
                "available": [], "open_decision": None, "decisions": [], "can_return": False}
            if current_id is None:
                return out
            current = load_version(tx, organization_id, current_id)
            out["current_version"] = current.identity()
            for header in tx.schedules.list_headers(
                    organization_id=organization_id, trial_id=patient.trial_id, status="APPROVED"):
                if header.based_on_schedule_version_id != current_id:
                    continue
                pair = tx.amendments.find_pair(organization_id, current_id, header.id)
                entry: dict[str, Any] = {
                    "schedule_version_id": str(header.id), "version_number": header.version_number,
                    "effective_from": header.effective_from.isoformat() if header.effective_from else None,
                    "approved_at": header.approved_at.isoformat() if header.approved_at else None,
                    "pair_id": str(pair.id) if pair else None, "ready_for_adoption": False,
                    "problems": [], "change_summary": pair.change_summary if pair else None}
                if pair is not None:
                    from .amendment_service import AmendmentService
                    view = AmendmentService(self._uow)._view(tx, organization_id, pair)
                    entry.update(ready_for_adoption=view["ready_for_adoption"], problems=view["problems"])
                out["available"].append(entry)
            history = tx.amendments.list_decisions_for_patient(organization_id, patient_id)
            out["decisions"] = [decision_view(item, staff=staff) for item in history]
            open_decision = next((item for item in history if item.is_open), None)
            if open_decision is not None:
                view = decision_view(open_decision, staff=staff)
                if open_decision.status == DecisionStatus.WAITING:
                    point = AdoptionPoint.model_validate(open_decision.adoption_point)
                    view["trigger_state"] = trigger_state(
                        tx, patient, current.schedule, current.id, point, today)
                out["open_decision"] = view
            last = next((item for item in reversed(history)
                         if item.decision_type == DecisionType.ADOPT
                         and item.status == DecisionStatus.APPLIED
                         and item.to_schedule_version_id == current_id), None)
            out["can_return"] = last is not None
            out["return_of_decision_id"] = str(last.id) if last else None
            return out

        return self._uow.read(read)


def _material(sim: AmendmentSimulationRecord, point: AdoptionPoint, kind: DecisionType,
              policy: FuturePolicy) -> str:
    """The MEANING of a decision: type, versions, point, policy. The reason text and the
    clock are not material (changing them is not a different operation)."""
    return sha256_text(canonical_json({
        "type": kind.value, "patient": str(sim.patient_id),
        "from": str(sim.from_schedule_version_id), "to": str(sim.to_schedule_version_id),
        "point": point.model_dump(mode="json"), "policy": policy.value}))
