"""The deterministic fact compiler (app/extraction/compiler.py).

Each test states one protocol sentence as a cited fact and checks the exact
canonical rule it compiles to (docs/schedule-engine-case-handling.md, Part 4),
or that it is refused as UNRESOLVED rather than guessed. The last tests take a
hand-written fact set all the way through import, review, approval and
enrolment, and check real patient dates.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.condition import MembershipCondition
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    InterpretationStatus, PatientConditionStatus, PatientContext, ScheduleStatus,
)
from app.domain.schedule.timing import (
    AbsoluteTiming, AnchorReference, ApproximateTiming, CycleDayTiming, CycleSelector,
    EventReference, NoLaterThanTiming,
    OffsetTiming, ProtocolDayTiming, ProtocolDefinedTiming, RangeTiming,
    TriggeredTiming, UnresolvedTiming, WithinTiming,
)
from app.extraction.compiler import compile_document, compile_schedule
from app.extraction.facts import DocumentFacts
from app.services.native_import import import_native_schedules
from app.services.operational_bridge import OperationalBridgeService
from app.services.schedule_service import ScheduleReviewService


def cite(page: int, quote: str) -> list[dict]:
    return [{"page": page, "section": "7.1", "quote": quote}]


def fact(what: str, kind: str = "visit", *, page: int = 10, quote: str | None = None, **fields) -> dict:
    return {"what": what, "kind": kind, "evidence": cite(page, quote or what), **fields}


REGISTRATION = fact("Registration", "anchor", anchor={"type": "RECORDED"},
                    quote="Patients are registered before any study treatment")
END_OF_THERAPY = fact("End of therapy", "milestone", anchor={"type": "RECORDED"},
                      quote="completion of all protocol therapy")
DAY_1 = {"day_1_anchor": "Registration", "counting_convention": "CLINICAL_DAY",
         "evidence": cite(5, "Day 1 is the day of registration")}


def compile_facts(*facts: dict, day_numbering: dict | None = DAY_1, **document):
    doc = DocumentFacts.model_validate({
        "day_numbering": day_numbering or {}, "facts": [REGISTRATION, END_OF_THERAPY, *facts],
        **document,
    })
    result = compile_schedule(doc, name="Primary")
    assert result.schedule is not None
    return result


def event(result, code: str):
    return next(item for item in result.schedule.events if item.code == code)


def codes(result, *, blocking_only: bool = True) -> set[str]:
    return {item.issue_code for item in result.issues if item.blocking or not blocking_only}


def issues_for(result, entity_id) -> set[str]:
    return {item.issue_code for item in result.issues if item.entity_id == entity_id and item.blocking}


# --- single-visit timing (spec A) ------------------------------------------------

def test_pre_treatment_labs_within_28_days_before_registration_are_within_before_that_anchor():
    """A8 / Change 8 compile rule: never re-pointed at first dose."""
    result = compile_facts(fact(
        "Screening labs", timing={"pattern": "WITHIN_BEFORE", "from": "Registration", "duration": "28 DAY"},
        quote="Labs within 28 days before registration"))

    timing = event(result, "SCREENING_LABS").timing
    assert isinstance(timing, WithinTiming)
    assert timing.direction == "BEFORE"
    assert timing.reference == AnchorReference(code="REGISTRATION")
    assert (timing.duration.value, timing.duration.unit) == (28, "DAY")
    assert not issues_for(result, event(result, "SCREENING_LABS").id)


def test_pet_scan_12_to_16_weeks_after_end_of_therapy_is_a_range_from_the_milestone_anchor():
    result = compile_facts(fact(
        "PET scan", event_type="IMAGING",
        timing={"pattern": "RANGE", "from": "End of therapy", "start": "12 WEEK", "end": "16 WEEK"},
        quote="PET scan 12-16 weeks after completion of therapy"))

    pet = event(result, "PET_SCAN")
    assert isinstance(pet.timing, RangeTiming)
    assert pet.timing.reference == AnchorReference(code="END_OF_THERAPY")
    assert (pet.timing.start.value, pet.timing.end.value, pet.timing.end.unit) == (12, 16, "WEEK")
    assert pet.event_type == "IMAGING"
    # The phase end is its own anchor, recorded by the site - not guessed from a visit.
    anchor = next(item for item in result.schedule.anchors if item.code == "END_OF_THERAPY")
    assert anchor.anchor_type == "MILESTONE" and anchor.source_event_code is None


def test_offset_with_a_stated_window_keeps_the_window_as_its_own_rule():
    result = compile_facts(fact(
        "Visit 3", timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"},
        window={"before": "3 DAY", "after": "3 DAY"},
        quote="Visit 3: 4 weeks after registration +/- 3 days"))

    visit = event(result, "VISIT_3")
    assert isinstance(visit.timing, OffsetTiming)
    assert (visit.window.before.value, visit.window.after.value, visit.window.after.unit) == (3, 3, "DAY")
    assert not issues_for(result, visit.id)


def test_a_stated_late_only_window_is_kept_one_sided():
    """B3: 'may be up to 3 days late but not early' states both sides (-0/+3)."""
    result = compile_facts(fact(
        "Day 15 visit", timing={"pattern": "PROTOCOL_DAY", "day": 15},
        window={"before": "0 DAY", "after": "3 DAY"}))

    window = event(result, "DAY_15_VISIT").window
    assert (window.before.value, window.after.value) == (0, 3)


def test_a_window_on_a_range_is_not_applied_because_it_has_no_centre():
    result = compile_facts(fact(
        "PET scan", timing={"pattern": "RANGE", "from": "End of therapy", "start": "12 WEEK", "end": "16 WEEK"},
        window={"before": "7 DAY", "after": "7 DAY"}))

    pet = event(result, "PET_SCAN")
    assert pet.window is None
    assert pet.metadata["window_as_stated"]["reason"].startswith("a window cannot be applied")


def test_missing_window_stays_empty_and_is_never_defaulted():
    """B4: no window stated -> no window, no issue pretending one exists."""
    result = compile_facts(fact(
        "Visit 4", timing={"pattern": "OFFSET", "from": "Registration", "offset": "8 WEEK"}))

    visit = event(result, "VISIT_4")
    assert isinstance(visit.timing, OffsetTiming)
    assert visit.window is None
    assert "window_as_stated" not in visit.metadata


def test_a_one_sided_window_is_kept_as_stated_but_not_turned_into_a_guessed_window():
    result = compile_facts(fact(
        "Day 15 visit", timing={"pattern": "PROTOCOL_DAY", "day": 15}, window={"after": "3 DAY"},
        quote="Day 15 visit, may be up to 3 days late"))

    visit = event(result, "DAY_15_VISIT")
    assert isinstance(visit.timing, ProtocolDayTiming)
    assert visit.metadata["window_as_stated"]["after"] == {"value": 3, "unit": "DAY"}
    assert "WINDOW_NOT_APPLIED" in codes(result, blocking_only=False)


def test_protocol_day_under_clinical_day_counting_uses_the_stated_day_1_anchor():
    result = compile_facts(fact("Day 8", timing={"pattern": "PROTOCOL_DAY", "day": 8}))

    timing = event(result, "DAY_8").timing
    assert timing == ProtocolDayTiming(reference=AnchorReference(code="REGISTRATION"), day=8)
    assert result.schedule.schedule_metadata.day_numbering.day_1_anchor.code == "REGISTRATION"


def test_day_0_under_clinical_day_counting_is_refused_not_shifted():
    result = compile_facts(fact("Day 0 visit", timing={"pattern": "PROTOCOL_DAY", "day": 0}))

    assert isinstance(event(result, "DAY_0_VISIT").timing, UnresolvedTiming)
    assert "AMBIGUOUS_TIMING" in codes(result)


def test_elapsed_day_counting_turns_day_n_into_an_offset():
    """A3: 'Day 0 is the day of first dose; Day 7 follow-up'."""
    result = compile_facts(
        fact("Day 7 follow-up", timing={"pattern": "PROTOCOL_DAY", "day": 7}),
        day_numbering={**DAY_1, "counting_convention": "ELAPSED_DURATION"})

    timing = event(result, "DAY_7_FOLLOW_UP").timing
    assert timing == OffsetTiming(reference=AnchorReference(code="REGISTRATION"),
                                  offset={"value": 7, "unit": "DAY"})


def test_no_day_1_statement_leaves_day_numbering_unset_and_the_validator_blocks():
    result = compile_facts(fact("Day 8", timing={"pattern": "PROTOCOL_DAY", "day": 8}),
                           day_numbering=None)

    assert result.schedule.schedule_metadata.day_numbering is None
    assert "UNRESOLVED_SCHEDULE_ORIGIN" in codes(result)
    # Without a Day 1 anchor the Day 8 visit itself cannot be dated either.
    assert isinstance(event(result, "DAY_8").timing, UnresolvedTiming)


def test_a_stated_anchor_but_unstated_counting_convention_still_blocks():
    result = compile_facts(fact("Visit", timing={"pattern": "OFFSET", "from": "Registration", "offset": "1 WEEK"}),
                           day_numbering={"day_1_anchor": "Registration"})

    assert result.schedule.schedule_metadata.day_numbering is None
    assert "UNRESOLVED_SCHEDULE_ORIGIN" in codes(result)


def test_absolute_no_later_than_and_approximate_map_to_their_rules():
    result = compile_facts(
        fact("Close-out", timing={"pattern": "ABSOLUTE", "value": "2027-01-15"}),
        fact("Safety follow-up", timing={"pattern": "NO_LATER_THAN", "from": "Last dose", "duration": "30 DAY"}),
        fact("Late QoL", timing={"pattern": "APPROXIMATE", "from": "End of therapy", "offset": "3 MONTH"}))

    assert event(result, "CLOSE_OUT").timing == AbsoluteTiming(value=date(2027, 1, 15))
    assert isinstance(event(result, "SAFETY_FOLLOW_UP").timing, NoLaterThanTiming)
    late = event(result, "LATE_QOL")
    assert isinstance(late.timing, ApproximateTiming)
    # A11: approximate wording is refused as a date, so approval is blocked.
    assert "UNSUPPORTED_TIMING" in issues_for(result, late.id)


def test_an_anchor_named_but_never_defined_becomes_a_recorded_anchor_with_a_warning():
    result = compile_facts(fact(
        "Safety follow-up", timing={"pattern": "NO_LATER_THAN", "from": "Last dose", "duration": "30 DAY"}))

    anchor = next(item for item in result.schedule.anchors if item.code == "LAST_DOSE")
    assert anchor.source_event_code is None and anchor.derivation_rule["source"] == "RECORDED"
    assert "ANCHOR_SOURCE_NOT_STATED" in codes(result, blocking_only=False)


def test_an_unmappable_statement_is_unresolved_with_its_source():
    result = compile_facts(fact(
        "Bone scan", timing={"pattern": "UNRESOLVED", "reason": "timing given only as 'pre-treatment'"},
        page=22, quote="PET/bone scan pre-treatment"))

    scan = event(result, "BONE_SCAN")
    assert isinstance(scan.timing, UnresolvedTiming)
    assert scan.timing.source_reference["page"] == 22
    assert "AMBIGUOUS_TIMING" in issues_for(result, scan.id)


def test_a_visit_counted_from_another_visit_depends_on_it_and_needs_a_stated_mode():
    result = compile_facts(
        fact("Visit 2", timing={"pattern": "OFFSET", "from": "Registration", "offset": "14 DAY"}),
        fact("Visit 3", timing={"pattern": "OFFSET", "from": "Visit 2", "offset": "14 DAY"}))

    visit = event(result, "VISIT_3")
    assert visit.timing.reference == EventReference(event_code="VISIT_2")
    assert [item.source_event_code for item in visit.dependencies] == ["VISIT_2"]
    assert visit.dependency_mode == "UNCLEAR"
    assert "UNCLEAR_DEPENDENCY_MODE" in issues_for(result, visit.id)


def test_on_demand_visit_is_available_not_dated():
    result = compile_facts(fact("Unscheduled visit", timing={"pattern": "ON_DEMAND"}))

    visit = event(result, "UNSCHEDULED_VISIT")
    assert visit.activation == "ON_DEMAND"
    assert isinstance(visit.timing, ProtocolDefinedTiming)
    assert not issues_for(result, visit.id)


# --- conflicts (spec O2) -------------------------------------------------------------

def test_conflicting_qol_statements_are_recorded_as_conflicting_and_block_approval():
    result = compile_facts(
        fact("QoL 3 months", timing={"pattern": "OFFSET", "from": "End of therapy", "offset": "3 MONTH"},
             conflict_group="qol", page=50, quote="QoL at 3 months post completion of treatment"),
        fact("QoL 3 months", timing={"pattern": "UNRESOLVED", "reason": "at the first follow-up visit"},
             conflict_group="qol", page=33, quote="QoL at the first follow-up visit"))

    qol = event(result, "QOL_3_MONTHS")
    assert qol.interpretation_status == InterpretationStatus.CONFLICTING
    assert isinstance(qol.timing, UnresolvedTiming)
    alternatives = qol.timing.source_reference["alternatives"]
    assert {item["citations"][0]["page"] for item in alternatives} == {50, 33}
    blocking = issues_for(result, qol.id)
    assert {"CONFLICTING_SOURCE", "CONFLICTING_EVIDENCE"} <= blocking
    conflict = next(item for item in result.issues if item.issue_code == "CONFLICTING_SOURCE")
    assert conflict.details["origin"] == "EXTRACTION"


def test_two_unflagged_readings_of_one_visit_that_disagree_are_also_conflicting():
    result = compile_facts(
        fact("Echo", timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"}, page=12),
        fact("Echo", timing={"pattern": "OFFSET", "from": "Registration", "offset": "6 WEEK"}, page=40))

    assert event(result, "ECHO").interpretation_status == InterpretationStatus.CONFLICTING


def test_two_readings_that_agree_merge_into_one_visit_with_both_citations():
    result = compile_facts(
        fact("Echo", timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"}, page=12),
        fact("Echo", timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"}, page=40))

    echo = event(result, "ECHO")
    assert echo.interpretation_status == InterpretationStatus.EXTRACTED
    pages = {item.page_number for item in result.schedule.evidence if item.id in echo.evidence_refs}
    assert pages == {12, 40}


# --- repeats (spec E) --------------------------------------------------------------

def test_counted_repeat_and_open_ended_repeat():
    result = compile_facts(
        fact("Treatment visit", timing={"pattern": "REPEAT", "from": "Registration"},
             repeat={"interval": "3 WEEK", "includes_start": True, "end": {"type": "COUNT", "count": 6}}),
        fact("Imaging", timing={"pattern": "REPEAT", "from": "Registration"},
             repeat={"interval": "12 WEEK", "includes_start": False, "end": {"type": "OPEN_ENDED"}}))

    treatment = event(result, "TREATMENT_VISIT").recurrence
    assert (treatment.interval.value, treatment.termination.type, treatment.termination.count) == (3, "COUNT", 6)
    assert treatment.include_start is True
    imaging = event(result, "IMAGING").recurrence
    assert imaging.termination.type == "HORIZON" and imaging.include_start is False


def test_a_repeat_whose_first_occurrence_is_not_stated_is_unresolved():
    result = compile_facts(fact(
        "Imaging", timing={"pattern": "REPEAT", "from": "Registration"},
        repeat={"interval": "12 WEEK", "end": {"type": "OPEN_ENDED"}}))

    assert isinstance(event(result, "IMAGING").timing, UnresolvedTiming)


def test_repeat_for_a_duration_needs_a_stated_inclusive_boundary():
    """E4: 'every 3 months for 2 years' - whether the 24-month visit counts is never assumed."""
    unstated = compile_facts(fact(
        "Follow-up", timing={"pattern": "REPEAT", "from": "End of therapy"},
        repeat={"interval": "3 MONTH", "includes_start": False,
                "end": {"type": "FOR_DURATION", "duration": "24 MONTH"}}))
    stated = compile_facts(fact(
        "Follow-up", timing={"pattern": "REPEAT", "from": "End of therapy"},
        repeat={"interval": "3 MONTH", "includes_start": False,
                "end": {"type": "FOR_DURATION", "duration": "24 MONTH", "inclusive": True}}))

    assert isinstance(event(unstated, "FOLLOW_UP").timing, UnresolvedTiming)
    termination = event(stated, "FOLLOW_UP").recurrence.termination
    assert (termination.type, termination.end_reference.code, termination.inclusive) == (
        "RELATIVE", "END_OF_THERAPY", True)


def test_repeat_until_a_condition_is_stopped_by_that_condition():
    """E3: 'every 12 weeks until progression'."""
    result = compile_facts(fact(
        "Tumour assessment", timing={"pattern": "REPEAT", "from": "Registration"},
        dependency_mode="NOMINAL",
        repeat={"interval": "12 WEEK", "includes_start": False,
                "end": {"type": "UNTIL_CONDITION", "condition": "Progression"}}))

    assessment = event(result, "TUMOUR_ASSESSMENT")
    assert assessment.recurrence.termination.type == "HORIZON"
    block = next(item for item in result.schedule.repeat_blocks if "TUMOUR_ASSESSMENT" in item.event_codes)
    progression = next(item for item in result.schedule.conditional_definitions if item.code == "PROGRESSION")
    assert [(item.action_type, item.target_code) for item in progression.actions] == [("STOP_BLOCK", block.code)]
    assert "UNCLEAR_DEPENDENCY_MODE" not in codes(result)


E9_SEGMENTS = [
    {"interval": "3 MONTH", "start": "0 MONTH", "end": "24 MONTH", "end_inclusive": True,
     "includes_start": False},
    {"interval": "6 MONTH", "start": "24 MONTH", "end": "60 MONTH", "end_inclusive": True},
    {"interval": "12 MONTH", "start": "60 MONTH"},
]
E9_QUOTE = "every 3 months for 2 years, then every 6 months to year 5, then yearly until end of study"


def e9_fact(segments=E9_SEGMENTS, end: dict | None = {"type": "OPEN_ENDED"}, **extra) -> dict:
    return fact("Follow-up", timing={"pattern": "PHASED_REPEAT", "from": "End of therapy",
                                     "segments": segments},
                repeat={"interval": "12 MONTH", "end": end} if end else None, quote=E9_QUOTE, **extra)


def test_phased_follow_up_compiles_to_one_visit_with_a_segmented_plan():
    """E9 / Change 5: one follow-up plan, not three chained visits."""
    result = compile_facts(e9_fact())

    follow_up = event(result, "FOLLOW_UP")
    assert [item.code for item in result.schedule.events] == ["FOLLOW_UP"]
    assert follow_up.recurrence is None
    plan = follow_up.recurrence_plan
    assert plan.start_reference == AnchorReference(code="END_OF_THERAPY")
    assert plan.include_start is False and plan.start_offset is None
    assert [(item.interval.value, item.interval.unit) for item in plan.segments] == [
        (3, "MONTH"), (6, "MONTH"), (12, "MONTH")]
    assert [(item.end_offset.value if item.end_offset else None, item.end_inclusive)
            for item in plan.segments] == [(24, True), (60, True), (None, None)]
    assert plan.termination.type == "HORIZON"
    assert not issues_for(result, follow_up.id)
    claimed = {(claim.claim_entity_id, claim.claim_type) for claim in result.schedule.claim_evidence}
    assert (follow_up.id, "RECURRENCE") in claimed


def test_phased_boundary_ownership_stated_by_the_next_segment_is_used():
    """'...then every 6 months starting at year 2' says the 24-month visit opens segment 2."""
    segments = [
        {"interval": "3 MONTH", "end": "24 MONTH", "includes_start": False},
        {"interval": "6 MONTH", "start": "2 YEAR", "end": "60 MONTH", "end_inclusive": True,
         "includes_start": True},
        {"interval": "12 MONTH"},
    ]
    plan = event(compile_facts(e9_fact(segments)), "FOLLOW_UP").recurrence_plan

    assert plan.segments[0].end_inclusive is False


def test_phased_segment_without_a_stated_boundary_is_left_unset_and_blocks():
    """E4: the boundary owner is never assumed; the validator blocks the plan."""
    result = compile_facts(e9_fact([
        {"interval": "3 MONTH", "start": "0 MONTH", "end": "24 MONTH"},
        {"interval": "6 MONTH", "start": "24 MONTH"},
    ]))

    follow_up = event(result, "FOLLOW_UP")
    assert follow_up.recurrence_plan.include_start is None
    assert follow_up.recurrence_plan.segments[0].end_inclusive is None
    assert "UNRESOLVED_RECURRENCE_BOUNDARY" in issues_for(result, follow_up.id)


def test_phased_plan_with_no_stated_end_is_not_defaulted_to_open_ended():
    result = compile_facts(e9_fact(end=None))

    follow_up = event(result, "FOLLOW_UP")
    assert follow_up.recurrence_plan.termination is None
    assert "PHASED_SEGMENT_UNBOUNDED" in issues_for(result, follow_up.id)


def test_phased_segments_that_disagree_about_the_boundary_are_unresolved():
    result = compile_facts(e9_fact([
        {"interval": "3 MONTH", "end": "24 MONTH", "end_inclusive": True, "includes_start": False},
        {"interval": "6 MONTH", "includes_start": True},
    ]))

    assert isinstance(event(result, "FOLLOW_UP").timing, UnresolvedTiming)


# --- cycles (spec F) -----------------------------------------------------------------

C1D1 = date(2026, 10, 5)
TREATMENT_CYCLES = {"name": "Treatment", "evidence": cite(9, "Treatment is given in 21-day cycles")}


def cycle_day(day: int, *, block: str | None = "Treatment", count: int | None = 6,
              repeat: dict | None = None, **extra) -> dict:
    if repeat is None and count:
        repeat = {"interval": "21 DAY", "end": {"type": "COUNT", "count": count}}
    return fact(f"Cycle day {day}", timing={
        "pattern": "CYCLE_DAY", "day": day,
        "cycle": {"start_anchor": "Cycle 1 Day 1", "length": "21 DAY"}},
        repeat=repeat, block=block, quote=f"Day {day} of each 21-day cycle", **extra)


def cycle_block(mode: str | None) -> list[dict]:
    return [{**TREATMENT_CYCLES, "dependency_mode": mode}]


def cycle_dates(result, *given: date, code: str = "CYCLE_DAY_1") -> list:
    schedule = result.schedule.model_copy(deep=True)
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"REGISTRATION": C1D1, "CYCLE_1_DAY_1": C1D1},
        actual_event_values={"CYCLE_DAY_1": list(given)} if given else {})
    evaluated = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2027, 6, 1))
    return [item for item in evaluated.events if item.event_code == code]


def test_every_cycle_day_compiles_to_a_cycle_plan_and_cycle_day_timing():
    """F4: 'Days 1 and 8 of each 21-day cycle' - one plan, dated per cycle."""
    result = compile_facts(cycle_day(1), cycle_day(8), blocks=cycle_block("NOMINAL"))

    block = next(item for item in result.schedule.repeat_blocks if item.code == "TREATMENT")
    plan = block.cycle_plan
    assert (plan.code, plan.start_reference.code, plan.length.value, plan.length.unit) == (
        "CYCLE", "CYCLE_1_DAY_1", 21, "DAY")
    assert (plan.dependency_mode, block.dependency_mode) == ("NOMINAL", "NOMINAL")
    assert (plan.termination.type, plan.termination.count) == ("COUNT", 6)
    assert plan.start_event_code == "CYCLE_DAY_1"
    day_8 = event(result, "CYCLE_DAY_8")
    assert day_8.timing == CycleDayTiming(cycle_reference="CYCLE", cycle=CycleSelector(type="CURRENT"), day=8)
    assert day_8.recurrence is None
    assert set(block.event_codes) == {"CYCLE_DAY_1", "CYCLE_DAY_8"}
    claimed = {(claim.claim_entity_id, claim.claim_type) for claim in result.schedule.claim_evidence}
    assert (block.id, "CYCLE_PLAN") in claimed
    assert not codes(result)


def test_f1_nominal_cycles_keep_the_grid_when_cycle_2_is_late():
    """F1: C1D1 05-Oct-2026, 21-day cycles: C2 26-Oct, C3 16-Nov; C2 given 02-Nov moves nothing."""
    result = compile_facts(cycle_day(1), blocks=cycle_block("NOMINAL"))
    assert not codes(result)

    dated = [item.timing.nominal_start for item in cycle_dates(result, C1D1, date(2026, 11, 2))]
    assert dated[:4] == [date(2026, 10, 5), date(2026, 10, 26), date(2026, 11, 16), date(2026, 12, 7)]


def test_f2_actual_previous_cycle_start_moves_the_next_cycle():
    """F2: 'Each cycle starts 21 days after Day 1 of the previous cycle.' C2 given
    02-Nov -> C3 23-Nov-2026; C4 waits for C3's actual start."""
    result = compile_facts(cycle_day(1), cycle_day(8),
                           blocks=cycle_block("ACTUAL_PREVIOUS_CYCLE_START"))
    block = next(item for item in result.schedule.repeat_blocks if item.code == "TREATMENT")
    assert (block.cycle_plan.dependency_mode, block.dependency_mode) == (
        "ACTUAL_PREVIOUS_CYCLE_START", "ACTUAL_PREVIOUS_EVENT")
    # The engine now dates this; the old UNSUPPORTED_TIMING workaround is gone.
    assert "UNSUPPORTED_TIMING" not in codes(result, blocking_only=False)
    assert not codes(result)

    items = cycle_dates(result, C1D1, date(2026, 11, 2))
    assert [item.timing.nominal_start for item in items[:3]] == [
        date(2026, 10, 5), date(2026, 11, 2), date(2026, 11, 23)]
    assert items[3].timing is None and items[3].status == "BLOCKED"
    day_8 = cycle_dates(result, C1D1, date(2026, 11, 2), code="CYCLE_DAY_8")
    assert [item.timing.nominal_start for item in day_8[:3]] == [
        date(2026, 10, 12), date(2026, 11, 9), date(2026, 11, 30)]


