from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from .condition import ConditionExpression
from .timing import (
    AnchorReference,
    Constraint,
    PositiveTemporalAmount,
    StrictModel,
    TemporalReference,
    TemporalAmount,
    TimingExpression,
    Window,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleStatus(StrEnum):
    DRAFT = "DRAFT"
    EXTRACTED = "EXTRACTED"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    # Doc 9 s34: finer-grained than APPROVED/SUPERSEDED alone, for a version
    # that is approved but should no longer take NEW patients while existing
    # patients stay on it. AD-6: members only for now - no service transitions
    # these automatically yet (Phase 1 follow-up; see current_enrollable_
    # schedule_version's single resolution path in schedule_service.py, which
    # any such transition should build on rather than duplicate).
    CURRENT_FOR_NEW_ENROLLMENT = "CURRENT_FOR_NEW_ENROLLMENT"
    HISTORICAL_ACTIVE = "HISTORICAL_ACTIVE"
    RETIRED_FOR_NEW_ENROLLMENT = "RETIRED_FOR_NEW_ENROLLMENT"


class InterpretationStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    CONFIRMED = "CONFIRMED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"


class Requiredness(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    CONDITIONAL = "CONDITIONAL"
    RECOMMENDED = "RECOMMENDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"


class EventType(StrEnum):
    """The event types the projection layer knows how to present - reminder
    wording, attendance expectation, calendar treatment (doc 10 s2-s3).

    AD-6: closed but escapable. ``Event.event_type`` itself stays a plain
    ``str`` (not this enum) so an unrecognised value never fails validation -
    doc 10 s3 requires it surface as a non-blocking review warning instead,
    which is what ``validator.py`` already does by comparing against
    ``frozenset(EventType)``. A protocol may legitimately name something this
    list has never seen; refusing approval over vocabulary would stop real
    work for no clinical reason, while presenting an unknown type as an
    ordinary site visit could tell a patient to travel. OTHER is the escape
    member for exactly that case.
    """

    SITE_VISIT = "SITE_VISIT"
    TELEPHONE_CONTACT = "TELEPHONE_CONTACT"
    HOME_VISIT = "HOME_VISIT"
    ASSESSMENT = "ASSESSMENT"
    IMAGING = "IMAGING"
    LABORATORY = "LABORATORY"
    INPATIENT_ADMISSION = "INPATIENT_ADMISSION"
    INPATIENT_DISCHARGE = "INPATIENT_DISCHARGE"
    UNSCHEDULED = "UNSCHEDULED"
    SAFETY_ASSESSMENT = "SAFETY_ASSESSMENT"
    TREATMENT = "TREATMENT"
    PROCEDURE = "PROCEDURE"
    REMOTE = "REMOTE"
    # Generic labels are accepted because they are presentation-SAFE: they
    # promise nothing about how the patient attends, so the mode fields decide
    # that. It is the specific-but-unknown type that needs a reviewer.
    VISIT = "VISIT"
    CONTACT = "CONTACT"
    OTHER = "OTHER"


#: HOW a scheduled occasion happens, once EventType has said what kind of
#: occasion it is - a distinct question ("a CT scan the patient travels to" is
#: IMAGING *and* CLINIC, not a choice between them). Kept separate from
#: EventType and defined exactly once, imported everywhere it is written
#: (extraction) or read (reminder/calendar projection), because this file's
#: own history already has one incident of a second, drifted vocabulary for
#: the same concept living in schedule_projection.py - see that module's
#: docstring. Plain string on Event.visit_mode, not this dict's key set, is
#: still what gets stored: an unrecognised mode must surface for review, not
#: fail validation (AD-6's closed-but-escapable pattern).
VISIT_MODE_WORDING: dict[str, str] = {
    "CLINIC": "Your study visit is scheduled",
    "TELEPHONE": "Your study team is expected to contact you",
    "VIDEO": "Your video study visit is scheduled",
    "HOME": "A study-related home visit is scheduled",
    "HOME_NURSE": "A study nurse home visit is scheduled",
    "REMOTE": "Your remote study visit is scheduled",
    "IMAGING_CENTRE": "Your imaging appointment is scheduled",
    "LABORATORY": "Your laboratory appointment is scheduled",
    "PHARMACY": "Your pharmacy appointment is scheduled",
}

#: Modes where the patient is expected to travel somewhere, as opposed to
#: being contacted or visited. Drives the reminder title (doc 10 s22: "Please
#: travel to the hospital" must never reach a telephone-contact patient).
VISIT_MODE_REQUIRES_ATTENDANCE = frozenset({"CLINIC", "IMAGING_CENTRE", "LABORATORY", "PHARMACY"})


class DependencyMode(StrEnum):
    NOMINAL = "NOMINAL"
    ACTUAL_PREVIOUS_EVENT = "ACTUAL_PREVIOUS_EVENT"
    MANUAL = "MANUAL"
    UNCLEAR = "UNCLEAR"


class CycleDependencyMode(StrEnum):
    """How each treatment cycle's start is dated (doc F1-F3).

    NOMINAL: every cycle sits on the grid counted from Cycle 1 Day 1, so a
    delayed Cycle 2 does not move Cycle 3. ACTUAL_PREVIOUS_CYCLE_START: each
    cycle starts one cycle length after the day the previous cycle ACTUALLY
    started, so a later cycle cannot be dated until the one before it happened.
    MANUAL: a person enters each cycle start. UNCLEAR: the protocol does not
    say, which blocks approval - the engine never picks one.
    """

    NOMINAL = "NOMINAL"
    ACTUAL_PREVIOUS_CYCLE_START = "ACTUAL_PREVIOUS_CYCLE_START"
    MANUAL = "MANUAL"
    UNCLEAR = "UNCLEAR"


#: The repeat-block mode that states the same thing as each cycle mode. A cycle
#: block carries both; disagreement is two readings of one protocol rule.
CYCLE_BLOCK_DEPENDENCY_MODES: dict[CycleDependencyMode, DependencyMode] = {
    CycleDependencyMode.NOMINAL: DependencyMode.NOMINAL,
    CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START: DependencyMode.ACTUAL_PREVIOUS_EVENT,
    CycleDependencyMode.MANUAL: DependencyMode.MANUAL,
    CycleDependencyMode.UNCLEAR: DependencyMode.UNCLEAR,
}


def cycle_start_anchor_code(plan_code: str, number: int) -> str:
    """The addressable anchor for one cycle's start, e.g. ``CYCLE_3_START``."""
    return f"{plan_code}_{number}_START"


class QualifierScope(StrEnum):
    CELL = "CELL"
    ROW = "ROW"
    COLUMN = "COLUMN"
    VISIT = "VISIT"
    ACTIVITY = "ACTIVITY"
    # AD-6: "BLOCK" alone conflated two different things a footnote can scope
    # to - a repeat/pause block spanning several visits (VISIT_BLOCK) versus a
    # rule that governs the whole schedule (SCHEDULE_BLOCK). Split rather than
    # left as one member a reader has to guess between.
    VISIT_BLOCK = "VISIT_BLOCK"
    SCHEDULE_BLOCK = "SCHEDULE_BLOCK"
    ARM = "ARM"
    COHORT = "COHORT"
    SUBSTUDY = "SUBSTUDY"
    PERIOD = "PERIOD"
    # Doc s5: a footnote can restrict a population rather than an arm.
    POPULATION = "POPULATION"
    GLOBAL = "GLOBAL"


class QualifierCategory(StrEnum):
    TIMING = "TIMING"
    WINDOW = "WINDOW"
    CONDITION = "CONDITION"
    APPLICABILITY = "APPLICABILITY"
    OPTIONALITY = "OPTIONALITY"
    EXCEPTION = "EXCEPTION"
    REPEAT = "REPEAT"
    STOP = "STOP"
    SUBSTITUTION = "SUBSTITUTION"
    SEQUENCE = "SEQUENCE"
    DETAIL = "DETAIL"
    POPULATION_RESTRICTION = "POPULATION_RESTRICTION"
    # Doc 8: a footnote can restrict eligibility to one arm/cohort/country
    # specifically, distinct from POPULATION_RESTRICTION's broader clinical
    # population (e.g. "renal impairment") and from applicability's own
    # dimension/value rules on the event itself.
    ARM_RESTRICTION = "ARM_RESTRICTION"
    COHORT_RESTRICTION = "COHORT_RESTRICTION"
    COUNTRY_RESTRICTION = "COUNTRY_RESTRICTION"
    # A reviewer determined the marker does not govern THIS schedule. It is
    # a decision that was made, which is why it is not the same as UNRESOLVED.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OTHER = "OTHER"
    UNRESOLVED = "UNRESOLVED"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class IssueStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    ACCEPTED = "ACCEPTED"
    DISMISSED = "DISMISSED"


class ConditionState(StrEnum):
    """Lifecycle of one protocol condition for one patient (requirement doc 2 s14).

    A condition is never a boolean checkbox: it carries a state, an occurrence date
    that can anchor resulting visits, and a resolution date that stops repeats.
    """

    NOT_OCCURRED = "NOT_OCCURRED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    ACTIVE = "ACTIVE"
    # A condition that resolved and has come back. It ACTS exactly like ACTIVE -
    # the same visits are generated - but stays distinguishable, because "the
    # toxicity returned" is a different clinical picture from "the patient had a
    # toxicity", and a reviewer deciding on dose reduction needs to see which.
    RECURRED = "RECURRED"
    RESOLVED = "RESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CANCELLED = "CANCELLED"


class BlockState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


#: AD-4a: one owner per status member, stated once so it is reviewable rather
#: than an implicit discipline scattered across the evaluator.
#:
#: PROTOCOL-LOGIC members are computed by ScheduleEvaluator from the schedule
#: definition and previously recorded facts - no code outside the evaluator
#: writes them: RESOLVED, AVAILABLE_ON_DEMAND, WAITING_FOR_ANCHOR,
#: WAITING_FOR_CONDITION, WAITING_FOR_ASSIGNMENT, NOT_APPLICABLE, BLOCKED, PAUSED, UNRESOLVED,
#: CANCELLED, UPCOMING, IN_CONFINEMENT, EXTENDED, DISCHARGE_PLANNED.
#:
#: OPERATIONAL-REALITY members are facts a human/site recorded - the evaluator
#: only ever REFLECTS one it already finds in `PatientContext` (see
#: evaluator.py's `_evaluate_activity`: COMPLETED/NOT_DONE come from
#: `context.activity_actuals`/`activity_statuses`, populated from
#: `PatientScheduleService.record_activity`'s persisted `PatientActivityRecord`
#: rows), it never originates one: COMPLETED, MISSED, NOT_DONE, DISCHARGED.
#: `PatientEventStatus.COMPLETED`/`MISSED` are written directly by the
#: occurrence-recording API (`record_patient_occurrence`), never by the
#: evaluator - confirmed by grep: no `PatientEventStatus.COMPLETED`/`MISSED`
#: literal exists anywhere in evaluator.py.


class ConfinementStatus(StrEnum):
    """State of one continuous inpatient stay (requirement doc 6 section 36).

    The distinction that matters clinically is: not yet arrived, currently confined,
    actually discharged. A planned discharge date passing is not a discharge.
    """

    UPCOMING = "UPCOMING"
    IN_CONFINEMENT = "IN_CONFINEMENT"
    EXTENDED = "EXTENDED"
    # A planned discharge date has passed with no actual discharge recorded
    # yet - clinically distinct from an ordinary IN_CONFINEMENT day and from
    # EXTENDED (which means a conditional rule pushed the plan itself out).
    DISCHARGE_PLANNED = "DISCHARGE_PLANNED"
    DISCHARGED = "DISCHARGED"
    CANCELLED = "CANCELLED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    WAITING_FOR_ANCHOR = "WAITING_FOR_ANCHOR"


class PatientActivityStatus(StrEnum):
    RESOLVED = "RESOLVED"
    WAITING_FOR_ANCHOR = "WAITING_FOR_ANCHOR"
    WAITING_FOR_CONDITION = "WAITING_FOR_CONDITION"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"
    COMPLETED = "COMPLETED"
    NOT_DONE = "NOT_DONE"


class PatientEventStatus(StrEnum):
    RESOLVED = "RESOLVED"
    # A protocol-defined unscheduled visit that exists but is not due. Distinct
    # from NOT_APPLICABLE, which means the patient will never have it, and from
    # WAITING_FOR_CONDITION, which means the protocol is still waiting on a
    # clinical trigger (requirement doc 10 s13-s15).
    AVAILABLE_ON_DEMAND = "AVAILABLE_ON_DEMAND"
    WAITING_FOR_ANCHOR = "WAITING_FOR_ANCHOR"
    WAITING_FOR_CONDITION = "WAITING_FOR_CONDITION"
    # Doc H2: the visit depends only on an assignment the patient does not have
    # yet (arm, cohort, population, or another study dimension - e.g. before a
    # late randomisation). Like every WAITING_* status it has no date, no
    # reminder and can never be overdue; it is kept apart from
    # WAITING_FOR_CONDITION because the action it needs is a randomisation or
    # assignment, not a clinical event.
    WAITING_FOR_ASSIGNMENT = "WAITING_FOR_ASSIGNMENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    UNRESOLVED = "UNRESOLVED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"


class UnscheduledOccurrence(StrictModel):
    """One unscheduled visit a site actually created (requirement doc 10 s14).

    A reason is required: an unscheduled visit with no stated cause is not
    reviewable later, and the protocol defines these by their cause.
    """

    event_code: str = Field(min_length=1)
    occurred_on: date | datetime
    reason: str = Field(min_length=1)
    visit_mode: str | None = None
    created_by: UUID | None = None
    recorded_at: datetime = Field(default_factory=utc_now)


class DayNumbering(StrictModel):
    day_1_anchor: AnchorReference
    counting_convention: Literal["CLINICAL_DAY", "ELAPSED_DURATION"] = "CLINICAL_DAY"


class ScheduleMetadata(StrictModel):
    name: str = Field(min_length=1)
    description: str | None = None
    schedule_type: str = "PRIMARY"
    protocol_version_id: UUID | None = None
    version_number: int = Field(default=1, gt=0)
    status: ScheduleStatus = ScheduleStatus.DRAFT
    # Doc 9 s8. When this version starts applying to NEW enrolments, which is not
    # the same moment as approval. None means "in force as soon as approved".
    effective_from: date | None = None
    day_numbering: DayNumbering | None = None
    extensions: list[dict[str, object]] = Field(default_factory=list)


class Epoch(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str | None = None
    sequence_number: int | None = None
    timing: TimingExpression | None = None
    applicability: list[ConditionExpression] = Field(default_factory=list)
    evidence_refs: list[UUID] = Field(default_factory=list)


class StudyDimension(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str | None = None
    criteria: ConditionExpression | None = None
    # Doc 8 s10: protocols nest their groups - "Part A, Cohort 1". Recording the
    # parent is what lets enrolment offer only the cohorts that exist inside the
    # part already chosen, instead of every cohort in the protocol.
    parent_dimension_type: str | None = None
    parent_code: str | None = None

    @model_validator(mode="after")
    def parent_is_complete(self) -> "StudyDimension":
        if (self.parent_dimension_type is None) != (self.parent_code is None):
            raise ValueError(
                "a parent dimension needs both its type and its code; a half-stated "
                "hierarchy would silently behave as if there were none"
            )
        return self


class GenericDimension(StudyDimension):
    dimension_type: str = Field(min_length=1)


class Anchor(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str | None = None
    display_name: str = Field(min_length=1)
    anchor_type: str = Field(min_length=1)
    derivation_rule: dict[str, object] | None = None
    source_event_code: str | None = None
    source_condition_code: str | None = None
    # AD-5: no default. Whether an anchor's resolution is actually settled is
    # itself a fact that must be stated, not assumed - a model-level default
    # here would silently cover every extraction path that never sets it,
    # including any future ambiguity-detecting one, the same way this exact
    # default let every anchor from the native pipeline read RESOLVED with
    # nothing ever having checked.
    status: Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS"]
    evidence_refs: list[UUID] = Field(default_factory=list)


#: Marks the anchor a schedule counts Day 0 from. Recorded on the anchor's own
#: ``derivation_rule`` by whatever produced the schedule, so the origin is a
#: stated fact carried WITH its semantic anchor type rather than something each
#: consumer re-derives (doc s4).
SCHEDULE_ORIGIN_ROLE = "SCHEDULE_ORIGIN"


def schedule_origin_anchor(schedule: "UniversalSchedule"):
    """The anchor this schedule's Day 0 is measured from, or None.

    Never matches on an anchor's printed NAME. A protocol's origin may be called
    Baseline, Day 1, Randomisation or First Dose, and those are different
    clinical facts; the origin is whichever anchor the schedule was built
    against, which the builder records explicitly - either as this schedule's
    stated ``day_numbering.day_1_anchor`` (the primary, correct source: a
    protocol-stated fact, not an inference) or, for the legacy adapter path
    until it is retired, an anchor whose ``derivation_rule`` carries the
    origin role directly.

    Returns ``None`` when neither is stated. That is the correct answer, not a
    gap: a caller needing this anchor (patient enrolment) must fail loudly
    rather than guess, and the validator blocks approval of a CLINICAL_DAY
    schedule that reaches this state.
    """
    day_numbering = schedule.schedule_metadata.day_numbering
    if day_numbering is not None:
        code = day_numbering.day_1_anchor.code
        for anchor in schedule.anchors:
            if anchor.code == code:
                return anchor
        return None
    for anchor in schedule.anchors:
        if (anchor.derivation_rule or {}).get("role") == SCHEDULE_ORIGIN_ROLE:
            return anchor
    return None


class ApplicabilityRule(StrictModel):
    dimension: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    operator: Literal["IN", "NOT_IN", "EQ", "NE", "LT", "LTE", "GT", "GTE"] = "IN"
    values: list[str] = Field(min_length=1)
    field: str | None = None
    condition: ConditionExpression | None = None
    # AD-7: how this rule combines with the NEXT rule in the same list
    # (a left-to-right fold, not a global operator for the whole list).
    # Default AND is byte-for-byte the old, only behaviour, so every
    # existing applicability list keeps evaluating exactly as before.
    combinator: Literal["AND", "OR"] = "AND"

    @model_validator(mode="after")
    def comparison_operator_takes_exactly_one_value(self) -> "ApplicabilityRule":
        """EQ/NE/LT/LTE/GT/GTE compare against one threshold, not a membership
        set - a second value would silently be ignored rather than compared."""
        if self.operator not in {"IN", "NOT_IN"} and len(self.values) != 1:
            raise ValueError(
                f"a {self.operator} applicability rule needs exactly one "
                "comparison value, not a membership set"
            )
        return self


class Dependency(StrictModel):
    source_event_code: str = Field(min_length=1)
    dependency_type: Literal["TEMPORAL", "TRIGGER", "PRECONDITION", "SEQUENCE", "ANCHOR"]
    condition: ConditionExpression | None = None


class Qualifier(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    marker: str | None = None
    text: str = Field(min_length=1)
    scope: QualifierScope
    category: QualifierCategory = QualifierCategory.UNRESOLVED
    target_codes: list[str] = Field(default_factory=list)
    resolved: bool = False
    evidence_refs: list[UUID] = Field(default_factory=list)
    #: Structured facts a qualifier's PROSE would otherwise be the only home for
    #: - a window's real type and bounds, a repeat rule's cycle list, and so on.
    #: Doc s7/s27: "preserve window type, lower bound, upper bound, units,
    #: reference event" means those numbers must survive as data a reviewer's
    #: decision can act on, not only as a sentence a human has to re-parse.
    details: dict[str, object] = Field(default_factory=dict)


class ConditionalAction(StrictModel):
    action_type: Literal[
        "ADD_EVENT", "REPEAT_EVENT", "STOP_BLOCK", "PAUSE_BLOCK",
        "RESUME_BLOCK", "EXTEND_CONFINEMENT", "CANCEL_EVENT", "MANUAL_REVIEW",
        # WP3 (RL-10/RL-11): transitions of ONE intervention/pathway process
        # (``ProcessDefinition.code``). Only obligations linked to that process
        # change; other interventions and follow-up continue.
        "HOLD_PROCESS", "RESUME_PROCESS", "STOP_PROCESS", "COMPLETE_PROCESS",
    ]
    target_code: str = Field(min_length=1)
    condition: ConditionExpression
    parameters: dict[str, object] = Field(default_factory=dict)
    evidence_refs: list[UUID] = Field(default_factory=list)


class ConfinementDefinition(StrictModel):
    admission_event_code: str = Field(min_length=1)
    dose_event_codes: list[str] = Field(min_length=1)
    discharge_event_code: str = Field(min_length=1)
    extension_actions_allowed: bool = False
    evidence_refs: list[UUID] = Field(default_factory=list)


class RuleScope(StrEnum):
    """Whose situation a protocol rule is about (doc I8).

    "Grade 3 toxicity: hold treatment" is about one patient. "Fatal toxicity:
    accrual is suspended and the DSMC reviews" is about the trial. Only the
    first kind may change a patient's visits; SITE/COUNTRY/TRIAL rules belong
    to a trial-level workflow and the patient engine refuses to act on them.
    """

    PATIENT = "PATIENT"
    ARM = "ARM"
    COHORT = "COHORT"
    SITE = "SITE"
    COUNTRY = "COUNTRY"
    TRIAL = "TRIAL"


#: Scopes whose rules act on an individual patient's schedule. ARM and COHORT
#: rules still describe each patient in that group; which patients is said by
#: the rule's applicability, exactly as for a PATIENT rule.
PATIENT_RULE_SCOPES = frozenset({RuleScope.PATIENT, RuleScope.ARM, RuleScope.COHORT})


class ConditionalDefinition(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    # Default PATIENT keeps every stored conditional definition behaving exactly
    # as before; it was the only kind the engine ever had.
    scope: RuleScope = RuleScope.PATIENT
    condition: ConditionExpression
    trigger_event_code: str | None = None
    applicability: list[ApplicabilityRule] = Field(default_factory=list)
    actions: list[ConditionalAction] = Field(min_length=1)
    resolution_condition: ConditionExpression | None = None
    recurrence: RecurrenceRule | None = None
    evidence_refs: list[UUID] = Field(default_factory=list)
    interpretation_status: InterpretationStatus = InterpretationStatus.EXTRACTED
    requires_review: bool = False


class RepeatBlock(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    event_codes: list[str] = Field(min_length=1)
    dependency_mode: DependencyMode
    resume_mode: Literal["NOMINAL", "ACTUAL_RESUME", "MANUAL", "UNCLEAR"] = "UNCLEAR"
    #: What ends a pause. ``None`` (every stored block) and EXPLICIT_RESUME both
    #: mean only an explicit RESUME_BLOCK transition resumes the block: marking
    #: the pausing condition RESOLVED ends that condition EPISODE but does not
    #: silently un-pause (the WP3 fix of the I1 hazard). CONDITION_RESOLUTION
    #: is the protocol stating that resolution itself resumes treatment, on the
    #: recorded resolution date.
    resume_trigger: Literal["EXPLICIT_RESUME", "CONDITION_RESOLUTION"] | None = None
    # Doc F1-F5: a treatment-cycle block states its cycles here - length, how
    # each start is dated, and what ends them. None keeps a plain repeat block.
    # Stored inside the block's existing JSON, so no schema change is needed.
    cycle_plan: CyclePlan | None = None
    evidence_refs: list[UUID] = Field(default_factory=list)


class ConfinementDayDefinition(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    day_label: str = Field(min_length=1)
    relative_day: int
    activity_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[UUID] = Field(default_factory=list)


class ConfinementEpisodeDefinition(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    admission_event_code: str = Field(min_length=1)
    dose_event_codes: list[str] = Field(min_length=1)
    discharge_event_code: str = Field(min_length=1)
    days: list[ConfinementDayDefinition] = Field(min_length=1)
    applicability: list[ApplicabilityRule] = Field(default_factory=list)
    conditions: list[ConditionExpression] = Field(default_factory=list)
    evidence_refs: list[UUID] = Field(default_factory=list)


class RecurrenceTermination(StrictModel):
    type: Literal["COUNT", "DATE", "EVENT", "CONDITION", "HORIZON", "RELATIVE"]
    count: int | None = Field(default=None, gt=0)
    termination_date: date | None = Field(default=None, alias="date")
    event_code: str | None = None
    condition: ConditionExpression | None = None
    end_reference: AnchorReference | None = None
    end_offset: TemporalAmount | None = None
    inclusive: bool | None = None

    @model_validator(mode="after")
    def matching_value(self) -> "RecurrenceTermination":
        values = {
            "COUNT": self.count,
            "DATE": self.termination_date,
            "EVENT": self.event_code,
            "CONDITION": self.condition,
            "HORIZON": True,
            "RELATIVE": self.end_reference,
        }
        if values[self.type] is None:
            raise ValueError(f"{self.type} termination requires its matching value")
        if self.type == "RELATIVE":
            if self.end_offset is None or self.inclusive is None:
                raise ValueError("RELATIVE termination requires an offset and an explicit inclusive boundary")
        elif any(value is not None for value in (self.end_reference, self.end_offset, self.inclusive)):
            raise ValueError("Relative boundary fields require RELATIVE termination")
        return self


class RecurrenceRule(StrictModel):
    type: Literal["INTERVAL"] = "INTERVAL"
    interval: PositiveTemporalAmount
    start_reference: TemporalReference
    # Optional for existing approved schedules. New extraction retains a stated
    # delayed first occurrence instead of restarting every repeat at baseline.
    start_offset: TemporalAmount | None = None
    termination: RecurrenceTermination
    include_start: bool = True
    #: AT-19: the versioned month-end semantics a MONTH/YEAR cadence relies on
    #: (``MONTH_END_CLAMP.v1``). ``None`` keeps the engine's existing clamp so
    #: approved schedules date exactly as before (NF-08), but every clamped
    #: occurrence then says the policy was not stated, so the gap stays visible.
    calendar_policy_ref: str | None = None


# ---------------------------------------------------------------------------
# WP3 - calendar (weekday) recurrence (RL-07, RL-08, RL-09, AT-05, AT-19)
# ---------------------------------------------------------------------------


class Weekday(StrEnum):
    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"
    SUN = "SUN"


#: ``date.weekday()`` value of each member.
WEEKDAY_INDEX: dict[Weekday, int] = {day: index for index, day in enumerate(Weekday)}

#: The only weekday-grid semantics the engine executes (registry-declared).
WEEKDAY_GRID_POLICY = "WEEKDAY_GRID.v1"


class HolidayCalendar(StrictModel):
    """Non-treatment dates supplied as DATA, per schedule or per site.

    A calendar is never inferred from a locale or a library: which days a site
    treats on is an operational fact. ``business_weekdays`` is required because
    "next business day" means nothing until the site's working week is stated.
    """

    code: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_.-]*$")
    version: str = Field(min_length=1)
    business_weekdays: list[Weekday] = Field(min_length=1)
    dates: list[date] = Field(default_factory=list)
    description: str | None = None

    @model_validator(mode="after")
    def unique_weekdays(self) -> "HolidayCalendar":
        if len(set(self.business_weekdays)) != len(self.business_weekdays):
            raise ValueError("business weekdays must not repeat")
        return self


class HolidayPolicy(StrEnum):
    #: A holiday is not a slot; the series continues on the next slot.
    SKIP = "SKIP"
    #: The slot keeps its identity and moves to the next business day that is
    #: not a holiday; if that day already holds a slot the move needs review.
    SHIFT_NEXT_BUSINESS_DAY = "SHIFT_NEXT_BUSINESS_DAY"
    #: The protocol does not say. A slot landing on a known holiday is
    #: UNRESOLVED (blocking only that slot and what depends on it - RL-09).
    NONE_STATED = "NONE_STATED"


class MissedOccurrencePolicy(StrEnum):
    APPEND_REPLACEMENT = "APPEND_REPLACEMENT"
    NO_REPLACEMENT = "NO_REPLACEMENT"
    NONE_STATED = "NONE_STATED"


class CountBasis(StrEnum):
    """What a COUNT termination counts (RL-08): "30 slots" is not "30
    delivered fractions". The two only differ once a session is missed or
    held, which is exactly when an unstated basis blocks (and only then)."""

    SLOTS = "SLOTS"
    DELIVERED = "DELIVERED"


class CalendarRecurrence(StrictModel):
    """Recurrence on named weekdays ("Monday to Friday", "every other Tuesday").

    Occurrence identity is the SLOT ordinal (session 1, 2, ...), independent of
    the date a slot lands on: a missed or moved session keeps its number, and a
    replacement is a new ordinal linked to the session it replaces.
    """

    type: Literal["CALENDAR"] = "CALENDAR"
    start_reference: TemporalReference
    start_offset: TemporalAmount | None = None
    weekdays: list[Weekday] = Field(min_length=1)
    #: Every n-th week, counted from the week (Mon-Sun) containing the start.
    interval_weeks: int = Field(default=1, gt=0)
    termination: RecurrenceTermination
    #: Required in meaning for COUNT termination; ``None`` = not stated.
    count_basis: CountBasis | None = None
    holiday_policy: HolidayPolicy = HolidayPolicy.NONE_STATED
    #: Code of a ``HolidayCalendar`` on the schedule or supplied for the site.
    holiday_calendar_ref: str | None = None
    missed_policy: MissedOccurrencePolicy = MissedOccurrencePolicy.NONE_STATED
    calendar_policy_ref: Literal["WEEKDAY_GRID.v1"] = WEEKDAY_GRID_POLICY

    @model_validator(mode="after")
    def coherent(self) -> "CalendarRecurrence":
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("recurrence weekdays must not repeat")
        if self.count_basis is not None and self.termination.type != "COUNT":
            raise ValueError("a count basis only qualifies a COUNT termination")
        return self


class RecurrenceSegment(StrictModel):
    """One cadence of a phased follow-up plan (doc E9).

    A segment ends either at ``end_offset`` measured from the PLAN's start
    reference ("to year 2" is +24 months from end of therapy), or after
    ``count`` visits, or - final segment only - where the plan's termination
    says. ``end_inclusive`` decides which segment owns a visit falling exactly
    on the boundary; it is never defaulted, because guessing it either
    duplicates or drops the boundary visit (doc E4). The validator blocks an
    unset flag.
    """

    interval: PositiveTemporalAmount
    end_offset: TemporalAmount | None = None
    end_inclusive: bool | None = None
    count: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def one_end(self) -> "RecurrenceSegment":
        if self.end_offset is not None and self.count is not None:
            raise ValueError("a segment ends at a boundary or after a count, not both")
        if self.end_offset is None and self.end_inclusive is not None:
            raise ValueError("end_inclusive describes an end_offset boundary")
        return self


class RecurrencePlan(StrictModel):
    """A follow-up plan whose cadence changes over time, as ONE rule (doc E9).

    "Every 3 months for 2 years, then every 6 months to year 5, then yearly."
    Each segment starts where the previous one ended, every occurrence is
    counted from its segment's start (no drift), and the whole series is one
    visit with continuous occurrence numbering - not chained visits a reviewer
    has to keep consistent by hand.
    """

    type: Literal["SEGMENTED"] = "SEGMENTED"
    start_reference: TemporalReference
    start_offset: TemporalAmount | None = None
    # Whether a visit falls ON the plan start itself (e.g. on the end-of-therapy
    # date). Never assumed; the validator blocks an unset value.
    include_start: bool | None = None
    segments: list[RecurrenceSegment] = Field(min_length=1)
    # What ends the plan overall - and the final segment when it states no end
    # of its own. HORIZON is the explicit "continues while on study".
    termination: RecurrenceTermination | None = None


class CyclePlan(StrictModel):
    """Treatment cycles as addressable starts (doc F1-F5).

    Cycle n's start is published as anchor ``<code>_<n>_START`` (CYCLE_2_START,
    ...): actual once it happened, planned before, so any rule can count from a
    specific cycle with the usual ACTUAL / PLANNED_OR_ACTUAL safety (doc C6).
    An actual cycle start is a person-recorded ``<code>_<n>_START`` anchor, or
    else the n-th recorded actual date of ``start_event_code`` (the Day-1 visit)
    in date order - cycles are given in sequence, so the k-th Day 1 given is
    Cycle k. Cycle 1 starts at ``start_reference``.
    """

    code: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    start_reference: AnchorReference
    length: PositiveTemporalAmount
    dependency_mode: CycleDependencyMode
    start_event_code: str | None = None
    # How many cycles, or what ends them: COUNT, CONDITION, EVENT, DATE,
    # RELATIVE, or an explicit HORIZON for protocols that treat until
    # progression or toxicity with no stated maximum.
    termination: RecurrenceTermination


class Activity(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str | None = None
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    activity_type: str = Field(min_length=1)
    requiredness: Requiredness = Requiredness.REQUIRED
    sequence_number: int | None = None
    timing: TimingExpression | None = None
    #: Composable constraint set (see Event.constraints). None = none stated.
    constraints: Constraint | None = None
    conditions: list[ConditionExpression] = Field(default_factory=list)
    applicability: list[ApplicabilityRule] = Field(default_factory=list)
    qualifiers: list[Qualifier] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    interpretation_status: InterpretationStatus = InterpretationStatus.EXTRACTED
    requires_review: bool = False
    evidence_refs: list[UUID] = Field(default_factory=list)


class Event(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    normalized_name: str | None = None
    event_type: str = Field(min_length=1)
    epoch_id: UUID | None = None
    sequence_number: int | None = None
    timing: TimingExpression
    # Doc B2/B3: the allowed window is its own rule, not a kind of timing, so it
    # survives on any target date - including every occurrence of a repeat - and
    # may be one-sided (-0/+3). None means the protocol states no window; it is
    # never defaulted (doc B4). NOMINAL_WITH_WINDOW timing keeps working; a
    # schedule carrying both must state the same window (validator).
    window: Window | None = None
    #: RL-04/RL-05: additional composable restrictions on WHEN this event may
    #: happen, from any number of anchors. The effective feasible set is the
    #: intersection of ``timing``'s resolved window and this set. None (every
    #: legacy schedule) evaluates exactly as before.
    constraints: Constraint | None = None
    applicability: list[ApplicabilityRule] = Field(default_factory=list)
    conditions: list[ConditionExpression] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    dependency_mode: DependencyMode | None = None
    # Doc 10 s2-s3 and s31-s32. ``visit_mode`` is the single mode when the
    # protocol states one; ``allowed_visit_modes`` holds every mode a hybrid
    # visit permits. Neither is defaulted - an unstated mode stays unstated so
    # no patient is told to travel on a guess (doc 10 s18, s33).
    visit_mode: str | None = None
    allowed_visit_modes: list[str] = Field(default_factory=list)
    activation: Literal["SCHEDULED", "ON_DEMAND"] = "SCHEDULED"
    recurrence: RecurrenceRule | None = None
    # Doc E9: a repeat whose cadence changes over time. Mutually exclusive with
    # ``recurrence`` - a visit repeats by one rule.
    recurrence_plan: RecurrencePlan | None = None
    #: WP3: weekday recurrence. Mutually exclusive with the two above.
    calendar_recurrence: CalendarRecurrence | None = None
    conditional_actions: list[ConditionalAction] = Field(default_factory=list)
    qualifiers: list[Qualifier] = Field(default_factory=list)
    confinement: ConfinementDefinition | None = None
    activities: list[Activity] = Field(default_factory=list)
    evidence_refs: list[UUID] = Field(default_factory=list)
    interpretation_status: InterpretationStatus = InterpretationStatus.EXTRACTED
    requires_review: bool = False
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def one_repeat_rule(self) -> "Event":
        stated = [item for item in (self.recurrence, self.recurrence_plan, self.calendar_recurrence)
                  if item is not None]
        if len(stated) > 1:
            raise ValueError(
                "an event repeats by one rule: a recurrence, a recurrence plan or a "
                "calendar recurrence")
        return self

    @property
    def repeats(self) -> bool:
        return (self.recurrence is not None or self.recurrence_plan is not None
                or self.calendar_recurrence is not None)


class Evidence(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    evidence_type: str = Field(min_length=1)
    page_number: int | None = Field(default=None, gt=0)
    section_title: str | None = None
    table_title: str | None = None
    row_identifier: str | None = None
    column_identifier: str | None = None
    source_text: str | None = None
    source_locator: dict[str, object] = Field(default_factory=dict)
    extraction_context: dict[str, object] = Field(default_factory=dict)


class ClaimEvidence(StrictModel):
    evidence_id: UUID
    claim_type: str = Field(min_length=1)
    claim_entity_type: str = Field(min_length=1)
    claim_entity_id: UUID
    claim_path: str | None = None
    interpretation: dict[str, object] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ValidationIssue(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    entity_type: str | None = None
    entity_id: UUID | None = None
    issue_code: str = Field(min_length=1)
    severity: Severity
    message: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)
    blocking: bool = False
    status: IssueStatus = IssueStatus.OPEN


# ---------------------------------------------------------------------------
# WP3 - scoped process states (RL-10, RL-11, AT-06, AT-07)
# ---------------------------------------------------------------------------


class ProcessKind(StrEnum):
    """What a process is. Participation decisions act by kind, never by name:
    withdrawal from treatment stops INTERVENTION processes, end of follow-up
    completes FOLLOW_UP ones, withdrawal of consent stops everything."""

    INTERVENTION = "INTERVENTION"
    FOLLOW_UP = "FOLLOW_UP"
    ASSESSMENT = "ASSESSMENT"


class ProcessState(StrEnum):
    ACTIVE = "ACTIVE"
    HELD = "HELD"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"


TERMINAL_PROCESS_STATES = frozenset({ProcessState.STOPPED, ProcessState.COMPLETED})


class HoldResumePolicy(StrEnum):
    """What a held process's remaining obligations do once it resumes
    (design s5). Never inferred from the word "resume"."""

    #: The grid never moved; obligations inside the hold are not made up.
    NOMINAL = "NOMINAL"
    #: Every obligation from the hold start moves later by the hold duration.
    SHIFT_BY_HOLD = "SHIFT_BY_HOLD"
    #: The first held obligation is placed on the actual resume date and the
    #: rest keep their spacing from it.
    RESTART_FROM_RESUME = "RESTART_FROM_RESUME"
    #: A person decides; the resume transition must carry the chosen policy.
    WAIT_FOR_DECISION = "WAIT_FOR_DECISION"
    #: The protocol does not say; obligations after the hold wait for review.
    NONE_STATED = "NONE_STATED"


#: Policies a resume DECISION may choose.
DECIDABLE_RESUME_POLICIES = frozenset({
    HoldResumePolicy.NOMINAL, HoldResumePolicy.SHIFT_BY_HOLD,
    HoldResumePolicy.RESTART_FROM_RESUME,
})


class ProcessDefinition(StrictModel):
    """One intervention or pathway with its own state (design s5)."""

    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    display_name: str = Field(min_length=1)
    kind: ProcessKind
    #: Obligations linked to this process. An event belongs to at most one.
    event_codes: list[str] = Field(default_factory=list)
    hold_resume_policy: HoldResumePolicy = HoldResumePolicy.NONE_STATED
    #: As RepeatBlock.resume_trigger: None/EXPLICIT_RESUME mean resolving the
    #: holding condition does not resume the process by itself.
    resume_trigger: Literal["EXPLICIT_RESUME", "CONDITION_RESOLUTION"] | None = None
    evidence_refs: list[UUID] = Field(default_factory=list)


class ProcessTransitionReason(StrEnum):
    DECISION = "DECISION"
    CONDITION = "CONDITION"
    PROTOCOL_COMPLETION = "PROTOCOL_COMPLETION"
    WITHDRAWAL_FROM_TREATMENT = "WITHDRAWAL_FROM_TREATMENT"
    WITHDRAWAL_OF_CONSENT = "WITHDRAWAL_OF_CONSENT"
    END_OF_FOLLOW_UP = "END_OF_FOLLOW_UP"


class ProcessTarget(StrictModel):
    """Which processes a transition changes: one process, every process of a
    kind, or the patient's whole participation (consent withdrawal)."""

    type: Literal["PROCESS", "PROCESS_KIND", "PATIENT"]
    process_code: str | None = None
    kind: ProcessKind | None = None

    @model_validator(mode="after")
    def matching_value(self) -> "ProcessTarget":
        if (self.type == "PROCESS") != (self.process_code is not None):
            raise ValueError("a PROCESS target names exactly one process code")
        if (self.type == "PROCESS_KIND") != (self.kind is not None):
            raise ValueError("a PROCESS_KIND target names exactly one kind")
        return self


class ProcessTransition(StrictModel):
    """A dated, attributable change of process state (a patient fact).

    Only CONFIRMED transitions act. ``effective_at`` is when it takes effect
    clinically, ``recorded_at`` when it entered the system; a late entry with
    an earlier effective time re-evaluates the affected period.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    target: ProcessTarget
    to_state: ProcessState
    effective_at: date | datetime
    recorded_at: datetime = Field(default_factory=utc_now)
    status: Literal["CONFIRMED", "PENDING_CONFIRMATION", "CANCELLED"]
    reason: ProcessTransitionReason
    #: DECISION (a person) or CONDITION (a confirmed protocol condition).
    source: Literal["DECISION", "CONDITION"] = "DECISION"
    source_ref: str | None = None
    authority: str | None = None
    #: A resume decision's chosen policy (WAIT_FOR_DECISION / NONE_STATED).
    resume_policy: HoldResumePolicy | None = None
    supersedes: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self) -> "ProcessTransition":
        if self.reason == ProcessTransitionReason.WITHDRAWAL_OF_CONSENT and (
                self.target.type != "PATIENT" or self.to_state != ProcessState.STOPPED):
            raise ValueError("withdrawal of consent stops the patient's whole participation")
        if self.target.type == "PATIENT" and self.to_state != ProcessState.STOPPED:
            raise ValueError("a patient-level transition can only stop participation")
        if self.reason == ProcessTransitionReason.WITHDRAWAL_FROM_TREATMENT and (
                self.target.type == "PATIENT" or self.to_state != ProcessState.STOPPED):
            raise ValueError(
                "withdrawal from treatment stops intervention processes, not participation")
        if self.reason == ProcessTransitionReason.END_OF_FOLLOW_UP and (
                self.to_state != ProcessState.COMPLETED or self.target.type == "PATIENT"):
            raise ValueError("end of follow-up completes a follow-up process")
        if self.resume_policy is not None and (
                self.to_state != ProcessState.ACTIVE
                or self.resume_policy not in DECIDABLE_RESUME_POLICIES):
            raise ValueError(
                "a resume policy is chosen by a resume (ACTIVE) transition and must be "
                "NOMINAL, SHIFT_BY_HOLD or RESTART_FROM_RESUME")
        return self


# ---------------------------------------------------------------------------
# WP3 - dated patient facts (PT-01, PT-02, PT-04, PT-07, RL-11, AT-08, AT-11)
# ---------------------------------------------------------------------------


class OccurrenceFactKind(StrEnum):
    #: The occurrence happened (``value`` = performed date/time).
    PERFORMED = "PERFORMED"
    #: The occurrence did not happen and will not (identity kept).
    MISSED = "MISSED"
    #: An appointment was booked (``value`` = booked date/time). A booking is
    #: never a performed time and never moves the protocol rule.
    BOOKED = "BOOKED"


class OccurrenceFact(StrictModel):
    """One recorded fact about one logical occurrence (event code + index)."""

    id: str = Field(min_length=1)
    event_code: str = Field(min_length=1)
    occurrence_index: int = Field(ge=0)
    kind: OccurrenceFactKind
    value: date | datetime | None = None
    #: End of the performed occurrence, when recorded (END boundary selector).
    end_value: date | datetime | None = None
    effective_at: date | datetime | None = None
    recorded_at: datetime
    #: The fact this one corrects; the superseded fact stays in history.
    supersedes: str | None = None
    source: str | None = None

    @model_validator(mode="after")
    def valued(self) -> "OccurrenceFact":
        if self.kind in {OccurrenceFactKind.PERFORMED, OccurrenceFactKind.BOOKED} and self.value is None:
            raise ValueError(f"a {self.kind.value} fact needs its date")
        return self


class AssignmentFact(StrictModel):
    """A dated assignment (arm, sequence, cohort ...) with its recorded time.

    ``effective_from`` None means "from the start of participation" (the
    assignment in force before any dated change). Later facts never rewrite
    earlier ones; a correction supersedes by id and both stay in history.
    """

    id: str = Field(min_length=1)
    dimension: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    value: str | None = None
    effective_from: date | None = None
    recorded_at: datetime
    source: str | None = None
    supersedes: str | None = None


class UniversalSchedule(StrictModel):
    schema_version: Literal["uctsm.v1"] = "uctsm.v1"
    schedule_version_id: UUID = Field(default_factory=uuid4)
    schedule_metadata: ScheduleMetadata
    epochs: list[Epoch] = Field(default_factory=list)
    arms: list[StudyDimension] = Field(default_factory=list)
    cohorts: list[StudyDimension] = Field(default_factory=list)
    populations: list[StudyDimension] = Field(default_factory=list)
    dimensions: list[GenericDimension] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    conditional_definitions: list[ConditionalDefinition] = Field(default_factory=list)
    repeat_blocks: list[RepeatBlock] = Field(default_factory=list)
    confinement_episodes: list[ConfinementEpisodeDefinition] = Field(default_factory=list)
    #: WP3: interventions/pathways with independent state (RL-10/RL-11).
    processes: list[ProcessDefinition] = Field(default_factory=list)
    #: WP3: schedule-level holiday calendars (a site may supply its own).
    holiday_calendars: list[HolidayCalendar] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claim_evidence: list[ClaimEvidence] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PatientContext(StrictModel):
    patient_id: UUID
    schedule_version_id: UUID
    #: Anchor values that record something that actually happened (or that a
    #: human confirmed as the real date). Every anchor reference may use these.
    anchors: dict[str, date | datetime] = Field(default_factory=dict)
    #: Expected dates only - a planned surgery, a projected last dose. Kept apart
    #: from ``anchors`` so that only a reference declaring PLANNED_OR_ACTUAL can
    #: count from one; an actual value for the same anchor always wins (doc C6).
    planned_anchors: dict[str, date | datetime] = Field(default_factory=dict)
    event_values: dict[str, list[date | datetime]] = Field(default_factory=dict)
    actual_event_values: dict[str, list[date | datetime]] = Field(default_factory=dict)
    activity_actuals: dict[str, date | datetime] = Field(default_factory=dict)
    activity_statuses: dict[str, str] = Field(default_factory=dict)
    conditions: dict[str, PatientConditionStatus] = Field(default_factory=dict)
    unscheduled_occurrences: list[UnscheduledOccurrence] = Field(default_factory=list)
    visit_modes: dict[str, str] = Field(default_factory=dict)
    state: dict[str, object] = Field(default_factory=dict)
    state_effective_at: dict[str, datetime] = Field(default_factory=dict)
    arm_code: str | None = None
    cohort_code: str | None = None
    population_code: str | None = None
    dimension_values: dict[str, list[str]] = Field(default_factory=dict)
    # Doc K2/K3: the IANA zone of the patient's SITE (Europe/London,
    # Asia/Kolkata). It lives on the patient context, not the schedule, because
    # one approved schedule runs at sites in different zones. When set, naive
    # recorded date-times are read as local site time and timed activities are
    # calculated in elapsed (UTC) time; dates stay dates. None keeps the
    # previous naive arithmetic.
    time_zone: str | None = None
    #: WP3 facts. All default empty, which evaluates exactly as before (NF-08).
    #: Per-occurrence performed / missed / booked facts with supersession.
    occurrence_facts: list[OccurrenceFact] = Field(default_factory=list)
    #: Confirmed-or-not process transitions recorded for the patient.
    process_transitions: list[ProcessTransition] = Field(default_factory=list)
    #: Dated assignment history; empty = the columns above apply throughout.
    assignment_history: list[AssignmentFact] = Field(default_factory=list)
    #: Site-supplied holiday calendars (override schedule ones by code).
    holiday_calendars: list[HolidayCalendar] = Field(default_factory=list)

    @model_validator(mode="after")
    def known_time_zone(self) -> "PatientContext":
        if self.time_zone is not None:
            try:
                ZoneInfo(self.time_zone)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError(f"unknown IANA time zone {self.time_zone!r}") from error
        return self


class PatientConditionStatus(StrictModel):
    condition_code: str = Field(min_length=1)
    state: ConditionState = ConditionState.NOT_OCCURRED
    occurrence_date: date | datetime | None = None
    resolution_date: date | datetime | None = None
    occurrence_index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def recurrence_is_numbered(self) -> "PatientConditionStatus":
        """A recurrence is at least the second occurrence, by definition."""
        if self.state == ConditionState.RECURRED and self.occurrence_index < 1:
            raise ValueError(
                "a RECURRED condition must carry the occurrence index of the "
                "recurrence; index 0 is the first occurrence, which is ACTIVE"
            )
        return self

    @model_validator(mode="after")
    def dated_when_active(self) -> "PatientConditionStatus":
        dated = {
            ConditionState.ACTIVE, ConditionState.RECURRED, ConditionState.RESOLVED,
        }
        if self.state in dated and self.occurrence_date is None:
            raise ValueError(f"{self.state.value} condition requires its occurrence date")
        return self


class ResolvedTiming(StrictModel):
    nominal_start: date | datetime | None = None
    nominal_end: date | datetime | None = None
    earliest: date | datetime | None = None
    latest: date | datetime | None = None
    precision: Literal["EXACT", "APPROXIMATE", "CONSTRAINT"]
    constraints: list[dict[str, object]] = Field(default_factory=list)


class FeasibilityStatus(StrEnum):
    """Outcome of evaluating a constraint set for one occurrence.

    FEASIBLE: every input is known and the permitted set is non-empty.
    INCOMPLETE: an input is missing; ``feasible_intervals`` hold the KNOWN
    bounds (a superset of the final set). Never operationally ready.
    INFEASIBLE: the known inputs already leave no permitted time.
    """

    FEASIBLE = "FEASIBLE"
    INCOMPLETE = "INCOMPLETE"
    INFEASIBLE = "INFEASIBLE"


class FeasibleBound(StrictModel):
    #: None = unbounded on this side (a legitimately one-sided requirement
    #: unless ``provisional`` says a missing input may still tighten it).
    value: date | datetime | None = None
    inclusive: bool = True
    #: Constraint ids that produced this bound (several when they coincide,
    #: e.g. a deduplicated restriction). ``timing`` ids name the legacy timing.
    sources: list[str] = Field(default_factory=list)
    #: True when a missing input could still move this side (INCOMPLETE only).
    provisional: bool = False


class FeasibleInterval(StrictModel):
    lower: FeasibleBound
    upper: FeasibleBound


class MissingInput(StrictModel):
    #: ANCHOR, EVENT, ACTIVITY, LITERAL_DATE, TIMING or CALENDAR_POLICY.
    kind: str
    code: str | None = None
    occurrence: str | None = None
    accepts: str | None = None
    #: NOT_RECORDED, PLANNED_ONLY, NO_PLANNED_TARGET, LAST_NOT_FINAL,
    #: OCCURRENCE_NOT_AVAILABLE, TIME_PRECISION, DATE_PRECISION,
    #: CALENDAR_POLICY_REQUIRED or TIMING_REFERENCE_MISSING.
    reason: str
    constraint_ids: list[str] = Field(default_factory=list)


class ConstraintTraceEntry(StrictModel):
    """NF-03: how one leaf constraint was evaluated for this occurrence."""

    constraint_id: str
    type: str
    status: Literal["RESOLVED", "MISSING"]
    value: date | datetime | None = None
    inclusive: bool | None = None
    #: Facts used: ``{"kind", "code", "occurrence", "value", "basis"}`` where
    #: basis is ACTUAL or PLANNED.
    inputs: list[dict[str, object]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    #: Whether this leaf produced a bound of the final set (a less restrictive
    #: restriction is kept here for audit with ``binding`` False - RL-04).
    binding: bool = False


class ConstraintRelation(StrictModel):
    """A semantic relation found while normalising a constraint set."""

    relation: Literal["DUPLICATE"]
    constraint_ids: list[str]
    evidence_refs: list[str] = Field(default_factory=list)


class EvaluatedActivity(StrictModel):
    activity_definition_id: UUID
    activity_code: str | None = None
    display_name: str
    requiredness: Requiredness
    sequence_number: int | None = None
    status: PatientActivityStatus
    timing: ResolvedTiming | None = None
    actual_time: date | datetime | None = None
    applicability_result: str | None = None
    condition_result: str | None = None
    #: Constraint-set evaluation (None when the rule carries no constraints,
    #: i.e. every legacy schedule - which then evaluates exactly as before).
    feasibility: FeasibilityStatus | None = None
    feasible_intervals: list[FeasibleInterval] = Field(default_factory=list)
    missing_inputs: list[MissingInput] = Field(default_factory=list)
    conflicting_constraints: list[str] = Field(default_factory=list)
    constraint_trace: list[ConstraintTraceEntry] = Field(default_factory=list)
    constraint_relations: list[ConstraintRelation] = Field(default_factory=list)
    explanation: dict[str, object] = Field(default_factory=dict)


class EvaluatedEvent(StrictModel):
    event_definition_id: UUID
    event_code: str
    occurrence_index: int = Field(default=0, ge=0)
    status: PatientEventStatus
    timing: ResolvedTiming | None = None
    visit_mode: str | None = None
    allowed_visit_modes: list[str] = Field(default_factory=list)
    unscheduled_reason: str | None = None
    applicability_result: str | None = None
    condition_result: str | None = None
    dependency_result: dict[str, object] = Field(default_factory=dict)
    activities: list[EvaluatedActivity] = Field(default_factory=list)
    #: Constraint-set evaluation (None when the rule carries no constraints,
    #: i.e. every legacy schedule - which then evaluates exactly as before).
    feasibility: FeasibilityStatus | None = None
    feasible_intervals: list[FeasibleInterval] = Field(default_factory=list)
    missing_inputs: list[MissingInput] = Field(default_factory=list)
    conflicting_constraints: list[str] = Field(default_factory=list)
    constraint_trace: list[ConstraintTraceEntry] = Field(default_factory=list)
    constraint_relations: list[ConstraintRelation] = Field(default_factory=list)
    #: PT-04: kept apart from the protocol target (``timing``), the permitted
    #: set (``feasible_intervals``) and each other. None = not recorded.
    booked_time: date | datetime | None = None
    #: True when the booked slot lies outside the permitted dates. The rule
    #: is never changed to make a booking compliant.
    booked_outside_permitted: bool | None = None
    actual_time: date | datetime | None = None
    explanation: dict[str, object] = Field(default_factory=dict)


class EvaluatedConfinementDay(StrictModel):
    day_label: str
    relative_day: int
    scheduled_date: date | datetime | None = None
    activity_codes: list[str] = Field(default_factory=list)


class EvaluatedConfinement(StrictModel):
    """One patient confinement episode: a single parent stay, not one visit per day."""

    episode_definition_id: UUID
    episode_code: str
    display_name: str
    status: ConfinementStatus
    planned_admission: date | datetime | None = None
    actual_admission: date | datetime | None = None
    planned_discharge: date | datetime | None = None
    actual_discharge: date | datetime | None = None
    extended_by: list[str] = Field(default_factory=list)
    days: list[EvaluatedConfinementDay] = Field(default_factory=list)
    explanation: dict[str, object] = Field(default_factory=dict)


class RollingHorizon(StrictModel):
    """How much of an open-ended repeat to materialize (requirement doc 3 s7-s8).

    ``upcoming_limit`` bounds only what is WRITTEN DOWN. It is a display and
    storage policy, never a statement about how long the protocol runs: doc 3 s2
    and s36 are explicit that a technical cap must not be presented as the
    protocol maximum. ``None`` keeps the previous behaviour of materializing
    everything up to the date horizon.
    """

    upcoming_limit: int | None = Field(default=None, gt=0)
    as_of: date | None = None


class RepeatSummary(StrictModel):
    """What a repeating rule does beyond the occurrences that were materialized.

    Carried alongside the events so a UI can show "every 21 days, continues"
    instead of implying the schedule ends at the last row (doc 3 s4, s36).
    """

    event_code: str
    block_code: str | None = None
    cadence: str
    termination: str
    materialized_count: int
    continues: bool
    next_unmaterialized: date | datetime | None = None
    # Doc E9: one entry per cadence of a phased plan, in order ("every 3 months
    # to year 2"). Empty for a single-cadence repeat.
    segments: list[str] = Field(default_factory=list)


class EvaluationResult(StrictModel):
    schedule_version_id: UUID
    patient_id: UUID
    evaluator_version: str
    evaluated_at: datetime = Field(default_factory=utc_now)
    input_snapshot: dict[str, object]
    events: list[EvaluatedEvent]
    confinements: list[EvaluatedConfinement] = Field(default_factory=list)
    repeat_summaries: list[RepeatSummary] = Field(default_factory=list)


# Models that name a type defined further down (e.g. RecurrenceRule) are built
# lazily by pydantic. Nested validation inside UniversalSchedule does not finish
# that build, so serialising such a model directly then failed with a
# MockValSer TypeError. Resolve every forward reference once, at import.
for _model in [value for value in list(globals().values())
               if isinstance(value, type) and issubclass(value, StrictModel)
               and not getattr(value, "__pydantic_complete__", True)]:
    _model.model_rebuild()
