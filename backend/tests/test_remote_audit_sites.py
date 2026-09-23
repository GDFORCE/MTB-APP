"""Integration tests for remote_audit_sites / remote_audit_geofence_versions
storage (app/services/remote_audit/sites.py) — versioned geofences, spec
§7.1: approving a new version never mutates or deletes an old one, and
invalid geometry is rejected before it's ever stored.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit import sites as s  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402
from app.services.remote_audit.geometry import InvalidGeometryError  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]

VALID_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[(77.0, 28.0), (77.002, 28.0), (77.002, 28.0018), (77.0, 28.0018), (77.0, 28.0)]],
}
INVALID_GEOMETRY = {"type": "Polygon", "coordinates": [[(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)]]}


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await s.ensure_indexes(database)
    yield database
    await database[s.SITES_COLLECTION].delete_many({"organization_id": f"org-{RUN_ID}"})
    await database[s.GEOFENCE_VERSIONS_COLLECTION].delete_many({"site_id": {"$regex": f"^site-{RUN_ID}"}})
    database.client.close()


@pytest.mark.asyncio
async def test_create_and_get_site(db):
    site = await s.create_site(
        db, organization_id=f"org-{RUN_ID}", official_name="Test Hospital",
        address={"line1": "1 Main St"}, timezone_name="Asia/Kolkata",
    )
    fetched = await s.get_site(db, site["id"])
    assert fetched["official_name"] == "Test Hospital"


@pytest.mark.asyncio
async def test_invalid_geometry_is_rejected_before_storage(db):
    site_id = f"site-{RUN_ID}-invalid"
    with pytest.raises(InvalidGeometryError):
        await s.propose_geofence_version(db, site_id=site_id, geometry=INVALID_GEOMETRY,
                                          buffer_m=10.0, proposed_by="user-1")
    versions = await s.list_geofence_versions(db, site_id)
    assert versions == []  # nothing was stored


@pytest.mark.asyncio
async def test_versions_increment_and_approval_supersedes_the_previous_one(db):
    site_id = f"site-{RUN_ID}-versioning"
    v1 = await s.propose_geofence_version(db, site_id=site_id, geometry=VALID_GEOMETRY,
                                           buffer_m=10.0, proposed_by="user-1")
    assert v1["version"] == 1
    approved_v1 = await s.approve_geofence_version(db, geofence_version_id=v1["id"], approved_by="approver-1")
    assert approved_v1["status"] == "approved"

    v2 = await s.propose_geofence_version(db, site_id=site_id, geometry=VALID_GEOMETRY,
                                           buffer_m=20.0, proposed_by="user-1")
    assert v2["version"] == 2
    approved_v2 = await s.approve_geofence_version(db, geofence_version_id=v2["id"], approved_by="approver-1")
    assert approved_v2["status"] == "approved"

    # v1 is superseded, not deleted or mutated beyond its status --
    # historical evidence classified against it stays explicable.
    versions = await s.list_geofence_versions(db, site_id)
    assert len(versions) == 2
    v1_after = next(v for v in versions if v["id"] == v1["id"])
    assert v1_after["status"] == "superseded"
    assert v1_after["buffer_m"] == 10.0  # untouched

    active = await s.get_active_geofence(db, site_id)
    assert active["id"] == v2["id"]


@pytest.mark.asyncio
async def test_only_one_approved_version_exists_at_a_time(db):
    site_id = f"site-{RUN_ID}-single-active"
    v1 = await s.propose_geofence_version(db, site_id=site_id, geometry=VALID_GEOMETRY,
                                           buffer_m=10.0, proposed_by="user-1")
    await s.approve_geofence_version(db, geofence_version_id=v1["id"], approved_by="approver-1")
    v2 = await s.propose_geofence_version(db, site_id=site_id, geometry=VALID_GEOMETRY,
                                           buffer_m=15.0, proposed_by="user-1")
    await s.approve_geofence_version(db, geofence_version_id=v2["id"], approved_by="approver-1")

    versions = await s.list_geofence_versions(db, site_id)
    approved = [v for v in versions if v["status"] == "approved"]
    assert len(approved) == 1
    assert approved[0]["id"] == v2["id"]