def test_the_older_actual_previous_event_wording_means_the_same_for_cycles():
    result = compile_facts(cycle_day(1), blocks=cycle_block("ACTUAL_PREVIOUS_EVENT"))

    plan = next(item for item in result.schedule.repeat_blocks if item.code == "TREATMENT").cycle_plan
    assert plan.dependency_mode == "ACTUAL_PREVIOUS_CYCLE_START"


def test_a_cycle_dependency_mode_stated_on_the_cycle_fact_is_used():
    result = compile_facts(cycle_day(1, dependency_mode="NOMINAL"))

    plan = next(item for item in result.schedule.repeat_blocks if item.code == "TREATMENT").cycle_plan
    assert plan.dependency_mode == "NOMINAL"
    assert not codes(result)


def test_unstated_cycle_mode_is_unclear_and_blocks():
    """F3: whether cycles follow the plan or the actual previous cycle is never assumed."""
    result = compile_facts(cycle_day(1), cycle_day(8), blocks=[TREATMENT_CYCLES])

    block = next(item for item in result.schedule.repeat_blocks if item.code == "TREATMENT")
    assert (block.cycle_plan.dependency_mode, block.dependency_mode) == ("UNCLEAR", "UNCLEAR")
    assert "UNCLEAR_DEPENDENCY_MODE" in issues_for(result, block.id)


