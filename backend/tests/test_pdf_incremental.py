from datetime import date, datetime, timedelta, timezone
import random
from uuid import uuid4

import pytest

from app.application.evaluation_cache import EvaluationMemo
from app.application.pilot import build_pilot_schedule
from app.domain.schedule.amendment import schedule_content_hash
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.incremental import EvaluationBaseline
from app.domain.schedule.models import Anchor, ClaimEvidence, Dependency, DependencyMode, Event, Evidence, OccurrenceFact, PatientContext, ScheduleMetadata, UniversalSchedule
from app.domain.schedule.timing import AnchorReference, EventReference, OffsetTiming, TemporalAmount

AS_OF = date(2026, 1, 1)
HORIZON = date(2027, 12, 31)


def graph(seed, count=20):
    rng = random.Random(seed)
    evidence = Evidence(evidence_type="TABLE_CELL", source_text="Synthetic graph; not a protocol.", page_number=1)
    events = []; claims = []
    for index in range(count):
        code = f"E{index}"
        # Two independent roots guarantee that changing one branch can reuse
        # something, while random downstream edges test transitive propagation.
        source = rng.randrange(index) if index > 1 and rng.random() < .8 else None
        ref = EventReference(event_code=f"E{source}", temporal_basis="CURRENT_PLANNED") if source is not None else AnchorReference(code=f"ROOT{index % 2}")
        event = Event(code=code, display_name=code, protocol_label=code, event_type="VISIT",
            timing=OffsetTiming(reference=ref, offset=TemporalAmount(value=rng.randrange(10), unit="DAY")),
            dependency_mode="NOMINAL", evidence_refs=[evidence.id],
            dependencies=[Dependency(source_event_code=f"E{source}", dependency_type="TEMPORAL")] if source is not None else [])
        events.append(event)
        for claim_type, path in [("EVENT_NAME", "display_name"), ("TIMING", "timing"), ("DEPENDENCY_MODE", "dependency_mode")
                ] + ([("DEPENDENCY", "dependencies")] if source is not None else []):
            claims.append(ClaimEvidence(evidence_id=evidence.id, claim_type=claim_type,
                claim_entity_type="EVENT", claim_entity_id=event.id, claim_path=path))
    schedule = UniversalSchedule(schedule_metadata=ScheduleMetadata(name="Synthetic linear graph", status="APPROVED"),
        anchors=[Anchor(code=f"ROOT{index}", display_name=f"Root {index}", anchor_type="BASELINE", status="RESOLVED") for index in range(2)],
        events=events, evidence=[evidence], claim_evidence=claims)
    context = PatientContext(patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"ROOT0": AS_OF, "ROOT1": AS_OF})
    return schedule, context


def calculate(schedule, context, baseline=None, **kwargs):
    return ScheduleEvaluator().evaluate(schedule, context, horizon=kwargs.get("horizon", HORIZON),
        today=kwargs.get("today", AS_OF), baseline=baseline)


def semantic(result):
    return result.model_dump(exclude={"evaluated_at", "evaluation_plan"})


@pytest.mark.parametrize("seed", range(30))
def test_random_downstream_graphs_match_full_evaluation_after_anchor_changes_and_corrections(seed):
    schedule, context = graph(seed, 10 + seed)
    original = calculate(schedule, context)
    baseline = EvaluationBaseline(schedule_content_hash(schedule), context, original, HORIZON, AS_OF)
    changed = context.model_copy(deep=True); changed.anchors["ROOT0"] += timedelta(days=7)
    incremental = calculate(schedule, changed, baseline)
    assert semantic(incremental) == semantic(calculate(schedule, changed))
    assert incremental.evaluation_plan["reused"] and incremental.evaluation_plan["affected"]
    next_base = EvaluationBaseline(schedule_content_hash(schedule), changed, incremental, HORIZON, AS_OF)
    correction = calculate(schedule, context, next_base)
    assert semantic(correction) == semantic(original)


def test_missing_anchor_and_actual_changes_match_the_full_oracle():
    schedule, context = graph(3)
    first = calculate(schedule, context)
    baseline = EvaluationBaseline(schedule_content_hash(schedule), context, first, HORIZON, AS_OF)
    missing = context.model_copy(deep=True); del missing.anchors["ROOT0"]
    assert semantic(calculate(schedule, missing, baseline)) == semantic(calculate(schedule, missing))
    actual = context.model_copy(deep=True)
    actual.actual_event_values["E0"] = [date(2026, 2, 10)]
    assert semantic(calculate(schedule, actual, baseline)) == semantic(calculate(schedule, actual))


def test_reused_events_really_skip_timing_calculation(monkeypatch):
    schedule, context = graph(19, 150)
    first = calculate(schedule, context)
    baseline = EvaluationBaseline(schedule_content_hash(schedule), context, first, HORIZON, AS_OF)
    calls = []; original = ScheduleEvaluator._evaluate_with_history
    def tracked(self, event, *args, **kwargs):
        calls.append(event.code); return original(self, event, *args, **kwargs)
    monkeypatch.setattr(ScheduleEvaluator, "_evaluate_with_history", tracked)
    changed = context.model_copy(deep=True); changed.anchors["ROOT0"] += timedelta(days=1)
    result = calculate(schedule, changed, baseline)
    assert set(calls) == set(result.evaluation_plan["affected"])
    assert len(calls) < 150


