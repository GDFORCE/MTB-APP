"""Composable constraint language: feasible-set evaluation (WP2).

Requirements: RL-04, RL-05, RL-06, RL-13, IR-04 (runtime side), NF-01, NF-03,
NF-08. Acceptance probes: AT-01, AT-02, AT-17, AT-18.

Every example is synthetic and independent of any real protocol: anchor
names, offsets and dates are invented to exercise the operators, and AT-17
re-runs the same shapes under unrelated names to prove no name-based rule
exists.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from _engine_cases import anchor, approved, by_code, context, evaluate, only, visit

from app.domain.schedule.condition import (
    AllCondition,
    AnyCondition,
    ComparisonCondition,
    ExistsCondition,
    FieldOperand,
    LiteralOperand,
    NotCondition,
    TruthValue,
    UnsupportedCondition,
    evaluate_condition,
)
from app.domain.schedule.deviation import DeviationType, assess_visit
from app.domain.schedule.diff import ChangeType, Significance, compare_schedule_versions
from app.domain.schedule.feasibility import Resolved, evaluate_feasibility
from app.domain.schedule.models import (
    Activity,
    ClaimEvidence,
    Evidence,
    FeasibilityStatus,
    PatientEventStatus,
    PatientActivityStatus,
    RecurrenceRule,
    RecurrenceTermination,
    ScheduleStatus,
    UniversalSchedule,
    Window,
)
from app.domain.schedule.projection import constraint_view, project_schedule
from app.domain.schedule.timing import (
    ActivityValue,
    AllConstraint,
    AnchorReference,
    AnchorValue,
    AnyConstraint,
    AtConstraint,
    ConstraintTiming,
    EventValue,
    LiteralDateValue,
    LowerBoundConstraint,
    NonNegativeTemporalAmount,
    OccurrenceSelector,
    OffsetTiming,
    PositiveTemporalAmount,
    RangeTiming,
    ShiftValue,
    TemporalAmount,
    UpperBoundConstraint,
)
from app.domain.schedule.validator import ScheduleValidator
from app.services.schedule_projection import (
    ProjectedEvent,
    feasibility_from_explanation,
    project,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def ref(code: str, accepts: str = "ACTUAL") -> AnchorValue:
    return AnchorValue(code=code, accepts=accepts)


def shift(value, amount: int, unit: str = "DAY", policy: str | None = None) -> ShiftValue:
    return ShiftValue(reference=value, amount=TemporalAmount(value=amount, unit=unit),
                      calendar_policy_ref=policy)


def lower(value, cid: str | None = None, *, inclusive: bool = True, evidence=()) -> LowerBoundConstraint:
    return LowerBoundConstraint(id=cid, value=value, inclusive=inclusive, evidence_refs=list(evidence))


def upper(value, cid: str | None = None, *, inclusive: bool = True, evidence=()) -> UpperBoundConstraint:
    return UpperBoundConstraint(id=cid, value=value, inclusive=inclusive, evidence_refs=list(evidence))


def all_of(*items, cid: str | None = None) -> AllConstraint:
    return AllConstraint(id=cid, items=list(items))


def any_of(*items, cid: str | None = None) -> AnyConstraint:
    return AnyConstraint(id=cid, items=list(items))


def constrained(constraint) -> ConstraintTiming:
    return ConstraintTiming(constraint=constraint)


def schedule_with(events, anchors, **changes) -> UniversalSchedule:
    """An approved schedule whose constraints/activities carry claim evidence."""
    schedule = approved(events, anchors=anchors, **changes)
    evidence_id = schedule.evidence[0].id
    for event in schedule.events:
        if event.constraints is not None or isinstance(event.timing, ConstraintTiming):
            schedule.claim_evidence.append(ClaimEvidence(
                evidence_id=evidence_id, claim_type="CONSTRAINT", claim_entity_type="EVENT",
                claim_entity_id=event.id, confidence=0.9))
        for activity in event.activities:
            activity.evidence_refs = [evidence_id]
            claim_types = ["ACTIVITY"]
            if activity.timing is not None:
                claim_types.append("ACTIVITY_TIMING")
            if activity.constraints is not None or isinstance(activity.timing, ConstraintTiming):
                claim_types.append("CONSTRAINT")
            schedule.claim_evidence.extend(ClaimEvidence(
                evidence_id=evidence_id, claim_type=claim_type, claim_entity_type="ACTIVITY",
                claim_entity_id=activity.id, confidence=0.9) for claim_type in claim_types)
    return schedule


def blocking_codes(schedule: UniversalSchedule) -> list[str]:
    return sorted(item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(schedule)))


def issues_by_code(schedule: UniversalSchedule, code: str):
    return [item for item in ScheduleValidator().validate(schedule) if item.issue_code == code]


A_DATE = date(2027, 1, 4)


def at01_constraint() -> AllConstraint:
    """Range 12-16 weeks after A, and at least 12 weeks after B (AT-01)."""
    return all_of(
        lower(shift(ref("A_END"), 12, "WEEK"), "a-min"),
        upper(shift(ref("A_END"), 16, "WEEK"), "a-max"),
        lower(shift(ref("B_END"), 12, "WEEK"), "b-min"),
    )


def at01_schedule(constraint=None) -> UniversalSchedule:
    return schedule_with(
        [visit("SCAN", constrained(constraint or at01_constraint()), event_type="IMAGING")],
        anchors=[anchor("A_END"), anchor("B_END"), anchor("C_END")])


# ---------------------------------------------------------------------------
# AT-01 / RL-04 / RL-13: conjunction across anchors
# ---------------------------------------------------------------------------


def test_at01_range_from_a_and_minimum_delay_from_b_intersect():
    schedule = at01_schedule()
    assert blocking_codes(schedule) == []
    b = date(2027, 1, 18)
    result = evaluate(schedule, context(schedule, anchors={"A_END": A_DATE, "B_END": b}))
    scan = only(result, "SCAN")

    assert scan.status == PatientEventStatus.RESOLVED
    assert scan.feasibility == FeasibilityStatus.FEASIBLE
    [interval] = scan.feasible_intervals
    assert interval.lower.value == b + timedelta(weeks=12)  # max(A+12w, B+12w)
    assert interval.upper.value == A_DATE + timedelta(weeks=16)
    assert interval.lower.sources == ["b-min"] and interval.upper.sources == ["a-max"]
    assert not interval.lower.provisional and not interval.upper.provisional
    # A range has no invented target date; the flat timing carries the set.
    assert scan.timing.nominal_start is None
    assert (scan.timing.earliest, scan.timing.latest) == (interval.lower.value, interval.upper.value)
    # RL-04: the less restrictive bound is retained for audit, marked non-binding.
    trace = {item.constraint_id: item for item in scan.constraint_trace}
    assert trace["a-min"].binding is False and trace["a-min"].value == A_DATE + timedelta(weeks=12)
    assert trace["b-min"].binding is True
    assert trace["b-min"].inputs == [
        {"kind": "ANCHOR", "code": "B_END", "value": b.isoformat(), "basis": "ACTUAL"}]
    assert scan.explanation["feasibility"]["status"] == "FEASIBLE"


def test_at01_same_semantics_with_legacy_range_timing_plus_event_constraints():
    event = visit("SCAN", RangeTiming(
        reference=AnchorReference(code="A_END"),
        start=TemporalAmount(value=12, unit="WEEK"), end=TemporalAmount(value=16, unit="WEEK")),
        constraints=lower(shift(ref("B_END"), 12, "WEEK"), "b-min"))
    schedule = schedule_with([event], anchors=[anchor("A_END"), anchor("B_END")])
    assert blocking_codes(schedule) == []
    b = date(2027, 1, 18)
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE, "B_END": b})), "SCAN")
    [interval] = scan.feasible_intervals
    assert interval.lower.sources == ["b-min"]
    assert interval.upper.sources == ["SCAN/timing"]
    assert interval.upper.value == A_DATE + timedelta(weeks=16)
    assert scan.timing.nominal_start is None  # range start is not a target once constrained


def test_at01_missing_b_is_incomplete_with_known_bounds_and_never_dated():
    schedule = at01_schedule()
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INCOMPLETE
    assert scan.status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert scan.timing is None
    [interval] = scan.feasible_intervals
    assert interval.lower.value == A_DATE + timedelta(weeks=12)
    assert interval.lower.provisional is True  # B may still raise it
    assert interval.upper.value == A_DATE + timedelta(weeks=16)
    assert interval.upper.provisional is False  # no missing input can move it
    [missing] = scan.missing_inputs
    assert (missing.kind, missing.code, missing.reason, missing.constraint_ids) == (
        "ANCHOR", "B_END", "NOT_RECORDED", ["b-min"])
    assert "B_END" in scan.explanation["reason"]


def test_at01_empty_intersection_is_infeasible_with_both_constraint_ids():
    schedule = at01_schedule()
    late_b = date(2027, 3, 1)  # B + 12w falls after A + 16w
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE, "B_END": late_b})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INFEASIBLE
    assert scan.status == PatientEventStatus.UNRESOLVED
    assert scan.timing is None and scan.feasible_intervals == []
    assert scan.conflicting_constraints == ["a-max", "b-min"]
    assert "a-max" in scan.explanation["reason"]


def test_known_contradiction_is_infeasible_even_while_another_input_is_missing():
    constraint = all_of(*at01_constraint().items, upper(shift(ref("C_END"), 1), "c-max"))
    schedule = at01_schedule(constraint)
    scan = only(evaluate(schedule, context(
        schedule, anchors={"A_END": A_DATE, "B_END": date(2027, 3, 1)})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INFEASIBLE
    assert scan.conflicting_constraints == ["a-max", "b-min"]
    assert [item.code for item in scan.missing_inputs] == ["C_END"]


def test_planned_anchor_never_satisfies_an_actual_only_reference():
    schedule = at01_schedule()
    scan = only(evaluate(schedule, context(
        schedule, anchors={"A_END": A_DATE}, planned_anchors={"B_END": date(2027, 1, 18)})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INCOMPLETE
    assert scan.missing_inputs[0].reason == "PLANNED_ONLY"
    assert scan.missing_inputs[0].accepts == "ACTUAL"

    planned_ok = at01_schedule(all_of(
        *at01_constraint().items[:2],
        lower(shift(ref("B_END", "PLANNED_OR_ACTUAL"), 12, "WEEK"), "b-min")))
    scan = only(evaluate(planned_ok, context(
        planned_ok, anchors={"A_END": A_DATE}, planned_anchors={"B_END": date(2027, 1, 18)})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.FEASIBLE
    trace = {item.constraint_id: item for item in scan.constraint_trace}
    assert trace["b-min"].inputs[0]["basis"] == "PLANNED"
    # An actual value, once recorded, wins over the plan.
    scan = only(evaluate(planned_ok, context(
        planned_ok, anchors={"A_END": A_DATE, "B_END": date(2027, 1, 11)},
        planned_anchors={"B_END": date(2027, 1, 18)})), "SCAN")
    assert {item.constraint_id: item for item in scan.constraint_trace}["b-min"].inputs[0]["basis"] == "ACTUAL"


def test_exclusive_bounds_on_dates_normalise_to_inclusive_neighbours():
    schedule = at01_schedule(all_of(
        lower(ref("A_END"), "after-a", inclusive=False),
        upper(ref("B_END"), "before-b", inclusive=False)))
    scan = only(evaluate(schedule, context(
        schedule, anchors={"A_END": A_DATE, "B_END": A_DATE + timedelta(days=5)})), "SCAN")
    [interval] = scan.feasible_intervals
    assert (interval.lower.value, interval.upper.value) == (
        A_DATE + timedelta(days=1), A_DATE + timedelta(days=4))
    # "after A" and "before A + 1" on whole days leave nothing - for ANY
    # patient, so the validator refuses it before it can ever be approved.
    schedule = at01_schedule(all_of(
        lower(ref("A_END"), "after-a", inclusive=False),
        upper(shift(ref("A_END"), 1), "before-next", inclusive=False)))
    [issue] = issues_by_code(schedule, "CONSTRAINT_INFEASIBLE")
    assert issue.details["conflicting_constraints"] == ["after-a", "before-next"]


def test_nominal_target_outside_the_constraint_set_is_dropped_not_moved():
    event = visit("SCAN", OffsetTiming(
        reference=AnchorReference(code="A_END"), offset=TemporalAmount(value=84, unit="DAY")),
        window=Window(before=NonNegativeTemporalAmount(value=7, unit="DAY"),
                      after=NonNegativeTemporalAmount(value=7, unit="DAY")),
        constraints=lower(shift(ref("B_END"), 12, "WEEK"), "b-min"))
    schedule = schedule_with([event], anchors=[anchor("A_END"), anchor("B_END")])
    target = A_DATE + timedelta(days=84)
    # B + 12w = target + 3: the window survives from target + 3 to target + 7.
    scan = only(evaluate(schedule, context(schedule, anchors={
        "A_END": A_DATE, "B_END": A_DATE + timedelta(days=3)})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.FEASIBLE
    assert scan.timing.nominal_start is None
    assert scan.explanation["nominal_target_excluded"] == target.isoformat()
    assert (scan.timing.earliest, scan.timing.latest) == (
        target + timedelta(days=3), target + timedelta(days=7))
    # When the target is still permitted it stays the target.
    scan = only(evaluate(schedule, context(schedule, anchors={
        "A_END": A_DATE, "B_END": A_DATE - timedelta(days=3)})), "SCAN")
    assert scan.timing.nominal_start == target
    assert scan.timing.earliest == target - timedelta(days=3)


# ---------------------------------------------------------------------------
# AT-02 / IR-04 runtime side: duplicates dedupe, complementary both enforced
# ---------------------------------------------------------------------------


def test_at02_duplicate_restriction_is_deduplicated_with_both_sources_kept():
    schedule = at01_schedule(all_of(
        lower(shift(ref("A_END"), 12, "WEEK"), "stmt-1"),
        lower(shift(ref("A_END"), 12, "WEEK"), "stmt-2"),
        upper(shift(ref("A_END"), 16, "WEEK"), "a-max")))
    repeated = Evidence(evidence_type="SECTION", page_number=40, source_text="Repeated rule")
    schedule.evidence.append(repeated)
    first, second = str(schedule.evidence[0].id), str(repeated.id)
    items = schedule.events[0].timing.constraint.items
    items[0].evidence_refs, items[1].evidence_refs = [first], [second]
    assert blocking_codes(schedule) == []
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "SCAN")
    assert [item.constraint_ids for item in scan.constraint_relations] == [["stmt-1", "stmt-2"]]
    assert scan.constraint_relations[0].evidence_refs == [first, second]
    [interval] = scan.feasible_intervals
    assert interval.lower.sources == ["stmt-1", "stmt-2"]
    assert interval.lower.value == A_DATE + timedelta(weeks=12)
    trace = [item for item in scan.constraint_trace if item.type == "LOWER_BOUND"]
    assert len(trace) == 1 and trace[0].evidence_refs == [first, second]


def test_at02_complementary_restriction_is_not_a_duplicate_and_is_enforced():
    schedule = at01_schedule(all_of(
        lower(shift(ref("A_END"), 12, "WEEK"), "stmt-1"),
        lower(shift(ref("B_END"), 12, "WEEK"), "stmt-2"),
        upper(shift(ref("A_END"), 16, "WEEK"), "a-max")))
    assert issues_by_code(schedule, "DUPLICATE_CONSTRAINT") == []
    early_b = only(evaluate(schedule, context(
        schedule, anchors={"A_END": A_DATE, "B_END": A_DATE - timedelta(days=30)})), "SCAN")
    late_b = only(evaluate(schedule, context(
        schedule, anchors={"A_END": A_DATE, "B_END": A_DATE + timedelta(days=10)})), "SCAN")
    assert early_b.constraint_relations == [] and late_b.constraint_relations == []
    assert early_b.feasible_intervals[0].lower.sources == ["stmt-1"]
    assert late_b.feasible_intervals[0].lower.sources == ["stmt-2"]
    assert late_b.feasible_intervals[0].lower.value == A_DATE + timedelta(days=10, weeks=12)


def test_same_offset_from_a_different_acceptance_is_not_a_duplicate():
    schedule = at01_schedule(all_of(
        lower(ref("A_END"), "actual-a"),
        lower(ref("A_END", "PLANNED_OR_ACTUAL"), "planned-a")))
    assert issues_by_code(schedule, "DUPLICATE_CONSTRAINT") == []


def test_validator_reports_duplicate_as_non_blocking_relation():
    schedule = at01_schedule(all_of(
        lower(shift(ref("A_END"), 12, "WEEK"), "stmt-1"),
        lower(shift(ref("A_END"), 12, "WEEK"), "stmt-2")))
    [issue] = issues_by_code(schedule, "DUPLICATE_CONSTRAINT")
    assert issue.blocking is False
    assert issue.details["constraint_ids"] == ["stmt-1", "stmt-2"]
    assert blocking_codes(schedule) == []


# ---------------------------------------------------------------------------
# AT-18 / RL-05: disjoint alternatives stay disjoint
# ---------------------------------------------------------------------------


def disjoint_schedule() -> UniversalSchedule:
    return at01_schedule(any_of(
        all_of(lower(shift(ref("A_END"), 7), "early-from"), upper(shift(ref("A_END"), 14), "early-to")),
        all_of(lower(shift(ref("A_END"), 28), "late-from"), upper(shift(ref("A_END"), 35), "late-to")),
    ))


def test_at18_two_disjoint_intervals_are_preserved_without_a_convex_hull():
    schedule = disjoint_schedule()
    assert blocking_codes(schedule) == []
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.FEASIBLE
    spans = [(item.lower.value, item.upper.value) for item in scan.feasible_intervals]
    assert spans == [
        (A_DATE + timedelta(days=7), A_DATE + timedelta(days=14)),
        (A_DATE + timedelta(days=28), A_DATE + timedelta(days=35)),
    ]
    # The flat timing holds ONE interval, never the hull [7, 35].
    assert (scan.timing.earliest, scan.timing.latest) == spans[0]
    assert scan.explanation["disjoint_intervals"] == 2
    assert [item["type"] for item in scan.timing.constraints][-1] == "FEASIBLE_SET"


def test_at18_gap_day_is_never_permitted_by_projection_or_deviation():
    schedule = disjoint_schedule()
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "SCAN")
    status, windows = feasibility_from_explanation(scan.explanation)
    assert status == "FEASIBLE" and len(windows) == 2
    gap_day = A_DATE + timedelta(days=20)

    def projected(today):
        return project([ProjectedEvent(
            logical_key="SCAN:0", event_code="SCAN", event_type="IMAGING", display_name="Scan",
            status=scan.status.value, nominal_date=None,
            earliest_date=scan.timing.earliest, latest_date=scan.timing.latest,
            feasibility=status, feasible_windows=windows)], today=today)

    assert projected(gap_day).reminders[0].policy_state == "UPCOMING"
    assert projected(A_DATE + timedelta(days=30)).reminders[0].policy_state == "DUE_WINDOW"
    assert projected(A_DATE + timedelta(days=36)).reminders[0].policy_state == "OVERDUE"
    # The reminder uses the start of the first interval - no invented midpoint.
    assert projected(A_DATE).reminders[0].nominal_date == A_DATE + timedelta(days=7)

    spans = [(item.start, item.end) for item in windows]

    def deviation(actual, today):
        return assess_visit(
            logical_key="SCAN:0", event_code="SCAN", display_name="Scan", status="RESOLVED",
            planned_date=None, earliest_date=scan.timing.earliest, latest_date=scan.timing.latest,
            actual_date=actual, today=today, feasibility=status, feasible_windows=spans)

    assert deviation(gap_day, gap_day).deviation_type == DeviationType.LATE
    assert "gap" in deviation(gap_day, gap_day).reason
    assert deviation(A_DATE + timedelta(days=30), gap_day).deviation_type == DeviationType.NONE
    assert deviation(None, gap_day).deviation_type == DeviationType.NONE  # later interval still open
    assert deviation(None, A_DATE + timedelta(days=40)).deviation_type == DeviationType.MISSED


def test_any_merges_overlapping_and_adjacent_alternatives_only():
    def spans(constraint):
        schedule = at01_schedule(constraint)
        scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "SCAN")
        return [((item.lower.value - A_DATE).days, (item.upper.value - A_DATE).days)
                for item in scan.feasible_intervals]

    window = lambda a, b, tag: all_of(lower(shift(ref("A_END"), a), f"{tag}-lo"),
                                      upper(shift(ref("A_END"), b), f"{tag}-hi"))
    assert spans(any_of(window(7, 14, "x"), window(10, 20, "y"))) == [(7, 20)]
    assert spans(any_of(window(7, 14, "x"), window(15, 20, "y"))) == [(7, 20)]  # whole days
    assert spans(any_of(window(7, 14, "x"), window(16, 20, "y"))) == [(7, 14), (16, 20)]


def test_any_with_an_unresolved_alternative_is_incomplete_not_narrowed():
    schedule = at01_schedule(any_of(
        all_of(lower(shift(ref("A_END"), 7), "a-lo"), upper(shift(ref("A_END"), 14), "a-hi")),
        all_of(lower(shift(ref("B_END"), 7), "b-lo"), upper(shift(ref("B_END"), 14), "b-hi"))))
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INCOMPLETE
    assert scan.status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert {item.code for item in scan.missing_inputs} == {"B_END"}


def test_any_of_only_infeasible_alternatives_is_infeasible_with_all_ids():
    # Patient-dependent (two anchors), so only runtime data reveals the conflict.
    schedule = at01_schedule(all_of(
        upper(ref("A_END"), "not-after-a"),
        any_of(lower(shift(ref("B_END"), 5), "alt-1"), lower(shift(ref("B_END"), 9), "alt-2"))))
    assert blocking_codes(schedule) == []
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE, "B_END": A_DATE})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INFEASIBLE
    # alt-2's set lies inside alt-1's, so the union's binding bound is alt-1's.
    assert scan.conflicting_constraints == ["alt-1", "not-after-a"]
    # Disjoint alternatives that each conflict are both named.
    schedule = at01_schedule(all_of(
        lower(ref("A_END"), "from-a"), upper(shift(ref("A_END"), 3), "to-a3"),
        any_of(upper(shift(ref("B_END"), -5), "alt-early"), lower(shift(ref("B_END"), 9), "alt-late"))))
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE, "B_END": A_DATE})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INFEASIBLE
    assert set(scan.conflicting_constraints) == {"from-a", "to-a3", "alt-early", "alt-late"}


# ---------------------------------------------------------------------------
# AT-17: renamed entities and an unfamiliar combination
# ---------------------------------------------------------------------------


def _renamed_case(names: dict[str, str]):
    n = names.__getitem__
    constraint = all_of(
        any_of(
            all_of(lower(shift(ref(n("X")), 2, "WEEK"), "k1"), upper(shift(ref(n("X")), 3, "WEEK"), "k2")),
            AtConstraint(id="k3", value=shift(ref(n("Y")), 1, "MONTH")),
        ),
        lower(ref(n("Y")), "k4", inclusive=False),
        upper(LiteralDateValue(value=date(2027, 12, 31), precision="DAY"), "k5"),
    )
    follow = visit(n("F"), OffsetTiming(
        reference=AnchorReference(code=n("DERIVED")), offset=TemporalAmount(value=1, unit="DAY")))
    main = visit(n("V"), constrained(constraint), event_type="LABORATORY")
    derived = anchor(n("DERIVED"), source_event_code=n("V"), derivation_rule={"selection": "FIRST"})
    schedule = schedule_with([main, follow], anchors=[anchor(n("X")), anchor(n("Y")), derived])
    return schedule, context(schedule, anchors={n("X"): date(2027, 2, 1), n("Y"): date(2027, 1, 20)})


def test_at17_unfamiliar_combination_under_new_names_evaluates_identically():
    first = {"X": "ALPHA", "Y": "BETA", "V": "VISIT_ONE", "F": "FOLLOW", "DERIVED": "ONE_DONE"}
    second = {"X": "QZ_MILESTONE", "Y": "KAPPA_7", "V": "WIDGET_CHECK", "F": "ZZ_AFTER",
              "DERIVED": "WIDGET_DONE"}
    outcomes = []
    for names in (first, second):
        schedule, ctx = _renamed_case(names)
        assert blocking_codes(schedule) == []
        result = by_code(evaluate(schedule, ctx))
        main = result[names["V"]][0]
        outcomes.append((
            main.status, main.feasibility,
            [(item.lower.value, item.upper.value, item.lower.sources, item.upper.sources)
             for item in main.feasible_intervals],
            result[names["F"]][0].status,
        ))
    assert outcomes[0] == outcomes[1]
    status, feasibility, intervals, follow_status = outcomes[0]
    assert feasibility == FeasibilityStatus.FEASIBLE
    # X+2w..X+3w, or exactly Y+1 month (inside it); after Y and before the literal.
    assert intervals == [(date(2027, 2, 15), date(2027, 2, 22), ["k1"], ["k2"])]
    # A constrained window has no planned target, so nothing is dated from it.
    assert follow_status == PatientEventStatus.WAITING_FOR_ANCHOR


def test_at17_intervals_are_exact_for_the_unfamiliar_combination():
    schedule, ctx = _renamed_case({"X": "P", "Y": "Q", "V": "R", "F": "S", "DERIVED": "T"})
    main = by_code(evaluate(schedule, ctx))["R"][0]
    spans = [(item.lower.value, item.upper.value) for item in main.feasible_intervals]
    # Y + 1 month = 20 Feb lies inside [15 Feb, 22 Feb], so the union is one interval.
    assert spans == [(date(2027, 2, 15), date(2027, 2, 22))]
    assert main.feasible_intervals[0].lower.sources == ["k1"]
    assert main.feasible_intervals[0].upper.sources == ["k2"]


# ---------------------------------------------------------------------------
# Property-style checks (deterministic seeds)
# ---------------------------------------------------------------------------


class _Stub:
    def __init__(self, anchors):
        self.anchors = anchors

    def anchor(self, value):
        found = self.anchors.get(value.code)
        return Resolved(found) if found is not None else Resolved(None, missing=[])

    def event(self, value):  # pragma: no cover - not used
        return Resolved(None)

    def activity(self, value):  # pragma: no cover - not used
        return Resolved(None)


def _members(result, start: date, days: int) -> set[date]:
    output = set()
    for offset in range(days):
        day = start + timedelta(days=offset)
        for interval in result.intervals:
            low, high = interval.lower.value, interval.upper.value
            if (low is None or day >= low) and (high is None or day <= high):
                output.add(day)
    return output


def _random_leaf(rng: random.Random, tag: str):
    anchor_code = rng.choice(["P", "Q"])
    value = shift(ref(anchor_code), rng.randint(-20, 40))
    kind = rng.choice(["lo", "hi", "at"])
    if kind == "at":
        return AtConstraint(id=tag, value=value)
    maker = lower if kind == "lo" else upper
    return maker(value, tag, inclusive=rng.choice([True, False]))


@pytest.mark.parametrize("seed", range(40))
def test_adding_a_constraint_to_all_never_widens_and_to_any_never_narrows(seed):
    rng = random.Random(seed)
    source = _Stub({"P": date(2027, 1, 1), "Q": date(2027, 1, 1) + timedelta(days=rng.randint(-10, 10))})
    items = [_random_leaf(rng, f"c{index}") for index in range(rng.randint(1, 4))]
    extra = _random_leaf(rng, "extra")
    start = date(2026, 11, 1)
    base_all = _members(evaluate_feasibility([("r", all_of(*items))], source), start, 150)
    more_all = _members(evaluate_feasibility([("r", all_of(*items, extra))], source), start, 150)
    assert more_all <= base_all
    base_any = _members(evaluate_feasibility([("r", any_of(*items))], source), start, 150)
    more_any = _members(evaluate_feasibility([("r", any_of(*items, extra))], source), start, 150)
    assert base_any <= more_any


@pytest.mark.parametrize("seed", range(20))
def test_evaluation_is_deterministic_and_order_independent_for_all(seed):
    rng = random.Random(1000 + seed)
    source = _Stub({"P": date(2027, 1, 1), "Q": date(2027, 1, 9)})
    items = [_random_leaf(rng, f"c{index}") for index in range(4)]
    first = evaluate_feasibility([("r", all_of(*items))], source)
    again = evaluate_feasibility([("r", all_of(*items))], source)
    shuffled = list(items)
    rng.shuffle(shuffled)
    reordered = evaluate_feasibility([("r", all_of(*shuffled))], source)
    assert first == again
    assert first.status == reordered.status
    assert [(i.lower.value, i.upper.value) for i in first.intervals] == [
        (i.lower.value, i.upper.value) for i in reordered.intervals]


def test_schedule_evaluation_is_deterministic_nf01():
    schedule = disjoint_schedule()
    ctx = context(schedule, anchors={"A_END": A_DATE})
    first = evaluate(schedule, ctx).model_dump(mode="json", exclude={"evaluated_at"})
    second = evaluate(schedule, ctx).model_dump(mode="json", exclude={"evaluated_at"})
    assert first == second


# ---------------------------------------------------------------------------
# RL-06: three-valued applicability (reusing condition.py)
# ---------------------------------------------------------------------------


def test_nested_predicates_keep_unknown_and_never_become_not_applicable():
    missing_field = ComparisonCondition(
        operator="GTE", left=FieldOperand(field="age"), right=LiteralOperand(value=65))
    nested = AllCondition(conditions=[
        AnyCondition(conditions=[ExistsCondition(field="never_set"), missing_field]),
        NotCondition(condition=ComparisonCondition(
            operator="EQUALS", left=FieldOperand(field="site"), right=LiteralOperand(value="X"))),
    ])
    assert evaluate_condition(nested, {"site": "Y"}) == TruthValue.UNKNOWN
    assert evaluate_condition(nested, {"site": "Y", "age": 70}) == TruthValue.TRUE
    assert evaluate_condition(nested, {"site": "X", "age": 70}) == TruthValue.FALSE
    gap = UnsupportedCondition(source_operator="ECOG_TREND_IMPROVING")
    assert evaluate_condition(gap, {}) == TruthValue.UNKNOWN
    assert evaluate_condition(NotCondition(condition=gap), {}) == TruthValue.UNKNOWN

    schedule = at01_schedule()
    schedule.events[0].conditions = [nested]
    schedule.claim_evidence.append(ClaimEvidence(
        evidence_id=schedule.evidence[0].id, claim_type="CONDITION", claim_entity_type="EVENT",
        claim_entity_id=schedule.events[0].id, confidence=0.9))
    scan = only(evaluate(schedule, context(
        schedule, anchors={"A_END": A_DATE, "B_END": A_DATE}, state={"site": "Y"})), "SCAN")
    assert scan.status == PatientEventStatus.WAITING_FOR_CONDITION
    assert scan.feasibility is None  # timing is not evaluated before applicability is known


# ---------------------------------------------------------------------------
# Occurrence selectors, activities, time precision, calendar policy
# ---------------------------------------------------------------------------


def dose_series(spacing_accepts: str = "ACTUAL") -> UniversalSchedule:
    event = visit(
        "DOSE", OffsetTiming(reference=AnchorReference(code="A_END"), offset=TemporalAmount(value=0, unit="DAY")),
        event_type="TREATMENT",
        window=Window(before=NonNegativeTemporalAmount(value=3, unit="DAY"),
                      after=NonNegativeTemporalAmount(value=3, unit="DAY")),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=7, unit="DAY"),
            start_reference=AnchorReference(code="A_END"),
            termination=RecurrenceTermination(type="COUNT", count=3)),
        constraints=lower(shift(EventValue(
            event_code="DOSE", occurrence=OccurrenceSelector(type="PREVIOUS"),
            accepts=spacing_accepts), 7), "spacing"))
    return schedule_with([event], anchors=[anchor("A_END")])


def test_previous_occurrence_spacing_uses_actual_prior_dose_per_occurrence():
    schedule = dose_series()
    assert blocking_codes(schedule) == []
    late_first = A_DATE + timedelta(days=2)
    doses = by_code(evaluate(schedule, context(
        schedule, anchors={"A_END": A_DATE}, actual_event_values={"DOSE": [late_first]})))["DOSE"]
    first, second, third = doses
    # First dose: no previous dose, the spacing restricts nothing.
    assert first.feasibility == FeasibilityStatus.FEASIBLE
    assert (first.timing.earliest, first.timing.latest) == (A_DATE - timedelta(days=3), A_DATE + timedelta(days=3))
    # Second dose: window day 4..10, but >= actual first dose + 7 = day 9.
    assert second.feasibility == FeasibilityStatus.FEASIBLE
    assert (second.timing.earliest, second.timing.latest) == (A_DATE + timedelta(days=9), A_DATE + timedelta(days=10))
    assert second.feasible_intervals[0].lower.sources == ["spacing"]
    # Third dose: the second dose has not actually happened - waiting, not dated.
    assert third.feasibility == FeasibilityStatus.INCOMPLETE
    assert third.status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert third.missing_inputs[0].reason == "NOT_RECORDED"


def test_previous_occurrence_may_use_the_planned_prior_target_when_allowed():
    schedule = dose_series("PLANNED_OR_ACTUAL")
    doses = by_code(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})))["DOSE"]
    assert [item.feasibility for item in doses] == [FeasibilityStatus.FEASIBLE] * 3
    # Planned targets are 7 days apart, so the spacing never binds against the window start.
    assert doses[2].timing.earliest == A_DATE + timedelta(days=14)


def test_activity_constraint_waits_for_actual_dose_time_then_resolves():
    dose = Activity(code="DOSE", protocol_label="Dose", display_name="Dose", activity_type="TREATMENT")
    sample = Activity(
        code="PK", protocol_label="PK", display_name="PK sample", activity_type="LABORATORY",
        timing=constrained(all_of(
            lower(shift(ActivityValue(activity_code="DOSE"), 2, "HOUR"), "pk-min"),
            upper(shift(ActivityValue(activity_code="DOSE"), 4, "HOUR"), "pk-max"))))
    event = visit("DAY_1", OffsetTiming(reference=AnchorReference(code="A_END"),
                                        offset=TemporalAmount(value=0, unit="DAY")),
                  activities=[dose, sample])
    schedule = schedule_with([event], anchors=[anchor("A_END")])
    assert blocking_codes(schedule) == []
    waiting = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "DAY_1")
    pk = next(item for item in waiting.activities if item.activity_code == "PK")
    assert pk.status == PatientActivityStatus.WAITING_FOR_ANCHOR
    assert pk.feasibility == FeasibilityStatus.INCOMPLETE
    assert pk.missing_inputs[0].kind == "ACTIVITY" and pk.missing_inputs[0].code == "DOSE"

    dosed = datetime(2027, 1, 4, 9, 0)
    resolved = only(evaluate(schedule, context(
        schedule, anchors={"A_END": A_DATE}, activity_actuals={"DAY_1#0#DOSE": dosed})), "DAY_1")
    pk = next(item for item in resolved.activities if item.activity_code == "PK")
    assert pk.status == PatientActivityStatus.RESOLVED
    assert (pk.timing.earliest, pk.timing.latest) == (dosed + timedelta(hours=2), dosed + timedelta(hours=4))


def test_hour_shift_of_a_date_only_anchor_waits_for_time_precision():
    schedule = at01_schedule(lower(shift(ref("A_END"), 2, "HOUR"), "two-hours"))
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INCOMPLETE
    assert scan.missing_inputs[0].reason == "TIME_PRECISION"
    # With a recorded time the same rule resolves.
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": datetime(2027, 1, 4, 8, 30)})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.FEASIBLE
    assert scan.feasible_intervals[0].lower.value == datetime(2027, 1, 4, 10, 30)


def test_month_end_clamp_requires_a_stated_calendar_policy():
    schedule = at01_schedule(lower(shift(ref("A_END"), 1, "MONTH"), "one-month"))
    end_of_january = date(2027, 1, 31)
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": end_of_january})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.INCOMPLETE
    assert scan.missing_inputs[0].reason == "CALENDAR_POLICY_REQUIRED"
    # No clamp needed: calendar months, not 30 days.
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": date(2027, 1, 15)})), "SCAN")
    assert scan.feasible_intervals[0].lower.value == date(2027, 2, 15)
    stated = at01_schedule(lower(shift(ref("A_END"), 1, "MONTH", "MONTH_END_CLAMP.v1"), "one-month"))
    scan = only(evaluate(stated, context(stated, anchors={"A_END": end_of_january})), "SCAN")
    assert scan.feasible_intervals[0].lower.value == date(2027, 2, 28)


def test_mixed_date_and_datetime_bounds_treat_a_date_as_the_whole_day():
    schedule = at01_schedule(all_of(
        lower(ref("A_END"), "from-a"),
        upper(LiteralDateValue(value=date(2027, 1, 4), precision="DAY"), "by-day")))
    morning = datetime(2027, 1, 4, 9, 0)
    scan = only(evaluate(schedule, context(schedule, anchors={"A_END": morning})), "SCAN")
    assert scan.feasibility == FeasibilityStatus.FEASIBLE
    [interval] = scan.feasible_intervals
    assert interval.lower.value == morning
    assert interval.upper.value == date(2027, 1, 4) and interval.upper.inclusive is True


# ---------------------------------------------------------------------------
# Reminders and deviation respect feasibility
# ---------------------------------------------------------------------------


def test_incomplete_and_infeasible_never_produce_reminders_or_due_states():
    schedule = at01_schedule()
    cases = {
        "incomplete": {"A_END": A_DATE},
        "infeasible": {"A_END": A_DATE, "B_END": date(2027, 3, 1)},
        "feasible": {"A_END": A_DATE, "B_END": date(2027, 1, 18)},
    }
    projected = []
    for key, anchors in cases.items():
        scan = only(evaluate(schedule, context(schedule, anchors=anchors)), "SCAN")
        status, windows = feasibility_from_explanation(scan.explanation)
        projected.append(ProjectedEvent(
            logical_key=f"SCAN:{key}", event_code="SCAN", event_type="IMAGING", display_name="Scan",
            status=scan.status.value,
            nominal_date=scan.timing.nominal_start if scan.timing else None,
            earliest_date=scan.timing.earliest if scan.timing else None,
            latest_date=scan.timing.latest if scan.timing else None,
            feasibility=status, feasible_windows=windows))
        deviation = assess_visit(
            logical_key="SCAN:0", event_code="SCAN", display_name="Scan", status=scan.status.value,
            planned_date=None,
            earliest_date=scan.timing.earliest if scan.timing else None,
            latest_date=scan.timing.latest if scan.timing else None,
            actual_date=None, today=date(2027, 9, 1), feasibility=status)
        assert deviation.deviation_type == (
            DeviationType.MISSED if key == "feasible" else DeviationType.NOT_ASSESSABLE)
    result = project(projected, today=date(2027, 5, 1))
    assert [item.logical_key for item in result.reminders] == ["SCAN:feasible"]
    reminder = result.reminders[0]
    # Earliest permitted day of the first interval - not a midpoint.
    assert reminder.nominal_date == date(2027, 1, 18) + timedelta(weeks=12)
    assert {item.logical_key for item in result.suppressed} == {"SCAN:incomplete", "SCAN:infeasible"}

    # Even a caller that still labels the row RESOLVED cannot remind about it.
    stale = projected[0].model_copy(update={"status": "RESOLVED", "earliest_date": A_DATE})
    assert project([stale], today=A_DATE).reminders == []


def test_legacy_event_without_constraints_carries_no_feasibility_nf08():
    schedule = approved([visit("DAY_7", OffsetTiming(
        reference=AnchorReference(code="A_END"), offset=TemporalAmount(value=7, unit="DAY")))],
        anchors=[anchor("A_END")])
    day_7 = only(evaluate(schedule, context(schedule, anchors={"A_END": A_DATE})), "DAY_7")
    assert day_7.feasibility is None and day_7.feasible_intervals == []
    assert "feasibility" not in day_7.explanation
    assert day_7.timing.nominal_start == A_DATE + timedelta(days=7)
    assert day_7.timing.constraints == []


# ---------------------------------------------------------------------------
# RL-13 validator
# ---------------------------------------------------------------------------


def test_validator_blocks_invalid_constraint_references():
    schedule = at01_schedule(all_of(
        lower(ref("NOPE"), "bad-anchor"),
        lower(EventValue(event_code="GHOST", occurrence=OccurrenceSelector(type="FIRST")), "bad-event"),
        lower(ActivityValue(activity_code="NONE"), "bad-activity"),
        lower(ref("A_END"), "bad-evidence", evidence=[str(uuid4())])))
    issues = ScheduleValidator().validate(schedule)
    by_id = {(item.issue_code, item.details.get("constraint_id")) for item in issues if item.blocking}
    assert ("UNKNOWN_ANCHOR", "bad-anchor") in by_id
    assert ("UNRESOLVED_REFERENCE", "bad-event") in by_id
    assert ("UNRESOLVED_REFERENCE", "bad-activity") in by_id
    assert ("UNRESOLVED_REFERENCE", "bad-evidence") in by_id


def test_validator_accepts_node_level_evidence_instead_of_a_claim():
    schedule = approved([visit("SCAN", constrained(lower(ref("A_END"), "a")))], anchors=[anchor("A_END")])
    assert "MISSING_EVIDENCE" in blocking_codes(schedule)
    schedule.events[0].timing.constraint.evidence_refs = [str(schedule.evidence[0].id)]
    assert blocking_codes(schedule) == []


def test_validator_blocks_same_occurrence_self_reference_but_allows_previous():
    current = EventValue(event_code="SCAN", occurrence=OccurrenceSelector(type="CURRENT"))
    schedule = at01_schedule(lower(shift(current, 1), "self"))
    assert "CONSTRAINT_DEPENDENCY_CYCLE" in blocking_codes(schedule)
    assert blocking_codes(dose_series()) == []


def test_validator_blocks_cross_event_constraint_cycles():
    first = visit("ONE", constrained(lower(EventValue(
        event_code="TWO", occurrence=OccurrenceSelector(type="FIRST"), accepts="PLANNED_OR_ACTUAL"), "one")))
    second = visit("TWO", constrained(lower(EventValue(
        event_code="ONE", occurrence=OccurrenceSelector(type="FIRST"), accepts="PLANNED_OR_ACTUAL"), "two")))
    schedule = schedule_with([first, second], anchors=[anchor("A_END")])
    assert "CIRCULAR_DEPENDENCY" in blocking_codes(schedule)


def test_validator_blocks_activity_constraint_cycles():
    left = Activity(code="L", protocol_label="L", display_name="L", activity_type="X",
                    constraints=lower(ActivityValue(activity_code="R"), "l"))
    right = Activity(code="R", protocol_label="R", display_name="R", activity_type="X",
                     constraints=lower(ActivityValue(activity_code="L"), "r"))
    event = visit("DAY_1", OffsetTiming(reference=AnchorReference(code="A_END"),
                                        offset=TemporalAmount(value=0, unit="DAY")),
                  activities=[left, right])
    schedule = schedule_with([event], anchors=[anchor("A_END")])
    assert "CONSTRAINT_DEPENDENCY_CYCLE" in blocking_codes(schedule)


def test_validator_detects_statically_infeasible_constant_constraints():
    schedule = at01_schedule(all_of(
        lower(shift(ref("A_END"), 16, "WEEK"), "too-late"),
        upper(shift(ref("A_END"), 12, "WEEK"), "too-early")))
    [issue] = issues_by_code(schedule, "CONSTRAINT_INFEASIBLE")
    assert issue.blocking and issue.details["conflicting_constraints"] == ["too-early", "too-late"]
    # Against the legacy timing's own window as well.
    event = visit("SCAN", RangeTiming(
        reference=AnchorReference(code="A_END"),
        start=TemporalAmount(value=12, unit="WEEK"), end=TemporalAmount(value=16, unit="WEEK")),
        constraints=lower(shift(ref("A_END"), 20, "WEEK"), "after-window"))
    schedule = schedule_with([event], anchors=[anchor("A_END")])
    [issue] = issues_by_code(schedule, "CONSTRAINT_INFEASIBLE")
    assert issue.details["conflicting_constraints"] == ["SCAN/timing", "after-window"]
    # Literals.
    schedule = at01_schedule(all_of(
        lower(LiteralDateValue(value=date(2027, 5, 1), precision="DAY"), "from-may"),
        upper(LiteralDateValue(value=date(2027, 4, 1), precision="DAY"), "to-april")))
    assert "CONSTRAINT_INFEASIBLE" in blocking_codes(schedule)


def test_validator_does_not_flag_patient_dependent_or_alternative_feasibility():
    # Depends on two different anchors: only patient data can decide.
    assert "CONSTRAINT_INFEASIBLE" not in blocking_codes(at01_schedule())
    # One alternative is always infeasible, the other is fine.
    schedule = at01_schedule(any_of(
        all_of(lower(shift(ref("A_END"), 9), "x-lo"), upper(shift(ref("A_END"), 2), "x-hi")),
        all_of(lower(shift(ref("A_END"), 1), "y-lo"), upper(shift(ref("A_END"), 2), "y-hi"))))
    assert "CONSTRAINT_INFEASIBLE" not in blocking_codes(schedule)


def test_validator_blocks_month_precision_literal_bounds():
    schedule = at01_schedule(upper(LiteralDateValue(value=date(2027, 3, 1), precision="MONTH"), "by-march"))
    assert "CONSTRAINT_PRECISION_INSUFFICIENT" in blocking_codes(schedule)


def test_literal_precision_must_match_its_value():
    with pytest.raises(ValueError):
        LiteralDateValue(value=datetime(2027, 1, 1, 9), precision="DAY")
    with pytest.raises(ValueError):
        OccurrenceSelector(type="NUMBER")
    with pytest.raises(ValueError):
        LowerBoundConstraint(value=ref("A_END"))  # inclusivity is never defaulted


# ---------------------------------------------------------------------------
# Persistence round trip, diff and presentation
# ---------------------------------------------------------------------------


def test_constraints_survive_persist_draft_get_and_replace_event():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import models as db
    from app.db.base import Base
    from app.db.repositories import CONSTRAINTS_METADATA_KEY, ScheduleRepository

    sample = Activity(
        code="PK", protocol_label="PK", display_name="PK", activity_type="LABORATORY",
        metadata={"note": "kept"},
        constraints=lower(shift(ActivityValue(activity_code="DOSE"), 2, "HOUR"), "pk-min"))
    dose = Activity(code="DOSE", protocol_label="Dose", display_name="Dose", activity_type="TREATMENT")
    scan = visit("SCAN", constrained(disjoint_schedule().events[0].timing.constraint),
                 constraints=lower(shift(ref("B_END"), 12, "WEEK"), "b-min"),
                 metadata={"origin": "test"}, activities=[dose, sample])
    schedule = schedule_with([scan], anchors=[anchor("A_END"), anchor("B_END")])
    schedule.schedule_metadata.status = ScheduleStatus.DRAFT
    version_id = uuid4()
    schedule.schedule_metadata.protocol_version_id = version_id
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    trial_id, protocol_id, definition_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all([
            db.Trial(id=trial_id, organization_id=uuid4()),
            db.Protocol(id=protocol_id, trial_id=trial_id, protocol_number="P-1"),
            db.ProtocolVersion(id=version_id, protocol_id=protocol_id, version_label="1",
                               document_name="p.pdf", document_uri="private://p.pdf",
                               document_hash="a" * 64),
            db.ScheduleDefinition(id=definition_id, protocol_version_id=version_id, name="Primary"),
        ])
        session.flush()
        repository = ScheduleRepository(session)
        repository.persist_draft(schedule, schedule_definition_id=definition_id)
        session.commit()
        loaded = repository.get(schedule.schedule_version_id)
        [event] = loaded.events
        assert event.timing == scan.timing
        assert event.constraints == scan.constraints
        assert event.metadata == {"origin": "test"}  # reserved key never leaks
        pk = next(item for item in event.activities if item.code == "PK")
        assert pk.constraints == sample.constraints and pk.metadata == {"note": "kept"}
        row = session.get(db.Event, scan.id)
        assert CONSTRAINTS_METADATA_KEY in row.metadata_json

        changed = event.model_copy(update={
            "constraints": lower(shift(ref("B_END"), 10, "WEEK"), "b-min"),
            "visit_mode": "TELEPHONE", "allowed_visit_modes": ["TELEPHONE", "VIDEO"],
            "activation": "ON_DEMAND"})
        repository.replace_event(schedule.schedule_version_id, changed)
        session.commit()
        reloaded = repository.get(schedule.schedule_version_id)
        assert reloaded.events[0].constraints == changed.constraints
        # replace_event used to drop attendance mode and activation on edit.
        assert reloaded.events[0].visit_mode == "TELEPHONE"
        assert reloaded.events[0].allowed_visit_modes == ["TELEPHONE", "VIDEO"]
        assert reloaded.events[0].activation == "ON_DEMAND"
        cleared = reloaded.events[0].model_copy(update={"constraints": None})
        repository.replace_event(schedule.schedule_version_id, cleared)
        session.commit()
        assert repository.get(schedule.schedule_version_id).events[0].constraints is None


def test_diff_reports_constraint_meaning_and_provenance_changes():
    before = at01_schedule()
    after = before.model_copy(deep=True)
    after.events[0].timing.constraint.items[2] = lower(shift(ref("B_END"), 10, "WEEK"), "b-min")
    [change] = [item for item in compare_schedule_versions(before, after).typed_changes
                if item.change_type == ChangeType.TIMING_CHANGED]
    assert change.significance == Significance.CLINICAL

    legacy = visit("SCAN", RangeTiming(
        reference=AnchorReference(code="A_END"),
        start=TemporalAmount(value=12, unit="WEEK"), end=TemporalAmount(value=16, unit="WEEK")),
        constraints=all_of(lower(ref("B_END"), "x"), upper(ref("C_END"), "y")))
    left = schedule_with([legacy], anchors=[anchor("A_END"), anchor("B_END"), anchor("C_END")])
    meaning = left.model_copy(deep=True)
    meaning.events[0].constraints = all_of(lower(ref("B_END"), "x"), upper(shift(ref("C_END"), 1), "y"))
    diff = compare_schedule_versions(left, meaning)
    assert [item.change_type for item in diff.typed_changes] == [ChangeType.CONSTRAINTS_CHANGED]
    assert diff.clinically_significant()
    assert [item.field_path for item in diff.changes] == ["constraints"]

    provenance = left.model_copy(deep=True)
    provenance.events[0].constraints = all_of(upper(ref("C_END"), "y2"), lower(ref("B_END"), "x2"))
    diff = compare_schedule_versions(left, provenance)
    assert [item.change_type for item in diff.typed_changes] == [ChangeType.CONSTRAINT_PROVENANCE_CHANGED]
    assert diff.clinically_significant() == []


def test_presentation_shows_every_restriction():
    [row] = project_schedule(disjoint_schedule())
    assert row.timing_display == (
        "(on or after A End + 7 days and on or before A End + 14 days) or "
        "(on or after A End + 28 days and on or before A End + 35 days)")
    assert "A End + 28 days" in row.timing_display and "A End + 14 days" in row.timing_display
    event = visit("SCAN", RangeTiming(
        reference=AnchorReference(code="A_END"),
        start=TemporalAmount(value=12, unit="WEEK"), end=TemporalAmount(value=16, unit="WEEK")),
        constraints=lower(shift(ref("B_END"), 12, "WEEK"), "b-min"))
    [row] = project_schedule(schedule_with([event], anchors=[anchor("A_END"), anchor("B_END")]))
    assert row.constraints_display == "on or after B End + 12 weeks"


def test_constraint_view_gives_every_node_a_summary_for_generic_formatters():
    view = constraint_view(disjoint_schedule().events[0].timing.constraint, "SCAN/timing/constraint")
    assert view["type"] == "ANY" and view["summary"].startswith("(on or after A End + 7 days")
    first = view["items"][0]
    assert first["id"] == "SCAN/timing/constraint/items/0" and first["type"] == "ALL"
    leaf = first["items"][0]
    assert leaf == {
        "id": "early-from", "path": "SCAN/timing/constraint/items/0/items/0",
        "type": "LOWER_BOUND", "summary": "on or after A End + 7 days",
        "inclusive": True, "value_summary": "A End + 7 days",
        "references": [{"kind": "ANCHOR", "code": "A_END", "accepts": "ACTUAL"}],
        "evidence_refs": [], "items": [],
    }
