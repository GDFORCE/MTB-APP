"""Two-axis scoring and decision — spec §13, §16.11.

Per the plan's Phase 7 exit criterion, every scenario here is written so that
it would FAIL against the naive implementation, not merely pass against the
correct one. Each test names the shortcut it is guarding against.

Pure functions throughout, so no DB fixture is needed.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.remote_audit.scoring import (  # noqa: E402
    ALGORITHM_VERSION,
    Decision,
    EvidenceConfidence,
    Override,
    RequirementOutcome as RO,
    RequirementResult,
    ScoringError,
    ScoringPolicy,
    SectionPolicy,
    apply_override,
    conditional_approval_is_valid,
    score_audit,
    score_section,
    validate_policy,
)

ONE_SECTION = ScoringPolicy(sections=[SectionPolicy("facilities", 100.0)])
TWO_SECTIONS = ScoringPolicy(sections=[
    SectionPolicy("facilities", 50.0),
    SectionPolicy("equipment", 50.0),
])


def req(rid, outcome, *, section="facilities", weight=1.0, critical=False, confidence=None):
    return RequirementResult(
        requirement_id=rid, section_id=section, outcome=outcome,
        weight=weight, is_critical=critical, evidence_confidence=confidence)


# ═══════════════════════════════════════════════════════════════════════════
# Weight validation
# ═══════════════════════════════════════════════════════════════════════════

def test_weights_that_do_not_total_100_are_refused():
    """Naive version: sum whatever weights exist and divide. That produces a
    number that looks like a percentage and isn't."""
    for weights in ((50.0, 40.0), (60.0, 50.0), (100.0, 1.0)):
        policy = ScoringPolicy(sections=[
            SectionPolicy("a", weights[0]), SectionPolicy("b", weights[1])])
        with pytest.raises(ScoringError) as exc:
            validate_policy(policy)
        assert exc.value.code == "WEIGHTS_MUST_TOTAL_100"


def test_a_duplicated_section_is_refused():
    with pytest.raises(ScoringError) as exc:
        validate_policy(ScoringPolicy(sections=[
            SectionPolicy("a", 50.0), SectionPolicy("a", 50.0)]))
    assert exc.value.code == "DUPLICATE_SECTION"


def test_a_requirement_naming_an_unknown_section_is_refused():
    """Naive version: silently drop it, quietly removing a requirement from
    the audit."""
    with pytest.raises(ScoringError) as exc:
        score_audit(policy=ONE_SECTION, results=[req("r1", RO.MET, section="nowhere")])
    assert exc.value.code == "UNKNOWN_SECTION"


# ═══════════════════════════════════════════════════════════════════════════
# N/A handling
# ═══════════════════════════════════════════════════════════════════════════

def test_not_applicable_is_excluded_from_the_denominator_not_scored_as_zero():
    """THE §16.11 classic. Naive version scores N/A as 0, punishing a site for
    a requirement that never applied to them."""
    score = score_section(SectionPolicy("facilities", 100.0), [
        req("r1", RO.MET), req("r2", RO.MET), req("r3", RO.NOT_APPLICABLE),
    ])
    # Two of two applicable requirements met = 100%, not 66.7%.
    assert score.compliance_percent == pytest.approx(100.0)
    assert score.not_applicable_count == 1
    assert score.counted_weight == pytest.approx(2.0)


def test_missing_is_not_treated_as_not_applicable():
    """The inverse and more dangerous shortcut: fold MISSING into N/A and an
    unanswered requirement vanishes from the audit entirely."""
    score = score_section(SectionPolicy("facilities", 100.0), [
        req("r1", RO.MET), req("r2", RO.MISSING),
    ])
    # MISSING stays in the denominator and scores zero.
    assert score.compliance_percent == pytest.approx(50.0)
    assert score.missing_count == 1
    assert score.counted_weight == pytest.approx(2.0)


