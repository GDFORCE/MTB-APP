"""Cited schedule facts: what the AI reader is allowed to say about a protocol.

Principle 1 of docs/schedule-engine-case-handling.md: the AI understands, the
engine calculates. The reader's whole output is a list of ``ScheduleFact``s -
small, atomic, cited statements of what the protocol *says* ("PET scan 12-16
weeks after completion of therapy", page 41). A fact never carries a patient
date, and nothing here is defaulted: a window, an inclusive boundary or a
counting convention the protocol does not state stays ``None``, and the
deterministic compiler (``compiler.py``) turns that ``None`` into an
UNRESOLVED rule a person must decide.

The timing ``pattern`` is a CLOSED list, one member per case in Part 4 of the
spec. A statement that fits none of them is an ``UNRESOLVED`` fact with its
quote - never a best guess squeezed into a nearby pattern.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FactModel(BaseModel):
    # Model replies are noisy, so an unknown key does not fail a whole batch -
    # but it is KEPT (``model_extra``) rather than dropped: a key the schema
    # does not know may carry an extra restriction, and IN-05 requires that to
    # surface as a visible issue (see ``unknown_fields``) instead of vanishing
    # through permissive parsing. Every field the compiler reads is typed.
    model_config = ConfigDict(extra="allow", populate_by_name=True)


#: Keys a model adds to annotate its own answer rather than to state protocol
#: meaning. They are preserved in the raw item but not reported as unknown
#: semantic fields. Anything else unknown is reported.
ANNOTATION_KEYS = frozenset({"confidence", "rationale", "explanation", "reasoning", "comment"})


def unknown_fields(model: BaseModel, path: str = "") -> list[str]:
    """Dotted paths of every key the schema does not define, at any depth.

    Used at the normalized boundary: the compiler reads only typed fields, so
    whatever is listed here was NOT interpreted and must be reviewed.
    """
    found: list[str] = []
    for key in sorted((model.model_extra or {}).keys()):
        if key not in ANNOTATION_KEYS:
            found.append(f"{path}{key}")
    for name in type(model).model_fields:
        value = getattr(model, name, None)
        items = value if isinstance(value, list) else [value]
        for index, item in enumerate(items):
            if isinstance(item, BaseModel):
                suffix = f"[{index}]" if isinstance(value, list) else ""
                found.extend(unknown_fields(item, f"{path}{name}{suffix}."))
    return found


TimeUnitName = Literal["MINUTE", "HOUR", "DAY", "WEEK", "MONTH", "YEAR"]

_UNIT_WORDS = {
    "MIN": "MINUTE", "MINS": "MINUTE", "MINUTE": "MINUTE", "MINUTES": "MINUTE",
    "H": "HOUR", "HR": "HOUR", "HRS": "HOUR", "HOUR": "HOUR", "HOURS": "HOUR",
    "D": "DAY", "DAY": "DAY", "DAYS": "DAY",
    "W": "WEEK", "WK": "WEEK", "WKS": "WEEK", "WEEK": "WEEK", "WEEKS": "WEEK",
    "M": "MONTH", "MO": "MONTH", "MONTH": "MONTH", "MONTHS": "MONTH",
    "Y": "YEAR", "YR": "YEAR", "YRS": "YEAR", "YEAR": "YEAR", "YEARS": "YEAR",
}
_AMOUNT_TEXT = re.compile(r"^\s*([+\-−]?\s*\d+)\s*([A-Za-z]+)\s*$")


class Duration(FactModel):
    """A signed amount of time exactly as the protocol states it."""

    value: int
    unit: TimeUnitName

    @model_validator(mode="before")
    @classmethod
    def from_text(cls, value: Any) -> Any:
        # The spec's own example writes amounts as "12 WEEK"; accept that and
        # "-7 days" so a formatting choice cannot drop a fact.
        if isinstance(value, str):
            match = _AMOUNT_TEXT.match(value)
            if not match:
                raise ValueError(f"cannot read an amount of time from {value!r}")
            number = int(match.group(1).replace("−", "-").replace(" ", ""))
            unit = _UNIT_WORDS.get(match.group(2).upper())
            if unit is None:
                raise ValueError(f"unknown time unit in {value!r}")
            return {"value": number, "unit": unit}
        if isinstance(value, dict) and isinstance(value.get("unit"), str):
            unit = _UNIT_WORDS.get(value["unit"].strip().upper())
            if unit is not None:
                return {**value, "unit": unit}
        return value


TimingPattern = Literal[
    "ABSOLUTE",               # A1  fixed calendar date
    "PROTOCOL_DAY",           # A2/A3  "Day 8", "Day -7"
    "OFFSET",                 # A4/A5  "+4 weeks after registration", "7 days before first dose"
    "RANGE",                  # A7  "12-16 weeks after completion of therapy"
    "WITHIN_BEFORE",          # A8  "within 28 days before registration"
    "WITHIN_AFTER",           # "within 14 days after surgery"
    "NO_LATER_THAN",          # A9  "no later than 30 days after last dose"
    "NO_EARLIER_THAN",        # "at least 12 weeks after the end of RT" (a minimum delay)
    "TRIGGERED",              # A10 "30 days after disease progression"
    "APPROXIMATE",            # A11 "approximately 3 months after treatment"
    "CYCLE_DAY",              # F4  "Days 1, 8 and 15 of each cycle"
    "REPEAT",                 # E1-E7 "every 3 months for 2 years"
    "PHASED_REPEAT",          # E9  "every 3 months to year 2, then every 6 months..."
    "HOLD_RESUME",            # I1-I3 "hold until <= grade 1, then resume"
    "DISCONTINUE",            # I4  "discontinue for progression"
    "REPEAT_UNTIL_RESOLVED",  # I5  "repeat CBC weekly until ANC >= 1500"
    "ON_DEMAND",              # L1  "unscheduled visit as clinically indicated"
    # Clearly stated, but no executable operator exists yet. They compile to an
    # UNSUPPORTED constraint node that blocks the rule and its dependents.
    "WEEKDAY_REPEAT",         # "5 days per week x 30 fractions", "Monday to Friday"
    "BUSINESS_DAYS",          # "within 3 working days"
    "HOLIDAY_RULE",           # "sessions missed for public holidays are added at the end"
    "UNRESOLVED",             # A12 anything the reader could not map
]

#: Patterns with no executable operator yet: name -> the source operator the
#: UNSUPPORTED node records (capabilities.py reports it as UNSUPPORTED_OPERATOR).
#: WEEKDAY_REPEAT is executable since WP3 (``Event.calendar_recurrence``);
#: HOLIDAY_RULE is executable only as the holiday policy of a weekday series
#: it modifies, and stays UNSUPPORTED on its own.
UNSUPPORTED_PATTERNS = {
    "BUSINESS_DAYS": "BUSINESS_DAY_OFFSET",
    "HOLIDAY_RULE": "HOLIDAY_ADJUSTMENT",
}

WeekdayName = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_WEEKDAY_WORDS = {
    "MON": "MON", "MONDAY": "MON", "TUE": "TUE", "TUES": "TUE", "TUESDAY": "TUE",
    "WED": "WED", "WEDNESDAY": "WED", "THU": "THU", "THUR": "THU", "THURS": "THU", "THURSDAY": "THU",
    "FRI": "FRI", "FRIDAY": "FRI", "SAT": "SAT", "SATURDAY": "SAT", "SUN": "SUN", "SUNDAY": "SUN",
}
ProcessKindName = Literal["INTERVENTION", "FOLLOW_UP", "ASSESSMENT"]

#: Patterns that describe a rule reacting to a clinical condition rather than
#: a visit's own date.
CONDITION_PATTERNS = frozenset({"HOLD_RESUME", "DISCONTINUE", "REPEAT_UNTIL_RESOLVED"})

FactKind = Literal["visit", "activity", "anchor", "condition", "milestone"]
RuleScope = Literal["PATIENT", "ARM", "COHORT", "SITE", "COUNTRY", "TRIAL"]
#: Rules at these scopes govern the trial or a site, never one patient's visits
#: (spec I8). The compiler records them but never turns them into patient actions.
NON_PATIENT_SCOPES = frozenset({"SITE", "COUNTRY", "TRIAL"})


EvidenceVerification = Literal["TEXT_MATCHED", "UNVERIFIED", "REJECTED"]


class Citation(FactModel):
    page: int = Field(gt=0)
    section: str | None = None
    table: str | None = None
    quote: str = Field(min_length=1)
    #: Set ONLY by the reader's own check against the page text, never taken
    #: from the model (a reply claiming "TEXT_MATCHED" is overwritten).
    #: TEXT_MATCHED: the words are on the cited page. UNVERIFIED: the page has
    #: no text layer (scan/image) so the quote could not be checked.
    #: REJECTED: the page has text and the quote is not on it (or the page
    #: does not exist). None: not checked (facts built directly in code).
    verification: EvidenceVerification | None = None


class Criterion(FactModel):
    """A machine-checkable clinical threshold ("ANC >= 1500"), only when the
    protocol states one. Free text stays in the fact's quote."""

    field: str = Field(min_length=1)
    operator: Literal["EQUALS", "NOT_EQUALS", "GT", "GTE", "LT", "LTE"]
    value: Any