def test_changed_clocks_patients_versions_and_global_inputs_force_full_evaluation():
    schedule, context = graph(1)
    first = calculate(schedule, context)
    baseline = EvaluationBaseline(schedule_content_hash(schedule), context, first, HORIZON, AS_OF)
    for patch in ({"patient_id": uuid4()}, {"time_zone": "Asia/Kolkata"}, {"state": {"symptomatic": True}}):
        changed = context.model_copy(update=patch, deep=True)
        result = calculate(schedule, changed, baseline)
        assert not result.evaluation_plan["reused"]
        assert semantic(result) == semantic(calculate(schedule, changed))
    assert not calculate(schedule, context, baseline, today=AS_OF + timedelta(days=1)).evaluation_plan["reused"]
    assert not calculate(schedule, context, baseline, horizon=HORIZON + timedelta(days=1)).evaluation_plan["reused"]
    complex_schedule = build_pilot_schedule(uuid4(), "Synthetic multi-track repeat")
    complex_schedule.schedule_metadata.status = "APPROVED"
    complex_context = PatientContext(patient_id=uuid4(), schedule_version_id=complex_schedule.schedule_version_id)
    result = calculate(complex_schedule, complex_context, baseline)
    assert not result.evaluation_plan["reused"]


def test_memo_is_tenant_scoped_and_bounded():
    schedule, context = graph(1)
    memo = EvaluationMemo(max_entries=1, max_bytes=1024 * 1024)
    one, two = uuid4(), uuid4()
    memo.evaluate(one, schedule, context, horizon=HORIZON, today=AS_OF)
    assert memo.evaluate(one, schedule, context, horizon=HORIZON, today=AS_OF).evaluation_plan["reused"]
    assert not memo.evaluate(two, schedule, context, horizon=HORIZON, today=AS_OF).evaluation_plan["reused"]
    assert len(memo._entries) == 1
    tiny = EvaluationMemo(max_entries=1, max_bytes=1)
    tiny.evaluate(one, schedule, context, horizon=HORIZON, today=AS_OF)
    assert not tiny._entries


@pytest.mark.parametrize("basis", ["ACTUAL_VISIT", "ACTUAL_TREATMENT"])
def test_incremental_actual_facts_corrections_and_missed_states_match_full_evaluation(basis):
    schedule, context = graph(1, 3)
    source, independent, dependent = schedule.events
    source.event_type = "TREATMENT"
    dependent.timing = OffsetTiming(reference=EventReference(event_code=source.code, temporal_basis=basis),
                                   offset=TemporalAmount(value=14, unit="DAY"))
    dependent.dependencies = [Dependency(source_event_code=source.code, dependency_type="TEMPORAL")]
    dependent.dependency_mode = DependencyMode.ACTUAL_PREVIOUS_EVENT
    schedule.claim_evidence.append(ClaimEvidence(evidence_id=schedule.evidence[0].id,
        claim_type="DEPENDENCY", claim_entity_type="EVENT", claim_entity_id=dependent.id,
        claim_path="dependencies"))
    as_of = date(2026, 2, 1)
    def fact(id, kind, value=None, supersedes=None):
        return OccurrenceFact(id=id, event_code=source.code, occurrence_index=0,
            kind=kind, value=value, supersedes=supersedes,
            recorded_at=datetime(2026, 1, 20, tzinfo=timezone.utc))
    treatment = fact("treatment", "TREATMENT", date(2026, 1, 13))
    visit = fact("visit", "PERFORMED", date(2026, 1, 12))
    corrected = fact("corrected-treatment", "TREATMENT", date(2026, 1, 17), "treatment")
    missed = fact("missed", "MISSED", supersedes="visit")
    restored = fact("corrected-visit", "PERFORMED", date(2026, 1, 19), "missed")
    states = [[], [treatment], [treatment, visit], [treatment, visit, corrected],
              [treatment, visit, corrected, missed], [treatment, visit, corrected, missed, restored]]
    prior_context = context
    prior_result = calculate(schedule, context, today=as_of)
    for facts in states[1:]:
        updated = context.model_copy(update={"occurrence_facts": facts}, deep=True)
        baseline = EvaluationBaseline(schedule_content_hash(schedule), prior_context, prior_result, HORIZON, as_of)
        incremental = calculate(schedule, updated, baseline, today=as_of)
        assert semantic(incremental) == semantic(calculate(schedule, updated, today=as_of))
        assert independent.code in incremental.evaluation_plan["reused"]
        assert {source.code, dependent.code} <= set(incremental.evaluation_plan["affected"])
        prior_context, prior_result = updated, incremental
    # Returning to no facts must not retain a previously supplied actual target.
    baseline = EvaluationBaseline(schedule_content_hash(schedule), prior_context, prior_result, HORIZON, as_of)
    reset = calculate(schedule, context, baseline, today=as_of)
    assert semantic(reset) == semantic(calculate(schedule, context, today=as_of))
    assert next(row for row in reset.events if row.event_code == dependent.code).timing is None
