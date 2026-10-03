from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .models import UniversalSchedule
from .timing import StrictModel


class FieldChange(StrictModel):
    event_code: str
    field_path: str
    before: object | None = None
    after: object | None = None


class ChangeType(StrEnum):
    """What kind of change an amendment made (requirement doc 9 s10)."""

    VISIT_ADDED = "VISIT_ADDED"
    VISIT_REMOVED = "VISIT_REMOVED"
    VISIT_RENAMED = "VISIT_RENAMED"
    TIMING_CHANGED = "TIMING_CHANGED"
    WINDOW_WIDENED = "WINDOW_WIDENED"
    WINDOW_NARROWED = "WINDOW_NARROWED"
    ACTIVITY_ADDED = "ACTIVITY_ADDED"
    ACTIVITY_REMOVED = "ACTIVITY_REMOVED"
    ACTIVITY_CHANGED = "ACTIVITY_CHANGED"
    APPLICABILITY_CHANGED = "APPLICABILITY_CHANGED"
    CONDITION_CHANGED = "CONDITION_CHANGED"
    DEPENDENCY_CHANGED = "DEPENDENCY_CHANGED"
    RECURRENCE_CHANGED = "RECURRENCE_CHANGED"
    # RL-04/05: a composable restriction set changed meaning (normalised
    # comparison - ids/evidence/ordering of identical items do not count).
    CONSTRAINTS_CHANGED = "CONSTRAINTS_CHANGED"
    # Only ids or evidence of otherwise identical restrictions changed.
    CONSTRAINT_PROVENANCE_CHANGED = "CONSTRAINT_PROVENANCE_CHANGED"
    VISIT_MODE_CHANGED = "VISIT_MODE_CHANGED"
    ANCHOR_ADDED = "ANCHOR_ADDED"
    ANCHOR_REMOVED = "ANCHOR_REMOVED"
    # A repeat block carries how a treatment block repeats, resumes and - for
    # cycles - how each cycle start is dated (doc F1-F3, I1-I3). Changing any of
    # that re-dates every patient in the block, so it is never silent.
    REPEAT_BLOCK_ADDED = "REPEAT_BLOCK_ADDED"
    REPEAT_BLOCK_REMOVED = "REPEAT_BLOCK_REMOVED"
    REPEAT_BLOCK_CHANGED = "REPEAT_BLOCK_CHANGED"
    OTHER = "OTHER"


class Significance(StrEnum):
    """Whether a change can affect a patient already on the study."""

    CLINICAL = "CLINICAL"
    ADMINISTRATIVE = "ADMINISTRATIVE"


#: Changes that cannot alter when a patient is seen or what is done to them.
#: Everything NOT listed here is treated as clinical, because an unclassified
#: change is one nobody has looked at yet.
ADMINISTRATIVE_CHANGES = frozenset({
    ChangeType.VISIT_RENAMED,
    ChangeType.WINDOW_WIDENED,
    ChangeType.CONSTRAINT_PROVENANCE_CHANGED,
})


class TypedChange(StrictModel):
    """One classified difference, in words a reviewer can act on."""

    change_type: ChangeType
    significance: Significance
    entity_type: str = "EVENT"
    entity_code: str
    summary: str
    detail: str | None = None
    before: object | None = None
    after: object | None = None


class ScheduleDiff(StrictModel):
    added_events: list[str] = Field(default_factory=list)
    removed_events: list[str] = Field(default_factory=list)
    changes: list[FieldChange] = Field(default_factory=list)
    #: Doc 9 s10. The raw `changes` above stay for callers that need the values;
    #: this is the vocabulary a human reviews.
    typed_changes: list[TypedChange] = Field(default_factory=list)

    def clinically_significant(self) -> list[TypedChange]:
        return [
            item for item in self.typed_changes
            if item.significance == Significance.CLINICAL
        ]


