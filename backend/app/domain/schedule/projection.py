from __future__ import annotations

from pydantic import Field

from .models import Event, UniversalSchedule
from .timing import (
    AbsoluteTiming, ApproximateTiming, CycleDayTiming, NoLaterThanTiming,
    NominalWindowTiming, OffsetTiming, ProtocolDefinedTiming, RangeTiming,
    ProtocolDayTiming, TemporalAmount, TriggeredTiming, UnresolvedTiming, WithinTiming,
)
from .timing import (
    ActivityValue, AllConstraint, AnchorValue, AnyConstraint, AtConstraint, ConstraintTiming,
    EventValue, LiteralDateValue, LowerBoundConstraint, ProcessBoundaryValue, ShiftValue,
    StrictModel,
    UnsupportedConstraint, UpperBoundConstraint,
)


def _amount(value: TemporalAmount) -> str:
    unit = value.unit.value.lower()
    suffix = "" if abs(value.value) == 1 else "s"
    return f"{value.value} {unit}{suffix}"


def _reference(value: object) -> str:
    code = getattr(value, "code", None) or getattr(value, "event_code", None)
    return str(code).replace("_", " ").title() if code else "reference event"


def _value_display(value: object) -> str:
    if isinstance(value, AnchorValue):
        suffix = "" if value.accepts.value == "ACTUAL" else " (planned or actual)"
        return f"{_reference(value)}{suffix}"
    if isinstance(value, EventValue):
        selector = value.occurrence
        which = (f"occurrence {selector.number}" if selector.type == "NUMBER"
                 else f"{selector.type.lower()} occurrence")
        suffix = "" if value.accepts.value == "ACTUAL" else ", planned or actual"
        edge = f"{value.boundary.lower()} of " if value.boundary else ""
        return f"{edge}{_reference(value)} ({which}{suffix})"
    if isinstance(value, ProcessBoundaryValue):
        edge = "start" if value.boundary == "START" else "end"
        return f"actual {edge} of {value.process_code.replace('_', ' ').lower()}"
    if isinstance(value, ActivityValue):
        return f"actual {value.activity_code.replace('_', ' ').lower()} time"
    if isinstance(value, LiteralDateValue):
        return value.value.isoformat()
    if isinstance(value, ShiftValue):
        sign = "+" if value.amount.value >= 0 else "-"
        amount = value.amount.model_copy(update={"value": abs(value.amount.value)})
        return f"{_value_display(value.reference)} {sign} {_amount(amount)}"
    return "unknown value"


def constraint_display(node: object) -> str:
    """Plain-language rendering of a constraint tree (every restriction shown)."""
    if isinstance(node, LowerBoundConstraint):
        return f"{'on or after' if node.inclusive else 'after'} {_value_display(node.value)}"
    if isinstance(node, UpperBoundConstraint):
        return f"{'on or before' if node.inclusive else 'before'} {_value_display(node.value)}"
    if isinstance(node, AtConstraint):
        return f"at {_value_display(node.value)}"
    if isinstance(node, AllConstraint):
        return " and ".join(
            f"({constraint_display(item)})" if isinstance(item, AnyConstraint) else constraint_display(item)
            for item in node.items)
    if isinstance(node, AnyConstraint):
        return " or ".join(
            f"({constraint_display(item)})" if isinstance(item, AllConstraint) else constraint_display(item)
            for item in node.items)
    if isinstance(node, UnsupportedConstraint):
        return f"Not yet executable: {node.source_operator}"
    return "Unrecognised restriction"


def _references_of(value: object) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    while isinstance(value, ShiftValue):
        value = value.reference
    if isinstance(value, AnchorValue):
        output.append({"kind": "ANCHOR", "code": value.code, "accepts": value.accepts.value})
    elif isinstance(value, EventValue):
        output.append({"kind": "EVENT", "code": value.event_code,
                       "occurrence": value.occurrence.model_dump(mode="json"),
                       "accepts": value.accepts.value})
    elif isinstance(value, ActivityValue):
        output.append({"kind": "ACTIVITY", "code": value.activity_code, "accepts": "ACTUAL"})
    elif isinstance(value, ProcessBoundaryValue):
        output.append({"kind": "PROCESS", "code": value.process_code,
                       "boundary": value.boundary, "accepts": "ACTUAL"})
    return output


