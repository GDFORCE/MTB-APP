"""The UCTSM capability catalog (P3-01).

STATUS OF THE ROLE DEFAULTS BELOW: **PROPOSED, NOT OWNER-APPROVED.**
P3-01 requires the role matrix to be reviewed by the product/clinical owner;
that review has not happened. The defaults are deliberately narrower than the
pre-P3 behaviour (where any organization member could do anything the tenant
check allowed) and every sensitive clinical right - correcting, reviewing,
approving or rejecting a schedule - has NO role default at all: it needs an
explicit, persisted grant. Anything not listed here is denied.

These names are authorization capabilities. They are unrelated to
``app.domain.schedule.capabilities`` (executable timing/condition operators) and
to the remote-audit capability grants.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

POLICY_VERSION = "uctsm-authz.v2"
#: Status of the role mapping, surfaced in the plan and the grants API.
#: "Selected under owner delegation" means the project owner delegated the choice
#: (docs/p1-p5-project-decisions.md, 15 Oct 2026) and the code implements it. It is
#: NOT an external clinical or regulatory sign-off, and no real user was granted
#: anything by it.
MATRIX_STATUS = "SELECTED_UNDER_OWNER_DELEGATION"
POLICY_PROVENANCE = {
    "selected_by": "project owner (delegated decision)",
    "selected_on": "2026-10-15",
    "source": "docs/p1-p5-project-decisions.md",
    "external_clinical_signoff": False,
}


class Capability(StrEnum):
    # schedule definition lifecycle
    SCHEDULE_READ = "schedule.read"
    SCHEDULE_EVIDENCE_READ = "schedule.evidence.read"
    SCHEDULE_EXPORT = "schedule.export"
    SCHEDULE_AUTHOR = "schedule.author"
    SCHEDULE_EXTRACT = "schedule.extract"
    SCHEDULE_VALIDATE = "schedule.validate"
    SCHEDULE_CORRECT = "schedule.correct"
    SCHEDULE_REVIEW = "schedule.review"
    SCHEDULE_APPROVE = "schedule.approve"
    SCHEDULE_REJECT = "schedule.reject"
    # patient schedule
    PATIENT_ENROL = "patient.enrol"
    PATIENT_SCHEDULE_READ = "patient.schedule.read"
    PATIENT_SCHEDULE_EVALUATE = "patient.schedule.evaluate"
    PATIENT_OCCURRENCE_RECORD = "patient.occurrence.record"
    PATIENT_ANCHOR_RECORD = "patient.anchor.record"
    PATIENT_STATE_RECORD = "patient.state.record"
    PATIENT_UNSCHEDULED_CREATE = "patient.unscheduled.create"
    PATIENT_IMPACT_PREVIEW = "patient.impact.preview"
    PATIENT_IMPACT_CONFIRM = "patient.impact.confirm"
    #: P5: preview a typed patient command (every command type needs this AND the
    #: capability of its own kind, see ``manifest``).
    PATIENT_COMMAND_PREVIEW = "patient.command.preview"
    #: P5: override / cancel / pause / resume / rebase / stop / reverse. High impact,
    #: so it has NO role default: it must be granted explicitly (proposal pending
    #: owner approval, like the rest of the matrix).
    PATIENT_COMMAND_HIGH_IMPACT = "patient.command.high_impact"
    #: P6: move a patient to an approved amendment (now, at a stated point), record that
    #: they remain on the current version, or - with ``patient.command.high_impact`` -
    #: return them. No role default; PI only; never self-grantable.
    AMENDMENT_ADOPT = "amendment.adopt"
    # protocol identity gate (P2)
    PROTOCOL_IDENTITY_READ = "protocol_identity.read"
    PROTOCOL_IDENTITY_REVIEW = "protocol_identity.review"
    # operational parity / cutover
    TRIAL_READ_MODE_READ = "trial.read_mode.read"
    TRIAL_READ_MODE_SET = "trial.read_mode.set"
    PARITY_RUN = "parity.run"
    PARITY_ACCEPT = "parity.accept"
    # organization-wide / development
    DASHBOARD_READ = "dashboard.read"
    DEMO_SEED = "demo.seed"
    # administration of grants themselves (organization administration, NOT a
    # clinical right: it cannot be used to grant oneself a clinical capability)
    GRANTS_MANAGE = "authorization.grants.manage"


class Privacy(StrEnum):
    """What a successful response may contain."""

    NONE = "NONE"                          # no patient or evidence content
    SCHEDULE = "SCHEDULE"                  # protocol schedule definition
    EVIDENCE = "EVIDENCE"                  # protocol text / source evidence
    PATIENT_DEIDENTIFIED = "PATIENT_DEIDENTIFIED"   # sponsor/CRO: no clinical notes
    PATIENT_CLINICAL = "PATIENT_CLINICAL"  # treating staff: dates, notes, actuals
    PATIENT_SELF = "PATIENT_SELF"          # the patient's own schedule only
    ADMIN = "ADMIN"                        # grants / settings


@dataclass(frozen=True)
class CapabilitySpec:
    capability: Capability
    #: Roles holding the capability by default (PROPOSED). Empty = grant only.
    default_roles: frozenset[str]
    #: Existing behaviour retained for compatibility: an organization admin holds
    #: it. Used only where the pre-P3 policy (P2 identity review) already said so.
    org_admin_default: bool = False
    #: Segregation of duties applies to this capability's commit.
    maker_checker: bool = False
    description: str = ""


def _roles(*names: str) -> frozenset[str]:
    return frozenset(names)


_SPONSOR_SIDE = ("sponsor", "cro")
_CLINICAL = ("pi", "crc")
_SITE = ("smo", "site")

CATALOG: dict[Capability, CapabilitySpec] = {spec.capability: spec for spec in (
    CapabilitySpec(Capability.SCHEDULE_READ, _roles(*_SPONSOR_SIDE, *_CLINICAL, *_SITE),
                   description="Read a trial's schedule definitions and versions"),
    CapabilitySpec(Capability.SCHEDULE_EVIDENCE_READ, _roles(*_SPONSOR_SIDE, *_CLINICAL),
                   description="Read review views, source evidence, accountability and extraction runs"),
    CapabilitySpec(Capability.SCHEDULE_EXPORT, _roles(*_SPONSOR_SIDE, "pi"),
                   description="Export a schedule version"),
    CapabilitySpec(Capability.SCHEDULE_AUTHOR, _roles(*_SPONSOR_SIDE),
                   description="Create trials, protocols and protocol versions"),
    CapabilitySpec(Capability.SCHEDULE_EXTRACT, _roles(*_SPONSOR_SIDE, "pi"),
                   description="Queue schedule extraction for a protocol version"),
    CapabilitySpec(Capability.SCHEDULE_VALIDATE, _roles(*_SPONSOR_SIDE, "pi"),
                   description="Re-run validation on a draft"),
    CapabilitySpec(Capability.SCHEDULE_CORRECT, _roles(),
                   description="Correct draft schedule content (a material edit)"),
    CapabilitySpec(Capability.SCHEDULE_REVIEW, _roles(),
                   description="Submit, confirm and record review decisions on a draft"),
    CapabilitySpec(Capability.SCHEDULE_APPROVE, _roles(), maker_checker=True,
                   description="Approve a schedule version"),
    CapabilitySpec(Capability.SCHEDULE_REJECT, _roles(),
                   description="Reject a schedule version in review"),
    CapabilitySpec(Capability.PATIENT_ENROL, _roles(*_CLINICAL, *_SITE),
                   description="Enrol a patient onto an approved schedule"),
    CapabilitySpec(Capability.PATIENT_SCHEDULE_READ, _roles(*_CLINICAL, *_SPONSOR_SIDE, "patient"),
                   description="Read a patient's schedule (patient: own only; sponsor/CRO: de-identified)"),
    CapabilitySpec(Capability.PATIENT_SCHEDULE_EVALUATE, _roles(*_CLINICAL),
                   description="Evaluate / recompute a patient's schedule"),
    CapabilitySpec(Capability.PATIENT_OCCURRENCE_RECORD, _roles(*_CLINICAL),
                   description="Record or correct an actual visit/activity occurrence"),
    CapabilitySpec(Capability.PATIENT_ANCHOR_RECORD, _roles(*_CLINICAL),
                   description="Record or confirm a patient anchor date"),
    CapabilitySpec(Capability.PATIENT_STATE_RECORD, _roles(*_CLINICAL),
                   description="Record patient state or condition changes"),
    CapabilitySpec(Capability.PATIENT_UNSCHEDULED_CREATE, _roles(*_CLINICAL),
                   description="Create an unscheduled visit"),
    CapabilitySpec(Capability.PATIENT_IMPACT_PREVIEW, _roles(*_CLINICAL),
                   description="Preview the effect of a change on a patient's schedule"),
    CapabilitySpec(Capability.PATIENT_IMPACT_CONFIRM, _roles("pi"),
                   description="Confirm or cancel a schedule impact proposal"),
    CapabilitySpec(Capability.PATIENT_COMMAND_PREVIEW, _roles(*_CLINICAL),
                   description="Preview a typed patient schedule command and read the command ledger"),
    CapabilitySpec(Capability.PATIENT_COMMAND_HIGH_IMPACT, _roles(),
                   description="Override, cancel, pause, resume, rebase, stop or reverse on a patient's schedule"),
    CapabilitySpec(Capability.AMENDMENT_ADOPT, _roles(),
                   description="Adopt, defer, decline (remain) or return an approved protocol amendment for a patient"),
    CapabilitySpec(Capability.PROTOCOL_IDENTITY_READ, _roles(*_SPONSOR_SIDE, "pi"),
                   description="See protocol identity results for one's own uploads"),
    #: Selected policy (docs/p1-p5-project-decisions.md): explicit grant only. Being
    #: an organization administrator is not a clinical qualification.
    CapabilitySpec(Capability.PROTOCOL_IDENTITY_REVIEW, _roles(),
                   description="Resolve and resume quarantined protocol uploads"),
    CapabilitySpec(Capability.TRIAL_READ_MODE_READ, _roles(*_SPONSOR_SIDE, "pi"),
                   description="Read a trial's read mode and cutover readiness"),
    CapabilitySpec(Capability.TRIAL_READ_MODE_SET, _roles(),
                   description="Move a trial's visit reads between stores"),
    CapabilitySpec(Capability.PARITY_RUN, _roles(*_SPONSOR_SIDE, "pi"),
                   description="Run a parity comparison for a patient"),
    CapabilitySpec(Capability.PARITY_ACCEPT, _roles(),
                   description="Sign off a parity run with differences"),
    CapabilitySpec(Capability.DASHBOARD_READ, _roles(*_CLINICAL, *_SPONSOR_SIDE),
                   description="Read the action dashboard (scoped to authorised trials)"),
    CapabilitySpec(Capability.DEMO_SEED, _roles(),
                   description="Seed demonstration data (also needs UCTSM_DEMO_MODE)"),
    CapabilitySpec(Capability.GRANTS_MANAGE, _roles(), org_admin_default=True,
                   description="Manage capability grants within one's organization"),
)}

#: Capabilities that carry clinical meaning. An organization admin never gets
#: these through ``org_admin`` alone, and nobody may grant them to themselves.
CLINICAL_DECISION_CAPABILITIES = frozenset({
    Capability.SCHEDULE_CORRECT, Capability.SCHEDULE_REVIEW, Capability.SCHEDULE_APPROVE,
    Capability.SCHEDULE_REJECT, Capability.AMENDMENT_ADOPT, Capability.PATIENT_IMPACT_CONFIRM,
    Capability.TRIAL_READ_MODE_SET, Capability.PARITY_ACCEPT, Capability.PATIENT_COMMAND_HIGH_IMPACT,
})

#: Clinical WRITE capabilities on a patient. Sponsor/CRO may never hold these, not
#: even through an explicit grant (their patient views stay de-identified/read-only).
PATIENT_CLINICAL_WRITE_CAPABILITIES = frozenset({
    Capability.PATIENT_ENROL, Capability.PATIENT_SCHEDULE_EVALUATE,
    Capability.PATIENT_OCCURRENCE_RECORD, Capability.PATIENT_ANCHOR_RECORD,
    Capability.PATIENT_STATE_RECORD, Capability.PATIENT_UNSCHEDULED_CREATE,
    Capability.PATIENT_IMPACT_PREVIEW, Capability.PATIENT_IMPACT_CONFIRM,
    Capability.PATIENT_COMMAND_HIGH_IMPACT, Capability.AMENDMENT_ADOPT,
})

#: WHO may hold a capability at all - for role defaults AND explicit grants. A grant
#: extends a holder inside this set; it cannot make an ineligible role eligible, and
#: a stored grant to an ineligible role is ignored at request time.
RECIPIENT_ROLES: dict[Capability, frozenset[str]] = {
    Capability.PATIENT_COMMAND_HIGH_IMPACT: frozenset({"pi"}),
    Capability.PATIENT_IMPACT_CONFIRM: frozenset({"pi"}),
    Capability.AMENDMENT_ADOPT: frozenset({"pi"}),
    Capability.PATIENT_OCCURRENCE_RECORD: frozenset({"pi", "crc"}),
    Capability.PATIENT_ANCHOR_RECORD: frozenset({"pi", "crc"}),
    Capability.PATIENT_STATE_RECORD: frozenset({"pi", "crc"}),
    Capability.PATIENT_UNSCHEDULED_CREATE: frozenset({"pi", "crc"}),
    Capability.PATIENT_SCHEDULE_EVALUATE: frozenset({"pi", "crc"}),
    Capability.PATIENT_IMPACT_PREVIEW: frozenset({"pi", "crc"}),
    Capability.PATIENT_ENROL: frozenset({"pi", "crc", "site", "smo"}),
}

#: Nobody may grant these to themselves (stricter self-grant rule): every clinical
#: write/decision right plus identity review, authority/read-mode and parity sign-off.
SELF_GRANT_PROTECTED = CLINICAL_DECISION_CAPABILITIES | PATIENT_CLINICAL_WRITE_CAPABILITIES | frozenset({
    Capability.PROTOCOL_IDENTITY_REVIEW,
})

#: Old / informal permission strings that already exist in stored users.
LEGACY_ALIASES: dict[str, Capability] = {
    "protocol_identity.review": Capability.PROTOCOL_IDENTITY_REVIEW,
}


def spec_for(capability: str | Capability) -> CapabilitySpec | None:
    try:
        return CATALOG.get(Capability(str(capability)))
    except ValueError:
        return None
