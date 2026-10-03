"""Static validation of composable constraint sets (RL-13, IR-04 runtime side).

Checks, per event/activity carrying constraints:

* references - anchors, events, same-visit activities and evidence ids exist;
* same-occurrence cycles - a constraint may refer to its OWN event only through
  the occurrence-advancing PREVIOUS selector; activity constraints may not form
  a cycle with each other or with legacy intra-visit timings;
* literal precision - a MONTH/YEAR literal can never become a day bound;
* static infeasibility - bounds on the same reference (or on literals) that no
  patient data could ever reconcile, e.g. ``>= A + 16 weeks`` and
  ``<= A + 12 weeks``, including against the legacy timing's own window;
* semantic relations - an identical normalised restriction stated twice is a
  DUPLICATE (reported, deduplicated at runtime with both evidence refs kept);
  a complementary restriction is NOT a duplicate and both are enforced;
* evidence - every leaf is justified by its own/ancestor ``evidence_refs`` or a
  ``CONSTRAINT`` claim on its owner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from itertools import product
from typing import Callable

from .calendar_math import add_amount
from .feasibility import constraint_leaves, node_id, semantic_key, value_nodes
from .models import Activity, Event, Severity, UniversalSchedule, ValidationIssue
from .timing import (
    ActivityReference,
    ActivityValue,
    AllConstraint,
    AnchorReference,
    AnchorValue,
    AnyConstraint,
    AtConstraint,
    ConstraintTiming,
    EventValue,
    ProcessBoundaryValue,
    LiteralDateValue,
    LowerBoundConstraint,
    NominalWindowTiming,
    NoLaterThanTiming,
    OffsetTiming,
    ProtocolDayTiming,
    RangeTiming,
    ShiftValue,
    TemporalAmount,
    TimeUnit,
    UpperBoundConstraint,
    WithinTiming,
)

IssueFactory = Callable[..., ValidationIssue]

_MAX_ALTERNATIVES = 64

_FAMILY = {
    TimeUnit.MINUTE: ("MINUTE", 1), TimeUnit.HOUR: ("MINUTE", 60),
    TimeUnit.DAY: ("DAY", 1), TimeUnit.WEEK: ("DAY", 7),
    TimeUnit.MONTH: ("MONTH", 1), TimeUnit.YEAR: ("MONTH", 12),
}


def _owners(schedule: UniversalSchedule):
    """(owner_type, owner, event, owner_label, roots) for every constrained rule."""
    for event in schedule.events:
        roots = []
        if isinstance(event.timing, ConstraintTiming):
            roots.append((f"{event.code}/timing/constraint", event.timing.constraint))
        if event.constraints is not None:
            roots.append((f"{event.code}/constraints", event.constraints))
        if roots:
            yield "EVENT", event, event, event.code, roots
        for activity in event.activities:
            label = f"{event.code}/{activity.code or activity.id}"
            roots = []
            if isinstance(activity.timing, ConstraintTiming):
                roots.append((f"{label}/timing/constraint", activity.timing.constraint))
            if activity.constraints is not None:
                roots.append((f"{label}/constraints", activity.constraints))
            if roots:
                yield "ACTIVITY", activity, event, label, roots


def validate_constraints(schedule: UniversalSchedule, issue: IssueFactory) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    anchors = {item.code for item in schedule.anchors}
    cycle_codes = {
        block.cycle_plan.code for block in schedule.repeat_blocks if block.cycle_plan is not None
    }
    events = {item.code for item in schedule.events}
    processes = {item.code for item in schedule.processes}
    evidence_ids = {str(item.id) for item in schedule.evidence}
    claims = {(claim.claim_entity_id, claim.claim_type) for claim in schedule.claim_evidence}

    from .validator import _is_cycle_anchor

    for owner_type, owner, event, label, roots in _owners(schedule):
        activity_codes = {item.code for item in event.activities if item.code}
        has_claim = (owner.id, "CONSTRAINT") in claims
        explicit_ids: dict[str, int] = {}

        def report(code: str, message: str, *, blocking: bool = True,
                   severity: Severity = Severity.ERROR, **details) -> None:
            issues.append(issue(
                code, message, entity_type=owner_type, entity_id=owner.id,
                blocking=blocking, severity=severity,
                details={"owner_code": label, **details}))

        for root_path, root in roots:
            for path, node, inherited in _walk_with_evidence(root, root_path, []):
                constraint_id = node_id(node, path)
                if getattr(node, "id", None):
                    explicit_ids[node.id] = explicit_ids.get(node.id, 0) + 1
                unknown_evidence = sorted(set(node.evidence_refs) - evidence_ids)
                if unknown_evidence:
                    report("UNRESOLVED_REFERENCE",
                           f"Constraint {constraint_id} cites evidence that is not in the schedule",
                           constraint_id=constraint_id, evidence_refs=unknown_evidence,
                           reference_kind="EVIDENCE")
                value = getattr(node, "value", None)
                if value is None:
                    continue
                if not (inherited or node.evidence_refs or has_claim):
                    report("MISSING_EVIDENCE",
                           f"Constraint {constraint_id} has no linked protocol evidence",
                           constraint_id=constraint_id, claim_type="CONSTRAINT")
                for item in value_nodes(value):
                    _check_value(item, constraint_id, owner_type, owner, event, label,
                                 anchors, cycle_codes, events, activity_codes, report,
                                 _is_cycle_anchor, processes)
            for relation in _duplicates(root, root_path):
                report("DUPLICATE_CONSTRAINT",
                       "The same restriction is stated more than once; it is enforced once "
                       "and every source is kept",
                       blocking=False, severity=Severity.INFO, **relation)
        for constraint_id, count in sorted(explicit_ids.items()):
            if count > 1:
                report("DUPLICATE_CONSTRAINT_ID",
                       f"Constraint id {constraint_id!r} is used for more than one restriction",
                       constraint_id=constraint_id)
        infeasible = _static_conflicts(owner_type, owner, event, roots)
        if infeasible:
            report("CONSTRAINT_INFEASIBLE",
                   "These restrictions can never be satisfied together, whatever the "
                   "patient's dates",
                   conflicting_constraints=sorted(infeasible))

    issues.extend(_activity_cycles(schedule, issue))
    return issues


def _walk_with_evidence(node: object, path: str, inherited: list[str]):
    yield path, node, inherited
    carried = [*inherited, *node.evidence_refs]
    for index, child in enumerate(getattr(node, "items", None) or []):
        yield from _walk_with_evidence(child, f"{path}/items/{index}", carried)


def _check_value(
    item, constraint_id, owner_type, owner, event, label, anchors, cycle_codes, events,
    activity_codes, report, is_cycle_anchor, processes=frozenset(),
) -> None:
    if isinstance(item, AnchorValue):
        if item.code not in anchors and not is_cycle_anchor(item.code, cycle_codes):
            report("UNKNOWN_ANCHOR", f"Constraint {constraint_id} uses unknown anchor {item.code!r}",
                   constraint_id=constraint_id, anchor_code=item.code)
    elif isinstance(item, EventValue):
        if item.event_code not in events:
            report("UNRESOLVED_REFERENCE",
                   f"Constraint {constraint_id} references unknown event {item.event_code!r}",
                   constraint_id=constraint_id, event_code=item.event_code, reference_kind="EVENT")
        elif item.event_code == event.code:
            if owner_type == "ACTIVITY" or item.occurrence.type != "PREVIOUS":
                report("CONSTRAINT_DEPENDENCY_CYCLE",
                       f"Constraint {constraint_id} dates {label} from the same occurrence "
                       "it is computing; only the PREVIOUS occurrence of an event's own "
                       "series can be referenced",
                       constraint_id=constraint_id, event_code=item.event_code,
                       occurrence=item.occurrence.type)
    elif isinstance(item, ActivityValue):
        if item.activity_code not in activity_codes:
            report("UNRESOLVED_REFERENCE",
                   f"Constraint {constraint_id} references unknown activity "
                   f"{item.activity_code!r} in the same visit",
                   constraint_id=constraint_id, activity_code=item.activity_code,
                   reference_kind="ACTIVITY")
        elif owner_type == "ACTIVITY" and item.activity_code == owner.code:
            report("CONSTRAINT_DEPENDENCY_CYCLE",
                   f"Constraint {constraint_id} times an activity relative to itself",
                   constraint_id=constraint_id, activity_code=item.activity_code)
    elif isinstance(item, ProcessBoundaryValue):
        if item.process_code not in processes:
            report("UNRESOLVED_REFERENCE",
                   f"Constraint {constraint_id} references unknown process {item.process_code!r}",
                   constraint_id=constraint_id, process_code=item.process_code,
                   reference_kind="PROCESS")
    elif isinstance(item, LiteralDateValue) and item.precision in {"MONTH", "YEAR"}:
        report("CONSTRAINT_PRECISION_INSUFFICIENT",
               f"Constraint {constraint_id} uses a {item.precision.lower()}-precision date as "
               "a bound; state the exact day (or record an authorized interpretation)",
               constraint_id=constraint_id, precision=item.precision)


def _duplicates(root: object, root_path: str) -> list[dict[str, object]]:
    output = []
    for path, node in constraint_leaves(root, root_path):
        if not isinstance(node, (AllConstraint, AnyConstraint)):
            continue
        seen: dict[str, tuple[str, object]] = {}
        for index, child in enumerate(node.items):
            child_path = f"{path}/items/{index}"
            key = semantic_key(child)
            if key in seen:
                kept_path, kept = seen[key]
                output.append({
                    "relation": "DUPLICATE",
                    "constraint_ids": [node_id(kept, kept_path), node_id(child, child_path)],
                    "evidence_refs": list(dict.fromkeys([*kept.evidence_refs, *child.evidence_refs])),
                })
            else:
                seen[key] = (child_path, child)
    return output


# ---------------------------------------------------------------------------
# Static infeasibility
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SymBound:
    side: str  # "lower" | "upper"
    key: str  # reference identity ("LITERAL" for absolute dates)
    family: str
    offset: int  # in family units; for LITERAL: date ordinal
    inclusive: bool
    ids: tuple[str, ...]


def _symbolic(value: object) -> tuple[str, str, int] | None:
    """(reference key, family, offset) for a reference + same-family shifts."""
    shifts: list[TemporalAmount] = []
    while isinstance(value, ShiftValue):
        shifts.append(value.amount)
        value = value.reference
    if isinstance(value, LiteralDateValue):
        if value.precision != "DAY" or isinstance(value.value, datetime):
            return None
        current: date = value.value
        for amount in reversed(shifts):
            if amount.unit in (TimeUnit.MINUTE, TimeUnit.HOUR):
                return None
            current = add_amount(current, amount)
        return "LITERAL", "DAY", current.toordinal()
    if isinstance(value, (AnchorValue, EventValue, ActivityValue)):
        key = json.dumps(value.model_dump(mode="json"), sort_keys=True)
    elif isinstance(value, AnchorReference):
        key = json.dumps(AnchorValue(code=value.code, accepts=value.accepts).model_dump(mode="json"),
                         sort_keys=True)
    else:
        return None
    family, total = None, 0
    for amount in shifts:
        unit_family, scale = _FAMILY[amount.unit]
        if family is not None and unit_family != family:
            return None
        family = unit_family
        total += amount.value * scale
    return key, family or "ANY", total


def _leaf_bounds(node: object, constraint_id: str) -> list[_SymBound] | None:
    symbolic = _symbolic(node.value)
    if symbolic is None:
        return None
    key, family, offset = symbolic
    ids = (constraint_id,)
    if isinstance(node, LowerBoundConstraint):
        return [_SymBound("lower", key, family, offset, node.inclusive, ids)]
    if isinstance(node, UpperBoundConstraint):
        return [_SymBound("upper", key, family, offset, node.inclusive, ids)]
    if isinstance(node, AtConstraint):
        return [_SymBound("lower", key, family, offset, True, ids),
                _SymBound("upper", key, family, offset, True, ids)]
    return None


def _dnf(node: object, path: str) -> list[list[_SymBound]] | None:
    """Disjunctive normal form over symbolic bounds; None = too large."""
    if isinstance(node, AllConstraint):
        parts = [_dnf(child, f"{path}/items/{index}") for index, child in enumerate(node.items)]
        if any(part is None for part in parts):
            return None
        combined: list[list[_SymBound]] = [[]]
        for part in parts:
            combined = [[*left, *right] for left, right in product(combined, part)]
            if len(combined) > _MAX_ALTERNATIVES:
                return None
        return combined
    if isinstance(node, AnyConstraint):
        output: list[list[_SymBound]] = []
        for index, child in enumerate(node.items):
            part = _dnf(child, f"{path}/items/{index}")
            if part is None:
                return None
            output.extend(part)
        return output if len(output) <= _MAX_ALTERNATIVES else None
    bounds = _leaf_bounds(node, node_id(node, path)) if hasattr(node, "value") else None
    # A leaf we cannot reason about statically restricts nothing here.
    return [bounds or []]


def _amount_bounds(reference, start, end, label, inclusive=(True, True)) -> list[_SymBound]:
    if not isinstance(reference, AnchorReference):
        return []
    output = []
    for side, amount, flag in (("lower", start, inclusive[0]), ("upper", end, inclusive[1])):
        if amount is None:
            continue
        symbolic = _symbolic(ShiftValue(reference=AnchorValue(code=reference.code, accepts=reference.accepts),
                                        amount=amount))
        if symbolic is None:
            continue
        key, family, offset = symbolic
        output.append(_SymBound(side, key, family, offset, flag, (label,)))
    return output


def _timing_bounds(owner: Event | Activity, label: str) -> list[_SymBound]:
    """The legacy timing's window as symbolic bounds, for the non-repeating case."""
    if isinstance(owner, Event) and owner.repeats:
        return []
    timing = owner.timing
    source = f"{label}/timing"
    window = owner.window if isinstance(owner, Event) else None
    if isinstance(timing, NominalWindowTiming):
        window, timing = timing.window, timing.nominal
    if isinstance(timing, RangeTiming):
        return _amount_bounds(timing.reference, timing.start, timing.end, source)
    if isinstance(timing, (OffsetTiming, ProtocolDayTiming)):
        if isinstance(timing, OffsetTiming):
            offset = timing.offset
        else:
            elapsed = timing.day - 1 if timing.day > 0 else timing.day
            offset = TemporalAmount(value=elapsed, unit=TimeUnit.DAY)
        if window is None:
            return _amount_bounds(timing.reference, offset, offset, source)
        start_family, start_scale = _FAMILY[offset.unit]
        window_family, window_scale = _FAMILY[window.before.unit]
        if start_family != window_family:
            return []
        base = offset.value * start_scale
        unit = {"DAY": TimeUnit.DAY, "MINUTE": TimeUnit.MINUTE, "MONTH": TimeUnit.MONTH}[start_family]
        return _amount_bounds(
            timing.reference,
            TemporalAmount(value=base - window.before.value * window_scale, unit=unit),
            TemporalAmount(value=base + window.after.value * window_scale, unit=unit), source)
    if isinstance(timing, WithinTiming):
        zero = TemporalAmount(value=0, unit=timing.duration.unit)
        if timing.direction == "AFTER":
            return _amount_bounds(timing.reference, zero, timing.duration, source)
        negative = TemporalAmount(value=-timing.duration.value, unit=timing.duration.unit)
        return _amount_bounds(timing.reference, negative, zero, source)
    if isinstance(timing, NoLaterThanTiming):
        return _amount_bounds(timing.reference, None, timing.duration, source)
    return []


