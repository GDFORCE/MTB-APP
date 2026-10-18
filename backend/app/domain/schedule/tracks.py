"""Schedule tracks at evaluation time (P5-01 / P5-02) - pure.

Three things happen here, all after (or around) the ordinary evaluation of an
event and none of them rewrites a protocol target:

* ``track_metadata`` explains, for CHANGED AND UNCHANGED occurrences alike,
  which track an occurrence belongs to, which policy governs it and what it
  counts from - so "baseline imaging did not move" has a stated reason.
* ``track_context`` applies a MANUAL_REBASE command: the track anchor counts from
  the date the command stated, for that track's events only.
* ``apply_overlays`` layers patient-specific operational facts - an appointment,
  an override, a cancellation - ON TOP of the protocol target and window, kept as
  separate values so an appointment is never mistaken for the protocol target.

A schedule with no tracks and a context with no command inputs passes through
untouched, so every earlier schedule evaluates exactly as before (NF-08).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .identity import event_logical_key
from .models import (
    EvaluatedEvent, Event, PatientContext, PatientEventStatus, ScheduleTrack, TrackRebaseInput,
    TrackRebasePolicy, UniversalSchedule,
)

_ACTUAL_BASES = {"ACTUAL_VISIT", "ACTUAL_TREATMENT", "ACTUAL_OR_PLANNED", "MANUAL_ANCHOR"}
_TERMINAL = {PatientEventStatus.COMPLETED, PatientEventStatus.MISSED, PatientEventStatus.CANCELLED}


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_date(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def track_of(event: Event, schedule: UniversalSchedule) -> ScheduleTrack | None:
    if event.track_id is None:
        return None
    return next((item for item in schedule.tracks if item.id == event.track_id), None)


def references_of(event: Event) -> list[dict[str, Any]]:
    """Every anchor / visit the event's own timing (and repeat start) counts from."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("kind")
            if kind == "ANCHOR" and node.get("code"):
                found.append({"kind": "ANCHOR", "code": node["code"]})
            elif kind == "EVENT" and node.get("event_code"):
                found.append({"kind": "EVENT", "code": node["event_code"],
                              "temporal_basis": node.get("temporal_basis")})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(event.timing.model_dump(mode="json"))
    if event.recurrence is not None:
        walk({"start": event.recurrence.start_reference.model_dump(mode="json")})
    unique: list[dict[str, Any]] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def track_metadata(event: Event, schedule: UniversalSchedule) -> dict[str, Any] | None:
    """None for a schedule without tracks (so legacy output is unchanged)."""
    if not schedule.tracks:
        return None
    refs = references_of(event)
    actual_refs = [item for item in refs
                   if item["kind"] == "EVENT" and item.get("temporal_basis") in _ACTUAL_BASES]
    if actual_refs:
        parts = [
            f"the {str(item['temporal_basis']).lower().replace('_', ' ')} of {item['code']}"
            for item in actual_refs]
        movement, reason = "FOLLOWS_ACTUAL", "counts from " + " and ".join(parts)
    elif refs:
        names = ", ".join(item["code"] for item in refs)
        movement, reason = "FIXED", (
            f"counts from {names}; no actual date of another visit moves it")
    else:
        movement, reason = "UNDETERMINED", "its timing does not name a reference"
    track = track_of(event, schedule)
    if track is None:
        scope = event.track_scope or "UNCLASSIFIED"
        return {"scope": scope, "counts_from": refs, "movement": movement, "reason": reason}
    return {
        "scope": "TRACK", "code": track.code, "track_type": track.track_type,
        "display_name": track.display_name, "rebase_policy": (
            track.rebase_policy.value if track.rebase_policy else None),
        "anchor_code": track.anchor_code, "process_code": track.process_code,
        "counts_from": refs, "movement": movement, "reason": reason,
    }


def rebase_variants(
    event: Event, context: PatientContext, schedule: UniversalSchedule,
) -> list[tuple["TrackRebaseInput", PatientContext]]:
    """One evaluation context per applicable MANUAL_REBASE (ascending by effective
    date). The caller evaluates the event once unrebased and once per variant and
    keeps, per occurrence, the latest variant whose period contains the
    occurrence's STABLE (unrebased) target. Selecting by the stable date - never
    by the date a rebase itself produces - is what keeps a command effective in
    2027 from touching 2026, and avoids a recursive boundary."""
    if not context.track_rebases:
        return []
    track = track_of(event, schedule)
    if track is None or track.rebase_policy is not TrackRebasePolicy.MANUAL_REBASE:
        return []
    variants = []
    for item in sorted(context.track_rebases, key=lambda row: (row.effective_at, row.command_id)):
        if item.track_code != track.code:
            continue
        scoped = context.model_copy(deep=True)
        scoped.anchors[item.anchor_code] = item.value
        variants.append((item, scoped))
    return variants