def constraint_view(node: object, path: str) -> dict[str, object]:
    """Formatter-friendly tree: every node carries ``summary`` (plain words),
    its effective id (explicit id or structural path) and its children, so a
    generic reviewer UI can show each restriction without knowing the AST."""
    value = getattr(node, "value", None)
    view: dict[str, object] = {
        "id": getattr(node, "id", None) or path,
        "path": path,
        "type": getattr(node, "type", type(node).__name__),
        "summary": constraint_display(node),
    }
    if hasattr(node, "inclusive"):
        view["inclusive"] = node.inclusive
    if value is not None:
        view["value_summary"] = _value_display(value)
        view["references"] = _references_of(value)
    if isinstance(node, UnsupportedConstraint):
        view.update(family=node.family, source_operator=node.source_operator,
                    source_text=node.source_text, executable=False)
    view["evidence_refs"] = list(getattr(node, "evidence_refs", []) or [])
    view["items"] = [
        constraint_view(child, f"{path}/items/{index}")
        for index, child in enumerate(getattr(node, "items", None) or [])
    ]
    return view


def timing_display(event: Event) -> tuple[str, str | None]:
    timing = event.timing
    if isinstance(timing, ConstraintTiming):
        text = constraint_display(timing.constraint)
        return text[:1].upper() + text[1:], None
    if isinstance(timing, AbsoluteTiming):
        return str(timing.value), None
    if isinstance(timing, OffsetTiming):
        direction = "after" if timing.offset.value >= 0 else "before"
        amount = timing.offset.model_copy(update={"value": abs(timing.offset.value)})
        return f"{_amount(amount)} {direction} {_reference(timing.reference)}", None
    if isinstance(timing, ProtocolDayTiming):
        return f"Day {timing.day} relative to {_reference(timing.reference)}", None
    if isinstance(timing, RangeTiming):
        return f"{_amount(timing.start)} to {_amount(timing.end)} from {_reference(timing.reference)}", None
    if isinstance(timing, NominalWindowTiming):
        temporary = event.model_copy(update={"timing": timing.nominal})
        nominal, _ = timing_display(temporary)
        return nominal, f"-{_amount(timing.window.before)}/+{_amount(timing.window.after)}"
    if isinstance(timing, WithinTiming):
        return f"Within {_amount(timing.duration)} {timing.direction.lower()} {_reference(timing.reference)}", None
    if isinstance(timing, NoLaterThanTiming):
        return f"No later than {_amount(timing.duration)} from {_reference(timing.reference)}", None
    if isinstance(timing, TriggeredTiming):
        nested = timing.timing_after_trigger
        amount = getattr(nested, "duration", None) or getattr(nested, "offset", None)
        return f"{nested.type.replace('_', ' ').title()} {_amount(amount)} after {_reference(timing.trigger)}", None
    if isinstance(timing, CycleDayTiming):
        cycle = timing.cycle.number or "current"
        return f"Cycle {cycle}, Day {timing.day}", None
    if isinstance(timing, ApproximateTiming):
        return f"Approximately {_amount(timing.offset)} from {_reference(timing.reference)}", None
    if isinstance(timing, UnresolvedTiming):
        return f"Needs review: {timing.reason}", None
    if isinstance(timing, ProtocolDefinedTiming):
        return "Timing requires site/sponsor confirmation", None
    return "Timing unavailable", None


class ScheduleDisplayProjection(StrictModel):
    event_id: str
    event_code: str
    title: str
    timing_display: str
    window_display: str | None = None
    event_type_display: str
    activities_display: list[str] = Field(default_factory=list)
    condition_display: str | None = None
    #: Every composable restriction on the event, in words (None when none).
    constraints_display: str | None = None
    status: str
    requires_review: bool
    evidence_refs: list[str] = Field(default_factory=list)


def project_schedule(schedule: UniversalSchedule) -> list[ScheduleDisplayProjection]:
    output = []
    for event in schedule.events:
        timing, window = timing_display(event)
        entities = {event.id, *(activity.id for activity in event.activities)}
        evidence_refs = list(dict.fromkeys([
            *event.evidence_refs,
            *(ref for activity in event.activities for ref in activity.evidence_refs),
            *(claim.evidence_id for claim in schedule.claim_evidence if claim.claim_entity_id in entities),
        ]))
        output.append(ScheduleDisplayProjection(
            event_id=str(event.id), event_code=event.code, title=event.display_name,
            timing_display=timing, window_display=window,
            event_type_display=event.event_type.replace("_", " ").title(),
            activities_display=[item.display_name for item in event.activities],
            condition_display="Conditional" if event.conditions else None,
            constraints_display=(
                constraint_display(event.constraints) if event.constraints is not None else None),
            status=event.interpretation_status.value,
            requires_review=event.requires_review,
            evidence_refs=[str(item) for item in evidence_refs],
        ))
    return output