def test_cycle_length_or_count_unstated_stays_unresolved():
    no_count = compile_facts(cycle_day(1, count=None), blocks=cycle_block("NOMINAL"))
    no_length = compile_facts(fact("Cycle day 1", timing={
        "pattern": "CYCLE_DAY", "day": 1, "cycle": {"start_anchor": "Cycle 1 Day 1"}},
        repeat={"interval": "21 DAY", "end": {"type": "COUNT", "count": 6}}, block="Treatment"),
        blocks=cycle_block("NOMINAL"))

    assert isinstance(event(no_count, "CYCLE_DAY_1").timing, UnresolvedTiming)
    assert "how many cycles" in event(no_count, "CYCLE_DAY_1").timing.reason
    assert isinstance(event(no_length, "CYCLE_DAY_1").timing, UnresolvedTiming)
    assert "cycle length" in event(no_length, "CYCLE_DAY_1").timing.reason
    assert not any(item.cycle_plan for item in no_count.schedule.repeat_blocks)


def test_cycles_until_progression_are_stopped_by_the_condition():
    result = compile_facts(cycle_day(1, count=None, repeat={
        "interval": "21 DAY", "end": {"type": "UNTIL_CONDITION", "condition": "Progression"}}),
        blocks=cycle_block("NOMINAL"))

    block = next(item for item in result.schedule.repeat_blocks if item.code == "TREATMENT")
    assert block.cycle_plan.termination.type == "HORIZON"
    progression = next(item for item in result.schedule.conditional_definitions if item.code == "PROGRESSION")
    assert [(item.action_type, item.target_code) for item in progression.actions] == [
        ("STOP_BLOCK", "TREATMENT")]


