from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    # populate_by_name lets a model be re-read from its own model_dump(). Without
    # it an aliased field (RecurrenceTermination.termination_date, alias "date")
    # serialises under one name and is rejected under the other, so a recurrence
    # that ends on a fixed date could not survive being stored and loaded back.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TimeUnit(StrEnum):
    MINUTE = "MINUTE"
    HOUR = "HOUR"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"


class Precision(StrEnum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"
    CONSTRAINT = "CONSTRAINT"


class TemporalAmount(StrictModel):
    value: int
    unit: TimeUnit


class NonNegativeTemporalAmount(StrictModel):
    value: int = Field(ge=0)
    unit: TimeUnit


class PositiveTemporalAmount(StrictModel):
    value: int = Field(gt=0)
    unit: TimeUnit


class AnchorAcceptance(StrEnum):
    """Which recorded values of an anchor a rule may count from (doc C6).

    A planned surgery date may place the pre-op visits, but a post-op visit dated
    from it would be presented - and reminded about - as if surgery had happened.
    So every anchor reference states whether it accepts a planned value at all.
    """

    ACTUAL = "ACTUAL"
    PLANNED_OR_ACTUAL = "PLANNED_OR_ACTUAL"


class AnchorReference(StrictModel):
    kind: Literal["ANCHOR"] = "ANCHOR"
    code: str = Field(min_length=1)
    # Default ACTUAL is the safe direction: a reference that never said it may use
    # a planned value waits for the real one. Every existing approved schedule
    # loads with this default, and the anchors enrolment records (registration,
    # baseline, consent) are stored CONFIRMED, which counts as actual - so those
    # schedules date exactly as before. Only a reviewer can opt a rule into
    # PLANNED_OR_ACTUAL, for visits that genuinely precede the event (pre-op).
    accepts: AnchorAcceptance = AnchorAcceptance.ACTUAL


class EventReference(StrictModel):
    kind: Literal["EVENT"] = "EVENT"
    event_code: str = Field(min_length=1)
    occurrence: Literal["FIRST", "LAST", "CURRENT"] = "CURRENT"


class ActivityReference(StrictModel):
    """An intra-day anchor: another activity inside the same visit occurrence.

    Protocols say "2 hours after dosing" or "2 hours after completion of infusion".
    The referenced activity's ACTUAL recorded time is the anchor; a planned time is
    never substituted, so dependent activities stay unresolved until it is recorded.
    """

    kind: Literal["ACTIVITY"] = "ACTIVITY"
    activity_code: str = Field(min_length=1)


TemporalReference = Annotated[
    Union[AnchorReference, EventReference, ActivityReference],
    Field(discriminator="kind"),
]


class Window(StrictModel):
    before: NonNegativeTemporalAmount
    after: NonNegativeTemporalAmount

    @model_validator(mode="after")
    def same_unit(self) -> "Window":
        if self.before.unit != self.after.unit:
            raise ValueError("window before and after must use the same unit")
        return self


class AbsoluteTiming(StrictModel):
    type: Literal["ABSOLUTE"] = "ABSOLUTE"
    value: date | datetime


class OffsetTiming(StrictModel):
    type: Literal["OFFSET"] = "OFFSET"
    reference: TemporalReference
    offset: TemporalAmount


class ProtocolDayTiming(StrictModel):
    """A numbered clinical day; unlike OFFSET, Day 1 resolves to the anchor."""
    type: Literal["PROTOCOL_DAY"] = "PROTOCOL_DAY"
    reference: AnchorReference
    day: int

    @model_validator(mode="after")
    def no_day_zero(self) -> "ProtocolDayTiming":
        if self.day == 0:
            raise ValueError("clinical day numbering has no Day 0")
        return self


class RangeTiming(StrictModel):
    type: Literal["RANGE"] = "RANGE"
    reference: TemporalReference
    start: TemporalAmount
    end: TemporalAmount

    @model_validator(mode="after")
    def ordered_compatible_bounds(self) -> "RangeTiming":
        if self.start.unit != self.end.unit:
            raise ValueError("range bounds must use the same unit")
        if self.start.value > self.end.value:
            raise ValueError("range start must be less than or equal to range end")
        return self


class NominalWindowTiming(StrictModel):
    type: Literal["NOMINAL_WITH_WINDOW"] = "NOMINAL_WITH_WINDOW"
    nominal: OffsetTiming | AbsoluteTiming | ProtocolDayTiming
    window: Window


class WithinTiming(StrictModel):
    type: Literal["WITHIN"] = "WITHIN"
    reference: TemporalReference
    duration: PositiveTemporalAmount
    direction: Literal["AFTER", "BEFORE"] = "AFTER"


class NoLaterThanTiming(StrictModel):
    type: Literal["NO_LATER_THAN"] = "NO_LATER_THAN"
    reference: TemporalReference
    duration: TemporalAmount


class ApproximateTiming(StrictModel):
    type: Literal["APPROXIMATE"] = "APPROXIMATE"
    reference: TemporalReference
    offset: TemporalAmount
    requires_review: bool = True


class TriggerOffset(StrictModel):
    type: Literal["OFFSET"] = "OFFSET"
    offset: TemporalAmount


class TriggerWithin(StrictModel):
    type: Literal["WITHIN"] = "WITHIN"
    duration: PositiveTemporalAmount
    direction: Literal["AFTER", "BEFORE"] = "AFTER"


class TriggerNoLaterThan(StrictModel):
    type: Literal["NO_LATER_THAN"] = "NO_LATER_THAN"
    duration: TemporalAmount


class TriggeredTiming(StrictModel):
    type: Literal["TRIGGERED"] = "TRIGGERED"
    trigger: TemporalReference
    timing_after_trigger: Annotated[
        Union[TriggerOffset, TriggerWithin, TriggerNoLaterThan],
        Field(discriminator="type"),
    ]


class CycleSelector(StrictModel):
    type: Literal["CURRENT", "NUMBER"]
    number: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def number_matches_type(self) -> "CycleSelector":
        if (self.type == "NUMBER") != (self.number is not None):
            raise ValueError("cycle number is required only for NUMBER selector")
        return self


class CycleDayTiming(StrictModel):
    type: Literal["CYCLE_DAY"] = "CYCLE_DAY"
    cycle_reference: str = Field(min_length=1)
    cycle: CycleSelector
    day: int = Field(gt=0)


class ProtocolExtension(StrictModel):
    type: str = Field(min_length=1)
    value: object


class ProtocolDefinedTiming(StrictModel):
    type: Literal["PROTOCOL_DEFINED"] = "PROTOCOL_DEFINED"
    handler: str | None = None
    extensions: list[ProtocolExtension] = Field(default_factory=list)
    requires_review: bool = True


class UnresolvedTiming(StrictModel):
    type: Literal["UNRESOLVED"] = "UNRESOLVED"
    reason: str = Field(min_length=1)
    source_reference: dict[str, object] = Field(default_factory=dict)
    requires_review: Literal[True] = True


# ---------------------------------------------------------------------------
# Composable constraint language (design: "Implementation specification:
# composable protocol language"). ADDITIVE to the timing union above: every
# legacy timing keeps its own evaluator; a constraint set is intersected with
# the legacy timing's resolved window only where a rule carries one. Nothing
# here has a default for a clinically material value - inclusivity, occurrence
# selection and literal precision must be stated.
# ---------------------------------------------------------------------------

#: Version of the constraint language; stamped into evaluation explanations and
#: the capability registry so a stored rule can be matched to its semantics.
CONSTRAINT_LANGUAGE_VERSION = "uctsm-constraints.v1"


class OccurrenceSelector(StrictModel):
    """Which occurrence of a repeated event a reference means.

    FIRST/LAST/NUMBER select within the referenced event's own series. CURRENT
    is the occurrence with the same index as the one being evaluated; PREVIOUS
    is the one before it (index - 1), which is the only selector a rule may use
    to refer to its OWN event ("at least 21 days after the previous dose").
    LAST with ACTUAL is known only once every scheduled occurrence has an
    actual - the latest dose given so far is not the last dose.
    """

    type: Literal["FIRST", "LAST", "CURRENT", "PREVIOUS", "NUMBER"]
    number: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def number_matches_type(self) -> "OccurrenceSelector":
        if (self.type == "NUMBER") != (self.number is not None):
            raise ValueError("an occurrence number is required only for the NUMBER selector")
        return self


class AnchorValue(StrictModel):
    type: Literal["ANCHOR"] = "ANCHOR"
    code: str = Field(min_length=1)
    # Same safe default as AnchorReference: a reference that never said it may
    # use a planned value waits for the actual one (doc C6, PT-01).
    accepts: AnchorAcceptance = AnchorAcceptance.ACTUAL


class EventValue(StrictModel):
    type: Literal["EVENT"] = "EVENT"
    event_code: str = Field(min_length=1)
    # No default: which dose/cycle/visit is meant is a material fact.
    occurrence: OccurrenceSelector
    accepts: AnchorAcceptance = AnchorAcceptance.ACTUAL
    #: WP3: which edge of the selected occurrence. ``None`` (every existing
    #: rule) is the occurrence's date exactly as before. START is its start;
    #: END is its end - planned from the occurrence's nominal end, actual only
    #: from a recorded end (a performed start is not proof of the end).
    boundary: Literal["START", "END"] | None = None


class ActivityValue(StrictModel):
    """The ACTUAL recorded time of another activity in the same visit occurrence."""

    type: Literal["ACTIVITY"] = "ACTIVITY"
    activity_code: str = Field(min_length=1)


class ProcessBoundaryValue(StrictModel):
    """WP3: when an intervention/pathway process actually started or ended.

    START is its first confirmed ACTIVE transition; END its confirmed STOPPED
    or COMPLETED transition. Both are ACTUAL facts only - a process that has
    not ended has no end date yet ("end of treatment" stays unknown while
    treatment continues), and no planned value is ever substituted.
    """

    type: Literal["PROCESS_BOUNDARY"] = "PROCESS_BOUNDARY"
    process_code: str = Field(min_length=1)
    boundary: Literal["START", "END"]


class LiteralDateValue(StrictModel):
    """A fixed calendar date or instant. ``precision`` is required: "by March
    2027" is not the same bound as "by 1 March 2027", and the evaluator will
    not pick a day inside a month for you (it reports missing precision)."""

    type: Literal["LITERAL_DATE"] = "LITERAL_DATE"
    value: date | datetime
    precision: Literal["INSTANT", "DAY", "MONTH", "YEAR"]

    @model_validator(mode="after")
    def precision_matches_value(self) -> "LiteralDateValue":
        is_instant = isinstance(self.value, datetime)
        if (self.precision == "INSTANT") != is_instant:
            raise ValueError("INSTANT precision needs a date-time; other precisions need a date")
        return self


class ShiftValue(StrictModel):
    """``reference`` moved by a signed, calendar-aware amount. Months and years
    stay calendar units; a month-end clamp needs ``calendar_policy_ref``."""

    type: Literal["SHIFT"] = "SHIFT"
    reference: "TemporalValue"
    amount: TemporalAmount
    calendar_policy_ref: str | None = None


TemporalValue = Annotated[
    Union[AnchorValue, EventValue, ActivityValue, LiteralDateValue, ShiftValue,
          ProcessBoundaryValue],
    Field(discriminator="type"),
]


class _ConstraintNode(StrictModel):
    #: Stable id used for provenance and conflict reports. When omitted the
    #: evaluator uses the node's structural path (e.g. ``SCAN/constraints/items/1``).
    id: str | None = Field(default=None, min_length=1)
    #: Evidence ids (``Evidence.id`` as a string) this node is justified by.
    evidence_refs: list[str] = Field(default_factory=list)


class LowerBoundConstraint(_ConstraintNode):
    type: Literal["LOWER_BOUND"] = "LOWER_BOUND"
    value: TemporalValue
    inclusive: bool


class UpperBoundConstraint(_ConstraintNode):
    type: Literal["UPPER_BOUND"] = "UPPER_BOUND"
    value: TemporalValue
    inclusive: bool


class AtConstraint(_ConstraintNode):
    type: Literal["AT"] = "AT"
    value: TemporalValue


class AllConstraint(_ConstraintNode):
    """Conjunction: the feasible sets of every item are intersected."""

    type: Literal["ALL"] = "ALL"
    items: list["Constraint"] = Field(min_length=1)


class AnyConstraint(_ConstraintNode):
    """Permitted temporal alternatives: a union that is never widened to its
    convex hull. It never selects a treatment arm or branch."""

    type: Literal["ANY"] = "ANY"
    items: list["Constraint"] = Field(min_length=1)


class UnsupportedConstraint(_ConstraintNode):
    """A source requirement the language cannot yet execute (a capability gap).

    Carried so the requirement stays visible and blocks approval of the rule
    it belongs to - never squeezed into a similar executable operator.
    ``family`` names what kind of operator was missing (CONSTRAINT, RECURRENCE,
    CONDITION, TIMING, ...), ``source_operator`` what the source asked for.
    """

    type: Literal["UNSUPPORTED"] = "UNSUPPORTED"
    family: str = Field(default="CONSTRAINT", min_length=1)
    source_operator: str = Field(min_length=1)
    source_text: str | None = None
    details: dict[str, object] = Field(default_factory=dict)


Constraint = Annotated[
    Union[
        LowerBoundConstraint,
        UpperBoundConstraint,
        AtConstraint,
        AllConstraint,
        AnyConstraint,
        UnsupportedConstraint,
    ],
    Field(discriminator="type"),
]


class ConstraintTiming(StrictModel):
    """Timing stated only as a constraint set (no single nominal target).

    Its own window is unbounded; the feasible set is exactly ``constraint``
    (intersected with the owning event's ``constraints`` when both exist).
    """

    type: Literal["CONSTRAINED"] = "CONSTRAINED"
    constraint: Constraint


ShiftValue.model_rebuild()
AllConstraint.model_rebuild()
AnyConstraint.model_rebuild()
ConstraintTiming.model_rebuild()


TimingExpression = Annotated[
    Union[
        AbsoluteTiming,
        OffsetTiming,
        ProtocolDayTiming,
        RangeTiming,
        NominalWindowTiming,
        WithinTiming,
        NoLaterThanTiming,
        ApproximateTiming,
        TriggeredTiming,
        CycleDayTiming,
        ProtocolDefinedTiming,
        UnresolvedTiming,
        ConstraintTiming,
    ],
    Field(discriminator="type"),
]
