"""Protocol order for compiled events.

Facts arrive in the order the pages were read, so a compiled schedule listed
"30-day follow-up" above "Cycle 1 Day 1" whenever the follow-up section came
first in the document. Every screen shows ``schedule.events`` as stored, so the
order is settled once, here.

This is presentation order only. Nothing is dated, defaulted or inferred from
it: an event whose timing is unresolved is not placed by its name, it goes
last, and the evaluator never reads the position of an event in the list.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.schedule.models import Anchor, Event, RepeatBlock

_DAYS = {"MINUTE": 1 / 1440, "HOUR": 1 / 24, "DAY": 1, "WEEK": 7, "MONTH": 30.44, "YEAR": 365.25}

#: Study stages, in the order a participant goes through them.
PRE_TREATMENT, TREATMENT, POST_TREATMENT, ON_DEMAND, UNPLACED = 0, 1, 2, 3, 4

# Checked first: "LAST_DOSE" and "END_OF_TREATMENT" also contain treatment words.
_POST = re.compile(r"END_OF|COMPLETION|COMPLETED|LAST_|FINAL|DISCONTINU|WITHDRAW|PROGRESSION|"
                   r"RELAPSE|FOLLOW_UP|EARLY_TERMINATION")
_PRE = re.compile(r"CONSENT|SCREEN|REGISTRATION|RANDOMI[SZ]|ENROL|BASELINE|ELIGIBILITY")

Position = tuple[int, int, float, float]
_LAST: Position = (UNPLACED, 0, 0.0, 0.0)


def _days(amount: dict[str, Any] | None) -> float:
    if not amount:
        return 0.0
    return float(amount.get("value") or 0) * _DAYS.get(str(amount.get("unit")), 1)


def _anchor_stage(code: str) -> int:
    if _POST.search(code):
        return POST_TREATMENT
    if _PRE.search(code):
        return PRE_TREATMENT
    return TREATMENT


def _first_reference(node: Any) -> dict[str, Any] | None:
    """The first anchor or event a constraint tree counts from."""
    if isinstance(node, dict):
        if node.get("type") in {"ANCHOR", "EVENT"} and ("code" in node or "event_code" in node):
            return {"kind": node["type"], **node}
        node = list(node.values())
    if isinstance(node, list):
        for item in node:
            found = _first_reference(item)
            if found:
                return found
    return None


class _Sorter:
    def __init__(self, events: list[Event], anchors: list[Anchor], blocks: list[RepeatBlock]) -> None:
        self.events = {event.code: event for event in events}
        self.anchor_events = {anchor.code: anchor.source_event_code for anchor in anchors
                              if anchor.source_event_code}
        self.cycle_days = {block.cycle_plan.code: _days(block.cycle_plan.length.model_dump(mode="json"))
                           for block in blocks if block.cycle_plan}
        self.cache: dict[str, Position] = {}
        self.resolving: set[str] = set()

    def of_event(self, code: str) -> Position:
        if code in self.cache:
            return self.cache[code]
        event = self.events.get(code)
        if event is None or code in self.resolving:
            return _LAST
        self.resolving.add(code)
        position = self.of_timing(event.timing.model_dump(mode="json"))
        if position[0] < ON_DEMAND and event.activation == "ON_DEMAND":
            position = (ON_DEMAND, *position[1:])
        self.resolving.discard(code)
        self.cache[code] = position
        return position

    def of_reference(self, reference: dict[str, Any] | None, offset: float) -> Position:
        if not reference:
            return _LAST
        if reference.get("kind") == "EVENT":
            base = self.of_event(str(reference.get("event_code")))
        elif reference.get("kind") == "ANCHOR":
            code = str(reference.get("code"))
            source = self.anchor_events.get(code)
            base = self.of_event(source) if source else _LAST
            if base[0] == UNPLACED:
                base = (_anchor_stage(code), 0, 0.0, 0.0)
        else:
            return _LAST
        if base[0] == UNPLACED:
            return base
        stage, tier, first, second = base
        if stage == TREATMENT and tier == 0 and first + offset < 0:
            # "Within 14 days before the first dose" happens before treatment.
            return (PRE_TREATMENT, 0, first + offset, 0.0)
        # Counted from an unsized cycle: stay beside that cycle day.
        return (stage, tier, first, second + offset) if tier else (stage, tier, first + offset, second)

    def of_timing(self, timing: dict[str, Any]) -> Position:
        kind = timing.get("type")
        if kind in {"OFFSET", "APPROXIMATE"}:
            return self.of_reference(timing.get("reference"), _days(timing.get("offset")))
        if kind == "PROTOCOL_DAY":
            day = int(timing.get("day") or 0)
            return self.of_reference(timing.get("reference"), float(day - 1 if day > 0 else day))
        if kind == "RANGE":
            return self.of_reference(timing.get("reference"), _days(timing.get("start")))
        if kind == "NOMINAL_WITH_WINDOW":
            return self.of_timing(timing.get("nominal") or {})
        if kind == "WITHIN":
            sign = -1 if timing.get("direction") == "BEFORE" else 1
            return self.of_reference(timing.get("reference"), sign * _days(timing.get("duration")))
        if kind == "NO_LATER_THAN":
            return self.of_reference(timing.get("reference"), _days(timing.get("duration")))
        if kind == "TRIGGERED":
            after = timing.get("timing_after_trigger") or {}
            sign = -1 if after.get("direction") == "BEFORE" else 1
            return self.of_reference(timing.get("trigger"),
                                     sign * _days(after.get("offset") or after.get("duration")))
        if kind == "CYCLE_DAY":
            cycle = (timing.get("cycle") or {}).get("number") or 1
            day = float(timing.get("day") or 1)
            length = self.cycle_days.get(str(timing.get("cycle_reference")))
            if length:
                return (TREATMENT, 0, (cycle - 1) * length + day - 1, 0.0)
            # Cycle length not stated: keep cycles together, in cycle/day order,
            # after the visits that are dated in days.
            return (TREATMENT, 1, float(cycle), day)
        if kind == "CONSTRAINED":
            return self.of_reference(_first_reference(timing.get("constraint")), 0.0)
        return _LAST


def chronological(events: list[Event], anchors: list[Anchor], blocks: list[RepeatBlock]) -> list[Event]:
    """``events`` in protocol order; ties and unplaceable events keep document order."""
    sorter = _Sorter(events, anchors, blocks)
    order = {event.code: index for index, event in enumerate(events)}
    return sorted(events, key=lambda event: (sorter.of_event(event.code), order[event.code]))
