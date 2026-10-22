"""Protocol amendments on the MongoDB UCTSM plane (P6): compare, review the event mapping,
simulate, adopt (now / at a stated point), remain, return.

Registered by ``create_uctsm_mongo_router`` onto the same router, so every route here is
in the authorization manifest and is reached only through the guard:

* the actor and the organization come from authentication, never from the body (every
  request model forbids unknown fields, so ``actor_id`` / ``grants`` are a 422);
* patient routes carry the persisted operational relationship to the patient; trial routes
  carry the caller's relationship to the trial, checked here against the persisted trial;
* ``amendment.adopt`` (no role default, PI-only, never self-grantable) decides adoption;
  a RETURN also needs ``patient.command.high_impact``; both are re-checked on every call,
  replays and deferred execution included.
"""

# No ``from __future__ import annotations``: FastAPI must resolve the local ``Annotated``
# dependency aliases below (same as ``app/api/uctsm_mongo.py``).
from datetime import date
from typing import Annotated, Any, Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.uctsm_shared import _identity
from app.application.amendment_adoption import decision_view
from app.application.patient_boundary import PatientScheduleBoundary
from app.application.amendment_service import AmendmentService
from app.application.authority import PlaneAuthorityError
from app.application.errors import (
    AmendmentBlockedError, AmendmentIntegrityError, DuplicateRecordError,
    IdempotencyConflictError, ScheduleVersionChangedError, StaleRevisionError,
)
from app.authorization.catalog import Capability
from app.authorization.guard import RouteAuthorizer
from app.authorization.policy import authorize
from app.domain.schedule.amendment import AdoptionPoint


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineageIn(_Body):
    schedule_version_id: UUID
    based_on_schedule_version_id: UUID
    reason: str = Field(min_length=3)


class PairIn(_Body):
    from_schedule_version_id: UUID
    to_schedule_version_id: UUID


class RuleIn(_Body):
    source_code: str = Field(min_length=1)
    source_index: int = Field(default=0, ge=0)
    target_code: str = Field(min_length=1)
    target_index: int = Field(default=0, ge=0)


class MappingReviewIn(_Body):
    expected_revision: int = Field(ge=1)
    action: Literal["CONFIRM", "REJECT", "SET_RULES", "REPLACE"]
    reason: str = Field(min_length=3)
    mapping_id: UUID | None = None
    rules: list[RuleIn] | None = None
    mapping_type: Literal["EQUIVALENT", "RENAMED", "SPLIT", "MERGED", "REMOVED", "ADDED"] | None = None
    source_codes: list[str] | None = None
    target_codes: list[str] | None = None


class SimulateIn(_Body):
    direction: Literal["ADOPT", "RETURN"] = "ADOPT"
    to_schedule_version_id: UUID | None = None
    adoption_point: AdoptionPoint
    horizon: date | None = None


class ConfirmIn(_Body):
    decision: Literal["ADOPT", "REMAIN", "RETURN"]
    reason: str = Field(min_length=3)
    future_policy: Literal["SAME_STRUCTURE", "SAME_IMPACT"] = "SAME_STRUCTURE"
    replaces_decision_id: UUID | None = None


class CancelIn(_Body):
    reason: str = Field(min_length=3)


class ManualDeclareIn(_Body):
    schedule_version_id: UUID
    event_code: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    kind: Literal["UNSUPPORTED", "MANUAL_ONLY"]
    reason: str = Field(min_length=3)
    evidence_refs: list[UUID] = Field(min_length=1)
    policy_reference: str = Field(min_length=3)
    accepts_responsibility: Literal[True]


class ManualApproveIn(_Body):
    schedule_version_id: UUID
    event_code: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3)