def test_a_specific_cycle_day_is_dated_from_the_cycle_plan():
    result = compile_facts(cycle_day(1), fact("C3D1 MRI", timing={
        "pattern": "CYCLE_DAY", "day": 1,
        "cycle": {"start_anchor": "Cycle 1 Day 1", "length": "3 WEEK", "number": 3}}, block="Treatment"),
        blocks=cycle_block("ACTUAL_PREVIOUS_CYCLE_START"))

    mri = event(result, "C3D1_MRI")
    assert mri.timing == CycleDayTiming(
        cycle_reference="CYCLE", cycle=CycleSelector(type="NUMBER", number=3), day=1)
    # C2 given late on 02-Nov: Cycle 3 follows it, so the MRI is 23-Nov, not 16-Nov.
    [item] = cycle_dates(result, C1D1, date(2026, 11, 2), code="C3D1_MRI")
    assert item.timing.nominal_start == date(2026, 11, 23)


def test_a_specific_cycle_day_without_a_plan_is_an_offset_only_on_a_stated_nominal_grid():
    nominal = compile_facts(fact("C3D1 MRI", dependency_mode="NOMINAL", timing={
        "pattern": "CYCLE_DAY", "day": 1,
        "cycle": {"start_anchor": "Cycle 1 Day 1", "length": "3 WEEK", "number": 3}}))
    unstated = compile_facts(fact("C3D1 MRI", timing={
        "pattern": "CYCLE_DAY", "day": 1,
        "cycle": {"start_anchor": "Cycle 1 Day 1", "length": "3 WEEK", "number": 3}}))

    assert event(nominal, "C3D1_MRI").timing.offset.value == 42
    assert isinstance(event(unstated, "C3D1_MRI").timing, UnresolvedTiming)


