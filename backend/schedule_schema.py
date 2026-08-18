"""Canonical, evidence-backed protocol schedule schema (version 2).

The mobile editor still consumes flattened visit rows.  This module preserves
the richer protocol meaning first, so calendar months, event-driven visits,
recurrences, activities, and conflicts are not destroyed during extraction.
"""
from __future__ import annotations

import re
import calendar
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SourceEvidence(BaseModel):
    evidence_id: str
    page_evidence_id: str = ""
    claim: str
    source_location: str
    source_quote: str
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence_id", "claim", "source_location", "source_quote")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("evidence fields cannot be blank")
        return value


class ScheduleOption(BaseModel):
    """One independently generatable schedule inside a multi-substudy protocol.

    A protocol with several arms sharing ONE Schedule of Assessments table is
    still a single schedule (see schedule_archetypes "multi_arm"). This model
    is only for the different case: a document that prints more than one
    genuinely separate Schedule of Assessments/Activities/Events table — for
    example distinct substudies, sub-protocols, or phase-specific appendices
    each with their own visit list, duration, and population — where merging
    every table into one graph would silently combine incompatible timelines.
    """

    id: str = Field(
        description="Stable slug unique within this document, e.g. 'ssa-p2', "
        "'ss3-m'. Derive it from the protocol's own short code when printed.")
    label: str = Field(
        description="Human label using the protocol's own naming, e.g. "
        "'Substudy A – Phase 2 (SSA-P2)' or 'Maintenance (SS3-M)'.")
    description: str = Field(
        default="",
        description="One short sentence distinguishing this schedule: "
        "population, duration, or purpose, e.g. '66-week Phase 2 induction "
        "and extension for treatment-naive subjects'.")
    source_location: str = Field(
        default="",
        description="Where this schedule's own table lives, e.g. 'Table 30, "
        "pages 156-159' or 'Appendix 1, Schedule of Assessments for SS3-M'.")

    @field_validator("id", "label")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("schedule option id/label cannot be blank")
        return value


class DocumentTaskClassification(BaseModel):
    """The AI's explicit decision about what document and schedule it is reading."""

    document_type: Literal[
        "protocol", "amendment", "synopsis", "schedule_only", "reference",
        "mixed", "unrelated",
    ]
    analysis_task: Literal[
        "full_protocol_schedule", "amendment_comparison", "schedule_table_only",
        "no_schedule",
    ]
    schedule_archetypes: list[Literal[
        "linear", "cyclic", "crossover", "multi_arm", "multi_phase",
        "event_driven", "intra_day", "long_term_extension", "mixed",
    ]] = Field(default_factory=list)
    complexity: Literal["simple", "moderate", "complex"]
    has_schedule: bool
    has_attached_reference: bool = False
    needs_version_comparison: bool = False
    schedule_options: list[ScheduleOption] = Field(
        default_factory=list,
        description="Populated ONLY when this document prints more than one "
        "independent Schedule of Assessments/Activities/Events (distinct "
        "substudies/sub-protocols, each with their own visit list and "
        "duration) that a reviewer must choose between before extraction. "
        "Leave empty for a single schedule, even a multi-arm/multi-phase one.")
    protocol_id: str = ""
    protocol_version: str = ""
    amendment_identifier: str = ""
    jurisdiction: str = ""
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    reasoning: str = ""


_UNIT_SYNONYMS = {
    "min": "minute", "mins": "minute", "minutes": "minute",
    "hr": "hour", "hrs": "hour", "hours": "hour", "h": "hour",
    "d": "day", "days": "day",
    "wk": "week", "wks": "week", "weeks": "week", "w": "week",
    "mo": "month", "mon": "month", "months": "month",
    "yr": "year", "yrs": "year", "years": "year", "y": "year",
}


class TemporalAmount(BaseModel):
    value: float
    unit: Literal["minute", "hour", "day", "week", "month", "year"]

    @field_validator("unit", mode="before")
    @classmethod
    def normalize_unit(cls, value):
        """Accept the plural/abbreviated units a model naturally writes.

        Pure normalisation: "days" and "day" mean the same duration, so this
        changes no meaning and only stops a whole schedule failing over a
        spelling the schema did not enumerate.
        """
        if isinstance(value, str):
            key = value.strip().lower()
            return _UNIT_SYNONYMS.get(key, key)
        return value


