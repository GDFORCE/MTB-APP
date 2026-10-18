"""P4-01..P4-05: conditional dependency outcomes and explicit recurrence basis.

Fixed dates, explicit state, injected context - no clock. Synthetic schedules
only. Each test names the defect or rule it pins.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand, TruthValue
from app.domain.schedule.dependency import DependencyOutcomeKind, aggregate, evaluate_dependency
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, Dependency, DependencyMode, Event, LogicalOccurrenceState, PatientContext,
    PatientEventStatus, RecurrenceBasis, RecurrenceRule, RecurrenceTermination,
    ScheduleMetadata, UniversalSchedule,
)
from app.domain.schedule.validator import ScheduleValidator
from test_uctsm_rolling_horizon import approved

BASELINE = date(2026, 1, 1)
HORIZON = date(2026, 12, 31)
PREGNANT = ComparisonCondition(
    operator="EQUALS", left=FieldOperand(field="pregnant"), right=LiteralOperand(value=True))


def anchors(*codes):
    return [Anchor(code=code, display_name=code.title(), anchor_type=code, status="RESOLVED")
            for code in codes]


def offset_event(code, days, *, anchor="BASELINE", **extra):
    return Event(
        code=code, protocol_label=code, display_name=code.title(), event_type="VISIT",
        timing={"type": "OFFSET", "reference": {"kind": "ANCHOR", "code": anchor},
                "offset": {"value": days, "unit": "DAY"}}, **extra)


def build(events, *, anchor_codes=("BASELINE",)):
    schedule = approved(events)
    from app.domain.schedule.models import ClaimEvidence
    evidence_id = schedule.evidence[0].id
    for event in events:
        if event.conditions:
            schedule.claim_evidence.append(ClaimEvidence(
                evidence_id=evidence_id, claim_type="CONDITION", claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path="conditions", confidence=0.9))
        if event.dependencies:
            if event.dependency_mode is None:
                event.dependency_mode = DependencyMode.NOMINAL
            for claim_type, path in (("DEPENDENCY", "dependencies"),
                                     ("DEPENDENCY_MODE", "dependency_mode")):
                schedule.claim_evidence.append(ClaimEvidence(
                    evidence_id=evidence_id, claim_type=claim_type, claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path=path, confidence=0.9))
    schedule.anchors = anchors(*anchor_codes)
    return schedule


def run(schedule, *, anchors_=None, state=None, history=(), horizon=HORIZON, **context):
    patient = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"BASELINE": BASELINE} if anchors_ is None else anchors_,
        state=state or {}, occurrence_history=list(history), **context)
    return ScheduleEvaluator().evaluate(schedule, patient, horizon=horizon, today=BASELINE)


def of(result, code):
    return [item for item in result.events if item.event_code == code]


def dates(result, code):
    return [item.timing.nominal_start for item in of(result, code) if item.timing is not None]


# ============================================================ P4-01 dependencies

def dependency_schedule(condition=PREGNANT, *, mode=None, source_applicable=True, dep_type="PRECONDITION"):
    """TARGET depends on PREG_TEST (conditional on pregnancy). PREG_TEST counts
    from an anchor the patient may not have yet."""
    source = offset_event("PREG_TEST", 0, anchor="SCREENING")
    if not source_applicable:
        source.conditions = [ComparisonCondition(
            operator="EQUALS", left=FieldOperand(field="never"), right=LiteralOperand(value=True))]
    target = offset_event(
        "TARGET", 7, dependencies=[Dependency(
            source_event_code="PREG_TEST", dependency_type=dep_type, condition=condition)],
        **({"dependency_mode": mode} if mode else {}))
    return build([source, target], anchor_codes=("BASELINE", "SCREENING"))


def test_false_condition_removes_the_dependency_so_it_cannot_block():
    result = run(dependency_schedule(), state={"pregnant": False})   # PREG_TEST has no SCREENING anchor
    target = of(result, "TARGET")[0]
    assert target.status == PatientEventStatus.RESOLVED
    assert target.timing.nominal_start == date(2026, 1, 8)
    detail = target.dependency_result["dependencies"][0]
    assert detail["outcome"] == "NOT_APPLICABLE" and detail["condition_result"] == "FALSE"
    assert of(result, "PREG_TEST")[0].status == PatientEventStatus.WAITING_FOR_ANCHOR


def test_unknown_condition_waits_for_the_condition_and_names_the_missing_input():
    target = of(run(dependency_schedule(), state={}), "TARGET")[0]
    assert target.status == PatientEventStatus.WAITING_FOR_CONDITION
    detail = target.dependency_result["dependencies"][0]
    assert detail["outcome"] == "WAITING_FOR_CONDITION" and detail["condition_result"] == "UNKNOWN"
    assert detail["missing_inputs"] == ["pregnant"]
    assert target.timing is None            # unknown is never coerced to false


def test_true_condition_with_the_source_still_absent_waits_for_the_source():
    target = of(run(dependency_schedule(), state={"pregnant": True}), "TARGET")[0]
    assert target.status == PatientEventStatus.BLOCKED
    assert target.dependency_result["blocked_by"] == ["PREG_TEST"]
    detail = target.dependency_result["dependencies"][0]
    assert detail["outcome"] == "WAITING_FOR_SOURCE"
    assert detail["source_status"] == "WAITING_FOR_ANCHOR"


def test_true_condition_with_a_resolved_source_is_satisfied():
    result = run(dependency_schedule(), anchors_={"BASELINE": BASELINE, "SCREENING": date(2025, 12, 20)},
                 state={"pregnant": True})
    target = of(result, "TARGET")[0]
    assert target.status == PatientEventStatus.RESOLVED
    assert target.dependency_result["dependencies"][0]["outcome"] == "SATISFIED"


def test_an_applicable_dependency_on_a_source_that_can_never_exist_is_blocked():
    schedule = dependency_schedule(source_applicable=False)
    result = run(schedule, anchors_={"BASELINE": BASELINE, "SCREENING": date(2025, 12, 20)},
                 state={"pregnant": True, "never": False})
    assert of(result, "PREG_TEST")[0].status == PatientEventStatus.NOT_APPLICABLE
    target = of(result, "TARGET")[0]
    assert target.status == PatientEventStatus.BLOCKED
    assert target.dependency_result["dependencies"][0]["outcome"] == "BLOCKED"


def test_unconditional_dependency_on_a_resolved_source_is_satisfied():
    result = run(dependency_schedule(condition=None),
                 anchors_={"BASELINE": BASELINE, "SCREENING": date(2025, 12, 20)})
    assert of(result, "TARGET")[0].dependency_result["dependencies"][0]["outcome"] == "SATISFIED"


@pytest.mark.parametrize("mode", [DependencyMode.ACTUAL_PREVIOUS_EVENT, DependencyMode.MANUAL])
def test_an_inactive_dependency_is_not_asked_for_an_actual_or_a_manual_date(mode):
    result = run(dependency_schedule(mode=mode), state={"pregnant": False})
    target = of(result, "TARGET")[0]
    assert target.status == PatientEventStatus.RESOLVED, target.dependency_result


def test_an_active_dependency_in_actual_mode_still_waits_for_its_actual():
    result = run(dependency_schedule(mode=DependencyMode.ACTUAL_PREVIOUS_EVENT),
                 anchors_={"BASELINE": BASELINE, "SCREENING": date(2025, 12, 20)},
                 state={"pregnant": True})
    target = of(result, "TARGET")[0]
    assert target.status == PatientEventStatus.BLOCKED
    assert target.dependency_result["waiting_for_actual"] == ["PREG_TEST"]


def test_a_false_dependency_does_not_supply_a_date_the_events_own_timing_needs():
    """Dependency not applicable != event not applicable: TARGET's own timing
    counts from PREG_TEST, so it still waits for it."""
    source = offset_event("PREG_TEST", 0, anchor="SCREENING")
    target = Event(
        code="TARGET", protocol_label="T", display_name="Target", event_type="VISIT",
        timing={"type": "OFFSET", "offset": {"value": 3, "unit": "DAY"},
                "reference": {"kind": "EVENT", "event_code": "PREG_TEST",
                              "temporal_basis": "CURRENT_PLANNED"}},
        dependencies=[Dependency(source_event_code="PREG_TEST", dependency_type="TEMPORAL",
                                 condition=PREGNANT)])
    schedule = build([source, target], anchor_codes=("BASELINE", "SCREENING"))
    result = run(schedule, state={"pregnant": False})
    item = of(result, "TARGET")[0]
    assert item.status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert item.status != PatientEventStatus.NOT_APPLICABLE
    assert item.dependency_result["dependencies"][0]["outcome"] == "NOT_APPLICABLE"


def test_mixed_dependencies_aggregate_strongest_first():
    def outcome(kind):
        from app.domain.schedule.dependency import DependencyOutcome
        return DependencyOutcome("X", "TEMPORAL", kind, "r")
    K = DependencyOutcomeKind
    assert aggregate([outcome(K.NOT_APPLICABLE), outcome(K.SATISFIED)]) == K.SATISFIED
    assert aggregate([outcome(K.WAITING_FOR_SOURCE), outcome(K.WAITING_FOR_CONDITION)]) == K.WAITING_FOR_CONDITION
    assert aggregate([outcome(K.WAITING_FOR_CONDITION), outcome(K.BLOCKED)]) == K.BLOCKED
    assert aggregate([outcome(K.NOT_APPLICABLE)]) == K.NOT_APPLICABLE


def test_dependency_evaluation_uses_the_one_truth_value_implementation():
    dep = Dependency(source_event_code="S", dependency_type="PRECONDITION", condition=PREGNANT)
    resolved = [PatientEventStatus.RESOLVED]
    assert evaluate_dependency(dep, state={"pregnant": False}, source_statuses=resolved).outcome \
        == DependencyOutcomeKind.NOT_APPLICABLE
    assert evaluate_dependency(dep, state={"pregnant": True}, source_statuses=resolved).outcome \
        == DependencyOutcomeKind.SATISFIED
    assert TruthValue.UNKNOWN.value == "UNKNOWN"


def test_a_dependency_cycle_is_structural_even_when_its_condition_is_false_today():
    a = offset_event("A", 1, dependencies=[Dependency(
        source_event_code="B", dependency_type="TEMPORAL", condition=PREGNANT)])
    b = offset_event("B", 2, dependencies=[Dependency(
        source_event_code="A", dependency_type="TEMPORAL")])
    codes = [issue.issue_code for issue in ScheduleValidator().validate(build([a, b]))]
    assert "CIRCULAR_DEPENDENCY" in codes
    own = offset_event("SELF", 1, dependencies=[Dependency(
        source_event_code="SELF", dependency_type="TEMPORAL")])
    assert "CIRCULAR_DEPENDENCY" in [
        issue.issue_code for issue in ScheduleValidator().validate(build([own]))]


# ============================================================ P4-02/03 recurrence

def repeating(basis, *, first_days=14, interval=(28, "DAY"), start_offset=None,
              include_start=True, termination=None, actual_source=None, anchor_day=None, status=None):
    rule = RecurrenceRule(
        interval={"value": interval[0], "unit": interval[1]},
        start_reference={"kind": "ANCHOR", "code": "BASELINE"},
        start_offset=start_offset, include_start=include_start,
        termination=termination or RecurrenceTermination(type="HORIZON"),
        basis=basis, actual_source=actual_source, basis_status=status)
    return build([offset_event("LABS", first_days, recurrence=rule)])


def test_offset_first_series_is_not_restarted_at_the_anchor_and_the_offset_is_not_doubled():
    """PDF scenario: baseline 1 Jan 2026, first +14 days, every 28 days."""
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE,
                         termination=RecurrenceTermination(type="COUNT", count=3))
    assert dates(run(schedule), "LABS") == [date(2026, 1, 15), date(2026, 2, 12), date(2026, 3, 12)]
    assert [item.explanation["recurrence"]["basis"]["basis"] for item in of(run(schedule), "LABS")] \
        == ["FIRST_RESOLVED_OCCURRENCE"] * 3


def test_a_restated_matching_start_offset_gives_the_same_dates():
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE,
                         start_offset={"value": 14, "unit": "DAY"},
                         termination=RecurrenceTermination(type="COUNT", count=3))
    assert dates(run(schedule), "LABS") == [date(2026, 1, 15), date(2026, 2, 12), date(2026, 3, 12)]
    assert not [i for i in ScheduleValidator().validate(schedule) if i.issue_code == "RECURRENCE_START_CONFLICT"]


def test_a_start_offset_that_disagrees_with_the_event_timing_blocks_and_moves_nothing():
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE,
                         start_offset={"value": 7, "unit": "DAY"},
                         termination=RecurrenceTermination(type="COUNT", count=3))
    issues = [i for i in ScheduleValidator().validate(schedule) if i.issue_code == "RECURRENCE_START_CONFLICT"]
    assert issues and issues[0].blocking
    # approval validation refuses it, and so does the engine's integrity re-check
    with pytest.raises(ValueError, match="integrity"):
        run(schedule)


def test_defect_pre_p4_rule_starting_at_the_anchor_keeps_its_dates_but_is_flagged():
    """The confirmed defect (series restarted at the raw anchor when the first
    offset lives only on the event). Legacy dates are NOT moved; the conflict is
    explained and approving a new version with it is blocked."""
    schedule = repeating(None, termination=RecurrenceTermination(type="COUNT", count=3))
    result = run(schedule)
    assert dates(result, "LABS") == [date(2026, 1, 1), date(2026, 1, 29), date(2026, 2, 26)]
    note = of(result, "LABS")[0].explanation["recurrence"]["basis"]
    assert note["status"] == "UNSTATED_LEGACY"
    assert note["start_conflict"] == {
        "event_timing": "2026-01-15", "series_start": "2026-01-01", "dates_changed": False}
    strict = [i for i in ScheduleValidator().validate(schedule) if i.blocking]
    assert {"RECURRENCE_BASIS_UNSTATED", "RECURRENCE_START_CONFLICT"} <= {i.issue_code for i in strict}
    tolerant = ScheduleValidator().validate(schedule, evaluating_approved=True)
    assert not [i for i in tolerant if i.issue_code.startswith("RECURRENCE_") and i.blocking]


def test_unambiguous_legacy_rule_is_only_a_warning():
    schedule = repeating(None, first_days=0, start_offset=None,
                         termination=RecurrenceTermination(type="COUNT", count=2))
    issues = [i for i in ScheduleValidator().validate(schedule) if i.issue_code == "RECURRENCE_BASIS_UNSTATED"]
    assert issues and not issues[0].blocking and issues[0].details["legacy_equivalent"] == "FIXED_ANCHOR_SERIES"


def month_series(basis, start=date(2026, 1, 31), count=4):
    schedule = repeating(basis, first_days=0, interval=(1, "MONTH"),
                         termination=RecurrenceTermination(type="COUNT", count=count))
    return dates(run(schedule, anchors_={"BASELINE": start}, horizon=date(2030, 12, 31)), "LABS")


def test_month_end_first_anchored_versus_previous_planned_are_different_series():
    expected_anchored = [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert month_series(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE) == expected_anchored
    assert month_series(RecurrenceBasis.FIXED_ANCHOR_SERIES) == expected_anchored
    assert month_series(RecurrenceBasis.PREVIOUS_PLANNED_OCCURRENCE) == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 28), date(2026, 4, 28)]


def test_leap_february_and_leap_day_anniversary():
    assert month_series(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, date(2028, 1, 31), 3) == [
        date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31)]
    yearly = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, first_days=0, interval=(1, "YEAR"),
                       termination=RecurrenceTermination(type="COUNT", count=3))
    assert dates(run(yearly, anchors_={"BASELINE": date(2028, 2, 29)}, horizon=date(2032, 12, 31)), "LABS") \
        == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28)]


def test_fixed_anchor_series_requires_the_event_timing_to_sit_on_the_grid():
    ok = repeating(RecurrenceBasis.FIXED_ANCHOR_SERIES, first_days=14,
                   start_offset={"value": 14, "unit": "DAY"},
                   termination=RecurrenceTermination(type="COUNT", count=3))
    assert dates(run(ok), "LABS") == [date(2026, 1, 15), date(2026, 2, 12), date(2026, 3, 12)]
    off_grid = repeating(RecurrenceBasis.FIXED_ANCHOR_SERIES, first_days=10,
                         start_offset={"value": 14, "unit": "DAY"},
                         termination=RecurrenceTermination(type="COUNT", count=3))
    assert "RECURRENCE_START_CONFLICT" in [
        i.issue_code for i in ScheduleValidator().validate(off_grid) if i.blocking]
    with pytest.raises(ValueError, match="integrity"):
        run(off_grid)


def test_include_start_false_does_not_renumber_and_skips_only_the_start_slot():
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, include_start=False,
                         termination=RecurrenceTermination(type="COUNT", count=2))
    result = run(schedule)
    assert dates(result, "LABS") == [date(2026, 2, 12), date(2026, 3, 12)]
    assert [item.occurrence_index for item in of(result, "LABS")] == [0, 1]


def history(*states):
    return [LogicalOccurrenceState(event_code="LABS", **item) for item in states]


def test_previous_actual_waits_for_the_actual_and_never_invents_one():
    schedule = repeating(RecurrenceBasis.PREVIOUS_ACTUAL_OCCURRENCE, actual_source="ACTUAL_VISIT",
                         termination=RecurrenceTermination(type="COUNT", count=4))
    result = run(schedule)
    items = of(result, "LABS")
    assert [item.status for item in items] == [
        PatientEventStatus.RESOLVED, PatientEventStatus.WAITING_FOR_ANCHOR]
    assert items[0].timing.nominal_start == date(2026, 1, 15)
    assert items[1].timing is None and items[1].occurrence_index == 1
    waiting = items[1].explanation["recurrence"]["waiting_for_actual_occurrence"]
    assert waiting["occurrence_index"] == 0 and waiting["source"] == "ACTUAL_VISIT"


def test_previous_actual_counts_from_the_actual_and_later_ones_wait_in_turn():
    schedule = repeating(RecurrenceBasis.PREVIOUS_ACTUAL_OCCURRENCE, actual_source="ACTUAL_VISIT",
                         termination=RecurrenceTermination(type="COUNT", count=4))
    result = run(schedule, history=history(
        {"occurrence_index": 0, "actual_visit_date": date(2026, 1, 20)}))
    items = of(result, "LABS")
    assert items[1].timing.nominal_start == date(2026, 2, 17)          # 20 Jan + 28 d
    assert items[1].explanation["recurrence"]["counted_from"] == {
        "from": "ACTUAL_VISIT", "occurrence_index": 0, "date": "2026-01-20"}
    assert items[2].status == PatientEventStatus.WAITING_FOR_ANCHOR and items[2].occurrence_index == 2
    assert len(items) == 3


def test_attendance_without_treatment_does_not_date_a_treatment_series():
    schedule = repeating(RecurrenceBasis.PREVIOUS_ACTUAL_OCCURRENCE, actual_source="ACTUAL_TREATMENT",
                         termination=RecurrenceTermination(type="COUNT", count=3))
    visit_only = run(schedule, history=history(
        {"occurrence_index": 0, "actual_visit_date": date(2026, 1, 20)}))
    assert of(visit_only, "LABS")[1].status == PatientEventStatus.WAITING_FOR_ANCHOR
    treated = run(schedule, history=history(
        {"occurrence_index": 0, "actual_visit_date": date(2026, 1, 20),
         "actual_treatment_date": date(2026, 1, 22)}))
    assert of(treated, "LABS")[1].timing.nominal_start == date(2026, 2, 19)


def test_actual_change_moves_only_the_actual_based_series_not_parallel_baseline_events():
    actual_rule = RecurrenceRule(
        interval={"value": 28, "unit": "DAY"}, start_reference={"kind": "ANCHOR", "code": "BASELINE"},
        termination=RecurrenceTermination(type="COUNT", count=2),
        basis="PREVIOUS_ACTUAL_OCCURRENCE", actual_source="ACTUAL_VISIT")
    schedule = build([offset_event("LABS", 14, recurrence=actual_rule), offset_event("IMAGING", 30)])
    early = run(schedule, history=history({"occurrence_index": 0, "actual_visit_date": date(2026, 1, 15)}))
    late = run(schedule, history=history({"occurrence_index": 0, "actual_visit_date": date(2026, 1, 25)}))
    assert dates(early, "IMAGING") == dates(late, "IMAGING") == [date(2026, 1, 31)]
    assert dates(early, "LABS")[0] == dates(late, "LABS")[0] == date(2026, 1, 15)  # planned, unchanged
    assert dates(early, "LABS")[1] == date(2026, 2, 12) and dates(late, "LABS")[1] == date(2026, 2, 22)


def test_unconfirmed_basis_is_never_dated_and_blocks_approval():
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, status="UNCONFIRMED")
    assert "RECURRENCE_BASIS_UNCONFIRMED" in [
        i.issue_code for i in ScheduleValidator().validate(schedule) if i.blocking]
    with pytest.raises(ValueError, match="integrity"):
        run(schedule)


def test_the_engine_itself_refuses_an_unconfirmed_basis_if_one_ever_reaches_it():
    """Defence in depth: bypass the integrity gate and the rule still never dates."""
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, status="UNCONFIRMED")
    event = schedule.events[0]
    context = PatientContext(patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
                             anchors={"BASELINE": BASELINE})
    from app.domain.schedule.evaluator import ResolvedTiming
    first = ResolvedTiming(nominal_start=date(2026, 1, 15), nominal_end=date(2026, 1, 15),
                           earliest=date(2026, 1, 15), latest=date(2026, 1, 15), precision="EXACT")
    out = ScheduleEvaluator()._expand_recurrence(
        event, first, context, HORIZON, {
            "event_definition_id": event.id, "event_code": event.code, "applicability_result": "TRUE",
            "condition_result": "TRUE", "explanation": {}, "visit_mode": None,
            "allowed_visit_modes": []})
    assert [item.status for item in out] == [PatientEventStatus.UNRESOLVED]
    assert out[0].timing is None


def test_model_rejects_impossible_basis_combinations():
    base = dict(interval={"value": 1, "unit": "WEEK"},
                start_reference={"kind": "ANCHOR", "code": "BASELINE"},
                termination={"type": "HORIZON"})
    with pytest.raises(ValidationError):
        RecurrenceRule(basis="PREVIOUS_ACTUAL_OCCURRENCE", **base)          # no actual source
    with pytest.raises(ValidationError):
        RecurrenceRule(basis="FIRST_RESOLVED_OCCURRENCE", actual_source="ACTUAL_VISIT", **base)
    with pytest.raises(ValidationError):
        RecurrenceRule(actual_source="ACTUAL_VISIT", **base)                # source without basis
    with pytest.raises(ValidationError):
        RecurrenceRule(basis="FIXED_ANCHOR_SERIES", **{**base, "start_reference": {
            "kind": "EVENT", "event_code": "X", "temporal_basis": "CURRENT_PLANNED"}})


def test_basis_round_trips_and_legacy_payloads_load_unchanged():
    schedule = repeating(RecurrenceBasis.PREVIOUS_ACTUAL_OCCURRENCE, actual_source="ACTUAL_TREATMENT")
    restored = UniversalSchedule.model_validate_json(schedule.model_dump_json())
    rule = restored.events[0].recurrence
    assert rule.basis == RecurrenceBasis.PREVIOUS_ACTUAL_OCCURRENCE and rule.actual_source == "ACTUAL_TREATMENT"
    legacy = schedule.model_dump(mode="json")
    for field in ("basis", "basis_status", "actual_source"):
        legacy["events"][0]["recurrence"].pop(field)
    loaded = UniversalSchedule.model_validate(legacy).events[0].recurrence
    assert loaded.basis is None and loaded.basis_status is None


# ============================================================ P4-04/05 termination

def terminated(termination, **kwargs):
    return repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, first_days=0, interval=(7, "DAY"),
                     termination=termination, **kwargs)


def test_count_date_and_horizon_boundaries():
    assert len(dates(run(terminated(RecurrenceTermination(type="COUNT", count=3))), "LABS")) == 3
    stop = RecurrenceTermination(type="DATE", date=date(2026, 1, 15))
    assert dates(run(terminated(stop)), "LABS") == [date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)]
    assert dates(run(terminated(RecurrenceTermination(type="HORIZON")), horizon=date(2026, 1, 14)),
                 "LABS") == [date(2026, 1, 1), date(2026, 1, 8)]


def test_event_termination_uses_the_source_occurrence_and_waits_when_it_has_no_date():
    end = RecurrenceTermination(type="EVENT", event_code="END_VISIT")
    schedule = build([offset_event("END_VISIT", 20), offset_event(
        "LABS", 0, recurrence=RecurrenceRule(
            interval={"value": 7, "unit": "DAY"}, start_reference={"kind": "ANCHOR", "code": "BASELINE"},
            termination=end, basis="FIRST_RESOLVED_OCCURRENCE"))])
    assert dates(run(schedule), "LABS") == [date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)]


def test_condition_termination_is_three_valued_and_needs_an_effective_time():
    condition = RecurrenceTermination(type="CONDITION", condition=ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="progressed"), right=LiteralOperand(value=True)))
    schedule = terminated(condition)
    assert of(run(schedule, state={}), "LABS")[0].status == PatientEventStatus.WAITING_FOR_CONDITION
    assert of(run(schedule, state={"progressed": True}), "LABS")[0].status == PatientEventStatus.UNRESOLVED
    ended = run(schedule, state={"progressed": True},
                state_effective_at={"recurrence:LABS": __import__("datetime").datetime(2026, 1, 10)})
    assert dates(ended, "LABS") == [date(2026, 1, 1), date(2026, 1, 8)]
    assert dates(run(schedule, state={"progressed": False}, horizon=date(2026, 1, 20)), "LABS") == [
        date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)]


def test_the_engine_safety_cap_is_reported_and_not_called_a_protocol_maximum():
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, first_days=0, interval=(1, "DAY"),
                         termination=RecurrenceTermination(type="HORIZON"))
    result = run(schedule, horizon=date(2100, 1, 1))
    items = of(result, "LABS")
    assert len(items) == 10000
    cap = items[-1].explanation["recurrence"]["engine_safety_cap"]
    assert cap["limit"] == 10000 and "not at a protocol-stated end" in cap["note"]
    assert "engine_safety_cap" not in items[0].explanation["recurrence"]


def test_out_of_order_actuals_do_not_shift_logical_identity():
    schedule = repeating(RecurrenceBasis.PREVIOUS_ACTUAL_OCCURRENCE, actual_source="ACTUAL_VISIT",
                         termination=RecurrenceTermination(type="COUNT", count=4))
    result = run(schedule, history=history(
        {"occurrence_index": 1, "actual_visit_date": date(2026, 1, 5)},       # recorded out of order
        {"occurrence_index": 0, "actual_visit_date": date(2026, 1, 20)}))
    items = sorted(of(result, "LABS"), key=lambda item: item.occurrence_index)
    assert [item.occurrence_index for item in items] == [0, 1, 2, 3]
    assert items[1].timing.nominal_start == date(2026, 2, 17)             # from occurrence 0, not 1
    assert items[2].timing.nominal_start == date(2026, 2, 2)              # 5 Jan + 28 d, index 2 stays index 2
    assert items[3].status == PatientEventStatus.WAITING_FOR_ANCHOR


# ===================================================== P5 prerequisite repairs

def test_dependency_selection_event_level_versus_occurrence_level():
    """ANY is event-level (one resolved occurrence satisfies it); FIRST / LAST /
    CURRENT / NUMBER need that particular occurrence."""
    R, W = PatientEventStatus.RESOLVED, PatientEventStatus.WAITING_FOR_ANCHOR
    source = [(0, R), (1, R), (2, W)]            # cycle 3 not dated yet

    def outcome(selector, number=None):
        dep = Dependency(source_event_code="CYCLE", dependency_type="TEMPORAL",
                         source_occurrence=selector, source_occurrence_number=number)
        return evaluate_dependency(dep, state={}, source_statuses=source).outcome
    K = DependencyOutcomeKind
    assert outcome("ANY") == K.SATISFIED
    assert outcome("FIRST") == K.SATISFIED
    assert outcome("LAST") == K.WAITING_FOR_SOURCE
    assert outcome("CURRENT") == K.WAITING_FOR_SOURCE
    assert outcome("NUMBER", 1) == K.SATISFIED
    assert outcome("NUMBER", 2) == K.WAITING_FOR_SOURCE
    assert outcome("NUMBER", 7) == K.WAITING_FOR_SOURCE      # not generated at all
    with pytest.raises(ValidationError):
        Dependency(source_event_code="X", dependency_type="TEMPORAL", source_occurrence="NUMBER")
    with pytest.raises(ValidationError):
        Dependency(source_event_code="X", dependency_type="TEMPORAL", source_occurrence_number=1)


def test_one_completed_cycle_does_not_satisfy_a_later_cycles_dependency_in_the_engine():
    source = offset_event("CYCLE_DOSE", 0, recurrence=RecurrenceRule(
        interval={"value": 7, "unit": "DAY"}, start_reference={"kind": "ANCHOR", "code": "BASELINE"},
        termination=RecurrenceTermination(type="COUNT", count=3), basis="FIRST_RESOLVED_OCCURRENCE",
        basis_status="STATED"))
    target = offset_event("FOLLOW_UP", 30, dependencies=[Dependency(
        source_event_code="CYCLE_DOSE", dependency_type="SEQUENCE",
        source_occurrence="NUMBER", source_occurrence_number=5)])
    result = run(build([source, target]))
    item = of(result, "FOLLOW_UP")[0]
    assert item.status == PatientEventStatus.BLOCKED               # occurrence 5 never exists
    assert item.dependency_result["dependencies"][0]["outcome"] == "WAITING_FOR_SOURCE"


def test_count_above_the_safety_cap_is_reported_incomplete_not_complete():
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, first_days=0, interval=(1, "DAY"),
                         termination=RecurrenceTermination(type="COUNT", count=10001))
    items = of(run(schedule, horizon=date(2100, 1, 1)), "LABS")
    assert len(items) == 10000                                      # the cap holds for COUNT too
    cap = items[-1].explanation["recurrence"]["engine_safety_cap"]
    assert cap["protocol_count"] == 10001 and cap["incomplete_projection"] is True
    exact = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, first_days=0, interval=(1, "DAY"),
                      termination=RecurrenceTermination(type="COUNT", count=10000))
    done = of(run(exact, horizon=date(2100, 1, 1)), "LABS")
    assert len(done) == 10000 and "engine_safety_cap" not in done[-1].explanation["recurrence"]


def test_event_termination_with_an_undated_end_event_is_provisional_not_silent():
    weekly = RecurrenceRule(
        interval={"value": 7, "unit": "DAY"}, start_reference={"kind": "ANCHOR", "code": "BASELINE"},
        termination=RecurrenceTermination(type="EVENT", event_code="STOP"),
        basis="FIRST_RESOLVED_OCCURRENCE", basis_status="STATED")
    schedule = build([offset_event("LABS", 0, recurrence=weekly),
                      offset_event("STOP", 0, anchor="END")], anchor_codes=("BASELINE", "END"))
    items = of(run(schedule, horizon=date(2026, 1, 29)), "LABS")
    assert len(items) == 5
    note = items[0].explanation["recurrence"]["termination"]
    assert note["status"] == "AWAITING_END_EVENT" and note["event_code"] == "STOP" and note["provisional"]
    dated = run(schedule, anchors_={"BASELINE": BASELINE, "END": date(2026, 1, 15)},
                horizon=date(2026, 1, 29), event_values={"STOP": [date(2026, 1, 15)]})
    ended = of(dated, "LABS")
    assert [i.timing.nominal_start for i in ended] == [date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)]
    assert "termination" not in ended[0].explanation["recurrence"]


def test_event_timing_plus_one_month_start_offset_is_blocked_at_approval_not_at_evaluation():
    schedule = repeating(RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE, first_days=14,
                         start_offset={"value": 1, "unit": "MONTH"},
                         termination=RecurrenceTermination(type="COUNT", count=3))
    blockers = [i for i in ScheduleValidator().validate(schedule) if i.blocking]
    unverifiable = [i for i in blockers if i.issue_code == "RECURRENCE_START_UNVERIFIABLE"]
    assert unverifiable and unverifiable[0].details["field_path"].endswith("/start_reference")
    assert unverifiable[0].details["reason"] == "INCOMPATIBLE_UNITS"


def test_fixed_anchor_grid_on_a_different_anchor_is_blocked_at_approval():
    rule = RecurrenceRule(
        interval={"value": 28, "unit": "DAY"}, start_reference={"kind": "ANCHOR", "code": "OTHER"},
        start_offset={"value": 14, "unit": "DAY"}, termination=RecurrenceTermination(type="COUNT", count=3),
        basis="FIXED_ANCHOR_SERIES", basis_status="STATED")
    schedule = build([offset_event("LABS", 14, recurrence=rule)], anchor_codes=("BASELINE", "OTHER"))
    issues = [i for i in ScheduleValidator().validate(schedule) if i.blocking]
    assert any(i.issue_code == "RECURRENCE_START_UNVERIFIABLE"
               and i.details["reason"] == "DIFFERENT_REFERENCE" for i in issues)


def test_a_patient_missing_the_anchor_is_a_wait_not_an_approval_blocker():
    schedule = repeating(RecurrenceBasis.FIXED_ANCHOR_SERIES, first_days=14,
                         start_offset={"value": 14, "unit": "DAY"},
                         termination=RecurrenceTermination(type="COUNT", count=3))
    assert not [i for i in ScheduleValidator().validate(schedule) if i.blocking]
    waiting = of(run(schedule, anchors_={}), "LABS")
    assert waiting and all(i.status == PatientEventStatus.WAITING_FOR_ANCHOR for i in waiting)


def test_numbered_source_with_actual_mode_waits_for_that_occurrences_own_actual():
    """P5 audit finding 3: occurrence 0 has an actual, occurrence 1 does not."""
    weekly = RecurrenceRule(
        interval={"value": 7, "unit": "DAY"}, start_reference={"kind": "ANCHOR", "code": "BASELINE"},
        termination=RecurrenceTermination(type="COUNT", count=2),
        basis="FIRST_RESOLVED_OCCURRENCE", basis_status="STATED")

    def target(selector, number=None):
        event = Event(
            code="TARGET", protocol_label="TARGET", display_name="Target", event_type="VISIT",
            timing={"type": "OFFSET",
                    "reference": {"kind": "EVENT", "event_code": "SOURCE",
                                  "temporal_basis": "ACTUAL_VISIT"},
                    "offset": {"value": 3, "unit": "DAY"}})
        event.dependencies = [Dependency(
            source_event_code="SOURCE", dependency_type="SEQUENCE",
            source_occurrence=selector, source_occurrence_number=number)]
        event.dependency_mode = DependencyMode.ACTUAL_PREVIOUS_EVENT
        return event

    history = [LogicalOccurrenceState(
        event_code="SOURCE", occurrence_index=0, actual_visit_date=date(2026, 1, 1))]
    ctx = {"actual_event_values": {"SOURCE": [date(2026, 1, 1)]}}

    def evaluate(selector, number=None, hist=history, actuals=ctx):
        schedule = build([offset_event("SOURCE", 0, recurrence=weekly), target(selector, number)])
        return of(run(schedule, history=hist, **actuals), "TARGET")[0]

    waiting = evaluate("NUMBER", 1)
    assert waiting.status == PatientEventStatus.BLOCKED and waiting.timing is None
    assert waiting.dependency_result["waiting_for_actual"] == ["SOURCE"]
    first = evaluate("NUMBER", 0)                       # index 0 genuinely has its actual
    assert first.status == PatientEventStatus.RESOLVED
    assert first.timing.nominal_start == date(2026, 1, 4)
    assert evaluate("FIRST").status == PatientEventStatus.RESOLVED
    assert evaluate("LAST").status == PatientEventStatus.BLOCKED

    both = history + [LogicalOccurrenceState(
        event_code="SOURCE", occurrence_index=1, actual_visit_date=date(2026, 1, 9))]
    done = evaluate("NUMBER", 1, hist=both,
                    actuals={"actual_event_values": {"SOURCE": [date(2026, 1, 1), date(2026, 1, 9)]}})
    assert done.status == PatientEventStatus.RESOLVED
    assert done.timing.nominal_start == date(2026, 1, 12)   # 9 Jan + 3, never index 0's date