def _pair_conflicts(lower: _SymBound, upper: _SymBound) -> bool:
    if lower.key != upper.key:
        return False
    families = {lower.family, upper.family}
    if len(families - {"ANY"}) > 1:
        return False
    family = next(iter(families - {"ANY"}), "ANY")
    low, high = lower.offset, upper.offset
    if family in {"DAY", "ANY"} or lower.key == "LITERAL":
        low += 0 if lower.inclusive else 1
        high -= 0 if upper.inclusive else 1
        return low > high
    return low > high or (low == high and not (lower.inclusive and upper.inclusive))


def _conjunction_conflict(bounds: list[_SymBound]) -> set[str] | None:
    lowers = [item for item in bounds if item.side == "lower"]
    uppers = [item for item in bounds if item.side == "upper"]
    for lower in lowers:
        for upper in uppers:
            if _pair_conflicts(lower, upper):
                return {*lower.ids, *upper.ids}
    return None


def _static_conflicts(owner_type, owner, event, roots) -> set[str]:
    label = roots[0][0].split("/timing/")[0].split("/constraints")[0]
    parts: list[list[list[_SymBound]]] = [[_timing_bounds(owner, label)]]
    for path, root in roots:
        dnf = _dnf(root, path)
        if dnf is None:
            return set()
        parts.append(dnf)
    conjunctions: list[list[_SymBound]] = [[]]
    for part in parts:
        conjunctions = [[*left, *right] for left, right in product(conjunctions, part)]
        if len(conjunctions) > _MAX_ALTERNATIVES:
            return set()
    conflicts: set[str] = set()
    for conjunction in conjunctions:
        found = _conjunction_conflict(conjunction)
        if found is None:
            return set()
        conflicts |= found
    return conflicts


