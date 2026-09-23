"""Explicit capability grants — v1.3 correction #8: some capabilities (most
notably `remote_audit.template_admin`) must never be implied by a broad role
like `admin` or `org_admin`; they require an explicit, separately-recorded,
auditable grant. This is a generic store so any capability can use the same
mechanism, though `template_admin` is the only one that needs it today (the
audit-participant model in participants.py already covers reviewer/approver/
capturer/site_admin per-audit).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List

from motor.motor_asyncio import AsyncIOMotorDatabase

from .capabilities import RemoteAuditCapability

GRANTS_COLLECTION = "remote_audit_capability_grants"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[GRANTS_COLLECTION].create_index([("user_id", 1), ("capability", 1)], unique=True)


async def grant_capability(
    db: AsyncIOMotorDatabase, *, user_id: str, capability: RemoteAuditCapability, granted_by: str,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc = {"user_id": user_id, "capability": capability.value, "granted_by": granted_by, "granted_at": now}
    await db[GRANTS_COLLECTION].update_one(
        {"user_id": user_id, "capability": capability.value}, {"$set": doc}, upsert=True,
    )
    return doc


async def revoke_capability(db: AsyncIOMotorDatabase, *, user_id: str, capability: RemoteAuditCapability) -> bool:
    result = await db[GRANTS_COLLECTION].delete_one({"user_id": user_id, "capability": capability.value})
    return result.deleted_count == 1


async def fetch_explicit_grants(db: AsyncIOMotorDatabase, user_id: str) -> FrozenSet[RemoteAuditCapability]:
    docs: List[Dict[str, Any]] = await db[GRANTS_COLLECTION].find(
        {"user_id": user_id}, {"_id": 0, "capability": 1},
    ).to_list(None)
    return frozenset(RemoteAuditCapability(d["capability"]) for d in docs)
