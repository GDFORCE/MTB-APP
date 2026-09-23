"""Audit templates — spec §9.1: facility/equipment/document/evidence
requirements defined as versioned, JSON-driven configuration so different
sponsors/regulators/therapeutic areas share one engine, never hard-coded
into the application.

Weight-sum validation happens at ACTIVATION time (spec §16.11's "weight
totals not 100%" edge case), not left to silently break Phase 7's scoring
later. Activating a new version supersedes the previously active one in the
same template family — the same versioning discipline as geofences
(sites.py): old versions are retained, never deleted or mutated, so an
audit created against an old template version stays explicable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

TEMPLATES_COLLECTION = "remote_audit_templates"

STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"

_WEIGHT_TOTAL_TOLERANCE = 0.01


class TemplateValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_scoring_policy_weights(scoring_policy: Dict[str, Dict[str, float]]) -> None:
    """Each axis's weights (e.g. ComplianceScore's factors, EvidenceConfidence's
    factors — spec §13.2) must sum to 100, checked here rather than
    discovered later when Phase 7 tries to compute a score."""
    if not scoring_policy:
        raise TemplateValidationError("EMPTY_SCORING_POLICY", "a template must define at least one scoring axis")
    for axis_name, weights in scoring_policy.items():
        if not weights:
            raise TemplateValidationError("EMPTY_AXIS_WEIGHTS", f"axis {axis_name!r} has no weighted factors")
        total = sum(weights.values())
        if abs(total - 100.0) > _WEIGHT_TOTAL_TOLERANCE:
            raise TemplateValidationError(
                "WEIGHT_TOTAL_INVALID", f"axis {axis_name!r} weights sum to {total}, not 100")


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[TEMPLATES_COLLECTION].create_index("id", unique=True)
    await db[TEMPLATES_COLLECTION].create_index([("family_id", 1), ("version", -1)])
    await db[TEMPLATES_COLLECTION].create_index([("family_id", 1), ("status", 1)])


async def create_template_draft(
    db: AsyncIOMotorDatabase,
    *,
    owner_org_id: str,
    jurisdiction: Optional[str],
    scoring_policy_json: Dict[str, Any],
    requirement_items: List[Dict[str, Any]],
    created_by: str,
    family_id: Optional[str] = None,
) -> Dict[str, Any]:
    """`family_id` groups versions of "the same template" across edits --
    omit it to start a brand-new template family; pass an existing family's
    id to draft the next version of it."""
    now = datetime.now(timezone.utc)
    family_id = family_id or str(uuid.uuid4())
    latest = await db[TEMPLATES_COLLECTION].find_one({"family_id": family_id}, sort=[("version", -1)])
    version = (latest["version"] + 1) if latest else 1
    doc = {
        "id": str(uuid.uuid4()), "family_id": family_id, "version": version,
        "owner_org_id": owner_org_id, "jurisdiction": jurisdiction,
        "scoring_policy_json": scoring_policy_json, "requirement_items": requirement_items,
        "status": STATUS_DRAFT, "created_by": created_by, "created_at": now,
        "activated_by": None, "activated_at": None,
    }
    await db[TEMPLATES_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def get_template(db: AsyncIOMotorDatabase, template_id: str) -> Optional[Dict[str, Any]]:
    return await db[TEMPLATES_COLLECTION].find_one({"id": template_id}, {"_id": 0})


async def activate_template(db: AsyncIOMotorDatabase, template_id: str, *, activated_by: str) -> Dict[str, Any]:
    """Raises TemplateValidationError (never activates a broken template)
    if the weights don't sum to 100 for every scoring axis. Supersedes the
    family's previously active version — old versions are retained, never
    deleted, and an in-flight audit keeps using the version it was created
    against (v1.3-consistent versioning discipline)."""
    template = await get_template(db, template_id)
    if template is None:
        raise ValueError(f"template {template_id!r} not found")
    validate_scoring_policy_weights(template["scoring_policy_json"])

    now = datetime.now(timezone.utc)
    await db[TEMPLATES_COLLECTION].update_many(
        {"family_id": template["family_id"], "status": STATUS_ACTIVE},
        {"$set": {"status": STATUS_SUPERSEDED, "superseded_at": now}},
    )
    await db[TEMPLATES_COLLECTION].update_one(
        {"id": template_id},
        {"$set": {"status": STATUS_ACTIVE, "activated_by": activated_by, "activated_at": now}},
    )
    return await get_template(db, template_id)


async def get_active_template(db: AsyncIOMotorDatabase, family_id: str) -> Optional[Dict[str, Any]]:
    return await db[TEMPLATES_COLLECTION].find_one({"family_id": family_id, "status": STATUS_ACTIVE}, {"_id": 0})


async def list_template_versions(db: AsyncIOMotorDatabase, family_id: str) -> List[Dict[str, Any]]:
    return await db[TEMPLATES_COLLECTION].find({"family_id": family_id}, {"_id": 0}).sort("version", 1).to_list(None)
