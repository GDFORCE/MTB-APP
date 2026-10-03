"""Feasible-set evaluation for the composable constraint language.

Requirements: RL-04 (conjunction across anchors), RL-05 (disjoint alternatives
stay disjoint), RL-13 (infeasible / missing inputs named, no fabricated date),
NF-01 (deterministic, no model calls), NF-03 (every bound carries the ids of
the constraints that produced it).

A result is a normalised list of intervals. Endpoints may be unbounded (None),
inclusive or exclusive. ALL intersects, ANY unions WITHOUT a convex hull.

Missing inputs never make a set smaller: a leaf whose value is unknown
contributes the unbounded set, so the intervals of an INCOMPLETE result are
the KNOWN bounds - a superset of the eventual permitted set. Because
intersecting supersets can only lose times the real set would also lose, an
empty intersection of known bounds is a real contradiction, which is why
INFEASIBLE can be reported while other inputs are still missing.

Date-only and date-time values are kept distinct: when every value is a date
the arithmetic is on whole days (exclusive bounds normalise to inclusive
neighbours); as soon as a date-time is involved, a date means the whole local
day [00:00, next 00:00). A sub-day shift of a date-only value is not computed
- a date does not establish a time of day - and is reported as missing
TIME_PRECISION instead.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Iterable, Protocol

from .calendar_math import add_amount
from .exceptions import UnsupportedTimingError
from .models import (
    ConstraintRelation,
    ConstraintTraceEntry,
    FeasibilityStatus,
    FeasibleBound,
    FeasibleInterval,
    MissingInput,
)
from .timing import (
    CONSTRAINT_LANGUAGE_VERSION,
    ActivityValue,
    ProcessBoundaryValue,
    AllConstraint,
    AnchorValue,
    AnyConstraint,
    AtConstraint,
    EventValue,
    LiteralDateValue,
    LowerBoundConstraint,
    ShiftValue,
    TimeUnit,
    UnsupportedConstraint,
    UpperBoundConstraint,
)

Temporal = date | datetime

#: Calendar policies a SHIFT may name. MONTH_END_CLAMP.v1 is the engine's
#: existing month arithmetic (31 Jan + 1 month = 28/29 Feb), stated so that a
#: rule relying on it says so; without a policy a clamping shift is reported
#: as missing CALENDAR_POLICY_REQUIRED rather than silently clamped.
KNOWN_CALENDAR_POLICIES = frozenset({"MONTH_END_CLAMP.v1"})


class UnsupportedConstraintError(UnsupportedTimingError):
    """An UNSUPPORTED node reached the evaluator (the validator blocks this)."""


# ---------------------------------------------------------------------------
# Value resolution
# ---------------------------------------------------------------------------


@dataclass
class Resolved:
    value: Temporal | None
    inputs: list[dict[str, object]] = field(default_factory=list)
    missing: list[MissingInput] = field(default_factory=list)
    #: The reference exists but does not apply to this occurrence (PREVIOUS of
    #: the first occurrence): the leaf restricts nothing.
    vacuous: bool = False


class ValueSource(Protocol):
    def anchor(self, value: AnchorValue) -> Resolved: ...

    def event(self, value: EventValue) -> Resolved: ...

    def activity(self, value: ActivityValue) -> Resolved: ...

    def process_boundary(self, value: ProcessBoundaryValue) -> Resolved: ...


def _month_end_clamps(value: Temporal, months: int) -> bool:
    index = value.month - 1 + months
    year = value.year + index // 12
    month = index % 12 + 1
    return value.day > calendar.monthrange(year, month)[1]


def resolve_value(value: object, source: ValueSource) -> Resolved:
    if isinstance(value, AnchorValue):
        return source.anchor(value)
    if isinstance(value, EventValue):
        return source.event(value)
    if isinstance(value, ActivityValue):
        return source.activity(value)
    if isinstance(value, ProcessBoundaryValue):
        resolver = getattr(source, "process_boundary", None)
        if resolver is None:
            return Resolved(None, missing=[MissingInput(
                kind="PROCESS", code=value.process_code, accepts="ACTUAL",
                reason="PROCESS_STATE_UNAVAILABLE")])
        return resolver(value)
    if isinstance(value, LiteralDateValue):
        if value.precision in {"MONTH", "YEAR"}:
            return Resolved(None, missing=[MissingInput(
                kind="LITERAL_DATE", code=str(value.value), reason="DATE_PRECISION")])
        return Resolved(value.value, inputs=[{
            "kind": "LITERAL_DATE", "value": value.value.isoformat(), "basis": "STATED"}])
    if isinstance(value, ShiftValue):
        inner = resolve_value(value.reference, source)
        if inner.value is None:
            return inner
        amount = value.amount
        if amount.unit in (TimeUnit.MINUTE, TimeUnit.HOUR) and not isinstance(inner.value, datetime):
            return Resolved(None, inputs=inner.inputs, missing=[MissingInput(
                kind="TIME_OF_DAY", code=_describe(value.reference), reason="TIME_PRECISION")])
        if amount.unit in (TimeUnit.MONTH, TimeUnit.YEAR):
            months = amount.value * (12 if amount.unit == TimeUnit.YEAR else 1)
            policy = value.calendar_policy_ref
            if policy is not None and policy not in KNOWN_CALENDAR_POLICIES:
                return Resolved(None, inputs=inner.inputs, missing=[MissingInput(
                    kind="CALENDAR_POLICY", code=policy, reason="UNKNOWN_CALENDAR_POLICY")])
            if policy is None and _month_end_clamps(inner.value, months):
                return Resolved(None, inputs=inner.inputs, missing=[MissingInput(
                    kind="CALENDAR_POLICY", code=_describe(value.reference),
                    reason="CALENDAR_POLICY_REQUIRED")])
        return Resolved(add_amount(inner.value, amount), inputs=inner.inputs,
                        vacuous=inner.vacuous)
    raise UnsupportedConstraintError(f"unsupported temporal value {type(value).__name__}")


def _describe(value: object) -> str:
    for name in ("code", "event_code", "activity_code", "process_code"):
        found = getattr(value, name, None)
        if found:
            return str(found)
    inner = getattr(value, "reference", None)
    return _describe(inner) if inner is not None else type(value).__name__


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _End:
    key: Temporal | None  # comparison value in the evaluation domain; None = infinite
    inclusive: bool
    sources: frozenset[str]
    shown: Temporal | None  # value presented to callers
    shown_inclusive: bool


_Interval = tuple[_End, _End]
_NEG_INF = _End(None, False, frozenset(), None, False)
_POS_INF = _End(None, False, frozenset(), None, False)
_UNIVERSE: list[_Interval] = [(_NEG_INF, _POS_INF)]


@dataclass
class _Domain:
    """DATE when every value is a date; DATETIME (with a zone) otherwise."""

    datetime_mode: bool = False
    zone: tzinfo | None = None

    def instant(self, value: Temporal) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None and self.zone is not None:
                return value.replace(tzinfo=self.zone)
            return value
        return datetime.combine(value, time.min, tzinfo=self.zone)

    def lower(self, value: Temporal, inclusive: bool, sources: frozenset[str]) -> _End:
        if not self.datetime_mode:
            key = value if inclusive else value + timedelta(days=1)
            return _End(key, True, sources, key, True)
        if isinstance(value, datetime):
            key = self.instant(value)
            return _End(key, inclusive, sources, value, inclusive)
        day = value if inclusive else value + timedelta(days=1)
        return _End(self.instant(day), True, sources, value, inclusive)

    def upper(self, value: Temporal, inclusive: bool, sources: frozenset[str]) -> _End:
        if not self.datetime_mode:
            key = value if inclusive else value - timedelta(days=1)
            return _End(key, True, sources, key, True)
        if isinstance(value, datetime):
            key = self.instant(value)
            return _End(key, inclusive, sources, value, inclusive)
        day = value + timedelta(days=1) if inclusive else value
        return _End(self.instant(day), False, sources, value, inclusive)


def _lower_rank(end: _End) -> tuple:
    return (0,) if end.key is None else (1, end.key, 0 if end.inclusive else 1)


def _upper_rank(end: _End) -> tuple:
    return (1,) if end.key is None else (0, end.key, 1 if end.inclusive else 0)


def _merge_sources(left: _End, right: _End) -> _End:
    return _End(left.key, left.inclusive, left.sources | right.sources,
                left.shown, left.shown_inclusive)


def _stricter_lower(left: _End, right: _End) -> _End:
    a, b = _lower_rank(left), _lower_rank(right)
    if a == b:
        return _merge_sources(left, right)
    return left if a > b else right


def _stricter_upper(left: _End, right: _End) -> _End:
    a, b = _upper_rank(left), _upper_rank(right)
    if a == b:
        return _merge_sources(left, right)
    return left if a < b else right


def _looser_upper(left: _End, right: _End) -> _End:
    a, b = _upper_rank(left), _upper_rank(right)
    if a == b:
        return _merge_sources(left, right)
    return left if a > b else right


def _crosses(lower: _End, upper: _End) -> bool:
    """True when no time satisfies both ``lower`` and ``upper``."""
    if lower.key is None or upper.key is None:
        return False
    if lower.key > upper.key:
        return True
    return lower.key == upper.key and not (lower.inclusive and upper.inclusive)


def _touches(upper: _End, lower: _End, domain: _Domain) -> bool:
    """Whether an interval ending at ``upper`` and one starting at ``lower``
    leave no excluded time between them (so merging them widens nothing)."""
    if upper.key is None or lower.key is None:
        return True
    if not domain.datetime_mode:
        return lower.key <= upper.key + timedelta(days=1)
    if lower.key < upper.key:
        return True
    return lower.key == upper.key and (lower.inclusive or upper.inclusive)


def _normalise(intervals: Iterable[_Interval], domain: _Domain) -> list[_Interval]:
    kept = sorted(
        (item for item in intervals if not _crosses(item[0], item[1])),
        key=lambda item: _lower_rank(item[0]),
    )
    output: list[_Interval] = []
    for lower, upper in kept:
        if output and _touches(output[-1][1], lower, domain):
            previous_lower, previous_upper = output[-1]
            if _lower_rank(previous_lower) == _lower_rank(lower):
                previous_lower = _merge_sources(previous_lower, lower)
            output[-1] = (previous_lower, _looser_upper(previous_upper, upper))
        else:
            output.append((lower, upper))
    return output


def _intersect(
    left: list[_Interval], right: list[_Interval], domain: _Domain,
) -> tuple[list[_Interval], set[str]]:
    result: list[_Interval] = []
    conflicts: set[str] = set()
    for a_lower, a_upper in left:
        for b_lower, b_upper in right:
            lower = _stricter_lower(a_lower, b_lower)
            upper = _stricter_upper(a_upper, b_upper)
            if not _crosses(lower, upper):
                result.append((lower, upper))
                continue
            if _crosses(a_lower, b_upper):
                conflicts |= a_lower.sources | b_upper.sources
            if _crosses(b_lower, a_upper):
                conflicts |= b_lower.sources | a_upper.sources
    normalised = _normalise(result, domain)
    return normalised, (conflicts if not normalised else set())


# ---------------------------------------------------------------------------
# Constraint trees
# ---------------------------------------------------------------------------


def semantic_key(node: object) -> str:
    """Normalised meaning of a constraint: ids and evidence do not count."""

    def strip(value: object) -> object:
        if isinstance(value, dict):
            output = {
                key: strip(item) for key, item in sorted(value.items())
                if key not in {"id", "evidence_refs"}
            }
            # ALL and ANY are commutative: item order carries no meaning.
            if output.get("type") in {"ALL", "ANY"} and isinstance(output.get("items"), list):
                output["items"] = sorted(
                    output["items"], key=lambda item: json.dumps(item, sort_keys=True))
            return output
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    return json.dumps(strip(node.model_dump(mode="json")), sort_keys=True)


def node_id(node: object, path: str) -> str:
    return getattr(node, "id", None) or path


@dataclass
class _Leaf:
    path: str
    node: object
    ids: list[str]
    evidence_refs: list[str]
    resolved: Resolved | None = None


@dataclass
class _Prepared:
    """A constraint tree with duplicates collapsed and leaves indexed."""

    root: object
    path: str
    leaves: dict[int, _Leaf] = field(default_factory=dict)
    relations: list[ConstraintRelation] = field(default_factory=list)
    aliases: dict[int, list[str]] = field(default_factory=dict)
    extra_evidence: dict[int, list[str]] = field(default_factory=dict)
    #: (id(parent), index) of children collapsed into an earlier identical sibling.
    skipped: set[tuple[int, int]] = field(default_factory=set)


def _prepare(root: object, path: str) -> _Prepared:
    prepared = _Prepared(root=root, path=path)

    def visit(node: object, node_path: str) -> None:
        if isinstance(node, UnsupportedConstraint):
            raise UnsupportedConstraintError(
                f"constraint {node_id(node, node_path)!r} needs unsupported operator "
                f"{node.source_operator!r}")
        if isinstance(node, (AllConstraint, AnyConstraint)):
            seen: dict[str, int] = {}
            for index, child in enumerate(node.items):
                child_path = f"{node_path}/items/{index}"
                key = semantic_key(child)
                if key in seen:
                    prepared.skipped.add((id(node), index))
                    kept_index = seen[key]
                    kept = node.items[kept_index]
                    kept_path = f"{node_path}/items/{kept_index}"
                    prepared.aliases.setdefault(id(kept), []).append(node_id(child, child_path))
                    prepared.extra_evidence.setdefault(id(kept), []).extend(child.evidence_refs)
                    prepared.relations.append(ConstraintRelation(
                        relation="DUPLICATE",
                        constraint_ids=[node_id(kept, kept_path), node_id(child, child_path)],
                        evidence_refs=list(dict.fromkeys([*kept.evidence_refs, *child.evidence_refs])),
                    ))
                    continue
                seen[key] = index
                visit(child, child_path)
            return
        prepared.leaves[id(node)] = _Leaf(
            path=node_path, node=node, ids=[node_id(node, node_path)],
            evidence_refs=list(node.evidence_refs))

    visit(root, path)
    for leaf_key, leaf in prepared.leaves.items():
        leaf.ids.extend(prepared.aliases.get(leaf_key, []))
        leaf.evidence_refs = list(dict.fromkeys(
            [*leaf.evidence_refs, *prepared.extra_evidence.get(leaf_key, [])]))
    return prepared


@dataclass
class _Outcome:
    intervals: list[_Interval]
    missing: list[MissingInput] = field(default_factory=list)
    conflicts: set[str] = field(default_factory=set)
    infeasible: bool = False
    #: Sides ("lower"/"upper") a missing input could still tighten.
    open_sides: set[str] = field(default_factory=set)


def _leaf_outcome(leaf: _Leaf, domain: _Domain) -> _Outcome:
    node, resolved = leaf.node, leaf.resolved
    assert resolved is not None
    sources = frozenset(leaf.ids)
    if resolved.vacuous:
        return _Outcome(intervals=list(_UNIVERSE))
    if resolved.value is None:
        missing = [
            item.model_copy(update={"constraint_ids": list(leaf.ids)})
            for item in resolved.missing
        ]
        sides = {"lower"} if isinstance(node, LowerBoundConstraint) else (
            {"upper"} if isinstance(node, UpperBoundConstraint) else {"lower", "upper"})
        return _Outcome(intervals=list(_UNIVERSE), missing=missing, open_sides=sides)
    value = resolved.value
    if isinstance(node, LowerBoundConstraint):
        interval = (domain.lower(value, node.inclusive, sources), _POS_INF)
    elif isinstance(node, UpperBoundConstraint):
        interval = (_NEG_INF, domain.upper(value, node.inclusive, sources))
    elif isinstance(node, AtConstraint):
        interval = (domain.lower(value, True, sources), domain.upper(value, True, sources))
    else:  # pragma: no cover - _prepare only indexes leaves
        raise UnsupportedConstraintError(f"unsupported constraint {type(node).__name__}")
    return _Outcome(intervals=_normalise([interval], domain))


def _combine_all(outcomes: list[_Outcome], domain: _Domain) -> _Outcome:
    result = _Outcome(intervals=list(_UNIVERSE))
    for outcome in outcomes:
        result.missing.extend(outcome.missing)
        result.open_sides |= outcome.open_sides
        if outcome.infeasible:
            result.infeasible = True
            result.conflicts |= outcome.conflicts
            continue
        if result.infeasible:
            continue
        intersected, conflicts = _intersect(result.intervals, outcome.intervals, domain)
        if not intersected:
            result.infeasible = True
            result.conflicts |= conflicts
            result.intervals = []
        else:
            result.intervals = intersected
    if result.infeasible:
        result.intervals = []
    return result


def _combine_any(outcomes: list[_Outcome], domain: _Domain) -> _Outcome:
    alive = [item for item in outcomes if not item.infeasible]
    if not alive:
        conflicts: set[str] = set()
        missing: list[MissingInput] = []
        for item in outcomes:
            conflicts |= item.conflicts
            missing.extend(item.missing)
        return _Outcome(intervals=[], missing=missing, conflicts=conflicts, infeasible=True)
    intervals: list[_Interval] = []
    missing = []
    for item in alive:
        intervals.extend(item.intervals)
        missing.extend(item.missing)
    # An unresolved alternative could add or remove times anywhere.
    sides = {"lower", "upper"} if missing else set()
    return _Outcome(intervals=_normalise(intervals, domain), missing=missing, open_sides=sides)


def _evaluate_node(node: object, prepared: _Prepared, domain: _Domain) -> _Outcome:
    if isinstance(node, (AllConstraint, AnyConstraint)):
        children = [
            _evaluate_node(child, prepared, domain)
            for index, child in enumerate(node.items)
            if (id(node), index) not in prepared.skipped
        ]
        combine = _combine_all if isinstance(node, AllConstraint) else _combine_any
        return combine(children, domain)
    return _leaf_outcome(prepared.leaves[id(node)], domain)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class BaseWindow:
    """The legacy timing's resolved window for one occurrence."""

    source_id: str
    known: bool = True
    lower: Temporal | None = None
    upper: Temporal | None = None
    missing: list[MissingInput] = field(default_factory=list)


