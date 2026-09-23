"""Minimal `remote_audits` shell — just enough for Phase 2's capture
sessions/evidence to attach to a real audit record (spec §14's
`POST /v1/audits`). Full template-driven creation, facilities/equipment, and
the review/decision workflow are later phases (3, 5, 7) — deliberately thin.

Cross-organization tenant model from day one (v1.3 correction #7): an audit
always carries BOTH the owning (sponsor/CRO) organization and the site's
organization as distinct fields — never collapsed into one, since in this
domain they are, definitionally, different organizations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

AUDITS_COLLECTION = "remote_audits"

STATE_DRAFT = "draft"
STATE_IN_PROGRESS = "in_progress"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[AUDITS_COLLECTION].create_index("id", unique=True)
    await db[AUDITS_COLLECTION].create_index("audit_owner_org_id")
    await db[AUDITS_COLLECTION].create_index("site_id")


async def create_audit(
    db: AsyncIOMotorDatabase,
    *,
    audit_owner_org_id: str,
    site_id: str,
    site_org_id: str,
    created_by: str,
    jurisdiction: Optional[str] = None,
    governing_authority: Optional[str] = None,
    template_id: Optional[str] = None,
    template_version: Optional[str] = None,
    regulated_system_of_record: bool = False,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid.uuid4()),
        "audit_owner_org_id": audit_owner_org_id,
        "site_id": site_id,
        "site_org_id": site_org_id,
        "jurisdiction": jurisdiction,
        "governing_authority": governing_authority,
        "template_id": template_id,
        "template_version": template_version,
        "regulated_system_of_record": regulated_system_of_record,
        "state": STATE_DRAFT,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }
    await db[AUDITS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_audit(db: AsyncIOMotorDatabase, audit_id: str) -> Optional[Dict[str, Any]]:
    return await db[AUDITS_COLLECTION].find_one({"id": audit_id}, {"_id": 0})
