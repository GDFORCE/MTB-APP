"""Capability registry for the executable protocol language (RL-01, RL-12).

Every operator the deterministic engine can execute is declared here with a
family, operator name and semantic version. ``capability_issues`` walks every
nested expression of a schedule - timings, constraint trees, temporal values,
conditions, applicability, recurrences, terminations, conditional actions,
calendar policies - and reports anything that is not a declared executable
operator as ``UNSUPPORTED_OPERATOR``. There is no catch-all: an expression node
the walk does not recognise is itself reported, never skipped.

An ``UNSUPPORTED`` placeholder (``UnsupportedConstraint`` /
``UnsupportedCondition``) is how extraction records a source requirement the
language cannot express. It keeps its source operator name and evidence, and
blocks approval together with every rule that transitively depends on it.
Those issues cannot be cleared by acknowledgement (``NON_ACKNOWLEDGEABLE_CODES``
- see ``ScheduleValidator.blocking``): only changing the rule to supported
operators, or removing it through an authorized edit, makes it go away.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from uuid import UUID

from .condition import (
    AllCondition,
    AnyCondition,
    ComparisonCondition,
    ExistsCondition,
    MembershipCondition,
    NotCondition,
    UnsupportedCondition,
)
from .feasibility import KNOWN_CALENDAR_POLICIES
from .models import (
    WEEKDAY_GRID_POLICY,
    Activity,
    ApplicabilityRule,
    CalendarRecurrence,
    CountBasis,
    HoldResumePolicy,
    HolidayPolicy,
    MissedOccurrencePolicy,
    OccurrenceFactKind,
    ProcessState,
    ConditionalAction,
    ConditionalDefinition,
    Event,
    RecurrencePlan,
    RecurrenceRule,
    RecurrenceTermination,
    Severity,
    UniversalSchedule,
    ValidationIssue,
)
from .timing import (
    CONSTRAINT_LANGUAGE_VERSION,
    AbsoluteTiming,
    ActivityReference,
    ActivityValue,
    AllConstraint,
    AnchorReference,
    AnchorValue,
    AnyConstraint,
    ApproximateTiming,
    AtConstraint,
    ConstraintTiming,
    CycleDayTiming,
    EventReference,
    EventValue,
    LiteralDateValue,
    LowerBoundConstraint,
    ProcessBoundaryValue,
    NoLaterThanTiming,
    NominalWindowTiming,
    OffsetTiming,
    ProtocolDayTiming,
    ProtocolDefinedTiming,
    RangeTiming,
    ShiftValue,
    TriggeredTiming,
    TriggerNoLaterThan,
    TriggerOffset,
    TriggerWithin,
    UnresolvedTiming,
    UnsupportedConstraint,
    UpperBoundConstraint,
    WithinTiming,
)

REGISTRY_VERSION = "uctsm-capabilities.v1"

#: Semantic version of every operator WP3 added (calendar recurrence, process
#: states, occurrence/assignment facts, START/END and PROCESS_BOUNDARY values).
WP3_VERSION = "uctsm-wp3.v1"

UNSUPPORTED_OPERATOR = "UNSUPPORTED_OPERATOR"
DEPENDS_ON_UNSUPPORTED_OPERATOR = "DEPENDS_ON_UNSUPPORTED_OPERATOR"

#: Issue codes whose blocking effect survives any review status (RL-12).
NON_ACKNOWLEDGEABLE_CODES = frozenset({UNSUPPORTED_OPERATOR, DEPENDS_ON_UNSUPPORTED_OPERATOR})


@dataclass(frozen=True)
class Capability:
    family: str
    operator: str
    version: str
    executable: bool
    #: Legacy non-executable forms already governed by their own validator
    #: issues (UNSUPPORTED_TIMING / AMBIGUOUS_TIMING, with the ON_DEMAND
    #: exemption); the capability walk leaves those to the existing checks so
    #: approved legacy schedules keep their meaning (NF-08).
    legacy_gated: bool = False
    description: str = ""


def _caps(family: str, version: str, *operators: str, **extra) -> list[Capability]:
    return [Capability(family, operator, version, True, **extra) for operator in operators]


_ENTRIES: list[Capability] = [
    *_caps("TIMING", "1", "ABSOLUTE", "OFFSET", "PROTOCOL_DAY", "RANGE",
           "NOMINAL_WITH_WINDOW", "WITHIN", "NO_LATER_THAN", "TRIGGERED", "CYCLE_DAY"),
    Capability("TIMING", "CONSTRAINED", CONSTRAINT_LANGUAGE_VERSION, True,
               description="timing stated only as a constraint set"),
    Capability("TIMING", "APPROXIMATE", "1", False, legacy_gated=True,
               description="needs an approved precision policy"),
    Capability("TIMING", "PROTOCOL_DEFINED", "1", False, legacy_gated=True,
               description="site/sponsor confirmation; executable only as ON_DEMAND"),
    Capability("TIMING", "UNRESOLVED", "1", False, legacy_gated=True,
               description="timing awaiting review"),
    *_caps("TRIGGER_TIMING", "1", "OFFSET", "WITHIN", "NO_LATER_THAN"),
    *_caps("TEMPORAL_REFERENCE", "1", "ANCHOR", "EVENT", "ACTIVITY"),
    *_caps("TEMPORAL_VALUE", CONSTRAINT_LANGUAGE_VERSION,
           "ANCHOR", "EVENT", "ACTIVITY", "LITERAL_DATE", "SHIFT"),
    *_caps("OCCURRENCE_SELECTOR", CONSTRAINT_LANGUAGE_VERSION,
           "FIRST", "LAST", "CURRENT", "PREVIOUS", "NUMBER"),
    *_caps("CONSTRAINT", CONSTRAINT_LANGUAGE_VERSION,
           "LOWER_BOUND", "UPPER_BOUND", "AT", "ALL", "ANY"),
    *_caps("CONDITION", "1", "COMPARISON", "MEMBERSHIP", "EXISTS", "AND", "OR", "NOT"),
    *_caps("APPLICABILITY_OPERATOR", "1", "IN", "NOT_IN", "EQ", "NE", "LT", "LTE", "GT", "GTE"),
    *_caps("RECURRENCE", "1", "INTERVAL", "SEGMENTED", "CYCLE_PLAN"),
    *_caps("RECURRENCE_TERMINATION", "1",
           "COUNT", "DATE", "EVENT", "CONDITION", "HORIZON", "RELATIVE"),
    *_caps("CONDITIONAL_ACTION", "1", "ADD_EVENT", "REPEAT_EVENT", "STOP_BLOCK",
           "PAUSE_BLOCK", "RESUME_BLOCK", "EXTEND_CONFINEMENT", "CANCEL_EVENT",
           "MANUAL_REVIEW"),
    *(Capability("CALENDAR_POLICY", policy, "1", True) for policy in sorted(KNOWN_CALENDAR_POLICIES)),
    # --- WP3 (calendar recurrence, process states, facts) -------------------
    Capability("RECURRENCE", "CALENDAR", WP3_VERSION, True,
               description="weekday recurrence on the WEEKDAY_GRID.v1 grid with explicit "
                           "holiday, missed-session and count-basis policies"),
    Capability("CALENDAR_POLICY", WEEKDAY_GRID_POLICY, WP3_VERSION, True,
               description="weekdays in weeks counted from the week containing the start"),
    *_caps("HOLIDAY_POLICY", WP3_VERSION, *(item.value for item in HolidayPolicy)),
    *_caps("MISSED_OCCURRENCE_POLICY", WP3_VERSION, *(item.value for item in MissedOccurrencePolicy)),
    *_caps("COUNT_BASIS", WP3_VERSION, *(item.value for item in CountBasis)),
    *_caps("CONDITIONAL_ACTION", WP3_VERSION,
           "HOLD_PROCESS", "RESUME_PROCESS", "STOP_PROCESS", "COMPLETE_PROCESS"),
    *_caps("PROCESS_STATE", WP3_VERSION, *(item.value for item in ProcessState)),
    *_caps("HOLD_RESUME_POLICY", WP3_VERSION, *(item.value for item in HoldResumePolicy)),
    *_caps("PROCESS_TARGET", WP3_VERSION, "PROCESS", "PROCESS_KIND", "PATIENT"),
    Capability("TEMPORAL_VALUE", "PROCESS_BOUNDARY", WP3_VERSION, True,
               description="actual start/end of an intervention or pathway process"),
    *_caps("OCCURRENCE_BOUNDARY", WP3_VERSION, "START", "END"),
    *_caps("OCCURRENCE_FACT", WP3_VERSION, *(item.value for item in OccurrenceFactKind)),
    Capability("ASSIGNMENT_HISTORY", "DATED_ASSIGNMENT", WP3_VERSION, True,
               description="assignment evaluated per period of its effective history"),
]

REGISTRY: dict[tuple[str, str], Capability] = {
    (item.family, item.operator): item for item in _ENTRIES
}


def lookup(family: str, operator: str) -> Capability | None:
    return REGISTRY.get((family, operator))


def is_executable(family: str, operator: str) -> bool:
    capability = lookup(family, operator)
    return capability is not None and capability.executable


def supported_operators() -> dict[str, object]:
    """The registry as JSON, for the API/UI and for extraction prompts."""
    return {
        "registry_version": REGISTRY_VERSION,
        "constraint_language_version": CONSTRAINT_LANGUAGE_VERSION,
        "operators": [
            {
                "family": item.family, "operator": item.operator, "version": item.version,
                "executable": item.executable, "legacy_gated": item.legacy_gated,
                "description": item.description,
            }
            for item in _ENTRIES
        ],
    }


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


@dataclass
class OperatorUse:
    family: str
    operator: str
    owner_type: str
    owner_id: UUID
    owner_code: str
    path: str
    node: object = None
    #: For UNSUPPORTED placeholders: what the source asked for.
    source_operator: str | None = None
    source_text: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    rule_id: str | None = None


_TIMING_CLASSES = {
    AbsoluteTiming, OffsetTiming, ProtocolDayTiming, RangeTiming, NominalWindowTiming,
    WithinTiming, NoLaterThanTiming, ApproximateTiming, TriggeredTiming, CycleDayTiming,
    ProtocolDefinedTiming, UnresolvedTiming, ConstraintTiming,
}


class _Walker:
    def __init__(self) -> None:
        self.uses: list[OperatorUse] = []
        self.owner: tuple[str, UUID, str] = ("SCHEDULE", UUID(int=0), "")

    def use(self, family: str, operator: str, path: str, node: object = None, **extra) -> None:
        owner_type, owner_id, owner_code = self.owner
        self.uses.append(OperatorUse(
            family=family, operator=operator, owner_type=owner_type, owner_id=owner_id,
            owner_code=owner_code, path=path, node=node, **extra))

    def unknown(self, family: str, node: object, path: str) -> None:
        self.use(family, f"<{type(node).__name__}>", path, node)

    # -- temporal ------------------------------------------------------------
    def reference(self, reference: object, path: str) -> None:
        if isinstance(reference, (AnchorReference, EventReference, ActivityReference)):
            self.use("TEMPORAL_REFERENCE", reference.kind, path, reference)
        else:
            self.unknown("TEMPORAL_REFERENCE", reference, path)

    def timing(self, timing: object, path: str) -> None:
        if timing is None:
            return
        if type(timing) not in _TIMING_CLASSES:
            self.unknown("TIMING", timing, path)
            return
        self.use("TIMING", timing.type, path, timing)
        reference = getattr(timing, "reference", None)
        if reference is not None:
            self.reference(reference, f"{path}/reference")
        if isinstance(timing, NominalWindowTiming):
            self.timing(timing.nominal, f"{path}/nominal")
        if isinstance(timing, TriggeredTiming):
            self.reference(timing.trigger, f"{path}/trigger")
            nested = timing.timing_after_trigger
            if isinstance(nested, (TriggerOffset, TriggerWithin, TriggerNoLaterThan)):
                self.use("TRIGGER_TIMING", nested.type, f"{path}/timing_after_trigger", nested)
            else:
                self.unknown("TRIGGER_TIMING", nested, f"{path}/timing_after_trigger")
        if isinstance(timing, ConstraintTiming):
            self.constraint(timing.constraint, f"{path}/constraint")

    def value(self, value: object, path: str) -> None:
        if isinstance(value, (AnchorValue, ActivityValue, LiteralDateValue)):
            self.use("TEMPORAL_VALUE", value.type, path, value)
        elif isinstance(value, ProcessBoundaryValue):
            self.use("TEMPORAL_VALUE", value.type, path, value)
            self.use("OCCURRENCE_BOUNDARY", value.boundary, f"{path}/boundary", value)
        elif isinstance(value, EventValue):
            self.use("TEMPORAL_VALUE", value.type, path, value)
            self.use("OCCURRENCE_SELECTOR", value.occurrence.type, f"{path}/occurrence",
                     value.occurrence)
            if value.boundary is not None:
                self.use("OCCURRENCE_BOUNDARY", value.boundary, f"{path}/boundary", value)
        elif isinstance(value, ShiftValue):
            self.use("TEMPORAL_VALUE", value.type, path, value)
            if value.calendar_policy_ref is not None:
                self.use("CALENDAR_POLICY", value.calendar_policy_ref,
                         f"{path}/calendar_policy_ref", value)
            self.value(value.reference, f"{path}/reference")
        else:
            self.unknown("TEMPORAL_VALUE", value, path)

    def constraint(self, node: object, path: str) -> None:
        if isinstance(node, UnsupportedConstraint):
            self.use(node.family, "UNSUPPORTED", path, node,
                     source_operator=node.source_operator, source_text=node.source_text,
                     evidence_refs=list(node.evidence_refs), rule_id=node.id)
        elif isinstance(node, (AllConstraint, AnyConstraint)):
            self.use("CONSTRAINT", node.type, path, node, rule_id=node.id)
            for index, child in enumerate(node.items):
                self.constraint(child, f"{path}/items/{index}")
        elif isinstance(node, (LowerBoundConstraint, UpperBoundConstraint, AtConstraint)):
            self.use("CONSTRAINT", node.type, path, node, rule_id=node.id)
            self.value(node.value, f"{path}/value")
        else:
            self.unknown("CONSTRAINT", node, path)

    # -- predicates ------------------------------------------------------------
    def condition(self, node: object, path: str) -> None:
        if node is None:
            return
        if isinstance(node, UnsupportedCondition):
            self.use("CONDITION", "UNSUPPORTED", path, node,
                     source_operator=node.source_operator, source_text=node.source_text,
                     evidence_refs=list(node.evidence_refs))
        elif isinstance(node, (AllCondition, AnyCondition)):
            self.use("CONDITION", node.type, path, node)
            for index, child in enumerate(node.conditions):
                self.condition(child, f"{path}/conditions/{index}")
        elif isinstance(node, NotCondition):
            self.use("CONDITION", node.type, path, node)
            self.condition(node.condition, f"{path}/condition")
        elif isinstance(node, (ComparisonCondition, MembershipCondition, ExistsCondition)):
            self.use("CONDITION", node.type, path, node)
        else:
            self.unknown("CONDITION", node, path)

    def applicability(self, rules: list[ApplicabilityRule], path: str) -> None:
        for index, rule in enumerate(rules):
            self.use("APPLICABILITY_OPERATOR", rule.operator, f"{path}/{index}", rule)
            self.condition(rule.condition, f"{path}/{index}/condition")

    def termination(self, termination: RecurrenceTermination | None, path: str) -> None:
        if termination is None:
            return
        self.use("RECURRENCE_TERMINATION", termination.type, path, termination)
        self.condition(termination.condition, f"{path}/condition")
        if termination.end_reference is not None:
            self.reference(termination.end_reference, f"{path}/end_reference")

    def recurrence(self, rule: object, path: str) -> None:
        if rule is None:
            return
        if isinstance(rule, RecurrenceRule):
            self.use("RECURRENCE", rule.type, path, rule)
            self.reference(rule.start_reference, f"{path}/start_reference")
            self.termination(rule.termination, f"{path}/termination")
        elif isinstance(rule, RecurrencePlan):
            self.use("RECURRENCE", rule.type, path, rule)
            self.reference(rule.start_reference, f"{path}/start_reference")
            self.termination(rule.termination, f"{path}/termination")
        elif isinstance(rule, CalendarRecurrence):
            self.use("RECURRENCE", rule.type, path, rule)
            self.reference(rule.start_reference, f"{path}/start_reference")
            self.termination(rule.termination, f"{path}/termination")
            self.use("CALENDAR_POLICY", rule.calendar_policy_ref, f"{path}/calendar_policy_ref", rule)
            self.use("HOLIDAY_POLICY", rule.holiday_policy.value, f"{path}/holiday_policy", rule)
            self.use("MISSED_OCCURRENCE_POLICY", rule.missed_policy.value, f"{path}/missed_policy", rule)
            if rule.count_basis is not None:
                self.use("COUNT_BASIS", rule.count_basis.value, f"{path}/count_basis", rule)
        else:
            self.unknown("RECURRENCE", rule, path)
        if isinstance(rule, RecurrenceRule) and rule.calendar_policy_ref is not None:
            self.use("CALENDAR_POLICY", rule.calendar_policy_ref, f"{path}/calendar_policy_ref", rule)

    def actions(self, actions: list[ConditionalAction], path: str) -> None:
        for index, action in enumerate(actions):
            self.use("CONDITIONAL_ACTION", action.action_type, f"{path}/{index}", action)
            self.condition(action.condition, f"{path}/{index}/condition")

    # -- owners --------------------------------------------------------------
    def activity(self, event: Event, activity: Activity) -> None:
        self.owner = ("ACTIVITY", activity.id, f"{event.code}/{activity.code or activity.id}")
        self.timing(activity.timing, "timing")
        if activity.constraints is not None:
            self.constraint(activity.constraints, "constraints")
        for index, item in enumerate(activity.conditions):
            self.condition(item, f"conditions/{index}")
        self.applicability(activity.applicability, "applicability")

    def event(self, event: Event) -> None:
        self.owner = ("EVENT", event.id, event.code)
        self.timing(event.timing, "timing")
        if event.constraints is not None:
            self.constraint(event.constraints, "constraints")
        for index, item in enumerate(event.conditions):
            self.condition(item, f"conditions/{index}")
        self.applicability(event.applicability, "applicability")
        for index, dependency in enumerate(event.dependencies):
            self.condition(dependency.condition, f"dependencies/{index}/condition")
        self.recurrence(event.recurrence, "recurrence")
        self.recurrence(event.recurrence_plan, "recurrence_plan")
        self.recurrence(event.calendar_recurrence, "calendar_recurrence")
        self.actions(event.conditional_actions, "conditional_actions")
        for activity in event.activities:
            self.activity(event, activity)

    def conditional(self, definition: ConditionalDefinition) -> None:
        self.owner = ("CONDITION", definition.id, definition.code)
        self.condition(definition.condition, "condition")
        self.condition(definition.resolution_condition, "resolution_condition")
        self.applicability(definition.applicability, "applicability")
        self.actions(definition.actions, "actions")
        self.recurrence(definition.recurrence, "recurrence")


def operator_uses(schedule: UniversalSchedule) -> list[OperatorUse]:
    """Every operator occurrence in the schedule, in a stable order."""
    walker = _Walker()
    for epoch in schedule.epochs:
        walker.owner = ("EPOCH", epoch.id, epoch.code)
        walker.timing(epoch.timing, "timing")
        for index, item in enumerate(epoch.applicability):
            walker.condition(item, f"applicability/{index}")
    for group in (schedule.arms, schedule.cohorts, schedule.populations, schedule.dimensions):
        for dimension in group:
            walker.owner = ("DIMENSION", dimension.id, dimension.code)
            walker.condition(dimension.criteria, "criteria")
    for event in schedule.events:
        walker.event(event)
    for definition in schedule.conditional_definitions:
        walker.conditional(definition)
    for block in schedule.repeat_blocks:
        if block.cycle_plan is not None:
            walker.owner = ("REPEAT_BLOCK", block.id, block.code)
            walker.use("RECURRENCE", "CYCLE_PLAN", "cycle_plan", block.cycle_plan)
            walker.reference(block.cycle_plan.start_reference, "cycle_plan/start_reference")
            walker.termination(block.cycle_plan.termination, "cycle_plan/termination")
    for process in schedule.processes:
        walker.owner = ("PROCESS", process.id, process.code)
        walker.use("HOLD_RESUME_POLICY", process.hold_resume_policy.value,
                   "hold_resume_policy", process)
    for episode in schedule.confinement_episodes:
        walker.owner = ("CONFINEMENT_EPISODE", episode.id, episode.code)
        for index, item in enumerate(episode.conditions):
            walker.condition(item, f"conditions/{index}")
        walker.applicability(episode.applicability, "applicability")
    return walker.uses


def _unsupported(use: OperatorUse) -> bool:
    if use.operator == "UNSUPPORTED":
        return True
    capability = lookup(use.family, use.operator)
    if capability is None:
        return True
    return not capability.executable and not capability.legacy_gated


# ---------------------------------------------------------------------------
# Dependents
# ---------------------------------------------------------------------------


def _downstream_events(schedule: UniversalSchedule) -> dict[str, set[str]]:
    """source event code -> events whose dates are computed from it."""
    from .validator import _graph_references

    anchors = {anchor.code: anchor for anchor in schedule.anchors}
    edges: dict[str, set[str]] = defaultdict(set)
    for target in schedule.events:
        sources = {item.source_event_code for item in target.dependencies}
        for reference in _graph_references(target):
            if isinstance(reference, EventReference):
                sources.add(reference.event_code)
            else:
                anchor = anchors.get(getattr(reference, "code", ""))
                if anchor is not None and anchor.source_event_code:
                    sources.add(anchor.source_event_code)
        for activity in target.activities:
            for reference in _activity_event_references(activity):
                sources.add(reference)
        for source in sources:
            if source != target.code:
                edges[source].add(target.code)
    return edges


def _activity_event_references(activity: Activity) -> list[str]:
    from .validator import constraint_references, timing_references

    references = [*timing_references(activity.timing), *constraint_references(activity.constraints)]
    return [item.event_code for item in references if isinstance(item, EventReference)]


def _events_using_anchors(schedule: UniversalSchedule, anchor_codes: set[str]) -> set[str]:
    from .validator import event_temporal_references

    output: set[str] = set()
    for event in schedule.events:
        if any(getattr(item, "code", None) in anchor_codes for item in event_temporal_references(event)):
            output.add(event.code)
    return output


def affected_dependents(schedule: UniversalSchedule, use: OperatorUse) -> list[str]:
    """Event codes that cannot be executed because ``use`` cannot (transitive)."""
    edges = _downstream_events(schedule)
    seeds: set[str] = set()
    if use.owner_type == "EVENT":
        seeds.add(use.owner_code)
    elif use.owner_type == "ACTIVITY":
        seeds.add(use.owner_code.split("/", 1)[0])
    elif use.owner_type == "CONDITION":
        definition = next(
            (item for item in schedule.conditional_definitions if item.id == use.owner_id), None)
        if definition is not None:
            seeds |= {action.target_code for action in definition.actions}
            condition_anchors = {
                anchor.code for anchor in schedule.anchors
                if anchor.source_condition_code == definition.code
            }
            seeds |= _events_using_anchors(schedule, condition_anchors)
    elif use.owner_type == "PROCESS":
        process = next((item for item in schedule.processes if item.id == use.owner_id), None)
        if process is not None:
            seeds |= set(process.event_codes)
    elif use.owner_type == "REPEAT_BLOCK":
        block = next((item for item in schedule.repeat_blocks if item.id == use.owner_id), None)
        if block is not None:
            seeds |= set(block.event_codes)
    known = {event.code for event in schedule.events}
    seen = {code for code in seeds if code in known}
    queue = deque(seen)
    while queue:
        for target in edges.get(queue.popleft(), ()):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return sorted(seen)


def capability_issues(schedule: UniversalSchedule) -> list[ValidationIssue]:
    """UNSUPPORTED_OPERATOR for every non-executable operator (RL-01, RL-12)."""
    issues: list[ValidationIssue] = []
    dependents_flagged: dict[str, list[str]] = defaultdict(list)
    event_ids = {event.code: event.id for event in schedule.events}
    for use in operator_uses(schedule):
        if not _unsupported(use):
            continue
        dependents = affected_dependents(schedule, use)
        operator_label = use.source_operator or use.operator
        issue_ref = f"{use.owner_type}:{use.owner_code}:{use.path}"
        issues.append(ValidationIssue(
            issue_code=UNSUPPORTED_OPERATOR,
            severity=Severity.ERROR, blocking=True,
            entity_type=use.owner_type, entity_id=use.owner_id,
            message=(
                f"{use.owner_code} needs {operator_label!r} ({use.family.lower()}), which "
                "the scheduling engine cannot execute yet; the rule and everything "
                "dated from it stay blocked - acknowledging this does not make it executable"),
            details={
                "family": use.family,
                "operator": use.operator,
                "source_operator": use.source_operator,
                "source_text": use.source_text,
                "path": use.path,
                "rule_id": use.rule_id,
                "owner_type": use.owner_type,
                "owner_code": use.owner_code,
                "evidence_refs": list(use.evidence_refs),
                "affected_dependents": dependents,
                "registry_version": REGISTRY_VERSION,
                "acknowledgeable": False,
            },
        ))
        owner_event = use.owner_code.split("/", 1)[0] if use.owner_type in {"EVENT", "ACTIVITY"} else None
        for code in dependents:
            if code != owner_event:
                dependents_flagged[code].append(issue_ref)
    for code, sources in sorted(dependents_flagged.items()):
        issues.append(ValidationIssue(
            issue_code=DEPENDS_ON_UNSUPPORTED_OPERATOR,
            severity=Severity.ERROR, blocking=True,
            entity_type="EVENT", entity_id=event_ids.get(code),
            message=f"{code} is dated from a rule the scheduling engine cannot execute yet",
            details={
                "event_code": code,
                "unsupported_sources": sorted(set(sources)),
                "registry_version": REGISTRY_VERSION,
                "acknowledgeable": False,
            },
        ))
    return issues


def capability_report(schedule: UniversalSchedule) -> dict[str, object]:
    """Per-schedule summary: which operators are used and which are unsupported."""
    uses = operator_uses(schedule)
    unsupported = [use for use in uses if _unsupported(use)]
    counts: dict[str, int] = defaultdict(int)
    for use in uses:
        counts[f"{use.family}:{use.operator}"] += 1
    return {
        "registry_version": REGISTRY_VERSION,
        "operators_used": dict(sorted(counts.items())),
        "unsupported": [
            {
                "family": use.family, "operator": use.operator,
                "source_operator": use.source_operator, "owner_type": use.owner_type,
                "owner_code": use.owner_code, "path": use.path,
                "evidence_refs": list(use.evidence_refs),
                "affected_dependents": affected_dependents(schedule, use),
            }
            for use in unsupported
        ],
        "fully_executable": not unsupported,
    }