# --- activity occurrence rules (spec F5) ---------------------------------------------------

def mri(occurrences: dict | None, note: str = "every second cycle", **extra) -> dict:
    return fact("MRI", "activity", at_visits=["Cycle day 1"], occurrence_note=note,
                occurrences=occurrences, quote=f"MRI {note}", **extra)


def activity_statuses(result, name: str = "MRI") -> dict[int, str]:
    schedule = result.schedule.model_copy(deep=True)
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"REGISTRATION": C1D1, "CYCLE_1_DAY_1": C1D1})
    evaluated = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2027, 6, 1))
    return {item.occurrence_index + 1: activity.status.value
            for item in evaluated.events if item.event_code == "CYCLE_DAY_1"
            for activity in item.activities if activity.display_name == name}


def test_mri_every_second_cycle_starting_cycle_2_becomes_an_occurrence_condition():
    result = compile_facts(cycle_day(1), mri({"every": 2, "starting_at": 2}), blocks=cycle_block("NOMINAL"))

    activity = next(item for item in event(result, "CYCLE_DAY_1").activities if item.code == "MRI")
    [condition] = activity.conditions
    assert isinstance(condition, MembershipCondition)
    assert (condition.value.field, condition.values) == ("occurrence_number", [2, 4, 6])
    assert not activity.qualifiers
    assert not codes(result)
    statuses = activity_statuses(result)
    assert [cycle for cycle, status in statuses.items() if status == "RESOLVED"] == [2, 4, 6]
    assert statuses[1] == statuses[3] == "NOT_APPLICABLE"


def test_pk_cycle_1_only_and_even_cycles():
    pk = compile_facts(cycle_day(1), mri({"numbers": [1]}, note="Cycle 1 only"), blocks=cycle_block("NOMINAL"))
    even = compile_facts(cycle_day(1), mri({"parity": "even"}, note="on even cycles"),
                         blocks=cycle_block("NOMINAL"))

    assert [c for c, status in activity_statuses(pk).items() if status == "RESOLVED"] == [1]
    assert [c for c, status in activity_statuses(even).items() if status == "RESOLVED"] == [2, 4, 6]


def test_an_ambiguous_every_second_cycle_stays_an_unresolved_qualifier():
    """No first cycle stated: C1, C3, C5 or C2, C4, C6 - never guessed."""
    result = compile_facts(cycle_day(1), mri({"every": 2}), blocks=cycle_block("NOMINAL"))

    activity = next(item for item in event(result, "CYCLE_DAY_1").activities if item.code == "MRI")
    assert not activity.conditions
    [qualifier] = activity.qualifiers
    assert qualifier.resolved is False and "starts with" in qualifier.details["reason"]
    assert codes(result)


def test_an_occurrence_pattern_on_open_ended_cycles_or_a_non_cycle_visit_stays_unresolved():
    open_ended = compile_facts(cycle_day(1, count=None, repeat={
        "interval": "21 DAY", "end": {"type": "OPEN_ENDED"}}), mri({"parity": "EVEN"}),
        blocks=cycle_block("NOMINAL"))
    plain_visit = compile_facts(
        fact("Cycle day 1", timing={"pattern": "OFFSET", "from": "Registration", "offset": "1 DAY"}),
        mri({"numbers": [1]}, note="Cycle 1 only"))

    for result in (open_ended, plain_visit):
        activity = next(item for item in event(result, "CYCLE_DAY_1").activities if item.code == "MRI")
        assert not activity.conditions and activity.qualifiers[0].resolved is False


def test_a_note_without_a_structured_selector_stays_an_unresolved_qualifier():
    result = compile_facts(cycle_day(1), mri(None), blocks=cycle_block("NOMINAL"))

    activity = next(item for item in event(result, "CYCLE_DAY_1").activities if item.code == "MRI")
    assert activity.qualifiers[0].text == "every second cycle" and not activity.conditions


# --- conditions and scope (spec I) ---------------------------------------------------

TREATMENT = fact("Treatment", timing={"pattern": "REPEAT", "from": "Registration"}, block="Treatment",
                 repeat={"interval": "3 WEEK", "includes_start": True, "end": {"type": "COUNT", "count": 6}})
BLOCKS = [{"name": "Treatment", "dependency_mode": "NOMINAL", "evidence": cite(9, "every 3 weeks")}]


def test_toxicity_hold_and_resume_become_pause_and_resume_block_actions():
    result = compile_facts(TREATMENT, fact(
        "Grade 3 toxicity", "condition", timing={"pattern": "HOLD_RESUME"},
        condition={"name": "Grade 3 toxicity", "targets": ["Treatment"], "resume_mode": "NOMINAL",
                   "trigger": {"field": "toxicity_grade", "operator": "GTE", "value": 3},
                   "resume_when": {"field": "toxicity_grade", "operator": "LTE", "value": 1}},
        quote="Grade 3 toxicity: hold treatment until <= grade 1; resume on schedule"), blocks=BLOCKS)

    definitions = {item.code: item for item in result.schedule.conditional_definitions}
    assert [a.action_type for a in definitions["GRADE_3_TOXICITY"].actions] == ["PAUSE_BLOCK"]
    assert [a.action_type for a in definitions["GRADE_3_TOXICITY_RESUMED"].actions] == ["RESUME_BLOCK"]
    block = next(item for item in result.schedule.repeat_blocks if item.code == "TREATMENT")
    assert block.resume_mode == "NOMINAL"
    assert not codes(result)


def test_hold_with_no_stated_resume_rule_leaves_resume_unclear():
    result = compile_facts(TREATMENT, fact(
        "Toxicity", "condition", timing={"pattern": "HOLD_RESUME"},
        condition={"name": "Toxicity", "targets": ["Treatment"]}), blocks=BLOCKS)

    block = next(item for item in result.schedule.repeat_blocks if item.code == "TREATMENT")
    assert block.resume_mode == "UNCLEAR"


