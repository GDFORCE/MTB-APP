"""Mongo-backed persistence for `remote_audit_participants` and
`remote_audit_site_approvers` — the cross-organization tenant model from
Production-Grade Implementation Plan v1.3 correction #7/#3.

Thin data-access layer only: it fetches grant records and calls the pure
decision functions in capabilities.py. Audit-event emission for grant
changes stays with the caller (route handlers, Phase 2+), matching this
codebase's existing convention (org_routes.py calls `write_audit()` itself
after each mutation rather than burying it in a service function).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet

from motor.motor_asyncio import AsyncIOMotorDatabase

from .capabilities import (
    AuditAccessDecision,
    ParticipantGrant,
    SiteApproverGrant,
    can_approve_geofence,
    resolve_audit_access,
)

PARTICIPANTS_COLLECTION = "remote_audit_participants"
SITE_APPROVERS_COLLECTION = "remote_audit_site_approvers"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[PARTICIPANTS_COLLECTION].create_index(
        [("audit_id", 1), ("user_id", 1)], unique=True)
    await db[SITE_APPROVERS_COLLECTION].create_index(
        [("site_id", 1), ("user_id", 1)], unique=True)


async def add_participant(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    user_id: str,
    role_in_audit: str,
    assigned_by: str,
) -> Dict[str, Any]:
    """Upserts a participant grant. Idempotent by (audit_id, user_id): a
    re-assignment updates the role rather than creating a duplicate grant."""
    now = datetime.now(timezone.utc)
    doc = {
        "audit_id": audit_id, "user_id": user_id, "role_in_audit": role_in_audit,
        "assigned_by": assigned_by, "assigned_at": now,
    }
    await db[PARTICIPANTS_COLLECTION].update_one(
        {"audit_id": audit_id, "user_id": user_id},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def remove_participant(db: AsyncIOMotorDatabase, *, audit_id: str, user_id: str) -> bool:
    result = await db[PARTICIPANTS_COLLECTION].delete_one({"audit_id": audit_id, "user_id": user_id})
    return result.deleted_count == 1


async def fetch_participants(db: AsyncIOMotorDatabase, audit_id: str) -> FrozenSet[ParticipantGrant]:
    docs = await db[PARTICIPANTS_COLLECTION].find({"audit_id": audit_id}, {"_id": 0}).to_list(None)
    return frozenset(
        ParticipantGrant(user_id=d["user_id"], role_in_audit=d["role_in_audit"])
        for d in docs
    )


async def resolve_access_for_audit(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    user_org_id: str,
    audit_owner_org_id: str,
    site_org_id: str,
    audit_id: str,
) -> AuditAccessDecision:
    participants = await fetch_participants(db, audit_id)
    return resolve_audit_access(
        user_id=user_id, user_org_id=user_org_id,
        audit_owner_org_id=audit_owner_org_id, site_org_id=site_org_id,
        participants=participants,
    )


async def add_site_approver(
    db: AsyncIOMotorDatabase, *, site_id: str, user_id: str, granted_by: str,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc = {"site_id": site_id, "user_id": user_id, "granted_by": granted_by, "granted_at": now}
    await db[SITE_APPROVERS_COLLECTION].update_one(
        {"site_id": site_id, "user_id": user_id}, {"$set": doc}, upsert=True,
    )
    return doc


async def remove_site_approver(db: AsyncIOMotorDatabase, *, site_id: str, user_id: str) -> bool:
    result = await db[SITE_APPROVERS_COLLECTION].delete_one({"site_id": site_id, "user_id": user_id})
    return result.deleted_count == 1


async def fetch_site_approvers(db: AsyncIOMotorDatabase, site_id: str) -> FrozenSet[SiteApproverGrant]:
    docs = await db[SITE_APPROVERS_COLLECTION].find({"site_id": site_id}, {"_id": 0}).to_list(None)
    return frozenset(SiteApproverGrant(user_id=d["user_id"], site_id=d["site_id"]) for d in docs)


async def can_user_approve_geofence(db: AsyncIOMotorDatabase, *, user_id: str, site_id: str) -> bool:
    grants = await fetch_site_approvers(db, site_id)
    return can_approve_geofence(user_id=user_id, site_id=site_id, grants=grants)