def compare_schedule_versions(left: UniversalSchedule, right: UniversalSchedule) -> ScheduleDiff:
    left_events = {event.code: event for event in left.events}
    right_events = {event.code: event for event in right.events}
    result = ScheduleDiff(
        added_events=sorted(right_events.keys() - left_events.keys()),
        removed_events=sorted(left_events.keys() - right_events.keys()),
    )
    fields = (
        "protocol_label", "display_name", "event_type", "timing", "window",
        "applicability", "conditions", "dependencies", "recurrence", "recurrence_plan",
        "calendar_recurrence", "activities", "constraints",
    )
    for code in sorted(left_events.keys() & right_events.keys()):
        before = left_events[code].model_dump(mode="json")
        after = right_events[code].model_dump(mode="json")
        for field in fields:
            if before.get(field) != after.get(field):
                result.changes.append(FieldChange(
                    event_code=code, field_path=field,
                    before=before.get(field), after=after.get(field),
                ))
    result.typed_changes = _classify(left, right)
    return result


def _typed(
    change_type: ChangeType, entity_code: str, summary: str, **changes,
) -> TypedChange:
    return TypedChange(
        change_type=change_type,
        significance=(
            Significance.ADMINISTRATIVE if change_type in ADMINISTRATIVE_CHANGES
            else Significance.CLINICAL
        ),
        entity_code=entity_code, summary=summary, **changes,
    )


def _classify(left: UniversalSchedule, right: UniversalSchedule) -> list[TypedChange]:
    left_events = {event.code: event for event in left.events}
    right_events = {event.code: event for event in right.events}
    output: list[TypedChange] = []

    for code in sorted(right_events.keys() - left_events.keys()):
        output.append(_typed(
            ChangeType.VISIT_ADDED, code,
            f"{right_events[code].display_name} is a new visit",
        ))
    for code in sorted(left_events.keys() - right_events.keys()):
        output.append(_typed(
            ChangeType.VISIT_REMOVED, code,
            f"{left_events[code].display_name} is no longer in the protocol",
            detail=(
                "patients already scheduled for it need a decision; the visit is "
                "not removed from their history"
            ),
        ))

    for code in sorted(left_events.keys() & right_events.keys()):
        output.extend(_classify_event(left_events[code], right_events[code]))

    left_anchors = {item.code: item for item in left.anchors}
    right_anchors = {item.code: item for item in right.anchors}
    for code in sorted(right_anchors.keys() - left_anchors.keys()):
        output.append(_typed(
            ChangeType.ANCHOR_ADDED, code,
            f"{right_anchors[code].display_name} is a new anchor",
            entity_type="ANCHOR",
            detail="sites will need to supply this date for enrolled patients",
        ))
    for code in sorted(left_anchors.keys() - right_anchors.keys()):
        output.append(_typed(
            ChangeType.ANCHOR_REMOVED, code,
            f"{left_anchors[code].display_name} is no longer an anchor",
            entity_type="ANCHOR",
        ))
    output.extend(_classify_repeat_blocks(left, right))
    return output


#: Repeat-block fields that are identity or provenance, not schedule meaning.
_BLOCK_IDENTITY_FIELDS = {"id", "evidence_refs"}


def _classify_repeat_blocks(left: UniversalSchedule, right: UniversalSchedule) -> list[TypedChange]:
    """Added, removed and changed repeat blocks, matched by code.

    A block's id is regenerated for every version, so it is not compared; its
    evidence links are provenance, not meaning. Everything else - member
    events, dependency and resume mode, the cycle plan (length, dependency
    mode, termination) - decides patient dates.
    """
    left_blocks = {item.code: item for item in left.repeat_blocks}
    right_blocks = {item.code: item for item in right.repeat_blocks}
    output: list[TypedChange] = []
    for code in sorted(right_blocks.keys() - left_blocks.keys()):
        output.append(_typed(
            ChangeType.REPEAT_BLOCK_ADDED, code,
            f"{right_blocks[code].display_name} is a new repeating block",
            entity_type="REPEAT_BLOCK",
            after=right_blocks[code].model_dump(mode="json", exclude=_BLOCK_IDENTITY_FIELDS),
        ))
    for code in sorted(left_blocks.keys() - right_blocks.keys()):
        output.append(_typed(
            ChangeType.REPEAT_BLOCK_REMOVED, code,
            f"{left_blocks[code].display_name} is no longer a repeating block",
            entity_type="REPEAT_BLOCK",
            detail="its visits no longer pause, resume or stop together",
            before=left_blocks[code].model_dump(mode="json", exclude=_BLOCK_IDENTITY_FIELDS),
        ))
    for code in sorted(left_blocks.keys() & right_blocks.keys()):
        before = left_blocks[code].model_dump(mode="json", exclude=_BLOCK_IDENTITY_FIELDS)
        after = right_blocks[code].model_dump(mode="json", exclude=_BLOCK_IDENTITY_FIELDS)
        changed = sorted(key for key in before.keys() | after.keys()
                         if before.get(key) != after.get(key))
        if not changed:
            continue
        output.append(_typed(
            ChangeType.REPEAT_BLOCK_CHANGED, code,
            f"{right_blocks[code].display_name} repeats differently "
            f"({', '.join(changed)} changed)",
            entity_type="REPEAT_BLOCK",
            detail="every patient in this block is recalculated",
            before={key: before.get(key) for key in changed},
            after={key: after.get(key) for key in changed},
        ))
    return output