def test_discontinuation_stops_the_treatment_block_and_adds_follow_up():
    result = compile_facts(
        TREATMENT,
        fact("Survival follow-up", timing={"pattern": "TRIGGERED",
                                           "from": {"name": "Progression", "kind": "CONDITION"},
                                           "offset": "30 DAY"},
             quote="Survival follow-up 30 days after disease progression"),
        fact("Progression", "condition", timing={"pattern": "DISCONTINUE"},
             condition={"name": "Progression", "targets": ["Treatment"]},
             quote="Discontinue for progression"),
        blocks=BLOCKS)

    progression = next(item for item in result.schedule.conditional_definitions if item.code == "PROGRESSION")
    actions = {(item.action_type, item.target_code) for item in progression.actions}
    assert actions == {("STOP_BLOCK", "TREATMENT"), ("ADD_EVENT", "SURVIVAL_FOLLOW_UP")}
    survival = event(result, "SURVIVAL_FOLLOW_UP")
    assert isinstance(survival.timing, TriggeredTiming)
    anchor = next(item for item in result.schedule.anchors if item.code == survival.timing.trigger.code)
    assert anchor.source_condition_code == "PROGRESSION"
    assert not codes(result)


def test_repeat_until_resolved_needs_its_interval_and_a_checkable_resolution():
    stated = compile_facts(fact(
        "Low ANC", "condition", timing={"pattern": "REPEAT_UNTIL_RESOLVED"},
        condition={"name": "Low ANC", "targets": ["CBC"], "interval": "1 WEEK",
                   "trigger": {"field": "anc", "operator": "LT", "value": 1000},
                   "resolution": {"field": "anc", "operator": "GTE", "value": 1500}},
        quote="If ANC < 1000, repeat CBC weekly until ANC >= 1500"))
    unstated = compile_facts(fact(
        "Low ANC", "condition", timing={"pattern": "REPEAT_UNTIL_RESOLVED"},
        condition={"name": "Low ANC", "targets": ["CBC"], "interval": "1 WEEK"}))

    definition = stated.schedule.conditional_definitions[0]
    action = definition.actions[0]
    assert (action.action_type, action.target_code) == ("REPEAT_EVENT", "CBC_WHILE_LOW_ANC")
    assert action.parameters["interval"] == {"value": 1, "unit": "WEEK"}
    assert definition.resolution_condition is not None
    assert not codes(stated)
    assert {"UNRESOLVED_CONDITION", "AMBIGUOUS_CONDITION"} <= codes(unstated)


def test_trial_level_fatal_toxicity_rule_never_becomes_a_patient_action():
    """I8: 'Fatal toxicity: accrual is suspended and the DSMC reviews.'"""
    result = compile_facts(TREATMENT, fact(
        "Fatal toxicity", "condition", scope="TRIAL", timing={"pattern": "DISCONTINUE"},
        condition={"name": "Fatal toxicity", "effect": "STOP", "targets": ["Treatment"]},
        quote="accrual is suspended and the DSMC reviews"), blocks=BLOCKS)

    [rule] = result.schedule.conditional_definitions
    assert rule.scope == "TRIAL"
    recorded = [item for item in result.schedule.schedule_metadata.extensions
                if item.get("type") == "NON_PATIENT_RULE"]
    assert [item["scope"] for item in recorded] == ["TRIAL"]
    assert not codes(result)

    # Even with the condition confirmed for a patient, no treatment visit moves.
    schedule = result.schedule.model_copy(deep=True)
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"REGISTRATION": date(2026, 10, 1)},
        conditions={"FATAL_TOXICITY": PatientConditionStatus(
            condition_code="FATAL_TOXICITY", state="ACTIVE", occurrence_date=date(2026, 10, 2))})
    evaluated = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2027, 6, 1))
    treatment = [item for item in evaluated.events if item.event_code == "TREATMENT"]
    assert len(treatment) == 6 and {item.status for item in treatment} == {"RESOLVED"}


def test_a_trial_rule_naming_nothing_patient_level_is_recorded_without_blocking():
    result = compile_facts(TREATMENT, fact(
        "Accrual suspension", "condition", scope="TRIAL", timing={"pattern": "DISCONTINUE"},
        condition={"name": "Fatal toxicity", "effect": "STOP"},
        quote="accrual is suspended and the DSMC reviews"), blocks=BLOCKS)

    assert result.schedule.conditional_definitions == []
    assert not codes(result)
    notice = next(item for item in result.issues if item.issue_code == "NON_PATIENT_RULE")
    assert notice.blocking is False


def test_a_condition_whose_effect_is_not_stated_is_reported():
    result = compile_facts(fact("Dose modification", "condition",
                                condition={"name": "Dose modification"}))

    assert "UNRESOLVED_CONDITION" in codes(result)


# --- applicability, activities, evidence ------------------------------------------------

def test_arm_and_attribute_rules_and_activity_links():
    result = compile_facts(
        fact("Arm B day 8", timing={"pattern": "PROTOCOL_DAY", "day": 8}, applies_to={"arms": ["Arm B"]},
             activities=["Vital signs"]),
        fact("Pregnancy test", "activity", at_visits=["Arm B day 8"],
             applies_to={"attributes": [{"field": "wocbp", "operator": "EQUALS", "value": True}]}),
        arms=[{"name": "Arm A"}, {"name": "Arm B"}])

    visit = event(result, "ARM_B_DAY_8")
    assert [(rule.dimension, rule.values) for rule in visit.applicability] == [("ARM", ["ARM_B"])]
    activities = {item.code: item for item in visit.activities}
    assert set(activities) == {"VITAL_SIGNS", "PREGNANCY_TEST"}
    rule = activities["PREGNANCY_TEST"].applicability[0]
    assert (rule.dimension, rule.field, rule.values) == ("PATIENT_ATTRIBUTE", "wocbp", ["true"])
    assert not codes(result)


def test_every_compiled_entity_carries_claim_level_evidence():
    result = compile_facts(
        fact("Screening labs", timing={"pattern": "WITHIN_BEFORE", "from": "Registration", "duration": "28 DAY"},
             activities=["CBC"]), TREATMENT, blocks=BLOCKS)

    assert "MISSING_EVIDENCE" not in codes(result)
    claimed = {(claim.claim_entity_id, claim.claim_type) for claim in result.schedule.claim_evidence}
    labs = event(result, "SCREENING_LABS")
    assert {(labs.id, "EVENT_NAME"), (labs.id, "TIMING")} <= claimed


def test_reader_issues_survive_revalidation():
    doc = DocumentFacts.model_validate({
        "day_numbering": DAY_1, "facts": [REGISTRATION],
        "reader_issues": [{"code": "EXTRACTION_PAGES_UNREAD", "message": "Pages 4-6 could not be read",
                           "pages": [4, 5, 6]}]})
    result = compile_schedule(doc, name="Primary")

    from app.domain.schedule.validator import ScheduleValidator
    again = ScheduleValidator().validate(result.schedule)
    assert "EXTRACTION_PAGES_UNREAD" in {item.issue_code for item in again if item.blocking}


def test_compilation_is_deterministic():
    facts = [fact("Visit 3", timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"},
                  window={"before": "3 DAY", "after": "3 DAY"}), TREATMENT]

    def shape(result):
        return [(item.code, item.timing.model_dump(mode="json"),
                 item.recurrence.model_dump(mode="json") if item.recurrence else None)
                for item in result.schedule.events]

    assert shape(compile_facts(*facts, blocks=BLOCKS)) == shape(compile_facts(*facts, blocks=BLOCKS))


