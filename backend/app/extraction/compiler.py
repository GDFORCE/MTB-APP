"""Deterministic compiler: cited schedule facts -> canonical UniversalSchedule.

No model call happens here. The same ``DocumentFacts`` always compiles to the
same schedule (apart from generated ids), so a reviewer can trust that every
rule in a draft traces back to a cited protocol statement plus one of the fixed
mappings below (docs/schedule-engine-case-handling.md, Part 4 and Part 5
"Change 8"):

* Pre-treatment "within N days before registration" -> ``WITHIN ... BEFORE``
  that anchor. The compiler never re-points it at first dose.
* End of a treatment phase (a ``milestone`` fact) -> its own anchor, recorded
  by the site or derived as the protocol states; a stated expected window
  becomes a separate planned-window event that never dates the anchor itself.
* Phased follow-up -> ONE visit with a segmented ``recurrence_plan`` whose
  boundaries come from stated inclusive flags (left unset when unstated, so
  the validator blocks) - a boundary visit is neither duplicated nor dropped.
* Treatment cycles -> a ``CyclePlan`` on the treatment block (length, how each
  cycle start is dated, what ends the cycles) and ``CYCLE_DAY`` timing against
  that plan, so "each cycle starts 21 days after the previous one" is dated
  from the actual previous cycle by the engine.
* A rule timed from a PLANNED date (pre-op before the scheduled surgery) is
  marked ``PLANNED_OR_ACTUAL`` only when the protocol says so; every other
  anchor reference waits for the actual date.
* "MRI every second cycle" / "PK in Cycle 1 only" -> an occurrence-number
  condition on the activity when the cycles are stated unambiguously.
* Toxicity / condition rules -> PAUSE_BLOCK, RESUME_BLOCK, STOP_BLOCK,
  CANCEL_EVENT, ADD_EVENT, REPEAT_EVENT or MANUAL_REVIEW actions.
* TRIAL / SITE / COUNTRY scoped rules are recorded for a trial-level workflow
  and never become patient actions.
* Facts that disagree -> interpretation ``CONFLICTING`` with UNRESOLVED timing
  and a blocking issue carrying every reading. The compiler does not choose.
* Anything unstated (window, inclusive boundary, first-occurrence rule, Day 1
  anchor, counting convention, dependency mode) stays unstated: an
  ``UnresolvedTiming``, an ``UNCLEAR`` mode, or an origin marker the validator
  blocks on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.domain.schedule.condition import (
    ComparisonCondition,
    ConditionExpression,
    ExistsCondition,
    FieldOperand,
    LiteralOperand,
    MembershipCondition,
)
from app.domain.schedule.models import (
    CYCLE_BLOCK_DEPENDENCY_MODES,
    Activity,
    Anchor,
    ApplicabilityRule,
    CalendarRecurrence,
    CountBasis,
    HoldResumePolicy,
    HolidayPolicy,
    MissedOccurrencePolicy,
    ProcessDefinition,
    ProcessKind,
    ClaimEvidence,
    ConditionalAction,
    ConditionalDefinition,
    CycleDependencyMode,
    CyclePlan,
    DayNumbering,
    Dependency,
    DependencyMode,
    Event,
    Evidence,
    GenericDimension,
    InterpretationStatus,
    Qualifier,
    QualifierCategory,
    QualifierScope,
    RecurrencePlan,
    RecurrenceRule,
    RecurrenceSegment,
    RecurrenceTermination,
    RepeatBlock,
    RuleScope,
    ScheduleMetadata,
    ScheduleStatus,
    Severity,
    StudyDimension,
    UniversalSchedule,
    ValidationIssue,
)
from app.domain.schedule.timing import (
    AbsoluteTiming,
    ActivityReference,
    AnchorAcceptance,
    AnchorReference,
    ApproximateTiming,
    CycleDayTiming,
    CycleSelector,
    BasisSource,
    EventReference,
    NoLaterThanTiming,
    NonNegativeTemporalAmount,
    OffsetTiming,
    PositiveTemporalAmount,
    ProtocolDayTiming,
    ProtocolDefinedTiming,
    RangeTiming,
    TemporalAmount,
    TriggeredTiming,
    TriggerOffset,
    UnresolvedTiming,
    Window,
    WithinTiming,
)
from app.domain.schedule.temporal_basis import legacy_basis
from app.domain.schedule.validator import ScheduleValidator

from app.domain.schedule.feasibility import semantic_key
from app.domain.schedule.timing import AllConstraint, AnyConstraint, ConstraintTiming

from . import ledger as L
from .constraints import ConstraintBuilder, NotExact, unsupported_wording, weekday_wording
from .contracts import ExtractionResult
from .ordering import chronological
from .facts import (
    CONDITION_PATTERNS,
    NON_PATIENT_SCOPES,
    UNSUPPORTED_PATTERNS,
    Citation,
    Criterion,
    DimensionFact,
    DocumentFacts,
    Duration,
    ReferenceTo,
    RepeatEnd,
    RepeatSpec,
    ScheduleFact,
    WindowSpec,
)

COMPILER_VERSION = "uctsm-fact-compiler.v6"

#: Timing patterns that name a target date vs. ones that only bound it. Two
#: readings of one occurrence where one targets and the other bounds (or two
#: different bounds) are BOTH restrictions (AND); two different targets are
#: a contradiction (CONFLICTS_WITH). Never decided by which is convenient.
_TARGET_PATTERNS = {"OFFSET", "PROTOCOL_DAY", "CYCLE_DAY", "ABSOLUTE", "APPROXIMATE", "TRIGGERED",
                    "REPEAT", "PHASED_REPEAT", "ON_DEMAND"}
_BOUND_PATTERNS = {"WITHIN_BEFORE", "WITHIN_AFTER", "NO_LATER_THAN", "NO_EARLIER_THAN", "RANGE"}


def _strip_ids(value: Any) -> Any:
    """Meaning only: constraint ids and evidence links do not change a rule."""
    if isinstance(value, dict):
        return {key: _strip_ids(item) for key, item in value.items() if key not in {"id", "evidence_refs"}}
    if isinstance(value, list):
        return [_strip_ids(item) for item in value]
    return value

#: Marks issues the compiler raised so ScheduleValidator keeps them on every
#: re-validation until a reviewer resolves them (validator.py keeps OPEN
#: issues whose ``details.origin`` is EXTRACTION).
ORIGIN = "EXTRACTION"

_DAY_1_ALIASES = {"DAY_1", "D1", "STUDY_DAY_1", "DAY_ONE"}
_DAYS_PER = {"DAY": 1, "WEEK": 7}
_MONTHS_PER = {"MONTH": 1, "YEAR": 12}
_OPERATORS = {"EQUALS": "EQ", "NOT_EQUALS": "NE", "GT": "GT", "GTE": "GTE", "LT": "LT", "LTE": "LTE"}


def code_for(name: str) -> str:
    """A stable schedule code from a protocol name ("Cycle 1 Day 8" -> CYCLE_1_DAY_8)."""
    code = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    if not code:
        code = "UNNAMED"
    return code if code[0].isalpha() else f"E_{code}"


class _Unresolved(Exception):
    """A fact the compiler cannot turn into a deterministic rule without guessing."""


@dataclass
class _Compiled:
    """One fact's timing, before it is attached to an event."""

    timing: Any
    recurrence: RecurrenceRule | None = None
    recurrence_plan: RecurrencePlan | None = None
    calendar_recurrence: CalendarRecurrence | None = None
    dependencies: list[Dependency] = field(default_factory=list)
    dependency_mode: DependencyMode | None = None
    activation: str = "SCHEDULED"
    window: Window | None = None
    window_note: dict[str, Any] | None = None
    stop_condition: str | None = None  # UNTIL_CONDITION: a condition stops the series
    block_hint: str | None = None  # every-cycle visits belong to their cycle block
    label_suffix: str = ""

    def signature(self) -> str:
        """What two readings must agree on to be the same rule."""
        return json.dumps({
            "timing": _strip_ids(_dump(self.timing, drop=("reason", "source_reference"))),
            "recurrence": _dump(self.recurrence),
            "recurrence_plan": _dump(self.recurrence_plan),
            "calendar_recurrence": _dump(self.calendar_recurrence),
            "window": _dump(self.window),
            "dependency_mode": self.dependency_mode,
            "activation": self.activation,
            "stop": self.stop_condition,
        }, sort_keys=True, default=str)


#: How a stated treatment-block mode reads for a cycle plan (spec F1-F3).
_CYCLE_MODES = {
    "NOMINAL": CycleDependencyMode.NOMINAL,
    "ACTUAL_PREVIOUS_EVENT": CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START,
    "ACTUAL_PREVIOUS_CYCLE_START": CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START,
    "MANUAL": CycleDependencyMode.MANUAL,
    "UNCLEAR": CycleDependencyMode.UNCLEAR,
}
_EVENT_MODES = {"ACTUAL_PREVIOUS_CYCLE_START": "ACTUAL_PREVIOUS_EVENT"}


@dataclass
class _CycleGroup:
    """Every CYCLE_DAY statement of one treatment block, and the cycle plan they
    state together (length, dependency mode, what ends the cycles)."""

    block: str
    code: str = ""
    facts: list[ScheduleFact] = field(default_factory=list)
    plan: CyclePlan | None = None
    reason: str | None = None  # why no plan could be built
    stop_condition: str | None = None
    start_anchor: AnchorReference | None = None
    length: Duration | None = None
    mode: CycleDependencyMode = CycleDependencyMode.UNCLEAR


def _dump(value: Any, drop: tuple[str, ...] = ()) -> Any:
    if value is None:
        return None
    data = value.model_dump(mode="json")
    for key in drop:
        data.pop(key, None)
    return data


def _amount(duration: Duration) -> TemporalAmount:
    return TemporalAmount(value=duration.value, unit=duration.unit)


def _positive(duration: Duration, what: str) -> PositiveTemporalAmount:
    if duration.value == 0:
        raise _Unresolved(f"{what} is zero")
    return PositiveTemporalAmount(value=abs(duration.value), unit=duration.unit)


def _same_unit(first: Duration, second: Duration) -> tuple[Duration, Duration]:
    """Express two amounts in one unit when that is exact (weeks -> days,
    years -> months)."""
    if first.unit == second.unit:
        return first, second
    for table, unit in ((_DAYS_PER, "DAY"), (_MONTHS_PER, "MONTH")):
        if first.unit in table and second.unit in table:
            return (
                Duration(value=first.value * table[first.unit], unit=unit),
                Duration(value=second.value * table[second.unit], unit=unit),
            )
    raise _Unresolved(
        f"bounds use incompatible units ({first.unit} and {second.unit}); "
        "converting them would need an assumed month length")


def _canonical(duration: Duration) -> tuple[int, str]:
    """An amount in its exact base unit (days, or months), for comparison."""
    for table, unit in ((_DAYS_PER, "DAY"), (_MONTHS_PER, "MONTH")):
        if duration.unit in table:
            return duration.value * table[duration.unit], unit
    return duration.value, duration.unit


def _criterion(criterion: Criterion | None, fallback_field: str) -> ConditionExpression:
    if criterion is None:
        # Only "the condition has been recorded"; the PI's confirmation is what
        # actually drives the patient schedule (conditional.py).
        return ExistsCondition(field=fallback_field)
    return ComparisonCondition(
        operator=criterion.operator,
        left=FieldOperand(field=criterion.field),
        right=LiteralOperand(value=criterion.value),
    )


#: Labels that mean "wherever this block's first cycle starts", not a date of
#: their own. When the same block also names a concrete start ("beginning of
#: consolidation therapy") the two are one anchor, not a contradiction.
_GENERIC_CYCLE_STARTS = {"CYCLE_1_DAY_1", "C1D1", "CYCLE_1", "DAY_1_OF_CYCLE_1", "CYCLE_1_D1"}


def _cycle_count_written_as_number(fact: ScheduleFact) -> ScheduleFact:
    """"Paclitaxel q3 wks x2 cycles" with ``cycle.number = 2`` means TWO cycles,
    not "Cycle 2 only". Corrected only when the cited words literally state the
    count ("x2 cycles", "for 2 cycles") and nothing else says what ends them."""
    timing = fact.timing
    if (timing is None or timing.pattern != "CYCLE_DAY" or timing.cycle is None
            or timing.cycle.number is None or fact.repeat is not None
            or timing.cycle.length is None):
        return fact
    number = timing.cycle.number
    count_words = re.compile(rf"(?:\bx|\u00d7|\bfor)\s*{number}\s*cycles?\b", re.IGNORECASE)
    named_cycle = re.compile(rf"\b(?:cycle\s*{number}\b|c{number}d\d)", re.IGNORECASE)
    quotes = " ".join(citation.quote for citation in fact.evidence) + " " + fact.what
    if not count_words.search(quotes) or named_cycle.search(quotes):
        return fact
    cycle = timing.cycle.model_copy(update={"number": None})
    return fact.model_copy(update={
        "timing": timing.model_copy(update={"cycle": cycle}),
        "repeat": RepeatSpec(interval=timing.cycle.length, end=RepeatEnd(type="COUNT", count=number)),
    })


def _merge_phased(facts: list[ScheduleFact]) -> list[ScheduleFact]:
    """Spec E9 is ONE plan. A reader that wrote "every 3 months for 2 years",
    "every 6 months for years 3 and 4" and "annually" as three phased facts
    from the same reference is merged back into one plan - but only when the
    segments are contiguous (each starts exactly where the previous ends);
    anything else is left for the reviewer as it was stated."""
    groups: dict[tuple, list[ScheduleFact]] = {}
    for fact in facts:
        timing = fact.timing
        if (timing is not None and timing.pattern == "PHASED_REPEAT" and timing.reference is not None
                and timing.segments and fact.scope not in NON_PATIENT_SCOPES):
            key = (code_for(timing.reference.name), fact.schedule_option or "", tuple(fact.applies_to.arms))
            groups.setdefault(key, []).append(fact)
    replaced: dict[str, ScheduleFact | None] = {}
    for group in groups.values():
        if len(group) < 2:
            continue

        def first_start(fact: ScheduleFact) -> int:
            start = fact.timing.segments[0].start
            return _months_or_days(start) if start is not None else 0

        ordered = sorted(group, key=first_start)
        segments = [segment for fact in ordered for segment in fact.timing.segments]
        contiguous = all(
            earlier.timing.segments[-1].end is not None
            and later.timing.segments[0].start is not None
            and _months_or_days(earlier.timing.segments[-1].end)
            == _months_or_days(later.timing.segments[0].start)
            for earlier, later in zip(ordered, ordered[1:])
        )
        if not contiguous:
            continue
        head, tail = ordered[0], ordered[-1]
        merged = head.model_copy(update={
            "timing": head.timing.model_copy(update={"segments": segments}),
            "repeat": tail.repeat or head.repeat,
            "evidence": [citation for fact in ordered for citation in fact.evidence],
        })
        replaced[head.id] = merged
        for fact in ordered[1:]:
            replaced[fact.id] = None
            MERGED_INTO[fact.id] = head.id
    return [replaced.get(fact.id, fact) for fact in facts if replaced.get(fact.id, fact) is not None]


