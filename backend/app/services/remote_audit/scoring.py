"""Two-axis scoring and the final decision — spec §13.

Pure logic: no DB, no network. The engine takes already-gathered inputs and
returns a deterministic result, which is what makes every §16.11 edge case
testable directly.

THE CENTRAL DESIGN POINT — TWO AXES THAT NEVER MIX:

    COMPLIANCE   "Did the site meet the requirements?"
    CONFIDENCE   "How sure are we that the evidence showing that is real?"

These are different questions with different answers, and the spec is explicit
that they must be reported separately. A site with perfect compliance whose
every photo failed verification is not 'mostly fine' — it is fully compliant
on paper and unverifiable in practice, and a reviewer has to see both numbers
to know that. Averaging them into one score would destroy exactly the
information the module exists to produce.

The other rules encoded here, each guarding a specific §16.11 failure:

* Hard rules run BEFORE the weighted average and can override it outright, so
  a critical failure can never be buried under a high average.
* N/A is EXCLUDED from the denominator. Scoring it as zero would punish a site
  for a requirement that never applied to it.
* MISSING is NOT N/A. An unanswered requirement scores zero and is named —
  silently treating it as not-applicable is how a gap disappears.
* Comparisons use unrounded values; rounding is display-only. 79.5 that rounds
  to 80 does not pass an 80 threshold.
* A reviewer override is stored ALONGSIDE the machine result, never instead
  of it, with a mandatory reason.
* Every result carries the algorithm version that produced it, so a later
  change never retroactively rewrites a historical audit's meaning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

ALGORITHM_VERSION = "remote-audit-scoring-v1"

# Comparisons are made on unrounded values; this tolerance only absorbs binary
# floating-point noise (0.1 + 0.2 != 0.3), never a real shortfall.
EPSILON = 1e-9


class RequirementOutcome(str, Enum):
    MET = "MET"
    PARTIAL = "PARTIAL"
    NOT_MET = "NOT_MET"
    #: Genuinely does not apply to this site. Excluded from the denominator.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    #: Nobody answered. Scores zero and is reported by name — this is the
    #: state that must never be quietly folded into NOT_APPLICABLE.
    MISSING = "MISSING"
    #: Not met, but formally waived with a recorded justification. Counts as
    #: met for scoring, and is always listed so the waiver stays visible.
    WAIVED = "WAIVED"


#: Fraction of a requirement's weight earned by each outcome.
OUTCOME_CREDIT: Dict[RequirementOutcome, float] = {
    RequirementOutcome.MET: 1.0,
    RequirementOutcome.PARTIAL: 0.5,
    RequirementOutcome.NOT_MET: 0.0,
    RequirementOutcome.MISSING: 0.0,
    RequirementOutcome.WAIVED: 1.0,
}


class EvidenceConfidence(str, Enum):
    """Mirrors the evidence engine's verdicts. Note there is no mapping from
    these into the compliance axis anywhere in this module — that is the
    separation the spec requires."""
    HIGH = "HIGH"
    LIMITED = "LIMITED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"


CONFIDENCE_CREDIT: Dict[EvidenceConfidence, float] = {
    EvidenceConfidence.HIGH: 1.0,
    EvidenceConfidence.LIMITED: 0.6,
    EvidenceConfidence.UNVERIFIED: 0.2,
    EvidenceConfidence.FAILED: 0.0,
}


class Decision(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_CONDITIONS = "APPROVED_WITH_CONDITIONS"
    NOT_APPROVED = "NOT_APPROVED"
    #: The machine will not propose an outcome — a human must decide.
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class RequirementResult:
    requirement_id: str
    section_id: str
    outcome: RequirementOutcome
    #: True when failing this single requirement fails the audit regardless of
    #: everything else (spec §13.3's hard rules).
    is_critical: bool = False
    weight: float = 1.0
    evidence_confidence: Optional[EvidenceConfidence] = None
    waiver_reason: Optional[str] = None
    note: Optional[str] = None


@dataclass(frozen=True)
class SectionPolicy:
    section_id: str
    #: Section weight as a percentage of the whole audit. All sections must
    #: total 100 — validated at template activation and re-checked here.
    weight_percent: float
    #: Optional per-section floor. A section below it blocks approval even if
    #: the overall average is comfortable.
    minimum_percent: Optional[float] = None


@dataclass(frozen=True)
class ScoringPolicy:
    sections: Sequence[SectionPolicy]
    approval_threshold_percent: float = 80.0
    conditional_threshold_percent: float = 60.0
    #: Confidence floor below which the machine refuses to propose approval —
    #: high compliance on unverifiable evidence is not an approvable state.
    minimum_confidence_percent: float = 50.0
    #: Whether an outstanding critical CAPA blocks the final decision.
    block_on_open_critical_capa: bool = True


@dataclass
class SectionScore:
    section_id: str
    weight_percent: float
    #: Unrounded. `display_percent` is the rounded one, and comparisons never
    #: use it.
    compliance_percent: float
    counted_weight: float
    applicable_count: int
    not_applicable_count: int
    missing_count: int
    waived_count: int
    critical_failures: List[str] = field(default_factory=list)
    below_minimum: bool = False

    @property
    def display_percent(self) -> float:
        return round(self.compliance_percent, 1)


@dataclass
class Blocker:
    code: str
    severity: str
    explanation: str
    requirement_ids: List[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    #: Axis 1 — did the site meet the requirements?
    compliance_percent: float
    #: Axis 2 — how much can the evidence for that be relied on? Never mixed
    #: into `compliance_percent`, by construction.
    evidence_confidence_percent: Optional[float]
    proposed_decision: Decision
    blockers: List[Blocker]
    sections: List[SectionScore]
    algorithm_version: str
    #: Everything the reviewer needs to see before the number.
    notes: List[str] = field(default_factory=list)

    @property
    def display_compliance(self) -> float:
        return round(self.compliance_percent, 1)

    @property
    def display_confidence(self) -> Optional[float]:
        return None if self.evidence_confidence_percent is None else round(
            self.evidence_confidence_percent, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compliance_percent": self.compliance_percent,
            "compliance_display": self.display_compliance,
            "evidence_confidence_percent": self.evidence_confidence_percent,
            "evidence_confidence_display": self.display_confidence,
            "proposed_decision": self.proposed_decision.value,
            "blockers": [b.__dict__ for b in self.blockers],
            "sections": [
                {**s.__dict__, "display_percent": s.display_percent} for s in self.sections
            ],
            "algorithm_version": self.algorithm_version,
            "notes": list(self.notes),
        }


class ScoringError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_policy(policy: ScoringPolicy) -> None:
    """Section weights must total exactly 100%.

    Enforced at activation AND here. A policy whose weights total 95 or 105
    produces a percentage that is not a percentage, and the resulting number
    would look entirely plausible.
    """
    if not policy.sections:
        raise ScoringError("NO_SECTIONS", "a scoring policy needs at least one section")
    total = sum(s.weight_percent for s in policy.sections)
    if abs(total - 100.0) > 1e-6:
        raise ScoringError("WEIGHTS_MUST_TOTAL_100",
                           f"section weights total {total}%, not 100%")
    ids = [s.section_id for s in policy.sections]
    if len(ids) != len(set(ids)):
        raise ScoringError("DUPLICATE_SECTION", "a section appears more than once in the policy")


def score_section(
    section: SectionPolicy, results: Sequence[RequirementResult],
) -> SectionScore:
    """Score one section.

    The denominator is the weight of APPLICABLE requirements only. N/A is
    excluded entirely — including it as a zero would penalise a site for a
    requirement that never applied to them, which is the §16.11 "N/A treated
    as zero" failure.

    MISSING is deliberately NOT excluded: it is an applicable requirement
    nobody answered, it scores zero, and it is counted so the reviewer is told
    how much of the section is simply absent.
    """
    counted_weight = 0.0
    earned = 0.0
    not_applicable = 0
    missing = 0
    waived = 0
    critical_failures: List[str] = []

    for result in results:
        if result.outcome == RequirementOutcome.NOT_APPLICABLE:
            not_applicable += 1
            continue
        if result.outcome == RequirementOutcome.MISSING:
            missing += 1
        if result.outcome == RequirementOutcome.WAIVED:
            waived += 1
        counted_weight += result.weight
        earned += result.weight * OUTCOME_CREDIT[result.outcome]
        # A waived critical requirement is not a critical failure — that is
        # what the waiver is. It stays visible through `waived_count`.
        if result.is_critical and result.outcome in (
            RequirementOutcome.NOT_MET, RequirementOutcome.MISSING,
        ):
            critical_failures.append(result.requirement_id)

    # A section where every requirement is N/A has no opinion. Reporting 0%
    # would be a failing grade for a section that did not apply; reporting
    # 100% would be an unearned pass. It is excluded from the total instead
    # (see `score_audit`'s renormalisation).
    percent = 100.0 * earned / counted_weight if counted_weight > 0 else float("nan")

    below_minimum = (
        section.minimum_percent is not None
        and counted_weight > 0
        and percent + EPSILON < section.minimum_percent
    )
    return SectionScore(
        section_id=section.section_id,
        weight_percent=section.weight_percent,
        compliance_percent=percent,
        counted_weight=counted_weight,
        applicable_count=sum(1 for r in results if r.outcome != RequirementOutcome.NOT_APPLICABLE),
        not_applicable_count=not_applicable,
        missing_count=missing,
        waived_count=waived,
        critical_failures=critical_failures,
        below_minimum=below_minimum,
    )


def _confidence_percent(results: Sequence[RequirementResult]) -> Optional[float]:
    """Axis 2, computed only from requirements that actually carry evidence.

    Returns None when nothing was evidenced at all, which is a different
    statement from 0% and must not be rendered as one.
    """
    graded = [r for r in results if r.evidence_confidence is not None]
    if not graded:
        return None
    total_weight = sum(r.weight for r in graded)
    if total_weight <= 0:
        return None
    earned = sum(r.weight * CONFIDENCE_CREDIT[r.evidence_confidence] for r in graded
                 if r.evidence_confidence is not None)
    return 100.0 * earned / total_weight


def score_audit(
    *,
    policy: ScoringPolicy,
    results: Sequence[RequirementResult],
    open_critical_capa_ids: Sequence[str] = (),
) -> ScoreResult:
    """Produce both axes and a proposed decision.

    Order is load-bearing: hard rules are collected first and evaluated
    against the outcome last, so a critical failure overrides the weighted
    average rather than being diluted by it.
    """
    validate_policy(policy)

    by_section: Dict[str, List[RequirementResult]] = {s.section_id: [] for s in policy.sections}
    orphans = [r.requirement_id for r in results if r.section_id not in by_section]
    if orphans:
        raise ScoringError(
            "UNKNOWN_SECTION",
            f"these requirements name a section the policy does not define: {', '.join(orphans)}")
    for result in results:
        by_section[result.section_id].append(result)

    sections = [score_section(s, by_section[s.section_id]) for s in policy.sections]

    # Renormalise across sections that actually had applicable requirements.
    # Keeping an all-N/A section's weight in the denominator would silently
    # cap the achievable score below 100%.
    scored = [s for s in sections if s.counted_weight > 0]
    live_weight = sum(s.weight_percent for s in scored)
    notes: List[str] = []
    if live_weight <= 0:
        compliance = float("nan")
        notes.append("No applicable requirements were scored, so there is no compliance score.")
    else:
        compliance = sum(s.compliance_percent * s.weight_percent for s in scored) / live_weight
        if abs(live_weight - 100.0) > 1e-6:
            notes.append(
                f"{100.0 - live_weight:.1f}% of the audit's weight was not applicable to this site "
                "and has been excluded from the score rather than counted as zero.")

    confidence = _confidence_percent(results)

    # ── Hard rules, evaluated before the average matters ───────────────────
    blockers: List[Blocker] = []

    critical_failures = [rid for s in sections for rid in s.critical_failures]
    if critical_failures:
        blockers.append(Blocker(
            code="CRITICAL_REQUIREMENT_FAILED", severity="critical",
            explanation="A critical requirement was not met. This fails the audit regardless of "
                        "the overall score.",
            requirement_ids=critical_failures))

    missing = [r.requirement_id for r in results if r.outcome == RequirementOutcome.MISSING]
    if missing:
        blockers.append(Blocker(
            code="REQUIREMENTS_UNANSWERED", severity="high",
            explanation="Some applicable requirements were never answered. They score zero and "
                        "are listed here rather than being treated as not applicable.",
            requirement_ids=missing))

    for section in sections:
        if section.below_minimum:
            blockers.append(Blocker(
                code="SECTION_BELOW_MINIMUM", severity="high",
                explanation=f"Section {section.section_id} scored {section.display_percent}%, "
                            f"below its required minimum.",
                requirement_ids=[]))

    if open_critical_capa_ids and policy.block_on_open_critical_capa:
        blockers.append(Blocker(
            code="OPEN_CRITICAL_CAPA", severity="critical",
            explanation="Critical corrective actions are still outstanding on this audit.",
            requirement_ids=list(open_critical_capa_ids)))

    if confidence is not None and confidence + EPSILON < policy.minimum_confidence_percent:
        blockers.append(Blocker(
            code="EVIDENCE_CONFIDENCE_TOO_LOW", severity="high",
            explanation=f"Evidence confidence is {round(confidence, 1)}%, below the "
                        f"{policy.minimum_confidence_percent}% needed to rely on these results. "
                        "This is separate from compliance — the site may have met every "
                        "requirement on evidence that cannot be verified.",
            requirement_ids=[]))
    elif confidence is None:
        notes.append("No evidence was graded for confidence, so the confidence axis is not a "
                     "score of zero — it is absent.")

    waived = [r.requirement_id for r in results if r.outcome == RequirementOutcome.WAIVED]
    if waived:
        notes.append(f"{len(waived)} requirement(s) counted as met under a recorded waiver.")

    # ── Proposed decision ─────────────────────────────────────────────────
    decision = _propose(compliance, blockers, policy)

    return ScoreResult(
        compliance_percent=compliance,
        evidence_confidence_percent=confidence,
        proposed_decision=decision,
        blockers=blockers,
        sections=sections,
        algorithm_version=ALGORITHM_VERSION,
        notes=notes,
    )


def _propose(compliance: float, blockers: Sequence[Blocker], policy: ScoringPolicy) -> Decision:
    """Turn the score and the blockers into a proposal.

    A `critical` blocker fails the audit outright — this is the check that
    stops a critical failure being hidden by a high average. Comparisons use
    the unrounded value, so 79.95 does not clear an 80% threshold just because
    it would display as 80.
    """
    if any(b.severity == "critical" for b in blockers):
        return Decision.NOT_APPROVED
    if compliance != compliance:  # NaN — nothing scored
        return Decision.REVIEW_REQUIRED
    if any(b.severity == "high" for b in blockers):
        # Something real is wrong but nothing is disqualifying. The machine
        # declines to propose approval and hands it to a person.
        return Decision.REVIEW_REQUIRED
    if compliance + EPSILON >= policy.approval_threshold_percent:
        return Decision.APPROVED
    if compliance + EPSILON >= policy.conditional_threshold_percent:
        return Decision.APPROVED_WITH_CONDITIONS
    return Decision.NOT_APPROVED


# ── Reviewer override ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Override:
    decision: Decision
    reason: str
    overridden_by: str


def apply_override(machine: ScoreResult, override: Override) -> Dict[str, Any]:
    """Record a reviewer's override ALONGSIDE the machine result.

    The machine's proposal and its blockers stay in the record untouched. An
    override that replaced them would erase the very disagreement an inspector
    is most interested in — "the system said not approved, a human approved it,
    and here is why" is the useful artefact.
    """
    if not (override.reason or "").strip():
        raise ScoringError("OVERRIDE_REASON_REQUIRED",
                           "overriding the machine's proposal requires a stated reason")
    return {
        "machine_result": machine.to_dict(),
        "final_decision": override.decision.value,
        "overridden": override.decision != machine.proposed_decision,
        "override_reason": override.reason.strip(),
        "overridden_by": override.overridden_by,
        "algorithm_version": machine.algorithm_version,
    }


def conditional_approval_is_valid(
    decision: Decision, linked_capa_ids: Sequence[str], capa_lookup: Dict[str, Dict[str, Any]],
) -> None:
    """An APPROVED_WITH_CONDITIONS decision must actually have conditions.

    Every linked CAPA needs an owner and a due date, because a condition
    nobody owns by no particular date is not a condition — it is a hope, and
    approving against it is approving unconditionally while saying otherwise.
    """
    if decision != Decision.APPROVED_WITH_CONDITIONS:
        return
    if not linked_capa_ids:
        raise ScoringError("CONDITIONS_REQUIRED",
                           "a conditional approval must link at least one CAPA")
    for capa_id in linked_capa_ids:
        capa = capa_lookup.get(capa_id)
        if capa is None:
            raise ScoringError("UNKNOWN_CAPA", f"CAPA {capa_id} does not exist on this audit")
        if not capa.get("owner_user_id"):
            raise ScoringError("CAPA_OWNER_REQUIRED", f"CAPA {capa_id} has no accountable owner")
        if not capa.get("due_date"):
            raise ScoringError("CAPA_DUE_DATE_REQUIRED", f"CAPA {capa_id} has no due date")
