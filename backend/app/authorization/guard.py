"""FastAPI integration of the UCTSM authorization policy (P3-02 / P3-03).

``RouteAuthorizer.scoped_user(...)`` returns a dependency that REPLACES the plain
"current user" dependency of the UCTSM routers. Every handler therefore receives
a user dict that has already been through the policy for its own route, and a
route without a manifest declaration cannot be reached (fail closed).

What it does, in order:

1. looks the route up in the manifest (undeclared -> 403 ROUTE_NOT_DECLARED);
2. builds the principal from the persisted user (already re-read per request by
   ``current_user``: suspension / forced logout) plus grants read fresh from the
   grant store - nothing in the request or the JWT is authorization evidence;
3. resolves the named resource from persisted records, verifies child-to-parent
   consistency, and derives the caller's relationship to it;
4. asks the pure policy for a decision;
5. on denial writes a minimal audit record (stable reason code, capability,
   actor, policy version, correlation id - no PHI) and raises the documented
   status BEFORE the handler runs, so nothing is mutated, queued or built;
6. on success hands the handler a user dict whose ``organization_id`` is the
   RESOURCE OWNER's organization (so an assigned investigator from another
   organization acts within the trial's tenant, not their own), plus
   ``_authz`` carrying the decision and, for organization-wide lists, the set of
   trials the caller may see.

HTTP semantics: 401 is produced earlier by authentication; 404 for a resource
the caller has no relationship to (identical to "does not exist"); 403 only when
the caller can already see the resource but lacks the capability.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.base import get_session

from .catalog import POLICY_VERSION, Capability
from .grants import GrantStore
from .manifest import RouteRule, rule_for
from .policy import (
    Decision, Principal, Relationship, ResourceContext, ResourceKind, authorize,
)

_INACTIVE_STATUSES = {"suspended", "inactive", "removed", "deactivated", "disabled"}

DenialSink = Callable[[dict[str, Any]], Awaitable[None]]


class OperationalAccess(Protocol):
    """The operational (legacy Mongo) relationship checks, supplied by ``server.py``.

    They are the existing ``_can_access_trial`` / ``_can_access_patient`` rules,
    keyed by the OPERATIONAL id that canonical records carry as
    ``external_trial_id`` / ``external_patient_id``.
    """

    async def can_access_trial(self, user: dict[str, Any], external_trial_id: str) -> bool: ...

    async def can_access_patient(self, user: dict[str, Any], external_patient_id: str) -> bool: ...

    async def is_own_patient_record(self, user: dict[str, Any], external_patient_id: str) -> bool: ...


@dataclass(frozen=True)
class AuthzOutcome:
    principal: Principal
    decision: Decision
    rule: RouteRule
    capability: Capability
    resource: ResourceContext
    #: canonical trial ids this caller may see through an organization-wide list.
    allowed_trial_ids: frozenset[UUID] | None = None
    correlation_id: str = ""


def _is_active(user: dict[str, Any]) -> bool:
    return str(user.get("status") or "").strip().lower() not in _INACTIVE_STATUSES


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


#: Candidate trials are scanned in id-ordered pages of this size, up to this many
#: pages (50 x 200 = 10 000 trials). Beyond that the request says so (503) instead of
#: silently dropping trials.
_CANDIDATE_PAGE = 200
_CANDIDATE_PAGES = 50


@dataclass
class RouteAuthorizer:
    grants: GrantStore
    access: OperationalAccess
    denial_sink: DenialSink | None = None
    #: ``(trial_id, external_patient_id) -> owning organization id | None``: the
    #: persisted canonical owner of a plane patient (R2). None = not configured.
    plane_locator: Callable[[UUID, str], Any] | None = None
    #: records every denial in memory too (tests and diagnostics).
    denials: list[dict[str, Any]] = field(default_factory=list)

    # -- principal -------------------------------------------------------------

    async def principal(self, user: dict[str, Any]) -> Principal:
        organization_id = str(user.get("organization_id") or user.get("org_id") or "")
        user_id = str(user.get("id") or "")
        grants = await self.grants.active_for_user(user_id, organization_id) if user_id else ()
        return Principal(
            user_id=user_id, organization_id=organization_id,
            role=str(user.get("role") or ""), org_admin=bool(user.get("org_admin")),
            active=_is_active(user), grants=tuple(grants))

    # -- scope resolution (canonical SQL) --------------------------------------

    async def _trial_context(self, user: dict[str, Any], principal: Principal,
                             trial: db.Trial | None, *, kind: ResourceKind,
                             resource_id: str | None) -> ResourceContext:
        if trial is None:
            return ResourceContext(kind=kind, found=False)
        owner = str(trial.organization_id)
        base = dict(kind=kind, organization_id=owner, trial_id=str(trial.id),
                    resource_id=resource_id or str(trial.id))
        if principal.role == "patient":
            # A patient never acts on a trial-level record.
            return ResourceContext(relationship=Relationship.NONE, **base)
        sponsor_side = principal.role in ("sponsor", "cro")
        if not trial.external_trial_id:
            # No verified link to the operational trial. Only the owning
            # organization's sponsor/CRO side is established by the canonical
            # record itself; everyone else is UNMAPPED and denied.
            rel = (Relationship.ORGANIZATION if owner == principal.organization_id and sponsor_side
                   else Relationship.UNMAPPED)
            return ResourceContext(relationship=rel, **base)
        if await self.access.can_access_trial(user, str(trial.external_trial_id)):
            same_org_sponsor = owner == principal.organization_id and sponsor_side
            return ResourceContext(
                relationship=Relationship.ORGANIZATION if same_org_sponsor else Relationship.ASSIGNED,
                **base)
        return ResourceContext(relationship=Relationship.NONE, **base)

    async def _patient_context(self, user: dict[str, Any], principal: Principal,
                               patient: db.Patient | None, session: Session) -> ResourceContext:
        if patient is None:
            return ResourceContext(kind=ResourceKind.PATIENT, found=False)
        trial = session.get(db.Trial, patient.trial_id)
        owner = str(trial.organization_id) if trial else str(patient.organization_id)
        base = dict(kind=ResourceKind.PATIENT, organization_id=owner,
                    trial_id=str(patient.trial_id), resource_id=str(patient.id))
        external = patient.external_patient_id
        if principal.role == "patient":
            if external and await self.access.is_own_patient_record(user, str(external)):
                return ResourceContext(relationship=Relationship.SELF, **base)
            return ResourceContext(relationship=Relationship.NONE if external else Relationship.UNMAPPED, **base)
        trial_ctx = await self._trial_context(
            user, principal, trial, kind=ResourceKind.PATIENT, resource_id=str(patient.id))
        if trial_ctx.relationship in (Relationship.NONE, Relationship.UNMAPPED):
            return trial_ctx
        sponsor_side = principal.role in ("sponsor", "cro")
        if not external:
            rel = (Relationship.ORGANIZATION
                   if sponsor_side and owner == principal.organization_id else Relationship.UNMAPPED)
            return ResourceContext(relationship=rel, **base)
        if await self.access.can_access_patient(user, str(external)):
            return ResourceContext(relationship=trial_ctx.relationship, **base)
        return ResourceContext(relationship=Relationship.NONE, **base)

    async def _resolve_sql(self, rule: RouteRule, params: dict[str, str], user: dict[str, Any],
                           principal: Principal, session: Session) -> list[ResourceContext]:
        kind = rule.resource
        missing = ResourceContext(kind=kind, found=False)

        async def trial_ctx(trial: db.Trial | None, rid: str | None = None) -> ResourceContext:
            return await self._trial_context(user, principal, trial, kind=kind, resource_id=rid)

        if kind is ResourceKind.TRIAL:
            trial_id = _uuid(params.get("trial_id"))
            return [await trial_ctx(session.get(db.Trial, trial_id) if trial_id else None)]

        if kind is ResourceKind.PROTOCOL:
            protocol_id = _uuid(params.get("protocol_id"))
            protocol = session.get(db.Protocol, protocol_id) if protocol_id else None
            if protocol is None:
                return [missing]
            if "protocol_version_id" in params:
                version_id = _uuid(params["protocol_version_id"])
                version = session.get(db.ProtocolVersion, version_id) if version_id else None
                # child must belong to the parent the URL names
                if version is None or version.protocol_id != protocol.id:
                    return [missing]
            return [await trial_ctx(session.get(db.Trial, protocol.trial_id), str(protocol.id))]

        if kind is ResourceKind.SCHEDULE_VERSION:
            out: list[ResourceContext] = []
            for name in rule.params:
                if name not in params:
                    continue
                version_id = _uuid(params[name])
                trial = None
                if version_id is not None:
                    trial = session.execute(
                        select(db.Trial)
                        .join(db.Protocol, db.Protocol.trial_id == db.Trial.id)
                        .join(db.ProtocolVersion, db.ProtocolVersion.protocol_id == db.Protocol.id)
                        .join(db.ScheduleDefinition, db.ScheduleDefinition.protocol_version_id == db.ProtocolVersion.id)
                        .join(db.ScheduleVersion, db.ScheduleVersion.schedule_definition_id == db.ScheduleDefinition.id)
                        .where(db.ScheduleVersion.id == version_id)
                    ).scalar_one_or_none()
                out.append(await trial_ctx(trial, str(version_id) if version_id else None))
            # children named in the path must belong to the schedule version
            version_id = _uuid(params.get("schedule_version_id"))
            if version_id is not None:
                event_id = _uuid(params.get("event_id")) if "event_id" in params else None
                if event_id is not None:
                    event = session.get(db.Event, event_id)
                    if event is None or event.schedule_version_id != version_id:
                        return [missing]
                issue_id = _uuid(params.get("issue_id")) if "issue_id" in params else None
                if issue_id is not None:
                    issue = session.get(db.ValidationIssue, issue_id)
                    if issue is None or issue.schedule_version_id != version_id:
                        return [missing]
            return out or [missing]

        if kind is ResourceKind.PATIENT:
            patient_id = _uuid(params.get("patient_id"))
            patient = session.get(db.Patient, patient_id) if patient_id else None
            ctx = await self._patient_context(user, principal, patient, session)
            candidate = _uuid(params.get("candidate_id")) if "candidate_id" in params else None
            if candidate is not None and ctx.found and patient is not None:
                row = session.get(db.PatientAnchor, candidate)
                if row is None or row.patient_id != patient.id:
                    return [missing]
            return [ctx]

        if kind is ResourceKind.EXTRACTION_RUN:
            run_id = _uuid(params.get("run_id"))
            run = session.get(db.ExtractionRun, run_id) if run_id else None
            if run is None:
                return [missing]
            version = session.get(db.ProtocolVersion, run.protocol_version_id)
            protocol = session.get(db.Protocol, version.protocol_id) if version else None
            trial = session.get(db.Trial, protocol.trial_id) if protocol else None
            return [await trial_ctx(trial, str(run.id))]

        if kind is ResourceKind.IMPACT_PROPOSAL:
            proposal_id = _uuid(params.get("proposal_id"))
            proposal = session.get(db.ScheduleImpactProposal, proposal_id) if proposal_id else None
            patient = session.get(db.Patient, proposal.patient_id) if proposal else None
            return [await self._patient_context(user, principal, patient, session)]

        if kind is ResourceKind.PATIENT_EVENT:
            event_id = _uuid(params.get("patient_event_id"))
            event = session.get(db.PatientEvent, event_id) if event_id else None
            schedule = session.get(db.PatientSchedule, event.patient_schedule_id) if event else None
            patient = session.get(db.Patient, schedule.patient_id) if schedule else None
            return [await self._patient_context(user, principal, patient, session)]

        if kind is ResourceKind.PARITY_RUN:
            trial_id = _uuid(params.get("trial_id"))
            run_id = _uuid(params.get("run_id"))
            run = session.get(db.ParityRun, run_id) if run_id else None
            if run is None or trial_id is None or run.trial_id != trial_id:
                return [missing]
            return [await trial_ctx(session.get(db.Trial, trial_id), str(run.id))]

        return [missing]

    # -- scope resolution (Mongo patient plane) --------------------------------

    async def _resolve_mongo_plane(self, params: dict[str, str], user: dict[str, Any],
                                   principal: Principal) -> ResourceContext:
        """The plane is organization-scoped by its own lookup (the handler's
        ``find_by_external_id``), so the owner is the caller's organization. The
        caller's relationship to the OPERATIONAL patient is what is checked here."""
        trial_id = _uuid(params.get("trial_id"))
        external = params.get("external_patient_id") or ""
        if trial_id is None or not external:
            return ResourceContext(kind=ResourceKind.PATIENT, found=False)
        if principal.role == "patient":
            rel = (Relationship.SELF if await self.access.is_own_patient_record(user, external)
                   else Relationship.NONE)
        elif await self.access.can_access_patient(user, external):
            rel = Relationship.ASSIGNED
        else:
            rel = Relationship.NONE
        # The OWNER is the persisted canonical patient's organization, not the
        # caller's: an investigator from another participating organization is
        # served in the owner's tenant once the operational relationship checks
        # out. The plane is opened for this only AFTER the relationship holds,
        # and nothing is trusted from the request but the two path ids.
        owner = principal.organization_id
        if rel in (Relationship.ASSIGNED, Relationship.SELF) and self.plane_locator is not None:
            try:
                located = self.plane_locator(trial_id, external)
                if hasattr(located, "__await__"):
                    located = await located
            except Exception:           # noqa: BLE001 - an unreachable plane denies, never widens
                located = None
            if located:
                owner = str(located)
        base = dict(kind=ResourceKind.PATIENT, organization_id=owner,
                    trial_id=str(trial_id), resource_id=external)
        return ResourceContext(relationship=rel, **base)

    async def trial_context(self, user: dict[str, Any], principal: Principal, trial: Any,
                            ) -> ResourceContext:
        """The caller's relationship to one canonical trial RECORD (anything with
        ``id``, ``organization_id`` and ``external_trial_id``), for the plane's
        trial-level routes whose handler loads the trial from its own store. The same
        rule the SQL plane applies: sponsor-side members of the owning organization, or
        staff with an operational assignment; nobody else."""
        return await self._trial_context(
            user, principal, trial, kind=ResourceKind.TRIAL, resource_id=str(trial.id))

    # -- lists -----------------------------------------------------------------

    async def _allowed_trials(self, user: dict[str, Any], principal: Principal,
                              capabilities: tuple[Capability, ...], session: Session) -> frozenset[UUID]:
        own_org = _uuid(principal.organization_id)
        trials = list(session.scalars(select(db.Trial).where(db.Trial.organization_id == own_org)))
        if principal.role in ("pi", "crc", "smo", "site"):
            # Cross-organization participation: trials owned by another
            # organization that this user is explicitly tied to. Bounded; each
            # candidate still has to pass the operational relationship check.
            after = None
            for _ in range(_CANDIDATE_PAGES):
                query = select(db.Trial).where(db.Trial.organization_id != own_org,
                                               db.Trial.external_trial_id.is_not(None))
                if after is not None:
                    query = query.where(db.Trial.id > after)
                page = list(session.scalars(query.order_by(db.Trial.id).limit(_CANDIDATE_PAGE)))
                trials += page
                if len(page) < _CANDIDATE_PAGE:
                    break
                after = page[-1].id
            else:
                # Never a silent cut-off: an enormous candidate set is reported.
                raise HTTPException(
                    503, "Too many participating trials to list in one request; narrow by trial_id.")
        allowed: set[UUID] = set()
        for trial in trials:
            ctx = await self._trial_context(
                user, principal, trial, kind=ResourceKind.TRIAL, resource_id=str(trial.id))
            if any(authorize(principal, cap, ctx).allowed for cap in capabilities):
                allowed.add(trial.id)
        return frozenset(allowed)

    # -- entry point -----------------------------------------------------------

    async def _deny(self, request: Request, user: dict[str, Any], principal: Principal | None,
                    rule: RouteRule | None, decision: Decision, resource: ResourceContext | None,
                    correlation_id: str) -> HTTPException:
        record = {
            "action": "uctsm.authorization.denied",
            "actor_id": str(user.get("id") or ""),
            "organization_id": principal.organization_id if principal else str(user.get("organization_id") or ""),
            "role": principal.role if principal else str(user.get("role") or ""),
            "capability": decision.capability,
            "reason_code": decision.reason_code,
            "status": decision.status,
            "policy_version": POLICY_VERSION,
            "method": request.method,
            "route": getattr(request.scope.get("route"), "path", request.url.path),
            "resource_kind": resource.kind.value if resource else "NONE",
            # canonical id only; never names, notes or document evidence
            "resource_id": resource.resource_id if resource else None,
            "trial_id": resource.trial_id if resource else None,
            "correlation_id": correlation_id,
        }
        self.denials.append(record)
        if self.denial_sink is not None:
            try:
                await self.denial_sink(record)
            except Exception:  # the denial itself must still be returned
                pass
        if decision.status == 403:
            # ``detail`` stays a plain string: existing screens render it as text.
            # The machine-readable reason travels in headers. A 404 carries none,
            # so a foreign resource is indistinguishable from a missing one.
            return HTTPException(
                403, "You do not have permission to perform this action.",
                headers={"X-Authz-Reason": decision.reason_code,
                         "X-Authz-Capability": decision.capability,
                         "X-Authz-Policy": POLICY_VERSION})
        return HTTPException(404, "Not found")

    async def authorize_request(self, request: Request, user: dict[str, Any],
                                session: Session | None) -> dict[str, Any]:
        correlation_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        rule = rule_for(request.method, route_path)
        if rule is None:
            decision = Decision(False, 403, "ROUTE_NOT_DECLARED", f"{request.method} {route_path}")
            raise await self._deny(request, user, None, None, decision, None, correlation_id)

        principal = await self.principal(user)
        capabilities = rule.capabilities
        # A route whose exact capability depends on the request body (approve vs
        # reject) is narrowed here, before any side effect.
        if rule.body_field and rule.body_map:
            try:
                payload = await request.json()
            except Exception:
                payload = None
            chosen = rule.body_map.get(str((payload or {}).get(rule.body_field))) \
                if isinstance(payload, dict) else None
            if chosen is not None:
                capabilities = (chosen,)
            elif rule.strict_unmapped:
                capabilities = (rule.capabilities[0],)

        params = {key: str(value) for key, value in request.path_params.items()}
        allowed_trials: frozenset[UUID] | None = None

        if rule.plane == "mongo_plane" and "external_patient_id" not in params:
            # a trial-level plane route (a paged list): authorized on the capability;
            # the handler scopes to the caller's organization and filters every row
            # through the operational relationship before it is serialised
            contexts = [ResourceContext()]
        elif rule.plane == "mongo_plane":
            contexts = [await self._resolve_mongo_plane(params, user, principal)]
        elif rule.resource is ResourceKind.NONE:
            contexts = [ResourceContext()]
        else:
            if session is None:  # a SQL-plane route reached without a SQL session
                raise await self._deny(
                    request, user, principal, rule,
                    Decision(False, 403, "NO_RESOLVER", capabilities[0].value), None, correlation_id)
            contexts = await self._resolve_sql(rule, params, user, principal, session)

        chosen_decision: Decision | None = None
        failing: tuple[Decision, ResourceContext] | None = None
        for capability in capabilities:
            decisions = [authorize(principal, capability, ctx) for ctx in contexts]
            if all(item.allowed for item in decisions):
                chosen_decision = decisions[0]
                chosen_capability = capability
                break
            bad = next(item for item in decisions if not item.allowed)
            # keep the most informative denial: a 404 (no relationship) wins over 403
            if failing is None or (bad.status == 404 and failing[0].status != 404):
                failing = (bad, contexts[decisions.index(bad)])
        if chosen_decision is None:
            decision, ctx = failing  # type: ignore[misc]
            raise await self._deny(request, user, principal, rule, decision, ctx, correlation_id)

        if rule.list_scoped and session is not None:
            allowed_trials = await self._allowed_trials(user, principal, capabilities, session)
            requested = _uuid(request.query_params.get("trial_id"))
            if requested is not None and requested not in allowed_trials:
                decision = Decision(False, 404, "NO_RELATIONSHIP", capabilities[0].value)
                raise await self._deny(request, user, principal, rule, decision,
                                       ResourceContext(kind=ResourceKind.TRIAL, resource_id=str(requested)),
                                       correlation_id)

        outcome = AuthzOutcome(
            principal=principal, decision=chosen_decision, rule=rule,
            capability=chosen_capability, resource=contexts[0],
            allowed_trial_ids=allowed_trials, correlation_id=correlation_id)
        scoped = dict(user)
        # Act within the tenant that OWNS the resource (an assigned investigator
        # from another organization works in the trial's tenant, not their own).
        owner = chosen_decision.organization_id or principal.organization_id
        scoped["organization_id"] = owner
        scoped["_authz"] = outcome
        return scoped

    # -- dependency factories --------------------------------------------------

    def scoped_user_sql(self, current_user_dependency: Callable[..., Any]) -> Callable[..., Any]:
        async def dependency(request: Request,
                             user: dict[str, Any] = Depends(current_user_dependency),
                             session: Session = Depends(get_session)) -> dict[str, Any]:
            return await self.authorize_request(request, user, session)
        return dependency

    def scoped_user_plane(self, current_user_dependency: Callable[..., Any]) -> Callable[..., Any]:
        async def dependency(request: Request,
                             user: dict[str, Any] = Depends(current_user_dependency)) -> dict[str, Any]:
            return await self.authorize_request(request, user, None)
        return dependency
