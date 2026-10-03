"""Scoped process states: interventions and pathways as concurrent processes
(design s5; RL-10, RL-11, AT-06, AT-07).

Each ``ProcessDefinition`` (drug A, drug B, radiotherapy, survival follow-up)
has its own dated state history built from CONFIRMED ``ProcessTransition``
facts - recorded decisions and confirmed protocol conditions. A transition
changes only the obligations linked to the process it targets:

* STOPPED / COMPLETED at t: linked occurrences after t are CANCELLED;
  occurrences on or before t, and anything performed, are untouched.
* HELD from a, resumed at b: linked occurrences are handled by the explicit
  ``HoldResumePolicy`` (NOMINAL / SHIFT_BY_HOLD / RESTART_FROM_RESUME), or wait
  for a decision (WAIT_FOR_DECISION / NONE_STATED). An open hold pauses.
* Participation decisions act by process KIND, never by name: withdrawal from
  treatment stops INTERVENTION processes (follow-up continues), end of
  follow-up completes FOLLOW_UP processes, withdrawal of consent stops every
  future obligation of the patient.

Pure and deterministic: no clock is read and no transition is ever inferred
from a date passing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .calendar_recurrence import HoldWindow
from .models import (
    DECIDABLE_RESUME_POLICIES,
    TERMINAL_PROCESS_STATES,
    EvaluatedEvent,
    HoldResumePolicy,
    OccurrenceFact,
    OccurrenceFactKind,
    PatientEventStatus,
    ProcessDefinition,
    ProcessKind,
    ProcessState,
    ProcessTransition,
    ProcessTransitionReason,
    UniversalSchedule,
)

PROCESS_SEMANTICS_VERSION = "uctsm-process-states.v1"

#: Statuses an occurrence can be moved out of by a process decision. Recorded
#: outcomes (COMPLETED, MISSED) and non-obligations are never touched.
_OPEN_STATUSES = frozenset({
    PatientEventStatus.RESOLVED, PatientEventStatus.WAITING_FOR_ANCHOR,
    PatientEventStatus.WAITING_FOR_CONDITION, PatientEventStatus.WAITING_FOR_ASSIGNMENT,
    PatientEventStatus.BLOCKED, PatientEventStatus.PAUSED, PatientEventStatus.UNRESOLVED,
    PatientEventStatus.AVAILABLE_ON_DEMAND,
})


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _recorded_key(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def effective_transitions(transitions: list[ProcessTransition]) -> list[ProcessTransition]:
    """CONFIRMED, non-superseded transitions in effective-time order.

    A correction supersedes by id; the superseded fact stays in the input
    (history) but no longer acts. Ties on the effective date keep recording
    order, so a late entry cannot reorder decisions made the same day.
    """
    superseded = {item.supersedes for item in transitions if item.supersedes}
    kept = [
        item for item in transitions
        if item.status == "CONFIRMED" and item.id not in superseded
    ]
    return sorted(kept, key=lambda item: (
        _as_date(item.effective_at), _recorded_key(item.recorded_at), item.id))


@dataclass
class ProcessTimeline:
    definition: ProcessDefinition
    changes: list[ProcessTransition] = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.definition.code

    def terminal(self) -> ProcessTransition | None:
        for change in self.changes:
            if change.to_state in TERMINAL_PROCESS_STATES:
                return change
        return None

    def started(self) -> ProcessTransition | None:
        for change in self.changes:
            if change.to_state == ProcessState.ACTIVE:
                return change
        return None

    def state_on(self, value: date) -> ProcessState | None:
        state: ProcessState | None = None
        for change in self.changes:
            if _as_date(change.effective_at) > value:
                break
            state = change.to_state
        return state

    def holds(self) -> tuple[list[HoldWindow], list[dict[str, object]]]:
        """Hold windows with their EFFECTIVE resume policy, plus notes on any
        resume decision that conflicts with a policy the protocol stated."""
        windows: list[HoldWindow] = []
        notes: list[dict[str, object]] = []
        stated = self.definition.hold_resume_policy
        held: ProcessTransition | None = None
        for change in self.changes:
            if change.to_state == ProcessState.HELD and held is None:
                held = change
            elif change.to_state == ProcessState.ACTIVE and held is not None:
                policy = stated
                if change.resume_policy is not None:
                    if stated in {HoldResumePolicy.WAIT_FOR_DECISION, HoldResumePolicy.NONE_STATED}:
                        policy = change.resume_policy
                    elif change.resume_policy != stated:
                        # Two readings of one rule: never pick either silently.
                        notes.append({
                            "conflict": "RESUME_POLICY_CONFLICT", "stated": stated.value,
                            "decided": change.resume_policy.value, "transition": change.id,
                        })
                        policy = HoldResumePolicy.WAIT_FOR_DECISION
                windows.append(HoldWindow(
                    start=_as_date(held.effective_at), end=_as_date(change.effective_at),
                    policy=policy, process_code=self.code,
                    transition_ids=(held.id, change.id)))
                held = None
            elif change.to_state in TERMINAL_PROCESS_STATES:
                if held is not None:
                    # Held then stopped: the hold ends in the stop, which the
                    # terminal rule handles; nothing after it resumes.
                    windows.append(HoldWindow(
                        start=_as_date(held.effective_at), end=None, policy=stated,
                        process_code=self.code, transition_ids=(held.id,)))
                    held = None
                break
        if held is not None:
            windows.append(HoldWindow(
                start=_as_date(held.effective_at), end=None, policy=stated,
                process_code=self.code, transition_ids=(held.id,)))
        return windows, notes


@dataclass
class ProcessStates:
    timelines: dict[str, ProcessTimeline] = field(default_factory=dict)
    event_process: dict[str, str] = field(default_factory=dict)
    consent_withdrawn: ProcessTransition | None = None
    #: Transitions whose target names no process of this schedule.
    unmatched: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.timelines and self.consent_withdrawn is None

    def process_for(self, event_code: str) -> ProcessTimeline | None:
        code = self.event_process.get(event_code)
        return self.timelines.get(code) if code else None

    def summary(self) -> dict[str, object]:
        return {
            "semantics": PROCESS_SEMANTICS_VERSION,
            "processes": {
                code: [{
                    "state": change.to_state.value,
                    "effective_at": change.effective_at.isoformat(),
                    "reason": change.reason.value, "transition": change.id,
                } for change in timeline.changes]
                for code, timeline in sorted(self.timelines.items())
            },
            "consent_withdrawn": (
                self.consent_withdrawn.effective_at.isoformat()
                if self.consent_withdrawn else None),
        }


def build_process_states(
    schedule: UniversalSchedule, transitions: list[ProcessTransition],
) -> ProcessStates:
    """Resolve every process's dated history from the effective transitions."""
    states = ProcessStates()
    for definition in schedule.processes:
        states.timelines[definition.code] = ProcessTimeline(definition)
        for code in definition.event_codes:
            states.event_process.setdefault(code, definition.code)
    for change in effective_transitions(transitions):
        target = change.target
        if target.type == "PATIENT":
            if states.consent_withdrawn is None:
                states.consent_withdrawn = change
            for timeline in states.timelines.values():
                timeline.changes.append(change)
            continue
        if target.type == "PROCESS":
            timeline = states.timelines.get(target.process_code or "")
            if timeline is None:
                states.unmatched.append(change.id)
                continue
            matched = [timeline]
        else:
            matched = [
                item for item in states.timelines.values() if item.definition.kind == target.kind]
        for timeline in matched:
            timeline.changes.append(change)
    for timeline in states.timelines.values():
        timeline.changes.sort(key=lambda item: (
            _as_date(item.effective_at), _recorded_key(item.recorded_at), item.id))
    return states


