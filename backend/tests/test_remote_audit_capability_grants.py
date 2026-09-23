"""Tests for explicit capability grants
(app/services/remote_audit/capability_grants.py) — v1.3 correction #8.
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

from app.services.remote_audit import capability_grants as cg  # noqa: E402
from app.services.remote_audit.capabilities import RemoteAuditCapability, capabilities_for  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await cg.ensure_indexes(database)
    yield database
    await database[cg.GRANTS_COLLECTION].delete_many({"user_id": {"$regex": f"^{RUN_ID}"}})
    database.client.close()


@pytest.mark.asyncio
async def test_grant_and_fetch_round_trips(db):
    user_id = f"{RUN_ID}-admin1"
    await cg.grant_capability(db, user_id=user_id, capability=RemoteAuditCapability.TEMPLATE_ADMIN, granted_by="root")
    grants = await cg.fetch_explicit_grants(db, user_id)
    assert RemoteAuditCapability.TEMPLATE_ADMIN in grants


@pytest.mark.asyncio
async def test_admin_role_without_explicit_grant_does_not_get_template_admin_end_to_end(db):
    """The full chain: fetch (empty) grants -> feed into capabilities_for ->
    confirm admin role still doesn't have template_admin."""
    user_id = f"{RUN_ID}-admin2"
    grants = await cg.fetch_explicit_grants(db, user_id)
    caps = capabilities_for("admin", explicit_grants=grants)
    assert RemoteAuditCapability.TEMPLATE_ADMIN not in caps


@pytest.mark.asyncio
async def test_revoke_removes_the_grant(db):
    user_id = f"{RUN_ID}-admin3"
    await cg.grant_capability(db, user_id=user_id, capability=RemoteAuditCapability.TEMPLATE_ADMIN, granted_by="root")
    removed = await cg.revoke_capability(db, user_id=user_id, capability=RemoteAuditCapability.TEMPLATE_ADMIN)
    assert removed is True
    grants = await cg.fetch_explicit_grants(db, user_id)
    assert RemoteAuditCapability.TEMPLATE_ADMIN not in grants


@pytest.mark.asyncio
async def test_regrant_is_idempotent_not_a_duplicate_error(db):
    user_id = f"{RUN_ID}-admin4"
    await cg.grant_capability(db, user_id=user_id, capability=RemoteAuditCapability.TEMPLATE_ADMIN, granted_by="root")
    await cg.grant_capability(db, user_id=user_id, capability=RemoteAuditCapability.TEMPLATE_ADMIN, granted_by="root2")
    grants = await cg.fetch_explicit_grants(db, user_id)
    assert len(grants) == 1