#: Filled by ``_merge_phased`` for the compiler instance using it (reset per
#: compile): fact id -> the phased fact it was merged into.
MERGED_INTO: dict[str, str] = {}


def _months_or_days(duration: Duration) -> int:
    """Comparable size for contiguity only: months stay months, days stay days."""
    if duration.unit in _MONTHS_PER:
        return duration.value * _MONTHS_PER[duration.unit] * 1000
    return duration.value * _DAYS_PER.get(duration.unit, 1)


class _Compiler:
    def __init__(self, document: DocumentFacts, facts: list[ScheduleFact], *, name: str):
        self.document = document
        self.facts = []
        seen: set[str] = set()
        for index, fact in enumerate(facts):
            if not fact.id or fact.id in seen:
                fact = fact.model_copy(update={"id": f"f{index + 1}" if f"f{index + 1}" not in seen
                                               else f"f{index + 1}-{len(seen)}"})
            seen.add(fact.id)
            self.facts.append(_cycle_count_written_as_number(fact))
        self.input_facts = list(self.facts)
        MERGED_INTO.clear()
        self.facts = _merge_phased(self.facts)
        self.merged_into = dict(MERGED_INTO)
        # Statement accountability (ledger.py): rules each fact compiled into,
        # relations between facts, and why a fact did not compile.
        self.rule_refs: dict[str, list[dict[str, str]]] = {}
        self.relations: list[dict[str, Any]] = []
        self.outcomes: dict[str, list[tuple[str, str, str | None]]] = {}
        self.all_facts = list(self.facts)
        self.non_schedule: list[ScheduleFact] = []
        # WP3: named treatment components -> processes (code -> name, kinds, facts).
        self.components: dict[str, dict[str, Any]] = {}
        self.processes: list[ProcessDefinition] = []
        self.constraints = ConstraintBuilder(
            resolve=lambda reference, fact: self.reference(reference, fact),
            cite=lambda fact: [str(item) for item in self.cite(fact.evidence, [fact])])
        self.name = name
        self.issues: list[ValidationIssue] = []
        self.extensions: list[dict[str, object]] = []
        self.evidence: dict[tuple, Evidence] = {}
        self.claims: list[ClaimEvidence] = []
        self.anchors: dict[str, Anchor] = {}
        self.anchor_names: dict[str, str] = {}
        self.events: dict[str, Event] = {}
        self.event_names: dict[str, str] = {}
        self.event_facts: dict[str, list[ScheduleFact]] = {}
        self.condition_names: dict[str, str] = {}
        self.condition_actions: dict[str, list[tuple[ConditionalAction, ScheduleFact]]] = {}
        self.condition_meta: dict[str, dict[str, Any]] = {}
        self.condition_anchor: dict[str, str] = {}
        self.block_members: dict[str, list[str]] = {}
        self.block_names: dict[str, str] = {}
        self.block_facts: dict[str, list[ScheduleFact]] = {}
        self.block_resume: dict[str, set[str]] = {}
        self.dimensions: dict[str, dict[str, StudyDimension]] = {
            "ARM": {}, "PART": {}, "COHORT": {}, "POPULATION": {}}
        self.referenced_anchor_facts: dict[str, list[ScheduleFact]] = {}
        self.cycle_groups: dict[str, _CycleGroup] = {}  # by block code
        self.cycle_plans: dict[str, _CycleGroup] = {}  # by cycle plan code
        # Blocks that exist only so a condition can pause or stop a visit series.
        self.condition_only_blocks: set[str] = set()

    # --- issues and evidence ------------------------------------------------

    def issue(self, code: str, message: str, *, blocking: bool = True,
              severity: Severity = Severity.ERROR, entity_type: str | None = None,
              entity_id: UUID | None = None, facts: list[ScheduleFact] | None = None,
              **details: object) -> None:
        payload: dict[str, object] = {**details, "origin": ORIGIN}
        if facts:
            payload["fact_ids"] = [fact.id for fact in facts]
            payload["citations"] = [
                citation.model_dump(mode="json") for fact in facts for citation in fact.evidence]
        if not blocking and severity == Severity.ERROR:
            severity = Severity.WARNING
        self.issues.append(ValidationIssue(
            issue_code=code, message=message, severity=severity, blocking=blocking,
            entity_type=entity_type, entity_id=entity_id, details=payload,
        ))

    def cite(self, citations: list[Citation], facts: list[ScheduleFact] | None = None) -> list[UUID]:
        ids: list[UUID] = []
        for citation in citations:
            key = (citation.page, citation.section, citation.table, citation.quote.strip())
            evidence = self.evidence.get(key)
            if evidence is None:
                context: dict[str, Any] = {"compiler": COMPILER_VERSION, "fact_ids": []}
                if citation.verification is not None:
                    context["verification"] = citation.verification
                evidence = Evidence(
                    evidence_type="TABLE" if citation.table else "PROTOCOL_TEXT",
                    page_number=citation.page, section_title=citation.section,
                    table_title=citation.table, source_text=citation.quote,
                    extraction_context=context,
                )
                self.evidence[key] = evidence
            fact_ids = evidence.extraction_context.setdefault("fact_ids", [])
            for fact in facts or []:
                if isinstance(fact_ids, list) and fact.id not in fact_ids:
                    fact_ids.append(fact.id)
            if evidence.id not in ids:
                ids.append(evidence.id)
        return ids

    def claim(self, entity_type: str, entity_id: UUID, claim_types: list[str],
              evidence_ids: list[UUID], interpretation: dict[str, object] | None = None) -> None:
        for claim_type in claim_types:
            for evidence_id in evidence_ids:
                self.claims.append(ClaimEvidence(
                    evidence_id=evidence_id, claim_type=claim_type,
                    claim_entity_type=entity_type, claim_entity_id=entity_id,
                    interpretation=interpretation or {},
                ))

    @staticmethod
    def citations(facts: list[ScheduleFact]) -> list[Citation]:
        return [citation for fact in facts for citation in fact.evidence]

    # --- statement accountability -------------------------------------------------

    def ref(self, facts: list[ScheduleFact], entity_type: str, code: str, entity_id: UUID | None = None,
            **extra: str) -> None:
        """Record that these facts compiled into (part of) a rule."""
        for fact in facts:
            if not fact.id:
                continue
            entry = {"entity_type": entity_type, "code": code, **extra}
            if entity_id is not None:
                entry["entity_id"] = str(entity_id)
            refs = self.rule_refs.setdefault(fact.id, [])
            if entry not in refs:
                refs.append(entry)

    def outcome(self, facts: list[ScheduleFact], disposition: str, reason: str,
                operator: str | None = None) -> None:
        for fact in facts:
            if fact.id:
                self.outcomes.setdefault(fact.id, []).append((disposition, reason, operator))

    def relate(self, kind: str, subject: ScheduleFact, other: ScheduleFact, reason: str) -> None:
        entry = {"type": kind, "subject": subject.id, "object": other.id, "reason": reason}
        if entry not in self.relations:
            self.relations.append(entry)

    def _separate_non_schedule(self) -> None:
        """Statements the reader classified NON_SCHEDULE never become visits -
        unless a schedule statement depends on them, in which case excluding
        them would conceal that dependency (design, gate sequence)."""
        candidates = [fact for fact in self.facts if fact.statement_type == "NON_SCHEDULE"]
        if not candidates:
            return
        names: dict[str, list[ScheduleFact]] = {}
        for fact in self.facts:
            if fact.statement_type == "NON_SCHEDULE":
                continue
            mentioned = list(fact.at_visits) + ([fact.depends_on] if fact.depends_on else [])
            for reference in (fact.timing.reference if fact.timing else None,
                              fact.repeat.start if fact.repeat else None):
                if reference is not None:
                    mentioned.append(reference.name)
            if fact.condition:
                mentioned += list(fact.condition.targets) + list(fact.condition.adds)
            if fact.repeat and fact.repeat.end:
                mentioned += [item for item in (fact.repeat.end.event, fact.repeat.end.condition) if item]
            for name in mentioned:
                names.setdefault(code_for(name), []).append(fact)
        for fact in candidates:
            users = names.get(code_for(fact.what), [])
            if users:
                self.issue(
                    "NON_SCHEDULE_STATEMENT_REFERENCED",
                    f"{fact.what!r} was classified as not a schedule statement, but "
                    f"{len(users)} schedule statement(s) depend on it; it was kept so that dependency "
                    "is not concealed. Confirm how it affects the schedule.",
                    facts=[fact] + users)
                continue
            self.facts.remove(fact)
            self.non_schedule.append(fact)
            self.outcome([fact], L.OUT_OF_SCOPE_NON_SCHEDULE,
                         f"not a schedule requirement ({fact.non_schedule_reason or 'reason not stated'}); "
                         "kept visible, never turned into a visit")
        if self.non_schedule:
            self.issue(
                "NON_SCHEDULE_STATEMENTS",
                f"{len(self.non_schedule)} statement(s) were classified as outside the visit schedule "
                "(e.g. reporting duties, data submission, dose calculation) and are listed for review; "
                "none became a visit.",
                blocking=False, severity=Severity.INFO, facts=self.non_schedule,
                statements=[{"fact_id": fact.id, "what": fact.what,
                             "reason": fact.non_schedule_reason} for fact in self.non_schedule])

    # --- names ----------------------------------------------------------------

    def _is_patient(self, fact: ScheduleFact) -> bool:
        return fact.scope not in NON_PATIENT_SCOPES

    def _is_condition_fact(self, fact: ScheduleFact) -> bool:
        pattern = fact.timing.pattern if fact.timing else None
        return fact.kind == "condition" or pattern in CONDITION_PATTERNS

    def _is_event_fact(self, fact: ScheduleFact) -> bool:
        if self._is_condition_fact(fact):
            return False
        if fact.kind == "visit":
            return True
        if fact.kind == "milestone":
            return fact.timing is not None
        if fact.kind == "activity":
            # An activity with a date of its own (not "2 hours after dosing")
            # is a scheduled occasion in its own right.
            return fact.timing is not None and not self._intra_visit(fact)
        return False

    @staticmethod
    def _intra_visit(fact: ScheduleFact) -> bool:
        reference = fact.timing.reference if fact.timing else None
        return bool(reference and reference.kind == "ACTIVITY")

    def _event_code(self, fact: ScheduleFact) -> str:
        base = code_for(fact.code or fact.what)
        return f"{base}_MILESTONE" if fact.kind == "milestone" else base

    def collect_names(self) -> None:
        stated = self.document.day_numbering.day_1_anchor
        if stated:
            self.anchor_names[code_for(stated)] = code_for(stated)
        for fact in self.facts:
            if fact.kind in {"anchor", "milestone"}:
                self.anchor_names[code_for(fact.code or fact.what)] = code_for(fact.code or fact.what)
                self.anchor_names[code_for(fact.what)] = code_for(fact.code or fact.what)
            if self._is_condition_fact(fact) and self._is_patient(fact):
                name = fact.condition.name if fact.condition else fact.what
                self.condition_names[code_for(name)] = code_for(name)
            if fact.anchor and fact.anchor.type == "CONDITION" and fact.anchor.condition:
                condition = code_for(fact.anchor.condition)
                self.condition_names[condition] = condition
                if fact.kind in {"anchor", "milestone"}:
                    self.condition_anchor.setdefault(condition, code_for(fact.code or fact.what))
            if fact.repeat and fact.repeat.end and fact.repeat.end.condition:
                self.condition_names[code_for(fact.repeat.end.condition)] = code_for(fact.repeat.end.condition)
            if (fact.timing and fact.timing.reference and fact.timing.reference.kind == "CONDITION"):
                name = fact.timing.reference.name
                self.condition_names[code_for(name)] = code_for(name)
            if self._is_event_fact(fact) and self._is_patient(fact):
                code = self._event_code(fact)
                self.event_names[code_for(fact.what)] = code
                self.event_names[code] = code
                if fact.code:
                    self.event_names[code_for(fact.code)] = code
        for fact in self.facts:
            if fact.component and self._is_patient(fact):
                entry = self.components.setdefault(code_for(fact.component), {
                    "name": fact.component, "kinds": set(), "facts": []})
                if fact.component_kind:
                    entry["kinds"].add(fact.component_kind)
                entry["facts"].append(fact)
        for block in self.document.blocks:
            self.block_names[code_for(block.name)] = code_for(block.name)
        for fact in self.facts:
            if fact.block:
                self.block_names[code_for(fact.block)] = code_for(fact.block)

    def _day_1_code(self) -> str | None:
        stated = self.document.day_numbering.day_1_anchor
        return code_for(stated) if stated else None

    def reference(self, reference: ReferenceTo, fact: ScheduleFact, *, allow_event: bool = True):
        """Resolve a named reference to an anchor, visit or condition anchor."""
        key = code_for(reference.name)
        kind = reference.kind
        if kind is None:
            if key in _DAY_1_ALIASES and self._day_1_code():
                kind, key = "ANCHOR", self._day_1_code()
            elif key in self.anchor_names:
                kind = "ANCHOR"
            elif key in self.event_names:
                kind = "EVENT"
            elif key in self.condition_names:
                kind = "CONDITION"
            else:
                kind = "ANCHOR"
        if kind == "ACTIVITY":
            raise _Unresolved(
                f"{reference.name!r} is another activity; only an activity inside a visit "
                "can be timed from it")
        if kind == "EVENT":
            if not allow_event:
                raise _Unresolved(f"{reference.name!r} is a visit, but this rule needs an anchor")
            code = self.event_names.get(key)
            if code is None:
                raise _Unresolved(f"the visit {reference.name!r} is not defined anywhere in the schedule")
            # P1-01: which date the visit is counted from is a protocol statement,
            # carried by the fact's dependency mode. Unstated -> an UNCONFIRMED
            # placeholder that cannot be approved until a reviewer states it.
            stated = _EVENT_MODES.get(fact.dependency_mode or "UNCLEAR", fact.dependency_mode or "UNCLEAR")
            known = stated in {"NOMINAL", "ACTUAL_PREVIOUS_EVENT", "MANUAL"}
            return EventReference(
                event_code=code, occurrence=reference.occurrence or "CURRENT",
                temporal_basis=legacy_basis(stated if known else None, is_dependency=True),
                basis_source=BasisSource.EXPLICIT if known else BasisSource.UNCONFIRMED)
        if kind == "CONDITION":
            condition = self.condition_names.get(key, key)
            self.condition_names.setdefault(key, condition)
            return AnchorReference(code=self.condition_anchor_code(condition, fact))
        code = self.anchor_names.get(key, key)
        if code not in self.anchor_names.values():
            # Named by the protocol but never defined by a fact of its own.
            self.referenced_anchor_facts.setdefault(code, []).append(fact)
            self.anchor_names[key] = code
        # Spec C6: a planned value is usable only when the protocol times this
        # rule from the planned / scheduled date; otherwise wait for the actual.
        return AnchorReference(code=code, accepts=AnchorAcceptance(reference.accepts or "ACTUAL"))

    def condition_anchor_code(self, condition: str, fact: ScheduleFact | None) -> str:
        existing = self.condition_anchor.get(condition)
        if existing:
            return existing
        code = f"{condition}_DATE"
        self.condition_anchor[condition] = code
        if fact is not None:
            self.referenced_anchor_facts.setdefault(code, []).append(fact)
        return code

    # --- timing -------------------------------------------------------------

    def compile_timing(self, fact: ScheduleFact) -> list[_Compiled]:
        timing = fact.timing
        if timing is None:
            raise _Unresolved("the protocol statement gives no timing for this visit")
        pattern = timing.pattern
        if pattern == "WEEKDAY_REPEAT" or (pattern == "REPEAT" and weekday_wording(fact)):
            return [self._weekday_series(fact)]
        if pattern in UNSUPPORTED_PATTERNS:
            # Clearly stated, no executable operator: an UNSUPPORTED node the
            # validator blocks together with every rule dated from it.
            operator = UNSUPPORTED_PATTERNS[pattern]
            self.outcome([fact], L.UNSUPPORTED, f"no executable operator for {operator} yet", operator=operator)
            return [_Compiled(timing=ConstraintTiming(constraint=self.constraints.unsupported(fact, operator)))]
        if pattern == "NO_EARLIER_THAN":
            try:
                [node] = self.constraints.nodes(fact)
            except NotExact as error:
                raise _Unresolved(str(error)) from error
            return [_Compiled(timing=ConstraintTiming(constraint=node))]
        if fact.repeat is not None and pattern not in {
                "REPEAT", "OFFSET", "PROTOCOL_DAY", "CYCLE_DAY", "PHASED_REPEAT"}:
            raise _Unresolved(f"a repeat on a {pattern} rule has no deterministic first occurrence")

        def need(value, what):
            if value is None:
                raise _Unresolved(f"{pattern} timing is missing its {what}")
            return value

        if pattern == "UNRESOLVED":
            raise _Unresolved(timing.reason or "the protocol statement could not be mapped to a timing rule")
        if pattern == "ON_DEMAND":
            return [_Compiled(
                timing=ProtocolDefinedTiming(handler="ON_DEMAND", requires_review=False),
                activation="ON_DEMAND")]
        if pattern == "ABSOLUTE":
            return [_Compiled(timing=AbsoluteTiming(value=need(timing.value, "date")))]
        if pattern == "PHASED_REPEAT":
            return [self._phased(fact)]

        reference = timing.reference
        if pattern == "PROTOCOL_DAY":
            day = need(timing.day, "day number")
            convention = self.document.day_numbering.counting_convention
            if reference is None and self._day_1_code() is None:
                raise _Unresolved(f"Day {day} is stated but the protocol does not state which date is Day 1")
            ref = (self.reference(reference, fact) if reference is not None
                   else AnchorReference(code=self._day_1_code()))
            if convention is None:
                raise _Unresolved(
                    f"Day {day} cannot be dated: the protocol does not state whether "
                    "Day 1 or Day 0 is the anchor day")
            if convention == "CLINICAL_DAY":
                if day == 0:
                    raise _Unresolved("the protocol counts from Day 1 but this rule names Day 0")
                elapsed = day - 1 if day > 0 else day
                expression = (ProtocolDayTiming(reference=ref, day=day)
                              if isinstance(ref, AnchorReference)
                              else OffsetTiming(reference=ref, offset=TemporalAmount(value=elapsed, unit="DAY")))
            else:
                elapsed = day
                expression = OffsetTiming(reference=ref, offset=TemporalAmount(value=day, unit="DAY"))
            compiled = _Compiled(timing=expression)
            if fact.repeat is not None:
                self._attach_repeat(compiled, fact, ref, Duration(value=elapsed, unit="DAY"), first_is_stated=True)
            return [self._with_dependency(compiled, ref, fact)]

        if pattern == "CYCLE_DAY":
            return [self._cycle_day(fact)]

        if pattern == "REPEAT":
            repeat = need(fact.repeat, "repeat rule")
            start_ref = self.reference(need(repeat.start or reference, "start reference"), fact)
            start_offset = repeat.start_offset or Duration(value=0, unit=repeat.interval.unit)
            compiled = _Compiled(timing=OffsetTiming(reference=start_ref, offset=_amount(start_offset)))
            self._attach_repeat(compiled, fact, start_ref, start_offset, first_is_stated=False)
            return [self._with_dependency(compiled, start_ref, fact)]

        ref = self.reference(need(reference, "reference"), fact)
        if pattern == "OFFSET":
            offset = need(timing.offset, "offset")
            compiled = _Compiled(timing=OffsetTiming(reference=ref, offset=_amount(offset)))
            if fact.repeat is not None:
                self._attach_repeat(compiled, fact, ref, offset, first_is_stated=True)
        elif pattern == "RANGE":
            start, end = _same_unit(need(timing.start, "start"), need(timing.end, "end"))
            if start.value > end.value:
                raise _Unresolved("range start is after range end")
            compiled = _Compiled(timing=RangeTiming(reference=ref, start=_amount(start), end=_amount(end)))
        elif pattern in {"WITHIN_BEFORE", "WITHIN_AFTER"}:
            duration = timing.duration or timing.offset
            compiled = _Compiled(timing=WithinTiming(
                reference=ref, duration=_positive(need(duration, "duration"), "within duration"),
                direction="BEFORE" if pattern == "WITHIN_BEFORE" else "AFTER"))
        elif pattern == "NO_LATER_THAN":
            duration = need(timing.duration or timing.offset, "duration")
            compiled = _Compiled(timing=NoLaterThanTiming(reference=ref, duration=_amount(duration)))
        elif pattern == "TRIGGERED":
            offset = need(timing.offset, "offset after the trigger")
            compiled = _Compiled(timing=TriggeredTiming(
                trigger=ref, timing_after_trigger=TriggerOffset(offset=_amount(offset))))
        elif pattern == "APPROXIMATE":
            # Kept as stated; the validator refuses to date it (spec A11).
            compiled = _Compiled(timing=ApproximateTiming(
                reference=ref, offset=_amount(need(timing.offset, "offset"))))
        else:
            raise _Unresolved(f"{pattern} is a condition rule, not a visit timing")
        return [self._with_dependency(compiled, ref, fact)]

    def _weekday_series(self, fact: ScheduleFact) -> _Compiled:
        """WP3 WEEKDAY_GRID.v1: "Monday to Friday, 30 fractions". Weekdays,
        count basis, holiday and missed-session policies only as stated."""
        timing = fact.timing
        if not timing.weekdays:
            raise _Unresolved("the protocol describes a weekday-only schedule but the extract does not "
                              "name the weekdays; a reviewer must state them")
        repeat = fact.repeat
        start = (repeat.start if repeat and repeat.start else None) or timing.reference
        if start is None:
            raise _Unresolved("the weekday series does not say what it starts from")
        start_ref = self.reference(start, fact)
        holder = _Compiled(timing=None)
        termination = self._termination(repeat.end if repeat else None, fact, holder)
        if fact.count_basis is not None and termination.type != "COUNT":
            raise _Unresolved("a count basis is stated for a series that is not ended by a count")
        offset = repeat.start_offset if repeat and repeat.start_offset and repeat.start_offset.value else None
        rule = CalendarRecurrence(
            start_reference=start_ref, start_offset=_amount(offset) if offset else None,
            weekdays=list(dict.fromkeys(timing.weekdays)), interval_weeks=timing.interval_weeks or 1,
            termination=termination,
            count_basis=CountBasis(fact.count_basis) if fact.count_basis else None,
            holiday_policy=HolidayPolicy(fact.holiday_policy) if fact.holiday_policy else HolidayPolicy.NONE_STATED,
            missed_policy=(MissedOccurrencePolicy(fact.missed_policy) if fact.missed_policy
                           else MissedOccurrencePolicy.NONE_STATED))
        compiled = _Compiled(
            timing=OffsetTiming(reference=start_ref, offset=_amount(offset) if offset
                                else TemporalAmount(value=0, unit="DAY")),
            calendar_recurrence=rule)
        compiled.stop_condition = holder.stop_condition
        return compiled

    def _with_dependency(self, compiled: _Compiled, ref: Any, fact: ScheduleFact) -> _Compiled:
        sources: list[tuple[str, str]] = []
        if isinstance(ref, EventReference):
            sources.append((ref.event_code, "TEMPORAL"))
        if fact.depends_on:
            code = self.event_names.get(code_for(fact.depends_on))
            if code is None:
                raise _Unresolved(f"depends on {fact.depends_on!r}, which is not defined in the schedule")
            if code not in {source for source, _ in sources}:
                sources.append((code, "SEQUENCE"))
        if sources:
            compiled.dependencies = [
                Dependency(source_event_code=code, dependency_type=kind) for code, kind in sources]
            # Spec section G: whether this counts from the planned or the actual
            # previous visit is a protocol statement. Unstated -> UNCLEAR, which
            # blocks approval.
            mode = fact.dependency_mode or "UNCLEAR"
            compiled.dependency_mode = DependencyMode(_EVENT_MODES.get(mode, mode))
        return compiled

    def _termination(self, end: RepeatEnd | None, fact: ScheduleFact,
                     compiled: _Compiled) -> RecurrenceTermination:
        if end is None:
            raise _Unresolved("the repeat does not state when it stops")
        if end.type == "COUNT":
            if end.count is None:
                raise _Unresolved("a counted repeat is missing its count")
            return RecurrenceTermination(type="COUNT", count=end.count)
        if end.type == "UNTIL_DATE":
            if end.until_date is None:
                raise _Unresolved("a repeat until a date is missing the date")
            return RecurrenceTermination(type="DATE", date=end.until_date)
        if end.type == "UNTIL_EVENT":
            code = self.event_names.get(code_for(end.event or ""))
            if code is None:
                raise _Unresolved(f"the repeat stops at {end.event!r}, which is not a defined visit")
            return RecurrenceTermination(type="EVENT", event_code=code)
        if end.type == "UNTIL_CONDITION":
            if not end.condition:
                raise _Unresolved("a repeat until a condition does not name the condition")
            # Stopped by the condition's confirmed date through a repeat block
            # (spec E3), so earlier occurrences stay and later ones cancel.
            compiled.stop_condition = self.condition_names.setdefault(
                code_for(end.condition), code_for(end.condition))
            return RecurrenceTermination(type="HORIZON")
        if end.type == "FOR_DURATION":
            if end.duration is None:
                raise _Unresolved("a repeat for a duration is missing the duration")
            if end.inclusive is None:
                raise _Unresolved(
                    "the protocol does not say whether the visit on the final boundary "
                    "is included; a reviewer must decide")
            reference = end.reference or (fact.repeat.start if fact.repeat else None) or (
                fact.timing.reference if fact.timing else None)
            if reference is None:
                raise _Unresolved("the repeat duration does not say what it counts from")
            ref = self.reference(reference, fact, allow_event=False)
            return RecurrenceTermination(
                type="RELATIVE", end_reference=ref, end_offset=_amount(end.duration),
                inclusive=end.inclusive)
        return RecurrenceTermination(type="HORIZON")  # OPEN_ENDED, stated as such

    def _attach_repeat(self, compiled: _Compiled, fact: ScheduleFact, start_ref: Any,
                       start_offset: Duration, *, first_is_stated: bool) -> None:
        repeat = fact.repeat
        assert repeat is not None
        if not isinstance(start_ref, AnchorReference | EventReference):
            raise _Unresolved("a repeat must count from an anchor or a visit")
        includes_start = repeat.includes_start
        if includes_start is None and first_is_stated:
            # The fact's own timing IS the first occurrence ("Day 1, then every
            # 21 days"), so the first occurrence is stated, not assumed.
            includes_start = True
        if includes_start is None:
            raise _Unresolved(
                "the protocol does not say whether the first repeat falls on the start "
                "date or one interval after it")
        compiled.recurrence = RecurrenceRule(
            interval=_positive(repeat.interval, "repeat interval"),
            start_reference=start_ref,
            start_offset=_amount(start_offset) if start_offset.value else None,
            termination=self._termination(repeat.end, fact, compiled),
            include_start=includes_start,
        )

    def _cycle_day(self, fact: ScheduleFact) -> _Compiled:
        timing = fact.timing
        assert timing is not None
        cycle = timing.cycle
        if cycle is None or timing.day is None:
            raise _Unresolved("a cycle-day rule is missing its cycle or day")
        if timing.day <= 0:
            raise _Unresolved("cycle days are numbered from Day 1")
        group = self.cycle_groups.get(self._cycle_block(fact))
        number = cycle.number
        if group is not None and group.plan is not None:
            # Spec F1-F5: dated by the engine from THIS cycle's start, which the
            # plan's dependency mode puts on the nominal grid or after the
            # actual previous cycle.
            compiled = _Compiled(timing=CycleDayTiming(
                cycle_reference=group.code,
                cycle=(CycleSelector(type="NUMBER", number=number) if number is not None
                       else CycleSelector(type="CURRENT")),
                day=timing.day))
            compiled.block_hint = group.block
            if number is None:
                compiled.stop_condition = group.stop_condition
            return compiled
        if number is None:
            raise _Unresolved((group.reason if group else None)
                              or "the treatment cycles could not be described")
        anchor = (group.start_anchor if group and group.start_anchor else
                  self.reference(ReferenceTo(name=cycle.start_anchor), fact, allow_event=False))
        if number == 1:
            # Cycle 1 starts on its anchor whichever way later cycles are dated.
            return _Compiled(timing=OffsetTiming(
                reference=anchor, offset=TemporalAmount(value=timing.day - 1, unit="DAY")))
        length = group.length if group else cycle.length
        mode = group.mode if group else CycleDependencyMode.UNCLEAR
        if length is None:
            # When the block's plan could not be built, that is the real reason
            # (e.g. two different Cycle 1 starts), not a missing length.
            raise _Unresolved((group.reason if group and group.reason else None)
                              or "the cycle length is not stated")
        if mode != CycleDependencyMode.NOMINAL:
            # Without a cycle plan only the nominal grid can be written as a
            # fixed offset; Cycle N after delayed cycles would be misdated.
            raise _Unresolved(
                f"Cycle {number} can only be dated once the protocol states whether cycles "
                "follow the nominal grid or the actual previous cycle"
                + (f" ({group.reason})" if group and group.reason else ""))
        if length.unit not in _DAYS_PER:
            raise _Unresolved("a cycle length in months cannot be combined with a day number exactly")
        offset = (number - 1) * length.value * _DAYS_PER[length.unit] + timing.day - 1
        return _Compiled(timing=OffsetTiming(reference=anchor, offset=TemporalAmount(value=offset, unit="DAY")))

    # --- cycle plans ----------------------------------------------------------------

    @staticmethod
    def _cycle_block(fact: ScheduleFact) -> str:
        cycle = fact.timing.cycle if fact.timing else None
        if fact.block:
            return code_for(fact.block)
        return f"{code_for(cycle.start_anchor)}_CYCLES" if cycle else "CYCLES"

    def collect_cycle_plans(self) -> None:
        """Spec F1-F5: one cycle plan per treatment block that has CYCLE_DAY rules.

        Length, what ends the cycles and how each cycle start is dated are
        properties of the block, so one statement of each suffices; two that
        disagree leave the plan unbuilt (and its visits unresolved). An unstated
        dependency mode stays UNCLEAR, which the validator blocks on.
        """
        stated_blocks = {code_for(block.name): block for block in self.document.blocks}
        for fact in self.facts:
            if (fact.timing is None or fact.timing.pattern != "CYCLE_DAY" or fact.timing.cycle is None
                    or not self._is_patient(fact) or not self._is_event_fact(fact)):
                continue
            block = self._cycle_block(fact)
            self.cycle_groups.setdefault(block, _CycleGroup(block=block)).facts.append(fact)
        single = len(self.cycle_groups) == 1
        for block, group in self.cycle_groups.items():
            # CYCLE -> anchors CYCLE_1_START, CYCLE_2_START, ... (spec F2).
            group.code = "CYCLE" if single else block
            self.cycle_plans[group.code] = group
            try:
                self._build_cycle_plan(group, stated_blocks.get(block))
            except _Unresolved as error:
                group.reason = str(error)

    def _build_cycle_plan(self, group: _CycleGroup, block_fact) -> None:
        facts = group.facts
        first = facts[0]
        anchors = {code_for(fact.timing.cycle.start_anchor) for fact in facts}
        concrete = anchors - _GENERIC_CYCLE_STARTS
        if concrete and len(concrete) < len(anchors):
            anchors = concrete
            first = next(fact for fact in facts if code_for(fact.timing.cycle.start_anchor) in concrete)
        if len(anchors) > 1:
            raise _Unresolved(f"the cycle rules name different Cycle 1 starts ({', '.join(sorted(anchors))})")
        group.start_anchor = self.reference(
            ReferenceTo(name=first.timing.cycle.start_anchor), first, allow_event=False)

        lengths: dict[tuple[int, str], Duration] = {}
        for fact in facts:
            length = fact.timing.cycle.length
            if length is not None:
                # "3 weeks" and "21 days" are one statement, not two.
                lengths.setdefault(_canonical(length), length)
        if len(lengths) > 1:
            self.issue("CONFLICTING_SOURCE",
                       f"Treatment block {group.block}: the protocol states different cycle lengths; "
                       "none was chosen.", facts=facts,
                       lengths=[f"{value} {unit}" for value, unit in sorted(lengths)])
            raise _Unresolved("the protocol states different cycle lengths")
        group.length = next(iter(lengths.values()), None)

        stated_modes = {fact.dependency_mode for fact in facts if fact.dependency_mode}
        if block_fact is not None and block_fact.dependency_mode:
            mode_name = block_fact.dependency_mode
        elif len({_CYCLE_MODES[mode] for mode in stated_modes}) == 1:
            mode_name = next(iter(stated_modes))
        else:
            mode_name = "UNCLEAR"  # unstated or contradictory: never picked (spec F3)
        group.mode = _CYCLE_MODES[mode_name]

        if group.length is None:
            raise _Unresolved("the cycle length is not stated")
        ends = {}
        for fact in facts:
            end = fact.repeat.end if fact.repeat else None
            if end is not None:
                ends[json.dumps(end.model_dump(mode="json"), sort_keys=True)] = (end, fact)
        if not ends:
            raise _Unresolved("the protocol does not state how many cycles there are or what ends them")
        if len(ends) > 1:
            self.issue("CONFLICTING_SOURCE",
                       f"Treatment block {group.block}: the protocol states different numbers of cycles "
                       "or different ends; none was chosen.", facts=facts)
            raise _Unresolved("the protocol states different ends for the treatment cycles")
        end, end_fact = next(iter(ends.values()))
        holder = _Compiled(timing=None)
        termination = self._termination(end, end_fact, holder)
        group.stop_condition = holder.stop_condition

        # The Day-1 visit of every cycle is where an actual cycle start is read
        # from (CyclePlan.start_event_code); not guessed when there is none.
        day_1 = sorted({self._event_code(fact) for fact in facts
                        if fact.timing.day == 1 and fact.timing.cycle.number is None})
        group.plan = CyclePlan(
            code=group.code, start_reference=group.start_anchor,
            length=_positive(group.length, "cycle length"), dependency_mode=group.mode,
            start_event_code=day_1[0] if len(day_1) == 1 else None,
            termination=termination)

    def _phased(self, fact: ScheduleFact) -> _Compiled:
        """Spec E9: "every 3 months to year 2, then every 6 months to year 5,
        then yearly" is ONE visit with a segmented recurrence plan."""
        timing = fact.timing
        assert timing is not None
        if timing.reference is None or not timing.segments:
            raise _Unresolved("a phased follow-up plan needs its reference and its segments")
        anchor = self.reference(timing.reference, fact, allow_event=False)
        segments = timing.segments
        last = len(segments) - 1
        # Which segment owns each boundary visit. Only what is stated: the
        # ending segment's end_inclusive, or - equivalently - the next segment
        # saying its first visit falls on the boundary. Two statements that
        # both claim (or both refuse) the boundary are a contradiction.
        end_inclusive: list[bool | None] = [segment.end_inclusive for segment in segments]
        for index, segment in enumerate(segments):
            if segment.end is not None and segment.count is not None:
                raise _Unresolved(f"segment {index + 1} states both an end and a number of visits")
            if segment.end is None and segment.count is None and index < last:
                raise _Unresolved(f"segment {index + 1} has no end but is followed by another segment")
            if segment.end is None and segment.end_inclusive is not None:
                raise _Unresolved(f"segment {index + 1} states an inclusive end but no end")
            if index == 0:
                continue
            previous = segments[index - 1]
            if previous.end is not None and segment.start is not None:
                start, end = _same_unit(segment.start, previous.end)
                if start.value != end.value:
                    raise _Unresolved(
                        f"segment {index + 1} does not start where segment {index} ends")
            if previous.end is not None and segment.includes_start is not None:
                if end_inclusive[index - 1] is None:
                    end_inclusive[index - 1] = not segment.includes_start
                elif end_inclusive[index - 1] == segment.includes_start:
                    raise _Unresolved(
                        f"segments {index} and {index + 1} disagree about who owns the visit on "
                        "their shared boundary")

        # What ends the whole plan (and a final segment with no end of its own).
        # Never defaulted to open-ended: unstated stays None and blocks.
        holder = _Compiled(timing=None)
        stated_end = fact.repeat.end if fact.repeat else None
        termination = self._termination(stated_end, fact, holder) if stated_end is not None else None
        first_start = segments[0].start
        start_offset = _amount(first_start) if first_start is not None and first_start.value else None
        plan = RecurrencePlan(
            start_reference=anchor, start_offset=start_offset,
            # Whether a visit falls ON the plan start is never assumed (E9).
            include_start=segments[0].includes_start,
            segments=[RecurrenceSegment(
                interval=_positive(segment.interval, f"segment {index + 1} interval"),
                end_offset=_amount(segment.end) if segment.end is not None else None,
                end_inclusive=end_inclusive[index] if segment.end is not None else None,
                count=segment.count) for index, segment in enumerate(segments)],
            termination=termination)
        compiled = _Compiled(
            timing=OffsetTiming(reference=anchor, offset=start_offset or TemporalAmount(
                value=0, unit=segments[0].interval.unit)),
            recurrence_plan=plan)
        compiled.stop_condition = holder.stop_condition
        return compiled

    def apply_window(self, compiled: _Compiled, window: WindowSpec | None, event_name: str) -> None:
        if window is None or (window.before is None and window.after is None):
            return  # spec B4: no window stated stays empty
        note = window.model_dump(mode="json")
        if window.before is None or window.after is None:
            compiled.window_note = {**note, "reason": "only one side of the window is stated"}
            return
        single_target = isinstance(compiled.timing, OffsetTiming | AbsoluteTiming | ProtocolDayTiming) or (
            isinstance(compiled.timing, TriggeredTiming)
            and isinstance(compiled.timing.timing_after_trigger, TriggerOffset))
        if compiled.recurrence is None and not single_target:
            # A range or "within" already IS the allowed span; a window on top
            # has no stated centre to sit around.
            compiled.window_note = {**note, "reason": f"a window cannot be applied to {compiled.timing.type} timing"}
            return
        try:
            before, after = _same_unit(window.before, window.after)
        except _Unresolved as error:
            compiled.window_note = {**note, "reason": str(error)}
            return
        # The window is its own rule on the event, so every occurrence of a
        # repeat re-applies it from its own target date.
        compiled.window = Window(
            before=NonNegativeTemporalAmount(value=abs(before.value), unit=before.unit),
            after=NonNegativeTemporalAmount(value=abs(after.value), unit=after.unit))

    # --- dimensions -----------------------------------------------------------

    @staticmethod
    def _dimension(kind: str, code: str, name: str, description: str | None = None) -> StudyDimension:
        # Parts have no dedicated list on the schedule; they are a generic
        # dimension the enrolment form nests cohorts under (spec H4).
        if kind == "PART":
            return GenericDimension(dimension_type="PART", code=code, protocol_label=name,
                                    display_name=name, description=description)
        return StudyDimension(code=code, protocol_label=name, display_name=name, description=description)

    def build_dimensions(self) -> None:
        stated: list[tuple[str, DimensionFact]] = []
        for kind, items in (("ARM", self.document.arms), ("PART", self.document.parts),
                            ("COHORT", self.document.cohorts),
                            ("POPULATION", self.document.populations)):
            for item in items:
                code = code_for(item.code or item.name)
                if code not in self.dimensions[kind]:
                    self.dimensions[kind][code] = self._dimension(kind, code, item.name, item.description)
                    stated.append((kind, item))
        for kind, item in stated:
            if item.parent:
                self._nest(kind, item)

    def _nest(self, kind: str, item: DimensionFact) -> None:
        """Spec H4: "Part A -> Cohort 1 or 2". The parent is recorded only when
        the protocol names one that resolves to exactly one defined group."""
        code = code_for(item.code or item.name)
        wanted = code_for(item.parent)
        kinds = [item.parent_type] if item.parent_type else list(self.dimensions)
        matches = [
            (parent_kind, dimension.code)
            for parent_kind in kinds
            for dimension in self.dimensions.get(parent_kind, {}).values()
            if (dimension.code == wanted or code_for(dimension.protocol_label) == wanted)
            and not (parent_kind == kind and dimension.code == code)
        ]
        if len(matches) != 1:
            evidence = [citation for citation in item.evidence]
            self.issue(
                "UNRESOLVED_REFERENCE",
                f"{kind.title()} {item.name!r} is said to belong to {item.parent!r}, which "
                + ("is not a defined group" if not matches else "matches more than one group")
                + "; enrolment cannot offer it under the right parent until a reviewer records it.",
                dimension=kind, dimension_code=code, parent=item.parent,
                candidates=[f"{parent_kind}:{parent}" for parent_kind, parent in matches],
                citations=[citation.model_dump(mode="json") for citation in evidence])
            return
        parent_kind, parent_code = matches[0]
        self.dimensions[kind][code] = self.dimensions[kind][code].model_copy(update={
            "parent_dimension_type": parent_kind, "parent_code": parent_code})

    def applicability(self, fact: ScheduleFact) -> list[ApplicabilityRule]:
        rules: list[ApplicabilityRule] = []
        for kind, names in (("ARM", fact.applies_to.arms), ("PART", fact.applies_to.parts),
                            ("COHORT", fact.applies_to.cohorts),
                            ("POPULATION", fact.applies_to.populations)):
            if not names:
                continue
            codes = []
            for name in names:
                code = code_for(name)
                matches = [dim for dim_code, dim in self.dimensions[kind].items()
                           if dim_code == code or code_for(dim.protocol_label) == code]
                if matches:
                    codes.append(matches[0].code)
                else:
                    # Named by the protocol for this rule; recorded as a group
                    # the schedule offers rather than silently dropped.
                    self.dimensions[kind][code] = self._dimension(kind, code, name)
                    codes.append(code)
            rules.append(ApplicabilityRule(dimension=kind, operator="IN", values=codes))
        for criterion in fact.applies_to.attributes:
            if code_for(criterion.field) == "AGE":
                rules.append(ApplicabilityRule(
                    dimension="AGE", operator=_OPERATORS[criterion.operator],
                    values=[str(criterion.value)]))
            else:
                rules.append(ApplicabilityRule(
                    dimension="PATIENT_ATTRIBUTE", operator=_OPERATORS[criterion.operator],
                    values=[str(criterion.value).lower() if isinstance(criterion.value, bool)
                            else str(criterion.value)],
                    field=criterion.field))
        return rules

    # --- events ---------------------------------------------------------------

    def _groups(self, facts: list[ScheduleFact]) -> list[list[ScheduleFact]]:
        """Facts describing the same visit, including stated alternatives."""
        parent: dict[str, str] = {fact.id: fact.id for fact in facts}

        def find(item: str) -> str:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(first: str, second: str) -> None:
            if first in parent and second in parent:
                parent[find(first)] = find(second)

        by_code: dict[str, str] = {}
        by_conflict: dict[str, str] = {}
        for fact in facts:
            code = self._event_code(fact)
            if code in by_code:
                union(fact.id, by_code[code])
            by_code.setdefault(code, fact.id)
            if fact.conflict_group:
                if fact.conflict_group in by_conflict:
                    union(fact.id, by_conflict[fact.conflict_group])
                by_conflict.setdefault(fact.conflict_group, fact.id)
            for other in fact.conflicts_with:
                union(fact.id, other)
        groups: dict[str, list[ScheduleFact]] = {}
        for fact in facts:
            groups.setdefault(find(fact.id), []).append(fact)
        return list(groups.values())

    def build_events(self) -> None:
        event_facts = [fact for fact in self.facts if self._is_event_fact(fact)]
        for fact in event_facts:
            if not self._is_patient(fact):
                self.non_patient(fact)
        patient_facts = [fact for fact in event_facts if self._is_patient(fact)]
        for group in self._groups(patient_facts):
            self._build_group(group)

    @staticmethod
    def _recurring(fact: ScheduleFact) -> bool:
        pattern = fact.timing.pattern if fact.timing else None
        return fact.repeat is not None or pattern in {"REPEAT", "PHASED_REPEAT"} or (
            pattern == "CYCLE_DAY" and fact.timing.cycle is not None and fact.timing.cycle.number is None)

    def _pair_relation(self, first: ScheduleFact, second: ScheduleFact) -> tuple[str, str]:
        """AND or CONFLICTS_WITH for two readings of one occurrence that
        compile to different rules (IR-04). Conservative: anything that is not
        clearly two restrictions on the same single occurrence conflicts."""
        one, two = first.timing.pattern, second.timing.pattern
        if one in UNSUPPORTED_PATTERNS or two in UNSUPPORTED_PATTERNS:
            return "AND", "a calendar rule on the same occurrences: both apply"
        if self._recurring(first) or self._recurring(second):
            return "CONFLICTS_WITH", ("the statements repeat differently (or only one repeats); state whether "
                                      "they are different occurrences")
        if one in _TARGET_PATTERNS and two in _TARGET_PATTERNS:
            return "CONFLICTS_WITH", "two different target dates for one occurrence"
        if one in _BOUND_PATTERNS and two in _BOUND_PATTERNS and one == two:
            ref_one = code_for(first.timing.reference.name) if first.timing.reference else None
            ref_two = code_for(second.timing.reference.name) if second.timing.reference else None
            if ref_one == ref_two:
                return "CONFLICTS_WITH", "the same kind of bound from the same reference, stated differently"
        if {one, two} <= (_TARGET_PATTERNS | _BOUND_PATTERNS):
            return "AND", "compatible restrictions on one occurrence: both apply"
        return "CONFLICTS_WITH", "the statements cannot be combined"

    def _build_group(self, group: list[ScheduleFact]) -> None:
        primary = group[0]
        code = self._event_code(primary)
        # A holiday rule modifies the series it is about; it is not a reading of
        # that series' timing (and alone it stays an UNSUPPORTED node).
        holiday_rules = [fact for fact in group if fact.timing and fact.timing.pattern == "HOLIDAY_RULE"]
        if len(holiday_rules) == len(group):
            holiday_rules = []
        readings: list[tuple[ScheduleFact, list[_Compiled] | None, str | None]] = []
        for fact in group:
            if fact in holiday_rules:
                continue
            try:
                compiled = self.compile_timing(fact)
                for item in compiled:
                    self.apply_window(item, fact.window, fact.what)
                readings.append((fact, compiled, None))
            except _Unresolved as error:
                readings.append((fact, None, str(error)))

        stated_conflict = any(fact.conflict_group or fact.conflicts_with for fact in group)
        # Permitted alternatives become ANY - but never a contradiction: a
        # group with a stated conflict stays CONFLICTING whatever else it says.
        alternatives = [] if stated_conflict else [fact for fact in group if fact.alternative_group]
        readings = [item for item in readings if item[0] not in alternatives]
        resolved = [item for item in readings if item[1] is not None]

        def signature(compiled: list[_Compiled]) -> str:
            return json.dumps([c.signature() for c in compiled] + [c.window_note for c in compiled], default=str)

        # One representative per distinct compiled rule; the rest are DUPLICATEs.
        distinct: list[tuple[ScheduleFact, list[_Compiled]]] = []
        by_signature: dict[str, ScheduleFact] = {}
        # Order-independent: the target reading first, then by meaning.
        resolved = sorted(resolved, key=lambda item: (item[0].timing.pattern not in _TARGET_PATTERNS,
                                                      signature(item[1])))
        for fact, compiled, _ in resolved:
            key = signature(compiled)
            if key in by_signature:
                self.relate("DUPLICATE", fact, by_signature[key], "states the same rule")
                continue
            by_signature[key] = fact
            distinct.append((fact, compiled))

        relations = {}
        for index, (first, _) in enumerate(distinct):
            for second, _ in distinct[index + 1:]:
                relations[(first.id, second.id)] = self._pair_relation(first, second)
        conjunction = (not stated_conflict and len(distinct) > 1
                       and all(kind == "AND" for kind, _ in relations.values()))
        conflicting = (stated_conflict and len(group) > 1) or (len(distinct) > 1 and not conjunction)
        if conflicting:
            for (first_id, second_id), (kind, reason) in relations.items():
                first = next(fact for fact, _ in distinct if fact.id == first_id)
                second = next(fact for fact, _ in distinct if fact.id == second_id)
                self.relate("CONFLICTS_WITH", second, first,
                            "the protocol marks them as disagreeing" if stated_conflict else reason)
            if stated_conflict:
                for fact in group[1:]:
                    self.relate("CONFLICTS_WITH", fact, primary, "the protocol marks them as disagreeing")
            alternatives = [{
                "fact_id": fact.id, "what": fact.what,
                "timing": fact.timing.model_dump(mode="json", by_alias=True) if fact.timing else None,
                "window": fact.window.model_dump(mode="json") if fact.window else None,
                "citations": [c.model_dump(mode="json") for c in fact.evidence],
                "unresolved_reason": reason,
            } for fact, _compiled, reason in readings]
            event = self._event(
                code, primary, group, _Compiled(timing=UnresolvedTiming(
                    reason=("The protocol gives conflicting statements for this visit; a reviewer "
                            "must choose one reading before approval."),
                    source_reference={"alternatives": alternatives})),
                status=InterpretationStatus.CONFLICTING)
            self.outcome(group, L.PENDING_INTERPRETATION,
                         "conflicts with another statement about the same visit; a reviewer must choose")
            self.issue(
                "CONFLICTING_SOURCE",
                f"{primary.what!r}: {len(group)} protocol statements disagree; none was chosen.",
                entity_type="EVENT", entity_id=event.id, facts=group, alternatives=alternatives)
            return

        if not resolved and alternatives:
            self._build_alternatives_only(code, group, alternatives, readings)
            return
        if not resolved:
            fact, _, reason = readings[0]
            citation = fact.evidence[0]
            self._event(code, primary, group, _Compiled(timing=UnresolvedTiming(
                reason=reason or "timing could not be resolved",
                source_reference={"page": citation.page, "section": citation.section,
                                  "quote": citation.quote, "fact_ids": [f.id for f in group]})))
            for item, _, item_reason in readings:
                self.outcome([item], L.PENDING_INTERPRETATION,
                             f"timing could not be compiled: {item_reason or 'not resolved'}")
            return
        unresolved = [fact for fact, compiled, _ in readings if compiled is None]
        # With a conjunction the primary is the reading that states the target;
        # every other restriction is attached to it, not dropped.
        if conjunction:
            fact, compiled_list = next(
                ((item, compiled) for item, compiled in distinct if item.timing.pattern in _TARGET_PATTERNS),
                distinct[0])
            extras = [(item, compiled) for item, compiled in distinct if item is not fact]
        else:
            fact, compiled_list = distinct[0]
            extras = []
        if len(compiled_list) == 1:
            events = [self._event(code, fact, group, compiled_list[0])]
        else:
            events = [self._event(f"{code}_P{index}", fact, group, compiled)
                      for index, compiled in enumerate(compiled_list, start=1)]
        target = events[0]
        if unresolved:
            # IR-05 / AT-04: a compiled primary statement must not hide an
            # uncompiled additional statement about the same visit - the visit
            # is not ready until the reviewer interprets it.
            for event in events:
                event.requires_review = True
            reasons = {item.id: reason for item, compiled, reason in readings if compiled is None}
            for item in unresolved:
                self.outcome([item], L.PENDING_INTERPRETATION,
                             f"an additional statement about this visit could not be compiled: {reasons[item.id]}")
            self.issue(
                "UNRESOLVED_STATEMENT",
                f"{primary.what!r}: {len(unresolved)} additional statement(s) about this visit could not be "
                "mapped. The visit is not ready until a reviewer interprets them; the compiled reading "
                "alone may omit a restriction.",
                entity_type="EVENT", entity_id=target.id, facts=unresolved,
                reasons=[reasons[item.id] for item in unresolved])
        items, sources, pending = self._restriction_nodes(fact, extras, alternatives)
        for rule in holiday_rules:
            self._apply_holiday_rule(rule, fact, events, items, sources, pending)
        if items:
            constraint = AllConstraint(id=f"{code}/constraints",
                                       items=sorted(items, key=semantic_key))
            for event in events:
                event.constraints = constraint
                event.metadata["constraint_sources"] = sources
            self.claim("EVENT", target.id, ["CONSTRAINT"],
                       [UUID(ref) for item in items for ref in item.evidence_refs])
        if pending:
            for event in events:
                event.requires_review = True
            self.outcome([item for item, _ in pending], L.PENDING_INTERPRETATION,
                         "an additional restriction on this visit cannot be written exactly")
            self.issue(
                "UNRESOLVED_STATEMENT",
                f"{primary.what!r}: {len(pending)} additional restriction(s) cannot be combined without "
                "guessing (for example an unstated boundary day or occurrence). The visit is not ready "
                "until a reviewer states them.",
                entity_type="EVENT", entity_id=target.id, facts=[item for item, _ in pending],
                reasons=[reason for _, reason in pending])

    def _apply_holiday_rule(self, rule: ScheduleFact, primary: ScheduleFact, events, items, sources,
                            pending) -> None:
        """HOLIDAY_ADJUSTMENT becomes the holiday policy of the weekday series it
        modifies; on anything else it stays an UNSUPPORTED node."""
        self.relate("AND", rule, primary, "a holiday rule of this series")
        series = [event for event in events if event.calendar_recurrence is not None]
        if not series:
            node = self.constraints.unsupported(rule, UNSUPPORTED_PATTERNS["HOLIDAY_RULE"])
            items.append(node)
            sources.append({"fact_id": rule.id, "relation": "UNSUPPORTED", "constraint_ids": [node.id]})
            self.outcome([rule], L.UNSUPPORTED, "a holiday rule on a series that is not a weekday series",
                         operator=UNSUPPORTED_PATTERNS["HOLIDAY_RULE"])
            return
        if rule.holiday_policy is None:
            pending.append((rule, "the holiday rule does not say whether a session is skipped or moved"))
            return
        for event in series:
            current = event.calendar_recurrence.holiday_policy
            if current != HolidayPolicy.NONE_STATED and current.value != rule.holiday_policy:
                pending.append((rule, f"contradicts the series' stated holiday policy {current.value}"))
                return
        for event in series:
            event.calendar_recurrence = event.calendar_recurrence.model_copy(
                update={"holiday_policy": HolidayPolicy(rule.holiday_policy)})
            event.evidence_refs.extend(self.cite(rule.evidence, [rule]))
        sources.append({"fact_id": rule.id, "relation": "HOLIDAY_POLICY", "constraint_ids": []})

    def _restriction_nodes(self, fact: ScheduleFact, extras, alternatives: list[ScheduleFact]):
        """Constraint nodes for every restriction beyond the primary reading:
        AND extras, OR alternatives and calendar rules no operator executes.
        Returns (nodes, provenance, statements that could not be written)."""
        items: list[Any] = []
        sources: list[dict[str, Any]] = []
        pending: list[tuple[ScheduleFact, str]] = []
        for item, _compiled in extras:
            self.relate("AND", item, fact, self._pair_relation(fact, item)[1])
            try:
                nodes = self.constraints.nodes(item)
            except (NotExact, _Unresolved) as error:
                pending.append((item, str(error)))
                continue
            items.extend(nodes)
            sources.append({"fact_id": item.id, "relation": "AND", "constraint_ids": [node.id for node in nodes]})
        for name in unsupported_wording(fact) if fact.timing.pattern not in UNSUPPORTED_PATTERNS else []:
            node = self.constraints.unsupported(fact, name)
            items.append(node)
            sources.append({"fact_id": fact.id, "relation": "UNSUPPORTED", "constraint_ids": [node.id]})
            self.outcome([fact], L.UNSUPPORTED, f"the cited text states a {name} rule that no executable "
                                                "operator covers yet", operator=name)
        any_nodes, any_pending = self._alternative_nodes(alternatives)
        items.extend(any_nodes)
        pending.extend(any_pending)
        for node in any_nodes:
            sources.append({"fact_ids": [ref for ref in node.id.split("|")], "relation": "OR",
                            "constraint_ids": [node.id]})
        return items, sources, pending

    def _alternative_nodes(self, alternatives: list[ScheduleFact]):
        """One ANY per alternative label. A label whose options cannot all be
        written exactly is not partially kept: every option stays pending."""
        by_label: dict[str, list[ScheduleFact]] = {}
        for fact in alternatives:
            # Labels are batch-prefixed by the reader; one visit's alternatives
            # delivered in different batches still form one choice.
            label = fact.alternative_group.split(":", 1)[-1] if fact.alternative_group else ""
            by_label.setdefault(label, []).append(fact)
        nodes: list[Any] = []
        pending: list[tuple[ScheduleFact, str]] = []
        for label, facts in sorted(by_label.items()):
            branches = []
            try:
                for fact in facts:
                    leaves = self.constraints.nodes(fact)
                    branches.append(leaves[0] if len(leaves) == 1 else AllConstraint(
                        id=f"{fact.id}/all", items=leaves))
            except (NotExact, _Unresolved) as error:
                pending.extend((fact, f"alternative {label!r}: {error}") for fact in facts)
                continue
            if len(branches) < 2:
                pending.extend((fact, f"alternative {label!r} has only one option") for fact in facts)
                continue
            for first, second in zip(facts, facts[1:]):
                self.relate("OR", second, first, "permitted alternatives for one occurrence")
            nodes.append(AnyConstraint(id="|".join(sorted(fact.id for fact in facts)),
                                       items=sorted(branches, key=semantic_key)))
        return nodes, pending

    def _build_alternatives_only(self, code: str, group: list[ScheduleFact], alternatives: list[ScheduleFact],
                                 readings) -> None:
        """A visit timed only by stated alternatives: CONSTRAINED timing = ANY."""
        nodes, pending = self._alternative_nodes(alternatives)
        unresolved = [(fact, reason) for fact, compiled, reason in readings if compiled is None]
        if not nodes:
            first = alternatives[0]
            citation = first.evidence[0]
            self._event(code, first, group, _Compiled(timing=UnresolvedTiming(
                reason="; ".join(reason for _, reason in pending) or "the alternatives could not be written",
                source_reference={"page": citation.page, "quote": citation.quote,
                                  "fact_ids": [fact.id for fact in group]})))
            self.outcome([fact for fact, _ in pending], L.PENDING_INTERPRETATION,
                         "the stated alternatives cannot be written exactly")
            return
        constraint = nodes[0] if len(nodes) == 1 else AllConstraint(id=f"{code}/alternatives", items=nodes)
        event = self._event(code, alternatives[0], group, _Compiled(timing=ConstraintTiming(constraint=constraint)))
        self.claim("EVENT", event.id, ["CONSTRAINT"], [UUID(ref) for node in nodes for leaf in node.items
                                                       for ref in leaf.evidence_refs])
        leftovers = pending + unresolved
        if leftovers:
            event.requires_review = True
            self.outcome([fact for fact, _ in leftovers], L.PENDING_INTERPRETATION,
                         "a statement about this visit could not be written exactly")
            self.issue("UNRESOLVED_STATEMENT",
                       f"{alternatives[0].what!r}: {len(leftovers)} statement(s) about this visit could not be "
                       "combined with its alternatives.", entity_type="EVENT", entity_id=event.id,
                       facts=[fact for fact, _ in leftovers], reasons=[reason for _, reason in leftovers])

    def _event(self, code: str, fact: ScheduleFact, group: list[ScheduleFact], compiled: _Compiled,
               *, status: InterpretationStatus = InterpretationStatus.EXTRACTED) -> Event:
        event_type = (fact.event_type or "").strip().upper() or {
            "activity": "ASSESSMENT", "milestone": "OTHER"}.get(fact.kind, "VISIT")
        metadata: dict[str, object] = {
            "fact_ids": [item.id for item in group], "compiler": COMPILER_VERSION}
        if fact.kind == "milestone":
            metadata["milestone_anchor"] = code_for(fact.code or fact.what)
            metadata["planned_window_only"] = True
        if fact.kind == "activity" and fact.at_visits:
            metadata["performed_with_visits"] = list(fact.at_visits)
        if compiled.window_note:
            metadata["window_as_stated"] = compiled.window_note
        timing = compiled.timing
        event = Event(
            code=code,
            protocol_label=fact.what + compiled.label_suffix,
            display_name=fact.what + compiled.label_suffix,
            event_type=event_type,
            timing=timing,
            applicability=self.applicability(fact),
            dependencies=compiled.dependencies,
            dependency_mode=compiled.dependency_mode,
            visit_mode=fact.visit_mode,
            activation=compiled.activation,
            recurrence=compiled.recurrence,
            recurrence_plan=compiled.recurrence_plan,
            calendar_recurrence=compiled.calendar_recurrence,
            window=compiled.window,
            interpretation_status=status,
            requires_review=status != InterpretationStatus.EXTRACTED or isinstance(timing, UnresolvedTiming),
            metadata=metadata,
        )
        evidence_ids = self.cite(self.citations(group), group)
        event.evidence_refs = evidence_ids
        claims = ["EVENT_NAME", "TIMING"] + (["WINDOW"] if event.window is not None else [])
        if (event.recurrence is not None or event.recurrence_plan is not None
                or event.calendar_recurrence is not None):
            claims.append("RECURRENCE")
        if event.applicability:
            claims.append("APPLICABILITY")
        if event.dependencies:
            claims.append("DEPENDENCY")
        if event.dependency_mode is not None:
            claims.append("DEPENDENCY_MODE")
        self.claim("EVENT", event.id, claims, evidence_ids)
        if compiled.window_note:
            self.issue(
                "WINDOW_NOT_APPLIED",
                f"{fact.what!r}: the stated window could not be applied ({compiled.window_note['reason']}); "
                "a reviewer must enter it.",
                blocking=False, entity_type="EVENT", entity_id=event.id, facts=group,
                window=compiled.window_note)
        self.events[code] = event
        self.event_facts[code] = group
        self.ref(group, "EVENT", code, event.id)
        blocks = {code_for(item.block) for item in group if item.block}
        if compiled.block_hint:
            blocks.add(compiled.block_hint)
            self.block_names.setdefault(compiled.block_hint, compiled.block_hint)
        for block in blocks:
            self.block_members.setdefault(block, []).append(code)
            self.block_facts.setdefault(block, []).extend(group)
        if compiled.stop_condition:
            series = next(iter(sorted(blocks)), f"{code}_SERIES")
            if not blocks:
                self.condition_only_blocks.add(series)
            if series not in self.block_members or code not in self.block_members[series]:
                self.block_members.setdefault(series, []).append(code)
                self.block_facts.setdefault(series, []).extend(group)
            self.add_action(compiled.stop_condition, "STOP_BLOCK", series, fact)
        return event

    # --- activities -------------------------------------------------------------

    def build_activities(self) -> None:
        wanted: dict[str, list[tuple[str, ScheduleFact | None, ScheduleFact]]] = {}
        for code, group in self.event_facts.items():
            for fact in group:
                for name in fact.activities:
                    wanted.setdefault(code, []).append((name, None, fact))
        for fact in self.facts:
            if fact.kind != "activity" or self._is_event_fact(fact):
                continue
            if not self._is_patient(fact):
                self.non_patient(fact)
                continue
            if not fact.at_visits:
                self.issue(
                    "UNRESOLVED_REFERENCE",
                    f"Activity {fact.what!r} names no visit and no timing of its own; it would be "
                    "silently missing from the schedule.", facts=[fact])
                continue
            for visit in fact.at_visits:
                mapped = self.event_names.get(code_for(visit), code_for(visit))
                codes = [mapped] if mapped in self.events else [
                    event_code for event_code in self.events if event_code.startswith(f"{mapped}_P")]
                if not codes:
                    self.issue(
                        "UNRESOLVED_REFERENCE",
                        f"Activity {fact.what!r} is performed at {visit!r}, which is not a defined visit.",
                        facts=[fact], visit=visit)
                    continue
                for event_code in codes:
                    wanted.setdefault(event_code, []).append((fact.what, fact, fact))

        for event_code, items in wanted.items():
            event = self.events[event_code]
            by_code: dict[str, Activity] = {}
            for name, detail, source in items:
                code = code_for(name)
                activity = by_code.get(code)
                if activity is None:
                    activity = Activity(
                        code=code, protocol_label=name, display_name=name,
                        activity_type=((detail.event_type if detail else None) or "ASSESSMENT").upper(),
                        sequence_number=len(by_code) + 1,
                    )
                    by_code[code] = activity
                evidence_ids = self.cite(source.evidence, [source])
                activity.evidence_refs.extend(item for item in evidence_ids if item not in activity.evidence_refs)
                self.claim("ACTIVITY", activity.id, ["ACTIVITY"], evidence_ids)
                if detail is not None:
                    self.ref([detail], "ACTIVITY", code, activity.id, event_code=event_code)
                    fact_ids = activity.metadata.setdefault("fact_ids", [])
                    if detail.id not in fact_ids:
                        fact_ids.append(detail.id)
                if detail is None:
                    continue
                activity.applicability = self.applicability(detail)
                if detail.timing is not None:
                    activity.timing = self._activity_timing(detail, activity)
                    if activity.timing is not None:
                        self.claim("ACTIVITY", activity.id, ["ACTIVITY_TIMING"], evidence_ids)
                if detail.occurrence_note or detail.occurrences is not None:
                    numbers, reason = self._occurrence_numbers(detail, event)
                    text = detail.occurrence_note or f"cycles {numbers}"
                    if numbers is not None:
                        # Spec F5: an evaluable gate on the activity - the engine
                        # seeds occurrence_number = cycle number for every-cycle
                        # visits, so MRI is NOT_APPLICABLE on the other cycles.
                        activity.conditions.append(MembershipCondition(
                            operator="IN", value=FieldOperand(field="occurrence_number"),
                            values=numbers))
                        activity.metadata["occurrence_rule"] = {
                            "text": text, "cycles": numbers, "fact_id": detail.id}
                        self.claim("ACTIVITY", activity.id, ["CONDITION"], evidence_ids)
                        continue
                    qualifier = Qualifier(
                        text=text, scope=QualifierScope.ACTIVITY,
                        category=QualifierCategory.REPEAT, target_codes=[code], resolved=False,
                        evidence_refs=evidence_ids,
                        details={"fact_id": detail.id, "reason": reason})
                    activity.qualifiers.append(qualifier)
                    self.claim("QUALIFIER", qualifier.id, ["QUALIFIER"], evidence_ids)
            event.activities = list(by_code.values())

    def _activity_timing(self, fact: ScheduleFact, activity: Activity):
        timing = fact.timing
        assert timing is not None
        if timing.pattern != "OFFSET" or timing.reference is None or timing.offset is None:
            self.issue(
                "UNSUPPORTED_TIMING",
                f"Activity {fact.what!r} has a timing inside its visit that is not an offset from "
                "another activity; a reviewer must enter it.", facts=[fact],
                entity_type="ACTIVITY", entity_id=activity.id)
            return None
        return OffsetTiming(
            reference=ActivityReference(activity_code=code_for(timing.reference.name)),
            offset=_amount(timing.offset))

    def _occurrence_numbers(self, fact: ScheduleFact, event: Event) -> tuple[list[int] | None, str]:
        """The cycle numbers an activity is done in, or why they cannot be
        stated as a checkable rule (then the note stays an unresolved qualifier).

        Only for a visit held once per cycle (``CYCLE_DAY`` of the CURRENT
        cycle), where the engine's ``occurrence_number`` IS the cycle number.
        A pattern ("every second cycle", "even cycles") becomes the explicit
        list of cycles, so it needs a stated number of cycles.
        """
        selector = fact.occurrences
        if selector is None:
            return None, "the cycles are described only in words"
        timing = event.timing
        group = self.cycle_plans.get(timing.cycle_reference) if isinstance(timing, CycleDayTiming) else None
        if group is None or group.plan is None or timing.cycle.type != "CURRENT":
            return None, "the visit is not held once per treatment cycle, so a cycle number cannot be checked there"
        termination = group.plan.termination
        count = termination.count if termination.type == "COUNT" else None
        patterns = [item for item in (bool(selector.numbers), selector.parity is not None,
                                      selector.every is not None) if item]
        if len(patterns) != 1:
            return None, "the cycles are not stated as exactly one list or pattern"
        if selector.numbers:
            numbers = sorted(set(selector.numbers))
            if numbers[0] < 1 or (count is not None and numbers[-1] > count):
                return None, "a listed cycle is outside the stated number of cycles"
            return numbers, ""
        if selector.parity is not None:
            first, step = (2 if selector.parity == "EVEN" else 1), 2
        else:
            if selector.starting_at is None:
                return None, (f"every {selector.every} cycles is stated without the cycle it starts "
                              f"with (Cycle 1 or Cycle {selector.every})")
            first, step = selector.starting_at, selector.every
        if count is None:
            return None, ("the number of cycles is not fixed, and a repeating cycle pattern can only be "
                          "checked as a listed set of cycles")
        numbers = list(range(first, count + 1, step))
        if not numbers:
            return None, "the pattern selects no cycle within the stated number of cycles"
        return numbers, ""

    # --- conditions and blocks ---------------------------------------------------

    def add_action(self, condition: str, action_type: str, target: str, fact: ScheduleFact,
                   parameters: dict[str, object] | None = None) -> None:
        actions = self.condition_actions.setdefault(condition, [])
        if any(item.action_type == action_type and item.target_code == target for item, _ in actions):
            return
        actions.append((ConditionalAction(
            action_type=action_type, target_code=target,
            condition=ExistsCondition(field=f"conditions.{condition}"),
            parameters=parameters or {}), fact))

    def link_triggered_events(self) -> None:
        """A visit triggered by a condition is added only once it occurs (spec A10)."""
        for code, event in self.events.items():
            timing = event.timing
            trigger = getattr(timing, "trigger", None)
            if not isinstance(trigger, AnchorReference):
                continue
            for condition, anchor_code in self.condition_anchor.items():
                if anchor_code == trigger.code and not event.metadata.get("repeat_until_resolved"):
                    self.add_action(condition, "ADD_EVENT", code, self.event_facts[code][0])

    def _resolvable(self, target: str) -> bool:
        key = code_for(target)
        return key in self.block_names or key in self.event_names

    def build_conditions(self) -> None:
        for fact in self.facts:
            if not self._is_condition_fact(fact):
                continue
            spec = fact.condition
            if not self._is_patient(fact):
                # Spec I8: recorded for the trial-level workflow. When it names
                # patient visits or blocks it is also kept as a rule scoped to
                # the trial, which the patient engine refuses to act on; with
                # nothing patient-level to name, there is nothing to block on.
                self.non_patient(fact)
                targets = [item for item in (spec.targets if spec else []) if self._resolvable(item)]
                adds = [item for item in (spec.adds if spec else []) if self._resolvable(item)]
                if not targets and not adds:
                    continue
                spec = spec.model_copy(update={"targets": targets, "adds": adds})
                fact = fact.model_copy(update={"condition": spec})
            name = spec.name if spec else fact.what
            code = self.condition_names.setdefault(code_for(name), code_for(name))
            meta = self.condition_meta.setdefault(code, {"facts": [], "trigger": None, "resolution": None,
                                                          "name": name, "conflicting": False,
                                                          "scopes": set()})
            meta.setdefault("scopes", set()).add(fact.scope)
            meta["facts"].append(fact)
            if fact.conflict_group or fact.conflicts_with:
                meta["conflicting"] = True
            if spec and spec.trigger and meta["trigger"] is None:
                meta["trigger"] = spec.trigger
            if spec and spec.resolution and meta["resolution"] is None:
                meta["resolution"] = spec.resolution
            pattern = fact.timing.pattern if fact.timing else None
            effect = (spec.effect if spec else None) or {
                "HOLD_RESUME": "PAUSE", "DISCONTINUE": "STOP", "REPEAT_UNTIL_RESOLVED": "REPEAT",
            }.get(pattern or "")
            if effect is None:
                self.issue(
                    "UNRESOLVED_CONDITION",
                    f"Condition {name!r}: the protocol statement does not say what it does to the "
                    "schedule.", facts=[fact])
                continue
            targets = list(spec.targets) if spec else []
            if spec and spec.participation:
                self._participation(code, spec, fact)
                continue
            component_targets = [item for item in (list(spec.target_components) if spec else [])
                                 + targets if code_for(item) in self.components]
            if component_targets and effect in {"PAUSE", "STOP"}:
                # WP3: the rule names one component, so only that process changes;
                # other drugs and follow-up continue.
                targets = [item for item in targets if item not in component_targets]
                for component in dict.fromkeys(code_for(item) for item in component_targets):
                    self._process_rule(component, spec, fact)
                    if effect == "PAUSE":
                        self.add_action(code, "HOLD_PROCESS", component, fact)
                        if pattern == "HOLD_RESUME":
                            resume = f"{code}_RESUMED"
                            self.condition_names.setdefault(resume, resume)
                            self.condition_meta.setdefault(resume, {
                                "facts": [fact], "trigger": spec.resume_when if spec else None,
                                "resolution": None, "name": f"{name} - resume",
                                "conflicting": meta["conflicting"], "scopes": {fact.scope}})
                            self.add_action(resume, "RESUME_PROCESS", component, fact)
                    else:
                        self.add_action(code, "STOP_PROCESS", component, fact)
                if not targets:
                    continue
            if effect == "PAUSE":
                self._block_action(code, "PAUSE_BLOCK", targets, fact)
                if pattern == "HOLD_RESUME":
                    resume = f"{code}_RESUMED"
                    self.condition_names.setdefault(resume, resume)
                    self.condition_meta.setdefault(resume, {
                        "facts": [fact], "trigger": spec.resume_when if spec else None,
                        "resolution": None, "name": f"{name} - resume",
                        "conflicting": meta["conflicting"], "scopes": {fact.scope}})
                    self._block_action(resume, "RESUME_BLOCK", targets, fact,
                                       resume_mode=spec.resume_mode if spec else None)
            elif effect == "STOP":
                for target in targets or [None]:
                    if target is not None and code_for(target) in self.event_names:
                        event_code = self.event_names[code_for(target)]
                        if not self._blocks_of(event_code):
                            self.add_action(code, "CANCEL_EVENT", event_code, fact)
                            continue
                    self._block_action(code, "STOP_BLOCK", [target] if target else [], fact)
            elif effect in {"CANCEL", "MANUAL_REVIEW", "ADD"}:
                action = {"CANCEL": "CANCEL_EVENT", "MANUAL_REVIEW": "MANUAL_REVIEW",
                          "ADD": "ADD_EVENT"}[effect]
                names = targets + (list(spec.adds) if spec else [])
                if not names:
                    self.issue("UNRESOLVED_CONDITION",
                               f"Condition {name!r} does not name the visits it affects.", facts=[fact])
                for target in names:
                    event_code = self.event_names.get(code_for(target))
                    if event_code is None:
                        self.issue("UNRESOLVED_REFERENCE",
                                   f"Condition {name!r} affects {target!r}, which is not a defined visit.",
                                   facts=[fact], target=target)
                        continue
                    self.add_action(code, action, event_code, fact)
            elif effect == "REPEAT":
                self._repeat_until_resolved(code, name, fact)
            for added in (spec.adds if spec and effect != "ADD" else []):
                event_code = self.event_names.get(code_for(added))
                if event_code is None:
                    self.issue("UNRESOLVED_REFERENCE",
                               f"Condition {name!r} adds {added!r}, which is not a defined visit.",
                               facts=[fact], target=added)
                else:
                    self.add_action(code, "ADD_EVENT", event_code, fact)

    def _process_rule(self, component: str, spec, fact: ScheduleFact) -> None:
        entry = self.components[component]
        entry.setdefault("rules", []).append((spec, fact))

    def _participation(self, condition: str, spec, fact: ScheduleFact) -> None:
        """Withdrawal from treatment, withdrawal of consent and end of
        follow-up are three different decisions and are never merged."""
        kind = {"WITHDRAWAL_FROM_TREATMENT": "INTERVENTION", "END_OF_FOLLOW_UP": "FOLLOW_UP"}.get(
            spec.participation)
        if kind is None:
            # Consent withdrawal stops the patient's whole participation; it is
            # enacted by a recorded patient-level decision, not a block stop.
            self.extensions.append({
                "type": "PARTICIPATION_RULE", "participation": spec.participation, "fact_id": fact.id,
                "what": fact.what, "enacted_by": "a recorded WITHDRAWAL_OF_CONSENT process transition",
                "citations": [citation.model_dump(mode="json") for citation in fact.evidence]})
            self.ref([fact], "PARTICIPATION_RULE", spec.participation)
            return
        targets = [code for code, entry in self.components.items() if kind in entry["kinds"]]
        if not targets:
            self.issue("UNRESOLVED_CONDITION",
                       f"{fact.what!r} is a {spec.participation.replace('_', ' ').lower()} rule, but no "
                       f"{kind.replace('_', ' ').lower()} component is stated to apply it to.", facts=[fact])
            return
        action = "STOP_PROCESS" if kind == "INTERVENTION" else "COMPLETE_PROCESS"
        for component in targets:
            self.add_action(condition, action, component, fact, parameters={"participation": spec.participation})

    def build_processes(self) -> None:
        """One process per named component, linked to the events it owns. Kind
        and hold/resume policy come only from what the protocol states."""
        owners: dict[str, set[str]] = {}
        for event_code, facts in self.event_facts.items():
            for fact in facts:
                if fact.component and code_for(fact.component) in self.components:
                    owners.setdefault(event_code, set()).add(code_for(fact.component))
        for code, entry in sorted(self.components.items()):
            facts = entry["facts"] + [fact for _, fact in entry.get("rules", [])]
            if len(entry["kinds"]) != 1:
                self.issue("PROCESS_KIND_UNSTATED",
                           f"Component {entry['name']!r}: the protocol extract "
                           + ("states more than one kind" if entry["kinds"] else "does not say whether it is "
                              "treatment, follow-up or an assessment pathway")
                           + "; it cannot be stopped or held on its own until a reviewer states it.",
                           facts=facts, component=code)
                continue
            policies = {spec.hold_resume_policy for spec, _ in entry.get("rules", []) if spec.hold_resume_policy}
            triggers = {spec.resume_trigger for spec, _ in entry.get("rules", []) if spec.resume_trigger}
            for label, values in (("hold/resume policy", policies), ("resume trigger", triggers)):
                if len(values) > 1:
                    self.issue("CONFLICTING_SOURCE",
                               f"Component {entry['name']!r}: statements give different {label}s; none was "
                               "chosen.", facts=facts, component=code, values=sorted(values))
            linked = [event for event, names in owners.items() if names == {code}]
            for event, names in owners.items():
                if code in names and len(names) > 1:
                    self.issue("PROCESS_OBLIGATION_AMBIGUOUS",
                               f"{event} is described as part of several components ({', '.join(sorted(names))}); "
                               "it is not linked to any until a reviewer decides.", facts=facts)
            evidence_ids = self.cite(self.citations(facts), facts)
            process = ProcessDefinition(
                code=code, display_name=entry["name"], kind=ProcessKind(next(iter(entry["kinds"]))),
                event_codes=sorted(set(linked)),
                hold_resume_policy=(HoldResumePolicy(next(iter(policies))) if len(policies) == 1
                                    else HoldResumePolicy.NONE_STATED),
                resume_trigger=next(iter(triggers)) if len(triggers) == 1 else None,
                evidence_refs=evidence_ids)
            self.claim("PROCESS", process.id, ["PROCESS"], evidence_ids)
            self.ref(facts, "PROCESS", code, process.id)
            self.processes.append(process)

    def _blocks_of(self, event_code: str) -> list[str]:
        return [block for block, members in self.block_members.items() if event_code in members]

    def _block_action(self, condition: str, action: str, targets: list[str], fact: ScheduleFact,
                      *, resume_mode: str | None = None) -> None:
        if not targets:
            self.issue("UNRESOLVED_CONDITION",
                       f"{action.replace('_', ' ').lower()} for {condition} does not name the treatment "
                       "it applies to.", facts=[fact])
            return
        for target in targets:
            key = code_for(target)
            if key in self.block_names:
                block = self.block_names[key]
            elif key in self.event_names:
                event_code = self.event_names[key]
                blocks = self._blocks_of(event_code)
                block = blocks[0] if blocks else f"{event_code}_BLOCK"
                if not blocks:
                    self.condition_only_blocks.add(block)
                    self.block_members.setdefault(block, []).append(event_code)
                    self.block_facts.setdefault(block, []).extend(self.event_facts.get(event_code, []))
                    self.block_names[block] = block
            else:
                self.issue("UNRESOLVED_REFERENCE",
                           f"{condition} applies to {target!r}, which is not a defined visit or "
                           "treatment block.", facts=[fact], target=target)
                continue
            self.block_facts.setdefault(block, []).append(fact)
            if resume_mode:
                self.block_resume.setdefault(block, set()).add(resume_mode)
            self.add_action(condition, action, block, fact)

    def _repeat_until_resolved(self, condition: str, name: str, fact: ScheduleFact) -> None:
        spec = fact.condition
        interval = (spec.interval if spec else None) or (fact.repeat.interval if fact.repeat else None)
        targets = (list(spec.targets) if spec else []) or [fact.what]
        for target in targets:
            code = f"{code_for(target)}_WHILE_{condition}"
            anchor = self.condition_anchor_code(condition, fact)
            if interval is None or interval.value <= 0:
                compiled = _Compiled(timing=UnresolvedTiming(
                    reason="the repeat interval while the condition is active is not stated",
                    source_reference={"page": fact.evidence[0].page, "quote": fact.evidence[0].quote}))
            else:
                compiled = _Compiled(timing=TriggeredTiming(
                    trigger=AnchorReference(code=anchor),
                    timing_after_trigger=TriggerOffset(offset=_amount(interval))))
            display = fact.model_copy(update={
                "what": f"{target} (repeat while {name})", "kind": "visit", "code": None,
                "event_type": fact.event_type})
            event = self._event(code, display, [fact], compiled)
            event.metadata["repeat_until_resolved"] = condition
            self.event_names[code] = code
            self.add_action(
                condition, "REPEAT_EVENT", code, fact,
                parameters={"interval": _positive(interval, "interval").model_dump(mode="json")}
                if interval is not None and interval.value else {})

    def build_condition_definitions(self) -> list[ConditionalDefinition]:
        definitions: list[ConditionalDefinition] = []
        for code, actions in self.condition_actions.items():
            meta = self.condition_meta.get(code, {"facts": [], "trigger": None, "resolution": None,
                                                  "name": code.replace("_", " ").title(),
                                                  "conflicting": False})
            facts = list(meta["facts"]) + [fact for _, fact in actions if fact not in meta["facts"]]
            trigger = _criterion(meta["trigger"], f"conditions.{code}")
            resolution = meta["resolution"]
            has_repeat = any(action.action_type == "REPEAT_EVENT" for action, _ in actions)
            scopes = set(meta.get("scopes") or {"PATIENT"})
            if len(scopes) > 1:
                # One condition described at two scopes: which one governs is
                # the reviewer's call, not the compiler's.
                meta["conflicting"] = True
            scope = RuleScope(sorted(scopes)[0])
            applicability = []
            for fact in facts:
                if self._is_condition_fact(fact) and self._is_patient(fact):
                    applicability = self.applicability(fact)
                    break
            definition = ConditionalDefinition(
                code=code, protocol_label=meta["name"], display_name=meta["name"],
                scope=scope,
                condition=trigger,
                applicability=applicability,
                actions=[action.model_copy(update={"condition": trigger}) for action, _ in actions],
                resolution_condition=(_criterion(resolution, f"conditions.{code}")
                                      if resolution is not None else None),
                interpretation_status=(InterpretationStatus.UNRESOLVED if meta["conflicting"]
                                       else InterpretationStatus.EXTRACTED),
                requires_review=bool(meta["conflicting"]),
            )
            evidence_ids = self.cite(self.citations(facts), facts)
            definition.evidence_refs = evidence_ids
            self.ref(facts, "CONDITION", code, definition.id)
            if meta["conflicting"]:
                self.outcome(facts, L.PENDING_INTERPRETATION,
                             "statements about this condition disagree; a reviewer must choose")
            claims = ["CONDITION", "CONDITIONAL_ACTION"]
            if definition.resolution_condition is not None:
                claims.append("CONDITIONAL_RESOLUTION")
            self.claim("CONDITION", definition.id, claims, evidence_ids)
            if meta["conflicting"]:
                self.issue("CONFLICTING_SOURCE",
                           f"Condition {meta['name']!r}: protocol statements disagree; none was chosen.",
                           entity_type="CONDITION", entity_id=definition.id, facts=facts)
            if has_repeat and resolution is None:
                self.issue("UNRESOLVED_CONDITION",
                           f"Condition {meta['name']!r} repeats a visit until it resolves, but the "
                           "resolution threshold is not stated as a checkable value.",
                           entity_type="CONDITION", entity_id=definition.id, facts=facts)
            definitions.append(definition)
        return definitions

    def build_blocks(self) -> list[RepeatBlock]:
        blocks: list[RepeatBlock] = []
        stated = {code_for(block.name): block for block in self.document.blocks}
        for code, members in self.block_members.items():
            members = list(dict.fromkeys(member for member in members if member in self.events))
            if not members:
                continue
            block_fact = stated.get(code)
            facts = [fact for fact in self.block_facts.get(code, [])]
            modes = {fact.dependency_mode for fact in facts
                     if fact.dependency_mode and fact.kind != "condition"}
            mode = (block_fact.dependency_mode if block_fact and block_fact.dependency_mode
                    else (modes.pop() if len(modes) == 1 else None)) or "UNCLEAR"
            resume_modes = set(self.block_resume.get(code, set()))
            resume = (block_fact.resume_mode if block_fact and block_fact.resume_mode
                      else (resume_modes.pop() if len(resume_modes) == 1 else None)) or "UNCLEAR"
            mode = _EVENT_MODES.get(mode, mode)
            group = self.cycle_groups.get(code)
            plan = group.plan if group is not None else None
            if plan is not None:
                # The block and its cycle plan state one rule; the validator
                # treats a disagreement as a conflict, so they come from the
                # same statement.
                mode = CYCLE_BLOCK_DEPENDENCY_MODES[plan.dependency_mode].value
                facts = facts + [fact for fact in group.facts if all(fact is not other for other in facts)]
            label = block_fact.name if block_fact else code.replace("_", " ").title()
            block = RepeatBlock(
                code=code, protocol_label=label, display_name=label, event_codes=members,
                dependency_mode=DependencyMode(mode), resume_mode=resume, cycle_plan=plan)
            citations = (list(block_fact.evidence) if block_fact else []) + self.citations(facts)
            evidence_ids = self.cite(citations, facts)
            block.evidence_refs = evidence_ids
            claims = ["REPEAT_BLOCK", "DEPENDENCY_MODE"] + (["CYCLE_PLAN"] if plan is not None else [])
            self.claim("REPEAT_BLOCK", block.id, claims, evidence_ids)
            if (mode == "ACTUAL_PREVIOUS_EVENT" and plan is None
                    and any(self.events[m].recurrence or self.events[m].recurrence_plan for m in members)):
                # A plain repeat is always dated on its own grid; only a cycle
                # plan follows the actual previous cycle (spec F2). Honouring
                # this block silently would give wrong dates.
                self.issue(
                    "UNSUPPORTED_TIMING",
                    f"Block {label!r}: later occurrences follow the actual previous one, but its "
                    "visits are plain repeats, not cycle days; restate them as Day N of each cycle.",
                    entity_type="REPEAT_BLOCK", entity_id=block.id, facts=facts)
            if code in self.condition_only_blocks and plan is None and mode == "UNCLEAR":
                # Decision (item 6): the engine never reads a plain block's
                # dependency mode - each visit keeps its own timing - but the
                # validator requires one on every block. It is NOT invented here;
                # this note tells the reviewer why the block exists.
                self.issue(
                    "CONDITION_BLOCK_MODE_UNSTATED",
                    f"Block {label!r} exists only so a condition can pause or stop "
                    f"{', '.join(members)}; the protocol states no cycle dependency for it. "
                    "Its visits keep their own timing; a reviewer must record the block mode.",
                    blocking=False, severity=Severity.INFO,
                    entity_type="REPEAT_BLOCK", entity_id=block.id, facts=facts)
            blocks.append(block)
        return blocks

    # --- anchors ------------------------------------------------------------------

    def build_anchors(self) -> None:
        for fact in self.facts:
            if fact.kind not in {"anchor", "milestone"}:
                continue
            code = code_for(fact.code or fact.what)
            if code in self.anchors:
                self.anchors[code].evidence_refs.extend(self.cite(fact.evidence, [fact]))
                self.ref([fact], "ANCHOR", code, self.anchors[code].id)
                continue
            source = fact.anchor
            status = "RESOLVED"
            kwargs: dict[str, Any] = {}
            if source is not None and source.type == "EVENT":
                event_code = self.event_names.get(code_for(source.event or ""))
                if event_code is None or event_code not in self.events or source.selection is None:
                    status = "UNRESOLVED"
                    self.issue(
                        "UNRESOLVED_REFERENCE",
                        f"Anchor {fact.what!r} is derived from a visit, but the visit or which "
                        "occurrence (first or last) is not stated.", facts=[fact])
                else:
                    kwargs["source_event_code"] = event_code
                    kwargs["derivation_rule"] = {"source": "EVENT", "selection": source.selection}
            elif source is not None and source.type == "CONDITION" and source.condition:
                condition = self.condition_names.setdefault(
                    code_for(source.condition), code_for(source.condition))
                self.condition_anchor.setdefault(condition, code)
                kwargs["source_condition_code"] = condition
                kwargs["derivation_rule"] = {"source": "CONDITION"}
            else:
                kwargs["derivation_rule"] = {"source": "RECORDED"}
            evidence_ids = self.cite(fact.evidence, [fact])
            anchor = Anchor(
                code=code, protocol_label=fact.what, display_name=fact.what,
                anchor_type="MILESTONE" if fact.kind == "milestone" else code,
                status=status, evidence_refs=evidence_ids, **kwargs)
            self.claim("ANCHOR", anchor.id, ["ANCHOR"], evidence_ids)
            self.anchors[code] = anchor
            self.ref([fact], "ANCHOR", code, anchor.id)

    def finish_anchors(self) -> None:
        """Anchors named by rules but not defined by an anchor fact of their own."""
        for condition, code in self.condition_anchor.items():
            if code not in self.anchors:
                facts = self.referenced_anchor_facts.get(code, [])
                evidence_ids = self.cite(self.citations(facts), facts)
                self.anchors[code] = Anchor(
                    code=code, protocol_label=f"Date of {condition.replace('_', ' ').lower()}",
                    display_name=f"Date of {condition.replace('_', ' ').lower()}",
                    anchor_type="CONDITION", source_condition_code=condition,
                    derivation_rule={"source": "CONDITION"}, status="RESOLVED",
                    evidence_refs=evidence_ids)
        day_1 = self._day_1_code()
        if day_1 and day_1 not in self.anchors:
            users = self.referenced_anchor_facts.get(day_1, [])
            citations = list(self.document.day_numbering.evidence) + self.citations(users)
            evidence_ids = self.cite(citations, users)
            name = self.document.day_numbering.day_1_anchor or day_1
            self.anchors[day_1] = Anchor(
                code=day_1, protocol_label=name, display_name=name, anchor_type=day_1,
                derivation_rule={"source": "RECORDED"},
                status="RESOLVED" if citations else "UNRESOLVED", evidence_refs=evidence_ids)
        for code, facts in self.referenced_anchor_facts.items():
            if code in self.anchors:
                continue
            # The protocol names this date (e.g. "within 28 days before
            # registration") without saying how it is derived, so it is a date
            # the site records - never derived from another visit by guess.
            name = code.replace("_", " ").title()
            for fact in facts:
                for reference in (fact.timing.reference if fact.timing else None,
                                  fact.repeat.start if fact.repeat else None):
                    if reference is not None and code_for(reference.name) == code:
                        name = reference.name
            evidence_ids = self.cite(self.citations(facts), facts)
            self.anchors[code] = Anchor(
                code=code, protocol_label=name, display_name=name, anchor_type=code,
                derivation_rule={"source": "RECORDED", "defined_by": "reference"},
                status="RESOLVED", evidence_refs=evidence_ids)
            self.issue(
                "ANCHOR_SOURCE_NOT_STATED",
                f"{name!r} is used as a reference date but the protocol extract does not say how "
                "it is derived; it will be entered by the site. Confirm this is correct.",
                blocking=False, severity=Severity.WARNING, facts=facts, anchor_code=code)

    # --- scope -------------------------------------------------------------------

    def non_patient(self, fact: ScheduleFact) -> None:
        """Spec I8: trial/site/country rules go to a trial-level workflow."""
        if any(item.get("fact_id") == fact.id for item in self.extensions):
            return
        self.ref([fact], "TRIAL_LEVEL_RULE", code_for(fact.what))
        self.extensions.append({
            "type": "NON_PATIENT_RULE", "fact_id": fact.id, "scope": fact.scope,
            "what": fact.what, "kind": fact.kind,
            "citations": [citation.model_dump(mode="json") for citation in fact.evidence],
        })
        self.issue(
            "NON_PATIENT_RULE",
            f"{fact.what!r} is a {fact.scope.lower()}-level rule; it was recorded for the trial "
            "workflow and does not change any patient's schedule.",
            blocking=False, severity=Severity.INFO, facts=[fact], scope=fact.scope)

    # --- assemble ------------------------------------------------------------------

    def day_numbering(self) -> DayNumbering | None:
        stated = self.document.day_numbering
        if stated.day_1_anchor and stated.counting_convention:
            return DayNumbering(
                day_1_anchor=AnchorReference(code=code_for(stated.day_1_anchor)),
                counting_convention=stated.counting_convention)
        missing = [label for label, value in (
            ("which date is Day 1", stated.day_1_anchor),
            ("whether days are counted from Day 1 or Day 0", stated.counting_convention),
        ) if not value]
        self.extensions.append({
            "type": "UNRESOLVED_SCHEDULE_ORIGIN",
            "detail": "The protocol extract does not state " + " or ".join(missing)
                      + "; a reviewer must record it before any Day-N visit can be dated.",
        })
        return None

    def _entity_for(self, details: dict[str, Any]) -> tuple[str | None, UUID | None]:
        """The rule a reader issue is about, so it blocks THAT rule's readiness."""
        anchor = details.get("anchor_code")
        if anchor and anchor in self.anchors:
            return "ANCHOR", self.anchors[anchor].id
        fact_ids = list(details.get("fact_ids") or []) + ([details["fact_id"]] if details.get("fact_id") else [])
        for fact_id in fact_ids:
            for entry in self.rule_refs.get(self.merged_into.get(fact_id, fact_id), []):
                if entry.get("entity_id") and entry["entity_type"] in {"EVENT", "ANCHOR", "CONDITION"}:
                    return entry["entity_type"], UUID(entry["entity_id"])
                if entry.get("entity_id") and entry["entity_type"] == "ACTIVITY":
                    event = self.events.get(entry.get("event_code", ""))
                    if event is not None:
                        return "EVENT", event.id
        return None, None

    def reader_issues(self) -> None:
        known = {fact.id for fact in self.all_facts}
        for item in self.document.reader_issues:
            details = dict(item.details)
            fact_ids = [fact_id for fact_id in (list(details.get("fact_ids") or [])
                                                + ([details["fact_id"]] if details.get("fact_id") else []))]
            if fact_ids and not any(fact_id in known for fact_id in fact_ids):
                # About statements that are not in this schedule option.
                continue
            entity_type, entity_id = self._entity_for(details)
            if entity_type == "EVENT" and entity_id is not None and item.blocking:
                for event in self.events.values():
                    if event.id == entity_id:
                        event.requires_review = True
            self.issue(item.code, item.message, blocking=item.blocking,
                       severity=Severity.ERROR if item.blocking else Severity.WARNING,
                       entity_type=entity_type, entity_id=entity_id,
                       pages=item.pages, **details)

    def compile(self) -> ExtractionResult:
        self._separate_non_schedule()
        self.collect_names()
        self.build_dimensions()
        self.collect_cycle_plans()
        self.build_events()
        self.build_activities()
        self.build_conditions()
        self.link_triggered_events()
        self.build_anchors()
        self.build_processes()
        definitions = self.build_condition_definitions()
        blocks = self.build_blocks()
        self.finish_anchors()
        day_numbering = self.day_numbering()
        self.reader_issues()

        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(
                name=self.name, status=ScheduleStatus.EXTRACTED,
                description="Compiled from cited protocol statements; requires review.",
                day_numbering=day_numbering, extensions=self.extensions),
            arms=list(self.dimensions["ARM"].values()),
            dimensions=list(self.dimensions["PART"].values()),
            cohorts=list(self.dimensions["COHORT"].values()),
            populations=list(self.dimensions["POPULATION"].values()),
            anchors=list(self.anchors.values()),
            events=chronological(list(self.events.values()), list(self.anchors.values()), blocks),
            conditional_definitions=definitions,
            repeat_blocks=blocks,
            processes=self.processes,
            evidence=list(self.evidence.values()),
            claim_evidence=self.claims,
            validation_issues=self.issues,
        )
        # The validator keeps every OPEN extraction-origin issue and adds its
        # own re-derivable checks, so this is the complete list a reviewer sees.
        issues = ScheduleValidator().validate(schedule)
        from .statements import build_statement_ledger
        statements, relations, extra_issues = build_statement_ledger(self, schedule, issues)
        issues = issues + extra_issues
        schedule.validation_issues = issues
        trace = [{
            "node": "compile", "compiler": COMPILER_VERSION,
            "facts": [fact.model_dump(mode="json", by_alias=True) for fact in self.facts],
            "events": len(schedule.events), "issues": len(issues),
        }]
        accountability = {
            "compiler": COMPILER_VERSION,
            "statements": statements,
            "dispositions": L.disposition_counts(statements),
            "relations": relations,
        }
        return ExtractionResult(schedule=schedule, issues=issues, extraction_trace=trace,
                                accountability=accountability)


