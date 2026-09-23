"""Standardized remote-audit audit-event payload — pure logic, no DB I/O.

`backend/server.py`'s existing `write_audit(user, action, detail, status='success', **ctx)`
already stores arbitrary keyword context verbatim, so extending the audit-trail
schema needs no change to `write_audit()` itself — this module only
standardizes what remote-audit callers pass to it, per the Phase 0 audit-event
schema in docs/remote-audit/ (Production-Grade Implementation Plan v1.3).

This module never calls the database. From Phase 1 onward, the caller wraps
the state-change write and the `write_audit()` call in one Mongo transaction
(or a transactional-outbox pattern where transactions aren't available) per
v1.3 correction #2 — that atomicity guarantee lives in the caller, not here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

Result = Literal["success", "failure", "blocked"]


@dataclass(frozen=True)
class RemoteAuditEvent:
    action: str  # namespaced, e.g. "remote_audit.evidence.finalized"
    resource_type: str
    resource_id: str
    result: Result
    organization_id: Optional[str] = None
    site_id: Optional[str] = None
    reason: Optional[str] = None
    reason_code: Optional[str] = None
    policy_version: Optional[str] = None
    rule_version: Optional[str] = None
    before_ref: Optional[str] = None
    after_ref: Optional[str] = None
    before_hash: Optional[str] = None
    after_hash: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def status(self) -> Result:
        """Alias matching write_audit's `status` parameter name."""
        return self.result

    def as_write_audit_kwargs(self) -> Dict[str, Any]:
        """Flatten to the `**ctx` kwargs `write_audit(user, action, detail,
        status=..., **ctx)` expects, using the same field names already used
        by existing callers (`target_id`, `org_id`) where they overlap, and
        adding the new fields this module contributes."""
        ctx: Dict[str, Any] = {
            "resource_type": self.resource_type,
            "target_id": self.resource_id,
            "correlation_id": self.correlation_id,
        }
        optional_fields = {
            "org_id": self.organization_id,
            "site_id": self.site_id,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "rule_version": self.rule_version,
            "before_ref": self.before_ref,
            "after_ref": self.after_ref,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
        }
        ctx.update({k: v for k, v in optional_fields.items() if v is not None})
        return ctx