@dataclass
class FeasibilityResult:
    status: FeasibilityStatus
    intervals: list[FeasibleInterval]
    missing_inputs: list[MissingInput]
    conflicting_constraints: list[str]
    trace: list[ConstraintTraceEntry]
    relations: list[ConstraintRelation]

    def summary(self) -> dict[str, object]:
        """JSON-safe copy stored in evaluation explanations (persisted)."""
        return {
            "language_version": CONSTRAINT_LANGUAGE_VERSION,
            "status": self.status.value,
            "intervals": [item.model_dump(mode="json") for item in self.intervals],
            "missing_inputs": [item.model_dump(mode="json") for item in self.missing_inputs],
            "conflicting_constraints": list(self.conflicting_constraints),
            "relations": [item.model_dump(mode="json") for item in self.relations],
        }


def _dedupe_missing(items: list[MissingInput]) -> list[MissingInput]:
    merged: dict[tuple, MissingInput] = {}
    for item in items:
        key = (item.kind, item.code, item.occurrence, item.accepts, item.reason)
        if key in merged:
            existing = merged[key]
            merged[key] = existing.model_copy(update={"constraint_ids": list(dict.fromkeys(
                [*existing.constraint_ids, *item.constraint_ids]))})
        else:
            merged[key] = item
    return list(merged.values())


