"""Persistence for section scores and the final audit decision — spec §13.

The scoring arithmetic lives in scoring.py (pure, no I/O). This module stores
what that engine produced, and enforces the two things that are about the
record rather than the maths:

1. **A decision snapshots the policy and algorithm version that produced it.**
   Changing a template's weights next quarter must not retroactively change
   what a signed-off audit meant. The snapshot is the whole defence against
   that, so it is stored inline rather than referenced by id.

2. **A decision is written once.** There is no update path — a superseding
   decision is a new record pointing at the one it supersedes, exactly like
   evidence verifications and document checks. "The approval was amended" and
   "the approval always said this" are different claims, and only one of them
   is true.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

SECTION_SCORES_COLLECTION = "remote_audit_section_scores"
DECISIONS_COLLECTION = "remote_audit_decisions"

DECISION_ACTIONS = ("APPROVED", "APPROVED_WITH_CONDITIONS", "NOT_APPROVED")


class DecisionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[SECTION_SCORES_COLLECTION].create_index("id", unique=True)
    await db[SECTION_SCORES_COLLECTION].create_index([("audit_id", 1), ("score_run_id", 1)])
    await db[DECISIONS_COLLECTION].create_index("id", unique=True)
    await db[DECISIONS_COLLECTION].create_index("audit_id")
    await db[DECISIONS_COLLECTION].create_index(
        [("audit_id", 1), ("decision_run_id", 1)], unique=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def record_score_run(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    scored_by: str,
    result: Dict[str, Any],
    policy_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Append a scoring run. Append-only and versioned: re-scoring after a
    rule change adds a run and leaves the previous one readable."""
    previous = await latest_score_run(db, audit_id)
    run_id = (previous["score_run_id"] + 1) if previous else 1
    doc = {
        "id": str(uuid.uuid4()),
        "audit_id": audit_id,
        "score_run_id": run_id,
        "supersedes_score_id": previous["id"] if previous else None,
        "scored_by": scored_by,
        "compliance_percent": result.get("compliance_percent"),
        "evidence_confidence_percent": result.get("evidence_confidence_percent"),
        "proposed_decision": result.get("proposed_decision"),
        "blockers": result.get("blockers", []),
        "sections": result.get("sections", []),
        "notes": result.get("notes", []),
        "algorithm_version": result.get("algorithm_version"),
        # Stored inline, not referenced: a template edited next quarter must
        # not retroactively change what this run meant.
        "policy_snapshot": policy_snapshot,
        "created_at": _now(),
    }
    await db[SECTION_SCORES_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def latest_score_run(db: AsyncIOMotorDatabase, audit_id: str) -> Optional[Dict[str, Any]]:
    return await db[SECTION_SCORES_COLLECTION].find_one(
        {"audit_id": audit_id}, {"_id": 0}, sort=[("score_run_id", -1)])


async def list_score_runs(db: AsyncIOMotorDatabase, audit_id: str) -> List[Dict[str, Any]]:
    return await db[SECTION_SCORES_COLLECTION].find(
        {"audit_id": audit_id}, {"_id": 0}).sort("score_run_id", 1).to_list(None)


async def latest_decision(db: AsyncIOMotorDatabase, audit_id: str) -> Optional[Dict[str, Any]]:
    return await db[DECISIONS_COLLECTION].find_one(
        {"audit_id": audit_id}, {"_id": 0}, sort=[("decision_run_id", -1)])


async def list_decisions(db: AsyncIOMotorDatabase, audit_id: str) -> List[Dict[str, Any]]:
    return await db[DECISIONS_COLLECTION].find(
        {"audit_id": audit_id}, {"_id": 0}).sort("decision_run_id", 1).to_list(None)


async def record_decision(
    db: AsyncIOMotorDatabase,
    *,
    audit_id: str,
    decided_by: str,
    decision: str,
    machine_result: Dict[str, Any],
    override_reason: Optional[str] = None,
    linked_capa_ids: Optional[List[str]] = None,
    signature_statement: str = "",
) -> Dict[str, Any]:
    """Write a final decision.

    A decision that disagrees with the machine must carry a reason — and both
    the human verdict and the machine's are stored, because "the system said
    not approved and a person approved it anyway, for this reason" is the
    single most audit-relevant thing this module records.
    """
    if decision not in DECISION_ACTIONS:
        raise DecisionError("UNKNOWN_DECISION", f"{decision} is not a decision")

    proposed = machine_result.get("proposed_decision")
    overridden = proposed is not None and decision != proposed
    if overridden and not (override_reason or "").strip():
        raise DecisionError(
            "OVERRIDE_REASON_REQUIRED",
            f"the system proposed {proposed}; recording {decision} instead requires a stated reason")

    previous = await latest_decision(db, audit_id)
    run_id = (previous["decision_run_id"] + 1) if previous else 1
    doc = {
        "id": str(uuid.uuid4()),
        "audit_id": audit_id,
        "decision_run_id": run_id,
        "supersedes_decision_id": previous["id"] if previous else None,
        "decision": decision,
        "decided_by": decided_by,
        "decided_at": _now(),
        # Never replaced by the human verdict — kept beside it.
        "machine_result": machine_result,
        "machine_proposed": proposed,
        "overridden": overridden,
        "override_reason": (override_reason or "").strip() or None,
        "linked_capa_ids": list(linked_capa_ids or []),
        "signature_statement": signature_statement,
        "algorithm_version": machine_result.get("algorithm_version"),
    }
    await db[DECISIONS_COLLECTION].insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}
