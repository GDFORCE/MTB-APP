"""Audit queries and CAPAs — spec §11.

Two state machines, both enforced here rather than by callers remembering:

    QUERY   OPEN ──► RESPONDED ──┬─► CLOSED ──► REOPENED ──► RESPONDED …
                                 └─► OPEN        (response rejected, with a reason)
            OPEN/RESPONDED ──► CANCELLED         (raised in error)

    CAPA    OPEN ──► IN_PROGRESS ──► COMPLETED ──┬─► CLOSED
                                                 └─► REOPENED ──► IN_PROGRESS …
            OPEN/IN_PROGRESS ──► CANCELLED

Properties this module is responsible for:

1. **Responses are append-only.** A site's answer is part of the audit record.
   Editing one in place would let a response be rewritten after a reviewer
   read it, so `add_response` only ever inserts.
2. **Reopening preserves history.** REOPENED is a state, not a reset — every
   prior response and every prior decision stays attached and retrievable.
3. **A rejection carries a reason code.** "Rejected" with no stated reason is
   not auditable, so the state machine refuses it.
4. **Concurrent transitions cannot both win.** Every move is a conditional
   write matched on the exact `(status, version)` the caller last saw.
5. **A CAPA closes only on a recorded effectiveness review.** "Site says it's
   done" is `COMPLETED`, not `CLOSED` — the two are different claims made by
   different parties.
6. **Overdue is derived, never stored.** A stored flag goes stale the moment
   the clock passes it; `is_overdue()` computes it from the due date so the
   answer cannot disagree with reality.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

QUERIES_COLLECTION = "remote_audit_queries"
QUERY_RESPONSES_COLLECTION = "remote_audit_query_responses"
CAPAS_COLLECTION = "remote_audit_capas"

# ── Query states ────────────────────────────────────────────────────────────
QUERY_OPEN = "OPEN"
QUERY_RESPONDED = "RESPONDED"
QUERY_CLOSED = "CLOSED"
QUERY_REOPENED = "REOPENED"
QUERY_CANCELLED = "CANCELLED"

QUERY_STATES = {QUERY_OPEN, QUERY_RESPONDED, QUERY_CLOSED, QUERY_REOPENED, QUERY_CANCELLED}

QUERY_TRANSITIONS: Dict[str, set] = {
    QUERY_OPEN: {QUERY_RESPONDED, QUERY_CANCELLED},
    # Back to OPEN when the reviewer rejects the response as inadequate.
    QUERY_RESPONDED: {QUERY_CLOSED, QUERY_OPEN, QUERY_CANCELLED},
    QUERY_CLOSED: {QUERY_REOPENED},
    QUERY_REOPENED: {QUERY_RESPONDED, QUERY_CLOSED, QUERY_CANCELLED},
    QUERY_CANCELLED: set(),
}

QUERY_CRITICALITIES = ("critical", "major", "minor")

# ── CAPA states ─────────────────────────────────────────────────────────────
CAPA_OPEN = "OPEN"
CAPA_IN_PROGRESS = "IN_PROGRESS"
CAPA_COMPLETED = "COMPLETED"
CAPA_CLOSED = "CLOSED"
CAPA_REOPENED = "REOPENED"
CAPA_CANCELLED = "CANCELLED"

CAPA_STATES = {CAPA_OPEN, CAPA_IN_PROGRESS, CAPA_COMPLETED, CAPA_CLOSED, CAPA_REOPENED, CAPA_CANCELLED}

CAPA_TRANSITIONS: Dict[str, set] = {
    CAPA_OPEN: {CAPA_IN_PROGRESS, CAPA_CANCELLED},
    CAPA_IN_PROGRESS: {CAPA_COMPLETED, CAPA_CANCELLED},
    # CLOSED requires a recorded effectiveness review; REOPENED is what an
    # ineffective one produces.
    CAPA_COMPLETED: {CAPA_CLOSED, CAPA_REOPENED},
    CAPA_REOPENED: {CAPA_IN_PROGRESS, CAPA_CANCELLED},
    CAPA_CLOSED: set(),
    CAPA_CANCELLED: set(),
}

#: CAPA states that still represent outstanding work. A "critical CAPA still
#: open" check must use this, not `!= CLOSED` — CANCELLED is not outstanding.
CAPA_OUTSTANDING_STATES = frozenset({CAPA_OPEN, CAPA_IN_PROGRESS, CAPA_COMPLETED, CAPA_REOPENED})

EFFECTIVENESS_EFFECTIVE = "EFFECTIVE"
EFFECTIVENESS_INEFFECTIVE = "INEFFECTIVE"


class QueryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class InvalidQueryTransition(QueryError):
    def __init__(self, current: str, target: str, *, kind: str = "query"):
        super().__init__("INVALID_TRANSITION", f"a {kind} cannot move from {current} to {target}")
        self.current = current
        self.target = target


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[QUERIES_COLLECTION].create_index("id", unique=True)
    await db[QUERIES_COLLECTION].create_index("audit_id")
    await db[QUERIES_COLLECTION].create_index([("audit_id", 1), ("status", 1)])
    await db[QUERIES_COLLECTION].create_index("linked_entity_id")
    await db[QUERY_RESPONSES_COLLECTION].create_index("query_id")
    await db[QUERY_RESPONSES_COLLECTION].create_index(
        [("query_id", 1), ("sequence", 1)], unique=True)
    await db[CAPAS_COLLECTION].create_index("id", unique=True)
    await db[CAPAS_COLLECTION].create_index("audit_id")
    await db[CAPAS_COLLECTION].create_index("query_id")
    await db[CAPAS_COLLECTION].create_index([("status", 1), ("due_date", 1)])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_due_date(value: Any) -> str:
    """Due dates are ISO-only, and unparseable input is refused.

    A CAPA due date drives overdue reminders and can block a final decision.
    Storing a guessed date would make both of those quietly wrong."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            pass
    raise QueryError("INVALID_DUE_DATE", f"due date must be an ISO date (YYYY-MM-DD), got {value!r}")


