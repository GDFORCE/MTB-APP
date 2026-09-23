"""Step-up re-authentication for sensitive remote-audit actions (final
decision, geofence approval, template activation, critical override).

Production-Grade Implementation Plan v1.3 correction #2: a password-based
step-up token is the DEVELOPMENT/PILOT fallback only. Production requires the
token to be minted from an MFA-authenticated session once platform-wide MFA
exists (none does yet, anywhere in this codebase). The token *primitive* --
short-lived, one-time-use, scoped to one user and one specific action -- is
identical either way; only what's required to mint one changes, controlled
by `REMOTE_AUDIT_STEPUP_REQUIRES_MFA` (defaults to the safe/production
choice, `true`, when unset — an explicit opt-out is required, never an
accidental default).
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

STEPUP_TOKENS_COLLECTION = "remote_audit_stepup_tokens"
DEFAULT_TTL_SECONDS = 300  # 5 minutes


class StepUpError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def stepup_requires_mfa() -> bool:
    raw = os.environ.get("REMOTE_AUDIT_STEPUP_REQUIRES_MFA", "true").strip().lower()
    return raw not in ("0", "false", "no")


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[STEPUP_TOKENS_COLLECTION].create_index("token", unique=True)
    await db[STEPUP_TOKENS_COLLECTION].create_index("expires_at", expireAfterSeconds=0)


async def issue_stepup_token(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    action_scope: str,
    mfa_verified: bool,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Dict[str, Any]:
    """`mfa_verified` is supplied by the caller (the route handler) — the one
    place that knows whether the session behind this request was actually
    MFA-authenticated, as opposed to a bare password re-entry. Refuses to
    mint a token when this environment requires MFA and the caller wasn't
    MFA-verified: "password-only step-up = dev/pilot fallback" is enforced
    here, not left as a comment for someone to remember.
    """
    if stepup_requires_mfa() and not mfa_verified:
        raise StepUpError(
            "MFA_REQUIRED",
            "this environment requires an MFA-authenticated session to step up for this action",
        )
    now = datetime.now(timezone.utc)
    doc = {
        "token": secrets.token_urlsafe(32),
        "user_id": user_id,
        "action_scope": action_scope,
        "created_at": now,
        "expires_at": now + timedelta(seconds=ttl_seconds),
        "consumed_at": None,
    }
    await db[STEPUP_TOKENS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def consume_stepup_token(
    db: AsyncIOMotorDatabase, *, token: Optional[str], user_id: str, action_scope: str,
) -> bool:
    """One-time use, scope-bound, user-bound. Returns False rather than
    raising on any mismatch (missing/expired/wrong-scope/wrong-user/already
    consumed) so callers map to their own error response uniformly."""
    if not token:
        return False
    now = datetime.now(timezone.utc)
    result = await db[STEPUP_TOKENS_COLLECTION].find_one_and_update(
        {
            "token": token, "user_id": user_id, "action_scope": action_scope,
            "consumed_at": None, "expires_at": {"$gt": now},
        },
        {"$set": {"consumed_at": now}},
    )
    return result is not None
