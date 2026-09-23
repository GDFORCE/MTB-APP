"""Integration tests for the Mongo-backed participant/site-approver
persistence layer (app/services/remote_audit/participants.py), proving the
DB-facing wrapper produces the same cross-organization access decisions as
the pure functions it wraps (already unit-tested directly in
test_remote_audit_capabilities.py).
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

from app.services.remote_audit import participants as p  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
AUDIT_ID = f"audit-{RUN_ID}"
SITE_ID = f"site-{RUN_ID}"
OWNER_ORG = f"org-sponsor-{RUN_ID}"
SITE_ORG = f"org-hospital-{RUN_ID}"


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await p.ensure_indexes(database)
    yield database
    await database[p.PARTICIPANTS_COLLECTION].delete_many({"audit_id": AUDIT_ID})
    await database[p.SITE_APPROVERS_COLLECTION].delete_many({"site_id": SITE_ID})
    database.client.close()


@pytest.mark.asyncio
async def test_add_and_fetch_participant_round_trips(db):
    await p.add_participant(db, audit_id=AUDIT_ID, user_id="u1", role_in_audit="reviewer", assigned_by="admin-1")
    grants = await p.fetch_participants(db, AUDIT_ID)
    assert any(g.user_id == "u1" and g.role_in_audit == "reviewer" for g in grants)


@pytest.mark.asyncio
async def test_reassigning_a_participant_updates_role_not_duplicates(db):
    await p.add_participant(db, audit_id=AUDIT_ID, user_id="u1", role_in_audit="reviewer", assigned_by="admin-1")
    await p.add_participant(db, audit_id=AUDIT_ID, user_id="u1", role_in_audit="approver", assigned_by="admin-1")
    grants = await p.fetch_participants(db, AUDIT_ID)
    matching = [g for g in grants if g.user_id == "u1"]
    assert len(matching) == 1
    assert matching[0].role_in_audit == "approver"


@pytest.mark.asyncio
async def test_remove_participant(db):
    await p.add_participant(db, audit_id=AUDIT_ID, user_id="u1", role_in_audit="reviewer", assigned_by="admin-1")
    removed = await p.remove_participant(db, audit_id=AUDIT_ID, user_id="u1")
    assert removed is True
    grants = await p.fetch_participants(db, AUDIT_ID)
    assert not any(g.user_id == "u1" for g in grants)


@pytest.mark.asyncio
async def test_resolve_access_for_audit_matches_the_pure_function_behavior(db):
    await p.add_participant(db, audit_id=AUDIT_ID, user_id="assigned-reviewer",
                             role_in_audit="reviewer", assigned_by="admin-1")

    assigned_decision = await p.resolve_access_for_audit(
        db, user_id="assigned-reviewer", user_org_id=OWNER_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG, audit_id=AUDIT_ID,
    )
    assert assigned_decision.can_act is True

    unassigned_decision = await p.resolve_access_for_audit(
        db, user_id="unassigned-reviewer", user_org_id=OWNER_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG, audit_id=AUDIT_ID,
    )
    assert unassigned_decision.can_list is True
    assert unassigned_decision.can_act is False

    other_org_decision = await p.resolve_access_for_audit(
        db, user_id="stranger", user_org_id=f"org-other-{RUN_ID}",
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG, audit_id=AUDIT_ID,
    )
    assert other_org_decision.can_list is False


@pytest.mark.asyncio
async def test_site_approver_grant_round_trips_and_gates_geofence_approval(db):
    assert await p.can_user_approve_geofence(db, user_id="approver-1", site_id=SITE_ID) is False
    await p.add_site_approver(db, site_id=SITE_ID, user_id="approver-1", granted_by="admin-1")
    assert await p.can_user_approve_geofence(db, user_id="approver-1", site_id=SITE_ID) is True
    assert await p.can_user_approve_geofence(db, user_id="someone-else", site_id=SITE_ID) is False

    removed = await p.remove_site_approver(db, site_id=SITE_ID, user_id="approver-1")
    assert removed is True
    assert await p.can_user_approve_geofence(db, user_id="approver-1", site_id=SITE_ID) is False
