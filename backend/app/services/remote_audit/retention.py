"""Retention & legal-hold enforcement — docs/remote-audit/retention-policy.md,
Production-Grade Implementation Plan v1.3 correction #14: the policy in that
document is enforced here at runtime, not just documented.

`decide_retention_action` is pure decision logic (no DB I/O) over a
`RetainableItem` snapshot. `sweep` is the thin, DB-touching runner a worker
job (job_type="retention.sweep") calls on a schedule, using a caller-supplied
adapter so it can operate over whatever collection actually holds retainable
items once one exists (evidence in Phase 2, documents in Phase 4) — no
evidence/document collection exists yet, so this module is proven now against
synthetic fixtures, per the plan, ahead of anything real to retain.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from .audit_events import RemoteAuditEvent


class RetentionAction(str, Enum):
    RETAIN = "RETAIN"                                   # not yet expired
    HELD = "HELD"                                        # expired but a legal hold blocks any action
    REFERENCED = "REFERENCED"                             # expired, unheld, but still referenced by an open audit/CAPA
    BLOCKED_BY_OBJECT_LOCK = "BLOCKED_BY_OBJECT_LOCK"     # expired, unheld, unreferenced, but WORM prevents deletion
    ELIGIBLE_FOR_DELETION = "ELIGIBLE_FOR_DELETION"


@dataclass(frozen=True)
class RetainableItem:
    item_id: str
    retention_expires_at: datetime
    legal_hold: bool
    object_lock_active: bool
    referenced_by_open_audit_or_capa: bool
    policy_version: str


@dataclass(frozen=True)
class RetentionDecision:
    item_id: str
    action: RetentionAction
    reason: str
    policy_version: str


def decide_retention_action(item: RetainableItem, *, now: Optional[datetime] = None) -> RetentionDecision:
    """Never falsely reports a deletion that Object Lock/legal hold actually
    prevented -- the whole point of this function existing (v1.3 correction
    #14) is that the system's own status always matches reality."""
    now = now or datetime.now(timezone.utc)
    if now < item.retention_expires_at:
        return RetentionDecision(
            item.item_id, RetentionAction.RETAIN,
            "retention period has not expired yet", item.policy_version,
        )
    if item.legal_hold:
        return RetentionDecision(
            item.item_id, RetentionAction.HELD,
            "retention expired but a legal hold blocks any action", item.policy_version,
        )
    if item.referenced_by_open_audit_or_capa:
        return RetentionDecision(
            item.item_id, RetentionAction.REFERENCED,
            "retention expired but the item is still referenced by an unresolved audit/CAPA",
            item.policy_version,
        )
    if item.object_lock_active:
        return RetentionDecision(
            item.item_id, RetentionAction.BLOCKED_BY_OBJECT_LOCK,
            "retention expired and unheld, but Object Lock/WORM prevents deletion",
            item.policy_version,
        )
    return RetentionDecision(
        item.item_id, RetentionAction.ELIGIBLE_FOR_DELETION,
        "retention expired, no legal hold, no open reference, no Object Lock",
        item.policy_version,
    )


def build_retention_audit_event(decision: RetentionDecision, *, resource_type: str) -> RemoteAuditEvent:
    """Every retention action -- including a blocked one -- gets an audit
    event referencing the policy version in force (v1.3 correction #14)."""
    result = "blocked" if decision.action in (
        RetentionAction.HELD, RetentionAction.REFERENCED, RetentionAction.BLOCKED_BY_OBJECT_LOCK,
    ) else "success"
    return RemoteAuditEvent(
        action=f"remote_audit.retention.{decision.action.value.lower()}",
        resource_type=resource_type,
        resource_id=decision.item_id,
        result=result,  # type: ignore[arg-type]
        reason=decision.reason,
        policy_version=decision.policy_version,
    )


def build_policy_version_change_event(*, old_version: Optional[str], new_version: str) -> RemoteAuditEvent:
    """A retention policy-version change is itself versioned and audited
    (v1.3 correction #14) -- reuses the same audit-event schema rather than
    inventing separate policy-change tracking."""
    return RemoteAuditEvent(
        action="remote_audit.retention.policy_version_changed",
        resource_type="retention_policy",
        resource_id=new_version,
        result="success",
        reason=f"retention policy moved from {old_version or '(none)'} to {new_version}",
        policy_version=new_version,
        before_ref=old_version,
        after_ref=new_version,
    )


ItemAdapter = Callable[[Dict[str, Any]], RetainableItem]
ActionHandler = Callable[[RetentionDecision, Dict[str, Any]], Awaitable[None]]


async def sweep(
    db: AsyncIOMotorDatabase,
    collection_name: str,
    *,
    adapt: ItemAdapter,
    on_decision: ActionHandler,
    query: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> List[RetentionDecision]:
    """Generic retention sweep over any collection of retainable items.
    Deliberately collection-agnostic (via `adapt`/`on_decision`) since no
    concrete evidence/document collection exists yet -- Phase 2/4 register
    their own adapter and handler against this same function rather than
    this module needing to know their shape."""
    now = now or datetime.now(timezone.utc)
    decisions: List[RetentionDecision] = []
    cursor = db[collection_name].find(query or {})
    async for doc in cursor:
        item = adapt(doc)
        decision = decide_retention_action(item, now=now)
        await on_decision(decision, doc)
        decisions.append(decision)
    return decisions