def _classify_event(before, after) -> list[TypedChange]:
    output: list[TypedChange] = []
    code = before.code

    if before.display_name != after.display_name:
        output.append(_typed(
            ChangeType.VISIT_RENAMED, code,
            f"renamed from {before.display_name!r} to {after.display_name!r}",
            before=before.display_name, after=after.display_name,
        ))

    before_timing = before.timing.model_dump(mode="json")
    after_timing = after.timing.model_dump(mode="json")
    if before_timing != after_timing:
        window = _window_change(code, before.timing, after.timing)
        if window is not None:
            output.append(window)
        else:
            output.append(_typed(
                ChangeType.TIMING_CHANGED, code,
                f"{after.display_name} happens at a different point in the protocol",
                detail="every patient dependent on this visit is recalculated",
                before=before_timing, after=after_timing,
            ))

    if _dump(before.window) != _dump(after.window):
        output.append(_event_window_change(code, after.display_name, before.window, after.window))

    before_activities = {
        item.code or item.display_name: item for item in before.activities}
    after_activities = {
        item.code or item.display_name: item for item in after.activities}
    for name in sorted(after_activities.keys() - before_activities.keys()):
        output.append(_typed(
            ChangeType.ACTIVITY_ADDED, code,
            f"{after_activities[name].display_name} was added to {after.display_name}",
        ))
    for name in sorted(before_activities.keys() - after_activities.keys()):
        output.append(_typed(
            ChangeType.ACTIVITY_REMOVED, code,
            f"{before_activities[name].display_name} is no longer done at "
            f"{after.display_name}",
        ))
    for name in sorted(before_activities.keys() & after_activities.keys()):
        left_item = before_activities[name].model_dump(mode="json")
        right_item = after_activities[name].model_dump(mode="json")
        left_item.pop("id", None)
        right_item.pop("id", None)
        if left_item != right_item:
            output.append(_typed(
                ChangeType.ACTIVITY_CHANGED, code,
                f"{after_activities[name].display_name} changed at {after.display_name}",
                before=left_item, after=right_item,
            ))

    for field, change_type, phrase in (
        ("applicability", ChangeType.APPLICABILITY_CHANGED,
         "applies to a different group of patients"),
        ("conditions", ChangeType.CONDITION_CHANGED,
         "is triggered by a different condition"),
        ("dependencies", ChangeType.DEPENDENCY_CHANGED,
         "depends on a different visit"),
        ("recurrence", ChangeType.RECURRENCE_CHANGED,
         "repeats on a different schedule"),
        ("recurrence_plan", ChangeType.RECURRENCE_CHANGED,
         "follows a different phased follow-up plan"),
        ("calendar_recurrence", ChangeType.RECURRENCE_CHANGED,
         "repeats on different weekdays or calendar policies"),
    ):
        left_value = _dump(getattr(before, field))
        right_value = _dump(getattr(after, field))
        if left_value != right_value:
            output.append(_typed(
                change_type, code, f"{after.display_name} {phrase}",
                before=left_value, after=right_value,
            ))

    output.extend(_constraint_changes(before, after))

    if (before.visit_mode, list(before.allowed_visit_modes)) != (
            after.visit_mode, list(after.allowed_visit_modes)):
        output.append(_typed(
            ChangeType.VISIT_MODE_CHANGED, code,
            f"{after.display_name} is attended differently",
            detail="patients may be told to travel when they previously were not",
            before={"visit_mode": before.visit_mode,
                    "allowed_visit_modes": list(before.allowed_visit_modes)},
            after={"visit_mode": after.visit_mode,
                   "allowed_visit_modes": list(after.allowed_visit_modes)},
        ))
    return output