class ReferenceTo(FactModel):
    """What a timing counts from. ``kind`` None lets the compiler look the name
    up among the anchors, visits and conditions it knows."""

    name: str = Field(min_length=1)
    kind: Literal["ANCHOR", "EVENT", "CONDITION", "ACTIVITY"] | None = None
    occurrence: Literal["FIRST", "LAST"] | None = None
    # Spec C6/D2: PLANNED_OR_ACTUAL only when the protocol wording times the
    # rule from the planned / scheduled date of the anchor ("within 14 days
    # before the planned surgery", a pre-operative assessment ahead of the
    # scheduled operation). None means the protocol says nothing about it and
    # the rule waits for the ACTUAL date - the safe default.
    accepts: Literal["ACTUAL", "PLANNED_OR_ACTUAL"] | None = None

    @model_validator(mode="before")
    @classmethod
    def from_text(cls, value: Any) -> Any:
        return {"name": value} if isinstance(value, str) else value

    @field_validator("accepts", mode="before")
    @classmethod
    def normalise_accepts(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().upper().replace(" ", "_")
            return {"PLANNED": "PLANNED_OR_ACTUAL", "": None}.get(value, value)
        return value


class CycleSpec(FactModel):
    """Which treatment cycle a CYCLE_DAY fact sits in."""

    start_anchor: str = Field(min_length=1)  # e.g. "Cycle 1 Day 1"
    length: Duration | None = None
    # WHICH single cycle the rule is for ("C3D1" -> 3); None = every cycle.
    # The NUMBER of cycles belongs in repeat.end (COUNT), never here.
    number: int | None = Field(default=None, gt=0)


class RepeatEnd(FactModel):
    type: Literal["COUNT", "UNTIL_DATE", "UNTIL_EVENT", "UNTIL_CONDITION",
                  "FOR_DURATION", "OPEN_ENDED"]
    count: int | None = Field(default=None, gt=0)
    # Named apart from the ``date`` type so the annotation stays resolvable.
    until_date: date | None = Field(default=None, alias="date")
    event: str | None = None
    condition: str | None = None
    duration: Duration | None = None
    reference: ReferenceTo | None = None  # FOR_DURATION: "2 years after <reference>"
    inclusive: bool | None = None  # FOR_DURATION: is the boundary visit included?


class RepeatSpec(FactModel):
    interval: Duration
    start: ReferenceTo | None = None  # defaults to the timing reference
    start_offset: Duration | None = None
    # Whether an occurrence falls ON the start itself. Never assumed.
    includes_start: bool | None = None
    end: RepeatEnd | None = None


class RepeatSegment(FactModel):
    """One cadence of a phased follow-up plan (spec E9).

    Segments follow one another: segment 1 starts at ``start`` (None = the
    plan reference itself) and every later segment starts where the previous
    one ended, so a later ``start`` is only a cross-check. A segment ends at
    ``end`` (an offset from the plan reference: "to year 2") or after
    ``count`` visits; the final segment may state neither, and then the fact's
    ``repeat.end`` says what ends the plan.
    """

    interval: Duration
    start: Duration | None = None
    end: Duration | None = None
    end_inclusive: bool | None = None
    includes_start: bool | None = None
    count: int | None = Field(default=None, gt=0)


class OccurrenceSelector(FactModel):
    """Which cycles an activity is done in (spec F5), only as stated.

    ``numbers``: the protocol lists them ("Cycle 1 only" -> [1], "Cycles 1-3"
    -> [1, 2, 3]). ``parity``: "on even cycles". ``every`` + ``starting_at``:
    "every second cycle starting with Cycle 2". An every-N rule whose first
    cycle is not stated is ambiguous (C1, C3, ... or C2, C4, ...) and stays an
    unresolved note.
    """

    numbers: list[int] = Field(default_factory=list)
    parity: Literal["EVEN", "ODD"] | None = None
    every: int | None = Field(default=None, gt=0)
    starting_at: int | None = Field(default=None, gt=0)

    @field_validator("numbers", mode="before")
    @classmethod
    def single_number(cls, value: Any) -> Any:
        if value is None:
            return []
        return [value] if isinstance(value, int) else value

    @field_validator("parity", mode="before")
    @classmethod
    def normalise_parity(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper() or None
        return value


class WindowSpec(FactModel):
    """Allowed deviation around a target. Each half is independently nullable
    and never defaulted: "may be 3 days late" states ``after`` only."""

    before: Duration | None = None
    after: Duration | None = None


class ConditionSpec(FactModel):
    """What a clinical condition does to the schedule."""

    name: str = Field(min_length=1)
    trigger: Criterion | None = None
    # Visits or treatment blocks affected.
    targets: list[str] = Field(default_factory=list)
    effect: Literal["PAUSE", "STOP", "CANCEL", "ADD", "MANUAL_REVIEW", "REPEAT"] | None = None
    adds: list[str] = Field(default_factory=list)
    resume_when: Criterion | None = None
    resume_mode: Literal["NOMINAL", "ACTUAL_RESUME", "MANUAL"] | None = None
    resolution: Criterion | None = None
    interval: Duration | None = None
    #: Treatment components this rule acts on ("hold drug B only"), when the
    #: protocol names one component rather than the whole treatment.
    target_components: list[str] = Field(default_factory=list)
    #: How the held component continues, ONLY as stated (design s5).
    hold_resume_policy: Literal["NOMINAL", "SHIFT_BY_HOLD", "RESTART_FROM_RESUME",
                                "WAIT_FOR_DECISION"] | None = None
    resume_trigger: Literal["EXPLICIT_RESUME", "CONDITION_RESOLUTION"] | None = None
    #: A participation decision - three distinct things, never merged:
    #: stop all treatment (follow-up continues), withdraw consent (everything
    #: stops), end follow-up (only follow-up completes).
    participation: Literal["WITHDRAWAL_FROM_TREATMENT", "WITHDRAWAL_OF_CONSENT",
                           "END_OF_FOLLOW_UP"] | None = None
    #: A threshold the model wrote as prose ("ANC >= 1.0 x 10^9/L and platelets
    #: > 75 x 10^9/L") instead of one field/operator/value. Kept verbatim for the
    #: reviewer; the condition then acts on the PI's confirmation, as when no
    #: threshold is stated.
    criteria_text: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def prose_criteria(cls, value: Any) -> Any:
        # Dropping the whole rule over a formatting choice lost every toxicity
        # hold in a real protocol (NCT01757288); the rule itself is still
        # well-defined without a machine-checkable threshold.
        if not isinstance(value, dict):
            return value
        texts = dict(value.get("criteria_text") or {})
        cleaned = dict(value)
        for key in ("trigger", "resume_when", "resolution"):
            if isinstance(cleaned.get(key), str):
                texts[key] = cleaned.pop(key)
        cleaned["criteria_text"] = texts
        return cleaned


class FactTiming(FactModel):
    pattern: TimingPattern
    reference: ReferenceTo | None = Field(default=None, alias="from")
    value: date | None = None  # ABSOLUTE
    day: int | None = None  # PROTOCOL_DAY / CYCLE_DAY
    offset: Duration | None = None  # OFFSET / TRIGGERED / APPROXIMATE / NO_LATER_THAN
    start: Duration | None = None  # RANGE
    end: Duration | None = None  # RANGE
    duration: Duration | None = None  # WITHIN_* / NO_LATER_THAN
    cycle: CycleSpec | None = None
    segments: list[RepeatSegment] = Field(default_factory=list)
    reason: str | None = None  # UNRESOLVED: why it could not be mapped
    #: Whether the boundary day itself is allowed, ONLY as the protocol states
    #: it ("at least" = inclusive lower, "more than" = exclusive). Needed when a
    #: bound is combined with other restrictions; never defaulted.
    lower_inclusive: bool | None = None
    upper_inclusive: bool | None = None
    #: WEEKDAY_REPEAT: the named weekdays ("Monday to Friday" -> MON..FRI) and
    #: every n-th week. Only as stated; "5 days per week" alone names no days.
    weekdays: list[WeekdayName] = Field(default_factory=list)
    interval_weeks: int | None = Field(default=None, gt=0)

    @field_validator("weekdays", mode="before")
    @classmethod
    def normalise_weekdays(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            return [_WEEKDAY_WORDS.get(str(item).strip().upper().rstrip("."), item) for item in value]
        return value

    @field_validator("pattern", mode="before")
    @classmethod
    def normalise_pattern(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().upper().replace(" ", "_").replace("-", "_")
            return {"WITHIN": "WITHIN_BEFORE"}.get(value, value)  # bare WITHIN is ambiguous
        return value


class AppliesTo(FactModel):
    arms: list[str] = Field(default_factory=list)  # empty = every arm
    parts: list[str] = Field(default_factory=list)
    cohorts: list[str] = Field(default_factory=list)
    populations: list[str] = Field(default_factory=list)
    attributes: list[Criterion] = Field(default_factory=list)

    @field_validator("arms", "parts", "cohorts", "populations", mode="before")
    @classmethod
    def all_means_everyone(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            return [item for item in value if str(item).strip().upper() not in {"ALL", "ANY", ""}]
        return value


class AnchorSource(FactModel):
    """Where an anchor's date comes from."""

    type: Literal["RECORDED", "EVENT", "CONDITION"]
    event: str | None = None
    selection: Literal["FIRST", "LAST"] | None = None
    condition: str | None = None


#: ACTUAL_PREVIOUS_CYCLE_START is the cycle wording of ACTUAL_PREVIOUS_EVENT
#: ("each cycle starts 21 days after Day 1 of the previous cycle"); both are
#: accepted and mean the same for a treatment block.
DependencyModeName = Literal["NOMINAL", "ACTUAL_PREVIOUS_EVENT", "ACTUAL_PREVIOUS_CYCLE_START",
                             "MANUAL", "UNCLEAR"]


def _normalise_mode(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().upper().replace(" ", "_") or None
    return value


class ScheduleFact(FactModel):
    """One cited, atomic statement about the protocol's schedule."""

    id: str | None = None
    what: str = Field(min_length=1)
    kind: FactKind
    code: str | None = None
    event_type: str | None = None
    visit_mode: str | None = None
    timing: FactTiming | None = None
    window: WindowSpec | None = None
    repeat: RepeatSpec | None = None
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    # visit facts: activities performed at this visit; activity facts: the
    # visits the activity is performed at.
    activities: list[str] = Field(default_factory=list)
    at_visits: list[str] = Field(default_factory=list)
    occurrence_note: str | None = None  # "every second cycle", "Cycle 1 only"
    # The same rule in checkable form, only when the protocol states it
    # unambiguously; otherwise the note stays an unresolved qualifier.
    occurrences: OccurrenceSelector | None = None
    block: str | None = None  # treatment block this visit belongs to
    depends_on: str | None = None
    dependency_mode: DependencyModeName | None = None
    anchor: AnchorSource | None = None  # anchor / milestone facts
    condition: ConditionSpec | None = None  # condition facts
    scope: RuleScope = "PATIENT"
    schedule_option: str | None = None
    evidence: list[Citation] = Field(min_length=1)
    # Two readings of the same thing that disagree share a conflict group
    # (spec O2). The compiler never chooses between them.
    conflict_group: str | None = None
    conflicts_with: list[str] = Field(default_factory=list)
    #: NON_SCHEDULE marks a statement that is not about when a patient visit or
    #: assessment happens (a reporting duty, data submission, dose
    #: recalculation, statistical analysis, QA review). It stays visible in the
    #: statement ledger as OUT_OF_SCOPE_NON_SCHEDULE with its reason; it never
    #: becomes a visit. None is treated as SCHEDULE.
    statement_type: Literal["SCHEDULE", "NON_SCHEDULE"] | None = None
    non_schedule_reason: str | None = None
    #: Which occurrence of a repeated activity this statement is about
    #: ("pre-treatment", "during consolidation", "follow-up"), when the
    #: protocol makes it clear. Two statements about one activity in different
    #: occurrence contexts are different occurrences, not a conflict.
    occurrence_context: str | None = None
    #: Statements the protocol offers as permitted ALTERNATIVES for one
    #: occurrence ("either within 7 days of A or within 3 days of B") share a
    #: label. Never used for a contradiction: that is ``conflict_group``.
    alternative_group: str | None = None
    #: The treatment component / pathway this obligation belongs to (one drug,
    #: radiotherapy, survival follow-up), and what kind it is. Each named
    #: component becomes a process with its own state; kind is never guessed.
    component: str | None = None
    component_kind: ProcessKindName | None = None
    #: Weekday-series policies, ONLY from explicit wording; None = not stated.
    count_basis: Literal["SLOTS", "DELIVERED"] | None = None
    holiday_policy: Literal["SKIP", "SHIFT_NEXT_BUSINESS_DAY"] | None = None
    missed_policy: Literal["APPEND_REPLACEMENT", "NO_REPLACEMENT"] | None = None
    #: Written by deterministic reconciliation (never by the model): how this
    #: fact was normalised - anchor aliases merged, grouping, source cells.
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("statement_type", mode="before")
    @classmethod
    def normalise_statement_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().upper().replace("-", "_").replace(" ", "_")
            return {"": None, "NONSCHEDULE": "NON_SCHEDULE"}.get(value, value)
        return value

    @field_validator("kind", mode="before")
    @classmethod
    def normalise_kind(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("dependency_mode", mode="before")
    @classmethod
    def normalise_dependency_mode(cls, value: Any) -> Any:
        return _normalise_mode(value)

    @field_validator("scope", mode="before")
    @classmethod
    def normalise_scope(cls, value: Any) -> Any:
        if value is None:
            return "PATIENT"
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("evidence", mode="before")
    @classmethod
    def single_citation(cls, value: Any) -> Any:
        return [value] if isinstance(value, dict) else value


class ScheduleOption(FactModel):
    """One independent Schedule of Assessments (e.g. a substudy)."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str | None = None


class DayNumberingFact(FactModel):
    """Which anchor is Day 1 and how days are counted - only as stated."""

    day_1_anchor: str | None = None
    counting_convention: Literal["CLINICAL_DAY", "ELAPSED_DURATION"] | None = None
    evidence: list[Citation] = Field(default_factory=list)


class DimensionFact(FactModel):
    name: str = Field(min_length=1)
    code: str | None = None
    description: str | None = None
    parent: str | None = None  # cohort inside a part, etc. (spec H4)
    # What kind of group the parent is. None lets the compiler look the name up
    # among the arms, parts, cohorts and populations; an unknown or ambiguous
    # parent is reported, never guessed.
    parent_type: Literal["ARM", "PART", "COHORT", "POPULATION"] | None = None
    evidence: list[Citation] = Field(default_factory=list)

    @field_validator("parent_type", mode="before")
    @classmethod
    def normalise_parent_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper() or None
        return value


class BlockFact(FactModel):
    """A repeating treatment block and how later cycles are timed (spec F1-F3)."""

    name: str = Field(min_length=1)
    dependency_mode: DependencyModeName | None = None
    resume_mode: Literal["NOMINAL", "ACTUAL_RESUME", "MANUAL", "UNCLEAR"] | None = None
    evidence: list[Citation] = Field(default_factory=list)

    @field_validator("dependency_mode", mode="before")
    @classmethod
    def normalise_dependency_mode(cls, value: Any) -> Any:
        return _normalise_mode(value)


class TrialMetadata(FactModel):
    """Add-Trial form fields. Unstated values stay empty, never invented."""

    # Form pre-fill only, no schedule meaning: extra keys are dropped here so
    # the legacy ExtractedTrialDetails contract stays exact.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    ctri_number: str = ""
    title: str = ""
    phase: str = ""
    indications: list[str] = Field(default_factory=list)
    drug: str = ""
    duration: str = ""
    target_enrollment: int | None = None
    total_visits: int | None = None

    @field_validator("ctri_number", "title", "phase", "drug", "duration", mode="before")
    @classmethod
    def none_is_empty(cls, value: Any) -> Any:
        return "" if value is None else value


class ReaderIssue(FactModel):
    """Something the reader itself could not do (a batch it could not parse,
    a quote not found on its cited page). Carried into the schedule so a
    reviewer sees it; never silently dropped."""

    code: str
    message: str
    pages: list[int] = Field(default_factory=list)
    blocking: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class DocumentFacts(FactModel):
    """Everything the reader found in one protocol."""

    trial: TrialMetadata = Field(default_factory=TrialMetadata)
    schedule_options: list[ScheduleOption] = Field(default_factory=list)
    day_numbering: DayNumberingFact = Field(default_factory=DayNumberingFact)
    arms: list[DimensionFact] = Field(default_factory=list)
    # Study parts that cohorts nest inside ("Part A -> Cohort 1 or 2").
    parts: list[DimensionFact] = Field(default_factory=list)
    cohorts: list[DimensionFact] = Field(default_factory=list)
    populations: list[DimensionFact] = Field(default_factory=list)
    blocks: list[BlockFact] = Field(default_factory=list)
    facts: list[ScheduleFact] = Field(default_factory=list)
    reader_issues: list[ReaderIssue] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    #: The accountability ledgers the reader and reconciliation build
    #: (``ledger.py``): source coverage, rejected raw items, footnotes / table
    #: cells, anchor reconciliation, duplicates removed. Empty for facts built
    #: directly in code (compiler unit tests), which then have no coverage claim.
    accountability: dict[str, Any] = Field(default_factory=dict)
