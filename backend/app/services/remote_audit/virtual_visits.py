"""Virtual visit sessions — spec §12, Production-Grade Implementation Plan
v1.3 Phase 6.

Four properties this module exists to guarantee, each of which is a privacy
control rather than a feature:

1. **E2EE and server-side recording are mutually exclusive, structurally.**
   They are not two checkboxes a careless caller can both tick — a room's
   `privacy_mode` is ONE value, and requesting egress on an E2EE room is
   refused by `assert_recording_allowed()`. A server cannot record media it
   cannot decrypt, so a UI that offered both would be lying about one of them.

2. **Recording policy is fixed BEFORE the room exists.** Spec §12.2: the mode
   is decided at scheduling time and frozen. Flipping recording on mid-call
   would mean participants consented to one thing and got another.

3. **Consent gates joining, not scheduling** (v1.3 correction #4). A session
   must exist as a record to be scheduled at all, so it is created with
   `consent_status=PENDING`; what consent gates is any participant joining,
   any media flowing, and any recording starting.

4. **Tokens are bound to a person, a room, and a role.** A participant token
   is minted for one authenticated user on one session, is short-lived, and
   carries only the grants that user's role needs. A viewer-role token cannot
   publish; nobody's token can start egress.

LiveKit is env-gated throughout: with no credentials configured, scheduling
and joining report LIVEKIT_UNAVAILABLE and the rest of the audit — documents,
queries, evidence, scoring — keeps working (v1.3 correction #15).
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

SESSIONS_COLLECTION = "remote_audit_virtual_sessions"
OBSERVATIONS_COLLECTION = "remote_audit_virtual_observations"

LIVEKIT_URL_ENV = "LIVEKIT_URL"
LIVEKIT_API_KEY_ENV = "LIVEKIT_API_KEY"
LIVEKIT_API_SECRET_ENV = "LIVEKIT_API_SECRET"

DEFAULT_TOKEN_TTL_SECONDS = 900  # 15 minutes — long enough to join, short enough to matter


class PrivacyMode(str, Enum):
    """ONE value per session. The whole point is that these cannot coexist."""
    #: End-to-end encrypted. The server relays ciphertext it cannot read, so
    #: recording is not merely disallowed by policy — it is impossible.
    E2EE_NO_RECORDING = "E2EE_NO_RECORDING"
    #: Server-side media, recorded to the evidence store under the audit's
    #: retention policy. Every participant is told, before joining.
    SERVER_RECORDED = "SERVER_RECORDED"
    #: Server-side media, not recorded. The default.
    SERVER_NO_RECORDING = "SERVER_NO_RECORDING"


#: The only mode in which an egress/recording request is legitimate.
RECORDING_MODES = frozenset({PrivacyMode.SERVER_RECORDED})

DEFAULT_PRIVACY_MODE = PrivacyMode.SERVER_NO_RECORDING


class ConsentStatus(str, Enum):
    PENDING = "PENDING"
    GRANTED = "GRANTED"
    DECLINED = "DECLINED"
    NOT_REQUIRED = "NOT_REQUIRED"


#: Consent states that permit a participant to join and media to flow.
JOINABLE_CONSENT = frozenset({ConsentStatus.GRANTED, ConsentStatus.NOT_REQUIRED})


class SessionState(str, Enum):
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"


SESSION_TRANSITIONS: Dict[str, set] = {
    SessionState.SCHEDULED.value: {SessionState.LIVE.value, SessionState.CANCELLED.value},
    SessionState.LIVE.value: {SessionState.ENDED.value},
    SessionState.ENDED.value: set(),
    SessionState.CANCELLED.value: set(),
}

#: Roles a participant may hold in a call, and what each may do. `observer`
#: exists so a second reviewer can watch without becoming another camera in
#: a patient-care area.
PARTICIPANT_ROLES = {
    "host": {"publish": True, "subscribe": True, "admit": True},
    "presenter": {"publish": True, "subscribe": True, "admit": False},
    "observer": {"publish": False, "subscribe": True, "admit": False},
}


class VirtualVisitError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LiveKitUnavailable(VirtualVisitError):
    def __init__(self, message: str = "video conferencing is not configured for this deployment"):
        super().__init__("LIVEKIT_UNAVAILABLE", message)


@dataclass(frozen=True)
class ParticipantToken:
    token: str
    url: str
    room: str
    identity: str
    role: str
    expires_at: datetime
    can_publish: bool


def livekit_configured() -> bool:
    return bool(
        os.environ.get(LIVEKIT_URL_ENV)
        and os.environ.get(LIVEKIT_API_KEY_ENV)
        and os.environ.get(LIVEKIT_API_SECRET_ENV)
    )


def assert_recording_allowed(privacy_mode: str) -> None:
    """The structural guard against the one combination that must never exist.

    Called before any egress request. An E2EE room's media is ciphertext to the
    server, so "record it anyway" would either fail silently or produce
    unusable files while the UI claimed a recording existed — both worse than
    an explicit refusal.
    """
    try:
        mode = PrivacyMode(privacy_mode)
    except ValueError as exc:
        raise VirtualVisitError("UNKNOWN_PRIVACY_MODE", f"{privacy_mode} is not a privacy mode") from exc
    if mode not in RECORDING_MODES:
        raise VirtualVisitError(
            "RECORDING_NOT_PERMITTED",
            f"this session is {mode.value}; recording is not permitted and, for E2EE, not possible")


def consent_permits_join(session: Dict[str, Any]) -> bool:
    """v1.3 correction #4. A session may be SCHEDULED with consent PENDING —
    the record has to exist to be scheduled. What consent gates is joining."""
    try:
        return ConsentStatus(session.get("consent_status", "PENDING")) in JOINABLE_CONSENT
    except ValueError:
        return False


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[SESSIONS_COLLECTION].create_index("id", unique=True)
    await db[SESSIONS_COLLECTION].create_index("audit_id")
    await db[SESSIONS_COLLECTION].create_index("room_name", unique=True)
    await db[SESSIONS_COLLECTION].create_index([("audit_id", 1), ("state", 1)])
    await db[OBSERVATIONS_COLLECTION].create_index("session_id")
    await db[OBSERVATIONS_COLLECTION].create_index([("session_id", 1), ("sequence", 1)], unique=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def schedule_session(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    scheduled_by: str,
    scheduled_start_utc: datetime,
    privacy_mode: str = DEFAULT_PRIVACY_MODE.value,
    consent_required: bool = True,
    expected_duration_minutes: int = 60,
    purpose: str = "",
) -> Dict[str, Any]:
    """Create a scheduled session with its recording policy already fixed.

    `privacy_mode` is set here and never changed afterwards — there is no
    update path for it, deliberately. Participants consent to a stated privacy
    posture, and a posture that can change after they agreed to it is not one
    they agreed to.
    """
    try:
        mode = PrivacyMode(privacy_mode)
    except ValueError as exc:
        raise VirtualVisitError("UNKNOWN_PRIVACY_MODE", f"{privacy_mode} is not a privacy mode") from exc

    now = _now()
    session_id = str(uuid.uuid4())
    doc = {
        "id": session_id,
        "audit_id": audit_id,
        # Opaque and unguessable: a room name is effectively a shared secret
        # for anyone who can reach the LiveKit server.
        "room_name": f"ra-{audit_id[:8]}-{uuid.uuid4().hex}",
        "state": SessionState.SCHEDULED.value,
        "privacy_mode": mode.value,
        "recording_enabled": mode in RECORDING_MODES,
        "consent_required": consent_required,
        "consent_status": (ConsentStatus.PENDING if consent_required
                           else ConsentStatus.NOT_REQUIRED).value,
        "consent_records": [],
        "scheduled_start_utc": scheduled_start_utc,
        "expected_duration_minutes": expected_duration_minutes,
        "purpose": (purpose or "").strip(),
        "scheduled_by": scheduled_by,
        "started_at": None,
        "ended_at": None,
        "recording_object_key": None,
        "recording_egress_id": None,
        "participants_joined": [],
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }
    await db[SESSIONS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_session(db: AsyncIOMotorDatabase, session_id: str) -> Optional[Dict[str, Any]]:
    return await db[SESSIONS_COLLECTION].find_one({"id": session_id}, {"_id": 0})


async def list_sessions(db: AsyncIOMotorDatabase, audit_id: str) -> List[Dict[str, Any]]:
    return await db[SESSIONS_COLLECTION].find(
        {"audit_id": audit_id}, {"_id": 0}).sort("scheduled_start_utc", 1).to_list(None)


async def record_consent(
    db: AsyncIOMotorDatabase,
    *,
    session_id: str,
    user_id: str,
    granted: bool,
    privacy_mode_shown: str,
) -> Dict[str, Any]:
    """Record one participant's consent decision.

    `privacy_mode_shown` is what the UI actually told them, and it is checked
    against the session's real mode. If they consented to a no-recording call
    and the session is recorded, that consent is not consent to this call —
    the mismatch is refused rather than quietly accepted.
    """
    session = await get_session(db, session_id)
    if session is None:
        raise VirtualVisitError("NOT_FOUND", "session not found")
    if session["state"] not in (SessionState.SCHEDULED.value, SessionState.LIVE.value):
        raise VirtualVisitError("SESSION_NOT_ACTIVE", f"session is {session['state']}")
    if privacy_mode_shown != session["privacy_mode"]:
        raise VirtualVisitError(
            "CONSENT_MODE_MISMATCH",
            "consent was given for a different privacy mode than this session uses")

    record = {
        "user_id": user_id,
        "granted": granted,
        "privacy_mode_shown": privacy_mode_shown,
        "recorded_at": _now(),
    }
    # A single decline blocks the call. Consent is not a majority vote.
    new_status = ConsentStatus.DECLINED.value if not granted else ConsentStatus.GRANTED.value
    if session["consent_status"] == ConsentStatus.DECLINED.value and granted:
        # Someone previously declined; one later approval does not clear it.
        new_status = ConsentStatus.DECLINED.value

    result = await db[SESSIONS_COLLECTION].find_one_and_update(
        {"id": session_id},
        {"$push": {"consent_records": record},
         "$set": {"consent_status": new_status, "updated_at": _now()},
         "$inc": {"version": 1}},
        projection={"_id": 0}, return_document=True,
    )
    return result


def build_participant_grants(role: str) -> Dict[str, bool]:
    grants = PARTICIPANT_ROLES.get(role)
    if grants is None:
        raise VirtualVisitError("UNKNOWN_PARTICIPANT_ROLE", f"{role} is not a call role")
    return dict(grants)


def mint_participant_token(
    *,
    room_name: str,
    identity: str,
    display_name: str,
    role: str,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
) -> ParticipantToken:
    """Mint a short-lived, role-bound LiveKit access token.

    Note what is NOT granted to anyone: `roomAdmin` and `roomRecord`. Egress is
    started server-side, by the API, after `assert_recording_allowed` — never
    by a client holding a token, however privileged the person is.
    """
    if not livekit_configured():
        raise LiveKitUnavailable()
    grants = build_participant_grants(role)

    from livekit import api as livekit_api

    ttl = timedelta(seconds=ttl_seconds)
    token = (
        livekit_api.AccessToken(os.environ[LIVEKIT_API_KEY_ENV], os.environ[LIVEKIT_API_SECRET_ENV])
        .with_identity(identity)
        .with_name(display_name)
        .with_ttl(ttl)
        .with_grants(livekit_api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=grants["publish"],
            can_subscribe=grants["subscribe"],
            can_publish_data=True,
            # Deliberately absent: room_admin / room_record / room_create.
            # A participant token must never be able to start a recording or
            # reconfigure the room it is joining.
        ))
    )
    return ParticipantToken(
        token=token.to_jwt(),
        url=os.environ[LIVEKIT_URL_ENV],
        room=room_name,
        identity=identity,
        role=role,
        expires_at=_now() + ttl,
        can_publish=grants["publish"],
    )


async def issue_join_token(
    db: AsyncIOMotorDatabase,
    *,
    session_id: str,
    user_id: str,
    display_name: str,
    role: str,
) -> ParticipantToken:
    """The gate every join passes through.

    Every refusal below is a privacy control, not a convenience check: consent
    before join, no joining an ended session, and no token at all when the
    provider is unconfigured.
    """
    session = await get_session(db, session_id)
    if session is None:
        raise VirtualVisitError("NOT_FOUND", "session not found")
    if session["state"] == SessionState.CANCELLED.value:
        raise VirtualVisitError("SESSION_CANCELLED", "this session was cancelled")
    if session["state"] == SessionState.ENDED.value:
        raise VirtualVisitError("SESSION_ENDED", "this session has ended")
    if not consent_permits_join(session):
        raise VirtualVisitError(
            "CONSENT_REQUIRED",
            "no participant may join and no media may flow until consent is recorded")

    token = mint_participant_token(
        room_name=session["room_name"], identity=user_id,
        display_name=display_name, role=role)

    await db[SESSIONS_COLLECTION].update_one(
        {"id": session_id},
        {"$addToSet": {"participants_joined": user_id},
         "$set": {"updated_at": _now()}},
    )
    return token


async def transition_session(
    db: AsyncIOMotorDatabase, session_id: str, target: str, *, extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session = await get_session(db, session_id)
    if session is None:
        raise VirtualVisitError("NOT_FOUND", "session not found")
    current = session["state"]
    if target not in SESSION_TRANSITIONS.get(current, set()):
        raise VirtualVisitError("INVALID_TRANSITION", f"a session cannot move from {current} to {target}")
    if target == SessionState.LIVE.value and not consent_permits_join(session):
        raise VirtualVisitError(
            "CONSENT_REQUIRED", "a session cannot go live before consent is recorded")

    updates = {"state": target, "updated_at": _now()}
    updates.update(extra or {})
    result = await db[SESSIONS_COLLECTION].find_one_and_update(
        {"id": session_id, "state": current, "version": session["version"]},
        {"$set": updates, "$inc": {"version": 1}},
        projection={"_id": 0}, return_document=True,
    )
    if result is None:
        raise VirtualVisitError("INVALID_TRANSITION", "the session changed while you were acting on it")
    return result


async def start_recording(db: AsyncIOMotorDatabase, session_id: str, *, started_by: str) -> Dict[str, Any]:
    """Begin server-side egress. Refuses unless the session's fixed privacy
    mode permits it AND consent is recorded — both, not either."""
    session = await get_session(db, session_id)
    if session is None:
        raise VirtualVisitError("NOT_FOUND", "session not found")
    assert_recording_allowed(session["privacy_mode"])
    if not consent_permits_join(session):
        raise VirtualVisitError("CONSENT_REQUIRED", "recording cannot start before consent is recorded")
    if session["state"] != SessionState.LIVE.value:
        raise VirtualVisitError("SESSION_NOT_LIVE", f"session is {session['state']}")
    if not livekit_configured():
        raise LiveKitUnavailable()

    object_key = f"remote-audit/{session['audit_id']}/virtual-sessions/{session_id}.mp4"
    result = await db[SESSIONS_COLLECTION].find_one_and_update(
        {"id": session_id},
        {"$set": {"recording_object_key": object_key, "recording_started_by": started_by,
                  "recording_started_at": _now(), "updated_at": _now()},
         "$inc": {"version": 1}},
        projection={"_id": 0}, return_document=True,
    )
    return result


async def reconcile_egress_completion(
    db: AsyncIOMotorDatabase, *, session_id: str, egress_id: str, object_key: str, duration_seconds: int,
) -> Dict[str, Any]:
    """Webhook reconciliation (spec §12.4).

    LiveKit tells us when the recording file actually landed. Until then the
    session's `recording_object_key` is an intention, not a fact — this is what
    turns it into one, and it refuses to do so for a session whose mode never
    permitted recording, in case a stale or spoofed webhook arrives.
    """
    session = await get_session(db, session_id)
    if session is None:
        raise VirtualVisitError("NOT_FOUND", "session not found")
    assert_recording_allowed(session["privacy_mode"])
    return await db[SESSIONS_COLLECTION].find_one_and_update(
        {"id": session_id},
        {"$set": {"recording_egress_id": egress_id, "recording_object_key": object_key,
                  "recording_duration_seconds": duration_seconds,
                  "recording_completed_at": _now(), "updated_at": _now()},
         "$inc": {"version": 1}},
        projection={"_id": 0}, return_document=True,
    )


# ── Observations made during a call ─────────────────────────────────────────

async def add_observation(
    db: AsyncIOMotorDatabase,
    *,
    session_id: str,
    author_id: str,
    body: str,
    linked_entity_type: Optional[str] = None,
    linked_entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Append an observation. Append-only, like query responses: what a
    reviewer noted during a call is part of the audit record, and editing it
    afterwards would change what the record says they saw."""
    if not (body or "").strip():
        raise VirtualVisitError("OBSERVATION_BODY_REQUIRED", "an observation needs a body")
    session = await get_session(db, session_id)
    if session is None:
        raise VirtualVisitError("NOT_FOUND", "session not found")

    last = await db[OBSERVATIONS_COLLECTION].find_one(
        {"session_id": session_id}, {"_id": 0}, sort=[("sequence", -1)])
    doc = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "audit_id": session["audit_id"],
        "sequence": (last["sequence"] + 1) if last else 1,
        "author_id": author_id,
        "body": body.strip(),
        "linked_entity_type": linked_entity_type,
        "linked_entity_id": linked_entity_id,
        "created_at": _now(),
    }
    await db[OBSERVATIONS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def list_observations(db: AsyncIOMotorDatabase, session_id: str) -> List[Dict[str, Any]]:
    return await db[OBSERVATIONS_COLLECTION].find(
        {"session_id": session_id}, {"_id": 0}).sort("sequence", 1).to_list(None)