def test_an_unanswered_requirement_is_named_in_the_blockers():
    result = score_audit(policy=ONE_SECTION, results=[
        req("r1", RO.MET), req("r2", RO.MISSING),
    ])
    blocker = next(b for b in result.blockers if b.code == "REQUIREMENTS_UNANSWERED")
    assert blocker.requirement_ids == ["r2"]
    # And it prevents an automatic approval.
    assert result.proposed_decision == Decision.REVIEW_REQUIRED


def test_an_all_na_section_is_excluded_rather_than_scored_zero_or_100():
    """Naive versions report either 0% (a failing grade for something that
    didn't apply) or 100% (an unearned pass)."""
    result = score_audit(policy=TWO_SECTIONS, results=[
        req("r1", RO.MET, section="facilities"),
        req("r2", RO.NOT_APPLICABLE, section="equipment"),
    ])
    # Only `facilities` was live, and it was fully met.
    assert result.compliance_percent == pytest.approx(100.0)
    assert any("not applicable" in note for note in result.notes)
    equipment = next(s for s in result.sections if s.section_id == "equipment")
    assert math.isnan(equipment.compliance_percent)


def test_an_entirely_na_audit_has_no_score_and_goes_to_review():
    result = score_audit(policy=ONE_SECTION, results=[req("r1", RO.NOT_APPLICABLE)])
    assert math.isnan(result.compliance_percent)
    assert result.proposed_decision == Decision.REVIEW_REQUIRED


# ═══════════════════════════════════════════════════════════════════════════
# Hard rules beat the average
# ═══════════════════════════════════════════════════════════════════════════

def test_a_critical_failure_is_not_hidden_by_a_high_average():
    """THE headline §16.11 case. Naive version computes the weighted average,
    sees 96%, and approves — while a critical requirement sits failed."""
    results = [req(f"r{i}", RO.MET) for i in range(24)]
    results.append(req("critical-1", RO.NOT_MET, critical=True))

    result = score_audit(policy=ONE_SECTION, results=results)

    assert result.compliance_percent == pytest.approx(96.0)
    assert result.proposed_decision == Decision.NOT_APPROVED
    blocker = next(b for b in result.blockers if b.code == "CRITICAL_REQUIREMENT_FAILED")
    assert blocker.requirement_ids == ["critical-1"]
    assert blocker.severity == "critical"


def test_a_missing_critical_requirement_also_fails_the_audit():
    """Unanswered is not a free pass on a critical item."""
    result = score_audit(policy=ONE_SECTION, results=[
        req("r1", RO.MET), req("critical-1", RO.MISSING, critical=True),
    ])
    assert result.proposed_decision == Decision.NOT_APPROVED
    assert any(b.code == "CRITICAL_REQUIREMENT_FAILED" for b in result.blockers)


def test_a_waived_critical_requirement_is_not_a_critical_failure():
    """A waiver is a recorded decision that this requirement doesn't apply
    here. Treating it as a failure would make waivers meaningless — but it
    stays visible, so nobody can hide behind one."""
    result = score_audit(policy=ONE_SECTION, results=[
        req("r1", RO.MET),
        RequirementResult("critical-1", "facilities", RO.WAIVED, is_critical=True,
                          waiver_reason="Not required under this jurisdiction."),
    ])
    assert not any(b.code == "CRITICAL_REQUIREMENT_FAILED" for b in result.blockers)
    assert result.compliance_percent == pytest.approx(100.0)
    assert any("waiver" in note for note in result.notes)
    assert next(s for s in result.sections if s.section_id == "facilities").waived_count == 1


