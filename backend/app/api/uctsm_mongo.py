"""P1-04: actual-occurrence recording on the MongoDB UCTSM patient plane.

This is NOT a replacement for the SQL ``/api/uctsm/patients/{id}/occurrence-facts``
route and it does not change which store is authoritative for any patient. It is
the HTTP entry to ``app.application.patient_schedule.PatientScheduleService`` for
patients that exist in the Mongo patient plane (created by the port's own
enrolment, ``app.application.operational_bridge``).

* Explicit configuration: the plane is reached only when ``UCTSM_MONGO_URL`` and
  ``UCTSM_MONGO_DB`` are set. Otherwise every route answers 503 - there is no
  fallback to SQL and no silent no-op.
* Identity is resolved from persisted records, never created: the caller names the
  canonical trial and the operational (legacy) patient id; the patient is found
  by ``external_patient_id`` within the caller's organization. An unknown patient
  is a 404; nothing is enrolled, pinned or assigned here.
* One command, one transaction: the route only translates HTTP to the existing
  ``record_actual_occurrence`` command and its errors back to status codes.
"""

# No ``from __future__ import annotations``: FastAPI must resolve the local
# ``Annotated`` dependency aliases below (same as ``app/api/uctsm.py``).
import threading
from datetime import date
from typing import Annotated, Any, Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.uctsm import _identity
from app.authorization.guard import RouteAuthorizer
from app.application.errors import (
    IdempotencyConflictError, ScheduleVersionChangedError, StaleRevisionError,
)
from app.application.authority import PlaneAuthorityError
from app.application.patient_commands import (
    CommandImpact, CommandOutcome, CommandRefusedError, PatientCommandService,
)
from app.application.patient_schedule import PatientScheduleService
from app.authorization.catalog import Capability
from app.authorization.policy import ResourceContext
from app.domain.schedule.commands import CommandPayload, CommandType, FACT_COMMANDS
from app.application.patients import CalendarEntry, OccurrenceOutcome
from app.domain.schedule.models import OccurrenceFactKind
from app.persistence.mongodb.client import build_client, get_database
from app.persistence.mongodb.settings import MongoSettings, MongoSettingsError
from app.persistence.mongodb.unit_of_work import MongoUnitOfWork

_lock = threading.Lock()
_unit_of_work: MongoUnitOfWork | None = None


def get_plane_uow() -> MongoUnitOfWork:
    """The configured Mongo patient-plane unit of work (503 when not configured)."""
    global _unit_of_work
    with _lock:
        if _unit_of_work is None:
            try:
                settings = MongoSettings.from_env()
            except MongoSettingsError as error:
                raise HTTPException(
                    503, f"The MongoDB patient plane is not configured: {error}") from error
            client = build_client(settings)
            _unit_of_work = MongoUnitOfWork(client, get_database(client, settings))
        return _unit_of_work


class ActualOccurrenceIn(BaseModel):
    """One performed / missed / booked fact for a logical occurrence.

    ``visit_date`` and ``treatment_date`` are separate facts: attending with
    treatment withheld is a visit date and no treatment date.
    """

    model_config = ConfigDict(extra="forbid")
    logical_key: str = Field(min_length=1, description="event code and index, e.g. V2:0")
    kind: Literal["PERFORMED", "MISSED", "BOOKED"]
    visit_date: date | None = None
    treatment_date: date | None = None
    effective_at: date | None = None
    supersedes_occurrence_id: UUID | None = None
    expected_schedule_version_id: UUID | None = None
    horizon: date | None = None
    reason: str = Field(min_length=1)


class PendingAnchorCandidate(BaseModel):
    id: UUID
    anchor_code: str
    value_date: date | None = None
    source_type: str | None = None
    status: str


class ActualOccurrenceOut(BaseModel):
    patient_id: UUID
    trial_id: UUID
    schedule_version_id: UUID | None
    outcome: OccurrenceOutcome
    calendar: list[CalendarEntry]
    #: Candidates still awaiting a person's confirmation. Recording an occurrence
    #: never confirms one.
    pending_anchor_candidates: list[PendingAnchorCandidate]


