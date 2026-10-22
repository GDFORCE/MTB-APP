"""Review and approve a draft in its existing Mongo plane; no store mirroring."""
from datetime import date
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.uctsm_shared import _identity, _maker_checker_http
from app.api.uctsm_amendments import refuse
from app.application.conflict_resolution import ConflictResolutionService
from app.application.manual_rules import ManualRuleService
from app.application.required_inputs import RequiredInputsService
from app.application.records import AuditRecord
from app.application.review_projection import build_review_view
from app.application.schedule_review import ScheduleReviewService, _accountability
from app.authorization.catalog import Capability
from app.authorization.maker_checker import MakerCheckerViolation
from app.authorization.policy import authorize


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=2000)
    reviewed_entire_table: Literal[True]
    effective_from: date | None = None


class CustomInputIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=2, max_length=41)
    label: str = Field(min_length=2, max_length=120)
    kind: Literal["DATE", "DATETIME", "TEXT", "NUMBER"]
    reason: str = Field(min_length=3, max_length=2000)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=2000)


class ResolveConflictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chosen_evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    confirmed_reading: str = Field(min_length=3, max_length=2000)
    reason: str = Field(min_length=3, max_length=2000)


def register_authoring_routes(router, *, authorizer, current_user_dependency, uow_dependency):
    User = Annotated[dict, Depends(authorizer.scoped_user_plane(current_user_dependency))]
    Uow = Annotated[object, Depends(uow_dependency)]
    base = "/trials/{trial_id}/schedule-versions/{schedule_version_id}"

    async def scope(user, uow, trial_id, capability):
        _, org = _identity(user)
        try:
            trial = uow.read(lambda tx: tx.authoring.get_trial(org, trial_id))
        except KeyError as error:
            raise HTTPException(404, "Not found") from error
        principal = user["_authz"].principal
        context = await authorizer.trial_context(user, principal, trial)
        decision = authorize(principal, capability, context)
        if not decision.allowed:
            raise HTTPException(decision.status, "Not found" if decision.status == 404
                                else "You do not have permission to perform this action.")

    def snapshot(uow, org, trial, version):
        def read(tx):
            header, schedule = ManualRuleService.scope(tx, org, trial, version)
            run = tx.authoring.get_run(header.extraction_run_id) if header.extraction_run_id else None
            view = build_review_view(schedule,
                version=SimpleNamespace(id=header.id, status=header.status, metadata_json={}),
                run=run, accountability=_accountability(run, version))
            return {"schedule_version_id": str(version), "revision": header.revision,
                    "status": header.status, "content_hash": header.content_hash, "view": view}
        return uow.run(read)

    @router.get(f"{base}/review-view")
    async def review_view(trial_id: UUID, schedule_version_id: UUID, user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.SCHEDULE_EVIDENCE_READ)
        _, org = _identity(user)
        try:
            return snapshot(uow, org, trial_id, schedule_version_id)
        except Exception as error:
            raise refuse(error) from error

    @router.post(f"{base}/approve-table")
    async def approve(trial_id: UUID, schedule_version_id: UUID, body: ApproveIn, user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.SCHEDULE_APPROVE)
        actor, org = _identity(user)
        try:
            ScheduleReviewService(uow).approve_reviewed_table(schedule_version_id,
                reviewer_id=actor, reason=body.reason, effective_from=body.effective_from,
                organization_id=org, trial_id=trial_id, expected_revision=body.expected_revision)
            current = snapshot(uow, org, trial_id, schedule_version_id)
            # Approval alone is not an evidence-read grant. Return a receipt;
            # the separately authorized GET serves the review table.
            return {key: value for key, value in current.items() if key != "view"}
        except MakerCheckerViolation as error:
            # The approval transaction has rolled back. Preserve the refusal in
            # a separate committed transaction, as the SQL compatibility path does.
            def audit(tx):
                ManualRuleService.scope(tx, org, trial_id, schedule_version_id)
                tx.audit.append(AuditRecord(organization_id=org, actor_id=actor,
                    action="SCHEDULE_APPROVAL_DENIED_MAKER_CHECKER", entity_type="SCHEDULE_VERSION",
                    entity_id=schedule_version_id,
                    after=error.outcome.audit(approver_id=str(actor), revision=str(body.expected_revision)),
                    reason=error.outcome.reason_code))
            uow.run(audit)
            raise _maker_checker_http(error) from error
        except Exception as error:
            raise refuse(error) from error

    @router.get(f"{base}/conflicts")
    async def list_conflicts(trial_id: UUID, schedule_version_id: UUID, user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.SCHEDULE_EVIDENCE_READ)
        _, org = _identity(user)
        try:
            return {"conflicts": ConflictResolutionService(uow).list_conflicts(org, trial_id, schedule_version_id)}
        except Exception as error:
            raise refuse(error) from error

    @router.post(f"{base}/conflicts/{{event_id}}/resolve")
    async def resolve_conflict(trial_id: UUID, schedule_version_id: UUID, event_id: UUID,
                               body: ResolveConflictIn, user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.SCHEDULE_CORRECT)
        actor, org = _identity(user)
        try:
            return ConflictResolutionService(uow).resolve_event(
                org, trial_id, schedule_version_id, event_id, reviewer_id=actor,
                chosen_evidence_ids=body.chosen_evidence_ids, confirmed_reading=body.confirmed_reading,
                reason=body.reason)
        except Exception as error:
            raise refuse(error) from error

    @router.get(f"{base}/required-inputs")
    async def required_inputs(trial_id: UUID, schedule_version_id: UUID, user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.SCHEDULE_EVIDENCE_READ)
        _, org = _identity(user)
        try:
            return RequiredInputsService(uow).view(org, trial_id, schedule_version_id)
        except Exception as error:
            raise refuse(error) from error

    @router.post(f"{base}/required-inputs/custom")
    async def add_required_input(trial_id: UUID, schedule_version_id: UUID, body: CustomInputIn, user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.SCHEDULE_CORRECT)
        actor, org = _identity(user)
        try:
            return RequiredInputsService(uow).add_custom(
                org, trial_id, schedule_version_id, reviewer_id=actor, code=body.code, label=body.label,
                kind=body.kind, reason=body.reason)
        except Exception as error:
            raise refuse(error) from error

    @router.post(f"{base}/required-inputs/custom/{{code}}/remove")
    async def remove_required_input(trial_id: UUID, schedule_version_id: UUID, code: str, body: ReasonIn,
                                    user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.SCHEDULE_CORRECT)
        actor, org = _identity(user)
        try:
            return RequiredInputsService(uow).remove_custom(
                org, trial_id, schedule_version_id, reviewer_id=actor, code=code, reason=body.reason)
        except Exception as error:
            raise refuse(error) from error

    @router.post(f"{base}/required-inputs/acknowledge")
    async def acknowledge_required_inputs(trial_id: UUID, schedule_version_id: UUID, body: ReasonIn,
                                          user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.SCHEDULE_REVIEW)
        actor, org = _identity(user)
        try:
            return RequiredInputsService(uow).acknowledge(
                org, trial_id, schedule_version_id, reviewer_id=actor, reason=body.reason)
        except Exception as error:
            raise refuse(error) from error

    @router.get("/trials/{trial_id}/enrolment-inputs")
    async def enrolment_inputs(trial_id: UUID, schedule_definition_id: UUID, user: User, uow: Uow):
        await scope(user, uow, trial_id, Capability.PATIENT_ENROL)
        _, org = _identity(user)
        try:
            return RequiredInputsService(uow).for_enrolment(org, trial_id, schedule_definition_id)
        except Exception as error:
            raise refuse(error) from error
