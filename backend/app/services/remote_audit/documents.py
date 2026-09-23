"""Document records and their lifecycle — spec §10.

State machine (spec §10.1, Production-Grade Implementation Plan v1.3 Phase 4):

    QUARANTINED ──► PROCESSING ──┬─► TECHNICALLY_VALID ──┬─► ACCEPTED
                                 │                       └─► REJECTED
                                 ├─► REVIEW_REQUIRED ────┬─► ACCEPTED
                                 │                       └─► REJECTED
                                 ├─► PROCESSING_BLOCKED  (scanner unreachable)
                                 └─► PROCESSING_FAILED   (worker/parser fault)

    ACCEPTED ──► SUPERSEDED | EXPIRED

Four properties this module is responsible for, each of which would be a real
defect if it were merely "usually true":

1. **Nothing is reviewer-visible before it has been scanned.** `is_visible_to_reviewer`
   is the single predicate for that, and every read path goes through it.
   PROCESSING_BLOCKED (scanner unavailable) is explicitly NOT visible — v1.3
   correction #15 is stricter than REVIEW here, on purpose.
2. **A document never reaches ACCEPTED except from a state that has actually
   completed its checks.** Enforced by `ALLOWED_TRANSITIONS`, not by callers
   remembering.
3. **"Signature present" never means "signature trusted."** This module stores
   the signature report but draws no conclusion from its existence — only
   `signatures.SignatureTrust.TRUSTED` is ever treated as trust, in
   document_pipeline.py.
4. **Extracted values are suggestions until a human confirms them.** `extracted`
   and `confirmed` are separate sub-documents and only `confirmed` is fact.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

DOCUMENTS_COLLECTION = "remote_audit_documents"
DOCUMENT_CHECKS_COLLECTION = "remote_audit_document_checks"

STATUS_QUARANTINED = "QUARANTINED"
STATUS_PROCESSING = "PROCESSING"
STATUS_PROCESSING_BLOCKED = "PROCESSING_BLOCKED"
STATUS_PROCESSING_FAILED = "PROCESSING_FAILED"
STATUS_TECHNICALLY_VALID = "TECHNICALLY_VALID"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_EXPIRED = "EXPIRED"

ALL_STATUSES = {
    STATUS_QUARANTINED, STATUS_PROCESSING, STATUS_PROCESSING_BLOCKED, STATUS_PROCESSING_FAILED,
    STATUS_TECHNICALLY_VALID, STATUS_REVIEW_REQUIRED, STATUS_ACCEPTED, STATUS_REJECTED,
    STATUS_SUPERSEDED, STATUS_EXPIRED,
}

#: The complete set of legal moves. Anything not listed is rejected by
#: `transition()` — there is no "and anything else the caller wants" branch.
ALLOWED_TRANSITIONS: Dict[str, set] = {
    STATUS_QUARANTINED: {STATUS_PROCESSING},
    # A blocked/failed document can be re-queued, which returns it to PROCESSING.
    STATUS_PROCESSING: {
        STATUS_TECHNICALLY_VALID, STATUS_REVIEW_REQUIRED,
        STATUS_PROCESSING_BLOCKED, STATUS_PROCESSING_FAILED, STATUS_REJECTED,
    },
    STATUS_PROCESSING_BLOCKED: {STATUS_PROCESSING, STATUS_REJECTED},
    STATUS_PROCESSING_FAILED: {STATUS_PROCESSING, STATUS_REJECTED},
    STATUS_TECHNICALLY_VALID: {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_REVIEW_REQUIRED, STATUS_PROCESSING},
    STATUS_REVIEW_REQUIRED: {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_PROCESSING},
    STATUS_ACCEPTED: {STATUS_SUPERSEDED, STATUS_EXPIRED},
    # Terminal.
    STATUS_REJECTED: set(),
    STATUS_SUPERSEDED: set(),
    STATUS_EXPIRED: set(),
}

#: Statuses a reviewer may see at all. Pre-scan and blocked states are absent
#: by design: an unscanned file is never put in front of a human.
REVIEWER_VISIBLE_STATUSES = {
    STATUS_TECHNICALLY_VALID, STATUS_REVIEW_REQUIRED, STATUS_ACCEPTED,
    STATUS_REJECTED, STATUS_SUPERSEDED, STATUS_EXPIRED,
}

ALLOWED_DOCUMENT_MEDIA_TYPES = {"application/pdf", "image/jpeg", "image/png"}
DEFAULT_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024

#: Fields a reviewer confirms. Kept explicit so a caller can't smuggle an
#: arbitrary key (say, `status`) into the confirmed sub-document.
CONFIRMABLE_FIELDS = {
    "certificate_number", "calibration_date", "due_date", "calibrated_by",
    "result", "issued_by", "document_date",
}


class DocumentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class InvalidTransition(DocumentError):
    def __init__(self, current: str, target: str):
        super().__init__("INVALID_DOCUMENT_TRANSITION",
                         f"a document cannot move from {current} to {target}")
        self.current = current
        self.target = target


def is_visible_to_reviewer(status: str) -> bool:
    """The single authority on reviewer visibility (spec §10.1, v1.3
    correction #15). PROCESSING_BLOCKED is deliberately excluded: when the
    malware scanner is unreachable the file stays hidden rather than being
    shown with a caveat."""
    return status in REVIEWER_VISIBLE_STATUSES


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[DOCUMENTS_COLLECTION].create_index("id", unique=True)
    await db[DOCUMENTS_COLLECTION].create_index("audit_id")
    await db[DOCUMENTS_COLLECTION].create_index([("audit_id", 1), ("status", 1)])
    await db[DOCUMENTS_COLLECTION].create_index("linked_entity_id")
    await db[DOCUMENTS_COLLECTION].create_index("sha256_server")
    await db[DOCUMENT_CHECKS_COLLECTION].create_index("document_id")
    await db[DOCUMENT_CHECKS_COLLECTION].create_index(
        [("document_id", 1), ("check_run_id", 1)], unique=True)


def _sanitize_display_filename(name: str) -> str:
    """Display-only. Never a storage key, never interpolated into a path.
    (v1.3 correction #8 — filename sanitization on every upload path.)"""
    cleaned = "".join(c for c in (name or "") if c.isprintable() and c not in "/\\")
    return cleaned[:200] or "document"


def _object_key(audit_id: str, document_id: str, media_type: str) -> str:
    ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}.get(media_type, "bin")
    return f"remote-audit/{audit_id}/documents/originals/{document_id}.{ext}"


async def create_document(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    uploaded_by: str,
    document_type: str,
    file_name: str,
    mime_claimed: str,
    size_claimed: int,
    linked_entity_type: Optional[str] = None,
    linked_entity_id: Optional[str] = None,
    supersedes_document_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reserve a document record and its opaque object key.

    Like evidence upload-intent, this validates CLAIMS ONLY — the bytes do not
    exist server-side yet, so nothing about the real content can be checked
    here (v1.3 correction #6). The record starts QUARANTINED and invisible.
    """
    if mime_claimed not in ALLOWED_DOCUMENT_MEDIA_TYPES:
        raise DocumentError("UNSUPPORTED_MEDIA_TYPE", f"{mime_claimed} is not an allowed document type")
    if size_claimed <= 0 or size_claimed > DEFAULT_MAX_DOCUMENT_BYTES:
        raise DocumentError("FILE_TOO_LARGE",
                            f"claimed size must be between 1 and {DEFAULT_MAX_DOCUMENT_BYTES} bytes")

    now = datetime.now(timezone.utc)
    document_id = str(uuid.uuid4())
    doc = {
        "id": document_id,
        "audit_id": audit_id,
        "uploaded_by": uploaded_by,
        "document_type": document_type,
        "display_name": _sanitize_display_filename(file_name),
        "media_type": mime_claimed,
        "size_claimed": size_claimed,
        "object_key": _object_key(audit_id, document_id, mime_claimed),
        "linked_entity_type": linked_entity_type,
        "linked_entity_id": linked_entity_id,
        "status": STATUS_QUARANTINED,
        "sha256_server": None,
        # Set the moment the document is handed to the worker. Until then the
        # uploader may re-send bytes (a retried upload); afterwards they may
        # not, which closes the window where the file examined and the file
        # stored could differ.
        "submitted_at": None,
        # Set only when the bytes must never be served again -- malware. The
        # RECORD stays visible so the site is told why; the CONTENT does not.
        "content_blocked": False,
        "supersedes_document_id": supersedes_document_id,
        "superseded_by_document_id": None,
        # AI/OCR output, never fact.
        "extracted": None,
        # Human-confirmed values — the only ones the rest of the system trusts.
        "confirmed": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "decision_reason_code": None,
        "decision_note": None,
        "decided_by": None,
        "decided_at": None,
        "expires_on": None,
        "uploaded_at": now,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }
    await db[DOCUMENTS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_document(db: AsyncIOMotorDatabase, document_id: str) -> Optional[Dict[str, Any]]:
    return await db[DOCUMENTS_COLLECTION].find_one({"id": document_id}, {"_id": 0})


async def list_documents(
    db: AsyncIOMotorDatabase, audit_id: str, *, reviewer_visible_only: bool = False,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"audit_id": audit_id}
    if reviewer_visible_only:
        query["status"] = {"$in": sorted(REVIEWER_VISIBLE_STATUSES)}
    return await db[DOCUMENTS_COLLECTION].find(query, {"_id": 0}).sort("created_at", -1).to_list(None)


async def transition(
    db: AsyncIOMotorDatabase,
    document_id: str,
    target_status: str,
    *,
    expected_status: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Move a document to `target_status`, or raise.

    The update is a single conditional write matched on the CURRENT status, so
    two concurrent transitions cannot both succeed — the loser sees the state
    it lost to rather than silently overwriting the winner.
    """
    if target_status not in ALL_STATUSES:
        raise DocumentError("UNKNOWN_DOCUMENT_STATUS", f"{target_status} is not a document status")
    current = await get_document(db, document_id)
    if current is None:
        raise DocumentError("DOCUMENT_NOT_FOUND", "document not found")
    current_status = current["status"]
    if expected_status is not None and current_status != expected_status:
        raise InvalidTransition(current_status, target_status)
    if not can_transition(current_status, target_status):
        raise InvalidTransition(current_status, target_status)

    updates: Dict[str, Any] = {
        "status": target_status,
        "updated_at": datetime.now(timezone.utc),
    }
    updates.update(extra or {})
    result = await db[DOCUMENTS_COLLECTION].find_one_and_update(
        {"id": document_id, "status": current_status, "version": current["version"]},
        {"$set": updates, "$inc": {"version": 1}},
        projection={"_id": 0},
        return_document=True,
    )
    if result is None:
        # Someone else moved it between our read and our write.
        latest = await get_document(db, document_id)
        raise InvalidTransition(latest["status"] if latest else "UNKNOWN", target_status)
    return result


async def record_check(
    db: AsyncIOMotorDatabase,
    *,
    document_id: str,
    outcome: str,
    checks: Dict[str, Any],
    findings: List[Dict[str, Any]],
    rule_set_version: str,
) -> Dict[str, Any]:
    """Append a check run. Append-only and versioned, exactly like evidence
    verification (v1.3 correction #7): a re-run after a rule-set change adds a
    row and supersedes the previous one by reference; it never overwrites what
    a reviewer already saw."""
    previous = await latest_check(db, document_id)
    run_id = (previous["check_run_id"] + 1) if previous else 1
    doc = {
        "id": str(uuid.uuid4()),
        "document_id": document_id,
        "check_run_id": run_id,
        "supersedes_check_id": previous["id"] if previous else None,
        "outcome": outcome,
        "checks": checks,
        "findings": findings,
        "rule_set_version": rule_set_version,
        "completed_at_utc": datetime.now(timezone.utc),
    }
    await db[DOCUMENT_CHECKS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def latest_check(db: AsyncIOMotorDatabase, document_id: str) -> Optional[Dict[str, Any]]:
    return await db[DOCUMENT_CHECKS_COLLECTION].find_one(
        {"document_id": document_id}, {"_id": 0}, sort=[("check_run_id", -1)])


async def list_checks(db: AsyncIOMotorDatabase, document_id: str) -> List[Dict[str, Any]]:
    return await db[DOCUMENT_CHECKS_COLLECTION].find(
        {"document_id": document_id}, {"_id": 0}).sort("check_run_id", 1).to_list(None)


async def set_extraction(
    db: AsyncIOMotorDatabase, document_id: str, extraction: Dict[str, Any],
) -> None:
    """Store machine-extracted fields. Written to `extracted`, never to
    `confirmed` — the distinction is the whole point of extract -> review ->
    commit, and the UI renders them differently."""
    await db[DOCUMENTS_COLLECTION].update_one(
        {"id": document_id},
        {"$set": {"extracted": extraction, "updated_at": datetime.now(timezone.utc)}},
    )


def _coerce_date(value: Any) -> Optional[str]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            return None
    return None


async def confirm_fields(
    db: AsyncIOMotorDatabase,
    document_id: str,
    *,
    fields: Dict[str, Any],
    confirmed_by: str,
) -> Dict[str, Any]:
    """Commit a reviewer's confirmed values.

    Dates are normalized to ISO here and REJECTED if unparseable. A certificate
    whose expiry we misread is worse than one we refuse to record: a silently
    wrong `due_date` would drive an expiry sweep to the wrong answer.
    """
    unknown = set(fields) - CONFIRMABLE_FIELDS
    if unknown:
        raise DocumentError("UNKNOWN_FIELD", f"not confirmable: {', '.join(sorted(unknown))}")

    cleaned: Dict[str, Any] = {}
    for key, value in fields.items():
        if key in ("calibration_date", "due_date", "document_date"):
            if value in (None, ""):
                cleaned[key] = None
                continue
            iso = _coerce_date(value)
            if iso is None:
                raise DocumentError("AMBIGUOUS_DATE", f"{key} is not an unambiguous ISO date: {value!r}")
            cleaned[key] = iso
        else:
            cleaned[key] = value

    now = datetime.now(timezone.utc)
    updates: Dict[str, Any] = {
        "confirmed": cleaned, "confirmed_by": confirmed_by, "confirmed_at": now, "updated_at": now,
    }
    # The confirmed due date is what the expiry sweep runs on — the extracted
    # suggestion never is.
    if cleaned.get("due_date"):
        updates["expires_on"] = cleaned["due_date"]
    result = await db[DOCUMENTS_COLLECTION].find_one_and_update(
        {"id": document_id}, {"$set": updates, "$inc": {"version": 1}},
        projection={"_id": 0}, return_document=True)
    if result is None:
        raise DocumentError("DOCUMENT_NOT_FOUND", "document not found")
    return result


async def supersede(
    db: AsyncIOMotorDatabase, *, old_document_id: str, new_document_id: str,
) -> Dict[str, Any]:
    """Mark an accepted document superseded by a newer one.

    Supersession is a link in both directions and never a deletion: the old
    certificate stays queryable, which is what makes "what did the site have on
    file in March?" answerable after the fact.
    """
    old = await get_document(db, old_document_id)
    if old is None:
        raise DocumentError("DOCUMENT_NOT_FOUND", "document not found")
    if old["status"] != STATUS_ACCEPTED:
        raise InvalidTransition(old["status"], STATUS_SUPERSEDED)
    updated = await transition(
        db, old_document_id, STATUS_SUPERSEDED,
        expected_status=STATUS_ACCEPTED,
        extra={"superseded_by_document_id": new_document_id},
    )
    await db[DOCUMENTS_COLLECTION].update_one(
        {"id": new_document_id}, {"$set": {"supersedes_document_id": old_document_id}})
    return updated


async def expire_due_documents(
    db: AsyncIOMotorDatabase, *, audit_id: Optional[str] = None, as_of: Optional[date] = None,
) -> List[str]:
    """Move ACCEPTED documents past their confirmed expiry to EXPIRED.

    Only the CONFIRMED `expires_on` drives this. An AI-suggested due date that
    nobody confirmed must never expire a document on its own — that would let
    a model's misreading silently invalidate a site's compliance record.

    **Which "today".** The comparison is `expires_on < today` evaluated in UTC,
    so a certificate stays valid through the whole of its due date and expires
    at the following UTC midnight. Sites east of UTC therefore keep a document
    for a few extra hours past their local midnight. That direction is
    deliberate: expiring early would flag a compliant site as non-compliant on
    a paperwork technicality, which is the more damaging of the two errors.
    Evaluating this per-site (`remote_audit_sites.timezone`) is a documented
    follow-up, not something to fake with a fixed offset here. Callers that
    need a specific reference day — tests especially — must pass `as_of`
    rather than assuming the server's local calendar matches UTC.
    """
    today = (as_of or datetime.now(timezone.utc).date()).isoformat()
    query: Dict[str, Any] = {
        "status": STATUS_ACCEPTED,
        "expires_on": {"$ne": None, "$lt": today},
    }
    if audit_id:
        query["audit_id"] = audit_id
    # Materialize the candidate ids BEFORE writing. Transitioning inside an
    # open cursor mutates the very documents the cursor is paging over — the
    # updated rows stop matching the query mid-iteration, and how much of the
    # result set survives that is not something to rely on.
    candidates = await db[DOCUMENTS_COLLECTION].find(query, {"_id": 0, "id": 1}).to_list(None)
    expired: List[str] = []
    for doc in candidates:
        try:
            await transition(db, doc["id"], STATUS_EXPIRED, expected_status=STATUS_ACCEPTED)
            expired.append(doc["id"])
        except InvalidTransition:
            continue  # someone moved it first; not an error
    return expired


async def find_duplicate_sha256(
    db: AsyncIOMotorDatabase, sha256: str, *, exclude_document_id: str,
) -> bool:
    existing = await db[DOCUMENTS_COLLECTION].find_one(
        {"sha256_server": sha256, "id": {"$ne": exclude_document_id}}, {"_id": 0, "id": 1})
    return existing is not None
