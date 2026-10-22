from __future__ import annotations

from collections import defaultdict, deque
import re
from uuid import UUID

from .models import (
    CYCLE_BLOCK_DEPENDENCY_MODES,
    CycleDependencyMode,
    DependencyMode,
    RecurrenceBasis,
    TrackRebasePolicy,
    EventType,
    IssueStatus,
    InterpretationStatus,
    PATIENT_RULE_SCOPES,
    Severity,
    UniversalSchedule,
    ValidationIssue,
    schedule_origin_anchor,
)
from .timing import (
    ActivityReference,
    AnchorAcceptance,
    BasisSource,
    TemporalBasis,
    AnchorReference,
    ActivityValue,
    AnchorValue,
    ApproximateTiming,
    ConstraintTiming,
    CycleDayTiming,
    EventReference,
    EventValue,
    NoLaterThanTiming,
    NominalWindowTiming,
    OffsetTiming,
    ProtocolDayTiming,
    ProtocolDefinedTiming,
    RangeTiming,
    TriggeredTiming,
    TriggerNoLaterThan,
    TriggerWithin,
    UnresolvedTiming,
    WithinTiming,
)


_UNIT_SCALE = {
    "MINUTE": ("MINUTE", 1), "HOUR": ("MINUTE", 60), "DAY": ("DAY", 1),
    "WEEK": ("DAY", 7), "MONTH": ("MONTH", 1), "YEAR": ("MONTH", 12),
}


def _comparable(amount: object) -> tuple[str, int]:
    family, scale = _UNIT_SCALE[str(amount.unit)]
    return family, amount.value * scale


def _is_cycle_anchor(code: str, cycle_codes: set[str]) -> bool:
    """CYCLE_3_START is a real anchor when CYCLE is a stated cycle plan."""
    return any(
        re.fullmatch(rf"{re.escape(plan)}_[1-9][0-9]*_START", code) is not None
        for plan in cycle_codes
    )


def legacy_reference(value: object) -> object:
    """A constraint reference value as the equivalent legacy reference model,
    so graph, reference and enrolment code keyed on ``code``/``event_code``
    sees constraint dependencies too. Occurrence detail beyond FIRST/LAST is
    not representable there and is not needed by those callers."""
    if isinstance(value, AnchorValue):
        return AnchorReference(code=value.code, accepts=value.accepts)
    if isinstance(value, EventValue):
        occurrence = value.occurrence.type if value.occurrence.type in {"FIRST", "LAST"} else "CURRENT"
        # Synthetic, never stored or evaluated (graph / enrolment ordering only).
        # The constraint value states its own acceptance, so the basis is explicit.
        if value.accepts == AnchorAcceptance.PLANNED_OR_ACTUAL:
            return EventReference(
                event_code=value.event_code, occurrence=occurrence,
                temporal_basis=TemporalBasis.ACTUAL_OR_PLANNED,
                fallback_basis=TemporalBasis.CURRENT_PLANNED)
        return EventReference(
            event_code=value.event_code, occurrence=occurrence,
            temporal_basis=TemporalBasis.ACTUAL_VISIT)
    if isinstance(value, ActivityValue):
        return ActivityReference(activity_code=value.activity_code)
    raise TypeError(f"not a reference value: {type(value).__name__}")


def constraint_references(constraint: object) -> list[object]:
    """Legacy-shaped references for every anchor/event/activity in a constraint tree."""
    from .feasibility import constraint_value_references

    if constraint is None:
        return []
    return [legacy_reference(item) for item in constraint_value_references(constraint)]


def event_temporal_references(event: object) -> list[object]:
    """Every reference an event's timing AND its constraint set depend on."""
    return [
        *timing_references(event.timing),
        *constraint_references(getattr(event, "constraints", None)),
    ]


def _graph_references(event: object) -> list[object]:
    """References that order events. A constraint's reference to its OWN event
    is occurrence-advancing (PREVIOUS - validated in ``_constraints``) and is
    not an ordering edge; legacy timing references are kept exactly as before."""
    legacy = [] if isinstance(event.timing, ConstraintTiming) else timing_references(event.timing)
    constrained = [
        item for item in (
            *constraint_references(event.timing.constraint if isinstance(event.timing, ConstraintTiming) else None),
            *constraint_references(getattr(event, "constraints", None)),
        )
        if not (isinstance(item, EventReference) and item.event_code == event.code)
    ]
    return [*legacy, *constrained]


def own_event_references(event: object) -> list[EventReference]:
    """The stored (legacy-timing) event references an event carries: its timing,
    its repeat rule's start / end, and its activities' timing. Constraint values
    state their own acceptance and are validated with the constraints."""
    references: list[object] = []
    if not isinstance(event.timing, ConstraintTiming):
        references.extend(timing_references(event.timing))
    for rule in (event.recurrence, event.recurrence_plan, event.calendar_recurrence):
        if rule is None:
            continue
        references.append(rule.start_reference)
        end = getattr(rule, "termination", None)
        if end is not None and end.type == "RELATIVE" and end.end_reference is not None:
            references.append(end.end_reference)
    for activity in event.activities:
        if activity.timing is not None and not isinstance(activity.timing, ConstraintTiming):
            references.extend(timing_references(activity.timing))
    return [item for item in references if isinstance(item, EventReference)]


def timing_references(timing: object) -> list[object]:
    """Return all temporal references nested in a timing expression."""
    if isinstance(timing, ConstraintTiming):
        return constraint_references(timing.constraint)
    references = []
    direct = getattr(timing, "reference", None)
    if direct is not None:
        references.append(direct)
    if isinstance(timing, NominalWindowTiming):
        references.extend(timing_references(timing.nominal))
    if isinstance(timing, TriggeredTiming):
        references.append(timing.trigger)
    return references


def _has_no_single_target(timing: object) -> bool:
    """Timings whose result is a span of allowed dates rather than one target."""
    if isinstance(timing, (RangeTiming, WithinTiming, NoLaterThanTiming, ConstraintTiming)):
        return True
    return isinstance(timing, TriggeredTiming) and isinstance(
        timing.timing_after_trigger, (TriggerWithin, TriggerNoLaterThan))


def _planned_after_references(timing: object) -> list[AnchorReference]:
    """Planned-accepting anchor references that place a visit after the anchor."""
    if isinstance(timing, NominalWindowTiming):
        return _planned_after_references(timing.nominal)
    reference = getattr(timing, "reference", None)
    if not isinstance(reference, AnchorReference) or (
            reference.accepts != AnchorAcceptance.PLANNED_OR_ACTUAL):
        return []
    after = (
        (isinstance(timing, OffsetTiming) and timing.offset.value > 0)
        or (isinstance(timing, ProtocolDayTiming) and timing.day > 1)
        or (isinstance(timing, RangeTiming) and timing.start.value > 0)
        or (isinstance(timing, WithinTiming) and timing.direction == "AFTER")
        or (isinstance(timing, NoLaterThanTiming) and timing.duration.value > 0)
    )
    return [reference] if after else []


#: AD-6: the closed taxonomy now lives as ``EventType`` on the domain model
#: itself (models.py); re-exported here under its original name for the
#: existing importers (schedule_projection.py, dashboard_service.py, tests).
#: ``Event.event_type`` stays a plain ``str`` - see ``EventType``'s docstring
#: for why an unrecognised value is a review WARNING here, never a hard
#: validation failure.
CORE_EVENT_TYPES = frozenset(EventType)


def _non_acknowledgeable_codes() -> frozenset[str]:
    from .capabilities import NON_ACKNOWLEDGEABLE_CODES

    return NON_ACKNOWLEDGEABLE_CODES