def _bound(end: _End, provisional: bool) -> FeasibleBound:
    if end.key is None:
        return FeasibleBound(value=None, inclusive=False, sources=[], provisional=provisional)
    return FeasibleBound(
        value=end.shown, inclusive=end.shown_inclusive,
        sources=sorted(end.sources), provisional=provisional)


def evaluate_feasibility(
    roots: list[tuple[str, object]],
    source: ValueSource,
    *,
    base: BaseWindow | None = None,
) -> FeasibilityResult:
    """Evaluate the conjunction of ``roots`` (path, constraint) and ``base``.

    Raises ``UnsupportedConstraintError`` for an UNSUPPORTED node; callers turn
    that into an UNRESOLVED occurrence (the validator already blocks approval).
    """
    prepared = [_prepare(root, path) for path, root in roots]
    values: list[Temporal] = []
    for item in prepared:
        for leaf in item.leaves.values():
            leaf.resolved = resolve_value(leaf.node.value, source)
            if leaf.resolved.value is not None:
                values.append(leaf.resolved.value)
    if base is not None and base.known:
        values.extend(value for value in (base.lower, base.upper) if value is not None)
    domain = _Domain()
    for value in values:
        if isinstance(value, datetime):
            domain.datetime_mode = True
            if value.tzinfo is not None and domain.zone is None:
                domain.zone = value.tzinfo

    outcomes: list[_Outcome] = []
    if base is not None:
        base_sources = frozenset({base.source_id})
        if not base.known:
            outcomes.append(_Outcome(
                intervals=list(_UNIVERSE),
                missing=[item.model_copy(update={"constraint_ids": [base.source_id]})
                         for item in base.missing],
                open_sides={"lower", "upper"}))
        else:
            lower = (domain.lower(base.lower, True, base_sources)
                     if base.lower is not None else _NEG_INF)
            upper = (domain.upper(base.upper, True, base_sources)
                     if base.upper is not None else _POS_INF)
            outcomes.append(_Outcome(intervals=_normalise([(lower, upper)], domain)))
    for item in prepared:
        outcomes.append(_evaluate_node(item.root, item, domain))
    combined = _combine_all(outcomes, domain)

    missing = _dedupe_missing(combined.missing)
    if combined.infeasible:
        status = FeasibilityStatus.INFEASIBLE
    elif missing:
        status = FeasibilityStatus.INCOMPLETE
    else:
        status = FeasibilityStatus.FEASIBLE
    incomplete = status == FeasibilityStatus.INCOMPLETE
    intervals = [
        FeasibleInterval(
            lower=_bound(lower, incomplete and "lower" in combined.open_sides),
            upper=_bound(upper, incomplete and "upper" in combined.open_sides),
        )
        for lower, upper in combined.intervals
    ]
    binding: set[str] = set()
    for interval in intervals:
        binding.update(interval.lower.sources)
        binding.update(interval.upper.sources)
    trace: list[ConstraintTraceEntry] = []
    for item in prepared:
        for leaf in item.leaves.values():
            resolved = leaf.resolved
            assert resolved is not None
            trace.append(ConstraintTraceEntry(
                constraint_id=leaf.ids[0], type=leaf.node.type,
                status="RESOLVED" if resolved.value is not None or resolved.vacuous else "MISSING",
                value=resolved.value,
                inclusive=getattr(leaf.node, "inclusive", True),
                inputs=resolved.inputs, evidence_refs=leaf.evidence_refs,
                binding=bool(set(leaf.ids) & (binding | combined.conflicts)),
            ))
    return FeasibilityResult(
        status=status,
        intervals=intervals,
        missing_inputs=missing,
        conflicting_constraints=sorted(combined.conflicts) if combined.infeasible else [],
        trace=trace,
        relations=[relation for item in prepared for relation in item.relations],
    )


