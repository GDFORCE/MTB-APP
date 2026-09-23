"""Remote-audit RBAC/ABAC capability model — pure logic, no DB/network I/O.

Implements the capability table in docs/remote-audit/threat-model.md, corrected
per Production-Grade Implementation Plan v1.3:

- correction #3: organization membership alone buys a sponsor/cro user only a
  MINIMAL audit list (id/status/site) — audit detail, evidence, documents, and
  every action require an explicit participant grant on that specific audit.
  Geofence approval is a per-site grant, never inferred from org ownership of
  the site (the site belongs to the hospital's org, not the sponsor's).
- correction #8: the platform `admin` role never implicitly receives
  reviewer/approver/geofence_approver/document_reviewer/template_admin — those
  must appear in a caller-supplied `explicit_grants` set, exactly like any
  other user.

Callers (route handlers in remote_audit_routes.py, from Phase 1 onward) fetch
the relevant `remote_audit_participants` / `remote_audit_site_approvers` rows
from Mongo and pass them in here as plain data — this module never queries a
database, which is what makes it directly unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet, Optional


class RemoteAuditCapability(str, Enum):
    EVIDENCE_CAPTURER = "remote_audit.evidence_capturer"
    DOCUMENT_REVIEWER = "remote_audit.document_reviewer"
    REVIEWER = "remote_audit.reviewer"
    GEOFENCE_APPROVER = "remote_audit.geofence_approver"
    APPROVER = "remote_audit.approver"
    TEMPLATE_ADMIN = "remote_audit.template_admin"
    PLATFORM_ADMIN = "remote_audit.platform_admin"


# Roles eligible for the reviewer/approver family, subject to explicit
# per-audit/per-site grants — being in this set is necessary, never sufficient.
REVIEWER_FAMILY_ROLES: FrozenSet[str] = frozenset({"sponsor", "cro"})
# Roles eligible for the site-participant family (capturer/site_admin/observer).
SITE_FAMILY_ROLES: FrozenSet[str] = frozenset({"site", "pi", "crc"})

AuditRoleInAudit = str  # "reviewer" | "approver" | "site_admin" | "capturer" | "observer"


def org_level_capabilities(role: str) -> FrozenSet[RemoteAuditCapability]:
    """Capabilities derivable from role + org membership ALONE, before any
    per-audit/per-site grant is consulted.

    Deliberately minimal (v1.3 correction #3): a sponsor/cro user's org
    membership buys only the ability to list their own org's audits — nothing
    about audit detail, evidence, documents, or any action.
    """
    if role in REVIEWER_FAMILY_ROLES:
        return frozenset({RemoteAuditCapability.REVIEWER})
    if role == "admin":
        return frozenset({RemoteAuditCapability.PLATFORM_ADMIN})
    return frozenset()


def capabilities_for(
    role: str,
    *,
    explicit_grants: FrozenSet[RemoteAuditCapability] = frozenset(),
) -> FrozenSet[RemoteAuditCapability]:
    """Full capability set for a user, given their role and any explicitly
    assigned capabilities.

    `explicit_grants` models capabilities recorded independently of role —
    e.g. `remote_audit.template_admin`, granted to a specific user and itself
    an audited action (see docs/remote-audit/threat-model.md, Phase 0 item 9).
    An `admin` user gets `PLATFORM_ADMIN` from `org_level_capabilities` and
    nothing else unless it also appears in `explicit_grants` — there is no
    code path in this function that adds TEMPLATE_ADMIN/REVIEWER/APPROVER/
    GEOFENCE_APPROVER/DOCUMENT_REVIEWER for the `admin` role implicitly.
    """
    return org_level_capabilities(role) | frozenset(explicit_grants)


@dataclass(frozen=True)
class ParticipantGrant:
    """One `remote_audit_participants` row for a specific audit, already
    fetched by the caller."""
    user_id: str
    role_in_audit: AuditRoleInAudit


@dataclass(frozen=True)
class AuditAccessDecision:
    can_list: bool
    can_view_details: bool
    can_act: bool
    role_in_audit: Optional[AuditRoleInAudit]
    reason: str


def resolve_audit_access(
    *,
    user_id: str,
    user_org_id: str,
    audit_owner_org_id: str,
    site_org_id: str,
    participants: FrozenSet[ParticipantGrant],
) -> AuditAccessDecision:
    """Pure resource-level ABAC decision for one (user, audit) pair.

    Cross-organization model (v1.3 correction #7): the sponsor/CRO running the
    audit and the hospital/site being audited are different organizations —
    `audit_owner_org_id` and `site_org_id` are never assumed equal, and
    authorization is based on the user's relationship to the audit (org
    membership for listing, an explicit participant grant for everything
    else), not on `user_org_id == some_single_org_id`.
    """
    my_roles = frozenset(
        p.role_in_audit for p in participants if p.user_id == user_id
    )
    is_owner_org_member = user_org_id == audit_owner_org_id
    is_site_org_member = user_org_id == site_org_id

    if my_roles:
        return AuditAccessDecision(
            can_list=True,
            can_view_details=True,
            can_act=True,
            role_in_audit=next(iter(sorted(my_roles))),
            reason=f"explicit participant grant: {', '.join(sorted(my_roles))}",
        )

    if is_owner_org_member:
        return AuditAccessDecision(
            can_list=True,
            can_view_details=False,
            can_act=False,
            role_in_audit=None,
            reason="owning-org member without a participant grant on this audit: minimal list only",
        )

    if is_site_org_member:
        return AuditAccessDecision(
            can_list=False,
            can_view_details=False,
            can_act=False,
            role_in_audit=None,
            reason="site-org member without a participant grant on this audit",
        )

    return AuditAccessDecision(
        can_list=False,
        can_view_details=False,
        can_act=False,
        role_in_audit=None,
        reason="no relationship to this audit's owning or site organization",
    )


@dataclass(frozen=True)
class SiteApproverGrant:
    """One `remote_audit_site_approvers` row, already fetched by the caller."""
    user_id: str
    site_id: str


def can_approve_geofence(
    *, user_id: str, site_id: str, grants: FrozenSet[SiteApproverGrant]
) -> bool:
    """True only for an explicit user+site grant — never inferred from org
    ownership of the site (v1.3 correction #3: a site belongs to the
    hospital's org, not the sponsor's, so that inference was never valid).
    """
    return any(g.user_id == user_id and g.site_id == site_id for g in grants)