def compile_schedule(document: DocumentFacts, facts: list[ScheduleFact] | None = None, *,
                     name: str = "Schedule of Assessments") -> ExtractionResult:
    """Compile one schedule from facts. Deterministic; raises nothing for bad
    facts - they become issues on the draft."""
    return _Compiler(document, document.facts if facts is None else facts, name=name).compile()


def compile_document(document: DocumentFacts, *, schedule_name: str = "Schedule of Assessments",
                     ) -> list[tuple[dict[str, Any] | None, ExtractionResult]]:
    """One ``(option, result)`` pair per independent Schedule of Assessments.

    A single-schedule protocol yields one pair whose option is None, which is
    the contract ``ExtractionService.complete_many`` and ``/protocols/extract``
    already use.
    """
    options = document.schedule_options
    if len(options) <= 1:
        return [(None, compile_schedule(document, name=schedule_name))]
    keys = {option.id: {code_for(option.id), code_for(option.label)} for option in options}
    known = set().union(*keys.values())
    results: list[tuple[dict[str, Any] | None, ExtractionResult]] = []
    for option in options:
        selected = [
            fact for fact in document.facts
            if fact.schedule_option is None
            or code_for(fact.schedule_option) in keys[option.id]
            or code_for(fact.schedule_option) not in known
        ]
        result = compile_schedule(document, selected, name=f"{schedule_name} - {option.label}")
        stray = [fact for fact in selected
                 if fact.schedule_option and code_for(fact.schedule_option) not in known]
        if stray and result.schedule is not None:
            issue = ValidationIssue(
                issue_code="UNKNOWN_SCHEDULE_OPTION",
                message=f"{len(stray)} statement(s) name a schedule the protocol extract did not "
                        "list; they were included in every schedule for review.",
                severity=Severity.WARNING, blocking=False,
                details={"origin": ORIGIN, "fact_ids": [fact.id for fact in stray]})
            result.schedule.validation_issues.append(issue)
            result.issues.append(issue)
        results.append(({"id": option.id, "label": option.label,
                         "description": option.description or ""}, result))
    return results
