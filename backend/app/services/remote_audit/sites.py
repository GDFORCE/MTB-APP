"""Mongo-backed storage for `remote_audit_sites` and
`remote_audit_geofence_versions` (spec §7.1, §6). Data-model layer only —
Phase 1 scope is the collections, indexes, and versioned storage semantics;
the propose/approve HTTP endpoints and reviewer map UI are Phase 3.

Every geofence version is immutable once created: approving a new version
never mutates or deletes an old one (spec §7.1 step 5) — it's superseded, and
evidence already classified against an old version keeps that classification
rather than being silently reclassified against a later edit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from .geometry import Geometry, validate_geometry

SITES_COLLECTION = "remote_audit_sites"
GEOFENCE_VERSIONS_COLLECTION = "remote_audit_geofence_versions"

STATUS_PROPOSED = "proposed"
STATUS_APPROVED = "approved"
STATUS_SUPERSEDED = "superseded"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[SITES_COLLECTION].create_index("id", unique=True)
    await db[SITES_COLLECTION].create_index("organization_id")
    await db[GEOFENCE_VERSIONS_COLLECTION].create_index("id", unique=True)
    await db[GEOFENCE_VERSIONS_COLLECTION].create_index([("site_id", 1), ("version", -1)])
    await db[GEOFENCE_VERSIONS_COLLECTION].create_index([("site_id", 1), ("status", 1)])
    # 2dsphere for storage-side spatial queries (e.g. "sites near this point"
    # in a future admin tool) -- point-in-polygon classification itself is
    # done by the pure geometry module, not this index (v1.3 correction #16).
    await db[GEOFENCE_VERSIONS_COLLECTION].create_index([("geometry", "2dsphere")])


async def create_site(
    db: AsyncIOMotorDatabase,
    *,
    organization_id: str,
    official_name: str,
    address: Dict[str, Any],
    timezone_name: str,
) -> Dict[str, Any]:
    doc = {
        "id": str(uuid.uuid4()),
        "organization_id": organization_id,
        "official_name": official_name,
        "address": address,
        "timezone": timezone_name,
        "created_at": datetime.now(timezone.utc),
    }
    await db[SITES_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_site(db: AsyncIOMotorDatabase, site_id: str) -> Optional[Dict[str, Any]]:
    return await db[SITES_COLLECTION].find_one({"id": site_id}, {"_id": 0})


async def propose_geofence_version(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    geometry: Geometry,
    buffer_m: float,
    proposed_by: str,
) -> Dict[str, Any]:
    """Validates the geometry (rejecting self-intersecting/out-of-range/
    unsplit-antimeridian geometry at write time, per v1.3 correction #16)
    before ever storing it — never a silently-broken geofence."""
    validate_geometry(geometry)  # raises InvalidGeometryError, letting the caller decide the HTTP mapping
    latest = await db[GEOFENCE_VERSIONS_COLLECTION].find_one(
        {"site_id": site_id}, sort=[("version", -1)]
    )
    next_version = (latest["version"] + 1) if latest else 1
    doc = {
        "id": str(uuid.uuid4()),
        "site_id": site_id,
        "version": next_version,
        "geometry": geometry,
        "buffer_m": buffer_m,
        "status": STATUS_PROPOSED,
        "proposed_by": proposed_by,
        "proposed_at": datetime.now(timezone.utc),
        "approved_by": None,
        "approved_at": None,
    }
    await db[GEOFENCE_VERSIONS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def approve_geofence_version(
    db: AsyncIOMotorDatabase, *, geofence_version_id: str, approved_by: str,
) -> Dict[str, Any]:
    """Approving a version supersedes the previously-approved one (if any) —
    the old version row is kept, never deleted or mutated beyond its status,
    so historical evidence classified against it remains explicable."""
    doc = await db[GEOFENCE_VERSIONS_COLLECTION].find_one({"id": geofence_version_id})
    if doc is None:
        raise ValueError("geofence version not found")
    now = datetime.now(timezone.utc)
    await db[GEOFENCE_VERSIONS_COLLECTION].update_many(
        {"site_id": doc["site_id"], "status": STATUS_APPROVED},
        {"$set": {"status": STATUS_SUPERSEDED, "superseded_at": now}},
    )
    await db[GEOFENCE_VERSIONS_COLLECTION].update_one(
        {"id": geofence_version_id},
        {"$set": {"status": STATUS_APPROVED, "approved_by": approved_by, "approved_at": now}},
    )
    return await db[GEOFENCE_VERSIONS_COLLECTION].find_one({"id": geofence_version_id}, {"_id": 0})


async def get_active_geofence(db: AsyncIOMotorDatabase, site_id: str) -> Optional[Dict[str, Any]]:
    return await db[GEOFENCE_VERSIONS_COLLECTION].find_one(
        {"site_id": site_id, "status": STATUS_APPROVED}, {"_id": 0}
    )


async def list_geofence_versions(db: AsyncIOMotorDatabase, site_id: str) -> List[Dict[str, Any]]:
    return await db[GEOFENCE_VERSIONS_COLLECTION].find(
        {"site_id": site_id}, {"_id": 0}
    ).sort("version", 1).to_list(None)