def test_a_section_below_its_minimum_blocks_approval_despite_a_good_average():
    """Naive version only checks the overall total, so a catastrophic section
    can be averaged away by strong ones."""
    policy = ScoringPolicy(sections=[
        SectionPolicy("facilities", 80.0),
        SectionPolicy("equipment", 20.0, minimum_percent=70.0),
    ])
    results = [req(f"f{i}", RO.MET, section="facilities") for i in range(10)]
    results += [req("e1", RO.NOT_MET, section="equipment"),
                req("e2", RO.MET, section="equipment")]

    result = score_audit(policy=policy, results=results)

    assert result.compliance_percent == pytest.approx(90.0)  # comfortably over 80
    assert result.proposed_decision != Decision.APPROVED
    assert any(b.code == "SECTION_BELOW_MINIMUM" for b in result.blockers)


def test_an_open_critical_capa_blocks_the_decision():
    result = score_audit(policy=ONE_SECTION, results=[req("r1", RO.MET)],
                         open_critical_capa_ids=["capa-1"])
    assert result.proposed_decision == Decision.NOT_APPROVED
    assert any(b.code == "OPEN_CRITICAL_CAPA" for b in result.blockers)


def test_capa_blocking_is_configurable():
    policy = ScoringPolicy(sections=[SectionPolicy("facilities", 100.0)],
                           block_on_open_critical_capa=False)
    result = score_audit(policy=policy, results=[req("r1", RO.MET)],
                         open_critical_capa_ids=["capa-1"])
    assert result.proposed_decision == Decision.APPROVED


# ═══════════════════════════════════════════════════════════════════════════
# The two axes never mix
# ═══════════════════════════════════════════════════════════════════════════

def test_evidence_confidence_never_leaks_into_the_compliance_score():
    """§16.11 explicitly. Naive version multiplies compliance by confidence,
    producing one number that answers neither question."""
    perfect_evidence = score_audit(policy=ONE_SECTION, results=[
        req("r1", RO.MET, confidence=EvidenceConfidence.HIGH),
        req("r2", RO.MET, confidence=EvidenceConfidence.HIGH),
    ])
    worthless_evidence = score_audit(policy=ONE_SECTION, results=[
        req("r1", RO.MET, confidence=EvidenceConfidence.FAILED),
        req("r2", RO.MET, confidence=EvidenceConfidence.FAILED),
    ])

    # Identical compliance: the site met every requirement in both cases.
    assert perfect_evidence.compliance_percent == pytest.approx(100.0)
    assert worthless_evidence.compliance_percent == pytest.approx(100.0)
    # Wildly different confidence.
    assert perfect_evidence.evidence_confidence_percent == pytest.approx(100.0)
    assert worthless_evidence.evidence_confidence_percent == pytest.approx(0.0)


def test_perfect_compliance_on_unverifiable_evidence_is_not_approvable():
    """The scenario the two-axis model exists for: everything met, nothing
    verifiable. A single blended score would call this a pass."""
    result = score_audit(policy=ONE_SECTION, results=[
        req("r1", RO.MET, confidence=EvidenceConfidence.FAILED),
        req("r2", RO.MET, confidence=EvidenceConfidence.FAILED),
    ])
    assert result.compliance_percent == pytest.approx(100.0)
    assert result.proposed_decision != Decision.APPROVED
    blocker = next(b for b in result.blockers if b.code == "EVIDENCE_CONFIDENCE_TOO_LOW")
    assert "separate from compliance" in blocker.explanation


def test_absent_confidence_is_none_not_zero():
    """None means "nothing was evidenced"; 0% means "everything was evidenced
    and all of it failed". Rendering the first as the second would invent a
    catastrophic finding out of an unscored axis."""
    result = score_audit(policy=ONE_SECTION, results=[req("r1", RO.MET)])
    assert result.evidence_confidence_percent is None
    assert result.display_confidence is None
    assert not any(b.code == "EVIDENCE_CONFIDENCE_TOO_LOW" for b in result.blockers)
    assert any("absent" in note for note in result.notes)


def test_confidence_is_weighted_by_requirement_weight():
    result = score_audit(policy=ONE_SECTION, results=[
        req("heavy", RO.MET, weight=9.0, confidence=EvidenceConfidence.HIGH),
        req("light", RO.MET, weight=1.0, confidence=EvidenceConfidence.FAILED),
    ])
    assert result.evidence_confidence_percent == pytest.approx(90.0)