# ═══════════════════════════════════════════════════════════════════════════
# Queries
# ═══════════════════════════════════════════════════════════════════════════

async def create_query(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    raised_by: str,
    title: str,
    detail: str,
    criticality: str = "minor",
    linked_entity_type: Optional[str] = None,
    linked_entity_id: Optional[str] = None,
    due_date: Optional[Any] = None,
) -> Dict[str, Any]:
    if criticality not in QUERY_CRITICALITIES:
        raise QueryError("INVALID_CRITICALITY",
                         f"criticality must be one of {', '.join(QUERY_CRITICALITIES)}")
    if not (title or "").strip():
        raise QueryError("TITLE_REQUIRED", "a query needs a title")
    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "audit_id": audit_id,
        "title": title.strip(),
        "detail": (detail or "").strip(),
        "criticality": criticality,
        "status": QUERY_OPEN,
        "linked_entity_type": linked_entity_type,
        "linked_entity_id": linked_entity_id,
        "due_date": _parse_due_date(due_date) if due_date else None,
        "raised_by": raised_by,
        "closed_by": None,
        "closed_at": None,
        "close_reason_code": None,
        "last_rejection_reason_code": None,
        "reopen_count": 0,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }
    await db[QUERIES_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_query(db: AsyncIOMotorDatabase, query_id: str) -> Optional[Dict[str, Any]]:
    return await db[QUERIES_COLLECTION].find_one({"id": query_id}, {"_id": 0})


async def list_queries(
    db: AsyncIOMotorDatabase, audit_id: str, *, status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"audit_id": audit_id}
    if status:
        query["status"] = status
    return await db[QUERIES_COLLECTION].find(query, {"_id": 0}).sort("created_at", -1).to_list(None)


def can_query_transition(current: str, target: str) -> bool:
    return target in QUERY_TRANSITIONS.get(current, set())


async def _transition(
    db: AsyncIOMotorDatabase,
    collection: str,
    doc_id: str,
    target: str,
    *,
    transitions: Dict[str, set],
    states: set,
    kind: str,
    expected_status: Optional[str] = None,
    expected_version: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One conditional write, matched on the exact state the caller last saw.

    `expected_version` is the caller's optimistic lock: two reviewers acting on
    the same query from two screens cannot both succeed, and the loser is told
    rather than silently overwriting the winner.
    """
    if target not in states:
        raise QueryError("UNKNOWN_STATE", f"{target} is not a {kind} state")
    current = await db[collection].find_one({"id": doc_id}, {"_id": 0})
    if current is None:
        raise QueryError("NOT_FOUND", f"{kind} not found")
    current_status = current["status"]
    if expected_status is not None and current_status != expected_status:
        raise InvalidQueryTransition(current_status, target, kind=kind)
    if expected_version is not None and current["version"] != expected_version:
        raise QueryError("STALE_VERSION",
                         f"this {kind} changed since you loaded it (now version {current['version']})")
    if target not in transitions.get(current_status, set()):
        raise InvalidQueryTransition(current_status, target, kind=kind)

    updates: Dict[str, Any] = {"status": target, "updated_at": _now()}
    updates.update(extra or {})
    result = await db[collection].find_one_and_update(
        {"id": doc_id, "status": current_status, "version": current["version"]},
        {"$set": updates, "$inc": {"version": 1}},
        projection={"_id": 0}, return_document=True,
    )
    if result is None:
        latest = await db[collection].find_one({"id": doc_id}, {"_id": 0})
        raise InvalidQueryTransition(latest["status"] if latest else "UNKNOWN", target, kind=kind)
    return result


async def add_response(
    db: AsyncIOMotorDatabase,
    *,
    query_id: str,
    author_id: str,
    body: str,
    attachment_ids: Optional[List[str]] = None,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Append a response and move the query to RESPONDED.

    Append-only: there is no update path for a response. Once a site has
    answered and a reviewer has read it, rewriting that answer would change
    what the audit record says happened.
    """
    if not (body or "").strip():
        raise QueryError("RESPONSE_BODY_REQUIRED", "a response needs a body")
    current = await get_query(db, query_id)
    if current is None:
        raise QueryError("NOT_FOUND", "query not found")

    last = await db[QUERY_RESPONSES_COLLECTION].find_one(
        {"query_id": query_id}, {"_id": 0}, sort=[("sequence", -1)])
    response = {
        "id": str(uuid.uuid4()),
        "query_id": query_id,
        "sequence": (last["sequence"] + 1) if last else 1,
        "author_id": author_id,
        "body": body.strip(),
        "attachment_ids": list(attachment_ids or []),
        "created_at": _now(),
    }
    await db[QUERY_RESPONSES_COLLECTION].insert_one(response)
    try:
        await _transition(
            db, QUERIES_COLLECTION, query_id, QUERY_RESPONDED,
            transitions=QUERY_TRANSITIONS, states=QUERY_STATES, kind="query",
            expected_version=expected_version)
    except InvalidQueryTransition:
        # The response is already recorded and stays recorded: a site's answer
        # is not discarded because the query moved underneath it. The caller
        # sees the failure and the thread stays complete.
        raise
    return {k: v for k, v in response.items() if k != "_id"}


async def list_responses(db: AsyncIOMotorDatabase, query_id: str) -> List[Dict[str, Any]]:
    return await db[QUERY_RESPONSES_COLLECTION].find(
        {"query_id": query_id}, {"_id": 0}).sort("sequence", 1).to_list(None)


async def reject_response(
    db: AsyncIOMotorDatabase,
    *,
    query_id: str,
    reviewer_id: str,
    reason_code: str,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Send a query back to the site because the response was inadequate.

    The reason code is mandatory. "Rejected, no reason given" is not something
    a site can act on or an inspector can later interpret.
    """
    if not (reason_code or "").strip():
        raise QueryError("REASON_CODE_REQUIRED", "a rejection must carry a reason code")
    return await _transition(
        db, QUERIES_COLLECTION, query_id, QUERY_OPEN,
        transitions=QUERY_TRANSITIONS, states=QUERY_STATES, kind="query",
        expected_version=expected_version,
        extra={"last_rejection_reason_code": reason_code.strip(), "rejected_by": reviewer_id,
               "rejected_at": _now()},
    )


async def close_query(
    db: AsyncIOMotorDatabase,
    *,
    query_id: str,
    reviewer_id: str,
    reason_code: Optional[str] = None,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    return await _transition(
        db, QUERIES_COLLECTION, query_id, QUERY_CLOSED,
        transitions=QUERY_TRANSITIONS, states=QUERY_STATES, kind="query",
        expected_version=expected_version,
        extra={"closed_by": reviewer_id, "closed_at": _now(), "close_reason_code": reason_code},
    )


async def reopen_query(
    db: AsyncIOMotorDatabase,
    *,
    query_id: str,
    reviewer_id: str,
    reason: str,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Reopen a closed query.

    REOPENED is a distinct state rather than a return to OPEN, so the record
    shows that this query was once closed and then wasn't — that history is
    exactly what an inspector asks about.
    """
    if not (reason or "").strip():
        raise QueryError("REOPEN_REASON_REQUIRED", "reopening needs a stated reason")
    result = await _transition(
        db, QUERIES_COLLECTION, query_id, QUERY_REOPENED,
        transitions=QUERY_TRANSITIONS, states=QUERY_STATES, kind="query",
        expected_version=expected_version,
        extra={"reopened_by": reviewer_id, "reopened_at": _now(), "reopen_reason": reason.strip()},
    )
    await db[QUERIES_COLLECTION].update_one({"id": query_id}, {"$inc": {"reopen_count": 1}})
    return await get_query(db, query_id) or result


async def cancel_query(
    db: AsyncIOMotorDatabase, *, query_id: str, reviewer_id: str, reason: str,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    if not (reason or "").strip():
        raise QueryError("CANCEL_REASON_REQUIRED", "cancelling needs a stated reason")
    return await _transition(
        db, QUERIES_COLLECTION, query_id, QUERY_CANCELLED,
        transitions=QUERY_TRANSITIONS, states=QUERY_STATES, kind="query",
        expected_version=expected_version,
        extra={"cancelled_by": reviewer_id, "cancelled_at": _now(), "cancel_reason": reason.strip()},
    )


# ═══════════════════════════════════════════════════════════════════════════
# CAPAs
# ═══════════════════════════════════════════════════════════════════════════

async def create_capa(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    created_by: str,
    title: str,
    description: str,
    owner_user_id: str,
    due_date: Any,
    criticality: str = "minor",
    query_id: Optional[str] = None,
) -> Dict[str, Any]:
    """A CAPA always has an owner and a due date.

    Both are required at creation rather than optional-and-filled-in-later:
    a corrective action with nobody accountable and no date is a note, and
    spec §13's conditional-approval rule depends on those two fields existing.
    """
    if criticality not in QUERY_CRITICALITIES:
        raise QueryError("INVALID_CRITICALITY",
                         f"criticality must be one of {', '.join(QUERY_CRITICALITIES)}")
    if not (title or "").strip():
        raise QueryError("TITLE_REQUIRED", "a CAPA needs a title")
    if not (owner_user_id or "").strip():
        raise QueryError("OWNER_REQUIRED", "a CAPA needs an accountable owner")
    parsed_due = _parse_due_date(due_date)

    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "audit_id": audit_id,
        "query_id": query_id,
        "title": title.strip(),
        "description": (description or "").strip(),
        "criticality": criticality,
        "status": CAPA_OPEN,
        "owner_user_id": owner_user_id,
        "due_date": parsed_due,
        "created_by": created_by,
        "completed_by": None,
        "completed_at": None,
        "completion_note": None,
        # The effectiveness review is a separate act by a different party from
        # the one who did the work — stored separately so it cannot be implied.
        "effectiveness_review": None,
        "closed_by": None,
        "closed_at": None,
        "reopen_count": 0,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }
    await db[CAPAS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_capa(db: AsyncIOMotorDatabase, capa_id: str) -> Optional[Dict[str, Any]]:
    return await db[CAPAS_COLLECTION].find_one({"id": capa_id}, {"_id": 0})


async def list_capas(
    db: AsyncIOMotorDatabase, audit_id: str, *, status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"audit_id": audit_id}
    if status:
        query["status"] = status
    return await db[CAPAS_COLLECTION].find(query, {"_id": 0}).sort("due_date", 1).to_list(None)


def can_capa_transition(current: str, target: str) -> bool:
    return target in CAPA_TRANSITIONS.get(current, set())


def is_overdue(capa: Dict[str, Any], *, as_of: Optional[date] = None) -> bool:
    """Derived, never stored.

    A persisted `overdue` flag is wrong the moment the clock passes it and
    nobody has run a sweep. Computing it means the answer can never disagree
    with the calendar. A CAPA that is already closed or cancelled is not
    overdue however old its due date is.
    """
    if capa.get("status") not in CAPA_OUTSTANDING_STATES:
        return False
    due = capa.get("due_date")
    if not due:
        return False
    today = as_of or datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(str(due)[:10]) < today
    except ValueError:
        return False


async def start_capa(
    db: AsyncIOMotorDatabase, *, capa_id: str, actor_id: str,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    return await _transition(
        db, CAPAS_COLLECTION, capa_id, CAPA_IN_PROGRESS,
        transitions=CAPA_TRANSITIONS, states=CAPA_STATES, kind="CAPA",
        expected_version=expected_version,
        extra={"started_by": actor_id, "started_at": _now()},
    )


async def complete_capa(
    db: AsyncIOMotorDatabase, *, capa_id: str, actor_id: str, note: str,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """The owner declares the work done. This is NOT closure — closure
    requires someone else to review whether it actually worked."""
    if not (note or "").strip():
        raise QueryError("COMPLETION_NOTE_REQUIRED",
                         "completing a CAPA needs a note describing what was done")
    return await _transition(
        db, CAPAS_COLLECTION, capa_id, CAPA_COMPLETED,
        transitions=CAPA_TRANSITIONS, states=CAPA_STATES, kind="CAPA",
        expected_version=expected_version,
        extra={"completed_by": actor_id, "completed_at": _now(), "completion_note": note.strip()},
    )


async def record_effectiveness_review(
    db: AsyncIOMotorDatabase,
    *,
    capa_id: str,
    reviewer_id: str,
    outcome: str,
    note: str,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """The effectiveness review, and the only route to CLOSED.

    An EFFECTIVE review closes the CAPA. An INEFFECTIVE one reopens it — the
    work goes back to the owner rather than the finding quietly disappearing,
    and `reopen_count` records that it took more than one attempt.

    Separation of duties is enforced: the person who did the work cannot be
    the person who certifies it worked.
    """
    if outcome not in (EFFECTIVENESS_EFFECTIVE, EFFECTIVENESS_INEFFECTIVE):
        raise QueryError("INVALID_OUTCOME",
                         f"outcome must be {EFFECTIVENESS_EFFECTIVE} or {EFFECTIVENESS_INEFFECTIVE}")
    if not (note or "").strip():
        raise QueryError("REVIEW_NOTE_REQUIRED", "an effectiveness review needs a note")

    current = await get_capa(db, capa_id)
    if current is None:
        raise QueryError("NOT_FOUND", "CAPA not found")
    if current.get("completed_by") and current["completed_by"] == reviewer_id:
        raise QueryError("SELF_REVIEW_FORBIDDEN",
                         "the person who completed a CAPA cannot certify that it was effective")

    review = {
        "outcome": outcome,
        "note": note.strip(),
        "reviewed_by": reviewer_id,
        "reviewed_at": _now(),
    }
    target = CAPA_CLOSED if outcome == EFFECTIVENESS_EFFECTIVE else CAPA_REOPENED
    extra: Dict[str, Any] = {"effectiveness_review": review}
    if target == CAPA_CLOSED:
        extra.update({"closed_by": reviewer_id, "closed_at": _now()})

    result = await _transition(
        db, CAPAS_COLLECTION, capa_id, target,
        transitions=CAPA_TRANSITIONS, states=CAPA_STATES, kind="CAPA",
        expected_status=CAPA_COMPLETED, expected_version=expected_version, extra=extra,
    )
    if target == CAPA_REOPENED:
        await db[CAPAS_COLLECTION].update_one({"id": capa_id}, {"$inc": {"reopen_count": 1}})
        return await get_capa(db, capa_id) or result
    return result


async def cancel_capa(
    db: AsyncIOMotorDatabase, *, capa_id: str, actor_id: str, reason: str,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    if not (reason or "").strip():
        raise QueryError("CANCEL_REASON_REQUIRED", "cancelling needs a stated reason")
    return await _transition(
        db, CAPAS_COLLECTION, capa_id, CAPA_CANCELLED,
        transitions=CAPA_TRANSITIONS, states=CAPA_STATES, kind="CAPA",
        expected_version=expected_version,
        extra={"cancelled_by": actor_id, "cancelled_at": _now(), "cancel_reason": reason.strip()},
    )


# ── Decision blocking & overdue reporting ───────────────────────────────────

async def open_critical_capas(
    db: AsyncIOMotorDatabase, audit_id: str,
) -> List[Dict[str, Any]]:
    """Critical CAPAs that are still outstanding on this audit.

    Used by Phase 7's final decision. Note the use of
    `CAPA_OUTSTANDING_STATES` rather than `status != CLOSED`: a CANCELLED CAPA
    is resolved, and treating it as outstanding would block a decision forever
    over a finding that was withdrawn.
    """
    return await db[CAPAS_COLLECTION].find(
        {"audit_id": audit_id, "criticality": "critical",
         "status": {"$in": sorted(CAPA_OUTSTANDING_STATES)}},
        {"_id": 0},
    ).to_list(None)


async def overdue_capas(
    db: AsyncIOMotorDatabase, *, audit_id: Optional[str] = None, as_of: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Every outstanding CAPA past its due date.

    Filtered in Mongo by state and date, then re-checked with `is_overdue` so
    the query and the predicate can never drift apart.
    """
    today = (as_of or datetime.now(timezone.utc).date()).isoformat()
    query: Dict[str, Any] = {
        "status": {"$in": sorted(CAPA_OUTSTANDING_STATES)},
        "due_date": {"$ne": None, "$lt": today},
    }
    if audit_id:
        query["audit_id"] = audit_id
    candidates = await db[CAPAS_COLLECTION].find(query, {"_id": 0}).to_list(None)
    reference = as_of or datetime.now(timezone.utc).date()
    return [c for c in candidates if is_overdue(c, as_of=reference)]


async def referencing_queries(
    db: AsyncIOMotorDatabase, *, linked_entity_id: str,
) -> List[Dict[str, Any]]:
    """Open queries pointing at an entity.

    Callers use this before deleting equipment/facilities/documents: removing
    something a live query refers to would leave the audit record pointing at
    nothing, which is worse than refusing the deletion.
    """
    return await db[QUERIES_COLLECTION].find(
        {"linked_entity_id": linked_entity_id,
         "status": {"$in": [QUERY_OPEN, QUERY_RESPONDED, QUERY_REOPENED]}},
        {"_id": 0},
    ).to_list(None)
