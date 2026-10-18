"""The pure authorization decision (P3-02).

``authorize(principal, capability, resource) -> Decision``

No FastAPI, no database. The caller (``guard``) resolves a minimal typed
``ResourceContext`` from persisted records; this module only decides.

Order of evaluation (first failing step wins) and the HTTP meaning of each:

1. inactive account                      -> 403 ACCOUNT_INACTIVE
2. capability not in the catalog          -> 403 UNMAPPED_CAPABILITY   (deny by default)
3. resource named but not found           -> 404 RESOURCE_NOT_FOUND
4. no persisted relationship to it        -> 404 NO_RELATIONSHIP       (non-disclosing)
   (foreign organization with no assignment, unmapped operational link, or
   another patient's record)
5. relationship exists, capability absent -> 403 CAPABILITY_MISSING
6. relationship + capability              -> allowed

Steps 4 and 5 are ordered so a caller can learn that a resource exists only if
they are already permitted to see it. A missing capability on a resource the
caller can see is a 403 because it discloses nothing new.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .catalog import (
    CATALOG, CLINICAL_DECISION_CAPABILITIES, LEGACY_ALIASES, PATIENT_CLINICAL_WRITE_CAPABILITIES,
    POLICY_VERSION, RECIPIENT_ROLES, SELF_GRANT_PROTECTED, Capability,
)


class Relationship(StrEnum):
    NONE = "NONE"                    # no persisted tie
    UNMAPPED = "UNMAPPED"            # canonical record has no operational link to check
    ORGANIZATION = "ORGANIZATION"    # the caller's organization owns it (sponsor/CRO side)
    ASSIGNED = "ASSIGNED"            # an explicit operational assignment/grant/invitation
    SELF = "SELF"                    # the caller is the patient


class ResourceKind(StrEnum):
    NONE = "NONE"                    # org-level action, no specific resource
    TRIAL = "TRIAL"
    PROTOCOL = "PROTOCOL"
    SCHEDULE_VERSION = "SCHEDULE_VERSION"
    PATIENT = "PATIENT"
    EXTRACTION_RUN = "EXTRACTION_RUN"
    IMPACT_PROPOSAL = "IMPACT_PROPOSAL"
    PATIENT_EVENT = "PATIENT_EVENT"
    PARITY_RUN = "PARITY_RUN"
    IDENTITY = "IDENTITY"


@dataclass(frozen=True)
class Grant:
    capability: str
    #: canonical trial id the grant is limited to; None = organization-wide.
    trial_id: str | None = None


@dataclass(frozen=True)
class Principal:
    user_id: str
    organization_id: str
    role: str
    org_admin: bool = False
    active: bool = True
    grants: tuple[Grant, ...] = ()


@dataclass(frozen=True)
class ResourceContext:
    kind: ResourceKind = ResourceKind.NONE
    found: bool = True
    #: organization that owns the resource (the trial's organization).
    organization_id: str | None = None
    trial_id: str | None = None
    relationship: Relationship = Relationship.ORGANIZATION
    #: stable canonical id; never a patient name or other PHI.
    resource_id: str | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    status: int
    reason_code: str
    capability: str
    policy_version: str = POLICY_VERSION
    #: organization whose data the caller is acting on (resource owner).
    organization_id: str | None = None
    trial_id: str | None = None
    held_via: str | None = None      # "role" | "grant" | "org_admin_default"

    @property
    def denied(self) -> bool:
        return not self.allowed


def _deny(status: int, reason: str, capability: str) -> Decision:
    return Decision(False, status, reason, capability)


def holds(principal: Principal, capability: Capability, trial_id: str | None) -> str | None:
    """How the principal holds the capability for this trial, or None."""
    spec = CATALOG.get(capability)
    if spec is None:
        return None
    for grant in principal.grants:
        mapped = LEGACY_ALIASES.get(grant.capability)
        name = mapped.value if mapped else grant.capability
        if name != capability.value:
            continue
        if grant.trial_id is None or (trial_id is not None and grant.trial_id == trial_id):
            return "grant"
    if principal.role in spec.default_roles:
        return "role"
    if spec.org_admin_default and principal.org_admin:
        return "org_admin_default"
    return None


#: Roles the policy knows how to reason about. Derived from the catalog (every
#: role any capability names) plus the patient. Anything else - unknown, missing,
#: malformed, or an operational role this policy was never written for - is
#: denied before grants are even looked at: a grant extends a SUPPORTED role, it
#: never creates standing for an unrecognised one.
SUPPORTED_ROLES = frozenset(
    {role for spec in CATALOG.values() for role in spec.default_roles} | {"patient"})


def authorize(principal: Principal, capability: Capability | str,
              resource: ResourceContext | None = None) -> Decision:
    resource = resource or ResourceContext()
    name = str(capability)
    if not principal.active:
        return _deny(403, "ACCOUNT_INACTIVE", name)
    if principal.role not in SUPPORTED_ROLES:
        return _deny(403, "UNSUPPORTED_ROLE", name)
    try:
        cap = Capability(name)
    except ValueError:
        return _deny(403, "UNMAPPED_CAPABILITY", name)
    if cap not in CATALOG:
        return _deny(403, "UNMAPPED_CAPABILITY", name)

    if resource.kind is not ResourceKind.NONE:
        if not resource.found:
            return _deny(404, "RESOURCE_NOT_FOUND", name)
        if resource.relationship in (Relationship.NONE, Relationship.UNMAPPED):
            return _deny(404, "NO_RELATIONSHIP", name)
        if resource.relationship is Relationship.SELF and principal.role != "patient":
            return _deny(404, "NO_RELATIONSHIP", name)
        # The patient role only ever acts as itself, on its own record.
        if principal.role == "patient" and resource.relationship is not Relationship.SELF:
            return _deny(404, "NO_RELATIONSHIP", name)
        if (resource.organization_id and resource.organization_id != principal.organization_id
                and resource.relationship is not Relationship.ASSIGNED):
            return _deny(404, "NO_RELATIONSHIP", name)

    if principal.role in ("sponsor", "cro") and cap in PATIENT_CLINICAL_WRITE_CAPABILITIES:
        # An explicit grant cannot turn a sponsor-side account into a clinical writer.
        return _deny(403, "CAPABILITY_MISSING", name)
    eligible = RECIPIENT_ROLES.get(cap)
    if eligible is not None and principal.role not in eligible:
        # Recipient eligibility applies to grants as well as defaults, evaluated at
        # request time so an old or hand-written grant cannot bypass it.
        return _deny(403, "CAPABILITY_MISSING", name)
    via = holds(principal, cap, resource.trial_id)
    if via is None:
        # No role default and no matching grant. Organization membership, or
        # administering the organization, is not a clinical capability.
        return _deny(403, "CAPABILITY_MISSING", name)
    if principal.role == "patient" and cap is not Capability.PATIENT_SCHEDULE_READ:
        return _deny(403, "CAPABILITY_MISSING", name)
    return Decision(
        True, 200, "ALLOWED", name,
        organization_id=resource.organization_id or principal.organization_id,
        trial_id=resource.trial_id, held_via=via)


def may_grant(grantor: Principal, capability: Capability, grantee_id: str,
              grantee_organization_id: str, grantee_role: str | None = None) -> tuple[bool, str]:
    """Rules for the grants API: administration is not a clinical right."""
    if grantee_organization_id != grantor.organization_id:
        return False, "GRANTEE_OUTSIDE_ORGANIZATION"
    eligible = RECIPIENT_ROLES.get(capability)
    if eligible is not None and grantee_role is not None and grantee_role not in eligible:
        return False, "GRANTEE_ROLE_NOT_ELIGIBLE"
    if capability in SELF_GRANT_PROTECTED and grantee_id == grantor.user_id:
        return False, "SELF_GRANT_OF_CLINICAL_CAPABILITY"
    return True, "OK"