# ═══════════════════════════════════════════════════════════════════════════
# Rounding never changes an outcome
# ═══════════════════════════════════════════════════════════════════════════

def test_a_score_that_rounds_up_to_the_threshold_does_not_pass_it():
    """§16.11's rounding case. Naive version rounds for display and then
    compares the rounded value, so 79.95 becomes 80.0 and passes."""
    # 1598 of 2000 weight = 79.9%, which displays as 79.9 but is under 80.
    results = [req("met", RO.MET, weight=799.0), req("unmet", RO.NOT_MET, weight=201.0)]
    result = score_audit(policy=ONE_SECTION, results=results)

    assert result.compliance_percent < 80.0
    assert result.proposed_decision != Decision.APPROVED
    assert result.proposed_decision == Decision.APPROVED_WITH_CONDITIONS


def test_the_displayed_value_is_rounded_but_the_stored_one_is_not():
    results = [req("a", RO.MET), req("b", RO.MET), req("c", RO.NOT_MET)]
    result = score_audit(policy=ONE_SECTION, results=results)
    assert result.compliance_percent == pytest.approx(66.666666, abs=1e-4)
    assert result.display_compliance == 66.7


def test_floating_point_noise_does_not_fail_an_exact_threshold():
    """The other direction: a score that IS exactly 80 must not fail because
    of binary floating-point representation."""
    results = [req(f"m{i}", RO.MET) for i in range(8)] + [req(f"u{i}", RO.NOT_MET) for i in range(2)]
    result = score_audit(policy=ONE_SECTION, results=results)
    assert result.compliance_percent == pytest.approx(80.0)
    assert result.proposed_decision == Decision.APPROVED


# ═══════════════════════════════════════════════════════════════════════════
# Partial credit and thresholds
# ═══════════════════════════════════════════════════════════════════════════

def test_partial_earns_half_credit():
    score = score_section(SectionPolicy("facilities", 100.0), [
        req("r1", RO.MET), req("r2", RO.PARTIAL),
    ])
    assert score.compliance_percent == pytest.approx(75.0)


def test_the_three_decision_bands():
    def score_for(met, total):
        results = [req(f"m{i}", RO.MET) for i in range(met)]
        results += [req(f"u{i}", RO.NOT_MET) for i in range(total - met)]
        return score_audit(policy=ONE_SECTION, results=results).proposed_decision

    assert score_for(9, 10) == Decision.APPROVED                   # 90%
    assert score_for(7, 10) == Decision.APPROVED_WITH_CONDITIONS   # 70%
    assert score_for(5, 10) == Decision.NOT_APPROVED               # 50%


# ═══════════════════════════════════════════════════════════════════════════
# Overrides and versioning
# ═══════════════════════════════════════════════════════════════════════════

def test_an_override_stores_the_machine_result_rather_than_erasing_it():
    """§16.11. Naive version overwrites the proposal, destroying the record of
    the disagreement — which is the most interesting artefact in the file."""
    machine = score_audit(policy=ONE_SECTION, results=[
        req("r1", RO.MET), req("critical-1", RO.NOT_MET, critical=True),
    ])
    assert machine.proposed_decision == Decision.NOT_APPROVED

    record = apply_override(machine, Override(
        decision=Decision.APPROVED_WITH_CONDITIONS,
        reason="Site provided an accepted regulatory exemption after the audit closed.",
        overridden_by="approver-1"))

    assert record["final_decision"] == "APPROVED_WITH_CONDITIONS"
    assert record["overridden"] is True
    # The machine's verdict AND its reasoning survive intact.
    assert record["machine_result"]["proposed_decision"] == "NOT_APPROVED"
    assert any(b["code"] == "CRITICAL_REQUIREMENT_FAILED"
               for b in record["machine_result"]["blockers"])


