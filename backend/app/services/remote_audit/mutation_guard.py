"""The shared remote-audit mutation guard — Production-Grade Implementation
Plan v1.3 correction #9: every state-changing endpoint in this module is
wrapped in ONE reusable set of controls instead of each route re-implementing
them, so a Phase 10 test sweep can prove every endpoint actually has them
rather than trusting each author remembered to.

This module provides the building blocks (idempotency, capability/authz
errors with stable machine codes, optimistic locking, rate limiting);
`remote_audit_routes.py` (Phase 2+) wires them into FastAPI dependencies once
there are endpoints to wrap. No feature endpoints exist yet in Phase 1 --
these are proven here in isolation, against the four core rejections the plan
calls out: missing capability, wrong org, replayed idempotency key, stale
version.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .capabilities import RemoteAuditCapability, capabilities_for

IDEMPOTENCY_COLLECTION = "remote_audit_idempotency"
RATE_LIMIT_COLLECTION = "remote_audit_rate_limits"


class RemoteAuditError(Exception):
    """Base for every stable, machine-readable remote-audit error (spec
    §14.1: 'API errors use stable machine codes... plus safe human
    messages')."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundOrForbidden(RemoteAuditError):
    """Raised for ANY authorization/tenant mismatch. Always maps to HTTP 404,
    never 403 -- the plan's resource-level ABAC deliberately does not
    distinguish 'exists but you can't see it' from 'doesn't exist', so a
    caller can never use the error to enumerate audits/sites they don't have
    access to."""

    def __init__(self, message: str = "Resource not found"):
        super().__init__("RESOURCE_NOT_FOUND", message)


class IdempotencyKeyConflict(RemoteAuditError):
    def __init__(self, message: str = "This idempotency key was already used for a different request"):
        super().__init__("IDEMPOTENCY_KEY_CONFLICT", message)


class IdempotencyInProgress(RemoteAuditError):
    def __init__(self, message: str = "A request with this idempotency key is already being processed"):
        super().__init__("IDEMPOTENCY_IN_PROGRESS", message)


class OptimisticLockFailed(RemoteAuditError):
    def __init__(self, message: str = "This resource was modified by someone else; reload and retry"):
        super().__init__("STALE_RESOURCE_VERSION", message)


class RateLimitExceeded(RemoteAuditError):
    def __init__(self, message: str = "Rate limit exceeded for this action"):
        super().__init__("RATE_LIMIT_EXCEEDED", message)


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def fingerprint_request(payload: Dict[str, Any]) -> str:
    """A stable hash of the request body, used to tell a genuine retry
    (same key, same payload -> replay the stored result) apart from an
    idempotency key reused for a different request (a client bug -> reject,
    never silently execute the new payload under the old key)."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_capability(
    role: str,
    required: RemoteAuditCapability,
    *,
    explicit_grants: FrozenSet[RemoteAuditCapability] = frozenset(),
) -> None:
    if required not in capabilities_for(role, explicit_grants=explicit_grants):
        raise NotFoundOrForbidden()


def assert_version_matches(doc: Dict[str, Any], expected_version: int) -> None:
    if doc.get("version") != expected_version:
        raise OptimisticLockFailed()


@dataclass(frozen=True)
class IdempotentResult:
    status_code: int
    response_body: Dict[str, Any]
    replayed: bool  # True if this is a stored result from a prior call, not a fresh one


class IdempotencyStore:
    """Every remote-audit mutation accepts an idempotency key; the server
    stores the request's fingerprint + result and replays that stored result
    on a retry rather than re-executing (v1.3 correction #1) -- so a
    duplicate request can never create a second evidence row, query, CAPA,
    or decision."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db[IDEMPOTENCY_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._col.create_index([("scope", 1), ("key", 1)], unique=True)

    async def begin(self, *, scope: str, key: str, payload: Dict[str, Any]) -> Optional[IdempotentResult]:
        """Call BEFORE executing the mutation. Returns a stored
        `IdempotentResult` if this is a replay of an already-completed
        request (the caller should return it directly without re-executing).
        Returns None if this is a genuinely new request (the caller must
        then call `complete()` after executing, exactly once).
        Raises `IdempotencyInProgress` if the same key is currently being
        processed by a concurrent request, and `IdempotencyKeyConflict` if
        the same key was previously used for a different request body.
        """
        fp = fingerprint_request(payload)
        now = datetime.now(timezone.utc)
        try:
            await self._col.insert_one({
                "scope": scope, "key": key, "fingerprint": fp,
                "status": "pending", "created_at": now,
            })
            return None  # freshly reserved -- proceed with execution
        except DuplicateKeyError:
            existing = await self._col.find_one({"scope": scope, "key": key})
            if existing is None:  # raced with a delete; treat as fresh
                return None
            if existing["fingerprint"] != fp:
                raise IdempotencyKeyConflict()
            if existing["status"] == "pending":
                raise IdempotencyInProgress()
            return IdempotentResult(
                status_code=existing["status_code"], response_body=existing["response_body"],
                replayed=True,
            )

    async def complete(self, *, scope: str, key: str, status_code: int, response_body: Dict[str, Any]) -> None:
        await self._col.update_one(
            {"scope": scope, "key": key},
            {"$set": {
                "status": "completed", "status_code": status_code,
                "response_body": response_body, "completed_at": datetime.now(timezone.utc),
            }},
        )

    async def release_without_completing(self, *, scope: str, key: str) -> None:
        """Call if execution raised an exception AFTER reserving the key, so
        a genuinely-new retry isn't permanently blocked by a 'pending'
        record that will never complete."""
        await self._col.delete_one({"scope": scope, "key": key, "status": "pending"})


async def check_rate_limit(
    db: AsyncIOMotorDatabase,
    key: str,
    *,
    limit: int,
    window_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    """Fixed-window rate limiting, atomic via a single `find_one_and_update`.
    This is the INNER, application-level layer the shared mutation dependency
    applies per action (e.g. capture-session creation); a production
    deployment additionally puts a WAF/API-gateway rate limiter in front of
    the API as the outer layer (docs/remote-audit/web-and-privacy-controls.md)
    -- this function does not attempt to be that outer layer.
    Returns True if the call is allowed, False if the limit is exceeded.
    """
    now = now or datetime.now(timezone.utc)
    window_id = int(now.timestamp() // window_seconds)
    doc_id = f"{key}:{window_id}"
    result = await db[RATE_LIMIT_COLLECTION].find_one_and_update(
        {"id": doc_id},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {"key": key, "window_id": window_id, "created_at": now},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result["count"] <= limit
