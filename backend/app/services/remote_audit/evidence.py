"""Capture-session, upload-intent, and finalize protocol for evidence —
spec §8.1/§25.2, with the corrected sequencing from v1.3 correction #6:
upload-intent validates CLAIMS only (the file doesn't exist server-side yet);
real magic-byte/hash/malware verification happens after upload, in the
worker, never here. An uploaded object alone is never accepted evidence.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

import storage as file_storage

CAPTURE_SESSIONS_COLLECTION = "remote_audit_capture_sessions"
EVIDENCE_COLLECTION = "remote_audit_evidence"
EVIDENCE_VERIFICATIONS_COLLECTION = "remote_audit_evidence_verifications"

ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/quicktime"}
DEFAULT_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200MB, generous for a short video
DEFAULT_CAPTURE_SESSION_TTL_SECONDS = 300  # spec §7.4 pilot default: 2-5 minutes

STATUS_QUARANTINED = "QUARANTINED"
STATUS_PROCESSING = "PROCESSING"
STATUS_PROCESSING_FAILED = "PROCESSING_FAILED"
# Terminal states are whatever the verification engine's OverallState names are
# (PASS / PASS_WITH_LIMITED_CONFIDENCE / REVIEW / FAIL_TECHNICAL / HOLD /
# SUSPICIOUS_REVIEW) -- evidence.py doesn't duplicate that enum, it just
# stores whatever verification.py produced.


class CaptureError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[CAPTURE_SESSIONS_COLLECTION].create_index("id", unique=True)
    await db[CAPTURE_SESSIONS_COLLECTION].create_index("nonce", unique=True)
    await db[EVIDENCE_COLLECTION].create_index("id", unique=True)
    await db[EVIDENCE_COLLECTION].create_index("sha256_server")
    await db[EVIDENCE_COLLECTION].create_index("audit_id")
    await db[EVIDENCE_VERIFICATIONS_COLLECTION].create_index("evidence_id")
    await db[EVIDENCE_VERIFICATIONS_COLLECTION].create_index(
        [("evidence_id", 1), ("verification_run_id", 1)], unique=True)


async def create_capture_session(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    user_id: str,
    linked_entity_type: str,
    linked_entity_id: str,
    requested_media_type: str,
    capture_mode: str,
    policy_version: str,
    ttl_seconds: int = DEFAULT_CAPTURE_SESSION_TTL_SECONDS,
) -> Dict[str, Any]:
    """Spec §8.1 POST /capture-sessions. LIVE_IN_APP requires an online
    session -- this function IS that online session; there is no offline
    variant (v1.3 correction #5: offline high-trust capture is out of scope
    for v1)."""
    if requested_media_type not in ALLOWED_MEDIA_TYPES:
        raise CaptureError("UNSUPPORTED_MEDIA_TYPE", f"{requested_media_type} is not an allowed capture media type")
    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid.uuid4()),
        "audit_id": audit_id,
        "user_id": user_id,
        "linked_entity_type": linked_entity_type,
        "linked_entity_id": linked_entity_id,
        "nonce": secrets.token_hex(16),  # >=128 bits, per spec §8.1
        "requested_media_type": requested_media_type,
        "capture_mode": capture_mode,
        "policy_version": policy_version,
        "expires_at": now + timedelta(seconds=ttl_seconds),
        "consumed_at": None,
        "reserved_object_key": None,
        "reserved_evidence_id": None,
        "created_at": now,
        "server_time_utc": now,
    }
    await db[CAPTURE_SESSIONS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_capture_session(db: AsyncIOMotorDatabase, session_id: str) -> Optional[Dict[str, Any]]:
    return await db[CAPTURE_SESSIONS_COLLECTION].find_one({"id": session_id}, {"_id": 0})


def _new_object_key(audit_id: str, evidence_id: str, media_type: str) -> str:
    ext = {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
        "video/mp4": "mp4", "video/quicktime": "mov",
    }.get(media_type, "bin")
    # Opaque, unique, server-generated -- never derived from a client
    # filename (spec §17: "Generate server object keys; sanitize display
    # filename"). Originals/derived separation per spec §6.1's layout.
    return f"remote-audit/{audit_id}/originals/{evidence_id}.{ext}"


def _sanitize_display_filename(name: str) -> str:
    """Strip path separators/control characters -- the display filename is
    never used as a storage key or trusted for anything but display
    (v1.3 correction #8 web/privacy edge controls: filename sanitization)."""
    cleaned = "".join(c for c in (name or "") if c.isprintable() and c not in "/\\")
    return cleaned[:200] or "upload"


async def create_upload_intent(
    db: AsyncIOMotorDatabase,
    *,
    capture_session_id: str,
    file_name: str,
    mime_claimed: str,
    size_claimed: int,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Spec §8.1 POST /capture-sessions/{id}/upload-intent, corrected
    sequencing (v1.3 #6): validates only the CLAIMS -- filename, claimed
    MIME, claimed size, session validity -- because the actual bytes do not
    exist server-side yet. Real magic-byte/hash/malware verification happens
    later, in the worker, after upload.
    """
    now = now or datetime.now(timezone.utc)
    session = await get_capture_session(db, capture_session_id)
    if session is None:
        raise CaptureError("SESSION_NOT_FOUND", "capture session not found")
    if session["consumed_at"] is not None:
        raise CaptureError("SESSION_ALREADY_CONSUMED", "this capture session was already finalized")
    if session["expires_at"] <= now:
        raise CaptureError("SESSION_EXPIRED", "capture session has expired; restart capture")
    if mime_claimed != session["requested_media_type"]:
        raise CaptureError("MEDIA_TYPE_MISMATCH", "claimed MIME does not match the session's requested media type")
    if size_claimed > max_bytes:
        raise CaptureError("FILE_TOO_LARGE", f"claimed size {size_claimed} exceeds the {max_bytes} byte limit")
    if size_claimed <= 0:
        raise CaptureError("INVALID_SIZE", "claimed size must be positive")

    evidence_id = str(uuid.uuid4())
    object_key = _new_object_key(session["audit_id"], evidence_id, mime_claimed)
    display_name = _sanitize_display_filename(file_name)

    await db[CAPTURE_SESSIONS_COLLECTION].update_one(
        {"id": capture_session_id},
        {"$set": {
            "reserved_object_key": object_key, "reserved_evidence_id": evidence_id,
            "claimed_size": size_claimed, "claimed_mime": mime_claimed,
            "display_name": display_name,
        }},
    )

    storage = file_storage.get_storage()
    presigned = await storage.presign_upload(object_key, mime_claimed, max_bytes)
    return {
        "evidence_id": evidence_id,
        "object_key": object_key,
        "upload": presigned,  # None on local storage -- caller falls back to a server-mediated upload endpoint
        "server_mediated": presigned is None,
    }


async def finalize_capture(
    db: AsyncIOMotorDatabase,
    *,
    capture_session_id: str,
    object_key: str,
    sha256_client: str,
    capture_manifest: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Spec §8.1 POST /capture-sessions/{id}/finalize + §25.2's transaction.
    Atomically claims the session (one-time consumption -- the
    `consumed_at IS NULL` filter is the replay defense), creates the
    evidence record in QUARANTINED, and returns what the caller enqueues as
    an `evidence.verify` job. An uploaded object alone is never accepted
    evidence -- this is the only path that creates an evidence record, and
    it never marks anything verified itself.
    """
    now = now or datetime.now(timezone.utc)
    session = await get_capture_session(db, capture_session_id)
    if session is None:
        raise CaptureError("SESSION_NOT_FOUND", "capture session not found")
    if session["expires_at"] <= now:
        raise CaptureError("SESSION_EXPIRED", "capture session has expired; restart capture")
    if session.get("reserved_object_key") != object_key:
        raise CaptureError("OBJECT_KEY_MISMATCH", "finalize object_key does not match the reserved upload")

    # Atomic one-time claim: only succeeds if consumed_at is still NULL --
    # a second finalize on the same session can never create a duplicate
    # evidence row (v1.3 correction #1: universal idempotency).
    claim = await db[CAPTURE_SESSIONS_COLLECTION].find_one_and_update(
        {"id": capture_session_id, "consumed_at": None},
        {"$set": {"consumed_at": now}},
    )
    if claim is None:
        raise CaptureError("SESSION_ALREADY_CONSUMED", "this capture session was already finalized")

    evidence_id = session["reserved_evidence_id"]
    doc = {
        "id": evidence_id,
        "audit_id": session["audit_id"],
        "capture_session_id": capture_session_id,
        "linked_entity_type": session["linked_entity_type"],
        "linked_entity_id": session["linked_entity_id"],
        "media_type": session["requested_media_type"],
        "capture_mode": session["capture_mode"],
        "object_key": object_key,
        "display_name": session.get("display_name"),
        "sha256_client": sha256_client,
        "sha256_server": None,  # computed by the worker after reading the real bytes
        "status": STATUS_QUARANTINED,
        "capture_manifest": capture_manifest,
        "policy_version": session["policy_version"],
        "created_by": session["user_id"],
        "created_at": now,
        "updated_at": now,
    }
    await db[EVIDENCE_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_evidence(db: AsyncIOMotorDatabase, evidence_id: str) -> Optional[Dict[str, Any]]:
    return await db[EVIDENCE_COLLECTION].find_one({"id": evidence_id}, {"_id": 0})


async def set_evidence_status(
    db: AsyncIOMotorDatabase, evidence_id: str, status: str, *, sha256_server: Optional[str] = None,
) -> None:
    updates: Dict[str, Any] = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if sha256_server is not None:
        updates["sha256_server"] = sha256_server
    await db[EVIDENCE_COLLECTION].update_one({"id": evidence_id}, {"$set": updates})


async def next_verification_run_id(db: AsyncIOMotorDatabase, evidence_id: str) -> int:
    latest = await db[EVIDENCE_VERIFICATIONS_COLLECTION].find_one(
        {"evidence_id": evidence_id}, sort=[("verification_run_id", -1)],
    )
    return (latest["verification_run_id"] + 1) if latest else 1


async def record_verification(
    db: AsyncIOMotorDatabase,
    *,
    evidence_id: str,
    overall_state: str,
    rule_set_version: str,
    reasons: List[Dict[str, Any]],
    signals: Dict[str, Any],
) -> Dict[str, Any]:
    """Append-only/versioned (v1.3 correction #7): a re-run NEVER overwrites
    a prior result -- every historical verification for a given evidence
    item stays retrievable via its verification_run_id/
    supersedes_verification_id chain."""
    run_id = await next_verification_run_id(db, evidence_id)
    previous = (
        await db[EVIDENCE_VERIFICATIONS_COLLECTION].find_one(
            {"evidence_id": evidence_id, "verification_run_id": run_id - 1})
        if run_id > 1 else None
    )
    doc = {
        "id": str(uuid.uuid4()),
        "evidence_id": evidence_id,
        "verification_run_id": run_id,
        "supersedes_verification_id": previous["id"] if previous else None,
        "overall_state": overall_state,
        "rule_set_version": rule_set_version,
        "reasons": reasons,
        "signals": signals,
        "completed_at_utc": datetime.now(timezone.utc),
    }
    await db[EVIDENCE_VERIFICATIONS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def list_verifications(db: AsyncIOMotorDatabase, evidence_id: str) -> List[Dict[str, Any]]:
    return await db[EVIDENCE_VERIFICATIONS_COLLECTION].find(
        {"evidence_id": evidence_id}, {"_id": 0},
    ).sort("verification_run_id", 1).to_list(None)


async def latest_verification(db: AsyncIOMotorDatabase, evidence_id: str) -> Optional[Dict[str, Any]]:
    return await db[EVIDENCE_VERIFICATIONS_COLLECTION].find_one(
        {"evidence_id": evidence_id}, {"_id": 0}, sort=[("verification_run_id", -1)],
    )


async def find_duplicate_sha256(db: AsyncIOMotorDatabase, sha256_server: str, *, exclude_evidence_id: str) -> bool:
    existing = await db[EVIDENCE_COLLECTION].find_one({
        "sha256_server": sha256_server, "id": {"$ne": exclude_evidence_id},
    })
    return existing is not None


async def reconcile_orphaned_uploads(db: AsyncIOMotorDatabase, *, older_than: datetime) -> List[str]:
    """Spec §16.4 'Upload succeeds but finalize request fails -> Orphan
    object': a capture session that reserved an object key at upload-intent
    time but was never finalized, and has since expired, is reconciled here
    -- the reservation is released so it never becomes a phantom evidence
    row. (Deleting the orphaned object itself is an object-storage lifecycle
    concern, handled at the storage-configuration layer, not here.)
    """
    cursor = db[CAPTURE_SESSIONS_COLLECTION].find({
        "consumed_at": None,
        "reserved_object_key": {"$ne": None},
        "expires_at": {"$lt": older_than},
    })
    reconciled: List[str] = []
    async for session in cursor:
        await db[CAPTURE_SESSIONS_COLLECTION].update_one(
            {"id": session["id"]},
            {"$set": {
                "reserved_object_key": None, "reserved_evidence_id": None,
                "orphaned_at": datetime.now(timezone.utc),
            }},
        )
        reconciled.append(session["id"])
    return reconciled