def test_an_override_requires_a_reason():
    machine = score_audit(policy=ONE_SECTION, results=[req("r1", RO.MET)])
    with pytest.raises(ScoringError) as exc:
        apply_override(machine, Override(Decision.NOT_APPROVED, "   ", "approver-1"))
    assert exc.value.code == "OVERRIDE_REASON_REQUIRED"


def test_agreeing_with_the_machine_is_not_recorded_as_an_override():
    machine = score_audit(policy=ONE_SECTION, results=[req("r1", RO.MET)])
    record = apply_override(machine, Override(
        Decision.APPROVED, "Agreed with the assessment.", "approver-1"))
    assert record["overridden"] is False


def test_every_result_carries_the_algorithm_version():
    """§16.11's "algorithm change retroactively recomputes history" case: a
    stored result names the version that produced it, so a later change can
    never silently rewrite what a past audit meant."""
    result = score_audit(policy=ONE_SECTION, results=[req("r1", RO.MET)])
    assert result.algorithm_version == ALGORITHM_VERSION
    assert result.to_dict()["algorithm_version"] == ALGORITHM_VERSION


# ═══════════════════════════════════════════════════════════════════════════
# Conditional approval must have real conditions
# ═══════════════════════════════════════════════════════════════════════════

def test_a_conditional_approval_needs_at_least_one_capa():
    """§16.11. "Approved with conditions" and no conditions is an unconditional
    approval wearing a hat."""
    with pytest.raises(ScoringError) as exc:
        conditional_approval_is_valid(Decision.APPROVED_WITH_CONDITIONS, [], {})
    assert exc.value.code == "CONDITIONS_REQUIRED"


def test_a_conditional_approvals_capas_need_an_owner_and_a_due_date():
    lookup = {
        "no-owner": {"owner_user_id": "", "due_date": "2027-01-01"},
        "no-date": {"owner_user_id": "u1", "due_date": None},
        "good": {"owner_user_id": "u1", "due_date": "2027-01-01"},
    }
    with pytest.raises(ScoringError) as owner:
        conditional_approval_is_valid(Decision.APPROVED_WITH_CONDITIONS, ["no-owner"], lookup)
    assert owner.value.code == "CAPA_OWNER_REQUIRED"

    with pytest.raises(ScoringError) as due:
        conditional_approval_is_valid(Decision.APPROVED_WITH_CONDITIONS, ["no-date"], lookup)
    assert due.value.code == "CAPA_DUE_DATE_REQUIRED"

    conditional_approval_is_valid(Decision.APPROVED_WITH_CONDITIONS, ["good"], lookup)


def test_an_unconditional_approval_needs_no_capas():
    conditional_approval_is_valid(Decision.APPROVED, [], {})


def test_a_conditional_approval_cannot_link_a_capa_from_another_audit():
    with pytest.raises(ScoringError) as exc:
        conditional_approval_is_valid(Decision.APPROVED_WITH_CONDITIONS, ["elsewhere"], {})
    assert exc.value.code == "UNKNOWN_CAPA"


# ═══════════════════════════════════════════════════════════════════════════
# Determinism
# ═══════════════════════════════════════════════════════════════════════════

def test_the_same_inputs_always_produce_the_same_result():
    """Spec §13.2 requires a deterministic engine: an audit re-scored from the
    same inputs must not drift."""
    results = [req("a", RO.MET), req("b", RO.PARTIAL), req("c", RO.NOT_APPLICABLE),
               req("d", RO.NOT_MET, confidence=EvidenceConfidence.LIMITED)]
    first = score_audit(policy=ONE_SECTION, results=results).to_dict()
    second = score_audit(policy=ONE_SECTION, results=list(reversed(results))).to_dict()
    assert first["compliance_percent"] == second["compliance_percent"]
    assert first["evidence_confidence_percent"] == second["evidence_confidence_percent"]
    assert first["proposed_decision"] == second["proposed_decision"]