def _occurrence_date(item: EvaluatedEvent) -> date | None:
    timing = item.timing
    if timing is None:
        return None
    value = timing.nominal_start or timing.earliest or timing.latest
    return _as_date(value) if value is not None else None


def _performed(item: EvaluatedEvent, facts: dict[tuple[str, int], OccurrenceFact]) -> bool:
    fact = facts.get((item.event_code, item.occurrence_index))
    return fact is not None and fact.kind == OccurrenceFactKind.PERFORMED


def _with_state(
    item: EvaluatedEvent, status: PatientEventStatus, reason: str,
    details: dict[str, object], *, clear_timing: bool = False,
) -> EvaluatedEvent:
    explanation = dict(item.explanation)
    explanation["process_state"] = details
    explanation["reason"] = reason
    update: dict[str, object] = {"status": status, "explanation": explanation}
    if clear_timing:
        update["timing"] = None
    return item.model_copy(update=update)


def _shift(item: EvaluatedEvent, days: int, details: dict[str, object]) -> EvaluatedEvent:
    if days == 0 or item.timing is None:
        return item
    if item.feasibility is not None:
        return _with_state(
            item, PatientEventStatus.UNRESOLVED,
            "the process resumed after a hold, but this occurrence is dated by a "
            "constraint set; moving it by the hold would ignore those restrictions, so "
            "it needs review", {**details, "shift_days": days}, clear_timing=True)
    delta = timedelta(days=days)
    timing = item.timing.model_copy(update={
        name: (getattr(item.timing, name) + delta if getattr(item.timing, name) is not None else None)
        for name in ("nominal_start", "nominal_end", "earliest", "latest")
    })
    explanation = dict(item.explanation)
    explanation["process_state"] = {**details, "shift_days": days}
    explanation["reason"] = (
        f"process {details['process']} resumed after a hold; this occurrence moved "
        f"{days} days later ({details['policy'].lower().replace('_', ' ')})")
    return item.model_copy(update={"timing": timing, "explanation": explanation})


