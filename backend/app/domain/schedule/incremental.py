"""Conservative affected-graph planning. Unproved constructs use full evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .amendment import schedule_content_hash
from .compiled import digest
from .models import EvaluationResult, PatientContext, UniversalSchedule
from .timing import AbsoluteTiming, AnchorReference, EventReference, NominalWindowTiming, OffsetTiming, ProtocolDayTiming
from .validator import timing_references


@dataclass(frozen=True)
class EvaluationBaseline:
    schedule_hash: str
    context: PatientContext
    result: EvaluationResult
    horizon: date
    as_of: date


@dataclass(frozen=True)
class EvaluationPlan:
    affected: frozenset[str]
    reused: frozenset[str]
    reason: str

    def as_dict(self):
        return {"version": "uctsm-plan.v1", "affected": sorted(self.affected),
                "reused": sorted(self.reused), "reason": self.reason}


def supported(schedule: UniversalSchedule) -> bool:
    # Cross-track/process/conditional/recurrence effects are deliberately NOT
    # approximated. Extend this proof boundary only with differential tests.
    if any(getattr(schedule, name) for name in ("epochs", "arms", "cohorts", "populations", "dimensions",
            "conditional_definitions", "repeat_blocks", "confinement_episodes", "processes",
            "holiday_calendars", "tracks", "track_links")):
        return False
    if any(anchor.source_event_code or anchor.source_condition_code or anchor.derivation_rule
           for anchor in schedule.anchors):
        return False
    for event in schedule.events:
        timing = event.timing.nominal if isinstance(event.timing, NominalWindowTiming) else event.timing
        if (not isinstance(timing, (AbsoluteTiming, OffsetTiming, ProtocolDayTiming)) or event.repeats
                or event.activities or event.conditions or event.applicability or event.conditional_actions
                or event.constraints or event.metadata or event.confinement or event.track_id
                or event.dependency_mode not in {None, "NOMINAL", "ACTUAL_PREVIOUS_EVENT"}
                or any(dependency.condition is not None for dependency in event.dependencies)):
            return False
        if any(not isinstance(ref, (AnchorReference, EventReference)) for ref in timing_references(event.timing)):
            return False
    return True


def plan_evaluation(schedule, context, baseline, *, horizon, as_of, evaluator_version, rolling=None):
    codes = frozenset(event.code for event in schedule.events)
    def full(reason):
        return EvaluationPlan(codes, frozenset(), reason)
    if baseline is None:
        return full("NO_BASELINE")
    if rolling is not None or not supported(schedule):
        return full("UNPROVEN_CONSTRUCT_FULL_EVALUATION")
    if (baseline.schedule_hash != schedule_content_hash(schedule)
            or baseline.horizon != horizon or baseline.as_of != as_of
            or baseline.result.evaluator_version != evaluator_version
            or baseline.context.patient_id != context.patient_id
            or baseline.context.schedule_version_id != context.schedule_version_id
            or baseline.result.input_snapshot != baseline.context.model_dump(mode="json")):
        return full("BASELINE_BINDING_CHANGED")
    prior_keys = [(row.event_code, row.occurrence_index) for row in baseline.result.events]
    if set(prior_keys) != {(code, 0) for code in codes} or len(prior_keys) != len(codes):
        return full("INCOMPLETE_BASELINE")
    before = baseline.context.model_dump(mode="json"); after = context.model_dump(mode="json")
    local = {"anchors", "planned_anchors", "event_values", "actual_event_values", "occurrence_history", "occurrence_facts"}
    if any(before[key] != after[key] for key in before.keys() - local):
        return full("GLOBAL_CONTEXT_CHANGED")
    # Even unchanged global histories can have effects crossing event boundaries.
    if context.process_transitions or context.assignment_history or context.unscheduled_occurrences:
        return full("UNPROVEN_CONTEXT_FULL_EVALUATION")
    affected = set(); changed_anchors = set()
    for name in ("anchors", "planned_anchors", "event_values", "actual_event_values"):
        changed = {key for key in before[name].keys() | after[name].keys()
                   if before[name].get(key) != after[name].get(key)}
        (changed_anchors if name in {"anchors", "planned_anchors"} else affected).update(changed)
    for name in ("occurrence_history", "occurrence_facts"):
        def grouped(rows):
            groups = {}
            for row in rows:
                groups.setdefault(row["event_code"], []).append(row)
            return {key: digest(value) for key, value in groups.items()}
        old, new = grouped(before[name]), grouped(after[name])
        affected.update(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
    reverse = {code: set() for code in codes}
    for event in schedule.events:
        sources = {dependency.source_event_code for dependency in event.dependencies}
        for ref in timing_references(event.timing):
            if isinstance(ref, EventReference):
                sources.add(ref.event_code)
            elif isinstance(ref, AnchorReference) and ref.code in changed_anchors:
                affected.add(event.code)
        for source in sources:
            if source in reverse:
                reverse[source].add(event.code)
    pending = list(affected)
    while pending:
        for dependent in reverse.get(pending.pop(), ()):
            if dependent not in affected:
                affected.add(dependent); pending.append(dependent)
    affected.intersection_update(codes)
    return EvaluationPlan(frozenset(affected), codes - affected, "PROVEN_LINEAR_GRAPH")