def test_independent_schedule_options_compile_separately():
    doc = DocumentFacts.model_validate({
        "day_numbering": DAY_1,
        "schedule_options": [{"id": "A", "label": "Substudy A"}, {"id": "B", "label": "Substudy B"}],
        "facts": [REGISTRATION,
                  fact("A visit", timing={"pattern": "OFFSET", "from": "Registration", "offset": "1 WEEK"},
                       schedule_option="A"),
                  fact("B visit", timing={"pattern": "OFFSET", "from": "Registration", "offset": "2 WEEK"},
                       schedule_option="B")],
    })
    results = compile_document(doc, schedule_name="protocol.pdf")

    assert [option["label"] for option, _ in results] == ["Substudy A", "Substudy B"]
    assert [[e.code for e in result.schedule.events] for _, result in results] == [["A_VISIT"], ["B_VISIT"]]
    assert results[0][1].schedule.schedule_metadata.name == "protocol.pdf - Substudy A"


def test_a_single_schedule_protocol_has_no_option():
    doc = DocumentFacts.model_validate({"day_numbering": DAY_1, "facts": [REGISTRATION]})
    [(option, result)] = compile_document(doc)

    assert option is None
    assert "EMPTY_SCHEDULE" in codes(result)


# --- end to end: facts -> canonical draft -> approval -> patient dates -------------------

ORG = uuid4()
REVIEWER = uuid4()


def journey_facts() -> DocumentFacts:
    return DocumentFacts.model_validate({
        "day_numbering": DAY_1,
        "facts": [
            REGISTRATION,
            fact("Screening labs", event_type="LABORATORY",
                 timing={"pattern": "WITHIN_BEFORE", "from": "Registration", "duration": "28 DAY"},
                 activities=["CBC"], quote="Labs within 28 days before registration"),
            fact("Day 8 visit", timing={"pattern": "PROTOCOL_DAY", "day": 8},
                 activities=["Vital signs"], quote="Day 8: vital signs"),
            fact("Visit 3", timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"},
                 window={"before": "3 DAY", "after": "3 DAY"},
                 quote="Visit 3: 4 weeks after registration +/- 3 days"),
            fact("Treatment", event_type="TREATMENT", timing={"pattern": "REPEAT", "from": "Registration"},
                 repeat={"interval": "3 WEEK", "includes_start": True, "end": {"type": "COUNT", "count": 4}},
                 quote="Treatment every 3 weeks for 4 cycles"),
        ],
    })


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as active:
        yield active


def test_compiled_facts_approve_and_date_a_real_patient(session):
    [(option, result)] = compile_document(journey_facts(), schedule_name="Primary")
    assert option is None
    assert not [item for item in result.issues if item.blocking], [
        (item.issue_code, item.message) for item in result.issues if item.blocking]

    trial, [row] = import_native_schedules(
        session, organization_id=ORG, actor_id=REVIEWER, external_trial_id="trial-extract",
        protocol_number="EXT-1", study_title="Extraction journey",
        document_uri="private://protocol-uploads/x.pdf", document_name="protocol.pdf",
        document_hash="e" * 64, definitions=[{
            "id": "sd-extract", "native_schedule": result.schedule.model_dump(mode="json"),
            "native_issues": [item.model_dump(mode="json") for item in result.issues],
        }])
    session.commit()
    assert row.status == "VALIDATION_REQUIRED"

    review = ScheduleReviewService(session)
    review.submit_for_review(row.schedule_version_id, actor_id=REVIEWER)
    schedule = ScheduleRepository(session).get(row.schedule_version_id)
    for item in schedule.events:
        paths = ["display_name", "timing"] + (["activities"] if item.activities else []) + (
            ["recurrence"] if item.recurrence else [])
        for path in paths:
            session.add(db.ReviewDecision(
                schedule_version_id=row.schedule_version_id, decision="CONFIRM", reviewer_id=REVIEWER,
                entity_type="EVENT", entity_id=item.id, field_path=path))
    session.flush()
    review.approve(row.schedule_version_id, reviewer_id=REVIEWER)
    session.commit()

    registration = date(2026, 10, 1)
    enrollment = OperationalBridgeService(session).enroll_patient(
        organization_id=ORG, actor_id=REVIEWER, canonical_trial_id=trial.id,
        schedule_definition_id=row.schedule_definition_id, external_patient_id="p-1",
        patient_code="P-001", baseline_date=registration, horizon=registration + timedelta(days=365))
    session.commit()

    by_code: dict[str, list] = {}
    for item in enrollment.events:
        by_code.setdefault(item.event_code, []).append(item)
    labs = by_code["SCREENING_LABS"][0]
    assert (labs.earliest_date, labs.latest_date) == (date(2026, 9, 3), date(2026, 10, 1))
    assert by_code["DAY_8_VISIT"][0].nominal_date == date(2026, 10, 8)
    visit_3 = by_code["VISIT_3"][0]
    assert (visit_3.nominal_date, visit_3.earliest_date, visit_3.latest_date) == (
        date(2026, 10, 29), date(2026, 10, 26), date(2026, 11, 1))
    assert [item.nominal_date for item in by_code["TREATMENT"]] == [
        date(2026, 10, 1), date(2026, 10, 22), date(2026, 11, 12), date(2026, 12, 3)]


def test_phased_follow_up_dates_match_the_spec_example():
    """E9 worked example: end of therapy 07-Dec-2026, one plan, each boundary once."""
    doc = DocumentFacts.model_validate({
        "day_numbering": DAY_1, "facts": [REGISTRATION, END_OF_THERAPY, e9_fact()]})
    result = compile_schedule(doc, name="Primary")
    assert not [item for item in result.issues if item.blocking]
    schedule = result.schedule.model_copy(deep=True)
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"REGISTRATION": date(2026, 10, 1), "END_OF_THERAPY": date(2026, 12, 7)})
    evaluated = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2034, 1, 1))

    dates = [item.timing.nominal_start for item in evaluated.events
             if item.event_code == "FOLLOW_UP" and item.timing]
    segment_1 = [date(2027, 3, 7), date(2027, 6, 7), date(2027, 9, 7), date(2027, 12, 7),
                 date(2028, 3, 7), date(2028, 6, 7), date(2028, 9, 7), date(2028, 12, 7)]
    segment_2 = [date(2029, 6, 7), date(2029, 12, 7), date(2030, 6, 7), date(2030, 12, 7),
                 date(2031, 6, 7), date(2031, 12, 7)]
    segment_3 = [date(2032, 12, 7), date(2033, 12, 7)]
    assert dates == segment_1 + segment_2 + segment_3
    assert len(dates) == len(set(dates)), "a boundary visit was duplicated"


def test_a_window_on_a_repeating_visit_is_applied_to_every_occurrence():
    """B2: 'every 3 months +/- 14 days' - each occurrence gets its own window."""
    result = compile_facts(fact(
        "Quarterly follow-up", timing={"pattern": "REPEAT", "from": "End of therapy"},
        window={"before": "14 DAY", "after": "14 DAY"},
        repeat={"interval": "3 MONTH", "includes_start": False, "end": {"type": "COUNT", "count": 3}}))
    assert not codes(result)
    schedule = result.schedule.model_copy(deep=True)
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"REGISTRATION": date(2026, 10, 1), "END_OF_THERAPY": date(2026, 12, 7)})
    evaluated = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2028, 1, 1))

    first = [item for item in evaluated.events if item.event_code == "QUARTERLY_FOLLOW_UP"][0]
    assert (first.timing.nominal_start, first.timing.earliest, first.timing.latest) == (
        date(2027, 3, 7), date(2027, 2, 21), date(2027, 3, 21))
    assert all(item.timing.earliest is not None for item in evaluated.events
               if item.event_code == "QUARTERLY_FOLLOW_UP")