def apply_process_states(
    event_code: str,
    results: list[EvaluatedEvent],
    states: ProcessStates,
    facts: dict[tuple[str, int], OccurrenceFact],
    *,
    holds_applied: bool = False,
) -> list[EvaluatedEvent]:
    """Apply consent withdrawal and the linked process's history to one event.

    ``holds_applied`` is True for calendar series, whose holds are laid back
    onto the weekday grid during expansion instead of shifted here.
    """
    if states.empty:
        return results
    output = list(results)
    withdrawn = states.consent_withdrawn
    if withdrawn is not None:
        output = [
            _terminate(item, withdrawn, "PATIENT", facts) for item in output]
    timeline = states.process_for(event_code)
    if timeline is None:
        return output
    terminal = timeline.terminal()
    if not holds_applied:
        windows, notes = timeline.holds()
        for window in windows:
            output = _apply_hold(output, window, facts, notes)
    if terminal is not None and terminal is not withdrawn:
        output = [_terminate(item, terminal, timeline.code, facts) for item in output]
    return output


def _terminate(
    item: EvaluatedEvent, change: ProcessTransition, scope: str,
    facts: dict[tuple[str, int], OccurrenceFact],
) -> EvaluatedEvent:
    if item.status not in _OPEN_STATUSES or _performed(item, facts):
        return item
    effective = _as_date(change.effective_at)
    when = _occurrence_date(item)
    if when is not None and when <= effective:
        return item
    details = {
        "process": scope, "state": change.to_state.value,
        "effective_at": change.effective_at.isoformat(), "reason": change.reason.value,
        "transition": change.id, "source": change.source, "source_ref": change.source_ref,
        "semantics": PROCESS_SEMANTICS_VERSION,
    }
    if scope == "PATIENT":
        reason = "the patient withdrew consent; no further study obligations apply"
    elif change.reason == ProcessTransitionReason.END_OF_FOLLOW_UP:
        reason = f"follow-up process {scope} ended; this occurrence is no longer due"
    elif change.to_state == ProcessState.COMPLETED:
        reason = f"process {scope} was completed; this occurrence is no longer due"
    elif change.reason == ProcessTransitionReason.WITHDRAWAL_FROM_TREATMENT:
        reason = f"the patient was withdrawn from treatment ({scope} stopped); follow-up is unaffected"
    else:
        reason = f"process {scope} was stopped; only obligations of {scope} are cancelled"
    return _with_state(item, PatientEventStatus.CANCELLED, reason, details)


def _apply_hold(
    results: list[EvaluatedEvent],
    window: HoldWindow,
    facts: dict[tuple[str, int], OccurrenceFact],
    notes: list[dict[str, object]],
) -> list[EvaluatedEvent]:
    details: dict[str, object] = {
        "process": window.process_code, "state": ProcessState.HELD.value,
        "hold_start": window.start.isoformat(),
        "resumed_on": window.end.isoformat() if window.end else None,
        "policy": window.policy.value, "transitions": list(window.transition_ids),
        "semantics": PROCESS_SEMANTICS_VERSION,
    }
    if notes:
        details["notes"] = notes
    ordered = sorted(results, key=lambda item: item.occurrence_index)
    affected = [
        item for item in ordered
        if item.status == PatientEventStatus.RESOLVED and not _performed(item, facts)
        and (when := _occurrence_date(item)) is not None and when >= window.start
    ]
    if not affected:
        return results
    replaced: dict[int, EvaluatedEvent] = {}
    if window.end is None:
        for item in affected:
            replaced[id(item)] = _with_state(
                item, PatientEventStatus.PAUSED,
                f"process {window.process_code} is held; this occurrence is not due until it "
                "resumes", details)
    elif window.policy == HoldResumePolicy.NOMINAL:
        for item in affected:
            if _occurrence_date(item) < window.end:
                replaced[id(item)] = _with_state(
                    item, PatientEventStatus.PAUSED,
                    f"process {window.process_code} was held on this date; it resumed on its "
                    "original grid, so this occurrence is not made up", details)
    elif window.policy == HoldResumePolicy.SHIFT_BY_HOLD:
        days = (window.end - window.start).days
        for item in affected:
            replaced[id(item)] = _shift(item, days, details)
    elif window.policy == HoldResumePolicy.RESTART_FROM_RESUME:
        first = _occurrence_date(affected[0])
        days = (window.end - first).days
        for item in affected:
            replaced[id(item)] = _shift(item, days, details)
    else:
        for item in affected:
            replaced[id(item)] = _with_state(
                item, PatientEventStatus.UNRESOLVED,
                f"process {window.process_code} resumed on {window.end.isoformat()}, but "
                f"its resume policy is {window.policy.value.lower().replace('_', ' ')}; "
                "this occurrence waits for a decision on how the schedule continues",
                details, clear_timing=True)
    return [replaced.get(id(item), item) for item in results]


def validate_resume_policy(value: object) -> HoldResumePolicy | None:
    """A conditional RESUME_PROCESS action's optional ``resume_policy``."""
    if value is None:
        return None
    policy = HoldResumePolicy(str(value))
    if policy not in DECIDABLE_RESUME_POLICIES:
        raise ValueError(f"{policy.value} is not a resume decision")
    return policy


__all__ = [
    "PROCESS_SEMANTICS_VERSION", "ProcessStates", "ProcessTimeline",
    "apply_process_states", "build_process_states", "effective_transitions",
    "validate_resume_policy", "ProcessKind",
]