# ---------------------------------------------------------------------------
# Activity cycles (same visit occurrence)
# ---------------------------------------------------------------------------


def _activity_cycles(schedule: UniversalSchedule, issue: IssueFactory) -> list[ValidationIssue]:
    from .validator import constraint_references, timing_references

    issues: list[ValidationIssue] = []
    for event in schedule.events:
        edges: dict[str, set[str]] = {}
        has_constraints = False
        for activity in event.activities:
            if not activity.code:
                continue
            has_constraints = has_constraints or activity.constraints is not None or isinstance(
                activity.timing, ConstraintTiming)
            references = [*timing_references(activity.timing),
                          *constraint_references(activity.constraints)]
            edges[activity.code] = {
                item.activity_code for item in references
                if isinstance(item, ActivityReference) and item.activity_code != activity.code
            }
        if not has_constraints:
            continue  # legacy intra-visit timings keep their existing checks
        state: dict[str, int] = {}
        cyclic: set[str] = set()

        def visit(code: str, stack: list[str]) -> None:
            state[code] = 1
            for target in edges.get(code, ()):
                if state.get(target) == 1:
                    cyclic.update(stack[stack.index(target):] if target in stack else [target, code])
                elif state.get(target) is None:
                    visit(target, [*stack, target])
            state[code] = 2

        for code in sorted(edges):
            if state.get(code) is None:
                visit(code, [code])
        if cyclic:
            issues.append(issue(
                "CONSTRAINT_DEPENDENCY_CYCLE",
                f"Activities in {event.display_name!r} are timed from each other in a cycle",
                entity_type="EVENT", entity_id=event.id,
                details={"event_code": event.code, "activity_codes": sorted(cyclic)},
            ))
    return issues