class CalendarOut(BaseModel):
    patient_id: UUID
    trial_id: UUID
    #: which store is authoritative for this patient's schedule (None = unclassified)
    authority_plane: str | None = None
    schedule_version_id: UUID | None
    calendar: list[CalendarEntry]
    pending_anchor_candidates: list[PendingAnchorCandidate]


def create_uctsm_mongo_router(
    current_user_dependency: Callable[..., Any],
    uow_dependency: Callable[..., Any] = get_plane_uow,
    *, authorizer: RouteAuthorizer,
    operational_provisioner: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None,
) -> APIRouter:
    """``operational_provisioner(user, description)`` (async) records the synthetic
    pilot's operational relationship documents in the SAME store the production
    relationship provider reads, so a seeded pilot is reachable through the normal
    staff relationship rules - never through a mode-based access bypass."""
    router = APIRouter(prefix="/api/uctsm/mongo-plane", tags=["universal-schedule-mongo"])

    def locate_owner(trial_id: UUID, external_patient_id: str):
        """Persisted canonical owner of the plane patient (R2)."""
        try:
            uow = uow_dependency()
        except HTTPException:
            return None
        record = uow.read(lambda tx: tx.patients.find_owner_by_external_id(
            trial_id, external_patient_id))
        return record.organization_id if record is not None else None

    authorizer.plane_locator = locate_owner
    # Capability + the caller's relationship to the operational patient are
    # checked by ``authorizer`` before the unit of work is even requested.
    User = Annotated[dict[str, Any], Depends(authorizer.scoped_user_plane(current_user_dependency))]
    Uow = Annotated[Any, Depends(uow_dependency)]
    base = "/trials/{trial_id}/patients/{external_patient_id}"

    def resolve(uow: Any, organization_id: UUID, trial_id: UUID, external_patient_id: str):
        patient = uow.read(lambda tx: tx.patients.find_by_external_id(
            organization_id, trial_id, external_patient_id))
        if patient is None:
            # Absent in this organization's plane - never created here.
            raise HTTPException(
                404, "Patient not found in the MongoDB patient plane for this trial")
        return patient

    def pending(uow: Any, patient_id: UUID) -> list[PendingAnchorCandidate]:
        rows = uow.read(lambda tx: tx.facts.list_anchors(
            patient_id, statuses={"PENDING_CONFIRMATION"}))
        return [PendingAnchorCandidate(
            id=row.id, anchor_code=row.anchor_code, value_date=row.value_date,
            source_type=row.source_type, status=row.status) for row in rows]

    @router.post(f"{base}/actual-occurrences", status_code=201, response_model=ActualOccurrenceOut)
    def record_actual_occurrence(
        trial_id: UUID, external_patient_id: str, body: ActualOccurrenceIn, user: User, uow: Uow,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1,
                                                       max_length=200)] = None,
    ):
        actor_id, organization_id = _identity(user)
        patient = resolve(uow, organization_id, trial_id, external_patient_id)
        service = PatientScheduleService(uow)
        try:
            outcome = service.record_actual_occurrence(
                patient.id, organization_id=organization_id, logical_key=body.logical_key,
                kind=OccurrenceFactKind(body.kind), visit_date=body.visit_date,
                treatment_date=body.treatment_date, effective_at=body.effective_at,
                supersedes_occurrence_id=body.supersedes_occurrence_id, actor_id=actor_id,
                reason=body.reason, idempotency_key=idempotency_key, horizon=body.horizon,
                expected_schedule_version_id=body.expected_schedule_version_id)
        except PlaneAuthorityError as error:
            raise HTTPException(409, {"code": error.code, "message": str(error)}) from error
        except KeyError as error:                       # also RecordNotFoundError
            raise HTTPException(404, str(error.args[0] if error.args else error)) from error
        except (IdempotencyConflictError, ScheduleVersionChangedError, StaleRevisionError) as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        calendar = for_role(
            service.current_calendar(patient.id, organization_id=organization_id), user)
        fresh = uow.read(lambda tx: tx.patients.get(organization_id, patient.id))
        return ActualOccurrenceOut(
            patient_id=patient.id, trial_id=trial_id,
            schedule_version_id=fresh.current_schedule_version_id, outcome=outcome,
            calendar=calendar, pending_anchor_candidates=pending(uow, patient.id))

    # ---------------------------------------------------------------- P5 commands
    def needed_capability(command_type: str) -> Capability:
        if command_type in {"RECORD_ACTUAL", "MARK_MISSED", "RESCHEDULE_APPOINTMENT"}:
            return Capability.PATIENT_OCCURRENCE_RECORD
        if command_type == "CREATE_UNSCHEDULED":
            return Capability.PATIENT_UNSCHEDULED_CREATE
        return Capability.PATIENT_COMMAND_HIGH_IMPACT

    def refuse(error: Exception) -> HTTPException:
        if isinstance(error, CommandRefusedError):
            return HTTPException(error.status, {
                "code": error.code, "message": str(error), "problems": error.problems})
        if isinstance(error, (IdempotencyConflictError, ScheduleVersionChangedError,
                              StaleRevisionError)):
            return HTTPException(409, {"code": "CONFLICT", "message": str(error)})
        if isinstance(error, KeyError):
            return HTTPException(404, str(error.args[0] if error.args else error))
        return HTTPException(422, str(error))

    @router.post(f"{base}/commands/preview", response_model=CommandImpact)
    def preview_command(trial_id: UUID, external_patient_id: str, body: dict[str, Any],
                        user: User, uow: Uow):
        """Calculate what a command would do. Nothing clinical is written."""
        from pydantic import TypeAdapter, ValidationError

        actor_id, organization_id = _identity(user)
        patient = resolve(uow, organization_id, trial_id, external_patient_id)
        envelope = {key: body.get(key) for key in ("effective_at", "reason", "authority", "horizon")}
        payload_data = {key: value for key, value in body.items()
                        if key not in envelope and key != "actor_id"}
        if "actor_id" in body or "grants" in body or "capability" in body:
            raise HTTPException(422, "actor and permissions come from the signed-in user")
        if payload_data.get("type") == "OVERRIDE_OCCURRENCE" and envelope["authority"]:
            payload_data.setdefault("authority", envelope["authority"])
        try:
            payload = TypeAdapter(CommandPayload).validate_python(payload_data)
            effective_at = date.fromisoformat(str(envelope["effective_at"]))
            horizon = date.fromisoformat(str(envelope["horizon"])) if envelope["horizon"] else None
        except (ValidationError, TypeError, ValueError) as error:
            raise HTTPException(422, f"invalid command: {error}") from error
        try:
            result = PatientCommandService(uow).preview(
                patient.id, organization_id=organization_id, actor_id=actor_id, payload=payload,
                effective_at=effective_at, reason=str(envelope["reason"] or ""),
                authority=envelope["authority"], horizon=horizon)
            if not is_staff(user):
                result = result.model_copy(update={"changes": strip_private(result.changes)})
            return result
        except Exception as error:      # noqa: BLE001 - translated, never swallowed
            raise refuse(error) from error

    def authorise_proposal(uow: Any, user: dict, organization_id: UUID, proposal_id: UUID,
                           patient_id: UUID) -> None:
        from app.authorization.policy import authorize

        try:
            proposal = uow.read(lambda tx: tx.impacts.get(proposal_id))
        except Exception as error:      # noqa: BLE001
            raise HTTPException(404, "Not found") from error
        if proposal.patient_id != patient_id or proposal.organization_id != organization_id:
            raise HTTPException(404, "Not found")        # another patient's proposal
        kind = ((proposal.impact or {}).get("command") or {}).get("type", "")
        outcome = user["_authz"]
        decision = authorize(outcome.principal, needed_capability(kind), outcome.resource)
        if not decision.allowed:
            raise HTTPException(
                403, "You do not have permission to perform this action.",
                headers={"X-Authz-Capability": decision.capability})

    class ConfirmIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        reason: str | None = None

    @router.post(f"{base}/commands/{{proposal_id}}/confirm", response_model=CommandOutcome)
    def confirm_command(
        trial_id: UUID, external_patient_id: str, proposal_id: UUID, user: User, uow: Uow,
        body: ConfirmIn | None = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1,
                                                       max_length=200)] = None,
    ):
        actor_id, organization_id = _identity(user)
        patient = resolve(uow, organization_id, trial_id, external_patient_id)
        # re-checked on EVERY confirm, replays included
        authorise_proposal(uow, user, organization_id, proposal_id, patient.id)
        try:
            outcome = PatientCommandService(uow).confirm(
                proposal_id, organization_id=organization_id, actor_id=actor_id,
                idempotency_key=idempotency_key, reason=body.reason if body else None)
            if not is_staff(user):
                outcome = outcome.model_copy(update={"changes": strip_private(outcome.changes)})
            return outcome
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error

    @router.post(f"{base}/commands/{{proposal_id}}/cancel")
    def cancel_command(trial_id: UUID, external_patient_id: str, proposal_id: UUID, body: ConfirmIn,
                       user: User, uow: Uow):
        actor_id, organization_id = _identity(user)
        patient = resolve(uow, organization_id, trial_id, external_patient_id)
        authorise_proposal(uow, user, organization_id, proposal_id, patient.id)
        try:
            row = PatientCommandService(uow).cancel(
                proposal_id, organization_id=organization_id, actor_id=actor_id,
                reason=body.reason or "cancelled")
        except Exception as error:      # noqa: BLE001
            raise refuse(error) from error
        return {"proposal_id": str(row.id), "status": row.status}

    def is_staff(user: dict) -> bool:
        role = str(getattr(user["_authz"].principal, "role", "") or "").lower()
        return role in staff_roles

    def strip_private(rows: list[dict]) -> list[dict]:
        """Remove authority / reason from the operational layers of a diff."""
        clean = []
        for row in rows:
            row = dict(row)
            operational = {}
            for side, layers in (row.get("operational") or {}).items():
                layers = dict(layers or {})
                for key in ("override", "cancelled"):
                    if isinstance(layers.get(key), dict):
                        layers[key] = {k: v for k, v in layers[key].items()
                                       if k not in ("authority", "reason", "command_id")}
                operational[side] = layers
            row["operational"] = operational
            clean.append(row)
        return clean

    PAGE_DEFAULT, PAGE_MAX = 50, 200

    @router.post("/pilot/seed")
    async def seed_pilot(user: User, uow: Uow):
        """Create (idempotently) the isolated synthetic pilot ``MTB-UCTSM-PILOT-001``.

        Local/development only: refused unless ``UCTSM_PILOT_MODE`` is enabled. The
        caller is the editor; the approver is a fixed synthetic local actor, so the
        pilot's approval passes the shared maker-checker boundary as a different
        person without involving any real account."""
        import os
        from uuid import NAMESPACE_URL, uuid5
        from app.application.pilot import PilotRefusedError, PilotService

        if os.getenv("UCTSM_PILOT_MODE", "").strip().lower() not in {"1", "true", "yes"}:
            raise HTTPException(404, "Not found")
        actor_id, organization_id = _identity(user)
        approver = uuid5(NAMESPACE_URL, "mtb-uctsm-pilot/synthetic-approver")
        try:
            description = PilotService(uow).seed(
                organization_id=organization_id, editor_id=actor_id, approver_id=approver)
        except PilotRefusedError as error:
            raise HTTPException(409, str(error)) from error
        if operational_provisioner is not None:
            # idempotent upsert of the operational relationship documents; a failure is
            # reported, and seeding again reconciles (the canonical side is complete)
            try:
                await operational_provisioner(user, description)
            except Exception as error:      # noqa: BLE001
                raise HTTPException(
                    503, "The pilot's operational relationship records could not be "
                         "written; seed again to reconcile.") from error
        return description

    @router.post("/pilot/seed-amendment")
    async def seed_pilot_amendment(user: User, uow: Uow):
        """Add (idempotently) the synthetic Amendment 1 of the pilot: a new protocol and
        schedule version that declares it amends Pilot 1.0, approved by the fixed synthetic
        approver. Local/development only (``UCTSM_PILOT_MODE``). No patient is moved."""
        import os
        from uuid import NAMESPACE_URL, uuid5
        from app.application.pilot import PilotRefusedError, PilotService

        if os.getenv("UCTSM_PILOT_MODE", "").strip().lower() not in {"1", "true", "yes"}:
            raise HTTPException(404, "Not found")
        actor_id, organization_id = _identity(user)
        approver = uuid5(NAMESPACE_URL, "mtb-uctsm-pilot/synthetic-approver")
        try:
            return PilotService(uow).seed_amendment(
                organization_id=organization_id, editor_id=actor_id, approver_id=approver)
        except PilotRefusedError as error:
            raise HTTPException(409, str(error)) from error

    @router.get(f"/trials/{{trial_id}}/patients")
    async def list_patients(
        trial_id: UUID, user: User, uow: Uow, cursor: str | None = None,
        limit: int = PAGE_DEFAULT,
    ):
        """A stable page of the plane's patients for one trial: scoped to the
        caller's organization, then filtered by the operational relationship
        BEFORE serialisation. ``next_cursor`` is explicit; no total is returned."""
        if limit < 1 or limit > PAGE_MAX:
            raise HTTPException(422, f"limit must be between 1 and {PAGE_MAX}")
        _, organization_id = _identity(user)
        scoped_role = str(getattr(user["_authz"].principal, "role", "") or "").lower()
        items: list[dict] = []
        position, exhausted, scanned = cursor, False, 0
        while len(items) < limit and scanned < 10:       # bounded fill of one page
            scanned += 1
            batch = uow.read(lambda tx: tx.patients.list_page_for_trial(
                organization_id, trial_id, after_code=position, limit=limit))
            if not batch:
                exhausted = True
                break
            for row in batch:
                position = row.patient_code
                if scoped_role == "patient":
                    allowed = await authorizer.access.is_own_patient_record(
                        user, row.external_patient_id or "")
                else:
                    allowed = await authorizer.access.can_access_patient(
                        user, row.external_patient_id or "")
                if allowed and row.external_patient_id:
                    items.append({"external_patient_id": row.external_patient_id,
                                  "patient_code": row.patient_code,
                                  "authority_plane": row.authority_plane})
                if len(items) >= limit:
                    break
            if len(batch) < limit and len(items) < limit:
                exhausted = True
                break
        next_cursor = None if exhausted else position
        return {"trial_id": str(trial_id), "items": items, "next_cursor": next_cursor,
                "limit": limit}

    @router.get(f"{base}/commands")
    def list_commands(trial_id: UUID, external_patient_id: str, user: User, uow: Uow):
        _, organization_id = _identity(user)
        patient = resolve(uow, organization_id, trial_id, external_patient_id)
        profile = "STAFF" if is_staff(user) else "RESTRICTED"
        return {"patient_id": str(patient.id), "profile": profile,
                "commands": PatientCommandService(uow).ledger(
                    patient.id, organization_id=organization_id, profile=profile)}

    staff_roles = {"pi", "crc", "smo", "site"}

    def for_role(calendar: list[CalendarEntry], user: dict) -> list[CalendarEntry]:
        """Authority, reasons and command identity are treating-staff detail: a
        patient or sponsor/CRO response carries the dates but not who authorised
        an exception or why."""
        role = str(getattr(user["_authz"].principal, "role", "") or "").lower()
        if role in staff_roles:
            return calendar
        hidden = {"override_authority": None, "override_reason": None,
                  "cancelled_reason": None, "last_command_id": None,
                  "track_rebase_policy": None}
        return [entry.model_copy(update=hidden) for entry in calendar]

    @router.get(f"{base}/calendar", response_model=CalendarOut)
    def get_calendar(trial_id: UUID, external_patient_id: str, user: User, uow: Uow):
        _, organization_id = _identity(user)
        patient = resolve(uow, organization_id, trial_id, external_patient_id)
        calendar = for_role(PatientScheduleService(uow).current_calendar(
            patient.id, organization_id=organization_id), user)
        return CalendarOut(
            patient_id=patient.id, trial_id=trial_id, authority_plane=patient.authority_plane,
            schedule_version_id=patient.current_schedule_version_id, calendar=calendar,
            pending_anchor_candidates=pending(uow, patient.id))

    # P6: protocol amendments (compare, mapping review, simulate, adopt / remain / return)
    from app.api.uctsm_amendments import register_amendment_routes
    register_amendment_routes(
        router, authorizer=authorizer, current_user_dependency=current_user_dependency,
        uow_dependency=uow_dependency)

    return router
