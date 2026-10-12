"""P1-02: recorded occurrence history drives actual-relative rules.

Pure domain tests (no database): a ``PatientContext`` carrying
``occurrence_history`` is evaluated and each reference counts from exactly the
date its temporal basis names - or waits, with a reason and no date.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.evaluator import ContextValueSource, ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Dependency, DependencyMode, Event, Evidence, LogicalOccurrenceState,
    PatientContext, PatientEventStatus, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorAcceptance, AnchorReference, EventReference, EventValue, OccurrenceSelector,
    OffsetTiming, TemporalAmount, TemporalBasis,
)

BASELINE = date(2026, 10, 1)
HORIZON = date(2027, 6, 30)


def days(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit="DAY")


def build(basis: TemporalBasis, *, mode: DependencyMode, fallback: TemporalBasis | None = None,
          dose_type: str = "TREATMENT") -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=1, source_text="schedule")
    dose = Event(
        code="DOSE", protocol_label="Dose", display_name="Dose", event_type=dose_type,
        sequence_number=1, timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=days(9)),
        evidence_refs=[evidence.id])
    follow_up = Event(
        code="FOLLOW_UP", protocol_label="Follow-up", display_name="Follow-up", event_type="VISIT",
        sequence_number=2,
        timing=OffsetTiming(
            reference=EventReference(event_code="DOSE", temporal_basis=basis, fallback_basis=fallback),
            offset=days(14)),
        dependencies=[Dependency(source_event_code="DOSE", dependency_type="TEMPORAL")],
        dependency_mode=mode, evidence_refs=[evidence.id])
    imaging = Event(
        code="IMAGING", protocol_label="Imaging", display_name="Imaging", event_type="VISIT",
        sequence_number=3,
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=days(30)),
        evidence_refs=[evidence.id])
    claims = []
    for event in (dose, follow_up, imaging):
        paths = [("EVENT_NAME", "display_name"), ("TIMING", "timing")]
        if event.dependencies:
            paths += [("DEPENDENCY", "dependencies"), ("DEPENDENCY_MODE", "dependency_mode")]
        claims += [ClaimEvidence(
            evidence_id=evidence.id, claim_type=kind, claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path=path, confidence=0.9) for kind, path in paths]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="P", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE",
                        status="RESOLVED")],
        events=[dose, follow_up, imaging], evidence=[evidence], claim_evidence=claims)


def evaluate(schedule, *, baseline=BASELINE, history=(), actual_visits=None):
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"BASELINE": baseline}, occurrence_history=list(history),
        actual_event_values=actual_visits or {})
    result = ScheduleEvaluator().evaluate(schedule, context, horizon=HORIZON)
    return {item.event_code: item for item in result.events}


def state(code="DOSE", index=0, **fields) -> LogicalOccurrenceState:
    return LogicalOccurrenceState(event_code=code, occurrence_index=index, **fields)


# --------------------------------------------------------------------- bases
def test_actual_visit_counts_from_the_recorded_visit_and_baseline_work_stays_put():
    schedule = build(TemporalBasis.ACTUAL_VISIT, mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)
    history = [state(original_planned_date=date(2026, 10, 10),
                     latest_planned_date=date(2026, 10, 10), actual_visit_date=date(2026, 10, 13))]
    result = evaluate(schedule, history=history)
    assert result["FOLLOW_UP"].timing.nominal_start == date(2026, 10, 27)
    assert result["IMAGING"].timing.nominal_start == date(2026, 10, 31)
    used = result["FOLLOW_UP"].explanation["temporal_basis"][0]
    assert (used["temporal_basis"], used["value"], used["source"]) == (
        "ACTUAL_VISIT", "2026-10-13", "ACTUAL_VISIT")


def test_actual_visit_waits_without_a_date_until_the_visit_is_recorded():
    schedule = build(TemporalBasis.ACTUAL_VISIT, mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)
    result = evaluate(schedule, history=[state(latest_planned_date=date(2026, 10, 10))])
    waiting = result["FOLLOW_UP"]
    assert waiting.status == PatientEventStatus.BLOCKED
    assert waiting.timing is None
    assert waiting.dependency_result["waiting_for_actual"] == ["DOSE"]


def test_current_planned_ignores_a_recorded_actual():
    schedule = build(TemporalBasis.CURRENT_PLANNED, mode=DependencyMode.NOMINAL)
    history = [state(actual_visit_date=date(2026, 10, 13))]
    result = evaluate(schedule, history=history)
    assert result["FOLLOW_UP"].timing.nominal_start == date(2026, 10, 24)  # 10 Oct + 14


def test_original_planned_does_not_follow_a_later_planned_date_move():
    schedule = build(TemporalBasis.ORIGINAL_PLANNED, mode=DependencyMode.NOMINAL)
    # baseline moved by 5 days, so the dose is now planned 15 Oct; originally 10 Oct
    history = [state(original_planned_date=date(2026, 10, 10),
                     latest_planned_date=date(2026, 10, 10))]
    result = evaluate(schedule, baseline=date(2026, 10, 6), history=history)
    assert result["DOSE"].timing.nominal_start == date(2026, 10, 15)
    assert result["FOLLOW_UP"].timing.nominal_start == date(2026, 10, 24)  # from the ORIGINAL


def test_original_planned_is_the_current_date_when_this_is_the_first_evaluation():
    schedule = build(TemporalBasis.ORIGINAL_PLANNED, mode=DependencyMode.NOMINAL)
    result = evaluate(schedule, history=[])
    assert result["FOLLOW_UP"].timing.nominal_start == date(2026, 10, 24)


def test_actual_treatment_waits_when_the_visit_happened_but_treatment_was_withheld():
    schedule = build(TemporalBasis.ACTUAL_TREATMENT, mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)
    attended_only = [state(actual_visit_date=date(2026, 10, 13))]
    waiting = evaluate(schedule, history=attended_only)["FOLLOW_UP"]
    assert waiting.timing is None and waiting.status == PatientEventStatus.BLOCKED

    treated = [state(actual_visit_date=date(2026, 10, 13), actual_treatment_date=date(2026, 10, 14))]
    assert evaluate(schedule, history=treated)["FOLLOW_UP"].timing.nominal_start == date(2026, 10, 28)


def test_actual_visit_based_rules_still_compute_when_treatment_was_withheld():
    schedule = build(TemporalBasis.ACTUAL_VISIT, mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)
    attended_only = [state(actual_visit_date=date(2026, 10, 13))]
    assert evaluate(schedule, history=attended_only)["FOLLOW_UP"].timing.nominal_start \
        == date(2026, 10, 27)


def test_actual_or_planned_says_it_fell_back_and_recalculates_when_the_actual_arrives():
    schedule = build(TemporalBasis.ACTUAL_OR_PLANNED, mode=DependencyMode.NOMINAL,
                     fallback=TemporalBasis.CURRENT_PLANNED)
    fell_back = evaluate(schedule, history=[])["FOLLOW_UP"]
    assert fell_back.timing.nominal_start == date(2026, 10, 24)           # 10 Oct planned + 14
    used = fell_back.explanation["temporal_basis"][0]
    assert used["fallback_used"] is True
    assert used["source"] == "FALLBACK_CURRENT_PLANNED" and used["value"] == "2026-10-10"

    arrived = evaluate(schedule, history=[state(actual_visit_date=date(2026, 10, 13))])["FOLLOW_UP"]
    assert arrived.timing.nominal_start == date(2026, 10, 27)
    assert arrived.explanation["temporal_basis"][0]["fallback_used"] is False


def test_actual_or_planned_can_fall_back_to_the_original_plan():
    schedule = build(TemporalBasis.ACTUAL_OR_PLANNED, mode=DependencyMode.NOMINAL,
                     fallback=TemporalBasis.ORIGINAL_PLANNED)
    history = [state(original_planned_date=date(2026, 10, 3))]
    result = evaluate(schedule, baseline=date(2026, 10, 6), history=history)["FOLLOW_UP"]
    assert result.timing.nominal_start == date(2026, 10, 17)             # original 3 Oct + 14
    assert result.explanation["temporal_basis"][0]["source"] == "FALLBACK_ORIGINAL_PLANNED"


def test_a_context_without_history_still_reads_the_legacy_actual_list():
    schedule = build(TemporalBasis.ACTUAL_VISIT, mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)
    result = evaluate(schedule, actual_visits={"DOSE": [date(2026, 10, 13)]})
    assert result["FOLLOW_UP"].timing.nominal_start == date(2026, 10, 27)


# ------------------------------------------------------- index alignment (D5)
def source_for(history, *, legacy=None) -> ContextValueSource:
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(), occurrence_history=list(history),
        actual_event_values=legacy or {})
    return ContextValueSource(context, event_code="OTHER", occurrence_index=0)


def number(n: int) -> EventValue:
    return EventValue(event_code="DOSE", occurrence=OccurrenceSelector(type="NUMBER", number=n),
                      accepts=AnchorAcceptance.ACTUAL)


def test_a_missed_first_occurrence_does_not_shift_the_second_ones_actual():
    history = [state(index=0), state(index=1, actual_visit_date=date(2026, 10, 20))]
    source = source_for(history)
    second = source.event(number(2))
    assert second.value == date(2026, 10, 20)
    assert source.event(number(1)).value is None            # occurrence 1 has no actual
    assert source.event(number(1)).missing


def test_without_history_actuals_remain_positional_exactly_as_before():
    source = source_for([], legacy={"DOSE": [date(2026, 10, 20)]})
    assert source.event(number(1)).value == date(2026, 10, 20)
