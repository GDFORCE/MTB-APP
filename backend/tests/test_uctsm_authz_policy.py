"""P3-01 / P3-02: the pure policy and the proposed capability catalog.

These tests assert the deny-by-default shape of the matrix; the matrix itself is
a PROPOSAL pending owner approval (``MATRIX_STATUS``) and the tests say so.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.authorization.catalog import (                          # noqa: E402
    CATALOG, CLINICAL_DECISION_CAPABILITIES, MATRIX_STATUS, POLICY_VERSION, Capability,
)
from app.authorization.policy import (                           # noqa: E402
    Grant, Principal, Relationship, ResourceContext, ResourceKind, authorize, may_grant,
)

ORG, OTHER = "org-1", "org-2"
TRIAL, OTHER_TRIAL = "trial-1", "trial-2"


def principal(role="sponsor", *, org=ORG, org_admin=False, active=True, grants=()):
    return Principal(user_id="u-1", organization_id=org, role=role, org_admin=org_admin,
                     active=active, grants=tuple(grants))


def trial_ctx(rel=Relationship.ORGANIZATION, *, org=ORG, trial=TRIAL, found=True):
    return ResourceContext(kind=ResourceKind.TRIAL, found=found, organization_id=org,
                           trial_id=trial, relationship=rel, resource_id=trial)


def assigned_ctx():
    return ResourceContext(kind=ResourceKind.PATIENT, organization_id=ORG, trial_id=TRIAL,
                           relationship=Relationship.ASSIGNED, resource_id="p-1")


def test_matrix_is_declared_as_selected_under_delegation_not_externally_signed_off():
    assert MATRIX_STATUS == "SELECTED_UNDER_OWNER_DELEGATION"       # not external sign-off
    assert POLICY_VERSION.startswith("uctsm-authz.")


def test_every_capability_is_in_the_catalog_and_unknown_names_deny():
    assert set(CATALOG) == set(Capability)
    decision = authorize(principal(), "schedule.teleport")
    assert not decision.allowed and decision.reason_code == "UNMAPPED_CAPABILITY"


@pytest.mark.parametrize("role", ["sponsor", "cro", "smo", "site", "pi", "crc", "patient", "admin", "unknown", ""])
@pytest.mark.parametrize("capability", sorted(CLINICAL_DECISION_CAPABILITIES, key=str))
def test_no_role_holds_a_clinical_decision_capability_by_default(role, capability):
    decision = authorize(principal(role, org_admin=True), capability, trial_ctx())
    if capability is Capability.PATIENT_IMPACT_CONFIRM and role == "pi":
        assert decision.allowed       # the only proposed role default among them
    else:
        assert not decision.allowed
        # a patient has no relationship to a trial-level record at all (404)
        from app.authorization.policy import SUPPORTED_ROLES as supported
        assert decision.reason_code == (
            "UNSUPPORTED_ROLE" if role not in supported
            else "NO_RELATIONSHIP" if role == "patient" else "CAPABILITY_MISSING")


def test_amendment_adoption_is_catalogued_but_held_by_nobody():
    for role in ("sponsor", "cro", "pi", "crc", "smo", "site", "patient", "admin"):
        assert not authorize(principal(role, org_admin=True), Capability.AMENDMENT_ADOPT,
                             trial_ctx()).allowed


def test_platform_admin_role_has_no_clinical_bypass():
    for capability in Capability:
        decision = authorize(principal("admin"), capability, trial_ctx())
        assert not decision.allowed, capability


def test_org_admin_does_not_gain_clinical_rights_only_the_documented_compat_ones():
    admin = principal("sponsor", org_admin=True)
    held = {c for c in Capability if authorize(admin, c, trial_ctx()).allowed}
    assert Capability.SCHEDULE_APPROVE not in held
    assert Capability.SCHEDULE_CORRECT not in held
    assert Capability.PATIENT_OCCURRENCE_RECORD not in held
    # selected policy (project decisions, 15 Oct 2026): identity review is explicit-grant only
    assert Capability.PROTOCOL_IDENTITY_REVIEW not in held
    assert Capability.GRANTS_MANAGE in held                    # administration, not clinical


def test_an_explicit_grant_gives_exactly_that_capability_for_its_scope():
    org_wide = principal("sponsor", grants=[Grant("schedule.approve")])
    assert authorize(org_wide, Capability.SCHEDULE_APPROVE, trial_ctx()).allowed
    assert authorize(org_wide, Capability.SCHEDULE_APPROVE, trial_ctx(trial=OTHER_TRIAL)).allowed
    assert not authorize(org_wide, Capability.SCHEDULE_CORRECT, trial_ctx()).allowed

    scoped = principal("sponsor", grants=[Grant("schedule.approve", TRIAL)])
    assert authorize(scoped, Capability.SCHEDULE_APPROVE, trial_ctx()).allowed
    other = authorize(scoped, Capability.SCHEDULE_APPROVE, trial_ctx(trial=OTHER_TRIAL))
    assert not other.allowed and other.reason_code == "CAPABILITY_MISSING"


def test_legacy_alias_maps_to_the_catalogued_capability():
    legacy = principal("pi", grants=[Grant("protocol_identity.review")])
    assert authorize(legacy, Capability.PROTOCOL_IDENTITY_REVIEW).allowed


# ---------------------------------------------------- resource / relationship

@pytest.mark.parametrize("relationship,expected_status,expected_reason", [
    (Relationship.NONE, 404, "NO_RELATIONSHIP"),
    (Relationship.UNMAPPED, 404, "NO_RELATIONSHIP"),
])
def test_without_a_relationship_the_answer_is_404_even_if_the_capability_is_held(
        relationship, expected_status, expected_reason):
    holder = principal("sponsor", grants=[Grant("schedule.approve")])
    decision = authorize(holder, Capability.SCHEDULE_APPROVE, trial_ctx(relationship))
    assert (decision.status, decision.reason_code) == (expected_status, expected_reason)


def test_relationship_without_capability_is_403_and_never_404():
    decision = authorize(principal("pi"), Capability.SCHEDULE_APPROVE,
                         trial_ctx(Relationship.ASSIGNED))
    assert (decision.status, decision.reason_code) == (403, "CAPABILITY_MISSING")


def test_missing_resource_is_404():
    decision = authorize(principal(), Capability.SCHEDULE_READ, trial_ctx(found=False))
    assert (decision.status, decision.reason_code) == (404, "RESOURCE_NOT_FOUND")


def test_foreign_organization_needs_an_explicit_assignment():
    assert not authorize(principal("pi", org=OTHER), Capability.SCHEDULE_READ,
                         trial_ctx(Relationship.ORGANIZATION)).allowed
    assigned = authorize(principal("pi", org=OTHER), Capability.SCHEDULE_READ,
                         trial_ctx(Relationship.ASSIGNED))
    assert assigned.allowed
    # acting inside the OWNER's tenant, not the caller's
    assert assigned.organization_id == ORG


def test_assignment_to_one_trial_says_nothing_about_another():
    decision = authorize(principal("pi", org=OTHER), Capability.SCHEDULE_READ,
                         trial_ctx(Relationship.NONE, trial=OTHER_TRIAL))
    assert decision.status == 404


def test_inactive_accounts_are_denied_whatever_they_hold():
    holder = principal("sponsor", active=False, grants=[Grant("schedule.approve")])
    assert authorize(holder, Capability.SCHEDULE_READ, trial_ctx()).reason_code == "ACCOUNT_INACTIVE"


def test_patient_role_acts_only_as_itself_and_only_to_read():
    me = ResourceContext(kind=ResourceKind.PATIENT, organization_id=ORG, trial_id=TRIAL,
                         relationship=Relationship.SELF, resource_id="p-1")
    assert authorize(principal("patient"), Capability.PATIENT_SCHEDULE_READ, me).allowed
    assert authorize(principal("patient"), Capability.PATIENT_OCCURRENCE_RECORD, me).status == 403
    # even a granted capability cannot make a patient a clinician
    granted = principal("patient", grants=[Grant("patient.occurrence.record")])
    assert not authorize(granted, Capability.PATIENT_OCCURRENCE_RECORD, me).allowed
    other = ResourceContext(kind=ResourceKind.PATIENT, organization_id=ORG, trial_id=TRIAL,
                            relationship=Relationship.ORGANIZATION, resource_id="p-2")
    assert authorize(principal("patient"), Capability.PATIENT_SCHEDULE_READ, other).status == 404


def test_sponsor_side_default_does_not_include_clinical_patient_actions():
    patient = ResourceContext(kind=ResourceKind.PATIENT, organization_id=ORG, trial_id=TRIAL,
                              relationship=Relationship.ORGANIZATION, resource_id="p-1")
    for role in ("sponsor", "cro"):
        assert authorize(principal(role), Capability.PATIENT_SCHEDULE_READ, patient).allowed
        for capability in (Capability.PATIENT_OCCURRENCE_RECORD, Capability.PATIENT_ANCHOR_RECORD,
                           Capability.PATIENT_STATE_RECORD, Capability.PATIENT_UNSCHEDULED_CREATE,
                           Capability.PATIENT_SCHEDULE_EVALUATE):
            assert not authorize(principal(role), capability, patient).allowed


# --------------------------------------------------------- grant administration

def test_grants_cannot_cross_organizations_or_be_self_issued_for_clinical_rights():
    admin = principal("sponsor", org_admin=True)
    assert may_grant(admin, Capability.SCHEDULE_APPROVE, "u-9", ORG) == (True, "OK")
    assert may_grant(admin, Capability.SCHEDULE_APPROVE, "u-9", OTHER)[0] is False
    ok, reason = may_grant(admin, Capability.SCHEDULE_APPROVE, admin.user_id, ORG)
    assert not ok and reason == "SELF_GRANT_OF_CLINICAL_CAPABILITY"
    assert may_grant(admin, Capability.DASHBOARD_READ, admin.user_id, ORG)[0] is True


# ---- R4: unknown roles fail closed even with a valid explicit grant ----------------
from app.authorization.catalog import Capability as _Cap          # noqa: E402
from app.authorization.policy import Grant as _Grant, SUPPORTED_ROLES  # noqa: E402


@pytest.mark.parametrize("role", ["UNKNOWN_ROLE", "", "admin", "Sponsor ", "SPONSOR"])
def test_unknown_or_malformed_roles_are_denied_despite_a_valid_grant(role):
    held = principal(role=role, grants=(_Grant(_Cap.SCHEDULE_APPROVE.value),))
    decision = authorize(held, _Cap.SCHEDULE_APPROVE, ResourceContext())
    assert decision.allowed is False and decision.reason_code == "UNSUPPORTED_ROLE"


def test_a_supported_role_with_the_same_explicit_grant_is_still_allowed():
    for role in sorted(SUPPORTED_ROLES - {"patient"}):
        held = principal(role=role, grants=(_Grant(_Cap.SCHEDULE_APPROVE.value),))
        assert authorize(held, _Cap.SCHEDULE_APPROVE, ResourceContext()).allowed, role


# ---- selected target matrix: stricter self-grant and no sponsor-side clinical writes ----
from app.authorization.catalog import SELF_GRANT_PROTECTED, PATIENT_CLINICAL_WRITE_CAPABILITIES  # noqa: E402
from app.authorization.policy import may_grant  # noqa: E402


@pytest.mark.parametrize("capability", sorted(SELF_GRANT_PROTECTED, key=str))
def test_nobody_grants_themselves_any_clinical_write_decision_or_identity_review(capability):
    grantor = principal("pi", org_admin=True)
    ok, reason = may_grant(grantor, capability, grantor.user_id, grantor.organization_id)
    assert ok is False and reason == "SELF_GRANT_OF_CLINICAL_CAPABILITY"
    ok, _ = may_grant(grantor, capability, "someone-else", grantor.organization_id)
    assert ok is True


@pytest.mark.parametrize("role", ["sponsor", "cro"])
@pytest.mark.parametrize("capability", sorted(PATIENT_CLINICAL_WRITE_CAPABILITIES, key=str))
def test_an_explicit_grant_never_gives_sponsor_side_accounts_patient_clinical_writes(role, capability):
    held = principal(role, grants=[Grant(capability.value)])
    assert authorize(held, capability, trial_ctx()).allowed is False


def test_extraction_default_holders_are_sponsor_cro_and_pi():
    from app.authorization.catalog import CATALOG
    assert CATALOG[Capability.SCHEDULE_EXTRACT].default_roles == frozenset({"sponsor", "cro", "pi"})


# ---- recipient eligibility: grants cannot make an ineligible role eligible --------------
from app.authorization.catalog import POLICY_PROVENANCE, RECIPIENT_ROLES  # noqa: E402


@pytest.mark.parametrize("role,capability", [
    ("crc", Capability.PATIENT_COMMAND_HIGH_IMPACT),
    ("site", Capability.PATIENT_OCCURRENCE_RECORD),
    ("smo", Capability.PATIENT_STATE_RECORD),
    ("crc", Capability.PATIENT_IMPACT_CONFIRM),
    ("site", Capability.PATIENT_UNSCHEDULED_CREATE),
])
def test_a_valid_explicit_grant_does_not_make_an_ineligible_role_eligible(role, capability):
    held = principal(role, grants=[Grant(capability.value, TRIAL)])
    decision = authorize(held, capability, assigned_ctx())
    assert decision.allowed is False and decision.reason_code == "CAPABILITY_MISSING"


def test_assigned_pi_with_the_high_impact_grant_is_allowed_unassigned_or_revoked_is_not():
    granted = principal("pi", grants=[Grant("patient.command.high_impact", TRIAL)])
    assert authorize(granted, Capability.PATIENT_COMMAND_HIGH_IMPACT, assigned_ctx()).allowed
    unassigned = ResourceContext(kind=ResourceKind.PATIENT, organization_id=ORG, trial_id=TRIAL,
                                 relationship=Relationship.NONE, resource_id="p")
    assert authorize(granted, Capability.PATIENT_COMMAND_HIGH_IMPACT, unassigned).allowed is False
    assert authorize(principal("pi"), Capability.PATIENT_COMMAND_HIGH_IMPACT, assigned_ctx()).allowed is False


def test_site_and_smo_keep_administrative_enrolment():
    for role in ("site", "smo"):
        assert authorize(principal(role), Capability.PATIENT_ENROL, assigned_ctx()).allowed, role


def test_grant_issuance_refuses_ineligible_recipient_roles():
    grantor = principal("sponsor", org_admin=True)
    assert may_grant(grantor, Capability.PATIENT_OCCURRENCE_RECORD, "u", grantor.organization_id,
                     "site") == (False, "GRANTEE_ROLE_NOT_ELIGIBLE")
    assert may_grant(grantor, Capability.PATIENT_OCCURRENCE_RECORD, "u", grantor.organization_id,
                     "crc")[0] is True
    assert may_grant(grantor, Capability.PATIENT_COMMAND_HIGH_IMPACT, "u", grantor.organization_id,
                     "crc") == (False, "GRANTEE_ROLE_NOT_ELIGIBLE")


def test_policy_provenance_names_the_delegation_and_denies_external_signoff():
    assert POLICY_PROVENANCE["external_clinical_signoff"] is False
    assert POLICY_PROVENANCE["selected_on"] == "2026-10-15"
    assert Capability.PATIENT_COMMAND_HIGH_IMPACT in RECIPIENT_ROLES
