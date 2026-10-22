"""What a clinic must enter about a patient before the schedule can be dated correctly (pure, no storage).

From the rules alone we can say which patient facts are needed that nobody can know in advance: a dated event the
protocol counts from (surgery, first dose ...) and, when a rule counts in hours or minutes from it, its TIME of day.
The baseline/origin date is excluded: enrolment already always asks for it. A reviewer can add free entries (for
example the patient's weight); those are stored as review decisions by ``application.required_inputs``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .timing import AnchorReference, EventReference, TimeUnit

KINDS = ("DATE", "DATETIME", "TEXT", "NUMBER")
SUB_DAY = {TimeUnit.MINUTE, TimeUnit.HOUR, "MINUTE", "HOUR"}


@dataclass(frozen=True)
class RequiredInput:
    code: str
    label: str
    kind: str                 # DATE | DATETIME | TEXT | NUMBER
    source: str               # DERIVED | CUSTOM
    reason: str
    anchor_code: str | None = None   # set for DERIVED entries: the protocol anchor this value becomes

    def as_dict(self) -> dict:
        return asdict(self)


def _walk(value, anchors: set[str], events: set[str], subday: list[bool]) -> None:
    if isinstance(value, AnchorReference):
        anchors.add(value.code)
        return
    if isinstance(value, EventReference):
        events.add(value.event_code)
    unit = getattr(value, "unit", None)
    if unit in SUB_DAY:
        subday[0] = True
    if hasattr(value, "model_dump"):
        for name in type(value).model_fields:
            _walk(getattr(value, name, None), anchors, events, subday)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, anchors, events, subday)


def _footprint(event) -> tuple[set[str], set[str], bool]:
    anchors: set[str] = set()
    events: set[str] = set()
    subday = [False]
    for part in (event.timing, event.recurrence, event.recurrence_plan, event.calendar_recurrence, event.window):
        _walk(part, anchors, events, subday)
    return anchors, events, subday[0]


def derive_required_inputs(schedule) -> list[RequiredInput]:
    from .models import schedule_origin_anchor

    origin = schedule_origin_anchor(schedule)
    if origin is None:
        # No stated schedule origin: patients cannot be enrolled at all (the validator blocks it), so there is
        # nothing meaningful to list, and no anchor is guessed to be the baseline by its name.
        return []
    by_code = {event.code: event for event in schedule.events}
    footprint = {code: _footprint(event) for code, event in by_code.items()}
    cycle_starts = {block.cycle_plan.code: block.cycle_plan.start_reference.code
                    for block in schedule.repeat_blocks if block.cycle_plan is not None}
    for event in schedule.events:                        # a cycle day counts from its cycle's start anchor
        cycle = getattr(event.timing, "cycle_reference", None)
        if cycle in cycle_starts:
            footprint[event.code][0].add(cycle_starts[cycle])

    def reach(code: str, seen: set[str]) -> set[str]:
        if code in seen or code not in footprint:
            return set()
        seen.add(code)
        anchors, events, _ = footprint[code]
        found = set(anchors)
        for other in events:
            found |= reach(other, seen)
        return found

    used: dict[str, set[str]] = {}        # anchor code -> codes of the events that count from it
    needs_time: dict[str, set[str]] = {}
    for code, (_, _, subday) in footprint.items():
        for anchor in reach(code, set()):
            used.setdefault(anchor, set()).add(code)
            if subday:
                needs_time.setdefault(anchor, set()).add(code)
    inputs = []
    for anchor in schedule.anchors:
        if (origin is not None and anchor.code == origin.code) or anchor.source_condition_code:
            continue
        if anchor.code not in used:
            continue
        times = sorted(needs_time.get(anchor.code, ()))
        kind = "DATETIME" if times else "DATE"
        reason = (f"Visits {', '.join(sorted(used[anchor.code]))} count from this."
                  + (f" {', '.join(times)} count in hours or minutes from it, so the time of day is needed too."
                     if times else ""))
        inputs.append(RequiredInput(code=f"ANCHOR:{anchor.code}", label=anchor.display_name, kind=kind,
                                    source="DERIVED", reason=reason, anchor_code=anchor.code))
    return sorted(inputs, key=lambda item: item.code)


def inputs_hash(inputs: list[RequiredInput]) -> str:
    """Stable fingerprint of the effective list; an acknowledgement is only valid for the list it was given."""
    body = json.dumps(sorted((item.as_dict() for item in inputs), key=lambda row: row["code"]),
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()