def interval_contains(interval: FeasibleInterval, value: Temporal) -> bool:
    """Whether ``value`` (compared at the interval's own granularity) is inside."""

    def compare(left: Temporal, right: Temporal) -> int:
        if isinstance(left, datetime) != isinstance(right, datetime):
            left = left.date() if isinstance(left, datetime) else left
            right = right.date() if isinstance(right, datetime) else right
        return (left > right) - (left < right)

    lower, upper = interval.lower, interval.upper
    if lower.value is not None:
        order = compare(value, lower.value)
        if order < 0 or (order == 0 and not lower.inclusive):
            return False
    if upper.value is not None:
        order = compare(value, upper.value)
        if order > 0 or (order == 0 and not upper.inclusive):
            return False
    return True


def constraint_leaves(root: object, path: str) -> list[tuple[str, object]]:
    """Every node of a constraint tree with its structural path (pre-order)."""
    output: list[tuple[str, object]] = [(path, root)]
    for index, child in enumerate(getattr(root, "items", None) or []):
        output.extend(constraint_leaves(child, f"{path}/items/{index}"))
    return output


def value_nodes(value: object) -> list[object]:
    """A temporal value and every value nested in it (SHIFT chains)."""
    output = [value]
    inner = getattr(value, "reference", None)
    if isinstance(value, ShiftValue) and inner is not None:
        output.extend(value_nodes(inner))
    return output


def constraint_value_references(root: object) -> list[object]:
    """Anchor/event/activity values referenced anywhere in a constraint tree."""
    output = []
    for _, node in constraint_leaves(root, ""):
        value = getattr(node, "value", None)
        if value is None:
            continue
        for item in value_nodes(value):
            if isinstance(item, (AnchorValue, EventValue, ActivityValue)):
                output.append(item)
    return output
