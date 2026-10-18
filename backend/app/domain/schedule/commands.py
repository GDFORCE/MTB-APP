"""Typed patient schedule commands (P5-03) - pure semantics.

A command is the clinical INTENT, recorded once and never rewritten. Its effect
on a schedule is computed, not stored: the ACTIVE commands (those no later
REVERSE command targets) are folded into inputs of the ordinary evaluator
(``command_inputs``), so previewing a command, committing it and reversing it all
go through the same calculation.

Which commands change protocol meaning and which do not
-------------------------------------------------------
=====================  =====================================================
RECORD_ACTUAL          a clinical FACT (visit / treatment date). Dependents
                       follow the explicit temporal bases. Not reversible: a
                       wrong actual is CORRECTED by a superseding fact.
CREATE_UNSCHEDULED     a clinical fact (an extra visit happened). Not reversible.
MARK_MISSED            a clinical fact (the occurrence did not happen).
RESCHEDULE_APPOINTMENT an OPERATIONAL booking. Never moves a protocol target;
                       outside the permitted window it is flagged.
OVERRIDE_OCCURRENCE    a patient-specific EXCEPTION shown beside the protocol
                       target/window; the protocol is unchanged.
CANCEL_OCCURRENCE      marks one future occurrence cancelled; history kept.
PAUSE/RESUME/STOP      process transitions on the track's process (HELD /
                       ACTIVE / STOPPED) under that process' resume policy.
REBASE_TRACK           MANUAL_REBASE tracks only: the track anchor counts from a
                       stated date.
REVERSE                a compensating command; the original stays in the ledger.
=====================  =====================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Sequence, Union

from pydantic import Field, model_validator

from .models import (
    HoldResumePolicy, OccurrenceCancellationInput, OccurrenceOverrideInput, ProcessState,
    ProcessTarget, ProcessTransition, ProcessTransitionReason, TrackRebaseInput,
    TrackRebasePolicy, UniversalSchedule,
)
from .timing import StrictModel

COMMAND_SEMANTICS_VERSION = "patient-commands.v1"


class CommandType(StrEnum):
    RECORD_ACTUAL = "RECORD_ACTUAL"
    CREATE_UNSCHEDULED = "CREATE_UNSCHEDULED"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    MARK_MISSED = "MARK_MISSED"
    CANCEL_OCCURRENCE = "CANCEL_OCCURRENCE"
    OVERRIDE_OCCURRENCE = "OVERRIDE_OCCURRENCE"
    PAUSE_TRACK = "PAUSE_TRACK"
    RESUME_TRACK = "RESUME_TRACK"
    REBASE_TRACK = "REBASE_TRACK"
    STOP_TRACK = "STOP_TRACK"
    REVERSE = "REVERSE"


#: Commands whose effect is a clinical fact; a REVERSE of one is refused (the
#: fact is corrected by supersession, never erased).
FACT_COMMANDS = frozenset({
    CommandType.RECORD_ACTUAL, CommandType.CREATE_UNSCHEDULED, CommandType.MARK_MISSED})
REVERSIBLE = frozenset({
    CommandType.RESCHEDULE_APPOINTMENT, CommandType.CANCEL_OCCURRENCE,
    CommandType.OVERRIDE_OCCURRENCE, CommandType.PAUSE_TRACK, CommandType.RESUME_TRACK,
    CommandType.REBASE_TRACK, CommandType.STOP_TRACK})
TRACK_COMMANDS = frozenset({
    CommandType.PAUSE_TRACK, CommandType.RESUME_TRACK, CommandType.REBASE_TRACK,
    CommandType.STOP_TRACK})
OCCURRENCE_COMMANDS = frozenset({
    CommandType.RECORD_ACTUAL, CommandType.RESCHEDULE_APPOINTMENT, CommandType.MARK_MISSED,
    CommandType.CANCEL_OCCURRENCE, CommandType.OVERRIDE_OCCURRENCE})


class RecordActual(StrictModel):
    type: Literal["RECORD_ACTUAL"] = "RECORD_ACTUAL"
    logical_key: str = Field(min_length=1)
    visit_date: date | None = None
    treatment_date: date | None = None
    supersedes_occurrence_id: str | None = None

    @model_validator(mode="after")
    def states_a_date(self) -> "RecordActual":
        if self.visit_date is None and self.treatment_date is None:
            raise ValueError("an actual states the visit date, the treatment date, or both")
        return self


class CreateUnscheduled(StrictModel):
    type: Literal["CREATE_UNSCHEDULED"] = "CREATE_UNSCHEDULED"
    event_code: str = Field(min_length=1)
    occurred_on: date
    visit_mode: str | None = None


class RescheduleAppointment(StrictModel):
    type: Literal["RESCHEDULE_APPOINTMENT"] = "RESCHEDULE_APPOINTMENT"
    logical_key: str = Field(min_length=1)
    new_date: date


class MarkMissed(StrictModel):
    type: Literal["MARK_MISSED"] = "MARK_MISSED"
    logical_key: str = Field(min_length=1)


class CancelOccurrence(StrictModel):
    type: Literal["CANCEL_OCCURRENCE"] = "CANCEL_OCCURRENCE"
    logical_key: str = Field(min_length=1)


class OverrideOccurrence(StrictModel):
    type: Literal["OVERRIDE_OCCURRENCE"] = "OVERRIDE_OCCURRENCE"
    logical_key: str = Field(min_length=1)
    target_date: date | None = None
    window_earliest: date | None = None
    window_latest: date | None = None
    #: Who authorised the exception (a named role/person), required.
    authority: str = Field(min_length=1)

    @model_validator(mode="after")
    def says_something(self) -> "OverrideOccurrence":
        if self.target_date is None and self.window_earliest is None and self.window_latest is None:
            raise ValueError("an override states a date, a window, or both")
        if (self.window_earliest is not None and self.window_latest is not None
                and self.window_latest < self.window_earliest):
            raise ValueError("an override window must not end before it starts")
        return self


class PauseTrack(StrictModel):
    type: Literal["PAUSE_TRACK"] = "PAUSE_TRACK"
    track_code: str = Field(min_length=1)


class ResumeTrack(StrictModel):
    type: Literal["RESUME_TRACK"] = "RESUME_TRACK"
    track_code: str = Field(min_length=1)
    #: Required when the track's process is WAIT_FOR_DECISION; otherwise the
    #: approved policy applies and nothing is chosen here.
    resume_policy: HoldResumePolicy | None = None


class RebaseTrack(StrictModel):
    type: Literal["REBASE_TRACK"] = "REBASE_TRACK"
    track_code: str = Field(min_length=1)
    anchor_code: str = Field(min_length=1)
    new_value: date


class StopTrack(StrictModel):
    type: Literal["STOP_TRACK"] = "STOP_TRACK"
    track_code: str = Field(min_length=1)


class ReverseCommand(StrictModel):
    type: Literal["REVERSE"] = "REVERSE"
    reverses_command_id: str = Field(min_length=1)


CommandPayload = Annotated[
    Union[RecordActual, CreateUnscheduled, RescheduleAppointment, MarkMissed, CancelOccurrence,
          OverrideOccurrence, PauseTrack, ResumeTrack, RebaseTrack, StopTrack, ReverseCommand],
    Field(discriminator="type")]


@dataclass(frozen=True)
class LedgerEntry:
    """What the pure rules need to know about one recorded command."""

    id: str
    type: CommandType
    payload: dict
    sequence: int
    effective_at: date
    reverses_command_id: str | None = None
    authority: str | None = None
    reason: str = ""

    @property
    def track_code(self) -> str | None:
        return self.payload.get("track_code")

    @property
    def logical_key(self) -> str | None:
        return self.payload.get("logical_key")


def active_entries(ledger: Sequence[LedgerEntry]) -> list[LedgerEntry]:
    """Commands still in force: not reversed, and not themselves a REVERSE."""
    reversed_ids = {item.reverses_command_id for item in ledger
                    if item.type is CommandType.REVERSE and item.reverses_command_id}
    return [item for item in sorted(ledger, key=lambda entry: entry.sequence)
            if item.type is not CommandType.REVERSE and item.id not in reversed_ids]


@dataclass
class CommandInputs:
    process_transitions: list[ProcessTransition] = field(default_factory=list)
    track_rebases: list[TrackRebaseInput] = field(default_factory=list)
    occurrence_overrides: list[OccurrenceOverrideInput] = field(default_factory=list)
    occurrence_cancellations: list[OccurrenceCancellationInput] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.process_transitions or self.track_rebases
                    or self.occurrence_overrides or self.occurrence_cancellations)


def _reversals(ledger: Sequence[LedgerEntry]) -> dict[str, LedgerEntry]:
    return {item.reverses_command_id: item for item in ledger
            if item.type is CommandType.REVERSE and item.reverses_command_id}


def command_inputs(schedule: UniversalSchedule, ledger: Sequence[LedgerEntry]) -> CommandInputs:
    """Fold the commands into evaluator inputs (the single calculation path for
    preview, commit and reversal).

    Time is explicit. A command carries its own ``effective_at``; it changes only
    what lies on or after that date. A REVERSE ends the effect of a period-bearing
    command (rebase, override, cancellation) at the reversal's ``effective_at``
    - earlier occurrences keep the effect - and is a plain retraction when it is
    effective on or before the command's own start. Process transitions have no
    period form: they are retracted whole (a later reversal date is refused when
    the reversal is accepted)."""
    tracks = {item.code: item for item in schedule.tracks}
    inputs = CommandInputs()
    reversed_by = _reversals(ledger)
    for entry in sorted(ledger, key=lambda item: item.sequence):
        if entry.type is CommandType.REVERSE:
            continue
        reversal = reversed_by.get(entry.id)
        ended_at = None
        if reversal is not None:
            if entry.type in (CommandType.PAUSE_TRACK, CommandType.RESUME_TRACK,
                              CommandType.STOP_TRACK):
                continue
            if reversal.effective_at <= entry.effective_at:
                continue
            ended_at = reversal.effective_at
        payload = entry.payload
        if entry.type in (CommandType.PAUSE_TRACK, CommandType.RESUME_TRACK, CommandType.STOP_TRACK):
            track = tracks.get(payload.get("track_code", ""))
            if track is None or not track.process_code:
                continue          # refused at validation; defensive here
            state = {CommandType.PAUSE_TRACK: ProcessState.HELD,
                     CommandType.RESUME_TRACK: ProcessState.ACTIVE,
                     CommandType.STOP_TRACK: ProcessState.STOPPED}[entry.type]
            policy = payload.get("resume_policy")
            inputs.process_transitions.append(ProcessTransition(
                id=f"command:{entry.id}",
                target=ProcessTarget(type="PROCESS", process_code=track.process_code),
                to_state=state, effective_at=entry.effective_at, status="CONFIRMED",
                reason=ProcessTransitionReason.DECISION, source="DECISION",
                source_ref=entry.id, authority=entry.authority,
                resume_policy=HoldResumePolicy(policy) if policy else None))
        elif entry.type is CommandType.REBASE_TRACK:
            inputs.track_rebases.append(TrackRebaseInput(
                command_id=entry.id, track_code=payload["track_code"],
                anchor_code=payload["anchor_code"],
                value=date.fromisoformat(str(payload["new_value"])),
                effective_at=entry.effective_at, ended_at=ended_at))
        elif entry.type is CommandType.OVERRIDE_OCCURRENCE:
            def _d(name: str) -> date | None:
                raw = payload.get(name)
                return date.fromisoformat(str(raw)) if raw else None
            inputs.occurrence_overrides.append(OccurrenceOverrideInput(
                command_id=entry.id, logical_key=payload["logical_key"],
                target_date=_d("target_date"), window_earliest=_d("window_earliest"),
                window_latest=_d("window_latest"), effective_at=entry.effective_at,
                ended_at=ended_at, authority=payload.get("authority") or entry.authority,
                reason=entry.reason or "patient-specific override"))
        elif entry.type is CommandType.CANCEL_OCCURRENCE:
            inputs.occurrence_cancellations.append(OccurrenceCancellationInput(
                command_id=entry.id, logical_key=payload["logical_key"],
                effective_at=entry.effective_at, ended_at=ended_at,
                reason=entry.reason or "occurrence cancelled"))
    return inputs


# ----------------------------------------------------------------------- rules

_TRANSITIONS = {CommandType.PAUSE_TRACK: "HELD", CommandType.RESUME_TRACK: "ACTIVE",
                CommandType.STOP_TRACK: "STOPPED"}


def _timeline(ledger: Sequence[LedgerEntry], track_code: str) -> list[LedgerEntry]:
    """The track's active transitions in EFFECTIVE order. Ties on the same day
    keep the order they were recorded in (a deterministic, documented rule)."""
    return sorted(
        (item for item in active_entries(ledger)
         if item.track_code == track_code and item.type in _TRANSITIONS),
        key=lambda item: (item.effective_at, item.sequence))


def track_state(ledger: Sequence[LedgerEntry], track_code: str,
                as_of: date | None = None) -> str:
    """ACTIVE / HELD / STOPPED at ``as_of`` (default: after every transition)."""
    state = "ACTIVE"
    for entry in _timeline(ledger, track_code):
        if as_of is not None and entry.effective_at > as_of:
            break
        state = _TRANSITIONS[entry.type]
    return state


def timeline_problems(entries: Sequence[LedgerEntry], track_code: str) -> list[dict[str, str]]:
    """Why the track's transitions, in effective order, do not make sense."""
    state = "ACTIVE"
    for entry in _timeline(entries, track_code):
        kind = entry.type
        when = entry.effective_at.isoformat()
        if state == "STOPPED":
            return [{"code": "TRACK_STOPPED",
                     "message": f"the track is already stopped before {when}; "
                                f"{kind.value} cannot take effect after a stop"}]
        if kind is CommandType.PAUSE_TRACK and state != "ACTIVE":
            return [{"code": "TRACK_NOT_ACTIVE",
                     "message": f"the track is already on hold on {when}"}]
        if kind is CommandType.RESUME_TRACK and state != "HELD":
            return [{"code": "TRACK_NOT_HELD",
                     "message": f"the track is not on hold on {when}, so there is nothing "
                                "to resume at that date; a resume must come after the "
                                "pause it ends"}]
        state = _TRANSITIONS[kind]
    return []