class TimingExpression(BaseModel):
    kind: Literal[
        "offset", "calendar_offset", "range", "relative", "event_driven",
        "constraint", "recurrence", "unresolved",
    ]
    anchor_id: str | None = None
    offset: TemporalAmount | None = None
    range_start: TemporalAmount | None = None
    range_end: TemporalAmount | None = None
    relation: Literal["before", "after", "on", "within", "between"] | None = None
    qualifier: Literal[
        "exact", "approximate", "minimum", "maximum", "up_to", "as_needed",
    ] | None = None
    calendar_mode: Literal["elapsed", "calendar"] | None = None
    source_label: str = ""
    alternative_source_labels: list[str] = Field(default_factory=list)
    weekday_rule: str = ""
    notes: str = ""
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def downgrade_unsupported_shape(cls, data):
        """Keep an under-specified timing as 'unresolved' instead of failing.

        Procedure prose such as "pre-dose", "at each visit" or "as clinically
        indicated" has no numeric offset and no anchor, and a model routinely
        labels it 'offset' or 'relative' anyway. Rejecting it discarded the
        whole schedule over a field that carries no value to lose: the source
        text survives in source_label and the fact stays unresolved for review,
        which is exactly how an unknown is meant to be represented. Nothing is
        invented here — a claim the payload never supported is simply dropped.
        """
        if not isinstance(data, dict):
            return data
        kind = data.get("kind")
        reason = ""
        if kind in ("offset", "calendar_offset", "relative") and data.get("offset") is None:
            reason = f"no offset amount was supplied for '{kind}' timing"
        elif kind == "range" and (
            data.get("range_start") is None or data.get("range_end") is None
        ):
            reason = "a range was supplied without both of its ends"
        elif kind in ("relative", "event_driven") and not data.get("anchor_id"):
            reason = f"no anchor was supplied for '{kind}' timing"
        if not reason:
            return data
        data = dict(data)
        data["kind"] = "unresolved"
        note = f"Timing left unresolved: {reason}."
        existing = str(data.get("notes") or "").strip()
        data["notes"] = f"{existing} {note}".strip() if existing else note
        return data

    @model_validator(mode="after")
    def required_shape(self):
        if self.kind == "calendar_offset":
            self.calendar_mode = "calendar"
        return self


class WindowSpec(BaseModel):
    scope: Literal["visit", "activity"] = "visit"
    window_type: Literal[
        "tolerance", "validity", "lookback", "minimum_gap", "maximum_gap", "other",
    ] = "tolerance"
    state: Literal["stated", "not_stated", "unclear", "conflicting"]
    early: TemporalAmount | None = None
    late: TemporalAmount | None = None
    source_label: str = ""
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def downgrade_valueless_stated_window(cls, data):
        """A 'stated' window with no amounts becomes 'unclear', not an error.

        The model asserting a window exists while giving no magnitude is the
        window-shaped twin of an offset with no number. Downgrading to
        'unclear' keeps that assertion visible and forces review, whereas
        inventing a default would breach the no-manufactured-window rule and
        rejecting it would discard the entire schedule.
        """
        if not isinstance(data, dict):
            return data
        if data.get("state") != "stated":
            return data
        if data.get("early") is not None or data.get("late") is not None:
            return data
        data = dict(data)
        data["state"] = "unclear"
        if not str(data.get("source_label") or "").strip():
            data["source_label"] = "A window was reported but no magnitude was given"
        return data

    @model_validator(mode="after")
    def stated_window_has_value(self):
        for amount in (self.early, self.late):
            if amount is not None and amount.value < 0:
                raise ValueError("window magnitudes must be non-negative")
        return self


class ScheduleAnchor(BaseModel):
    id: str
    name: str
    anchor_type: Literal[
        "consent", "screening", "randomization", "first_dose", "dose",
        "cycle_start", "period_start", "last_dose", "end_of_treatment",
        "discharge", "progression", "other",
    ]
    source_label: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class SchedulePhase(BaseModel):
    id: str
    name: str
    phase_type: Literal[
        "screening", "run_in", "treatment", "washout", "follow_up",
        "extension", "other",
    ]
    parent_phase_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ScheduleBranch(BaseModel):
    id: str
    name: str
    branch_type: Literal["arm", "cohort", "period", "sequence"]
    parent_branch_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ScheduleCondition(BaseModel):
    id: str
    expression: str
    applies_to_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ActivityTemplate(BaseModel):
    id: str
    name: str
    timing: TimingExpression | None = None
    window: WindowSpec | None = None
    conditional_text: str = ""
    operational_constraints: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ScheduleEvent(BaseModel):
    id: str
    name: str
    event_type: str = Field(
        default="visit",
        description="The visit's category, using the protocol's own 'Visit Type' row/codes "
        "when present (e.g. 'SS' study-site, 'V' virtual, 'T/C' telephone). Otherwise use "
        "'screening', 'baseline', 'randomization', 'treatment', 'follow_up', "
        "'end_of_treatment', 'end_of_study', 'early_termination', 'unscheduled', or "
        "'telephonic' (a phone/telephone-icon contact) based on the visit's role in the "
        "schedule. Never leave this at the generic default 'visit' when the protocol or "
        "its position in the schedule (first visit, last visit, phone-only contact) makes a "
        "more specific category determinable.")
    phase_id: str | None = None
    arm_id: str | None = None
    period_id: str | None = None
    timing: TimingExpression
    window: WindowSpec = Field(default_factory=lambda: WindowSpec(state="not_stated"))
    activity_ids: list[str] = Field(default_factory=list)
    required: bool = True
    conditional_text: str = ""
    operational_constraints: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RecurrenceRule(BaseModel):
    id: str
    event_ids: list[str]
    frequency: TemporalAmount
    start_occurrence: int = Field(default=1, ge=1)
    end_occurrence: int | None = Field(default=None, ge=1)
    until_event_id: str | None = None
    source_label: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class TransitionRule(BaseModel):
    id: str
    from_event_id: str
    to_event_id: str
    relation: Literal["before", "after", "same_day", "minimum_gap", "maximum_gap"]
    amount: TemporalAmount | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ScheduleConflict(BaseModel):
    id: str
    field_path: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    resolution: str = ""
    status: Literal["unresolved", "resolved"] = "unresolved"


