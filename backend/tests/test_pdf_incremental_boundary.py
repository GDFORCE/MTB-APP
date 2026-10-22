"""P9-01: the proof boundary of incremental evaluation is pinned, measured at scale, and never trades correctness."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.application.pilot import build_pilot_schedule
from app.domain.schedule.amendment import schedule_content_hash
from app.domain.schedule.incremental import EvaluationBaseline, plan_evaluation, supported
from app.domain.schedule.models import ClaimEvidence, PatientContext, RecurrenceRule, RecurrenceTermination
from app.domain.schedule.timing import AnchorReference, PositiveTemporalAmount, TimeUnit
from test_pdf_incremental import AS_OF, HORIZON, calculate, graph, semantic

VERSION = "v-test"


def baseline_for(schedule, context, **kwargs):
    original = calculate(schedule, context)
    return EvaluationBaseline(schedule_content_hash(schedule), context, original, HORIZON, AS_OF)


def plan(schedule, context, baseline):
    return plan_evaluation(schedule, context, baseline, horizon=HORIZON, as_of=AS_OF,
                           evaluator_version=baseline.result.evaluator_version)


def simple_recurrence(schedule):
    schedule.events[2].recurrence = RecurrenceRule(
        interval=PositiveTemporalAmount(value=7, unit=TimeUnit.DAY), start_reference=AnchorReference(code="ROOT0"),
        termination=RecurrenceTermination(type="COUNT", count=3), basis="FIRST_RESOLVED_OCCURRENCE", basis_status="STATED")
    schedule.claim_evidence.append(ClaimEvidence(evidence_id=schedule.evidence[0].id, claim_type="RECURRENCE",
                                                 claim_entity_type="EVENT", claim_entity_id=schedule.events[2].id,
                                                 claim_path="recurrence"))


CONSTRUCTS = {
    "event metadata": lambda s: s.events[1].metadata.update({"note": 1}),
    "track membership": lambda s: setattr(s.events[1], "track_id", uuid4()),
    "recurrence": simple_recurrence,
    "extra anchor derivation": lambda s: s.anchors[0].derivation_rule.update({"role": "X"}) if s.anchors[0].derivation_rule
        else setattr(s.anchors[0], "derivation_rule", {"role": "X"}),
}


@pytest.mark.parametrize("name", list(CONSTRUCTS))
def test_each_unproven_construct_is_outside_the_boundary_and_still_evaluates_exactly(name):
    schedule, context = graph(3, 12)
    assert supported(schedule)                                          # the control is inside the boundary
    CONSTRUCTS[name](schedule)
    assert not supported(schedule)
    changed = context.model_copy(deep=True)
    changed.anchors["ROOT0"] += timedelta(days=5)
    base = baseline_for(schedule, context)
    decided = plan(schedule, changed, base)
    assert decided.reason == "UNPROVEN_CONSTRUCT_FULL_EVALUATION" and decided.reused == frozenset()
    with_baseline = calculate(schedule, changed, base)
    assert semantic(with_baseline) == semantic(calculate(schedule, changed))      # full evaluation, identical to scratch


def test_the_pilot_schedule_with_tracks_processes_and_recurrence_is_never_approximated():
    schedule = build_pilot_schedule(uuid4(), "pilot")
    schedule.schedule_metadata.status = "APPROVED"
    context = PatientContext(patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id, anchors={"BASELINE": AS_OF})
    changed = context.model_copy(deep=True)
    changed.anchors["BASELINE"] += timedelta(days=9)
    base = baseline_for(schedule, context)
    assert not supported(schedule)
    assert plan(schedule, changed, base).reused == frozenset()
    assert semantic(calculate(schedule, changed, base)) == semantic(calculate(schedule, changed))


def test_at_scale_one_changed_root_reuses_the_unaffected_branch_and_equals_a_from_scratch_run():
    schedule, context = graph(11, 400)
    base = baseline_for(schedule, context)
    changed = context.model_copy(deep=True)
    changed.anchors["ROOT1"] += timedelta(days=3)
    decided = plan(schedule, changed, base)
    total = len(schedule.events)
    assert decided.reason == "PROVEN_LINEAR_GRAPH" and len(decided.affected) + len(decided.reused) == total
    assert decided.reused and decided.affected and decided.affected.isdisjoint(decided.reused)
    saved = len(decided.reused) / total
    assert saved > 0.1, f"only {saved:.0%} reused"                     # measured, not assumed
    assert semantic(calculate(schedule, changed, base)) == semantic(calculate(schedule, changed))
    unchanged = plan(schedule, context, base)
    assert unchanged.affected == frozenset() and len(unchanged.reused) == total        # nothing changed: everything reused