def refuse(error: Exception) -> HTTPException:
    """Every domain refusal becomes a structured, actionable response."""
    if isinstance(error, AmendmentBlockedError):
        return HTTPException(409, {"code": "AMENDMENT_BLOCKED", "message": str(error),
                                   "blockers": error.blockers})
    if isinstance(error, AmendmentIntegrityError):
        return HTTPException(409, {"code": "AMENDMENT_INTEGRITY", "message": str(error)})
    if isinstance(error, PlaneAuthorityError):
        return HTTPException(409, {"code": error.code, "message": str(error)})
    if isinstance(error, (IdempotencyConflictError, ScheduleVersionChangedError,
                          StaleRevisionError, DuplicateRecordError)):
        return HTTPException(409, {"code": "CONFLICT", "message": str(error)})
    if isinstance(error, KeyError):
        return HTTPException(404, "Not found")
    return HTTPException(422, str(error))


def register_amendment_routes(
    router: APIRouter, *, authorizer: RouteAuthorizer,
    current_user_dependency: Callable[..., Any], uow_dependency: Callable[..., Any],
) -> None:
    User = Annotated[dict[str, Any], Depends(authorizer.scoped_user_plane(current_user_dependency))]
    Uow = Annotated[Any, Depends(uow_dependency)]
    trial_base = "/trials/{trial_id}/amendments"
    base = "/trials/{trial_id}/patients/{external_patient_id}/amendments"

    # --------------------------------------------------------------------- scoping
    async def trial_scope(uow: Any, user: dict, trial_id: UUID, capability: Capability):
        """The trial must exist in the caller's organization and the caller must hold the
        capability against THIS trial (sponsor-side member or assigned staff)."""
        _, org = _identity(user)
        try:
            trial = uow.read(lambda tx: tx.authoring.get_trial(org, trial_id))
        except KeyError as error:
            raise HTTPException(404, "Not found") from error
        outcome = user["_authz"]
        context = await authorizer.trial_context(user, outcome.principal, trial)
        decision = authorize(outcome.principal, capability, context)
        if not decision.allowed:
            raise HTTPException(
                decision.status,
                "You do not have permission to perform this action." if decision.status == 403
                else "Not found",
                headers=({"X-Authz-Capability": decision.capability,
                          "X-Authz-Reason": decision.reason_code} if decision.status == 403 else None))
        return trial

    def patient_of(uow: Any, user: dict, trial_id: UUID, external_patient_id: str):
        _, org = _identity(user)
        patient = uow.read(lambda tx: tx.patients.find_by_external_id(org, trial_id, external_patient_id))
        if patient is None:
            raise HTTPException(404, "Patient not found in the MongoDB patient plane for this trial")
        return patient

    def require(user: dict, capability: Capability) -> None:
        """An additional capability, evaluated against the patient relationship the guard
        already resolved - from the CURRENT principal, never the body."""
        outcome = user["_authz"]
        decision = authorize(outcome.principal, capability, outcome.resource)
        if not decision.allowed:
            raise HTTPException(
                decision.status,
                "You do not have permission to perform this action." if decision.status == 403
                else "Not found",
                headers=({"X-Authz-Capability": decision.capability} if decision.status == 403 else None))

    def pair_of(uow: Any, org: UUID, trial_id: UUID, pair_id: UUID):
        try:
            pair = uow.read(lambda tx: tx.amendments.get_pair(org, pair_id))
        except KeyError as error:
            raise HTTPException(404, "Not found") from error
        if pair.trial_id != trial_id:
            raise HTTPException(404, "Not found")            # another trial's pair
        return pair

    # ------------------------------------------------------------------ trial level
    @router.get(f"{trial_base}/rule-handling")
    async def rule_handling(trial_id: UUID, schedule_version_id: UUID, user: User, uow: Uow):
        from app.application.manual_rules import ManualRuleService
        await trial_scope(uow, user, trial_id, Capability.SCHEDULE_EVIDENCE_READ)
        _, org = _identity(user)
        try:
            return ManualRuleService(uow).view(organization_id=org, trial_id=trial_id,
                                               version_id=schedule_version_id)
        except Exception as error:
            raise refuse(error) from error

    @router.post(f"{trial_base}/rule-handling")
    async def declare_manual(trial_id: UUID, body: ManualDeclareIn, user: User, uow: Uow):
        from app.application.manual_rules import ManualRuleService
        await trial_scope(uow, user, trial_id, Capability.SCHEDULE_CORRECT)
        actor, org = _identity(user)
        try:
            return ManualRuleService(uow).decide(organization_id=org, trial_id=trial_id,
                version_id=body.schedule_version_id, event_code=body.event_code, actor_id=actor,
                expected_revision=body.expected_revision, action="DECLARE", reason=body.reason,
                kind=body.kind, evidence_refs=body.evidence_refs, policy_reference=body.policy_reference)
        except Exception as error:
            raise refuse(error) from error

    @router.post(f"{trial_base}/rule-handling/approve")
    async def approve_manual(trial_id: UUID, body: ManualApproveIn, user: User, uow: Uow):
        from app.application.manual_rules import ManualRuleService
        await trial_scope(uow, user, trial_id, Capability.SCHEDULE_APPROVE)
        actor, org = _identity(user)
        try:
            return ManualRuleService(uow).decide(organization_id=org, trial_id=trial_id,
                version_id=body.schedule_version_id, event_code=body.event_code, actor_id=actor,
                expected_revision=body.expected_revision, action="APPROVE", reason=body.reason)
        except Exception as error:
            raise refuse(error) from error

    @router.get(trial_base)
    async def trial_amendments(trial_id: UUID, user: User, uow: Uow):
        await trial_scope(uow, user, trial_id, Capability.SCHEDULE_READ)
        _, org = _identity(user)
        return AmendmentService(uow).trial_overview(organization_id=org, trial_id=trial_id)

    @router.post(f"{trial_base}/lineage", status_code=201)
    async def declare_lineage(trial_id: UUID, body: LineageIn, user: User, uow: Uow):
        await trial_scope(uow, user, trial_id, Capability.SCHEDULE_AUTHOR)
        actor, org = _identity(user)
        try:
            return AmendmentService(uow).declare_lineage(
                organization_id=org, actor_id=actor, schedule_version_id=body.schedule_version_id,
                based_on_schedule_version_id=body.based_on_schedule_version_id, reason=body.reason,
                expected_trial_id=trial_id)
        except Exception as error:      # noqa: BLE001 - translated, never swallowed
            raise refuse(error) from error

    @router.post(f"{trial_base}/pairs", status_code=201)
    async def prepare_pair(trial_id: UUID, body: PairIn, user: User, uow: Uow):
        await trial_scope(uow, user, trial_id, Capability.SCHEDULE_REVIEW)
        actor, org = _identity(user)
        try:
            view = AmendmentService(uow).prepare_pair(
                organization_id=org, actor_id=actor,
                from_schedule_version_id=body.from_schedule_version_id,
                to_schedule_version_id=body.to_schedule_version_id, expected_trial_id=trial_id)
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error
        return view

    @router.get(f"{trial_base}/pairs/{{pair_id}}")
    async def get_pair(trial_id: UUID, pair_id: UUID, user: User, uow: Uow):
        await trial_scope(uow, user, trial_id, Capability.SCHEDULE_READ)
        _, org = _identity(user)
        pair_of(uow, org, trial_id, pair_id)
        try:
            return AmendmentService(uow).pair_view(organization_id=org, pair_id=pair_id)
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error

    @router.post(f"{trial_base}/pairs/{{pair_id}}/mappings")
    async def review_mapping(trial_id: UUID, pair_id: UUID, body: MappingReviewIn,
                             user: User, uow: Uow):
        await trial_scope(uow, user, trial_id, Capability.SCHEDULE_REVIEW)
        actor, org = _identity(user)
        pair_of(uow, org, trial_id, pair_id)
        try:
            return AmendmentService(uow).review_mapping(
                organization_id=org, actor_id=actor, pair_id=pair_id,
                expected_revision=body.expected_revision, action=body.action, reason=body.reason,
                mapping_id=body.mapping_id,
                rules=[item.model_dump() for item in body.rules] if body.rules is not None else None,
                mapping_type=body.mapping_type, source_codes=body.source_codes,
                target_codes=body.target_codes)
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error

    # ---------------------------------------------------------------- patient level
    @router.get(base)
    def patient_amendments(trial_id: UUID, external_patient_id: str, user: User, uow: Uow):
        patient = patient_of(uow, user, trial_id, external_patient_id)
        _, org = _identity(user)
        try:
            return PatientScheduleBoundary(uow).amendment_status(
                organization_id=org, patient_id=patient.id)
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error

    @router.post(f"{base}/simulate", status_code=201)
    def simulate(trial_id: UUID, external_patient_id: str, body: SimulateIn, user: User, uow: Uow):
        """Read-only: nothing clinical is written (a retained simulation is provenance)."""
        patient = patient_of(uow, user, trial_id, external_patient_id)
        actor, org = _identity(user)
        try:
            return PatientScheduleBoundary(uow).simulate_adoption(
                organization_id=org, actor_id=actor, patient_id=patient.id,
                direction=body.direction, point=body.adoption_point,
                to_schedule_version_id=body.to_schedule_version_id, horizon=body.horizon)
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error

    def simulation_of(uow: Any, org: UUID, patient_id: UUID, simulation_id: UUID):
        try:
            sim = uow.read(lambda tx: tx.amendments.get_simulation(org, simulation_id))
        except KeyError as error:
            raise HTTPException(404, "Not found") from error
        if sim.patient_id != patient_id:
            raise HTTPException(404, "Not found")            # another patient's simulation
        return sim

    @router.post(f"{base}/simulations/{{simulation_id}}/confirm")
    def confirm(
        trial_id: UUID, external_patient_id: str, simulation_id: UUID, body: ConfirmIn,
        user: User, uow: Uow,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1,
                                                       max_length=200)] = None,
    ):
        patient = patient_of(uow, user, trial_id, external_patient_id)
        actor, org = _identity(user)
        simulation_of(uow, org, patient.id, simulation_id)
        # re-checked on EVERY call, replays included
        capabilities = [Capability.AMENDMENT_ADOPT]
        if body.decision == "RETURN":
            capabilities.append(Capability.PATIENT_COMMAND_HIGH_IMPACT)
        for capability in capabilities:
            require(user, capability)
        try:
            return PatientScheduleBoundary(uow).confirm_adoption(
                organization_id=org, actor_id=actor, simulation_id=simulation_id,
                decision=body.decision, reason=body.reason, idempotency_key=idempotency_key,
                future_policy=body.future_policy, replaces_decision_id=body.replaces_decision_id,
                capabilities=tuple(item.value for item in capabilities))
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error

    def decision_of(uow: Any, org: UUID, patient_id: UUID, decision_id: UUID):
        try:
            decision = uow.read(lambda tx: tx.amendments.get_decision(org, decision_id))
        except KeyError as error:
            raise HTTPException(404, "Not found") from error
        if decision.patient_id != patient_id:
            raise HTTPException(404, "Not found")
        return decision

    @router.post(f"{base}/decisions/{{decision_id}}/execute")
    def execute(trial_id: UUID, external_patient_id: str, decision_id: UUID, user: User, uow: Uow):
        """Run a deferred decision whose point has come. Authority is that of THIS caller,
        now: the guard has just re-resolved their grants and relationship."""
        patient = patient_of(uow, user, trial_id, external_patient_id)
        actor, org = _identity(user)
        decision_of(uow, org, patient.id, decision_id)
        try:
            return PatientScheduleBoundary(uow).execute_adoption(
                organization_id=org, actor_id=actor, decision_id=decision_id,
                capabilities=(Capability.AMENDMENT_ADOPT.value,))
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error

    @router.post(f"{base}/decisions/{{decision_id}}/cancel")
    def cancel(trial_id: UUID, external_patient_id: str, decision_id: UUID, body: CancelIn,
               user: User, uow: Uow):
        patient = patient_of(uow, user, trial_id, external_patient_id)
        actor, org = _identity(user)
        decision_of(uow, org, patient.id, decision_id)
        try:
            return PatientScheduleBoundary(uow).cancel_adoption(
                organization_id=org, actor_id=actor, decision_id=decision_id, reason=body.reason)
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error


__all__ = ["register_amendment_routes", "decision_view", "refuse"]