class CanonicalSchedulePlan(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    protocol_id: str = ""
    protocol_version: str = ""
    title: str = ""
    anchors: list[ScheduleAnchor] = Field(default_factory=list)
    phases: list[SchedulePhase] = Field(default_factory=list)
    branches: list[ScheduleBranch] = Field(default_factory=list)
    events: list[ScheduleEvent] = Field(default_factory=list)
    activities: list[ActivityTemplate] = Field(default_factory=list)
    recurrences: list[RecurrenceRule] = Field(default_factory=list)
    transitions: list[TransitionRule] = Field(default_factory=list)
    conditions: list[ScheduleCondition] = Field(default_factory=list)
    conflicts: list[ScheduleConflict] = Field(default_factory=list)


def _stable_id(prefix: str, label: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
    return f"{prefix}-{slug or index}-{index}"


def canonical_from_flat(schedule) -> CanonicalSchedulePlan:
    """Lossless-enough fallback when a provider omits the canonical graph."""
    anchors = [ScheduleAnchor(
        id="anchor-baseline", name="Baseline / schedule origin",
        anchor_type="first_dose", source_label=(
            f"Day {schedule.anchor_study_day}"
            if schedule.anchor_study_day is not None else "Schedule origin"),
    )]
    events: list[ScheduleEvent] = []
    activities: list[ActivityTemplate] = []
    activity_by_name: dict[str, str] = {}
    branch_specs = []
    branch_ids: dict[tuple[str, str], str] = {}
    for visit in schedule.visits:
        for branch_type, value in (("arm", visit.arm), ("period", visit.period)):
            if value and (branch_type, value) not in branch_ids:
                branch_id = _stable_id(branch_type, value, len(branch_ids) + 1)
                branch_ids[(branch_type, value)] = branch_id
                branch_specs.append(ScheduleBranch(
                    id=branch_id, name=value, branch_type=branch_type))
    indexed_event_ids = [
        _stable_id("event", visit.name, index)
        for index, visit in enumerate(schedule.visits, 1)
    ]
    event_ids_by_name: dict[str, list[str]] = {}
    for visit, event_id in zip(schedule.visits, indexed_event_ids):
        event_ids_by_name.setdefault(visit.name.strip().lower(), []).append(event_id)
    for index, visit in enumerate(schedule.visits, 1):
        event_id = indexed_event_ids[index - 1]
        evidence_ids = sorted({eid for link in visit.field_evidence for eid in link.evidence_ids})
        if visit.relative_to:
            timing = TimingExpression(
                kind="relative", anchor_id=(
                    (event_ids_by_name.get(visit.relative_to.strip().lower()) or [None])[0]
                    or _stable_id("event", visit.relative_to, 0)),
                offset=TemporalAmount(value=visit.relative_offset_days or 0, unit="day"),
                relation="after" if (visit.relative_offset_days or 0) >= 0 else "before",
                source_label=visit.source_day_label or "", evidence_ids=evidence_ids)
        elif visit.hour_offset is not None:
            timing = TimingExpression(
                kind="offset", anchor_id="anchor-baseline",
                offset=TemporalAmount(value=visit.hour_offset, unit="hour"),
                source_label=visit.source_day_label or "", evidence_ids=evidence_ids)
        elif visit.day_offset is not None:
            timing = TimingExpression(
                kind="offset", anchor_id="anchor-baseline",
                offset=TemporalAmount(value=visit.day_offset, unit="day"),
                source_label=visit.source_day_label or "", evidence_ids=evidence_ids)
        else:
            timing = TimingExpression(
                kind="unresolved", source_label=visit.source_day_label or "-",
                evidence_ids=evidence_ids)
        if visit.window_before is not None or visit.window_after is not None:
            window = WindowSpec(
                state="stated",
                early=TemporalAmount(value=visit.window_before or 0, unit="day"),
                late=TemporalAmount(value=visit.window_after or 0, unit="day"),
                evidence_ids=evidence_ids)
        elif visit.window_days is not None:
            amount = TemporalAmount(value=visit.window_days, unit="day")
            window = WindowSpec(state="stated", early=amount, late=amount,
                                evidence_ids=evidence_ids)
        else:
            window = WindowSpec(state="not_stated")
        ids = []
        for name in visit.activities:
            if name not in activity_by_name:
                aid = _stable_id("activity", name, len(activity_by_name) + 1)
                activity_by_name[name] = aid
                activities.append(ActivityTemplate(id=aid, name=name, evidence_ids=evidence_ids))
            ids.append(activity_by_name[name])
        events.append(ScheduleEvent(
            id=event_id, name=visit.name, event_type=visit.visit_type or "visit",
            period_id=branch_ids.get(("period", visit.period)),
            arm_id=branch_ids.get(("arm", visit.arm)), timing=timing, window=window,
            activity_ids=ids, evidence_ids=evidence_ids))
    recurrences = []
    for index, block in enumerate(schedule.repeating_blocks, 1):
        recurrences.append(RecurrenceRule(
            id=f"recurrence-{index}", event_ids=[],
            frequency=TemporalAmount(value=block.cycle_length_days, unit="day"),
            start_occurrence=block.from_cycle, end_occurrence=block.to_cycle,
            source_label=f"Cycles {block.from_cycle}-"
                         f"{block.to_cycle if block.to_cycle is not None else 'open ended'}"))
    return CanonicalSchedulePlan(
        protocol_id=(schedule.classification.protocol_id if schedule.classification else ""),
        protocol_version=(schedule.classification.protocol_version if schedule.classification else ""),
        anchors=anchors, branches=branch_specs, events=events, activities=activities,
        recurrences=recurrences)


def validate_canonical_plan(
    plan: CanonicalSchedulePlan,
    evidence_ids: set[str] | None = None,
) -> list[str]:
    """Deterministic integrity checks; issues force human review."""
    issues: list[str] = []
    groups = {
        "anchor": [item.id for item in plan.anchors],
        "phase": [item.id for item in plan.phases],
        "branch": [item.id for item in plan.branches],
        "event": [item.id for item in plan.events],
        "activity": [item.id for item in plan.activities],
        "recurrence": [item.id for item in plan.recurrences],
        "transition": [item.id for item in plan.transitions],
        "condition": [item.id for item in plan.conditions],
        "conflict": [item.id for item in plan.conflicts],
    }
    all_ids: list[str] = []
    for kind, ids in groups.items():
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            issues.append(f"Duplicate {kind} IDs: {', '.join(duplicates)}")
        all_ids.extend(ids)
    duplicates = sorted({item for item in all_ids if all_ids.count(item) > 1})
    if duplicates:
        issues.append("IDs must be globally unique: " + ", ".join(duplicates))
    anchors, phases = set(groups["anchor"]), set(groups["phase"])
    branches = set(groups["branch"])
    events, activities = set(groups["event"]), set(groups["activity"])
    for event in plan.events:
        if event.phase_id and event.phase_id not in phases:
            issues.append(f"{event.id} references unknown phase {event.phase_id}")
        for label, branch_id in (("arm", event.arm_id), ("period", event.period_id)):
            if branch_id and branch_id not in branches:
                issues.append(f"{event.id} references unknown {label} branch {branch_id}")
        if event.timing.anchor_id and event.timing.anchor_id not in anchors | events:
            issues.append(f"{event.id} references unknown timing anchor {event.timing.anchor_id}")
        missing = sorted(set(event.activity_ids) - activities)
        if missing:
            issues.append(f"{event.id} references unknown activities: {', '.join(missing)}")
    for rule in plan.recurrences:
        missing = sorted(set(rule.event_ids) - events)
        if missing:
            issues.append(f"{rule.id} references unknown events: {', '.join(missing)}")
        if rule.end_occurrence is not None and rule.end_occurrence < rule.start_occurrence:
            issues.append(f"{rule.id} ends before it starts")
        if rule.end_occurrence is None and not rule.until_event_id:
            issues.append(f"{rule.id} is open-ended and requires reviewer confirmation")
    for rule in plan.transitions:
        if rule.from_event_id not in events or rule.to_event_id not in events:
            issues.append(f"{rule.id} references an unknown transition event")
    target_ids = set(all_ids)
    for condition in plan.conditions:
        missing = sorted(set(condition.applies_to_ids) - target_ids)
        if missing:
            issues.append(f"{condition.id} references unknown targets: {', '.join(missing)}")
    for conflict in plan.conflicts:
        if conflict.status == "unresolved":
            issues.append(f"Unresolved source conflict at {conflict.field_path}: {conflict.description}")
    if evidence_ids is not None:
        referenced: set[str] = set()
        for collection in (
            plan.anchors, plan.phases, plan.branches, plan.events, plan.activities,
            plan.recurrences, plan.transitions, plan.conditions, plan.conflicts,
        ):
            for item in collection:
                referenced.update(item.evidence_ids)
                timing = getattr(item, "timing", None)
                window = getattr(item, "window", None)
                if timing:
                    referenced.update(timing.evidence_ids)
                if window:
                    referenced.update(window.evidence_ids)
        unknown = sorted(referenced - evidence_ids)
        if unknown:
            issues.append("Canonical graph cites unknown evidence IDs: " + ", ".join(unknown))
    return list(dict.fromkeys(issues))


def apply_temporal_amount(
    anchor: date | datetime,
    amount: TemporalAmount,
    *,
    direction: int = 1,
) -> date | datetime:
    """Apply protocol timing without approximating calendar months or leap years."""
    value = amount.value * direction
    if amount.unit == "minute":
        return anchor + timedelta(minutes=value)
    if amount.unit == "hour":
        return anchor + timedelta(hours=value)
    if amount.unit == "day":
        return anchor + timedelta(days=value)
    if amount.unit == "week":
        return anchor + timedelta(weeks=value)
    if not float(value).is_integer():
        raise ValueError("calendar month/year offsets must be whole numbers")
    months = int(value) * (12 if amount.unit == "year" else 1)
    absolute_month = anchor.year * 12 + (anchor.month - 1) + months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def format_temporal_amount(amount: TemporalAmount | None) -> str:
    if amount is None:
        return ""
    value = int(amount.value) if float(amount.value).is_integer() else amount.value
    unit = amount.unit if abs(amount.value) == 1 else f"{amount.unit}s"
    return f"{value} {unit}"


def format_window(window: WindowSpec | None) -> str:
    if window is None or window.state == "not_stated":
        return ""
    if window.state != "stated":
        return window.source_label or window.state.replace("_", " ")
    if window.source_label:
        return window.source_label
    early, late = window.early, window.late
    if early and late and early == late:
        return f"±{format_temporal_amount(early)}"
    pieces = []
    if early:
        pieces.append(f"-{format_temporal_amount(early)}")
    if late:
        pieces.append(f"+{format_temporal_amount(late)}")
    return "/".join(pieces)


# A qualified or ranged timing statement bounds a visit; it does not fix its
# day. Projecting "within 28 days before randomization" as a plain Day -28 row
# would silently turn a permitted window into an appointment.
_INEXACT_QUALIFIERS = {"approximate", "minimum", "maximum", "up_to", "as_needed"}
_INEXACT_RELATIONS = {"within", "between"}


def _inexact_timing_note(timing: TimingExpression) -> str:
    """Describe a bounded/approximate timing, or '' when the day is exact."""
    if timing.kind == "range":
        bounds = " to ".join(part for part in (
            format_temporal_amount(timing.range_start),
            format_temporal_amount(timing.range_end),
        ) if part)
        return f"Timing is a range, not an exact day: {timing.source_label or bounds}"
    qualified = timing.qualifier in _INEXACT_QUALIFIERS
    bounded = timing.relation in _INEXACT_RELATIONS
    if not qualified and not bounded:
        return ""
    descriptor = timing.source_label.strip() or " ".join(part for part in (
        (timing.relation or "").replace("_", " "),
        format_temporal_amount(timing.offset),
    ) if part)
    qualifier_text = (timing.qualifier or timing.relation or "").replace("_", " ")
    return (
        f"Timing is bounded, not an exact day ({qualifier_text}): {descriptor}"
    ).strip()


# Protocols list assessments, paperwork and site logistics in one column of the
# Schedule of Assessments. The visit editor separates clinical work from
# administrative work, so the split happens here, deterministically.
#
# Matching is deliberately conservative and defaults to CLINICAL: mis-filing a
# real assessment as paperwork is the dangerous direction, and a coordinator
# can move a row in the editor. Patterns are word-anchored so "breakfast" does
# not match "fast" and "reconsent" still matches "consent".
_ADMIN_TASK_PATTERNS: tuple[str, ...] = (
    # Regulatory / documentation
    r"consent", r"\bassent\b", r"\be?crf\b", r"case report form",
    r"source (?:data|document)", r"data entry", r"quer(?:y|ies)",
    r"protocol deviation", r"\bdemographics?\b", r"\bdiary (?:issue|review|collection|dispens\w*)",
    # Enrolment / allocation
    r"randomi[sz]", r"\biwrs\b", r"\bivrs\b", r"\brtsm\b", r"enrol", r"registration",
    r"subject (?:number|id)", r"screening number", r"eligibilit",
    r"inclusion", r"exclusion",
    # Drug handling and compliance
    r"accountabilit", r"dispens", r"drug return", r"\bcompliance\b",
    r"pill count", r"tablet count",
    # Site logistics — real work, but not a clinical assessment
    r"\bhousing\b", r"confine", r"check[- ]?in", r"check[- ]?out",
    r"\badmission\b", r"\badmitted\b", r"\bdischarge\b", r"overnight",
    r"ambulator", r"\bwashout\b", r"\bfast(?:ing)?\b", r"\bmeals?\b",
    r"reimburse", r"\btravel\b", r"appointment", r"schedul",
)

_ADMIN_TASK_RE = re.compile("|".join(_ADMIN_TASK_PATTERNS), re.IGNORECASE)


def classify_visit_activities(activities: list[str]) -> tuple[list[str], list[str]]:
    """Split protocol activities into (clinical_tasks, admin_tasks).

    Order and wording are preserved exactly — this only routes each item to a
    column. Duplicates are dropped case-insensitively because a schedule table
    and its footnotes often name the same procedure twice.
    """
    clinical: list[str] = []
    admin: list[str] = []
    seen: set[str] = set()
    for activity in activities:
        name = str(activity or "").strip()
        if not name:
            continue
        key = " ".join(name.split()).casefold()
        if key in seen:
            continue
        seen.add(key)
        (admin if _ADMIN_TASK_RE.search(name) else clinical).append(name)
    return clinical, admin


def _elapsed_days(amount: TemporalAmount | None) -> float | None:
    if amount is None:
        return None
    factors = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7}
    factor = factors.get(amount.unit)
    return amount.value * factor if factor is not None else None


# A calendar-unit timing ("Month 3") states no day count — the protocol only
# ever gives the unit. Leaving day_offset null is honest but leaves the visit
# undated in every calendar/month-based schedule (seamless long-term-extension
# and cardiopulmonary-outcome designs commonly use nothing but Month N). A
# 30-day month / 365-day year is the standard clinical-scheduling convention
# used elsewhere for visit-window planning, so it is used here too, but only
# to populate the displayable day number — source_day_label keeps the
# protocol's own "Month 3" text, and build_row attaches a note wherever this
# approximation is actually used so a reviewer can tell an estimate from a
# protocol-stated day.
_CALENDAR_APPROX_DAYS = {"month": 30, "year": 365}


def _calendar_elapsed_days(amount: TemporalAmount | None) -> float | None:
    exact = _elapsed_days(amount)
    if exact is not None or amount is None:
        return exact
    factor = _CALENDAR_APPROX_DAYS.get(amount.unit)
    return amount.value * factor if factor is not None else None


def project_canonical_plan(
    plan: CanonicalSchedulePlan,
    *,
    open_ended_preview_count: int = 12,
) -> tuple[list[dict], list[str]]:
    """Compile one canonical graph into the legacy/mobile visit-row contract.

    Calendar months/years and event-driven timing remain undated in the template
    while their exact source timing is retained.  They are resolved only after a
    patient-specific anchor date exists.
    """
    warnings: list[str] = []
    anchor_ids = {item.id for item in plan.anchors}
    event_by_id = {item.id: item for item in plan.events}
    activity_by_id = {item.id: item for item in plan.activities}
    branch_by_id = {item.id: item for item in plan.branches}
    preferred = next((item for item in plan.anchors if item.anchor_type in (
        "randomization", "first_dose", "cycle_start", "period_start")), None)
    baseline_anchor_id = preferred.id if preferred else (
        plan.anchors[0].id if plan.anchors else None)
    anchor_by_id = {item.id: item for item in plan.anchors}

    # A protocol declares several anchors (first dose, Period II dose, last
    # dose). Only the baseline sits at day zero; the others must be derived or
    # every event hanging off them stays undated and renders as a bare dash.
    #
    # Events inside a period/arm routinely omit the anchor on all but one row
    # ("Day 0 of each period" is printed once per period). Treating that
    # omission as the baseline silently stacks every period onto the first, so
    # a branch instead inherits the anchor its own sibling events declare.
    branch_anchor: dict[str, str] = {}
    for event in plan.events:
        branch_id = event.period_id or event.arm_id
        if branch_id and event.timing.anchor_id in anchor_ids:
            branch_anchor.setdefault(branch_id, event.timing.anchor_id)

    def effective_anchor_id(event: ScheduleEvent) -> str | None:
        if event.timing.anchor_id:
            return event.timing.anchor_id
        branch_id = event.period_id or event.arm_id
        if branch_id and branch_id in branch_anchor:
            return branch_anchor[branch_id]
        return baseline_anchor_id

    anchor_days: dict[str, float] = {}
    if baseline_anchor_id is not None:
        anchor_days[baseline_anchor_id] = 0.0

    def resolve_event_day(
        event_id: str, seen: frozenset[str] = frozenset(),
    ) -> float | None:
        if event_id in seen:
            return None
        event = event_by_id.get(event_id)
        if not event:
            return None
        timing = event.timing
        if timing.kind not in ("offset", "relative", "calendar_offset"):
            return None
        delta = _calendar_elapsed_days(timing.offset)
        if delta is None:
            return None
        if timing.relation == "before" and delta > 0:
            delta = -delta
        if timing.anchor_id in event_by_id:
            parent = resolve_event_day(timing.anchor_id, seen | {event_id})
            return None if parent is None else parent + delta
        anchor_id = effective_anchor_id(event)
        base = anchor_days.get(anchor_id) if anchor_id else None
        return None if base is None else base + delta

    # Derive the remaining anchors from transitions that state a real gap:
    # "Period II Day 0 follows Period I Day 3 by 7 days" dates the Period II
    # anchor and therefore every visit in that period. A transition with no
    # stated amount (an unquantified washout) derives nothing, and that period
    # stays undated rather than being invented onto the baseline.
    for _ in range(len(plan.anchors) + 1):
        progressed = False
        for transition in plan.transitions:
            gap = _elapsed_days(transition.amount)
            if gap is None:
                continue
            source = event_by_id.get(transition.from_event_id)
            target = event_by_id.get(transition.to_event_id)
            if source is None or target is None:
                continue
            target_anchor = effective_anchor_id(target)
            if target_anchor is None or target_anchor in anchor_days:
                continue
            source_day = resolve_event_day(source.id)
            if source_day is None:
                continue
            target_offset = _elapsed_days(target.timing.offset) or 0.0
            anchor_days[target_anchor] = source_day + abs(gap) - target_offset
            progressed = True
        if not progressed:
            break

    # A protocol names several anchors inside one period — "Day 0 of each
    # period" for check-in and "Day 1 of Period I" for dosing. The graph never
    # states the gap between them, so every event hanging off the dosing anchor
    # was undated even though the protocol prints its study day plainly.
    #
    # The day is read from the anchor's own source label and applied only
    # relative to another anchor used by the SAME period, so "Day 0 of each
    # period" cannot leak from Period I into Period II. Nothing is invented:
    # an anchor whose label states no day stays unresolved.
    branch_anchor_ids: dict[str, set[str]] = {}
    for event in plan.events:
        branch_id = event.period_id or event.arm_id
        anchor_id = effective_anchor_id(event)
        if branch_id and anchor_id:
            branch_anchor_ids.setdefault(branch_id, set()).add(anchor_id)

    def label_study_day(anchor_id: str) -> float | None:
        anchor = anchor_by_id.get(anchor_id)
        if anchor is None:
            return None
        match = re.search(r"\bday\s*([+-]?\d+)\b", anchor.source_label or "", re.IGNORECASE)
        return float(match.group(1)) if match else None

    for _ in range(len(plan.anchors) + 1):
        progressed = False
        for anchor_ids_in_branch in branch_anchor_ids.values():
            known: list[tuple[str, float]] = []
            for anchor_id in sorted(anchor_ids_in_branch):
                if anchor_id not in anchor_days:
                    continue
                labelled = label_study_day(anchor_id)
                if labelled is not None:
                    known.append((anchor_id, labelled))
            if not known:
                continue
            reference_id, reference_label = known[0]
            for anchor_id in anchor_ids_in_branch:
                if anchor_id in anchor_days:
                    continue
                own_label = label_study_day(anchor_id)
                if own_label is None:
                    continue
                anchor_days[anchor_id] = (
                    anchor_days[reference_id] + own_label - reference_label)
                progressed = True
        if not progressed:
            break

    def unresolved_anchor_note(event: ScheduleEvent) -> str:
        """Say what an undated row is waiting on instead of showing a bare dash."""
        anchor_id = effective_anchor_id(event)
        if anchor_id is None or anchor_id in anchor_days:
            return ""
        anchor = anchor_by_id.get(anchor_id)
        return (
            f"Scheduled from '{anchor.name if anchor else anchor_id}', whose "
            "interval from the baseline is not stated in the protocol. This date "
            "is set once that event happens."
        )

    def evidence_links(event: ScheduleEvent, activities: list[ActivityTemplate]) -> list[dict]:
        name_ids = list(dict.fromkeys(event.evidence_ids))
        timing_ids = list(dict.fromkeys(event.timing.evidence_ids))
        window_ids = list(dict.fromkeys(event.window.evidence_ids))
        activity_ids = list(dict.fromkeys(
            evidence_id for activity in activities for evidence_id in activity.evidence_ids))
        return [
            {"field": field, "evidence_ids": ids}
            for field, ids in (("name", name_ids), ("timing", timing_ids),
                               ("window", window_ids), ("activities", activity_ids))
            if ids
        ]

    def build_row(event: ScheduleEvent, *, occurrence: int | None = None,
                  recurrence_delta: float | None = None) -> dict:
        timing = event.timing
        source_label = timing.source_label.strip()
        if not source_label:
            source_label = format_temporal_amount(timing.offset)
        day = resolve_event_day(event.id)
        if day is not None and recurrence_delta is not None:
            day += recurrence_delta
        day_offset = int(day) if day is not None and float(day).is_integer() else None

        # day_offset above is a 30-day/365-day approximation for a Month/Year
        # label. Once a real patient anchor date exists, exact calendar math
        # (real month lengths, leap years) beats that approximation — but only
        # when the offset is measured straight off the baseline: an event
        # chained onto another event's timing has no single real date to
        # apply_temporal_amount against at the per-patient stage.
        calendar_offset_value = None
        calendar_offset_unit = None
        if (
            timing.kind == "calendar_offset"
            and timing.offset is not None
            and timing.offset.unit in ("month", "year")
            and recurrence_delta is None
            and timing.anchor_id not in event_by_id
            and effective_anchor_id(event) == baseline_anchor_id
        ):
            calendar_offset_value = timing.offset.value
            if timing.relation == "before" and calendar_offset_value > 0:
                calendar_offset_value = -calendar_offset_value
            calendar_offset_unit = timing.offset.unit
        hour_offset = None
        if timing.offset and timing.offset.unit in ("minute", "hour") \
                and timing.kind == "offset":
            hour_offset = timing.offset.value / 60 if timing.offset.unit == "minute" \
                else timing.offset.value
        day_end = None
        if timing.kind == "range":
            start, end = _elapsed_days(timing.range_start), _elapsed_days(timing.range_end)
            if start is not None and end is not None and baseline_anchor_id == timing.anchor_id:
                day_offset = int(start) if float(start).is_integer() else None
                day_end = int(end) if float(end).is_integer() else None

        early = event.window.early
        late = event.window.late
        visit_window_is_days = event.window.scope == "visit" \
            and event.window.window_type == "tolerance" and event.window.state == "stated" \
            and all(item is None or item.unit == "day" for item in (early, late))
        window_before = int(early.value) if visit_window_is_days and early else None
        window_after = int(late.value) if visit_window_is_days and late else None
        window_days = None
        if window_before is not None and window_after is not None \
                and window_before == window_after:
            window_days = window_before
            window_before = window_after = None

        activities = [activity_by_id[item] for item in event.activity_ids
                      if item in activity_by_id]
        procedures = []
        operational_constraints: list[str] = list(event.operational_constraints)
        if event.conditional_text.strip():
            operational_constraints.append(event.conditional_text.strip())
        operational_constraints.extend(
            item for item in (
                timing.weekday_rule.strip(), timing.notes.strip(),
                *(label.strip() for label in timing.alternative_source_labels),
            ) if item)
        if event.window.state != "not_stated" and not visit_window_is_days:
            # A unit the legacy row cannot hold, or an unclear/conflicting
            # window, must stay readable instead of vanishing with the field.
            operational_constraints.append(
                f"Visit constraint: {format_window(event.window)}")
        inexact_note = _inexact_timing_note(timing)
        if inexact_note:
            operational_constraints.append(inexact_note)
        if timing.kind == "calendar_offset" and day_offset is not None:
            unit = timing.offset.unit if timing.offset else "month"
            operational_constraints.append(
                f"Day {day_offset} is approximated from the protocol's stated "
                f"'{source_label or format_temporal_amount(timing.offset)}' "
                f"({'30 days/month' if unit == 'month' else '365 days/year'}), "
                "not an exact protocol-given day.")
        anchor_note = unresolved_anchor_note(event)
        if anchor_note:
            operational_constraints.append(anchor_note)
        for activity in activities:
            timing_text = activity.timing.source_label if activity.timing else ""
            window_text = format_window(activity.window)
            # A procedure whose timing could not be structured still has to tell
            # the reviewer why, or the gap looks like the protocol was silent.
            activity_notes = [
                note for note in (
                    activity.timing.notes.strip() if activity.timing else "",
                ) if note
            ]
            procedures.append({
                "id": activity.id,
                "name": activity.name,
                "timing": timing_text,
                "window": window_text,
                "condition": activity.conditional_text,
                "evidence_ids": activity.evidence_ids,
                "constraints": list(activity.operational_constraints) + activity_notes,
            })
            detail = "; ".join(part for part in (
                timing_text and f"timing: {timing_text}",
                window_text and f"window: {window_text}",
                activity.conditional_text.strip(),
            ) if part)
            if detail:
                operational_constraints.append(f"{activity.name} — {detail}")
            operational_constraints.extend(
                f"{activity.name} — {constraint}"
                for constraint in activity.operational_constraints if constraint.strip())
        for transition in plan.transitions:
            if event.id not in (transition.from_event_id, transition.to_event_id):
                continue
            other_id = transition.from_event_id if transition.to_event_id == event.id \
                else transition.to_event_id
            other = event_by_id.get(other_id)
            amount = format_temporal_amount(transition.amount)
            operational_constraints.append(
                " ".join(part for part in (
                    transition.relation.replace("_", " "), amount,
                    other.name if other else other_id,
                ) if part))
        name = event.name
        if occurrence is not None:
            name = name.replace("{occurrence}", str(occurrence)).replace(
                "{cycle}", str(occurrence))
            if name == event.name and occurrence > 1:
                name = f"{name} (Occurrence {occurrence})"
        arm = branch_by_id.get(event.arm_id).name if event.arm_id in branch_by_id else None
        period = branch_by_id.get(event.period_id).name \
            if event.period_id in branch_by_id else None
        unresolved = day_offset is None and hour_offset is None
        # A resolved range keeps both of its ends, so it is represented
        # faithfully. A qualified single day ("within 28 days before", "at least
        # 21 days after") is a permitted boundary that the legacy row can only
        # show as one number, so it must never pass as a confirmed appointment.
        bounded_single_day = bool(inexact_note) and timing.kind != "range"
        review = (
            unresolved
            or bounded_single_day
            or event.window.state in ("unclear", "conflicting")
        )
        return {
            "canonical_event_id": event.id,
            "name": name,
            "visit_type": event.event_type,
            "day_offset": day_offset,
            "day_end": day_end,
            "calendar_offset_value": calendar_offset_value,
            "calendar_offset_unit": calendar_offset_unit,
            "source_day_label": source_label or "-",
            "hour_offset": hour_offset,
            "hour_offset_basis": "absolute" if hour_offset is not None else None,
            "hour_end": None,
            "window_days": window_days,
            "window_before": window_before,
            "window_after": window_after,
            "relative_to": event_by_id[timing.anchor_id].name
                if timing.kind == "relative" and timing.anchor_id in event_by_id else None,
            "relative_offset_days": (
                -int(abs(_elapsed_days(timing.offset)))
                if timing.relation == "before" else int(_elapsed_days(timing.offset))
            )
                if timing.kind == "relative" and _elapsed_days(timing.offset) is not None
                and float(_elapsed_days(timing.offset)).is_integer() else None,
            "arm": arm,
            "period": period,
            "activities": [item.name for item in activities],
            "procedures": procedures,
            "operational_constraints": list(dict.fromkeys(
                item for item in operational_constraints if item.strip())),
            "field_evidence": evidence_links(event, activities),
            "extraction_warning": review,
            "review_status": "pending" if review else "ok",
        }

    recurrence_by_event: dict[str, list[RecurrenceRule]] = {}
    for recurrence in plan.recurrences:
        for event_id in recurrence.event_ids:
            recurrence_by_event.setdefault(event_id, []).append(recurrence)
    rows: list[dict] = []
    for event in plan.events:
        recurrences = recurrence_by_event.get(event.id) or []
        if not recurrences:
            rows.append(build_row(event))
            continue
        for recurrence in recurrences:
            end = recurrence.end_occurrence
            if end is None:
                end = recurrence.start_occurrence + max(1, open_ended_preview_count) - 1
                warnings.append(
                    f"'{recurrence.source_label or recurrence.id}' is open-ended; "
                    f"showing {open_ended_preview_count} occurrences for review only.")
            frequency_days = _elapsed_days(recurrence.frequency)
            for occurrence in range(recurrence.start_occurrence, end + 1):
                delta = None if frequency_days is None else (
                    occurrence - recurrence.start_occurrence) * frequency_days
                row = build_row(
                    event, occurrence=occurrence, recurrence_delta=delta)
                if frequency_days is None and occurrence > recurrence.start_occurrence:
                    # Calendar-month/year recurrence needs a real patient date.
                    # Never duplicate the first occurrence's numeric offset.
                    row["day_offset"] = None
                    row["hour_offset"] = None
                    row["hour_offset_basis"] = None
                    row["extraction_warning"] = True
                    row["review_status"] = "pending"
                    cadence = recurrence.source_label or (
                        "Every " + format_temporal_amount(recurrence.frequency))
                    row["source_day_label"] = "; ".join(
                        item for item in (row["source_day_label"], cadence) if item)
                rows.append(row)
    return rows, list(dict.fromkeys(warnings))
