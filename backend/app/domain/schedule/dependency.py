"""Dependency outcomes (P4-01) - pure, shared by every evaluation path.

A dependency has an optional CONDITION and a SOURCE event. The condition is
decided first (three-valued, the one ``evaluate_condition``), because a
dependency whose condition is FALSE does not exist for this patient and must not
be asked for a source, an actual or a manual date.

Decision table (rows are tried top to bottom; first match wins)

=========================  =========================  ===========================
condition                  source state               outcome
=========================  =========================  ===========================
FALSE                      (not consulted)            NOT_APPLICABLE
UNKNOWN                    (not consulted)            WAITING_FOR_CONDITION
TRUE / none                resolved or completed      SATISFIED
TRUE / none                waiting / paused / blocked WAITING_FOR_SOURCE
                           / on demand / not yet
                           evaluated
TRUE / none                not applicable, missed,    BLOCKED
                           cancelled or unresolved
=========================  =========================  ===========================

Aggregation over several dependencies of one event, strongest first:
BLOCKED, then WAITING_FOR_CONDITION, then WAITING_FOR_SOURCE; otherwise the
event's dependencies are satisfied (a NOT_APPLICABLE one is simply skipped).

NOT_APPLICABLE here means the DEPENDENCY does not apply. It says nothing about
the event: if the event's own timing references the same source, that timing
still waits for it (see ``evaluator._evaluate_event``).

The dependency type is carried into the outcome for explanation. It does not
change satisfaction by itself: whether a recorded actual is required is the
event's explicit ``dependency_mode`` (ACTUAL_PREVIOUS_EVENT / MANUAL), which is
never inferred from the type.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from .condition import TruthValue, evaluate_condition, unresolved_inputs
from .models import Dependency, Event, PatientEventStatus


class DependencyOutcomeKind(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    WAITING_FOR_CONDITION = "WAITING_FOR_CONDITION"
    WAITING_FOR_SOURCE = "WAITING_FOR_SOURCE"
    BLOCKED = "BLOCKED"


_SATISFIED_SOURCE = {PatientEventStatus.RESOLVED, PatientEventStatus.COMPLETED}
_WAITING_SOURCE = {
    PatientEventStatus.WAITING_FOR_ANCHOR, PatientEventStatus.WAITING_FOR_CONDITION,
    PatientEventStatus.WAITING_FOR_ASSIGNMENT, PatientEventStatus.BLOCKED,
    PatientEventStatus.PAUSED, PatientEventStatus.AVAILABLE_ON_DEMAND,
}


@dataclass(frozen=True)
class DependencyOutcome:
    source_event_code: str
    dependency_type: str
    outcome: DependencyOutcomeKind
    reason: str
    condition_result: str | None = None
    source_status: str | None = None
    missing_inputs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "source_event_code": self.source_event_code,
            "dependency_type": self.dependency_type,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "condition_result": self.condition_result,
            "source_status": self.source_status,
            "missing_inputs": list(self.missing_inputs),
        }


def _select(dependency: Dependency, source: Sequence) -> tuple[list[PatientEventStatus], str | None]:
    """The statuses that decide this dependency, and why none could be chosen.

    ``source`` items are statuses (legacy, event-level) or ``(index, status)``
    pairs. ANY looks at every occurrence; FIRST/LAST/CURRENT/NUMBER at one."""
    pairs: list[tuple[int, PatientEventStatus]] = []
    for position, item in enumerate(source):
        if isinstance(item, tuple):
            pairs.append((int(item[0]), item[1]))
        else:
            pairs.append((position, item))
    pairs.sort(key=lambda pair: pair[0])
    selector = dependency.source_occurrence
    if selector == "ANY" or not pairs:
        return [status for _, status in pairs], None
    if selector == "FIRST":
        return [pairs[0][1]], None
    if selector in ("LAST", "CURRENT"):
        return [pairs[-1][1]], None
    wanted = dependency.source_occurrence_number
    chosen = [status for index, status in pairs if index == wanted]
    if not chosen:
        return [], f"occurrence {wanted} of {dependency.source_event_code} has not been generated"
    return chosen, None


def selected_indexes(
    dependency: Dependency, source: Sequence,
) -> frozenset[int] | None:
    """The occurrence INDEXES an exact selector names, or None for ANY (the legacy,
    event-level reading). Resolved once and reused for the status check, the
    actual-visit / actual-treatment / manual checks and timing resolution, so a
    NUMBER selector can never fall back to "any occurrence's date"."""
    if dependency.source_occurrence == "ANY":
        return None
    indices = sorted(int(item[0]) if isinstance(item, tuple) else position
                     for position, item in enumerate(source))
    if dependency.source_occurrence == "FIRST":
        return frozenset(indices[:1])
    if dependency.source_occurrence in ("LAST", "CURRENT"):
        return frozenset(indices[-1:])
    return frozenset([dependency.source_occurrence_number])    # type: ignore[list-item]


def evaluate_dependency(
    dependency: Dependency,
    *,
    state: dict[str, object],
    source_statuses: Sequence,
) -> DependencyOutcome:
    code, kind = dependency.source_event_code, dependency.dependency_type
    condition_result: str | None = None
    if dependency.condition is not None:
        result = evaluate_condition(dependency.condition, state)
        condition_result = result.value
        if result is TruthValue.FALSE:
            return DependencyOutcome(
                code, kind, DependencyOutcomeKind.NOT_APPLICABLE,
                "its condition is false for this patient, so this dependency does not apply",
                condition_result)
        if result is TruthValue.UNKNOWN:
            missing = tuple(unresolved_inputs(dependency.condition, state))
            detail = f" (missing: {', '.join(missing)})" if missing else ""
            return DependencyOutcome(
                code, kind, DependencyOutcomeKind.WAITING_FOR_CONDITION,
                f"the condition on this dependency cannot be decided yet{detail}",
                condition_result, missing_inputs=missing)
    statuses, missing_reason = _select(dependency, source_statuses)
    if not statuses:
        return DependencyOutcome(
            code, kind, DependencyOutcomeKind.WAITING_FOR_SOURCE,
            missing_reason or f"{code} has not been evaluated for this patient",
            condition_result)
    if any(status in _SATISFIED_SOURCE for status in statuses):
        return DependencyOutcome(
            code, kind, DependencyOutcomeKind.SATISFIED, f"{code} is resolved",
            condition_result, PatientEventStatus.RESOLVED.value)
    if any(status in _WAITING_SOURCE for status in statuses):
        waiting = next(status for status in statuses if status in _WAITING_SOURCE)
        return DependencyOutcome(
            code, kind, DependencyOutcomeKind.WAITING_FOR_SOURCE,
            f"waiting for {code} ({waiting.value.lower().replace('_', ' ')})",
            condition_result, waiting.value)
    first = statuses[0]
    return DependencyOutcome(
        code, kind, DependencyOutcomeKind.BLOCKED,
        f"{code} is {first.value.lower().replace('_', ' ')} for this patient and cannot "
        "supply a date for this dependency",
        condition_result, first.value)


def evaluate_dependencies(
    event: Event,
    *,
    state: dict[str, object],
    source_statuses: Mapping[str, Sequence],
) -> list[DependencyOutcome]:
    return [
        evaluate_dependency(
            dependency, state=state,
            source_statuses=source_statuses.get(dependency.source_event_code, ()))
        for dependency in event.dependencies
    ]


def aggregate(outcomes: Sequence[DependencyOutcome]) -> DependencyOutcomeKind:
    """The strongest outcome that stops the event, else SATISFIED (or
    NOT_APPLICABLE when every dependency was skipped)."""
    kinds = {item.outcome for item in outcomes}
    for kind in (DependencyOutcomeKind.BLOCKED, DependencyOutcomeKind.WAITING_FOR_CONDITION,
                 DependencyOutcomeKind.WAITING_FOR_SOURCE):
        if kind in kinds:
            return kind
    if DependencyOutcomeKind.SATISFIED in kinds or not kinds:
        return DependencyOutcomeKind.SATISFIED
    return DependencyOutcomeKind.NOT_APPLICABLE


def active_source_codes(outcomes: Sequence[DependencyOutcome]) -> list[str]:
    """Sources that still count: a NOT_APPLICABLE dependency is never asked for
    an actual, a manual date, or a value in the timing context."""
    return [item.source_event_code for item in outcomes
            if item.outcome is DependencyOutcomeKind.SATISFIED]
