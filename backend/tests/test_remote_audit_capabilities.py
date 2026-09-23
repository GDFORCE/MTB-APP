"""Phase 0 exit-criterion tests for the remote-audit RBAC/ABAC capability model.

Covers Production-Grade Implementation Plan v1.3 corrections #3, #7, #8:
- platform admin never implicitly gets audit-policy/reviewer/approver capability,
- org membership alone yields only a minimal audit list, never detail/action access,
- cross-organization access is resolved from participant grants, not org-id equality,
- geofence approval is a per-site grant, never inferred from org ownership of the site.
"""
from __future__ import annotations

from app.services.remote_audit.capabilities import (
    AuditAccessDecision,
    ParticipantGrant,
    RemoteAuditCapability,
    SiteApproverGrant,
    can_approve_geofence,
    capabilities_for,
    org_level_capabilities,
    resolve_audit_access,
)


def test_sponsor_org_membership_grants_only_reviewer_list_capability():
    caps = capabilities_for("sponsor")
    assert caps == frozenset({RemoteAuditCapability.REVIEWER})


def test_cro_role_behaves_like_sponsor():
    assert org_level_capabilities("cro") == frozenset({RemoteAuditCapability.REVIEWER})


def test_site_pi_crc_get_no_org_level_capability():
    for role in ("site", "pi", "crc"):
        assert org_level_capabilities(role) == frozenset()


def test_patient_role_gets_nothing():
    assert capabilities_for("patient") == frozenset()


def test_platform_admin_never_implicitly_gets_reviewer_or_template_admin():
    """v1.3 correction #8: admin gets PLATFORM_ADMIN only, never any other
    remote-audit capability, unless it is explicitly granted."""
    caps = capabilities_for("admin")
    assert caps == frozenset({RemoteAuditCapability.PLATFORM_ADMIN})
    assert RemoteAuditCapability.TEMPLATE_ADMIN not in caps
    assert RemoteAuditCapability.REVIEWER not in caps
    assert RemoteAuditCapability.APPROVER not in caps
    assert RemoteAuditCapability.GEOFENCE_APPROVER not in caps
    assert RemoteAuditCapability.DOCUMENT_REVIEWER not in caps


def test_platform_admin_can_be_explicitly_granted_template_admin():
    """Explicit assignment is the ONLY path to this capability for an admin —
    proving the grant path exists and is independent of the role string."""
    caps = capabilities_for(
        "admin", explicit_grants=frozenset({RemoteAuditCapability.TEMPLATE_ADMIN})
    )
    assert RemoteAuditCapability.TEMPLATE_ADMIN in caps
    assert RemoteAuditCapability.PLATFORM_ADMIN in caps
    assert RemoteAuditCapability.REVIEWER not in caps


# ── resolve_audit_access ─────────────────────────────────────────────────────

OWNER_ORG = "org-sponsor-A"
OTHER_OWNER_ORG = "org-sponsor-B"
SITE_ORG = "org-hospital-X"
OTHER_SITE_ORG = "org-hospital-Y"


def test_owner_org_member_without_participant_grant_gets_minimal_list_only():
    decision = resolve_audit_access(
        user_id="u1", user_org_id=OWNER_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG,
        participants=frozenset(),
    )
    assert decision.can_list is True
    assert decision.can_view_details is False
    assert decision.can_act is False


def test_assigned_reviewer_gets_full_access_to_that_audit():
    decision = resolve_audit_access(
        user_id="u1", user_org_id=OWNER_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG,
        participants=frozenset({ParticipantGrant(user_id="u1", role_in_audit="reviewer")}),
    )
    assert decision.can_list and decision.can_view_details and decision.can_act
    assert decision.role_in_audit == "reviewer"


def test_reviewer_not_assigned_to_this_specific_audit_cannot_act_on_it():
    """v1.3 correction #7's key test case: same owning org, but this user was
    never added as a participant on THIS audit."""
    decision = resolve_audit_access(
        user_id="unassigned-reviewer", user_org_id=OWNER_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG,
        participants=frozenset({ParticipantGrant(user_id="someone-else", role_in_audit="reviewer")}),
    )
    assert decision.can_list is True  # still a member of the owning org
    assert decision.can_view_details is False
    assert decision.can_act is False


def test_sponsor_a_cannot_see_sponsor_b_audit():
    decision = resolve_audit_access(
        user_id="u1", user_org_id=OTHER_OWNER_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG,
        participants=frozenset(),
    )
    assert decision.can_list is False
    assert decision.can_view_details is False
    assert decision.can_act is False


def test_site_a_cannot_see_site_b_audit():
    decision = resolve_audit_access(
        user_id="u1", user_org_id=OTHER_SITE_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG,
        participants=frozenset(),
    )
    assert decision.can_list is False
    assert decision.can_view_details is False
    assert decision.can_act is False


def test_site_org_member_without_grant_cannot_even_list():
    """Unlike the owning org's minimal-list courtesy, site-org membership
    alone grants nothing — the hospital's staff must be explicitly added as
    participants on an audit before they see it at all."""
    decision = resolve_audit_access(
        user_id="u1", user_org_id=SITE_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG,
        participants=frozenset(),
    )
    assert decision.can_list is False


def test_site_participant_capturer_gets_full_access_to_assigned_audit():
    decision = resolve_audit_access(
        user_id="site-user-1", user_org_id=SITE_ORG,
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG,
        participants=frozenset({ParticipantGrant(user_id="site-user-1", role_in_audit="capturer")}),
    )
    assert decision.can_list and decision.can_view_details and decision.can_act
    assert decision.role_in_audit == "capturer"


def test_unrelated_user_gets_nothing():
    decision = resolve_audit_access(
        user_id="stranger", user_org_id="org-unrelated",
        audit_owner_org_id=OWNER_ORG, site_org_id=SITE_ORG,
        participants=frozenset(),
    )
    assert decision == AuditAccessDecision(
        can_list=False, can_view_details=False, can_act=False,
        role_in_audit=None,
        reason="no relationship to this audit's owning or site organization",
    )


# ── geofence approval ────────────────────────────────────────────────────────

def test_geofence_approval_requires_explicit_site_grant_not_org_ownership():
    """v1.3 correction #3: approving a geofence is never inferred from
    'the sponsor org owns this site' — it never does, since sites belong to
    the hospital's org."""
    grants = frozenset({SiteApproverGrant(user_id="approver-1", site_id="site-1")})
    assert can_approve_geofence(user_id="approver-1", site_id="site-1", grants=grants) is True
    assert can_approve_geofence(user_id="approver-1", site_id="site-2", grants=grants) is False
    assert can_approve_geofence(user_id="someone-else", site_id="site-1", grants=grants) is False


def test_geofence_approval_with_no_grants_is_always_denied():
    assert can_approve_geofence(user_id="anyone", site_id="site-1", grants=frozenset()) is False