def choose_rebased(
    base: list["EvaluatedEvent"],
    variants: list[tuple["TrackRebaseInput", list["EvaluatedEvent"]]],
) -> list["EvaluatedEvent"]:
    """Per occurrence index: the latest rebase whose [effective_at, ended_at)
    contains the occurrence's stable target; otherwise the unrebased result."""
    def stable(item: "EvaluatedEvent") -> date | None:
        timing = item.timing
        return _as_date(timing.nominal_start) if timing and timing.nominal_start else None

    by_index = {item.occurrence_index: item for item in base}
    indices = sorted(set(by_index) | {r.occurrence_index for _, rows in variants for r in rows})
    output: list["EvaluatedEvent"] = []
    for index in indices:
        original = by_index.get(index)
        day = stable(original) if original is not None else None
        chosen = original
        for rebase, rows in variants:                     # ascending by effective date
            candidate = next((row for row in rows if row.occurrence_index == index), None)
            if candidate is None:
                continue
            anchor_day = day if day is not None else stable(candidate)
            if anchor_day is None:
                continue
            if anchor_day < rebase.effective_at:
                continue
            if rebase.ended_at is not None and anchor_day >= rebase.ended_at:
                continue
            explanation = dict(candidate.explanation)
            explanation["rebase"] = {
                "command_id": rebase.command_id, "effective_at": rebase.effective_at.isoformat(),
                "ended_at": rebase.ended_at.isoformat() if rebase.ended_at else None,
                "stable_target": anchor_day.isoformat()}
            chosen = candidate.model_copy(update={"explanation": explanation})
        if chosen is not None:
            output.append(chosen)
    return output


def _within(day: date | None, start: date, end: date | None) -> bool:
    """An occurrence with no date yet is not excluded by a period."""
    if day is None:
        return True
    return day >= start and (end is None or day < end)


def apply_overlays(
    event: Event, results: list[EvaluatedEvent], schedule: UniversalSchedule,
    context: PatientContext,
) -> list[EvaluatedEvent]:
    """Attach track metadata and operational layers; apply cancellations."""
    meta = track_metadata(event, schedule)
    overrides = {item.logical_key: item for item in context.occurrence_overrides}
    cancellations = {item.logical_key: item for item in context.occurrence_cancellations}
    if meta is None and not overrides and not cancellations:
        return results
    output: list[EvaluatedEvent] = []
    for item in results:
        key = event_logical_key(item.event_code, item.occurrence_index)
        explanation = dict(item.explanation)
        update: dict[str, Any] = {}
        if meta is not None:
            explanation["track"] = meta
        override = overrides.get(key)
        cancel = cancellations.get(key)
        stable_day = _as_date(item.timing.nominal_start) if item.timing and item.timing.nominal_start else None
        if override is not None and not _within(stable_day, override.effective_at, override.ended_at):
            override = None
        if cancel is not None and not _within(stable_day, cancel.effective_at, cancel.ended_at):
            cancel = None
        if override is not None or cancel is not None:
            operational: dict[str, Any] = dict(explanation.get("operational") or {})
            timing = item.timing
            operational["protocol"] = {
                "target": _iso(timing.nominal_start) if timing else None,
                "earliest": _iso(timing.earliest) if timing else None,
                "latest": _iso(timing.latest) if timing else None,
            }
            if override is not None:
                outside = None
                window_state = "UNKNOWN"
                if override.target_date is not None and timing is not None:
                    # Only bounds the protocol states count: an unstated window is
                    # NOT a zero-width window around the target.
                    low, high = _as_date(timing.earliest), _as_date(timing.latest)
                    if low is not None and high is not None:
                        window_state = "KNOWN"
                    elif low is not None or high is not None:
                        window_state = "PARTIAL"
                    if low is not None or high is not None:
                        outside = bool((low is not None and override.target_date < low)
                                       or (high is not None and override.target_date > high))
                        if window_state == "PARTIAL" and outside is False:
                            outside = None      # inside the known side only: not proven
                operational["override"] = {
                    "command_id": override.command_id,
                    "target_date": _iso(override.target_date),
                    "window_earliest": _iso(override.window_earliest),
                    "window_latest": _iso(override.window_latest),
                    "authority": override.authority, "reason": override.reason,
                    "outside_protocol_window": outside,
                    "protocol_window": window_state,
                    # an override is an EXCEPTION: it does not make the protocol
                    # window say anything different
                    "protocol_window_unchanged": True,
                }
                operational["effective_date"] = _iso(override.target_date) or operational.get(
                    "effective_date") or _iso(timing.nominal_start if timing else None)
            if cancel is not None:
                performed = (item.status in _TERMINAL or item.actual_time is not None)
                if performed:
                    operational["cancellation_ignored"] = {
                        "command_id": cancel.command_id,
                        "reason": "this occurrence already has a recorded outcome; "
                                  "history is preserved"}
                else:
                    operational["cancelled"] = {
                        "command_id": cancel.command_id, "reason": cancel.reason,
                        "effective_at": cancel.effective_at.isoformat()}
                    update["status"] = PatientEventStatus.CANCELLED
            explanation["operational"] = operational
        update["explanation"] = explanation
        output.append(item.model_copy(update=update))
    return output
