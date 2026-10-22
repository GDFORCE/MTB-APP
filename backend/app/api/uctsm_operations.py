"""Operations surface of the Mongo plane: dead-letter replay, pilot cohort cutover, notification preferences.

Each handler is a thin translation to an application service. The route guard decides the capability
(and, for trial/patient routes, the relationship); the services re-check the capability themselves, so a
route that was mis-declared could not widen access.
"""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.uctsm_shared import _identity
from app.application.cohort_cutover import CohortCutoverService, CutoverRefusedError
from app.application.errors import DuplicateRecordError, RecordNotFoundError, StaleRevisionError
from app.application.outbox_delivery import DeadLetterService, ReplayDeniedError
from app.application.reminder_sync import ReminderSyncService


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=2000)


class CohortIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    external_patient_ids: list[str] = Field(min_length=1, max_length=200)


class SnapshotIn(BaseModel):
    model_config = ConfigDict(extra="ignore")      # the CLI report carries more fields (counts, timings) than are used
    status: str
    manifest_hash: str = Field(min_length=64, max_length=64)
    encrypted_file_hash: str = Field(min_length=8, max_length=200)
    created_at: str


class PromoteIn(ReasonIn):
    snapshot: SnapshotIn


class RollbackIn(ReasonIn):
    accept_plane_changes_loss: bool = False


class PreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: Literal["GRANTED", "DECLINED", "WITHDRAWN"]
    timezone: str = Field(min_length=3, max_length=64)
    delivery_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    lead_days: int = Field(ge=0, le=30)
    quiet_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


def refuse(error: Exception) -> HTTPException:
    if isinstance(error, (ReplayDeniedError,)):
        return HTTPException(403, "You do not have permission to perform this action.")
    if isinstance(error, CutoverRefusedError):
        status = 403 if error.code == "CAPABILITY_MISSING" else 409
        return HTTPException(status, {"code": error.code, "details": error.details})
    if isinstance(error, (RecordNotFoundError, KeyError)):
        return HTTPException(404, "Not found")
    if isinstance(error, (StaleRevisionError, DuplicateRecordError)):
        return HTTPException(409, {"code": "CONFLICT", "message": "The record changed; reload and retry."})
    return HTTPException(422, str(error))


def register_operations_routes(router, *, authorizer, current_user_dependency, uow_dependency):
    User = Annotated[dict, Depends(authorizer.scoped_user_plane(current_user_dependency))]
    Uow = Annotated[object, Depends(uow_dependency)]

    def principal(user):
        return user["_authz"].principal

    def own_trial(uow, org, trial_id):
        try:
            uow.read(lambda tx: tx.authoring.get_trial(org, trial_id))
        except KeyError as error:
            raise HTTPException(404, "Not found") from error

    # -- dead letters ---------------------------------------------------------------------
    @router.get("/operations/dead-letters")
    def list_dead_letters(user: User, uow: Uow, limit: int = 100):
        if limit < 1 or limit > 500:
            raise HTTPException(422, "limit must be between 1 and 500")
        _, org = _identity(user)
        try:
            return {"messages": DeadLetterService(uow).list_dead(principal(user), org, limit=limit)}
        except Exception as error:
            raise refuse(error) from error

    @router.post("/operations/dead-letters/{outbox_id}/replay")
    def replay_dead_letter(outbox_id: UUID, body: ReasonIn, user: User, uow: Uow):
        _, org = _identity(user)
        try:
            return DeadLetterService(uow).replay(principal(user), org, outbox_id, reason=body.reason)
        except Exception as error:
            raise refuse(error) from error

    # -- pilot cohorts --------------------------------------------------------------------
    def cohort_view(row):
        return {"id": str(row.id), "trial_id": str(row.trial_id), "name": row.name, "state": row.state,
                "members": [member.external_patient_id for member in row.members],
                "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None}

    @router.post("/trials/{trial_id}/cohorts")
    def create_cohort(trial_id: UUID, body: CohortIn, user: User, uow: Uow):
        _, org = _identity(user)
        own_trial(uow, org, trial_id)
        try:
            return cohort_view(CohortCutoverService(uow).create_shadow(
                principal(user), org, trial_id, body.name, body.external_patient_ids))
        except Exception as error:
            raise refuse(error) from error

    @router.get("/trials/{trial_id}/cohorts/{cohort_id}/readiness")
    def cohort_readiness(trial_id: UUID, cohort_id: UUID, user: User, uow: Uow):
        _, org = _identity(user)
        own_trial(uow, org, trial_id)
        try:
            return CohortCutoverService(uow).readiness(org, cohort_id)
        except Exception as error:
            raise refuse(error) from error

    @router.post("/trials/{trial_id}/cohorts/{cohort_id}/promote")
    def promote_cohort(trial_id: UUID, cohort_id: UUID, body: PromoteIn, user: User, uow: Uow):
        _, org = _identity(user)
        own_trial(uow, org, trial_id)
        try:
            return cohort_view(CohortCutoverService(uow).promote(
                principal(user), org, cohort_id, reason=body.reason, snapshot=body.snapshot.model_dump()))
        except Exception as error:
            raise refuse(error) from error

    @router.post("/trials/{trial_id}/cohorts/{cohort_id}/rollback")
    def rollback_cohort(trial_id: UUID, cohort_id: UUID, body: RollbackIn, user: User, uow: Uow):
        _, org = _identity(user)
        own_trial(uow, org, trial_id)
        try:
            return cohort_view(CohortCutoverService(uow).rollback(
                principal(user), org, cohort_id, reason=body.reason,
                accept_plane_changes_loss=body.accept_plane_changes_loss))
        except Exception as error:
            raise refuse(error) from error

    # -- notification preferences (recorded by staff from the patient's documented choice) --
    def patient_of(uow, org, trial_id, external):
        row = uow.read(lambda tx: tx.patients.find_by_external_id(org, trial_id, external))
        if row is None:
            raise HTTPException(404, "Not found")
        return row

    @router.get("/trials/{trial_id}/patients/{external_patient_id}/notification-preferences")
    def get_preferences(trial_id: UUID, external_patient_id: str, user: User, uow: Uow):
        _, org = _identity(user)
        patient = patient_of(uow, org, trial_id, external_patient_id)
        row = uow.read(lambda tx: tx.reminders.get_preferences(org, patient.id))
        return {"preferences": None if row is None else row.model_dump(mode="json", exclude={"id", "organization_id", "patient_id"})}

    @router.put("/trials/{trial_id}/patients/{external_patient_id}/notification-preferences")
    def put_preferences(trial_id: UUID, external_patient_id: str, body: PreferencesIn, user: User, uow: Uow):
        actor, org = _identity(user)
        patient = patient_of(uow, org, trial_id, external_patient_id)
        try:
            saved = ReminderSyncService(uow).record_preferences(
                org, patient.id, actor_id=actor, consent=body.consent, timezone_name=body.timezone,
                delivery_time=body.delivery_time, lead_days=body.lead_days,
                quiet_start=body.quiet_start, quiet_end=body.quiet_end)
        except Exception as error:
            raise refuse(error) from error
        return {"consent": saved.consent, "revision": saved.revision}
