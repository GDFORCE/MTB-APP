"""Does anything in a schedule depend on a given event? (P1-04)

Deciding that a recorded occurrence is irrelevant means NOT re-evaluating, so a
missed dependency would leave a stale date in front of a clinician. The check is
therefore deliberately conservative: an event is relevant if its code appears as
a VALUE anywhere in the schedule outside that event's own identity fields -
timing and recurrence references, dependencies, constraints, derived anchors,
confinement episodes, cycle plans, process definitions, conditional actions.
Over-reporting costs one extra evaluation; under-reporting costs a wrong date.
"""

from __future__ import annotations

from typing import Any

from .models import UniversalSchedule

#: Keys that carry a name, a label or free text rather than a reference.
_IDENTITY_KEYS = frozenset({
    "code", "protocol_label", "display_name", "normalized_name", "description",
    "text", "source_text", "marker", "message", "name",
})


def _mentions(node: Any, code: str, *, skip: frozenset[str]) -> bool:
    if isinstance(node, dict):
        return any(
            _mentions(value, code, skip=skip)
            for key, value in node.items() if key not in skip)
    if isinstance(node, (list, tuple)):
        return any(_mentions(item, code, skip=skip) for item in node)
    return isinstance(node, str) and node == code


def references_event(schedule: UniversalSchedule, code: str) -> bool:
    """True when some other part of the schedule refers to event ``code``."""
    for event in schedule.events:
        payload = event.model_dump(mode="json")
        if event.code == code:
            # an event may count from its own previous occurrence
            payload.pop("code", None)
            payload.pop("activities", None)
            timing = payload.get("timing")
            if _mentions(timing, code, skip=_IDENTITY_KEYS) \
                    or _mentions(payload.get("recurrence"), code, skip=_IDENTITY_KEYS) \
                    or _mentions(payload.get("constraints"), code, skip=_IDENTITY_KEYS):
                return True
            continue
        if _mentions(payload, code, skip=_IDENTITY_KEYS):
            return True
    sections = (
        schedule.anchors, schedule.conditional_definitions, schedule.repeat_blocks,
        schedule.confinement_episodes, schedule.processes, schedule.dimensions)
    for items in sections:
        for item in items:
            if _mentions(item.model_dump(mode="json"), code, skip=_IDENTITY_KEYS):
                return True
    return False