# --- planned vs actual anchors (spec C6 / D2) -------------------------------------------

SURGERY = fact("Surgery", "anchor", anchor={"type": "RECORDED"}, quote="Surgery is performed")


def surgery_result():
    return compile_facts(
        SURGERY,
        fact("Pre-op assessment", timing={
            "pattern": "WITHIN_BEFORE", "duration": "14 DAY",
            "from": {"name": "Surgery", "accepts": "PLANNED_OR_ACTUAL"}},
            quote="Pre-operative assessment within 14 days before the planned surgery"),
        fact("Post-op visit", timing={"pattern": "OFFSET", "from": "Surgery", "offset": "30 DAY"},
             quote="Post-op visit 30 days after surgery"))


def test_only_a_rule_stated_against_the_planned_date_accepts_a_planned_anchor():
    result = surgery_result()

    pre_op, post_op = event(result, "PRE_OP_ASSESSMENT"), event(result, "POST_OP_VISIT")
    assert pre_op.timing.reference == AnchorReference(code="SURGERY", accepts="PLANNED_OR_ACTUAL")
    assert post_op.timing.reference == AnchorReference(code="SURGERY", accepts="ACTUAL")
    assert not codes(result)


def test_d2_planned_surgery_dates_pre_op_but_post_op_waits_for_the_actual_date():
    """D2: surgery planned 14-Dec-2026 -> pre-op window 30-Nov..14-Dec RESOLVED;
    post-op WAITING_FOR_ANCHOR. D3: surgery on 17-Dec -> post-op 16-Jan-2027."""
    schedule = surgery_result().schedule.model_copy(deep=True)
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED

    def evaluate(**values):
        context = PatientContext(patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
                                 anchors={"REGISTRATION": date(2026, 10, 1), **values.pop("anchors", {})},
                                 **values)
        return {item.event_code: item for item in
                ScheduleEvaluator().evaluate(schedule, context, horizon=date(2027, 6, 1)).events}

    planned = evaluate(planned_anchors={"SURGERY": date(2026, 12, 14)})
    pre_op = planned["PRE_OP_ASSESSMENT"]
    assert pre_op.status == "RESOLVED"
    assert (pre_op.timing.earliest, pre_op.timing.latest) == (date(2026, 11, 30), date(2026, 12, 14))
    assert planned["POST_OP_VISIT"].status == "WAITING_FOR_ANCHOR"

    actual = evaluate(anchors={"SURGERY": date(2026, 12, 17)})
    assert actual["POST_OP_VISIT"].timing.nominal_start == date(2027, 1, 16)


# --- nested groups (spec H4) -----------------------------------------------------------------

def test_cohorts_nested_in_parts_carry_their_parent():
    """'Part A -> Cohort 1 or 2; Part B -> Cohort 3.'"""
    from app.domain.schedule.enrolment import enrolment_options

    result = compile_facts(
        fact("Part A visit", timing={"pattern": "OFFSET", "from": "Registration", "offset": "1 WEEK"},
             applies_to={"parts": ["Part A"]}),
        parts=[{"name": "Part A"}, {"name": "Part B"}],
        cohorts=[{"name": "Cohort 1", "parent": "Part A"},
                 {"name": "Cohort 2", "parent": "Part A", "parent_type": "part"},
                 {"name": "Cohort 3", "parent": "Part B"}])

    cohorts = {item.code: (item.parent_dimension_type, item.parent_code) for item in result.schedule.cohorts}
    assert cohorts == {"COHORT_1": ("PART", "PART_A"), "COHORT_2": ("PART", "PART_A"),
                       "COHORT_3": ("PART", "PART_B")}
    assert [(item.dimension_type, item.code) for item in result.schedule.dimensions] == [
        ("PART", "PART_A"), ("PART", "PART_B")]
    assert [(rule.dimension, rule.values) for rule in event(result, "PART_A_VISIT").applicability] == [
        ("PART", ["PART_A"])]
    assert not codes(result)
    options = {item.dimension_type: item for item in enrolment_options(result.schedule, {"PART": "PART_A"})}
    assert [item.code for item in options["COHORT"].options] == ["COHORT_1", "COHORT_2"]


def test_a_parent_that_is_not_defined_is_reported_not_guessed():
    result = compile_facts(cohorts=[{"name": "Cohort 1", "parent": "Part Z"}])

    [cohort] = result.schedule.cohorts
    assert cohort.parent_code is None
    issue = next(item for item in result.issues if item.issue_code == "UNRESOLVED_REFERENCE")
    assert issue.blocking and issue.details["parent"] == "Part Z"


# --- blocks that exist only for a condition (item 6) ---------------------------------------

def test_a_series_stopped_by_a_condition_does_not_invent_a_block_mode():
    """The engine never reads a plain block's dependency mode, but the validator
    requires one; the compiler does not invent it and explains why the block exists."""
    result = compile_facts(fact(
        "Tumour assessment", timing={"pattern": "REPEAT", "from": "Registration"},
        repeat={"interval": "12 WEEK", "includes_start": False,
                "end": {"type": "UNTIL_CONDITION", "condition": "Progression"}}))

    block = next(item for item in result.schedule.repeat_blocks if "TUMOUR_ASSESSMENT" in item.event_codes)
    assert block.dependency_mode == "UNCLEAR" and block.cycle_plan is None
    assert "UNCLEAR_DEPENDENCY_MODE" in issues_for(result, block.id)
    note = next(item for item in result.issues if item.issue_code == "CONDITION_BLOCK_MODE_UNSTATED")
    assert note.blocking is False and note.entity_id == block.id


# --- rule scope (spec I8) ----------------------------------------------------------------------

@pytest.mark.parametrize("scope", ["SITE", "COUNTRY", "TRIAL"])
def test_site_country_and_trial_rules_keep_their_scope_on_the_conditional_definition(scope):
    result = compile_facts(TREATMENT, fact(
        "Enrolment pause", "condition", scope=scope, timing={"pattern": "HOLD_RESUME"},
        condition={"name": "Enrolment pause", "effect": "PAUSE", "targets": ["Treatment"]},
        quote="the site suspends enrolment"), blocks=BLOCKS)

    [rule] = [item for item in result.schedule.conditional_definitions if item.code == "ENROLMENT_PAUSE"]
    assert rule.scope == scope
    assert any(item.get("type") == "NON_PATIENT_RULE" and item["scope"] == scope
               for item in result.schedule.schedule_metadata.extensions)


# --- protocol order ---------------------------------------------------------

def test_events_are_listed_in_protocol_order_not_the_order_the_pages_were_read():
    """A follow-up section read before the visit table must not list first."""
    result = compile_facts(
        fact("PET scan", event_type="IMAGING",
             timing={"pattern": "RANGE", "from": "End of therapy", "start": "12 WEEK", "end": "16 WEEK"}),
        fact("Visit 3", timing={"pattern": "OFFSET", "from": "Registration", "offset": "4 WEEK"}),
        fact("Unplaced visit"),
        fact("Day 15 visit", timing={"pattern": "PROTOCOL_DAY", "day": 15}),
        fact("Screening labs",
             timing={"pattern": "WITHIN_BEFORE", "from": "Registration", "duration": "28 DAY"}))

    assert [item.code for item in result.schedule.events] == [
        "SCREENING_LABS", "DAY_15_VISIT", "VISIT_3", "PET_SCAN", "UNPLACED_VISIT"]