def validate_track_command(
    schedule: UniversalSchedule, ledger: Sequence[LedgerEntry], payload: CommandPayload,
    effective_at: date | None = None,
) -> list[dict[str, str]]:
    """Problems that make a track command invalid for this schedule/ledger."""
    problems: list[dict[str, str]] = []

    def refuse(code: str, message: str) -> None:
        problems.append({"code": code, "message": message})

    kind = CommandType(payload.type)
    if kind not in TRACK_COMMANDS:
        return problems
    track = next((item for item in schedule.tracks if item.code == payload.track_code), None)
    if track is None:
        refuse("TRACK_UNKNOWN", f"this schedule has no track {payload.track_code}")
        return problems
    if track.review_state != "CONFIRMED":
        refuse("TRACK_POLICY_UNRESOLVED",
               f"track {track.code} has not been confirmed in review, so it cannot be commanded")
        return problems
    state = track_state(ledger, track.code, effective_at)
    if kind in (CommandType.PAUSE_TRACK, CommandType.RESUME_TRACK, CommandType.STOP_TRACK):
        process = next((item for item in schedule.processes if item.code == track.process_code), None)
        if not track.process_code or process is None:
            refuse("TRACK_HAS_NO_PROCESS",
                   f"track {track.code} has no pause/stop process in the approved schedule")
            return problems
        if kind is CommandType.PAUSE_TRACK and state != "ACTIVE":
            refuse("TRACK_NOT_ACTIVE", f"track {track.code} is {state.lower()}, not active")
        if kind is CommandType.RESUME_TRACK:
            if state != "HELD":
                when = effective_at.isoformat() if effective_at else "now"
                refuse("TRACK_NOT_HELD",
                       f"track {track.code} is {state.lower()} on {when}, not on hold, so there is "
                       "nothing to resume at that date; a resume must come after the pause it ends")
            elif process.hold_resume_policy is HoldResumePolicy.WAIT_FOR_DECISION and \
                    getattr(payload, "resume_policy", None) is None:
                refuse("RESUME_POLICY_REQUIRED",
                       "the approved policy leaves the resume rule to a person: choose how "
                       "the cadence continues")
        if kind is CommandType.STOP_TRACK:
            if track.stoppable is not True:
                refuse("TRACK_NOT_STOPPABLE",
                       f"the approved policy for track {track.code} does not allow stopping it")
            elif state == "STOPPED":
                refuse("TRACK_ALREADY_STOPPED", f"track {track.code} is already stopped")
        if not problems and effective_at is not None:
            # The WHOLE resulting timeline must still be coherent: a backdated
            # transition may not break one that is already recorded.
            hypothetical = LedgerEntry(
                id="proposed", type=kind, payload={"track_code": track.code},
                sequence=max((item.sequence for item in ledger), default=0) + 1,
                effective_at=effective_at)
            for found in timeline_problems([*ledger, hypothetical], track.code):
                refuse(found["code"], found["message"])
    if kind is CommandType.REBASE_TRACK:
        if track.rebase_policy is not TrackRebasePolicy.MANUAL_REBASE:
            policy = track.rebase_policy.value if track.rebase_policy else "NOT STATED"
            refuse("REBASE_NOT_ALLOWED",
                   f"track {track.code} has rebase policy {policy}; only MANUAL_REBASE tracks "
                   "accept a manual rebase")
        elif track.anchor_code and payload.anchor_code != track.anchor_code:
            refuse("REBASE_ANCHOR_MISMATCH",
                   f"track {track.code} counts from {track.anchor_code}, not {payload.anchor_code}")
        if state == "STOPPED":
            refuse("TRACK_STOPPED", f"track {track.code} is stopped")
    return problems