class ScheduleValidator:
    version = "uctsm-validator.v2"

    def validate(
        self, schedule: UniversalSchedule, *, evaluating_approved: bool = False,
    ) -> list[ValidationIssue]:
        """``evaluating_approved``: the engine re-checks an already APPROVED
        version before dating a patient. Findings about a legacy repeat that
        predates ``RecurrenceRule.basis`` are then advisory - an approved
        schedule must keep dating exactly as it was approved (NF-08); they
        still block approving a NEW or corrected version."""
        # Source coverage cannot be recomputed from the surviving schedule.
        # Preserve these findings until explicitly resolved or re-extracted.
        issues: list[ValidationIssue] = [
            issue.model_copy(deep=True) for issue in schedule.validation_issues
            if issue.details.get("origin") == "EXTRACTION" and (
                issue.status == IssueStatus.OPEN
                or issue.issue_code in _non_acknowledgeable_codes())
        ]
        if not schedule.events:
            issues.append(self._issue("EMPTY_SCHEDULE", "No visits or protocol events were extracted."))
        issues.extend(self._unique_codes(schedule))
        issues.extend(self._references(schedule))
        issues.extend(self._temporal_basis(schedule))
        issues.extend(self._dependency_graph(schedule))
        issues.extend(self._clinical_completeness(schedule))
        issues.extend(self._schedule_structures(schedule))
        issues.extend(self._anchor_integrity(schedule))
        issues.extend(self._information_loss(schedule))
        issues.extend(self._windows(schedule))
        issues.extend(self._planned_anchor_use(schedule))
        issues.extend(self._rule_scopes(schedule))
        issues.extend(self._cycle_plans(schedule))
        issues.extend(self._interval_recurrences(schedule, tolerant=evaluating_approved))
        issues.extend(self._tracks(schedule))
        if not evaluating_approved:
            issues.extend(self._dependency_selectors(schedule))
        issues.extend(self._recurrence_plans(schedule))
        issues.extend(self._calendar_recurrences(schedule))
        issues.extend(self._processes(schedule))
        issues.extend(self._constraints(schedule))
        from .capabilities import capability_issues

        issues.extend(capability_issues(schedule))
        from .graph_validation import graph_integrity_issues, locate_issues

        issues.extend(graph_integrity_issues(schedule))
        from .manual_handling import validation_issues as handling_issues
        issues = handling_issues(schedule, issues)
        return locate_issues(schedule, issues)

    def _interval_recurrences(
        self, schedule: UniversalSchedule, *, tolerant: bool = False,
    ) -> list[ValidationIssue]:
        """P4-02/P4-04: an interval repeat states how its series progresses, and
        its first occurrence agrees with the event's own timing.

        Structural only: a patient who has not yet got an actual or a condition
        value does not make the PROTOCOL invalid (that is an evaluation wait).
        Legacy rules (``basis`` is None) that are unambiguous stay a warning so
        approved schedules keep working; ambiguous ones block approval.
        """
        issues: list[ValidationIssue] = []
        for event in schedule.events:
            rule = event.recurrence
            if rule is None:
                continue
            common = {"entity_type": "EVENT", "entity_id": event.id}
            path = f"events/{event.code}/recurrence"
            timing = event.timing
            nominal = timing.nominal if isinstance(timing, NominalWindowTiming) else timing
            if isinstance(nominal, ProtocolDayTiming) and nominal.day > 0:
                # "Day N" counted from an anchor is an offset of N-1 days.
                nominal = OffsetTiming(
                    reference=nominal.reference,
                    offset={"value": nominal.day - 1, "unit": "DAY"})
            same_reference = (
                isinstance(nominal, OffsetTiming)
                and nominal.reference.model_dump(include={"kind", "code", "event_code"})
                == rule.start_reference.model_dump(include={"kind", "code", "event_code"}))
            comparable = False
            conflict = False
            if same_reference:
                series_offset = rule.start_offset
                event_family, event_value = _comparable(nominal.offset)
                if series_offset is None:
                    series_family, series_value = event_family, 0
                else:
                    series_family, series_value = _comparable(series_offset)
                comparable = series_family == event_family
                conflict = comparable and series_value != event_value
            if rule.basis_status == "UNCONFIRMED" or rule.basis is None and rule.basis_status:
                issues.append(self._issue(
                    "RECURRENCE_BASIS_UNCONFIRMED",
                    "The recurrence basis was proposed without protocol evidence; state which "
                    "occurrence each repeat counts from before approval.",
                    details={"field_path": f"{path}/basis", "proposed": rule.basis},
                    **common))
            elif rule.basis is None:
                ambiguous = (not same_reference or not comparable or conflict
                             or event.dependency_mode == DependencyMode.ACTUAL_PREVIOUS_EVENT)
                issues.append(self._issue(
                    "RECURRENCE_BASIS_UNSTATED",
                    "This repeat predates explicit recurrence bases. "
                    + ("It is ambiguous - state what each occurrence counts from."
                       if ambiguous else
                       "It dates as a fixed series from its start reference; confirm that basis."),
                    blocking=ambiguous and not tolerant,
                    severity=Severity.ERROR if ambiguous and not tolerant else Severity.WARNING,
                    details={"field_path": f"{path}/basis", "legacy_equivalent":
                             None if ambiguous else RecurrenceBasis.FIXED_ANCHOR_SERIES.value},
                    **common))
            if rule.basis in (None, RecurrenceBasis.FIXED_ANCHOR_SERIES):
                check = conflict
            else:
                check = rule.start_offset is not None and conflict
            # An explicit basis must be CHECKABLE at approval. When the event's
            # own timing and the series start cannot be compared structurally
            # (different anchors, different calendar unit families, a timing
            # that is not an offset from the same reference) the contradiction
            # would only surface as an unusable series for a patient. Blocking
            # it here names the field to fix; a patient who merely has no
            # anchor value yet is NOT this case - that stays an evaluation wait.
            needs_proof = rule.basis is RecurrenceBasis.FIXED_ANCHOR_SERIES or (
                rule.basis is not None and rule.start_offset is not None)
            if needs_proof and not conflict and rule.basis_status != "UNCONFIRMED":
                provable = same_reference and comparable
                if not provable:
                    reason = ("they count from different references" if not same_reference
                              else "their units are different calendar families")
                    issues.append(self._issue(
                        "RECURRENCE_START_UNVERIFIABLE",
                        f"The event's own timing and the recurrence start cannot be shown to "
                        f"agree ({reason}). State the start in the same reference and unit as "
                        "the event timing, or remove the start offset so the event timing is "
                        "the first occurrence.",
                        details={"field_path": f"{path}/start_reference",
                                 "reason": "DIFFERENT_REFERENCE" if not same_reference
                                 else "INCOMPATIBLE_UNITS",
                                 "basis": rule.basis.value},
                        **common))
            if check:
                legacy = rule.basis is None and tolerant
                issues.append(self._issue(
                    "RECURRENCE_START_CONFLICT",
                    "The event's own timing and the recurrence's start offset put the first "
                    "occurrence on different dates; neither is chosen until a reviewer decides.",
                    blocking=not legacy,
                    severity=Severity.WARNING if legacy else Severity.ERROR,
                    details={"field_path": f"{path}/start_offset",
                             "event_offset": nominal.offset.model_dump(mode="json"),
                             "start_offset": rule.start_offset.model_dump(mode="json")
                             if rule.start_offset else None},
                    **common))
        return issues

    def _dependency_selectors(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """New and edited drafts must say WHICH occurrence of a source a dependency
        needs. ``CURRENT`` is only an alias of LAST (the latest generated
        occurrence), not a target-relative reading, so it is refused for approval;
        approved legacy schedules keep evaluating (this runs on drafts only)."""
        issues: list[ValidationIssue] = []
        for event in schedule.events:
            for index, dependency in enumerate(event.dependencies):
                if dependency.source_occurrence == "CURRENT":
                    issues.append(self._issue(
                        "DEPENDENCY_SELECTOR_AMBIGUOUS",
                        f"{event.display_name} depends on {dependency.source_event_code} with the "
                        "selector CURRENT, which only means the latest generated occurrence. "
                        "Choose FIRST, LAST or a specific occurrence number.",
                        entity_type="EVENT", entity_id=event.id,
                        details={"field_path": f"events/{event.code}/dependencies/{index}/source_occurrence",
                                 "selector": "CURRENT"}))
        return issues

    def _tracks(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """P5-01/P5-02: track membership, policies and how they compose.

        A schedule with no tracks and no classified events is a legacy schedule:
        nothing is forced and every date is as before. Once a schedule carries
        tracks it is TRACK-AWARE and each event must be a member of a track or be
        explicitly GLOBAL - there is no fabricated default track.
        """
        from .tracks import references_of

        issues: list[ValidationIssue] = []
        aware = bool(schedule.tracks or schedule.track_links) or any(
            event.track_scope is not None for event in schedule.events)
        if not aware:
            return issues
        by_id = {track.id: track for track in schedule.tracks}
        by_code: dict[str, object] = {}
        anchors = {anchor.code: anchor for anchor in schedule.anchors}
        processes = {item.code: item for item in schedule.processes}
        events_by_code = {event.code: event for event in schedule.events}
        for track in schedule.tracks:
            path = f"tracks/{track.code}"
            if track.code in by_code:
                issues.append(self._issue(
                    "TRACK_CODE_DUPLICATE", f"Two tracks share the code {track.code}.",
                    entity_type="TRACK", entity_id=track.id, details={"field_path": f"{path}/code"}))
            by_code[track.code] = track
            common = {"entity_type": "TRACK", "entity_id": track.id}
            if track.review_state != "CONFIRMED":
                issues.append(self._issue(
                    "TRACK_NOT_CONFIRMED", f"Track {track.code} is proposed and has not been "
                    "confirmed in review.", details={"field_path": f"{path}/review_state"}, **common))
            if track.rebase_policy is None:
                issues.append(self._issue(
                    "TRACK_POLICY_UNRESOLVED", f"Track {track.code} does not state what may move "
                    "its targets (rebase policy). It is not inferred from the track's name.",
                    details={"field_path": f"{path}/rebase_policy"}, **common))
            if track.anchor_code is not None and track.anchor_code not in anchors:
                issues.append(self._issue(
                    "TRACK_ANCHOR_UNKNOWN", f"Track {track.code} counts from anchor "
                    f"{track.anchor_code}, which this schedule does not define.",
                    details={"field_path": f"{path}/anchor_code"}, **common))
            if track.rebase_policy is TrackRebasePolicy.MANUAL_REBASE and not track.anchor_code:
                issues.append(self._issue(
                    "TRACK_REBASE_ANCHOR_MISSING", f"Track {track.code} is rebased manually but "
                    "names no anchor to rebase.", details={"field_path": f"{path}/anchor_code"},
                    **common))
            members = {event.code for event in schedule.events if event.track_id == track.id}
            if track.process_code is not None:
                process = processes.get(track.process_code)
                if process is None:
                    issues.append(self._issue(
                        "TRACK_PROCESS_UNKNOWN", f"Track {track.code} pauses/stops through process "
                        f"{track.process_code}, which this schedule does not define.",
                        details={"field_path": f"{path}/process_code"}, **common))
                elif set(process.event_codes) != members:
                    issues.append(self._issue(
                        "TRACK_PROCESS_MISMATCH", f"The events of track {track.code} and the "
                        f"events of process {process.code} differ, so pausing the track would "
                        "pause the wrong visits.",
                        details={"field_path": f"{path}/process_code",
                                 "track_events": sorted(members),
                                 "process_events": sorted(process.event_codes)}, **common))
                if track.stoppable is None:
                    issues.append(self._issue(
                        "TRACK_TERMINATION_UNSTATED", f"Track {track.code} does not say whether it "
                        "may be stopped; stopping it is refused until it does.", blocking=False,
                        severity=Severity.WARNING,
                        details={"field_path": f"{path}/stoppable"}, **common))
        for link in schedule.track_links:
            for side, code in (("source_track_code", link.source_track_code),
                               ("target_track_code", link.target_track_code)):
                if code not in by_code:
                    issues.append(self._issue(
                        "TRACK_LINK_UNKNOWN", f"A cross-track rule names track {code}, which "
                        "does not exist.", details={"field_path": f"track_links/{side}"}))
            if link.review_state != "CONFIRMED":
                issues.append(self._issue(
                    "TRACK_LINK_NOT_CONFIRMED", f"The cross-track rule {link.source_track_code} -> "
                    f"{link.target_track_code} is proposed and has not been confirmed.",
                    details={"field_path": "track_links"}))
        linked = {(item.source_track_code, item.target_track_code) for item in schedule.track_links}
        track_code_of = {track.id: track.code for track in schedule.tracks}
        actual_bases = ("ACTUAL_VISIT", "ACTUAL_TREATMENT", "ACTUAL_OR_PLANNED", "MANUAL_ANCHOR")
        for event in schedule.events:
            common = {"entity_type": "EVENT", "entity_id": event.id}
            path = f"events/{event.code}"
            if event.track_scope is None:
                issues.append(self._issue(
                    "TRACK_MEMBERSHIP_MISSING", f"{event.display_name} is not in a track and is "
                    "not marked as study-wide. Choose its track or mark it global.",
                    details={"field_path": f"{path}/track_id"}, **common))
                continue
            if event.track_scope == "TRACK" and event.track_id not in by_id:
                issues.append(self._issue(
                    "TRACK_REFERENCE_UNKNOWN", f"{event.display_name} names a track that is not "
                    "part of this schedule version.",
                    details={"field_path": f"{path}/track_id"}, **common))
                continue
            track = by_id.get(event.track_id) if event.track_id else None
            refs = references_of(event)
            if track is not None and track.rebase_policy is not None:
                actual_refs = [r for r in refs if r["kind"] == "EVENT"
                               and r.get("temporal_basis") in actual_bases]
                policy = track.rebase_policy
                if policy in (TrackRebasePolicy.FIXED_FROM_ANCHOR, TrackRebasePolicy.NO_REBASE) \
                        and actual_refs:
                    issues.append(self._issue(
                        "TRACK_POLICY_BASIS_CONFLICT",
                        f"{event.display_name} counts from an actual date but track {track.code} "
                        f"is {policy.value}: nothing may move it.",
                        details={"field_path": f"{path}/timing", "track": track.code,
                                 "policy": policy.value}, **common))
                if event.recurrence is not None and event.recurrence.basis is not None:
                    basis = event.recurrence.basis.value
                    allowed = {
                        TrackRebasePolicy.FIXED_FROM_ANCHOR: {
                            "FIXED_ANCHOR_SERIES", "FIRST_RESOLVED_OCCURRENCE"},
                        TrackRebasePolicy.NO_REBASE: {
                            "FIXED_ANCHOR_SERIES", "FIRST_RESOLVED_OCCURRENCE"},
                        TrackRebasePolicy.MANUAL_REBASE: {
                            "FIXED_ANCHOR_SERIES", "FIRST_RESOLVED_OCCURRENCE"},
                        TrackRebasePolicy.PREVIOUS_PLANNED_OCCURRENCE: {
                            "PREVIOUS_PLANNED_OCCURRENCE"},
                        TrackRebasePolicy.PREVIOUS_ACTUAL_OCCURRENCE: {
                            "PREVIOUS_ACTUAL_OCCURRENCE"},
                    }[policy]
                    if basis not in allowed:
                        issues.append(self._issue(
                            "TRACK_POLICY_BASIS_CONFLICT",
                            f"{event.display_name} repeats {basis} but track {track.code} is "
                            f"{policy.value}; they do not describe the same series.",
                            details={"field_path": f"{path}/recurrence/basis", "track": track.code,
                                     "policy": policy.value, "recurrence_basis": basis}, **common))
            # references across tracks need an explicit, confirmed rule
            sources: list[str] = [r["code"] for r in refs if r["kind"] == "EVENT"]
            sources += [dep.source_event_code for dep in event.dependencies]
            for ref in refs:
                if ref["kind"] == "ANCHOR":
                    anchor = anchors.get(ref["code"])
                    if anchor is not None and anchor.source_event_code:
                        sources.append(anchor.source_event_code)
            if event.track_scope == "TRACK" and track is not None:
                for source_code in dict.fromkeys(sources):
                    source = events_by_code.get(source_code)
                    if source is None or source.track_scope != "TRACK" \
                            or source.track_id == event.track_id:
                        continue
                    source_track = track_code_of.get(source.track_id)
                    if source_track is not None and (source_track, track.code) not in linked:
                        issues.append(self._issue(
                            "TRACK_CROSS_REFERENCE_UNDECLARED",
                            f"{event.display_name} (track {track.code}) counts from "
                            f"{source.display_name} (track {source_track}) with no declared "
                            "cross-track rule.",
                            details={"field_path": f"{path}/timing", "source_track": source_track,
                                     "target_track": track.code, "source_event": source_code},
                            **common))
        return issues

    def _constraints(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        from .constraint_validation import validate_constraints

        return validate_constraints(schedule, self._issue)

    def _windows(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc B2/B3: an event window needs one target date to sit around.

        RANGE, WITHIN and NO_LATER_THAN already ARE the allowed dates; a second
        window on top has no stated centre, and choosing one (the range start?
        its midpoint?) would be a guess. A repeating event is exempt: each
        occurrence's own target date is the centre. A NOMINAL_WITH_WINDOW timing
        stating a different window from the event is two readings of one rule.
        """
        issues: list[ValidationIssue] = []
        for event in schedule.events:
            if event.window is None:
                continue
            if (not event.repeats
                    and _has_no_single_target(event.timing)):
                issues.append(self._issue(
                    "AMBIGUOUS_WINDOW",
                    f"{event.display_name!r} states a window but its timing "
                    f"({event.timing.type}) has no single target date to apply it "
                    "to; restate the timing or the window before approval",
                    entity_type="EVENT", entity_id=event.id,
                    details={"event_code": event.code, "timing_type": event.timing.type},
                ))
            if (isinstance(event.timing, NominalWindowTiming)
                    and event.timing.window.model_dump() != event.window.model_dump()):
                issues.append(self._issue(
                    "CONFLICTING_SOURCE",
                    "The visit window and the timing's own window disagree",
                    entity_type="EVENT", entity_id=event.id,
                    details={
                        "event_code": event.code,
                        "event_window": event.window.model_dump(mode="json"),
                        "timing_window": event.timing.window.model_dump(mode="json"),
                    },
                ))
        return issues

    #: Which bases a listed dependency may use under each stated dependency mode.
    #: The mode decides whether a missing source BLOCKS the event; the reference's
    #: basis decides which date counts. They must tell one story.
    _MODE_BASES = {
        "NOMINAL": frozenset({
            TemporalBasis.CURRENT_PLANNED, TemporalBasis.ORIGINAL_PLANNED,
            TemporalBasis.ACTUAL_OR_PLANNED}),
        "ACTUAL_PREVIOUS_EVENT": frozenset({
            TemporalBasis.ACTUAL_VISIT, TemporalBasis.ACTUAL_TREATMENT}),
        "MANUAL": frozenset({TemporalBasis.MANUAL_ANCHOR}),
    }

    def _temporal_basis(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """P1-01 / P1-02: every event reference says which date it counts from,
        and what it says must be coherent with the rest of the rule."""
        from .temporal_basis import mode_is_stated

        issues: list[ValidationIssue] = []
        types = {item.code: str(item.event_type).upper() for item in schedule.events}
        for event in schedule.events:
            dependencies = {item.source_event_code for item in event.dependencies}
            stated = mode_is_stated(event.dependency_mode)
            mode_name = getattr(event.dependency_mode, "value", event.dependency_mode)
            legacy_codes: set[str] = set()

            def block(code: str, message: str, ref: EventReference, **extra: object) -> None:
                issues.append(self._issue(
                    code, message, entity_type="EVENT", entity_id=event.id,
                    details={"event_code": event.code, "referenced_event": ref.event_code,
                             "temporal_basis": ref.temporal_basis.value, **extra}))

            for ref in own_event_references(event):
                is_dependency = ref.event_code in dependencies
                if ref.basis_source == BasisSource.LEGACY_EQUIVALENT:
                    legacy_codes.add(ref.event_code)
                if ref.basis_source == BasisSource.UNCONFIRMED:
                    # A reviewer stating the owner's dependency mode settles it
                    # (the basis is then derived from that mode); until then the
                    # extractor's placeholder must not reach a patient.
                    if not stated:
                        block("TEMPORAL_BASIS_UNCONFIRMED",
                              f"{event.display_name!r} counts from {ref.event_code!r} but the "
                              "protocol does not say whether that is its planned or actual "
                              "date; a reviewer must state it", ref)
                    continue
                if (ref.temporal_basis == TemporalBasis.ACTUAL_TREATMENT
                        and types.get(ref.event_code) != "TREATMENT"):
                    block("TEMPORAL_BASIS_TREATMENT_ON_NON_TREATMENT",
                          f"{event.display_name!r} counts from the actual treatment date of "
                          f"{ref.event_code!r}, which is not a treatment event", ref)
                    continue
                if (ref.temporal_basis == TemporalBasis.MANUAL_ANCHOR
                        and event.dependency_mode != DependencyMode.MANUAL):
                    block("TEMPORAL_BASIS_MANUAL_REQUIRES_MANUAL_MODE",
                          f"{event.display_name!r} counts from a manually entered date of "
                          f"{ref.event_code!r} but its dependency mode is not MANUAL", ref)
                    continue
                allowed = self._MODE_BASES.get(str(mode_name))
                if stated and is_dependency and allowed is not None and ref.temporal_basis not in allowed:
                    block("TEMPORAL_BASIS_MODE_MISMATCH",
                          f"{event.display_name!r} states {ref.temporal_basis.value} for "
                          f"{ref.event_code!r} but its dependency mode {mode_name} counts from "
                          + " / ".join(sorted(item.value for item in allowed)),
                          ref, allowed_bases=sorted(item.value for item in allowed))
            if legacy_codes:
                issues.append(self._issue(
                    "TEMPORAL_BASIS_LEGACY_EQUIVALENT",
                    f"{event.display_name!r} references {', '.join(sorted(legacy_codes))} with "
                    "the date source the engine already used; nobody has confirmed it",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.WARNING, blocking=False,
                    details={"event_code": event.code, "referenced_events": sorted(legacy_codes)}))
        return issues

    def _planned_anchor_use(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc C6: surface a planned anchor dating a visit AFTER that anchor.

        PLANNED_OR_ACTUAL exists for visits that precede the event (pre-op). A
        post-op visit that accepts a planned surgery date is exactly what the
        rule is there to prevent, so a reviewer is asked to look - not blocked,
        because a protocol may genuinely want a projected follow-up.
        """
        issues: list[ValidationIssue] = []
        for event in schedule.events:
            codes = sorted({
                reference.code for reference in _planned_after_references(event.timing)
            })
            if codes:
                issues.append(self._issue(
                    "PLANNED_ANCHOR_AFTER_EVENT",
                    f"{event.display_name!r} falls after {', '.join(codes)} but may be "
                    "dated from a planned value; confirm it should not wait for the "
                    "actual date",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.WARNING, blocking=False,
                    details={"event_code": event.code, "anchor_codes": codes},
                ))
        return issues

    def _rule_scopes(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc I8: a trial-level rule never changes a patient's visits.

        The patient engine refuses SITE/COUNTRY/TRIAL rules, so any patient
        action such a rule carries will never happen. Said here, at review,
        rather than left for someone to discover when accrual is suspended and
        no patient schedule moved. Not blocking: the rule is legitimate protocol
        content that belongs in a trial-level workflow, and refusing approval
        would only push a reviewer to delete it.
        """
        issues: list[ValidationIssue] = []
        for definition in schedule.conditional_definitions:
            if definition.scope in PATIENT_RULE_SCOPES:
                continue
            issues.append(self._issue(
                "TRIAL_LEVEL_RULE_NOT_APPLIED_TO_PATIENTS",
                f"Rule {definition.display_name!r} is {definition.scope.value}-scoped; "
                "its patient actions ("
                + ", ".join(sorted({item.action_type for item in definition.actions}))
                + ") will not change any patient's schedule and must be handled "
                "by a trial-level workflow",
                entity_type="CONDITION", entity_id=definition.id,
                severity=Severity.WARNING, blocking=False,
                details={
                    "rule_code": definition.code, "scope": definition.scope.value,
                    "action_targets": sorted({item.target_code for item in definition.actions}),
                },
            ))
        return issues

    @staticmethod
    def _issue(
        code: str,
        message: str,
        *,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        blocking: bool = True,
        severity: Severity = Severity.ERROR,
        details: dict[str, object] | None = None,
    ) -> ValidationIssue:
        return ValidationIssue(
            issue_code=code,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            blocking=blocking,
            severity=severity,
            details=details or {},
        )

    def _unique_codes(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for kind, values in (
            ("EVENT", schedule.events),
            ("ANCHOR", schedule.anchors),
            ("EPOCH", schedule.epochs),
            ("ARM", schedule.arms),
            ("COHORT", schedule.cohorts),
            ("POPULATION", schedule.populations),
            ("DIMENSION", schedule.dimensions),
            ("CONDITION", schedule.conditional_definitions),
            ("REPEAT_BLOCK", schedule.repeat_blocks),
            ("CONFINEMENT_EPISODE", schedule.confinement_episodes),
        ):
            seen: dict[str, UUID] = {}
            for value in values:
                normalized = value.code.casefold()
                if normalized in seen:
                    issues.append(self._issue(
                        f"DUPLICATE_{kind}",
                        f"Duplicate {kind.lower()} code {value.code!r}",
                        entity_type=kind,
                        entity_id=value.id,
                        details={"first_id": str(seen[normalized]), "code": value.code},
                    ))
                else:
                    seen[normalized] = value.id
        return issues

    def _anchor_integrity(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc s4/s29: an anchor the import could not fully resolve stays a
        durable, re-derivable block - not a one-time note the adapter happened
        to attach at import.

        A ``ValidationIssue`` appended only once, at import time, is discarded
        the next time anyone calls validate/submit/approve, because those all
        replace ``validation_issues`` with a fresh run of this validator. If a
        reviewer patches around the SYMPTOM (say, gives one event's timing an
        explicit anchor) without ever resolving which anchor is actually the
        schedule's origin, a validator with no memory of the ambiguity would
        wave the schedule through. Checking ``Anchor.status`` here instead
        means the block is re-derived from the schedule's own persisted state
        every single time, so it cannot be bypassed by fixing something else.
        """
        issues: list[ValidationIssue] = []
        for anchor in schedule.anchors:
            if anchor.status == "AMBIGUOUS":
                issues.append(self._issue(
                    "AMBIGUOUS_BASELINE",
                    f"Anchor {anchor.display_name!r} is one of several equally "
                    "plausible schedule origins; the reviewer must confirm "
                    "which anchor Day 0 is measured from before this schedule "
                    "can be approved.",
                    entity_type="ANCHOR", entity_id=anchor.id,
                ))
            elif anchor.status == "UNRESOLVED":
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    f"Anchor {anchor.display_name!r} could not be resolved from "
                    "the protocol and needs a reviewed value.",
                    entity_type="ANCHOR", entity_id=anchor.id,
                ))
        # AD-5: a CLINICAL_DAY schedule's Day-N labels are computed relative to
        # its stated origin anchor. This only fires once ``day_numbering`` is
        # actually stated: a schedule built by hand with a legacy
        # SCHEDULE_ORIGIN-tagged anchor and no ``day_numbering`` at all is
        # still resolved by ``schedule_origin_anchor``'s second path and is
        # not touched by this check. Enrolment (operational_bridge.py) already
        # raises a ValueError for an unresolvable origin; this check exists so
        # the same schedule cannot be silently approved first.
        day_numbering = schedule.schedule_metadata.day_numbering
        if (
            day_numbering is not None
            and day_numbering.counting_convention == "CLINICAL_DAY"
            and schedule_origin_anchor(schedule) is None
        ):
            issues.append(self._issue(
                "UNRESOLVED_SCHEDULE_ORIGIN",
                "This schedule counts visits by clinical day but does not "
                "state which anchor Day 0 is measured from; every Day-N date "
                "on it would be uncomputable.",
            ))
        # The extraction pipeline hits a different shape of the same gap: it
        # has anchors but the model never claimed any of them as the origin
        # (claude_provider.py's ``_apply_anchors``), so there is no
        # ``day_numbering`` to check above. Re-derived from
        # ``schedule_metadata.extensions`` for the same durability reason as
        # ``_information_loss`` below, rather than only from the one-time
        # issue the adapter emits at import.
        for extension in schedule.schedule_metadata.extensions:
            if extension.get("type") != "UNRESOLVED_SCHEDULE_ORIGIN":
                continue
            issues.append(self._issue(
                "UNRESOLVED_SCHEDULE_ORIGIN",
                str(extension.get("detail") or (
                    "This schedule has anchors but none was recorded as its "
                    "Day 1/Day 0 origin.")),
            ))
        return issues

    def _information_loss(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc s29: re-derive every import-time loss marker on every pass.

        The adapter cannot leave a trace of something that never entered the
        schedule (a missing activity template has no row to point at), so it
        records the fact durably instead: on the OWNING event's own
        ``metadata`` when there is one, or on ``schedule_metadata.extensions``
        when there is not (a recurrence naming an event that does not exist
        anywhere in the plan). This mirrors ``_anchor_integrity`` - the point
        of storing it durably rather than only returning it once from the
        adapter is that validate/submit/approve all replace
        ``validation_issues`` with a fresh run of this validator, which would
        otherwise silently discard a one-time note the moment anyone
        revalidates.
        """
        issues: list[ValidationIssue] = []
        for event in schedule.events:
            dangling_activities = list(event.metadata.get("unresolved_activity_refs") or [])
            if dangling_activities:
                issues.append(self._issue(
                    "INFORMATION_LOSS",
                    f"{event.display_name!r} references {len(dangling_activities)} "
                    "activity/activities this schedule never defined; those "
                    "assessments would be silently missing.",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.CRITICAL,
                    details={"unresolved_activity_refs": dangling_activities},
                ))
            dangling_dependencies = event.metadata.get("unresolved_dependency_refs") or []
            if dangling_dependencies:
                issues.append(self._issue(
                    "INFORMATION_LOSS",
                    f"{event.display_name!r} depends on a visit this schedule "
                    "never defined; that ordering rule would be silently "
                    "dropped.",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.CRITICAL,
                    details={"unresolved_dependency_refs": dangling_dependencies},
                ))
        for extension in schedule.schedule_metadata.extensions:
            if extension.get("type") != "INFORMATION_LOSS":
                continue
            issues.append(self._issue(
                "INFORMATION_LOSS",
                f"A {extension.get('kind', 'schedule')} rule names a visit "
                "this schedule never defined; it would be silently dropped.",
                severity=Severity.CRITICAL, details=extension,
            ))
        return issues

    def _references(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        events = {item.code for item in schedule.events}
        anchors = {item.code for item in schedule.anchors}
        cycle_codes = {
            block.cycle_plan.code for block in schedule.repeat_blocks
            if block.cycle_plan is not None
        }
        epochs = {item.id for item in schedule.epochs}
        dimensions = {
            "ARM": {item.code for item in schedule.arms},
            "COHORT": {item.code for item in schedule.cohorts},
            "POPULATION": {item.code for item in schedule.populations},
            "EPOCH": {item.code for item in schedule.epochs},
        }
        for item in schedule.dimensions:
            dimensions.setdefault(item.dimension_type, set()).add(item.code)
        issues: list[ValidationIssue] = []

        def check_reference(reference: object, event_id: UUID) -> None:
            if isinstance(reference, EventReference) and reference.event_code not in events:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    f"Unknown event reference {reference.event_code!r}",
                    entity_type="EVENT", entity_id=event_id,
                ))
            code = getattr(reference, "code", None)
            if code is not None and code not in anchors and not _is_cycle_anchor(code, cycle_codes):
                issues.append(self._issue(
                    "UNKNOWN_ANCHOR", f"Unknown anchor {code!r}",
                    entity_type="EVENT", entity_id=event_id,
                ))

        for event in schedule.events:
            if event.epoch_id is not None and event.epoch_id not in epochs:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE", "Event references an unknown epoch",
                    entity_type="EVENT", entity_id=event.id,
                ))
            timing = event.timing
            # Constraint references are checked with their constraint ids in
            # _constraints; checking them here too would report them twice.
            for reference in ([] if isinstance(timing, ConstraintTiming) else timing_references(timing)):
                check_reference(reference, event.id)
            if event.recurrence is not None:
                check_reference(event.recurrence.start_reference, event.id)
                if event.recurrence.termination.type == "RELATIVE":
                    check_reference(event.recurrence.termination.end_reference, event.id)
            if event.recurrence_plan is not None:
                check_reference(event.recurrence_plan.start_reference, event.id)
                plan_end = event.recurrence_plan.termination
                if plan_end is not None and plan_end.type == "RELATIVE":
                    check_reference(plan_end.end_reference, event.id)
            if event.calendar_recurrence is not None:
                check_reference(event.calendar_recurrence.start_reference, event.id)
                if event.calendar_recurrence.termination.type == "RELATIVE":
                    check_reference(event.calendar_recurrence.termination.end_reference, event.id)
            for dependency in event.dependencies:
                if dependency.source_event_code not in events:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        f"Unknown dependency event {dependency.source_event_code!r}",
                        entity_type="EVENT", entity_id=event.id,
                        details={"field_path": f"events/{event.code}/dependencies/{dependency.source_event_code}/source_event_code"},
                    ))
                if dependency.source_event_code == event.code:
                    issues.append(self._issue(
                        "CIRCULAR_DEPENDENCY", "An event cannot depend on itself",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            if event.dependencies and event.dependency_mode in {None, DependencyMode.UNCLEAR}:
                issues.append(self._issue(
                    "UNCLEAR_DEPENDENCY_MODE",
                    "Event dependencies require an explicit nominal, actual-previous-event, or manual mode",
                    entity_type="EVENT", entity_id=event.id,
                ))
            for rule in event.applicability:
                allowed = dimensions.get(rule.dimension)
                # AD-7: AGE is a known dimension with no fixed code list (it is
                # compared, not matched by membership) - the same shape as
                # PATIENT_ATTRIBUTE/CUSTOM, so it is exempt from "unknown
                # dimension" the same way.
                if (allowed is None
                        and rule.dimension not in {"PATIENT_ATTRIBUTE", "CUSTOM", "AGE"}):
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        f"Unknown applicability dimension {rule.dimension!r}",
                        entity_type="EVENT", entity_id=event.id,
                    ))
                # A comparison rule's values are a threshold, not a code list -
                # checking them against the dimension's known codes would flag
                # a real number like "65" as an unknown ARM/COHORT code.
                if allowed is not None and rule.operator in {"IN", "NOT_IN"}:
                    unknown = sorted(set(rule.values) - allowed)
                    if unknown:
                        issues.append(self._issue(
                            "UNRESOLVED_REFERENCE",
                            f"Unknown {rule.dimension.lower()} applicability values",
                            entity_type="EVENT", entity_id=event.id,
                            details={"values": unknown},
                        ))
                if rule.dimension in {"PATIENT_ATTRIBUTE", "CUSTOM"} and rule.condition is None and not rule.field:
                    issues.append(self._issue(
                        "AMBIGUOUS_CONDITION",
                        f"{rule.dimension} applicability requires a field or typed condition",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            activity_codes = {item.code for item in event.activities if item.code}
            seen_activity_codes: set[str] = set()
            for activity in event.activities:
                if activity.requiredness.value == "CONDITIONAL" and not activity.conditions and not activity.applicability:
                    issues.append(self._issue(
                        "AMBIGUOUS_CONDITION", "A conditional assessment has no executable condition or applicability rule.",
                        entity_type="ACTIVITY", entity_id=activity.id,
                    ))
                if activity.code:
                    normalized = activity.code.casefold()
                    if normalized in seen_activity_codes:
                        issues.append(self._issue(
                            "DUPLICATE_ACTIVITY",
                            f"Duplicate activity code {activity.code!r} inside one visit",
                            entity_type="ACTIVITY", entity_id=activity.id,
                        ))
                    seen_activity_codes.add(normalized)
                for reference in (
                        [] if isinstance(activity.timing, ConstraintTiming)
                        else timing_references(activity.timing)):
                    if not isinstance(reference, ActivityReference):
                        check_reference(reference, event.id)
                        continue
                    if reference.activity_code not in activity_codes:
                        issues.append(self._issue(
                            "UNRESOLVED_REFERENCE",
                            f"Activity timing references unknown activity {reference.activity_code!r}"
                            " in the same visit",
                            entity_type="ACTIVITY", entity_id=activity.id,
                        ))
                    elif reference.activity_code == activity.code:
                        issues.append(self._issue(
                            "CIRCULAR_DEPENDENCY",
                            "An activity cannot be timed relative to itself",
                            entity_type="ACTIVITY", entity_id=activity.id,
                        ))
                for rule in activity.applicability:
                    allowed = dimensions.get(rule.dimension)
                    if allowed is None and rule.dimension not in {"PATIENT_ATTRIBUTE", "CUSTOM"}:
                        issues.append(self._issue(
                            "UNRESOLVED_REFERENCE",
                            f"Unknown activity applicability dimension {rule.dimension!r}",
                            entity_type="ACTIVITY", entity_id=activity.id,
                        ))
                    if allowed is not None:
                        unknown = sorted(set(rule.values) - allowed)
                        if unknown:
                            issues.append(self._issue(
                                "UNRESOLVED_REFERENCE",
                                f"Unknown {rule.dimension.lower()} activity applicability values",
                                entity_type="ACTIVITY", entity_id=activity.id,
                                details={"values": unknown},
                            ))
            for action in event.conditional_actions:
                if action.action_type in {"ADD_EVENT", "REPEAT_EVENT", "CANCEL_EVENT"} and action.target_code not in events:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        f"Unknown conditional-action event {action.target_code!r}",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            if event.confinement is not None:
                confinement_codes = [
                    event.confinement.admission_event_code,
                    *event.confinement.dose_event_codes,
                    event.confinement.discharge_event_code,
                ]
                missing = sorted(set(confinement_codes) - events)
                if missing:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        "Confinement definition references unknown events",
                        entity_type="EVENT", entity_id=event.id,
                        details={"event_codes": missing},
                    ))
                if len(set(confinement_codes)) != len(confinement_codes):
                    issues.append(self._issue(
                        "INVALID_CONFINEMENT",
                        "Admission, dose, and discharge must remain distinct events",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            if isinstance(timing, CycleDayTiming):
                if timing.cycle_reference not in anchors | cycle_codes:
                    issues.append(self._issue(
                        "UNKNOWN_ANCHOR",
                        f"Unknown cycle reference {timing.cycle_reference!r}",
                        entity_type="EVENT", entity_id=event.id,
                    ))
        return issues

    def _dependency_graph(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        edges: dict[str, set[str]] = defaultdict(set)
        indegree = {event.code: 0 for event in schedule.events}
        event_by_code = {event.code: event for event in schedule.events}
        anchors = {anchor.code: anchor for anchor in schedule.anchors}
        for target in schedule.events:
            refs = {dep.source_event_code for dep in target.dependencies}
            for timing_ref in _graph_references(target):
                if isinstance(timing_ref, EventReference):
                    refs.add(timing_ref.event_code)
                else:
                    anchor = anchors.get(getattr(timing_ref, "code", ""))
                    if anchor and anchor.source_event_code:
                        refs.add(anchor.source_event_code)
            for source in refs:
                if source in indegree and target.code not in edges[source]:
                    edges[source].add(target.code)
                    indegree[target.code] += 1
        queue = deque(code for code, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            source = queue.popleft()
            visited += 1
            for target in edges[source]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited == len(indegree):
            return []
        cyclic = sorted(code for code, degree in indegree.items() if degree > 0)
        return [self._issue(
            "CIRCULAR_DEPENDENCY",
            "Event dependency graph contains a cycle",
            entity_type="SCHEDULE",
            entity_id=schedule.schedule_version_id,
            details={"event_codes": cyclic, "event_ids": [str(event_by_code[c].id) for c in cyclic]},
        )]

    def _clinical_completeness(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        evidence_ids = {evidence.id for evidence in schedule.evidence}
        claims = {(claim.claim_entity_id, claim.claim_type) for claim in schedule.claim_evidence}
        for claim in schedule.claim_evidence:
            if claim.evidence_id not in evidence_ids:
                issues.append(self._issue(
                    "MISSING_EVIDENCE",
                    "Claim-level evidence references a source that is not in the schedule",
                    entity_type=claim.claim_entity_type,
                    entity_id=claim.claim_entity_id,
                    details={
                        "claim_type": claim.claim_type,
                        "evidence_id": str(claim.evidence_id),
                    },
                ))
        qualifier_markers: dict[str, list[tuple[str, UUID, str, frozenset[str]]]] = defaultdict(list)
        evidence_by_id = {item.id: item for item in schedule.evidence}
        for event in schedule.events:
            missing_refs = [str(ref) for ref in event.evidence_refs if ref not in evidence_ids]
            has_name_claim = (event.id, "EVENT_NAME") in claims
            has_timing_claim = (event.id, "TIMING") in claims
            if not event.evidence_refs or not has_name_claim or not has_timing_claim or missing_refs:
                issues.append(self._issue(
                    "MISSING_EVIDENCE",
                    "Event identity and timing require linked protocol evidence",
                    entity_type="EVENT", entity_id=event.id,
                    details={
                        "missing_reference_ids": missing_refs,
                        "event_name_claim": has_name_claim,
                        "timing_claim": has_timing_claim,
                    },
                ))
            for field, present, claim_type in (
                ("conditions", bool(event.conditions), "CONDITION"),
                ("applicability", bool(event.applicability), "APPLICABILITY"),
                ("recurrence", event.recurrence is not None, "RECURRENCE"),
                ("recurrence_plan", event.recurrence_plan is not None, "RECURRENCE"),
                ("calendar_recurrence", event.calendar_recurrence is not None, "RECURRENCE"),
                # Doc B4: a window is a protocol statement like any other; one
                # a reviewer adds must cite where the protocol says it.
                ("window", event.window is not None, "WINDOW"),
                ("dependencies", bool(event.dependencies), "DEPENDENCY"),
                ("dependency_mode", event.dependency_mode is not None, "DEPENDENCY_MODE"),
                ("conditional_actions", bool(event.conditional_actions), "CONDITIONAL_ACTION"),
                ("confinement", event.confinement is not None, "CONFINEMENT"),
            ):
                if present and (event.id, claim_type) not in claims:
                    issues.append(self._issue(
                        "MISSING_EVIDENCE", f"Event {field} requires claim-level evidence",
                        entity_type="EVENT", entity_id=event.id,
                        details={"claim_type": claim_type, "field": field},
                    ))
            for activity in event.activities:
                missing_activity_refs = [str(ref) for ref in activity.evidence_refs if ref not in evidence_ids]
                if (
                    not activity.evidence_refs
                    or missing_activity_refs
                    or (activity.id, "ACTIVITY") not in claims
                ):
                    issues.append(self._issue(
                        "MISSING_EVIDENCE", "Activity requires claim-level protocol evidence",
                        entity_type="ACTIVITY", entity_id=activity.id,
                        details={"missing_reference_ids": missing_activity_refs},
                    ))
                if activity.timing is not None and (activity.id, "ACTIVITY_TIMING") not in claims:
                    issues.append(self._issue(
                        "MISSING_EVIDENCE",
                        "Intra-day activity timing requires its own evidence claim",
                        entity_type="ACTIVITY", entity_id=activity.id,
                        details={"claim_type": "ACTIVITY_TIMING", "field": "timing"},
                    ))
            for owner_type, owner_id, qualifiers in (
                ("EVENT", event.id, event.qualifiers),
                *(("ACTIVITY", activity.id, activity.qualifiers) for activity in event.activities),
            ):
                for qualifier in qualifiers:
                    if qualifier.marker:
                        # A symbol is local to its explicitly cited table legend.
                        # Do not infer scope from page numbers: a table can span
                        # pages, and several tables can share one page. Unknown
                        # or global scope remains comparable to every legend.
                        definitions = [evidence_by_id[ref] for ref in qualifier.evidence_refs
                            if ref in evidence_by_id and evidence_by_id[ref].evidence_type == "FOOTNOTE"]
                        tables = frozenset(item.table_title.strip().casefold() for item in definitions
                            if item.table_title and item.table_title.strip()) if (
                                definitions and all(item.table_title and item.table_title.strip() for item in definitions)
                                and qualifier.scope.value != "GLOBAL") else frozenset()
                        qualifier_markers[qualifier.marker.casefold()].append((
                            qualifier.text.strip().casefold(), qualifier.id,
                            qualifier.category.value, tables,
                        ))
                    missing_qualifier_refs = [
                        str(ref) for ref in qualifier.evidence_refs if ref not in evidence_ids]
                    if qualifier.scope.value != "GLOBAL" and not qualifier.target_codes:
                        issues.append(self._issue(
                            "ORPHAN_QUALIFIER",
                            "A non-global table qualifier must identify its target",
                            entity_type=owner_type, entity_id=owner_id,
                        ))
                    if not qualifier.resolved or qualifier.category.value == "UNRESOLVED":
                        issues.append(self._issue(
                            "UNRESOLVED_QUALIFIER",
                            "Table marker or footnote has not been converted into reviewed schedule meaning",
                            entity_type=owner_type, entity_id=owner_id,
                        ))
                    if (
                        not qualifier.evidence_refs
                        or missing_qualifier_refs
                        or (qualifier.id, "QUALIFIER") not in claims
                    ):
                        issues.append(self._issue(
                            "MISSING_EVIDENCE",
                            "Qualifier requires its own linked source-evidence claim",
                            entity_type="QUALIFIER", entity_id=qualifier.id,
                            details={
                                "owner_type": owner_type, "owner_id": str(owner_id),
                                "missing_reference_ids": missing_qualifier_refs,
                                "claim_type": "QUALIFIER",
                            },
                        ))
            if isinstance(event.timing, UnresolvedTiming):
                issues.append(self._issue(
                    "AMBIGUOUS_TIMING", event.timing.reason,
                    entity_type="EVENT", entity_id=event.id,
                ))
            elif isinstance(event.timing, (ProtocolDefinedTiming, ApproximateTiming)):
                # Doc 10 s13-s15: an on-demand visit has no computable date BY
                # DESIGN. That is the requirement, not an extraction failure, so
                # it must not block approval the way an undated scheduled visit
                # does. An ambiguous timing (UnresolvedTiming, above) still does.
                if event.activation != "ON_DEMAND":
                    issues.append(self._issue(
                        "UNSUPPORTED_TIMING",
                        "Timing requires a reviewed deterministic policy before execution",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            elif event.activation == "ON_DEMAND":
                # The inverse mistake: a visit marked on-demand that also carries a
                # calculable protocol date is contradictory, and silently honouring
                # either reading would produce a visit that is due when it should
                # not be, or absent when it should be due.
                issues.append(self._issue(
                    "CONFLICTING_SOURCE",
                    "An unscheduled visit cannot also have a protocol-calculated date",
                    entity_type="EVENT", entity_id=event.id,
                    details={"event_code": event.code,
                             "timing_type": event.timing.type},
                ))
            if event.event_type.strip().upper() not in CORE_EVENT_TYPES:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    "Event type is outside the protocol taxonomy and needs a reviewer "
                    "to confirm how this visit should be presented",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.WARNING, blocking=False,
                    details={
                        "event_code": event.code, "event_type": event.event_type,
                        "known_types": sorted(CORE_EVENT_TYPES),
                    },
                ))
            # Doc 10 s32: a stated mode outside the permitted set means the two
            # readings disagree, and choosing either silently would tell a patient
            # the wrong thing about where to be.
            if (event.visit_mode and event.allowed_visit_modes
                    and event.visit_mode not in event.allowed_visit_modes):
                issues.append(self._issue(
                    "CONFLICTING_SOURCE",
                    "Visit mode is not among the modes the protocol permits",
                    entity_type="EVENT", entity_id=event.id,
                    details={
                        "event_code": event.code, "visit_mode": event.visit_mode,
                        "allowed_visit_modes": list(event.allowed_visit_modes),
                    },
                ))
            # Doc 10 s18 and s31: a hybrid visit needs someone to choose per
            # patient. Surfaced, not blocking - the protocol is legitimate.
            if len(event.allowed_visit_modes) > 1 and not event.visit_mode:
                issues.append(self._issue(
                    "UNRESOLVED_QUALIFIER",
                    "Visit permits more than one mode; the mode must be selected per patient",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.WARNING, blocking=False,
                    details={
                        "event_code": event.code,
                        "allowed_visit_modes": list(event.allowed_visit_modes),
                    },
                ))
            if event.interpretation_status in {
                InterpretationStatus.AMBIGUOUS,
                InterpretationStatus.CONFLICTING,
                InterpretationStatus.UNRESOLVED,
            }:
                issues.append(self._issue(
                    "CONFLICTING_EVIDENCE" if event.interpretation_status == InterpretationStatus.CONFLICTING else "UNRESOLVED_REFERENCE",
                    f"Event interpretation is {event.interpretation_status.value}",
                    entity_type="EVENT", entity_id=event.id,
                ))
        for marker, meanings in qualifier_markers.items():
            conflicting = set()
            for index, (text, identifier, category, tables) in enumerate(meanings):
                for other_text, other_id, other_category, other_tables in meanings[index + 1:]:
                    if (text, category) != (other_text, other_category) and (
                        not tables or not other_tables or not tables.isdisjoint(other_tables)):
                        conflicting.update([identifier, other_id])
            if conflicting:
                relevant = [item for item in meanings if item[1] in conflicting]
                issues.append(self._issue(
                    "CONFLICTING_EVIDENCE",
                    f"Table marker {marker!r} has conflicting qualifier meanings",
                    entity_type="QUALIFIER", entity_id=relevant[0][1],
                    details={
                        "qualifier_ids": sorted({str(item[1]) for item in relevant}),
                        "meanings": sorted({
                            f"{category}: {text}" for text, _, category, _ in relevant
                        }),
                    },
                ))
        return issues

    def _schedule_structures(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Validate conditional definitions, repeat blocks, and confinement episodes.

        These are the structures a reviewer cannot easily eyeball, so an impossible
        one is blocked at approval rather than discovered on a patient.
        """
        issues: list[ValidationIssue] = []
        events = {item.code for item in schedule.events}
        blocks = {item.code for item in schedule.repeat_blocks}
        episodes = {item.code for item in schedule.confinement_episodes}
        processes = {item.code for item in schedule.processes}
        activity_codes = {
            activity.code
            for event in schedule.events for activity in event.activities
            if activity.code
        }
        evidence_ids = {item.id for item in schedule.evidence}
        claims = {(item.claim_entity_id, item.claim_type) for item in schedule.claim_evidence}

        def require_structure_evidence(
            *, entity_type: str, entity_id: UUID, refs: list[UUID],
            claims_required: list[str], message: str,
        ) -> None:
            missing_refs = [str(ref) for ref in refs if ref not in evidence_ids]
            missing_claims = [
                claim_type for claim_type in claims_required
                if (entity_id, claim_type) not in claims
            ]
            if not refs or missing_refs or missing_claims:
                issues.append(self._issue(
                    "MISSING_EVIDENCE", message,
                    entity_type=entity_type, entity_id=entity_id,
                    details={
                        "missing_reference_ids": missing_refs,
                        "missing_claim_types": missing_claims,
                    },
                ))

        for definition in schedule.conditional_definitions:
            for action in definition.actions:
                known = {
                    "ADD_EVENT": events, "REPEAT_EVENT": events, "CANCEL_EVENT": events,
                    "MANUAL_REVIEW": events, "STOP_BLOCK": blocks, "PAUSE_BLOCK": blocks,
                    "RESUME_BLOCK": blocks, "EXTEND_CONFINEMENT": episodes,
                    "HOLD_PROCESS": processes, "RESUME_PROCESS": processes,
                    "STOP_PROCESS": processes, "COMPLETE_PROCESS": processes,
                }[action.action_type]
                if action.action_type == "RESUME_PROCESS" and "resume_policy" in action.parameters:
                    from .processes import validate_resume_policy

                    try:
                        validate_resume_policy(action.parameters["resume_policy"])
                    except ValueError:
                        issues.append(self._issue(
                            "AMBIGUOUS_CONDITION",
                            "A resume action's resume_policy must be NOMINAL, SHIFT_BY_HOLD or "
                            "RESTART_FROM_RESUME",
                            entity_type="CONDITION", entity_id=definition.id,
                        ))
                if action.target_code not in known:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        f"Conditional action {action.action_type} targets unknown "
                        f"{action.target_code!r}",
                        entity_type="CONDITION", entity_id=definition.id,
                    ))
                if action.action_type == "REPEAT_EVENT" and "interval" not in action.parameters:
                    issues.append(self._issue(
                        "AMBIGUOUS_CONDITION",
                        "A repeat-until-resolution action requires its repeat interval",
                        entity_type="CONDITION", entity_id=definition.id,
                    ))
                if action.action_type == "EXTEND_CONFINEMENT" and "duration" not in action.parameters:
                    issues.append(self._issue(
                        "AMBIGUOUS_CONDITION",
                        "A confinement extension requires its duration",
                        entity_type="CONDITION", entity_id=definition.id,
                    ))
            repeats = [item for item in definition.actions if item.action_type == "REPEAT_EVENT"]
            if repeats and definition.resolution_condition is None:
                issues.append(self._issue(
                    "AMBIGUOUS_CONDITION",
                    "A repeating conditional action requires an explicit resolution rule",
                    entity_type="CONDITION", entity_id=definition.id,
                ))
            conditional_claims = ["CONDITION", "CONDITIONAL_ACTION"]
            if definition.resolution_condition is not None:
                conditional_claims.append("CONDITIONAL_RESOLUTION")
            require_structure_evidence(
                entity_type="CONDITION", entity_id=definition.id,
                refs=definition.evidence_refs, claims_required=conditional_claims,
                message=(
                    "Conditional trigger, actions, and resolution require claim-level "
                    "protocol evidence"
                ),
            )
            if definition.interpretation_status == InterpretationStatus.UNRESOLVED:
                issues.append(self._issue(
                    "UNRESOLVED_CONDITION",
                    "Conditional requirement needs reviewer confirmation before approval",
                    entity_type="CONDITION", entity_id=definition.id,
                ))

        for block in schedule.repeat_blocks:
            require_structure_evidence(
                entity_type="REPEAT_BLOCK", entity_id=block.id,
                refs=block.evidence_refs,
                claims_required=["REPEAT_BLOCK", "DEPENDENCY_MODE"],
                message=(
                    "Repeating-block membership and dependency mode require "
                    "claim-level protocol evidence"
                ),
            )
            unknown = sorted(set(block.event_codes) - events)
            if unknown:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE", "Repeating block references unknown events",
                    entity_type="REPEAT_BLOCK", entity_id=block.id,
                    details={"event_codes": unknown},
                ))
            # The nominal-vs-actual question only has a clinical answer when
            # something in the block repeats. A block that exists purely so a
            # condition can pause or stop single visits has no "later cycle" to
            # place, so demanding the protocol state one would block approval
            # over a question the protocol never had to answer.
            repeats = block.cycle_plan is not None or any(
                event.repeats
                for event in schedule.events if event.code in block.event_codes
            )
            if block.dependency_mode == DependencyMode.UNCLEAR and repeats:
                issues.append(self._issue(
                    "UNCLEAR_DEPENDENCY_MODE",
                    "A repeating block must state whether later cycles follow the nominal "
                    "schedule or the actual previous cycle",
                    entity_type="REPEAT_BLOCK", entity_id=block.id,
                ))

        for episode in schedule.confinement_episodes:
            require_structure_evidence(
                entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                refs=episode.evidence_refs, claims_required=["CONFINEMENT"],
                message=(
                    "Confinement admission, study-day, dosing, and discharge structure "
                    "requires claim-level protocol evidence"
                ),
            )
            references = [
                episode.admission_event_code, *episode.dose_event_codes,
                episode.discharge_event_code,
            ]
            unknown = sorted(set(references) - events)
            if unknown:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    "Confinement episode references unknown events",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                    details={"event_codes": unknown},
                ))
            # Doc 6 section 10: admission, dosing, and discharge carry different
            # clinical meaning and must not be flattened into one event.
            if len(set(references)) != len(references):
                issues.append(self._issue(
                    "INVALID_CONFINEMENT",
                    "Admission, dose, and discharge must remain distinct events",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                ))
            relative_days = [day.relative_day for day in episode.days]
            if len(set(relative_days)) != len(relative_days):
                issues.append(self._issue(
                    "INVALID_CONFINEMENT",
                    "A confinement episode cannot contain two study days with the same number",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                ))
            if 0 in relative_days:
                issues.append(self._issue(
                    "INVALID_CONFINEMENT",
                    "Clinical day numbering has no Day 0",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                ))
            unknown_activities = sorted({
                code for day in episode.days for code in day.activity_codes
            } - activity_codes)
            if unknown_activities:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    "Confinement study day references unknown activities",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                    details={"activity_codes": unknown_activities},
                ))
        return issues

    def _cycle_plans(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc F1-F3: a cycle plan must say how each cycle start is dated.

        UNCLEAR blocks approval: nominal grid and follow-the-actual-cycle give
        different dates from Cycle 3 on, and the engine never picks one.
        """
        issues: list[ValidationIssue] = []
        events = {item.code for item in schedule.events}
        anchors = {item.code for item in schedule.anchors}
        claims = {(item.claim_entity_id, item.claim_type) for item in schedule.claim_evidence}
        seen: set[str] = set()
        for block in schedule.repeat_blocks:
            plan = block.cycle_plan
            if plan is None:
                continue
            details = {"cycle_plan": plan.code, "block": block.code}
            if plan.code in seen:
                issues.append(self._issue(
                    "DUPLICATE_CYCLE_PLAN", f"Duplicate cycle plan code {plan.code!r}",
                    entity_type="REPEAT_BLOCK", entity_id=block.id, details=details,
                ))
            seen.add(plan.code)
            if plan.start_reference.code not in anchors:
                issues.append(self._issue(
                    "UNKNOWN_ANCHOR",
                    f"Cycle plan {plan.code!r} starts from unknown anchor "
                    f"{plan.start_reference.code!r}",
                    entity_type="REPEAT_BLOCK", entity_id=block.id, details=details,
                ))
            if plan.start_event_code is not None and plan.start_event_code not in events:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    f"Cycle plan {plan.code!r} reads cycle starts from unknown visit "
                    f"{plan.start_event_code!r}",
                    entity_type="REPEAT_BLOCK", entity_id=block.id, details=details,
                ))
            termination = plan.termination
            if termination.type == "RELATIVE" and termination.end_reference.code not in anchors:
                issues.append(self._issue(
                    "UNKNOWN_ANCHOR", f"Unknown anchor {termination.end_reference.code!r}",
                    entity_type="REPEAT_BLOCK", entity_id=block.id, details=details,
                ))
            if termination.type == "EVENT" and termination.event_code not in events:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    f"Cycle plan {plan.code!r} ends at unknown visit {termination.event_code!r}",
                    entity_type="REPEAT_BLOCK", entity_id=block.id, details=details,
                ))
            if plan.dependency_mode == CycleDependencyMode.UNCLEAR:
                issues.append(self._issue(
                    "UNCLEAR_DEPENDENCY_MODE",
                    "The protocol does not state whether each cycle follows the nominal "
                    "grid or the actual start of the previous cycle",
                    entity_type="REPEAT_BLOCK", entity_id=block.id, details=details,
                ))
            elif CYCLE_BLOCK_DEPENDENCY_MODES[plan.dependency_mode] != block.dependency_mode:
                issues.append(self._issue(
                    "CONFLICTING_SOURCE",
                    "The cycle plan and its repeating block state different dependency modes",
                    entity_type="REPEAT_BLOCK", entity_id=block.id,
                    details={
                        **details, "cycle_mode": plan.dependency_mode.value,
                        "block_mode": block.dependency_mode.value,
                    },
                ))
            if (plan.dependency_mode == CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START
                    and plan.start_event_code is None):
                issues.append(self._issue(
                    "CYCLE_START_SOURCE_UNSTATED",
                    "Cycles follow the actual previous cycle but no Day-1 visit is named; "
                    f"each actual start must be recorded as {plan.code}_<n>_START",
                    entity_type="REPEAT_BLOCK", entity_id=block.id, details=details,
                    severity=Severity.WARNING, blocking=False,
                ))
            if (block.id, "CYCLE_PLAN") not in claims:
                issues.append(self._issue(
                    "MISSING_EVIDENCE",
                    "Cycle length, count and dependency mode require claim-level evidence",
                    entity_type="REPEAT_BLOCK", entity_id=block.id,
                    details={**details, "claim_type": "CYCLE_PLAN"},
                ))
        return issues

    def _recurrence_plans(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc E4/E9: every segment boundary is stated, never assumed."""
        issues: list[ValidationIssue] = []
        for event in schedule.events:
            plan = event.recurrence_plan
            if plan is None:
                continue
            details: dict[str, object] = {"event_code": event.code}
            if plan.include_start is None:
                issues.append(self._issue(
                    "UNRESOLVED_RECURRENCE_BOUNDARY",
                    f"{event.display_name!r}: the follow-up plan does not state whether a "
                    "visit falls on its start date",
                    entity_type="EVENT", entity_id=event.id, details=details,
                ))
            previous_end: tuple[str, int] | None = None
            last = len(plan.segments) - 1
            for index, segment in enumerate(plan.segments):
                segment_details = {**details, "segment": index + 1}
                if segment.end_offset is not None and segment.end_inclusive is None:
                    issues.append(self._issue(
                        "UNRESOLVED_RECURRENCE_BOUNDARY",
                        f"{event.display_name!r}: segment {index + 1} does not state "
                        "whether the visit on its end boundary belongs to it",
                        entity_type="EVENT", entity_id=event.id, details=segment_details,
                    ))
                open_end = segment.end_offset is None and segment.count is None
                if open_end and (index < last or plan.termination is None):
                    issues.append(self._issue(
                        "PHASED_SEGMENT_UNBOUNDED",
                        f"{event.display_name!r}: segment {index + 1} states no end"
                        + ("" if index < last else " and the plan states no termination"),
                        entity_type="EVENT", entity_id=event.id, details=segment_details,
                    ))
                if segment.end_offset is not None:
                    end = _comparable(segment.end_offset)
                    if previous_end is not None and (
                            end[0] == previous_end[0] and end[1] <= previous_end[1]):
                        issues.append(self._issue(
                            "CONFLICTING_SOURCE",
                            f"{event.display_name!r}: segment {index + 1} does not end "
                            "after the previous segment",
                            entity_type="EVENT", entity_id=event.id, details=segment_details,
                        ))
                    previous_end = end
        return issues

    def _calendar_recurrences(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """WP3 (RL-08/RL-09): material calendar policies are stated or visible.

        An unstated holiday, missed-session or count-basis policy blocks only
        the sessions that depend on it at runtime, so it is a WARNING here; a
        stated policy that cannot be executed (no calendar to shift against) or
        two policies that contradict each other block approval.
        """
        from .models import CountBasis, HolidayPolicy, MissedOccurrencePolicy

        issues: list[ValidationIssue] = []
        calendars = {item.code for item in schedule.holiday_calendars}
        for event in schedule.events:
            rule = event.calendar_recurrence
            if rule is None:
                continue
            details = {"event_code": event.code}
            if rule.holiday_policy != HolidayPolicy.NONE_STATED and rule.holiday_calendar_ref is None:
                issues.append(self._issue(
                    "CALENDAR_HOLIDAY_CALENDAR_REQUIRED",
                    f"{event.display_name!r} states holiday policy {rule.holiday_policy.value} "
                    "but names no holiday calendar to apply it with",
                    entity_type="EVENT", entity_id=event.id, details=details))
            if rule.holiday_calendar_ref is not None and rule.holiday_calendar_ref not in calendars:
                issues.append(self._issue(
                    "CALENDAR_HOLIDAY_CALENDAR_NOT_IN_SCHEDULE",
                    f"Holiday calendar {rule.holiday_calendar_ref!r} is not part of the schedule; "
                    "each site must supply it or the series stays undated",
                    entity_type="EVENT", entity_id=event.id, blocking=False,
                    severity=Severity.WARNING,
                    details={**details, "calendar": rule.holiday_calendar_ref}))
            if rule.holiday_policy == HolidayPolicy.NONE_STATED:
                issues.append(self._issue(
                    "CALENDAR_HOLIDAY_POLICY_UNSTATED",
                    f"{event.display_name!r} does not state what happens to a session on a "
                    "holiday; such a session will wait for review",
                    entity_type="EVENT", entity_id=event.id, blocking=False,
                    severity=Severity.WARNING, details=details))
            if rule.termination.type == "COUNT" and rule.count_basis is None:
                issues.append(self._issue(
                    "CALENDAR_COUNT_BASIS_UNSTATED",
                    f"{event.display_name!r} states {rule.termination.count} sessions but not "
                    "whether that counts slots or delivered sessions; after a missed or held "
                    "session the extra session will wait for review",
                    entity_type="EVENT", entity_id=event.id, blocking=False,
                    severity=Severity.WARNING, details=details))
            if rule.missed_policy == MissedOccurrencePolicy.NONE_STATED and rule.count_basis != CountBasis.DELIVERED:
                issues.append(self._issue(
                    "CALENDAR_MISSED_POLICY_UNSTATED",
                    f"{event.display_name!r} does not state whether a missed session is "
                    "replaced; a replacement will wait for review",
                    entity_type="EVENT", entity_id=event.id, blocking=False,
                    severity=Severity.WARNING, details=details))
            if (rule.count_basis == CountBasis.DELIVERED
                    and rule.missed_policy == MissedOccurrencePolicy.NO_REPLACEMENT):
                issues.append(self._issue(
                    "CALENDAR_COUNT_BASIS_CONFLICT",
                    f"{event.display_name!r} counts delivered sessions but also says a missed "
                    "session is not replaced; the two readings contradict each other",
                    entity_type="EVENT", entity_id=event.id, details=details))
        return issues

    def _processes(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """WP3 (RL-10/RL-11): process definitions are well formed and evidenced."""
        from .models import HoldResumePolicy

        issues: list[ValidationIssue] = []
        events = {item.code for item in schedule.events}
        evidence_ids = {item.id for item in schedule.evidence}
        claims = {(item.claim_entity_id, item.claim_type) for item in schedule.claim_evidence}
        owners: dict[str, str] = {}
        seen: set[str] = set()
        for process in schedule.processes:
            if process.code in seen:
                issues.append(self._issue(
                    "DUPLICATE_CODE", f"Process code {process.code!r} is used more than once",
                    entity_type="PROCESS", entity_id=process.id))
            seen.add(process.code)
            unknown = sorted(set(process.event_codes) - events)
            if unknown:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE", "Process links unknown events",
                    entity_type="PROCESS", entity_id=process.id,
                    details={"event_codes": unknown}))
            for code in process.event_codes:
                if code in owners and owners[code] != process.code:
                    issues.append(self._issue(
                        "PROCESS_OBLIGATION_CONFLICT",
                        f"Event {code!r} is linked to processes {owners[code]!r} and "
                        f"{process.code!r}; a stop of one would be ambiguous for it",
                        entity_type="PROCESS", entity_id=process.id,
                        details={"event_code": code}))
                owners.setdefault(code, process.code)
            missing_refs = [str(ref) for ref in process.evidence_refs if ref not in evidence_ids]
            if not process.evidence_refs or missing_refs or (process.id, "PROCESS") not in claims:
                issues.append(self._issue(
                    "MISSING_EVIDENCE", "A process definition requires claim-level protocol evidence",
                    entity_type="PROCESS", entity_id=process.id,
                    details={"missing_reference_ids": missing_refs, "claim_type": "PROCESS"}))
            if process.hold_resume_policy == HoldResumePolicy.NONE_STATED:
                issues.append(self._issue(
                    "PROCESS_RESUME_POLICY_UNSTATED",
                    f"Process {process.code!r} does not state how its schedule continues after "
                    "a hold; obligations after a resume will wait for a decision",
                    entity_type="PROCESS", entity_id=process.id, blocking=False,
                    severity=Severity.WARNING))
        return issues

    @staticmethod
    def blocking(issues: list[ValidationIssue]) -> list[ValidationIssue]:
        # RL-12: an unsupported operator stays blocking whatever its review
        # status - acknowledging a capability gap does not make it executable.
        from .capabilities import NON_ACKNOWLEDGEABLE_CODES

        return [
            issue for issue in issues
            if issue.blocking and (
                issue.status == IssueStatus.OPEN
                or issue.issue_code in NON_ACKNOWLEDGEABLE_CODES)
        ]


def topological_event_codes(schedule: UniversalSchedule) -> list[str]:
    """Stable topological ordering used by the evaluator after validation."""
    original = {event.code: index for index, event in enumerate(schedule.events)}
    indegree = {event.code: 0 for event in schedule.events}
    edges: dict[str, set[str]] = defaultdict(set)
    anchors = {anchor.code: anchor for anchor in schedule.anchors}
    cycle_starts = {
        block.cycle_plan.code: block.cycle_plan.start_reference.code
        for block in schedule.repeat_blocks if block.cycle_plan is not None
    }
    for target in schedule.events:
        refs = {item.source_event_code for item in target.dependencies}
        if isinstance(target.timing, CycleDayTiming) and target.timing.cycle_reference in cycle_starts:
            anchor = anchors.get(cycle_starts[target.timing.cycle_reference])
            # A Day-1 visit may itself be the source of Cycle 1's anchor.
            if anchor and anchor.source_event_code and anchor.source_event_code != target.code:
                refs.add(anchor.source_event_code)
        for timing_ref in _graph_references(target):
            if isinstance(timing_ref, EventReference):
                refs.add(timing_ref.event_code)
            else:
                anchor = anchors.get(getattr(timing_ref, "code", ""))
                if anchor and anchor.source_event_code:
                    refs.add(anchor.source_event_code)
        for source in refs:
            if source in indegree and target.code not in edges[source]:
                edges[source].add(target.code)
                indegree[target.code] += 1
    ready = sorted((code for code, degree in indegree.items() if degree == 0), key=original.get)
    result: list[str] = []
    while ready:
        source = ready.pop(0)
        result.append(source)
        for target in sorted(edges[source], key=original.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=original.get)
    if len(result) != len(schedule.events):
        raise ValueError("event graph contains a cycle")
    return result