def _constraint_changes(before, after) -> list[TypedChange]:
    from .feasibility import semantic_key

    left, right = before.constraints, after.constraints
    if _dump(left) == _dump(right):
        return []
    left_key = semantic_key(left) if left is not None else None
    right_key = semantic_key(right) if right is not None else None
    if left_key == right_key:
        return [_typed(
            ChangeType.CONSTRAINT_PROVENANCE_CHANGED, before.code,
            f"the sources cited for {after.display_name}'s restrictions changed",
            detail="the permitted dates are unchanged",
            before=_dump(left), after=_dump(right),
        )]
    return [_typed(
        ChangeType.CONSTRAINTS_CHANGED, before.code,
        f"{after.display_name} has different restrictions on when it may happen",
        detail="every patient's permitted dates for this visit are recalculated",
        before=_dump(left), after=_dump(right),
    )]


def _dump(value: object) -> object:
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _window_days(timing: object) -> tuple[int, int] | None:
    window = getattr(timing, "window", None)
    if window is None:
        return None
    before = getattr(window, "before", None)
    after = getattr(window, "after", None)
    if before is None or after is None:
        return None
    return int(getattr(before, "value", 0)), int(getattr(after, "value", 0))


def _event_window_change(code: str, name: str, before, after) -> TypedChange:
    """A change to an event's own window rule (doc B2/B3).

    Only a same-unit change whose both edges moved outward is administrative.
    Adding, removing, or re-uniting a window changes which dates count as on
    time, so it is reviewed as clinical.
    """
    left, right = _dump(before), _dump(after)
    same_unit = (
        before is not None and after is not None
        and before.before.unit == after.before.unit
    )
    if same_unit:
        widened = (
            after.before.value >= before.before.value
            and after.after.value >= before.after.value
        )
        return _typed(
            ChangeType.WINDOW_WIDENED if widened else ChangeType.WINDOW_NARROWED,
            code, f"the {name} window {'widened' if widened else 'narrowed'}",
            detail=(
                "no existing booking can fall outside a wider window" if widened
                else "patients already booked may now fall outside their window"
            ),
            before=left, after=right,
        )
    return _typed(
        ChangeType.TIMING_CHANGED, code,
        f"the allowed window for {name} was "
        f"{'added' if before is None else 'removed' if after is None else 'changed'}",
        detail="which dates count as on time changes for every patient",
        before=left, after=right,
    )


def _window_change(code: str, before_timing, after_timing) -> TypedChange | None:
    """A window change, only when the NOMINAL date itself did not move.

    Widening a window cannot put an existing booking outside it, so it is
    administrative. Narrowing one can, which is why the two are separate types
    rather than a single "window changed".
    """
    before_window = _window_days(before_timing)
    after_window = _window_days(after_timing)
    if before_window is None or after_window is None or before_window == after_window:
        return None
    before_nominal = getattr(before_timing, "nominal", None)
    after_nominal = getattr(after_timing, "nominal", None)
    if before_nominal is None or after_nominal is None:
        return None
    if before_nominal.model_dump(mode="json") != after_nominal.model_dump(mode="json"):
        return None

    widened = (
        after_window[0] >= before_window[0] and after_window[1] >= before_window[1]
    )
    change_type = ChangeType.WINDOW_WIDENED if widened else ChangeType.WINDOW_NARROWED
    detail = (
        "no existing booking can fall outside a wider window" if widened
        else "patients already booked may now fall outside their window"
    )
    return TypedChange(
        change_type=change_type,
        significance=(
            Significance.ADMINISTRATIVE if change_type in ADMINISTRATIVE_CHANGES
            else Significance.CLINICAL
        ),
        entity_code=code,
        summary=(
            f"the visit window {'widened' if widened else 'narrowed'} from "
            f"-{before_window[0]}/+{before_window[1]} to "
            f"-{after_window[0]}/+{after_window[1]} days"
        ),
        detail=detail,
        before={"before": before_window[0], "after": before_window[1]},
        after={"before": after_window[0], "after": after_window[1]},
    )