def check_reversal(
    ledger: Sequence[LedgerEntry], target_id: str, effective_at: date | None = None,
) -> list[dict[str, str]]:
    """Why ``target_id`` cannot be reversed right now (empty = it can)."""
    entries = {item.id: item for item in ledger}
    target = entries.get(target_id)
    if target is None:
        return [{"code": "REVERSAL_TARGET_UNKNOWN",
                 "message": "that command is not part of this patient's schedule history"}]
    if target.type is CommandType.REVERSE:
        return [{"code": "REVERSAL_OF_REVERSAL",
                 "message": "a reversal cannot itself be reversed; issue the command again"}]
    if target.type in FACT_COMMANDS:
        return [{"code": "FACT_NOT_REVERSIBLE",
                 "message": "a recorded clinical fact is corrected by a superseding fact, "
                            "never erased by a reversal"}]
    if target.type not in REVERSIBLE:
        return [{"code": "NOT_REVERSIBLE", "message": f"{target.type.value} cannot be reversed"}]
    if any(item.type is CommandType.REVERSE and item.reverses_command_id == target_id
           for item in ledger):
        return [{"code": "ALREADY_REVERSED", "message": "that command was already reversed"}]
    newer = [
        item for item in active_entries(ledger)
        if item.sequence > target.sequence and item.id != target.id and (
            (target.track_code and item.track_code == target.track_code)
            or (target.logical_key and item.logical_key == target.logical_key))
    ]
    if (effective_at is not None and target.type in _TRANSITIONS
            and effective_at > target.effective_at):
        return [{"code": "REVERSAL_PERIOD_UNSUPPORTED",
                 "message": "a pause, resume or stop is withdrawn as a whole; to end its effect "
                            "at a later date record the opposite transition instead"}]
    if newer:
        return [{"code": "NEWER_COMMAND_DEPENDS",
                 "message": "newer commands apply to the same "
                            + ("track" if target.track_code else "occurrence")
                            + "; reverse them first: " + ", ".join(item.id for item in newer)}]
    remaining = [item for item in ledger if item.id != target_id]
    if target.track_code and target.type in _TRANSITIONS:
        for found in timeline_problems(remaining, target.track_code):
            return [{"code": "REVERSAL_BREAKS_TIMELINE", "message": found["message"]}]
    return []
