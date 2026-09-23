"""Applicability comparison operators and AND/OR combinators (AD-7).

Before this, ``ApplicabilityRule`` could only ask "is this patient's arm/
cohort/population IN this set" - AD-7's own motivating example, "subjects
aged >= 65 get an extra ECG", had nowhere to go. The extension is additive:
every existing IN/NOT_IN rule list defaults every entry's combinator to AND,
which is exactly the old, only behaviour (see test_uctsm_generic_dimensions.py
and the other 8 files the plan names, which exercise that this did not
change).
"""

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, ApplicabilityRule, ClaimEvidence, Event, Evidence, PatientContext,
    PatientEventStatus, ScheduleMetadata, ScheduleStatus, StudyDimension,
    UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.domain.schedule.validator import ScheduleValidator

BASELINE = date(2026, 1, 1)
HORIZON = date(2026, 12, 31)


def _schedule(applicability: list[ApplicabilityRule]) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=1, source_text="Extra ECG")
    event = Event(
        code="ECG_EXTRA", protocol_label="Extra ECG", display_name="Extra ECG",
        event_type="ASSESSMENT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"),
                             offset=TemporalAmount(value=7, unit="DAY")),
        applicability=applicability, evidence_refs=[evidence.id],
    )
    claims = [ClaimEvidence(
        evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
        claim_entity_id=event.id, claim_path=path,
    ) for claim_type, path in (
        ("EVENT_NAME", "display_name"), ("TIMING", "timing"),
        ("APPLICABILITY", "applicability"),
    )]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE", status="RESOLVED")],
        arms=[
            StudyDimension(code="SURGICAL", protocol_label="Surgical", display_name="Surgical arm"),
            StudyDimension(code="MEDICAL", protocol_label="Medical", display_name="Medical arm"),
        ],
        events=[event], evidence=[evidence], claim_evidence=claims,
    )


def context(schedule: UniversalSchedule, **changes) -> PatientContext:
    values = {
        "patient_id": uuid4(), "schedule_version_id": schedule.schedule_version_id,
        "anchors": {"BASELINE": BASELINE},
    }
    values.update(changes)
    return PatientContext(**values)


def status(schedule: UniversalSchedule, ctx: PatientContext) -> PatientEventStatus:
    return ScheduleEvaluator().evaluate(schedule, ctx, horizon=HORIZON).events[0].status


def test_a_gte_comparison_rule_gates_by_age():
    """AD-7's own example: subjects aged >= 65."""
    schedule = _schedule([ApplicabilityRule(dimension="AGE", operator="GTE", values=["65"])])

    older = status(schedule, context(schedule, state={"age": 70}))
    younger = status(schedule, context(schedule, state={"age": 40}))
    exact = status(schedule, context(schedule, state={"age": 65}))

    assert older == PatientEventStatus.RESOLVED
    assert younger == PatientEventStatus.NOT_APPLICABLE
    assert exact == PatientEventStatus.RESOLVED


def test_an_unstated_age_is_unknown_not_a_silent_pass_or_fail():
    schedule = _schedule([ApplicabilityRule(dimension="AGE", operator="GTE", values=["65"])])

    result = ScheduleEvaluator().evaluate(
        schedule, context(schedule, state={}), horizon=HORIZON).events[0]

    assert result.status == PatientEventStatus.WAITING_FOR_CONDITION


def test_two_rules_joined_by_or_pass_if_either_matches():
    """The surgical arm gets it regardless of age; everyone else needs 65+."""
    schedule = _schedule([
        ApplicabilityRule(dimension="ARM", operator="IN", values=["SURGICAL"], combinator="OR"),
        ApplicabilityRule(dimension="AGE", operator="GTE", values=["65"]),
    ])

    surgical_and_young = status(schedule, context(
        schedule, arm_code="SURGICAL", state={"age": 30}))
    medical_and_old = status(schedule, context(
        schedule, arm_code="MEDICAL", state={"age": 70}))
    medical_and_young = status(schedule, context(
        schedule, arm_code="MEDICAL", state={"age": 30}))

    assert surgical_and_young == PatientEventStatus.RESOLVED
    assert medical_and_old == PatientEventStatus.RESOLVED
    assert medical_and_young == PatientEventStatus.NOT_APPLICABLE


def test_a_comparison_operator_with_more_than_one_value_is_rejected():
    """EQ/NE/LT/LTE/GT/GTE compare against ONE threshold. A second value in
    the list would silently never be looked at - refuse it at construction,
    the same way a half-stated hierarchy is refused elsewhere in this model.
    """
    with pytest.raises(ValueError, match="exactly one comparison value"):
        ApplicabilityRule(dimension="AGE", operator="GTE", values=["65", "70"])


def test_age_is_a_known_dimension_not_an_unresolved_reference():
    """AGE has no fixed code list to check membership against - it must not
    be flagged as an unknown applicability dimension the way a genuinely
    unrecognised one is (test_unknown_generic_dimension_is_blocking).
    """
    schedule = _schedule([ApplicabilityRule(dimension="AGE", operator="GTE", values=["65"])])

    issues = ScheduleValidator().validate(schedule)
    assert not any(
        issue.issue_code == "UNRESOLVED_REFERENCE" and "dimension" in issue.message
        for issue in issues
    )
