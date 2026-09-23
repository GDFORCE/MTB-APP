"""Remote Audit HTTP API — Phase 2 (Core Evidence & Trust Chain) and
Phase 3 (Sites, Facilities, Equipment, Geofence Map).

Imported at the bottom of server.py, exactly like admin_routes.py/org_routes.py
(see server.py's own comment there): this module imports `db`, `current_user`,
`write_audit`, `now`, `serialize`, `_read_upload_capped` back from server, so
it can only be imported once those names exist.

Authorization here is deliberately explicit per-endpoint (matching this
codebase's existing style in org_routes.py) rather than hidden behind one
opaque dependency: each handler resolves the audit, resolves the caller's
access via the participant model (app/services/remote_audit/participants.py),
and checks the specific role/capability that action requires. Every
mismatch — wrong org, wrong role, audit not found — returns 404, never 403,
so a caller can never use the response to enumerate resources they can't
access (Production-Grade Implementation Plan v1.3's resource-level ABAC).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

import storage as file_storage
from server import _read_upload_capped, current_user, db, now, pwd_ctx, serialize, write_audit

from app.services.remote_audit import audits as audits_service
from app.services.remote_audit import capability_grants as capability_grants_service
from app.services.remote_audit import decisions as decisions_service
from app.services.remote_audit import documents as documents_service
from app.services.remote_audit import evidence as evidence_service
from app.services.remote_audit import file_checks
from app.services.remote_audit import facilities as facilities_service
from app.services.remote_audit import geofence_onboarding
from app.services.remote_audit import observability as obs
from app.services.remote_audit import participants as participants_service
from app.services.remote_audit import queries as queries_service
from app.services.remote_audit import report as report_service
from app.services.remote_audit import scoring
from app.services.remote_audit import sites as sites_service
from app.services.remote_audit import stepup as stepup_service
from app.services.remote_audit import templates as templates_service
from app.services.remote_audit import virtual_visits
from app.services.remote_audit import vision_extraction
from app.services.remote_audit.capabilities import RemoteAuditCapability
from app.services.remote_audit.job_queue import JobQueue
from app.services.remote_audit.mutation_guard import (
    IdempotencyKeyConflict,
    IdempotencyInProgress,
    IdempotencyStore,
    check_rate_limit,
    new_correlation_id,
)

router = APIRouter(prefix="/api/remote-audit")

_idempotency = IdempotencyStore(db)
_job_queue = JobQueue(db)


# ── Shared helpers ───────────────────────────────────────────────────────────

async def _resolve_audit_or_404(audit_id: str) -> Dict[str, Any]:
    audit = await audits_service.get_audit(db, audit_id)
    if audit is None:
        raise HTTPException(404, "Not found")
    return audit


def _require_org_id(user: dict) -> str:
    org_id = user.get("organization_id")
    if not org_id:
        raise HTTPException(404, "Not found")  # no tenant context -- never leak "why"
    return org_id


async def _access_decision(audit: Dict[str, Any], user: dict):
    org_id = _require_org_id(user)
    decision = await participants_service.resolve_access_for_audit(
        db, user_id=user["id"], user_org_id=org_id,
        audit_owner_org_id=audit["audit_owner_org_id"], site_org_id=audit["site_org_id"],
        audit_id=audit["id"],
    )
    # Denials are the security signal spec §20 asks for. A caller from an org
    # with no relationship to this audit at all is the enumeration-shaped one,
    # so it gets its own metric rather than being lost among ordinary
    # "assigned to a different audit" denials.
    if not decision.can_list:
        unrelated = org_id not in (audit["audit_owner_org_id"], audit["site_org_id"])
        obs.emit_metric(obs.Metric.CROSS_ORG_DENIED if unrelated else obs.Metric.AUTHORIZATION_DENIED,
                        tags={"audit_id": audit["id"], "reason": decision.reason})
    return decision


async def _begin_idempotent(scope: str, idempotency_key: Optional[str], payload: dict):
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header is required")
    try:
        return await _idempotency.begin(scope=scope, key=idempotency_key, payload=payload)
    except IdempotencyKeyConflict as exc:
        raise HTTPException(409, {"code": exc.code, "message": exc.message}) from exc
    except IdempotencyInProgress as exc:
        raise HTTPException(409, {"code": exc.code, "message": exc.message}) from exc


async def _complete_idempotent(scope: str, idempotency_key: str, status_code: int, body: Any) -> None:
    await _idempotency.complete(scope=scope, key=idempotency_key, status_code=status_code, response_body=body)


def _capture_error(exc: evidence_service.CaptureError) -> HTTPException:
    return HTTPException(400, {"code": exc.code, "message": str(exc)})


# ── Audits (minimal shell -- full template-driven creation is Phase 3) ──────

class CreateAuditIn(BaseModel):
    site_id: str
    # KNOWN TEMPORARY GAP, closed in Phase 3: this minimal shell has no real
    # `remote_audit_sites` record to resolve a site's true organization_id
    # from server-side yet, so the caller supplies it directly. This does
    # NOT grant the caller any elevated access on its own -- participant
    # grants (checked everywhere else) are still required for any action,
    # and org membership alone never earns audit detail (correction #3) --
    # so a bogus value here only mis-scopes who COULD later be assigned as
    # a site-side participant, never an immediate authorization bypass.
    # Phase 3 replaces this field with server-side lookup via `site_id`.
    site_org_id: str
    jurisdiction: Optional[str] = None
    governing_authority: Optional[str] = None


@router.post("/audits")
async def create_audit_endpoint(body: CreateAuditIn, user=Depends(current_user)):
    if user["role"] not in ("sponsor", "cro", "admin"):
        raise HTTPException(404, "Not found")
    org_id = _require_org_id(user)
    audit = await audits_service.create_audit(
        db, audit_owner_org_id=org_id, site_id=body.site_id, site_org_id=body.site_org_id,
        created_by=user["id"], jurisdiction=body.jurisdiction, governing_authority=body.governing_authority,
    )
    await write_audit(user, "remote_audit.audit.created", f"Created remote audit {audit['id']}",
                      target_id=audit["id"], org_id=org_id)
    return serialize({**audit})


@router.get("/audits/{audit_id}")
async def get_audit_endpoint(audit_id: str, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_list:
        raise HTTPException(404, "Not found")
    if not decision.can_view_details:
        # Minimal-list-only view (v1.3 correction #3): org membership alone
        # never earns audit detail.
        return {"id": audit["id"], "state": audit["state"], "site_id": audit["site_id"]}
    return serialize({**audit})


# ── Participants (minimal assignment endpoint so Phase 2 is testable end to end) ──

class AddParticipantIn(BaseModel):
    user_id: str
    role_in_audit: str = Field(pattern="^(reviewer|approver|site_admin|capturer|observer)$")


_OWNER_SIDE_ROLES = {"reviewer", "approver"}
_SITE_SIDE_ROLES = {"capturer", "site_admin", "observer"}


@router.post("/audits/{audit_id}/participants")
async def add_participant_endpoint(audit_id: str, body: AddParticipantIn, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    org_id = _require_org_id(user)
    is_owner_admin = org_id == audit["audit_owner_org_id"] and bool(user.get("org_admin"))
    is_site_admin = org_id == audit["site_org_id"] and bool(user.get("org_admin"))
    is_platform_admin = user["role"] == "admin"

    if body.role_in_audit in _OWNER_SIDE_ROLES and not (is_owner_admin or is_platform_admin):
        raise HTTPException(404, "Not found")
    if body.role_in_audit in _SITE_SIDE_ROLES and not (is_site_admin or is_platform_admin):
        raise HTTPException(404, "Not found")

    grant = await participants_service.add_participant(
        db, audit_id=audit_id, user_id=body.user_id, role_in_audit=body.role_in_audit, assigned_by=user["id"],
    )
    await write_audit(user, "remote_audit.participant.assigned",
                      f"Assigned {body.user_id} as {body.role_in_audit} on audit {audit_id}",
                      target_id=audit_id, org_id=org_id)
    return grant


# ── Capture sessions / upload-intent / finalize (spec §8.1) ─────────────────

class CreateCaptureSessionIn(BaseModel):
    linked_entity_type: str
    linked_entity_id: str
    requested_media_type: str
    capture_mode: str = Field(pattern="^(LIVE_IN_APP|GALLERY_HISTORICAL)$")


@router.post("/audits/{audit_id}/capture-sessions")
async def create_capture_session_endpoint(
    audit_id: str,
    body: CreateCaptureSessionIn,
    user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_act or decision.role_in_audit not in ("capturer", "site_admin"):
        raise HTTPException(404, "Not found")

    scope = f"capture-session:create:{audit_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None  # _begin_idempotent raises 400 above if falsy

    allowed = await check_rate_limit(db, f"{user['id']}:capture-session-create", limit=30, window_seconds=60)
    if not allowed:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise HTTPException(429, {"code": "RATE_LIMIT_EXCEEDED", "message": "too many capture sessions requested"})

    session = await evidence_service.create_capture_session(
        db, audit_id=audit_id, user_id=user["id"],
        linked_entity_type=body.linked_entity_type, linked_entity_id=body.linked_entity_id,
        requested_media_type=body.requested_media_type, capture_mode=body.capture_mode,
        policy_version=audit.get("template_version") or "default-v1",
    )
    obs.emit_metric(obs.Metric.CAPTURE_SESSION_CREATED,
                    tags={"capture_mode": body.capture_mode})
    await write_audit(user, "remote_audit.capture_session.created", f"Created capture session {session['id']}",
                      target_id=session["id"], org_id=audit["site_org_id"])
    response = serialize({**session})
    await _complete_idempotent(scope, idempotency_key, 201, response)
    return response


class UploadIntentIn(BaseModel):
    file_name: str
    mime_claimed: str
    size_claimed: int = Field(gt=0)


@router.post("/capture-sessions/{session_id}/upload-intent")
async def upload_intent_endpoint(session_id: str, body: UploadIntentIn, user=Depends(current_user)):
    session = await evidence_service.get_capture_session(db, session_id)
    if session is None or session["user_id"] != user["id"]:
        raise HTTPException(404, "Not found")
    try:
        intent = await evidence_service.create_upload_intent(
            db, capture_session_id=session_id, file_name=body.file_name,
            mime_claimed=body.mime_claimed, size_claimed=body.size_claimed,
        )
    except evidence_service.CaptureError as exc:
        raise _capture_error(exc) from exc
    if intent["server_mediated"]:
        intent["server_upload_url"] = f"/api/remote-audit/capture-sessions/{session_id}/upload-bytes"
    return intent


@router.post("/capture-sessions/{session_id}/upload-bytes")
async def upload_bytes_endpoint(session_id: str, file: UploadFile = File(...), user=Depends(current_user)):
    """Server-mediated fallback used only when the storage backend can't
    issue a presigned upload (local disk, dev mode) — see storage.py's
    `presign_upload()` docstring. Production (S3) uploads bypass the API
    entirely via the presigned POST returned by upload-intent."""
    session = await evidence_service.get_capture_session(db, session_id)
    if session is None or session["user_id"] != user["id"]:
        raise HTTPException(404, "Not found")
    object_key = session.get("reserved_object_key")
    if not object_key:
        raise HTTPException(400, "No upload was reserved for this session; call upload-intent first")
    data = await _read_upload_capped(file, evidence_service.DEFAULT_MAX_UPLOAD_BYTES, "File is too large")
    storage = file_storage.get_storage()
    await storage.save(object_key, data, session.get("claimed_mime") or "application/octet-stream")
    return {"ok": True, "object_key": object_key, "size": len(data)}


class FinalizeIn(BaseModel):
    object_key: str
    sha256_client: str
    capture_manifest: Dict[str, Any]


@router.post("/capture-sessions/{session_id}/finalize")
async def finalize_endpoint(
    session_id: str,
    body: FinalizeIn,
    user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    session = await evidence_service.get_capture_session(db, session_id)
    if session is None or session["user_id"] != user["id"]:
        raise HTTPException(404, "Not found")

    scope = f"finalize:{session_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None  # _begin_idempotent raises 400 above if falsy

    try:
        evidence_doc = await evidence_service.finalize_capture(
            db, capture_session_id=session_id, object_key=body.object_key,
            sha256_client=body.sha256_client, capture_manifest=body.capture_manifest,
        )
    except evidence_service.CaptureError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        if exc.code == "SESSION_ALREADY_CONSUMED":
            obs.emit_metric(obs.Metric.CAPTURE_SESSION_REPLAY_REJECTED)
            await write_audit(user, "remote_audit.capture.replay_rejected", str(exc),
                              status="failure", target_id=session_id)
        raise _capture_error(exc) from exc

    await _job_queue.ensure_indexes()
    await _job_queue.enqueue(
        "evidence.verify", {"evidence_id": evidence_doc["id"]}, correlation_id=new_correlation_id())

    obs.emit_metric(obs.Metric.CAPTURE_SESSION_FINALIZED,
                    tags={"capture_mode": session["capture_mode"]})
    await write_audit(user, "remote_audit.evidence.finalized", f"Finalized evidence {evidence_doc['id']}",
                      target_id=evidence_doc["id"])
    response = serialize({**evidence_doc})
    await _complete_idempotent(scope, idempotency_key, 201, response)
    return response


# ── Evidence & verification (read) ──────────────────────────────────────────

async def _evidence_access_or_404(evidence_id: str, user: dict):
    evidence_doc = await evidence_service.get_evidence(db, evidence_id)
    if evidence_doc is None:
        raise HTTPException(404, "Not found")
    audit = await _resolve_audit_or_404(evidence_doc["audit_id"])
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return evidence_doc


@router.get("/evidence/{evidence_id}")
async def get_evidence_endpoint(evidence_id: str, user=Depends(current_user)):
    evidence_doc = await _evidence_access_or_404(evidence_id, user)
    return serialize({**evidence_doc})


@router.get("/evidence/{evidence_id}/verification")
async def get_evidence_verification_endpoint(evidence_id: str, user=Depends(current_user)):
    await _evidence_access_or_404(evidence_id, user)
    latest = await evidence_service.latest_verification(db, evidence_id)
    history = await evidence_service.list_verifications(db, evidence_id)
    return {"latest": latest, "history": history}


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — Sites, Facilities, Equipment, Geofence Map
# ═══════════════════════════════════════════════════════════════════════════

# ── Step-up re-authentication (spec's re-auth principle for sensitive actions) ──

class StepUpIn(BaseModel):
    action_scope: str
    password: str


@router.post("/step-up")
async def issue_step_up_token_endpoint(body: StepUpIn, user=Depends(current_user)):
    """Password-based step-up is the documented dev/pilot fallback (v1.3
    correction #2) -- production requires MFA, enforced inside
    issue_stepup_token() itself via REMOTE_AUDIT_STEPUP_REQUIRES_MFA. This
    endpoint always re-verifies the password (never assumed true just
    because the caller is authenticated); `mfa_verified=False` here honestly
    reflects that no MFA session concept exists anywhere in this app yet.
    """
    full_user = await db.users.find_one({"id": user["id"]}, {"_id": 0, "hashed_password": 1})
    if full_user is None or not pwd_ctx.verify(body.password, full_user["hashed_password"]):
        raise HTTPException(401, "Incorrect password")
    try:
        token = await stepup_service.issue_stepup_token(
            db, user_id=user["id"], action_scope=body.action_scope, mfa_verified=False,
        )
    except stepup_service.StepUpError as exc:
        raise HTTPException(403, {"code": exc.code, "message": str(exc)}) from exc
    await write_audit(user, "remote_audit.stepup.issued", f"Step-up issued for {body.action_scope}",
                      target_id=user["id"])
    return {"token": token["token"], "expires_at": token["expires_at"].isoformat()}


# ── Capability grants (admin-only; template_admin must never be implicit) ──

class GrantCapabilityIn(BaseModel):
    user_id: str
    capability: str


@router.post("/capability-grants")
async def grant_capability_endpoint(body: GrantCapabilityIn, user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(404, "Not found")
    try:
        capability = RemoteAuditCapability(body.capability)
    except ValueError:
        raise HTTPException(400, "Unknown capability")
    grant = await capability_grants_service.grant_capability(
        db, user_id=body.user_id, capability=capability, granted_by=user["id"])
    await write_audit(user, "remote_audit.capability.granted",
                      f"Granted {body.capability} to {body.user_id}", target_id=body.user_id)
    return grant


async def _explicit_grants(user_id: str):
    return await capability_grants_service.fetch_explicit_grants(db, user_id)


# ── Sites & geofences (spec §7.1) ────────────────────────────────────────────

class CreateSiteIn(BaseModel):
    official_name: str
    address: Dict[str, Any] = {}
    timezone: str = "UTC"


@router.post("/sites")
async def create_site_endpoint(body: CreateSiteIn, user=Depends(current_user)):
    if user["role"] not in ("site", "admin") or not (user.get("org_admin") or user["role"] == "admin"):
        raise HTTPException(404, "Not found")
    org_id = _require_org_id(user)
    site = await sites_service.create_site(
        db, organization_id=org_id, official_name=body.official_name,
        address=body.address, timezone_name=body.timezone,
    )
    await write_audit(user, "remote_audit.site.created", f"Created site {site['id']}",
                      target_id=site["id"], org_id=org_id)
    return site


class ProposeGeofenceFromAddressIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    default_radius_m: float = Field(default=geofence_onboarding.DEFAULT_RADIUS_M, gt=0)


async def _site_or_404(site_id: str) -> Dict[str, Any]:
    site = await sites_service.get_site(db, site_id)
    if site is None:
        raise HTTPException(404, "Not found")
    return site


def _require_site_admin(site: Dict[str, Any], user: dict) -> None:
    org_id = _require_org_id(user)
    is_site_admin = org_id == site["organization_id"] and bool(user.get("org_admin"))
    if not (is_site_admin or user["role"] == "admin"):
        raise HTTPException(404, "Not found")


@router.post("/sites/{site_id}/geofence-versions")
async def propose_geofence_endpoint(site_id: str, body: ProposeGeofenceFromAddressIn, user=Depends(current_user)):
    site = await _site_or_404(site_id)
    _require_site_admin(site, user)
    proposal = await geofence_onboarding.propose_geofence_for_address(
        db, site_id=site_id, lat=body.lat, lon=body.lon, proposed_by=user["id"],
        default_radius_m=body.default_radius_m,
    )
    await write_audit(user, "remote_audit.geofence.proposed",
                      f"Proposed geofence v{proposal.geofence_version['version']} for site {site_id} "
                      f"(source={proposal.source})", target_id=site_id, org_id=site["organization_id"])
    return {
        "geofence_version": proposal.geofence_version,
        "source": proposal.source,
        "preview_url": proposal.preview_url,
    }


@router.get("/sites/{site_id}/geofence-versions")
async def list_geofence_versions_endpoint(site_id: str, user=Depends(current_user)):
    site = await _site_or_404(site_id)
    _require_site_admin(site, user)
    return await sites_service.list_geofence_versions(db, site_id)


class ApproveGeofenceIn(BaseModel):
    step_up_token: str


@router.post("/sites/{site_id}/geofence-versions/{geofence_version_id}/approve")
async def approve_geofence_endpoint(
    site_id: str, geofence_version_id: str, body: ApproveGeofenceIn, user=Depends(current_user),
):
    await _site_or_404(site_id)
    if not await participants_service.can_user_approve_geofence(db, user_id=user["id"], site_id=site_id):
        raise HTTPException(404, "Not found")
    if not await stepup_service.consume_stepup_token(
        db, token=body.step_up_token, user_id=user["id"], action_scope="geofence.approve",
    ):
        raise HTTPException(403, {"code": "STEP_UP_REQUIRED", "message": "a valid step-up token is required"})

    try:
        version = await sites_service.approve_geofence_version(
            db, geofence_version_id=geofence_version_id, approved_by=user["id"])
    except ValueError as exc:
        raise HTTPException(404, "Not found") from exc
    await write_audit(user, "remote_audit.geofence.approved",
                      f"Approved geofence version {geofence_version_id} for site {site_id}", target_id=site_id)
    return version


class GrantSiteApproverIn(BaseModel):
    user_id: str


@router.post("/sites/{site_id}/approvers")
async def grant_site_approver_endpoint(site_id: str, body: GrantSiteApproverIn, user=Depends(current_user)):
    # Conservative default, scoped deliberately: platform admin only, until a
    # clearer "who designates a site's geofence approvers" ownership model
    # is needed. Never inferred from org_admin/role alone (v1.3 correction #3).
    if user["role"] != "admin":
        raise HTTPException(404, "Not found")
    await _site_or_404(site_id)
    grant = await participants_service.add_site_approver(db, site_id=site_id, user_id=body.user_id, granted_by=user["id"])
    await write_audit(user, "remote_audit.site_approver.granted",
                      f"Granted {body.user_id} geofence-approval rights on site {site_id}", target_id=site_id)
    return grant


# ── Facilities & equipment (spec §9) ─────────────────────────────────────────

class CreateFacilityIn(BaseModel):
    facility_type: str
    name: str
    floor: Optional[str] = None
    location_text: Optional[str] = None
    capacity: Optional[int] = None


async def _require_capturer_access(audit_id: str, user: dict):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_act or decision.role_in_audit not in ("capturer", "site_admin"):
        raise HTTPException(404, "Not found")
    return audit


@router.post("/audits/{audit_id}/facilities")
async def create_facility_endpoint(audit_id: str, body: CreateFacilityIn, user=Depends(current_user)):
    await _require_capturer_access(audit_id, user)
    facility = await facilities_service.create_facility(
        db, audit_id=audit_id, facility_type=body.facility_type, name=body.name, created_by=user["id"],
        floor=body.floor, location_text=body.location_text, capacity=body.capacity,
    )
    await write_audit(user, "remote_audit.facility.created", f"Created facility {facility['id']}",
                      target_id=facility["id"])
    return facility


@router.get("/audits/{audit_id}/facilities")
async def list_facilities_endpoint(audit_id: str, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return await facilities_service.list_facilities(db, audit_id)


async def _facility_or_404_with_audit(facility_id: str):
    facility = await facilities_service.get_facility(db, facility_id)
    if facility is None:
        raise HTTPException(404, "Not found")
    audit = await _resolve_audit_or_404(facility["audit_id"])
    return facility, audit


class CreateEquipmentIn(BaseModel):
    category: str
    quantity: int = Field(default=1, ge=1)
    serial_number: Optional[str] = None
    calibration_date: Optional[str] = None


@router.post("/facilities/{facility_id}/equipment")
async def create_equipment_endpoint(facility_id: str, body: CreateEquipmentIn, user=Depends(current_user)):
    facility, audit = await _facility_or_404_with_audit(facility_id)
    await _require_capturer_access(audit["id"], user)
    equipment = await facilities_service.create_equipment(
        db, facility_id=facility_id, category=body.category, created_by=user["id"],
        quantity=body.quantity, serial_number=body.serial_number, calibration_date=body.calibration_date,
    )
    await write_audit(user, "remote_audit.equipment.created", f"Created equipment {equipment['id']}",
                      target_id=equipment["id"])
    return equipment


@router.post("/equipment/{equipment_id}/ai-suggest")
async def ai_suggest_equipment_endpoint(equipment_id: str, file: UploadFile = File(...), user=Depends(current_user)):
    """extract -> review -> commit, step 1 ("extract"): analyzes a photo and
    stores the raw suggestion. Never writes to `confirmed` -- see
    /equipment/{id}/confirm for the human-in-the-loop step that does."""
    equipment = await facilities_service.get_equipment(db, equipment_id)
    if equipment is None:
        raise HTTPException(404, "Not found")
    facility, audit = await _facility_or_404_with_audit(equipment["facility_id"])
    await _require_capturer_access(audit["id"], user)

    data = await _read_upload_capped(file, 20 * 1024 * 1024, "Image is too large")
    media_type = file.content_type or "image/jpeg"
    try:
        result = await vision_extraction.analyze_equipment_photo(image_bytes=data, media_type=media_type)
    except Exception as exc:  # noqa: BLE001 -- AI unavailable/misconfigured falls back to manual entry, never a crash
        raise HTTPException(503, {"code": "AI_SUGGESTION_UNAVAILABLE", "message": str(exc)}) from exc

    ai_suggestion = {
        "fields": result.fields, "confidence": result.confidence, "confidence_notes": result.confidence_notes,
        "provider": result.provider, "model": result.model,
    }
    await db[facilities_service.EQUIPMENT_COLLECTION].update_one(
        {"id": equipment_id}, {"$set": {"ai_suggestion": ai_suggestion}})
    await write_audit(user, "remote_audit.equipment.ai_suggested",
                      f"AI-suggested fields for equipment {equipment_id} ({result.provider})",
                      target_id=equipment_id)
    return ai_suggestion


class ConfirmEquipmentIn(BaseModel):
    fields: Dict[str, Any]


@router.post("/equipment/{equipment_id}/confirm")
async def confirm_equipment_endpoint(equipment_id: str, body: ConfirmEquipmentIn, user=Depends(current_user)):
    """extract -> review -> commit, step 2 ("review"/"commit"): only what a
    human confirms here is ever treated as fact, whether or not an AI
    suggestion informed it."""
    equipment = await facilities_service.get_equipment(db, equipment_id)
    if equipment is None:
        raise HTTPException(404, "Not found")
    facility, audit = await _facility_or_404_with_audit(equipment["facility_id"])
    decision = await _access_decision(audit, user)
    if not decision.can_act:  # any participant role may confirm, not just capturer (a reviewer may correct it too)
        raise HTTPException(404, "Not found")
    try:
        confirmed = await facilities_service.confirm_equipment(db, equipment_id, fields=body.fields, confirmed_by=user["id"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await write_audit(user, "remote_audit.equipment.confirmed", f"Confirmed equipment {equipment_id}",
                      target_id=equipment_id)
    return confirmed


# ── Audit templates (spec §9.1) ──────────────────────────────────────────────

class CreateTemplateDraftIn(BaseModel):
    jurisdiction: Optional[str] = None
    scoring_policy_json: Dict[str, Dict[str, float]]
    requirement_items: list = []
    family_id: Optional[str] = None


async def _require_template_admin(user: dict) -> None:
    grants = await _explicit_grants(user["id"])
    if RemoteAuditCapability.TEMPLATE_ADMIN not in grants:
        raise HTTPException(404, "Not found")


@router.post("/templates")
async def create_template_endpoint(body: CreateTemplateDraftIn, user=Depends(current_user)):
    await _require_template_admin(user)
    org_id = _require_org_id(user)
    draft = await templates_service.create_template_draft(
        db, owner_org_id=org_id, jurisdiction=body.jurisdiction,
        scoring_policy_json=body.scoring_policy_json, requirement_items=body.requirement_items,
        created_by=user["id"], family_id=body.family_id,
    )
    await write_audit(user, "remote_audit.template.drafted", f"Drafted template {draft['id']} v{draft['version']}",
                      target_id=draft["id"], org_id=org_id)
    return draft


class ActivateTemplateIn(BaseModel):
    step_up_token: str


@router.post("/templates/{template_id}/activate")
async def activate_template_endpoint(template_id: str, body: ActivateTemplateIn, user=Depends(current_user)):
    await _require_template_admin(user)
    if not await stepup_service.consume_stepup_token(
        db, token=body.step_up_token, user_id=user["id"], action_scope="template.activate",
    ):
        raise HTTPException(403, {"code": "STEP_UP_REQUIRED", "message": "a valid step-up token is required"})
    try:
        activated = await templates_service.activate_template(db, template_id, activated_by=user["id"])
    except templates_service.TemplateValidationError as exc:
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(404, "Not found") from exc
    await write_audit(user, "remote_audit.template.activated", f"Activated template {template_id}",
                      target_id=template_id)
    return activated


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4 — Documents (spec §10)
# ═══════════════════════════════════════════════════════════════════════════
#
# The same corrected upload sequencing as evidence (v1.3 correction #6): this
# endpoint validates CLAIMS and authorization, reserves an opaque object key,
# and creates a QUARANTINED record. The bytes do not exist server-side yet, so
# nothing about the real file can be — or is — checked here. Everything that
# touches the content happens in the worker.


class CreateDocumentIn(BaseModel):
    document_type: str
    file_name: str
    mime_claimed: str
    size_claimed: int = Field(gt=0)
    linked_entity_type: Optional[str] = None
    linked_entity_id: Optional[str] = None
    supersedes_document_id: Optional[str] = None


def _document_error(exc: documents_service.DocumentError) -> HTTPException:
    status = 409 if isinstance(exc, documents_service.InvalidTransition) else 400
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


async def _document_or_404(document_id: str, user: dict, *, require_act: bool = False):
    """Resolve a document plus the caller's access to its audit.

    Reviewer visibility is enforced HERE, not in the UI: a document that has
    not cleared the malware scan is 404 to everyone except the site user who
    uploaded it, who needs to be able to see that their own upload is still
    processing. Everyone else cannot tell an unscanned document from one that
    does not exist.
    """
    doc = await documents_service.get_document(db, document_id)
    if doc is None:
        raise HTTPException(404, "Not found")
    audit = await _resolve_audit_or_404(doc["audit_id"])
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    if not documents_service.is_visible_to_reviewer(doc["status"]) and doc["uploaded_by"] != user["id"]:
        raise HTTPException(404, "Not found")
    if require_act and not decision.can_act:
        raise HTTPException(404, "Not found")
    return doc, audit, decision


@router.post("/audits/{audit_id}/documents")
async def create_document_endpoint(
    audit_id: str,
    body: CreateDocumentIn,
    user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    await _require_capturer_access(audit_id, user)
    scope = f"document.create:{audit_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None

    try:
        doc = await documents_service.create_document(
            db, audit_id=audit_id, uploaded_by=user["id"], document_type=body.document_type,
            file_name=body.file_name, mime_claimed=body.mime_claimed, size_claimed=body.size_claimed,
            linked_entity_type=body.linked_entity_type, linked_entity_id=body.linked_entity_id,
            supersedes_document_id=body.supersedes_document_id,
        )
    except documents_service.DocumentError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _document_error(exc) from exc

    storage = file_storage.get_storage()
    presigned = await storage.presign_upload(
        doc["object_key"], doc["media_type"], documents_service.DEFAULT_MAX_DOCUMENT_BYTES)
    response = serialize({
        **doc,
        "upload": presigned,
        "server_mediated": presigned is None,
        "server_upload_url": (
            None if presigned is not None
            else f"/api/remote-audit/documents/{doc['id']}/upload-bytes"),
    })
    await write_audit(user, "remote_audit.document.created", f"Created document {doc['id']}",
                      target_id=doc["id"])
    await _complete_idempotent(scope, idempotency_key, 201, response)
    return response


@router.post("/documents/{document_id}/upload-bytes")
async def upload_document_bytes_endpoint(
    document_id: str, file: UploadFile = File(...), user=Depends(current_user),
):
    """Server-mediated upload (local/dev storage). The API stores the bytes and
    does not look inside them — parsing is the worker's job, in every
    environment (v1.3 correction #4)."""
    doc = await documents_service.get_document(db, document_id)
    if doc is None or doc["uploaded_by"] != user["id"]:
        raise HTTPException(404, "Not found")
    # Refused once the document has been handed to the worker. Between submit
    # and the worker reading the object there would otherwise be a window in
    # which the file that gets scanned and the file that gets stored are not
    # the same file.
    if doc.get("submitted_at") is not None:
        raise HTTPException(409, {"code": "DOCUMENT_ALREADY_SUBMITTED",
                                  "message": "this document has been submitted for checks; "
                                             "upload a new document instead"})
    if doc["status"] != documents_service.STATUS_QUARANTINED:
        raise HTTPException(409, {"code": "DOCUMENT_ALREADY_SUBMITTED",
                                  "message": "this document's bytes have already been submitted"})
    data = await _read_upload_capped(
        file, documents_service.DEFAULT_MAX_DOCUMENT_BYTES, "File is too large")
    storage = file_storage.get_storage()
    await storage.save(doc["object_key"], data, doc["media_type"])
    return {"ok": True, "object_key": doc["object_key"], "size": len(data)}


@router.post("/documents/{document_id}/submit")
async def submit_document_endpoint(
    document_id: str,
    user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Hand the uploaded document to the worker.

    This only enqueues. It deliberately returns while the document is still
    QUARANTINED — there is no synchronous path to a verdict, because a
    synchronous path would mean parsing untrusted bytes in the API process.
    """
    doc = await documents_service.get_document(db, document_id)
    if doc is None or doc["uploaded_by"] != user["id"]:
        raise HTTPException(404, "Not found")
    scope = f"document.submit:{document_id}"
    replay = await _begin_idempotent(scope, idempotency_key, {"document_id": document_id})
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None

    if doc["status"] != documents_service.STATUS_QUARANTINED:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise HTTPException(409, {"code": "DOCUMENT_ALREADY_SUBMITTED",
                                  "message": f"document is already {doc['status']}"})

    storage = file_storage.get_storage()
    if not await storage.exists(doc["object_key"]):
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise HTTPException(409, {"code": "OBJECT_MISSING",
                                  "message": "no uploaded bytes were found for this document"})

    # Stamped BEFORE enqueueing, so the byte-replacement window closes before
    # a worker can possibly pick the job up.
    await db[documents_service.DOCUMENTS_COLLECTION].update_one(
        {"id": document_id}, {"$set": {"submitted_at": now()}})
    await _job_queue.ensure_indexes()
    await _job_queue.enqueue(
        "document.process", {"document_id": document_id}, correlation_id=new_correlation_id())
    await write_audit(user, "remote_audit.document.submitted",
                      f"Submitted document {document_id} for processing", target_id=document_id)
    response = serialize({"id": document_id, "status": doc["status"], "queued": True})
    await _complete_idempotent(scope, idempotency_key, 202, response)
    return response


@router.get("/audits/{audit_id}/documents")
async def list_documents_endpoint(audit_id: str, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    all_docs = await documents_service.list_documents(db, audit_id)
    # A site user sees their own in-flight uploads so they can tell whether
    # something is still processing; nobody sees anyone else's unscanned file.
    return [
        d for d in all_docs
        if documents_service.is_visible_to_reviewer(d["status"]) or d["uploaded_by"] == user["id"]
    ]


@router.get("/documents/{document_id}")
async def get_document_endpoint(document_id: str, user=Depends(current_user)):
    doc, _audit, _decision = await _document_or_404(document_id, user)
    return serialize(doc)


@router.get("/documents/{document_id}/checks")
async def get_document_checks_endpoint(document_id: str, user=Depends(current_user)):
    await _document_or_404(document_id, user)
    latest = await documents_service.latest_check(db, document_id)
    history = await documents_service.list_checks(db, document_id)
    return serialize({"latest": latest, "history": history})


@router.get("/documents/{document_id}/content")
async def get_document_content_endpoint(document_id: str, user=Depends(current_user)):
    """Serve the stored bytes.

    Gated on `content_blocked` independently of status: a document rejected for
    malware keeps a visible RECORD (so the site is told why) but its CONTENT is
    never served to anyone, including the uploader.
    """
    doc, _audit, _decision = await _document_or_404(document_id, user)
    if doc.get("content_blocked"):
        raise HTTPException(403, {"code": "CONTENT_BLOCKED",
                                  "message": "this file's contents are not available"})
    if not documents_service.is_visible_to_reviewer(doc["status"]):
        raise HTTPException(409, {"code": "DOCUMENT_NOT_READY",
                                  "message": f"document is {doc['status']}"})
    storage = file_storage.get_storage()
    data, content_type = await storage.open(doc["object_key"])
    await write_audit(user, "remote_audit.document.object_access",
                      f"Read document object {document_id}", target_id=document_id)
    from fastapi.responses import Response

    return Response(
        content=data,
        media_type=content_type or doc["media_type"],
        headers={
            # `attachment` + a sanitized name: the display filename is never
            # rendered inline, so a script-named file can't execute in a
            # reviewer's browser (v1.3 correction #8).
            "Content-Disposition": f'attachment; filename="{doc["display_name"]}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


class ConfirmDocumentFieldsIn(BaseModel):
    fields: Dict[str, Any]


@router.post("/documents/{document_id}/confirm")
async def confirm_document_endpoint(
    document_id: str, body: ConfirmDocumentFieldsIn, user=Depends(current_user),
):
    """extract -> review -> commit, the "commit" step. Whatever the AI or the
    text layer suggested, only what a human confirms here is treated as fact —
    and only confirmed values drive expiry."""
    doc, _audit, _decision = await _document_or_404(document_id, user, require_act=True)
    if not documents_service.is_visible_to_reviewer(doc["status"]):
        raise HTTPException(409, {"code": "DOCUMENT_NOT_READY",
                                  "message": f"document is {doc['status']}"})
    try:
        updated = await documents_service.confirm_fields(
            db, document_id, fields=body.fields, confirmed_by=user["id"])
    except documents_service.DocumentError as exc:
        raise _document_error(exc) from exc
    await write_audit(user, "remote_audit.document.confirmed",
                      f"Confirmed fields on document {document_id}", target_id=document_id)
    return serialize(updated)


class DecideDocumentIn(BaseModel):
    decision: str = Field(pattern="^(ACCEPTED|REJECTED)$")
    reason_code: Optional[str] = None
    note: Optional[str] = None


#: Document types whose whole purpose is an expiry date. Accepting one without
#: a confirmed due date would put a certificate into the record that no expiry
#: sweep can ever act on — it would silently never expire.
_EXPIRY_BEARING_TYPES = {"calibration_certificate", "preventive_maintenance", "inspection_certificate"}


@router.post("/documents/{document_id}/decide")
async def decide_document_endpoint(
    document_id: str,
    body: DecideDocumentIn,
    user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """The human decision. A document can only reach ACCEPTED through here, and
    only from a state whose checks actually completed — the transition table in
    documents.py is what enforces that, not this handler's good intentions."""
    doc, audit, decision_access = await _document_or_404(document_id, user, require_act=True)
    if decision_access.role_in_audit not in ("reviewer", "approver"):
        raise HTTPException(404, "Not found")

    scope = f"document.decide:{document_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None

    try:
        if body.decision == "REJECTED":
            if not body.reason_code:
                raise documents_service.DocumentError(
                    "REASON_CODE_REQUIRED", "a rejection must carry a reason code")
        else:
            latest = await documents_service.latest_check(db, document_id)
            if latest is None:
                raise documents_service.DocumentError(
                    "NO_COMPLETED_CHECKS",
                    "this document has no completed check run, so it cannot be accepted")
            malware_status = latest["checks"].get("malware_status")
            # NOT_SCANNED is acceptable ONLY where the operator has explicitly
            # switched scanning off (development). In production the flag is on
            # and this collapses back to "CLEAN or nothing".
            scan_ok = malware_status == "CLEAN" or (
                malware_status == "NOT_SCANNED" and not file_checks.scan_is_required())
            if not scan_ok:
                raise documents_service.DocumentError(
                    "MALWARE_SCAN_NOT_CLEAN",
                    "a document can only be accepted after a completed, clean malware scan")
            confirmed = doc.get("confirmed") or {}
            if doc["document_type"] in _EXPIRY_BEARING_TYPES and not confirmed.get("due_date"):
                raise documents_service.DocumentError(
                    "CONFIRMED_DUE_DATE_REQUIRED",
                    "this document type needs a reviewer-confirmed due date before it can be accepted")

        updated = await documents_service.transition(
            db, document_id, body.decision,
            extra={
                "decision_reason_code": body.reason_code,
                "decision_note": body.note,
                "decided_by": user["id"],
                "decided_at": now(),
            },
        )
    except documents_service.DocumentError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _document_error(exc) from exc

    if body.decision == "ACCEPTED" and doc.get("supersedes_document_id"):
        # Accepting a replacement retires the one it replaces, in the same
        # request — otherwise two "current" certificates coexist for a window.
        try:
            await documents_service.supersede(
                db, old_document_id=doc["supersedes_document_id"], new_document_id=document_id)
        except documents_service.DocumentError:
            pass  # the superseded document was already retired; not an error

    await write_audit(user, f"remote_audit.document.{body.decision.lower()}",
                      f"{body.decision} document {document_id}", target_id=document_id,
                      org_id=audit["audit_owner_org_id"])
    response = serialize(updated)
    await _complete_idempotent(scope, idempotency_key, 200, response)
    return response


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5 — Queries & CAPAs (spec §11)
# ═══════════════════════════════════════════════════════════════════════════
#
# Who may do what here follows the spec's separation of duties rather than a
# blanket "any participant can act": the sponsor side raises, rejects, closes
# and reopens queries and certifies CAPA effectiveness; the site side answers
# and does the work. Neither side can do the other's job, which is the point
# of having two of them.


def _query_error(exc: queries_service.QueryError) -> HTTPException:
    if exc.code == "NOT_FOUND":
        return HTTPException(404, "Not found")
    status = 409 if isinstance(exc, queries_service.InvalidQueryTransition) else 400
    if exc.code == "STALE_VERSION":
        status = 409
    return HTTPException(status, {"code": exc.code, "message": str(exc)})


_OWNER_SIDE = {"reviewer", "approver"}
_SITE_SIDE = {"capturer", "site_admin"}


async def _query_or_404(query_id: str, user: dict):
    query = await queries_service.get_query(db, query_id)
    if query is None:
        raise HTTPException(404, "Not found")
    audit = await _resolve_audit_or_404(query["audit_id"])
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return query, audit, decision


async def _capa_or_404(capa_id: str, user: dict):
    capa = await queries_service.get_capa(db, capa_id)
    if capa is None:
        raise HTTPException(404, "Not found")
    audit = await _resolve_audit_or_404(capa["audit_id"])
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return capa, audit, decision


def _require_role(decision, allowed: set) -> None:
    """404, never 403 — a caller must not learn a resource exists by being
    told they lack the role to touch it."""
    if not decision.can_act or decision.role_in_audit not in allowed:
        raise HTTPException(404, "Not found")


class CreateQueryIn(BaseModel):
    title: str
    detail: str = ""
    criticality: str = Field(default="minor", pattern="^(critical|major|minor)$")
    linked_entity_type: Optional[str] = None
    linked_entity_id: Optional[str] = None
    due_date: Optional[str] = None


@router.post("/audits/{audit_id}/queries")
async def create_query_endpoint(
    audit_id: str, body: CreateQueryIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    _require_role(decision, _OWNER_SIDE)

    scope = f"query.create:{audit_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None
    try:
        query = await queries_service.create_query(
            db, audit_id=audit_id, raised_by=user["id"], title=body.title, detail=body.detail,
            criticality=body.criticality, linked_entity_type=body.linked_entity_type,
            linked_entity_id=body.linked_entity_id, due_date=body.due_date)
    except queries_service.QueryError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _query_error(exc) from exc
    await write_audit(user, "remote_audit.query.raised", f"Raised query {query['id']}",
                      target_id=query["id"], org_id=audit["audit_owner_org_id"])
    response = serialize(query)
    await _complete_idempotent(scope, idempotency_key, 201, response)
    return response


@router.get("/audits/{audit_id}/queries")
async def list_queries_endpoint(audit_id: str, status: Optional[str] = None, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return serialize(await queries_service.list_queries(db, audit_id, status=status))


@router.get("/queries/{query_id}")
async def get_query_endpoint(query_id: str, user=Depends(current_user)):
    query, _audit, _decision = await _query_or_404(query_id, user)
    responses = await queries_service.list_responses(db, query_id)
    return serialize({**query, "responses": responses})


class RespondIn(BaseModel):
    body: str
    attachment_ids: list = []
    expected_version: Optional[int] = None


@router.post("/queries/{query_id}/responses")
async def respond_to_query_endpoint(
    query_id: str, body: RespondIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """The site answers. Responses are append-only — there is no edit path,
    because rewriting an answer a reviewer already read would change what the
    audit record says happened."""
    query, audit, decision = await _query_or_404(query_id, user)
    _require_role(decision, _SITE_SIDE)

    scope = f"query.respond:{query_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None
    try:
        response = await queries_service.add_response(
            db, query_id=query_id, author_id=user["id"], body=body.body,
            attachment_ids=body.attachment_ids, expected_version=body.expected_version)
    except queries_service.QueryError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _query_error(exc) from exc
    await write_audit(user, "remote_audit.query.responded", f"Responded to query {query_id}",
                      target_id=query_id, org_id=audit["site_org_id"])
    payload = serialize(response)
    await _complete_idempotent(scope, idempotency_key, 201, payload)
    return payload


class QueryDecisionIn(BaseModel):
    action: str = Field(pattern="^(CLOSE|REJECT|REOPEN|CANCEL)$")
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    expected_version: Optional[int] = None


@router.post("/queries/{query_id}/decision")
async def decide_query_endpoint(
    query_id: str, body: QueryDecisionIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Close, reject, reopen or cancel — all sponsor-side acts, each requiring
    a stated reason so the record is still interpretable months later."""
    query, audit, decision = await _query_or_404(query_id, user)
    _require_role(decision, _OWNER_SIDE)

    scope = f"query.decision:{query_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None
    try:
        if body.action == "CLOSE":
            result = await queries_service.close_query(
                db, query_id=query_id, reviewer_id=user["id"], reason_code=body.reason_code,
                expected_version=body.expected_version)
        elif body.action == "REJECT":
            result = await queries_service.reject_response(
                db, query_id=query_id, reviewer_id=user["id"], reason_code=body.reason_code or "",
                expected_version=body.expected_version)
        elif body.action == "REOPEN":
            result = await queries_service.reopen_query(
                db, query_id=query_id, reviewer_id=user["id"], reason=body.reason or "",
                expected_version=body.expected_version)
        else:
            result = await queries_service.cancel_query(
                db, query_id=query_id, reviewer_id=user["id"], reason=body.reason or "",
                expected_version=body.expected_version)
    except queries_service.QueryError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _query_error(exc) from exc

    await write_audit(user, f"remote_audit.query.{body.action.lower()}",
                      f"{body.action} on query {query_id}", target_id=query_id,
                      org_id=audit["audit_owner_org_id"])
    payload = serialize(result)
    await _complete_idempotent(scope, idempotency_key, 200, payload)
    return payload


# ── CAPAs ───────────────────────────────────────────────────────────────────

class CreateCapaIn(BaseModel):
    title: str
    description: str = ""
    owner_user_id: str
    due_date: str
    criticality: str = Field(default="minor", pattern="^(critical|major|minor)$")
    query_id: Optional[str] = None


@router.post("/audits/{audit_id}/capas")
async def create_capa_endpoint(
    audit_id: str, body: CreateCapaIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    _require_role(decision, _OWNER_SIDE)

    scope = f"capa.create:{audit_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None
    try:
        capa = await queries_service.create_capa(
            db, audit_id=audit_id, created_by=user["id"], title=body.title,
            description=body.description, owner_user_id=body.owner_user_id,
            due_date=body.due_date, criticality=body.criticality, query_id=body.query_id)
    except queries_service.QueryError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _query_error(exc) from exc
    await write_audit(user, "remote_audit.capa.created", f"Created CAPA {capa['id']}",
                      target_id=capa["id"], org_id=audit["audit_owner_org_id"])
    response = serialize({**capa, "is_overdue": queries_service.is_overdue(capa)})
    await _complete_idempotent(scope, idempotency_key, 201, response)
    return response


@router.get("/audits/{audit_id}/capas")
async def list_capas_endpoint(audit_id: str, status: Optional[str] = None, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    capas = await queries_service.list_capas(db, audit_id, status=status)
    # Overdue is computed, never stored — a persisted flag goes stale the
    # moment the clock passes it and nobody has run a sweep.
    return serialize([{**c, "is_overdue": queries_service.is_overdue(c)} for c in capas])


@router.get("/capas/{capa_id}")
async def get_capa_endpoint(capa_id: str, user=Depends(current_user)):
    capa, _audit, _decision = await _capa_or_404(capa_id, user)
    return serialize({**capa, "is_overdue": queries_service.is_overdue(capa)})


class CapaProgressIn(BaseModel):
    action: str = Field(pattern="^(START|COMPLETE|CANCEL)$")
    note: Optional[str] = None
    expected_version: Optional[int] = None


@router.post("/capas/{capa_id}/progress")
async def progress_capa_endpoint(
    capa_id: str, body: CapaProgressIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """The owner's side of a CAPA: start it, declare it done, or withdraw it.

    COMPLETE is the owner saying the work is finished. It is explicitly not
    closure — that needs someone else's effectiveness review.
    """
    capa, audit, decision = await _capa_or_404(capa_id, user)
    if not decision.can_act:
        raise HTTPException(404, "Not found")
    # The accountable owner drives progress. A sponsor-side participant may
    # withdraw a CAPA they raised, but may not mark someone else's work done.
    if body.action in ("START", "COMPLETE") and capa["owner_user_id"] != user["id"]:
        raise HTTPException(404, "Not found")
    if body.action == "CANCEL" and decision.role_in_audit not in _OWNER_SIDE:
        raise HTTPException(404, "Not found")

    scope = f"capa.progress:{capa_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None
    try:
        if body.action == "START":
            result = await queries_service.start_capa(
                db, capa_id=capa_id, actor_id=user["id"], expected_version=body.expected_version)
        elif body.action == "COMPLETE":
            result = await queries_service.complete_capa(
                db, capa_id=capa_id, actor_id=user["id"], note=body.note or "",
                expected_version=body.expected_version)
        else:
            result = await queries_service.cancel_capa(
                db, capa_id=capa_id, actor_id=user["id"], reason=body.note or "",
                expected_version=body.expected_version)
    except queries_service.QueryError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _query_error(exc) from exc

    await write_audit(user, f"remote_audit.capa.{body.action.lower()}",
                      f"{body.action} on CAPA {capa_id}", target_id=capa_id,
                      org_id=audit["audit_owner_org_id"])
    payload = serialize({**result, "is_overdue": queries_service.is_overdue(result)})
    await _complete_idempotent(scope, idempotency_key, 200, payload)
    return payload


class EffectivenessReviewIn(BaseModel):
    outcome: str = Field(pattern="^(EFFECTIVE|INEFFECTIVE)$")
    note: str
    expected_version: Optional[int] = None


@router.post("/capas/{capa_id}/effectiveness-review")
async def review_capa_effectiveness_endpoint(
    capa_id: str, body: EffectivenessReviewIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """The only route to CLOSED. An INEFFECTIVE review reopens the CAPA rather
    than letting the finding quietly disappear."""
    capa, audit, decision = await _capa_or_404(capa_id, user)
    _require_role(decision, _OWNER_SIDE)

    scope = f"capa.effectiveness:{capa_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None
    try:
        result = await queries_service.record_effectiveness_review(
            db, capa_id=capa_id, reviewer_id=user["id"], outcome=body.outcome,
            note=body.note, expected_version=body.expected_version)
    except queries_service.QueryError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _query_error(exc) from exc

    await write_audit(user, "remote_audit.capa.effectiveness_reviewed",
                      f"{body.outcome} effectiveness review on CAPA {capa_id}",
                      target_id=capa_id, org_id=audit["audit_owner_org_id"])
    payload = serialize({**result, "is_overdue": queries_service.is_overdue(result)})
    await _complete_idempotent(scope, idempotency_key, 200, payload)
    return payload


@router.get("/audits/{audit_id}/capas/overdue")
async def list_overdue_capas_endpoint(audit_id: str, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return serialize(await queries_service.overdue_capas(db, audit_id=audit_id))


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6 — Virtual visits (spec §12)
# ═══════════════════════════════════════════════════════════════════════════
#
# Env-gated end to end. With no LiveKit credentials configured, scheduling and
# joining return LIVEKIT_UNAVAILABLE and every other part of the audit keeps
# working (v1.3 correction #15) — video being down must not take documents,
# queries, evidence and scoring down with it.


def _virtual_error(exc: virtual_visits.VirtualVisitError) -> HTTPException:
    if exc.code == "NOT_FOUND":
        return HTTPException(404, "Not found")
    if isinstance(exc, virtual_visits.LiveKitUnavailable):
        return HTTPException(503, {"code": exc.code, "message": str(exc)})
    if exc.code in ("INVALID_TRANSITION", "SESSION_ENDED", "SESSION_CANCELLED", "SESSION_NOT_LIVE",
                    "SESSION_NOT_ACTIVE"):
        return HTTPException(409, {"code": exc.code, "message": str(exc)})
    if exc.code in ("CONSENT_REQUIRED", "RECORDING_NOT_PERMITTED"):
        return HTTPException(403, {"code": exc.code, "message": str(exc)})
    return HTTPException(400, {"code": exc.code, "message": str(exc)})


async def _session_or_404(session_id: str, user: dict):
    session = await virtual_visits.get_session(db, session_id)
    if session is None:
        raise HTTPException(404, "Not found")
    audit = await _resolve_audit_or_404(session["audit_id"])
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return session, audit, decision


class ScheduleSessionIn(BaseModel):
    scheduled_start_utc: datetime
    privacy_mode: str = Field(
        default="SERVER_NO_RECORDING",
        pattern="^(E2EE_NO_RECORDING|SERVER_RECORDED|SERVER_NO_RECORDING)$")
    consent_required: bool = True
    expected_duration_minutes: int = Field(default=60, ge=5, le=480)
    purpose: str = ""


@router.post("/audits/{audit_id}/virtual-sessions")
async def schedule_virtual_session_endpoint(
    audit_id: str, body: ScheduleSessionIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Schedule a session with its privacy mode already fixed.

    The mode is chosen here and never changeable afterwards — participants
    consent to a stated privacy posture, and a posture that can change after
    they agreed to it is not one they agreed to.
    """
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    _require_role(decision, _OWNER_SIDE)

    scope = f"virtual_session.schedule:{audit_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump(mode="json"))
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None
    try:
        session = await virtual_visits.schedule_session(
            db, audit_id=audit_id, scheduled_by=user["id"],
            scheduled_start_utc=body.scheduled_start_utc, privacy_mode=body.privacy_mode,
            consent_required=body.consent_required,
            expected_duration_minutes=body.expected_duration_minutes, purpose=body.purpose)
    except virtual_visits.VirtualVisitError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _virtual_error(exc) from exc

    await write_audit(user, "remote_audit.virtual_session.scheduled",
                      f"Scheduled virtual session {session['id']} ({session['privacy_mode']})",
                      target_id=session["id"], org_id=audit["audit_owner_org_id"])
    response = serialize({**session, "livekit_configured": virtual_visits.livekit_configured()})
    await _complete_idempotent(scope, idempotency_key, 201, response)
    return response


@router.get("/audits/{audit_id}/virtual-sessions")
async def list_virtual_sessions_endpoint(audit_id: str, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    sessions = await virtual_visits.list_sessions(db, audit_id)
    return serialize({"sessions": sessions,
                      "livekit_configured": virtual_visits.livekit_configured()})


@router.get("/virtual-sessions/{session_id}")
async def get_virtual_session_endpoint(session_id: str, user=Depends(current_user)):
    session, _audit, _decision = await _session_or_404(session_id, user)
    observations = await virtual_visits.list_observations(db, session_id)
    return serialize({**session, "observations": observations,
                      "livekit_configured": virtual_visits.livekit_configured()})


class ConsentIn(BaseModel):
    granted: bool
    #: What the UI actually told this participant. Checked against the
    #: session's real mode so consent to a no-recording call can never be
    #: applied to a recorded one.
    privacy_mode_shown: str


@router.post("/virtual-sessions/{session_id}/consent")
async def record_consent_endpoint(
    session_id: str, body: ConsentIn, user=Depends(current_user),
):
    session, audit, decision = await _session_or_404(session_id, user)
    if not decision.can_act:
        raise HTTPException(404, "Not found")
    try:
        updated = await virtual_visits.record_consent(
            db, session_id=session_id, user_id=user["id"], granted=body.granted,
            privacy_mode_shown=body.privacy_mode_shown)
    except virtual_visits.VirtualVisitError as exc:
        raise _virtual_error(exc) from exc
    await write_audit(user, "remote_audit.virtual_session.consent",
                      f"Consent {'granted' if body.granted else 'declined'} for session {session_id}",
                      target_id=session_id, org_id=audit["audit_owner_org_id"])
    return serialize(updated)


class JoinIn(BaseModel):
    role: str = Field(default="presenter", pattern="^(host|presenter|observer)$")


@router.post("/virtual-sessions/{session_id}/join")
async def join_virtual_session_endpoint(
    session_id: str, body: JoinIn, user=Depends(current_user),
):
    """Mint a short-lived, role-bound participant token.

    Refused before consent is recorded — that is the gate v1.3 correction #4
    actually specifies, and it sits here rather than in the client.
    """
    session, audit, decision = await _session_or_404(session_id, user)
    if not decision.can_act:
        raise HTTPException(404, "Not found")
    # Only the sponsor side hosts. A site participant cannot admit others into
    # a call about their own site.
    role = body.role
    if role == "host" and decision.role_in_audit not in _OWNER_SIDE:
        raise HTTPException(404, "Not found")
    try:
        token = await virtual_visits.issue_join_token(
            db, session_id=session_id, user_id=user["id"],
            display_name=user.get("full_name") or user["id"], role=role)
    except virtual_visits.VirtualVisitError as exc:
        obs.emit_metric(obs.Metric.VIRTUAL_SESSION_JOIN_FAILED, tags={"code": exc.code})
        raise _virtual_error(exc) from exc

    obs.emit_metric(obs.Metric.VIRTUAL_SESSION_JOIN, tags={"role": role})
    await write_audit(user, "remote_audit.virtual_session.joined",
                      f"Joined virtual session {session_id} as {role}",
                      target_id=session_id, org_id=audit["audit_owner_org_id"])
    return {
        "token": token.token, "url": token.url, "room": token.room,
        "identity": token.identity, "role": token.role, "can_publish": token.can_publish,
        "expires_at": token.expires_at.isoformat(),
        "privacy_mode": session["privacy_mode"],
        "recording_enabled": session["recording_enabled"],
    }


class SessionStateIn(BaseModel):
    action: str = Field(pattern="^(GO_LIVE|END|CANCEL)$")


@router.post("/virtual-sessions/{session_id}/state")
async def change_virtual_session_state_endpoint(
    session_id: str, body: SessionStateIn, user=Depends(current_user),
):
    session, audit, decision = await _session_or_404(session_id, user)
    _require_role(decision, _OWNER_SIDE)
    target = {"GO_LIVE": "LIVE", "END": "ENDED", "CANCEL": "CANCELLED"}[body.action]
    extra = {"started_at": now()} if target == "LIVE" else (
        {"ended_at": now()} if target == "ENDED" else {})
    try:
        updated = await virtual_visits.transition_session(db, session_id, target, extra=extra)
    except virtual_visits.VirtualVisitError as exc:
        raise _virtual_error(exc) from exc
    await write_audit(user, f"remote_audit.virtual_session.{target.lower()}",
                      f"Session {session_id} -> {target}", target_id=session_id,
                      org_id=audit["audit_owner_org_id"])
    return serialize(updated)


@router.post("/virtual-sessions/{session_id}/recording")
async def start_recording_endpoint(session_id: str, user=Depends(current_user)):
    """Start server-side recording.

    Refused unless the session's FIXED privacy mode permits it. An E2EE room's
    media is ciphertext to the server, so this is not a policy preference — it
    is a statement about what is possible.
    """
    session, audit, decision = await _session_or_404(session_id, user)
    _require_role(decision, _OWNER_SIDE)
    try:
        updated = await virtual_visits.start_recording(db, session_id, started_by=user["id"])
    except virtual_visits.VirtualVisitError as exc:
        raise _virtual_error(exc) from exc
    await write_audit(user, "remote_audit.virtual_session.recording_started",
                      f"Recording started on session {session_id}", target_id=session_id,
                      org_id=audit["audit_owner_org_id"])
    return serialize(updated)


class ObservationIn(BaseModel):
    body: str
    linked_entity_type: Optional[str] = None
    linked_entity_id: Optional[str] = None


@router.post("/virtual-sessions/{session_id}/observations")
async def add_observation_endpoint(
    session_id: str, body: ObservationIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Append an observation made during the call. Append-only, like query
    responses — what a reviewer noted during a call is part of the record."""
    session, audit, decision = await _session_or_404(session_id, user)
    _require_role(decision, _OWNER_SIDE)

    scope = f"virtual_observation:{session_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None
    try:
        observation = await virtual_visits.add_observation(
            db, session_id=session_id, author_id=user["id"], body=body.body,
            linked_entity_type=body.linked_entity_type, linked_entity_id=body.linked_entity_id)
    except virtual_visits.VirtualVisitError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _virtual_error(exc) from exc
    await write_audit(user, "remote_audit.virtual_session.observation",
                      f"Observation on session {session_id}", target_id=session_id,
                      org_id=audit["audit_owner_org_id"])
    payload = serialize(observation)
    await _complete_idempotent(scope, idempotency_key, 201, payload)
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 7 — Scoring & final decision (spec §13)
# ═══════════════════════════════════════════════════════════════════════════


def _scoring_error(exc: scoring.ScoringError) -> HTTPException:
    return HTTPException(400, {"code": exc.code, "message": str(exc)})


#: Axis names a template may use for the compliance axis. The template model
#: (templates.py) stores `scoring_policy_json` as {axis_name: {factor: weight}}
#: with each axis's factors summing to 100 — the COMPLIANCE axis's factors are
#: this module's sections. The evidence-confidence axis is computed from
#: per-requirement evidence verdicts instead, never from these weights, which
#: is what keeps the two axes from silently merging.
_COMPLIANCE_AXIS_NAMES = ("compliance", "ComplianceScore", "compliance_score", "COMPLIANCE")


def _policy_from_template(template: Dict[str, Any]) -> scoring.ScoringPolicy:
    """Build a scoring policy from an activated template.

    Only an ACTIVATED template may be used: activation is where
    `validate_scoring_policy_weights` runs, and scoring against an unvalidated
    policy would produce a number that looks like a percentage but isn't.

    Section minimums are not expressible in the axis/factor shape, so they come
    from an optional top-level `section_minimums` field rather than being
    invented here.
    """
    raw = template.get("scoring_policy_json") or {}
    axis = next((raw[name] for name in _COMPLIANCE_AXIS_NAMES if name in raw), None)
    if axis is None:
        # A single-axis template is unambiguous; more than one without a
        # recognizable compliance axis is not, and guessing would silently
        # score the audit against the wrong weights.
        if len(raw) == 1:
            axis = next(iter(raw.values()))
        else:
            raise scoring.ScoringError(
                "NO_COMPLIANCE_AXIS",
                "this template defines no compliance axis, so its section weights are ambiguous")

    minimums = template.get("section_minimums") or {}
    sections = [
        scoring.SectionPolicy(
            section_id=section_id,
            weight_percent=float(weight),
            minimum_percent=(float(minimums[section_id]) if section_id in minimums else None),
        )
        for section_id, weight in axis.items()
    ]
    return scoring.ScoringPolicy(sections=sections)


class RequirementResultIn(BaseModel):
    requirement_id: str
    section_id: str
    outcome: str = Field(pattern="^(MET|PARTIAL|NOT_MET|NOT_APPLICABLE|MISSING|WAIVED)$")
    is_critical: bool = False
    weight: float = Field(default=1.0, gt=0)
    evidence_confidence: Optional[str] = Field(
        default=None, pattern="^(HIGH|LIMITED|UNVERIFIED|FAILED)$")
    waiver_reason: Optional[str] = None
    note: Optional[str] = None


class ScoreAuditIn(BaseModel):
    template_id: str
    results: List[RequirementResultIn]


@router.post("/audits/{audit_id}/score")
async def score_audit_endpoint(
    audit_id: str, body: ScoreAuditIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Run the two-axis engine and store the result.

    Returns both axes separately — compliance and evidence confidence answer
    different questions and are never blended into one number.
    """
    audit = await _resolve_audit_or_404(audit_id)
    decision_access = await _access_decision(audit, user)
    _require_role(decision_access, _OWNER_SIDE)

    template = await templates_service.get_template(db, body.template_id)
    if template is None or template.get("status") != "active":
        raise HTTPException(400, {"code": "TEMPLATE_NOT_ACTIVE",
                                  "message": "scoring requires an activated template"})

    scope = f"audit.score:{audit_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None

    try:
        policy = _policy_from_template(template)
        results = [
            scoring.RequirementResult(
                requirement_id=r.requirement_id, section_id=r.section_id,
                outcome=scoring.RequirementOutcome(r.outcome), is_critical=r.is_critical,
                weight=r.weight,
                evidence_confidence=(scoring.EvidenceConfidence(r.evidence_confidence)
                                     if r.evidence_confidence else None),
                waiver_reason=r.waiver_reason, note=r.note)
            for r in body.results
        ]
        open_critical = await queries_service.open_critical_capas(db, audit_id)
        result = scoring.score_audit(
            policy=policy, results=results,
            open_critical_capa_ids=[c["id"] for c in open_critical])
    except scoring.ScoringError as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise _scoring_error(exc) from exc

    run = await decisions_service.record_score_run(
        db, audit_id=audit_id, scored_by=user["id"], result=result.to_dict(),
        policy_snapshot={"template_id": template["id"], "template_version": template.get("version"),
                         "scoring_policy_json": template.get("scoring_policy_json")})
    await write_audit(user, "remote_audit.audit.scored",
                      f"Scored audit {audit_id} (run {run['score_run_id']})",
                      target_id=audit_id, org_id=audit["audit_owner_org_id"])
    payload = serialize(run)
    await _complete_idempotent(scope, idempotency_key, 201, payload)
    return payload


@router.get("/audits/{audit_id}/score")
async def get_audit_score_endpoint(audit_id: str, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return serialize({
        "latest": await decisions_service.latest_score_run(db, audit_id),
        "history": await decisions_service.list_score_runs(db, audit_id),
    })


class FinalDecisionIn(BaseModel):
    decision: str = Field(pattern="^(APPROVED|APPROVED_WITH_CONDITIONS|NOT_APPROVED)$")
    step_up_token: str
    override_reason: Optional[str] = None
    linked_capa_ids: List[str] = []
    signature_statement: str = ""


@router.post("/audits/{audit_id}/decision")
async def record_final_decision_endpoint(
    audit_id: str, body: FinalDecisionIn, user=Depends(current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """The final decision.

    Requires the approver role on THIS audit plus a step-up token (MFA-backed
    in production, per v1.3 correction #2). A conditional approval must link
    real CAPAs with owners and due dates — "approved with conditions" and no
    conditions is an unconditional approval wearing a hat.
    """
    audit = await _resolve_audit_or_404(audit_id)
    access = await _access_decision(audit, user)
    if not access.can_act or access.role_in_audit != "approver":
        raise HTTPException(404, "Not found")

    if not await stepup_service.consume_stepup_token(
        db, token=body.step_up_token, user_id=user["id"], action_scope="audit.decision",
    ):
        raise HTTPException(403, {"code": "STEP_UP_REQUIRED",
                                  "message": "a valid step-up token is required to sign off an audit"})

    scope = f"audit.decision:{audit_id}"
    replay = await _begin_idempotent(scope, idempotency_key, body.model_dump())
    if replay is not None:
        return replay.response_body
    assert idempotency_key is not None

    run = await decisions_service.latest_score_run(db, audit_id)
    if run is None:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise HTTPException(400, {"code": "NOT_SCORED",
                                  "message": "this audit has not been scored, so it cannot be decided"})

    try:
        capas = await queries_service.list_capas(db, audit_id)
        scoring.conditional_approval_is_valid(
            scoring.Decision(body.decision), body.linked_capa_ids,
            {c["id"]: c for c in capas})
        machine_result = {
            "proposed_decision": run["proposed_decision"],
            "compliance_percent": run["compliance_percent"],
            "evidence_confidence_percent": run["evidence_confidence_percent"],
            "blockers": run["blockers"],
            "algorithm_version": run["algorithm_version"],
            "score_run_id": run["score_run_id"],
        }
        record = await decisions_service.record_decision(
            db, audit_id=audit_id, decided_by=user["id"], decision=body.decision,
            machine_result=machine_result, override_reason=body.override_reason,
            linked_capa_ids=body.linked_capa_ids, signature_statement=body.signature_statement)
    except (scoring.ScoringError, decisions_service.DecisionError) as exc:
        await _idempotency.release_without_completing(scope=scope, key=idempotency_key)
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from exc

    if record["overridden"]:
        # A high override rate is a signal about the RULES, not about the
        # reviewers — which is why this is measured rather than just audited.
        obs.emit_metric(obs.Metric.MANUAL_OVERRIDE,
                        tags={"audit_id": audit_id, "machine": record["machine_proposed"],
                              "human": body.decision})
    await write_audit(user, "remote_audit.audit.decided",
                      f"{body.decision} on audit {audit_id}"
                      + (" (override)" if record["overridden"] else ""),
                      target_id=audit_id, org_id=audit["audit_owner_org_id"])
    payload = serialize(record)
    await _complete_idempotent(scope, idempotency_key, 201, payload)
    return payload


@router.get("/audits/{audit_id}/decision")
async def get_audit_decision_endpoint(audit_id: str, user=Depends(current_user)):
    audit = await _resolve_audit_or_404(audit_id)
    decision = await _access_decision(audit, user)
    if not decision.can_view_details:
        raise HTTPException(404, "Not found")
    return serialize({
        "latest": await decisions_service.latest_decision(db, audit_id),
        "history": await decisions_service.list_decisions(db, audit_id),
    })



@router.get("/audits/{audit_id}/report.pdf")
async def audit_report_endpoint(audit_id: str, user=Depends(current_user)):
    """The report a sponsor hands to a regulator.

    Blockers print before the score and the two axes print as two numbers, so
    a reader who stops at the headline has already seen what qualifies it.
    Generated on demand rather than stored: the audit record is the source of
    truth, and a cached PDF would quietly go stale against it.
    """
    audit = await _resolve_audit_or_404(audit_id)
    access = await _access_decision(audit, user)
    if not access.can_view_details:
        raise HTTPException(404, "Not found")

    pdf = report_service.build_audit_report_pdf(
        audit=audit,
        score_run=await decisions_service.latest_score_run(db, audit_id),
        decision=await decisions_service.latest_decision(db, audit_id),
        queries=await queries_service.list_queries(db, audit_id),
        capas=await queries_service.list_capas(db, audit_id),
        generated_by=user.get("full_name") or user["id"],
    )
    await write_audit(user, "remote_audit.report.generated",
                      f"Generated the report for audit {audit_id}", target_id=audit_id,
                      org_id=audit["audit_owner_org_id"])
    from fastapi.responses import Response

    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="remote-audit-{audit_id}.pdf"',
                 "X-Content-Type-Options": "nosniff"})


# ── Audit overview list (eventual landing page for the tab replacing Notifs —
# navigation itself is wired only in Phase 11, per standing instruction) ────

@router.get("/audits")
async def list_audits_endpoint(user=Depends(current_user)):
    org_id = _require_org_id(user)
    cursor = db[audits_service.AUDITS_COLLECTION].find(
        {"$or": [{"audit_owner_org_id": org_id}, {"site_org_id": org_id}]},
        {"_id": 0, "id": 1, "state": 1, "site_id": 1, "audit_owner_org_id": 1, "site_org_id": 1},
    )
    return await cursor.to_list(500)
