"""Legacy flat visit-schedule model and its deterministic expansion.

``ExtractedSchedule`` is the draft shape the Mongo schedule-definition flow
stores and edits; :func:`expand_schedule` turns its declared structure
(repeating blocks, relative anchors, conditional activities) into concrete
visits in Python, where the arithmetic is deterministic and unit-testable.

The LLM extractors that used to populate this model were removed ahead of the
adaptive extraction rebuild. Only the schema, the timing helpers and the
expansion remain; nothing here calls a model.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from schedule_schema import (
    _SIMPLE_DAY_LABEL,
    _SIMPLE_DAY_RANGE_LABEL,
    CanonicalSchedulePlan,
    DocumentTaskClassification,
    SourceEvidence,
    canonical_from_flat,
    project_canonical_plan,
    simple_day_label_offset,
    simple_day_label_range_offsets,
    study_day_to_offset,  # noqa: F401 - re-exported for existing callers
    validate_canonical_plan,
)

log = logging.getLogger(__name__)

# Upload size limit for protocol documents.
MAX_PDF_BYTES = 25 * 1024 * 1024

# How many occurrences of an open-ended repetition ("continue until progression",
# "every 8th week thereafter") to SHOW for review.
#
# This is a display/performance preview limit, NOT the protocol schedule. The
# protocol rule remains "repeat until the stopping criterion", and MTB must never
# present occurrence N as the last one (requirement doc 3 section 36). Every
# expansion that hits this limit records an explicit reviewer assumption saying so.
#
# Generating the real rolling patient horizon is the canonical engine's job
# (app/domain/schedule/evaluator.py expands recurrence to an explicit horizon).
OPEN_ENDED_PREVIEW_CYCLES = 4

# Retained name for existing callers and tests; the value is a preview limit.
OPEN_ENDED_CYCLE_CAP = OPEN_ENDED_PREVIEW_CYCLES

# Sanity ceiling on a single expansion, independent of the cap above.
MAX_EXPANDED_VISITS = 400


# ─────────────────────────── model-facing schema ───────────────────────────
# Field descriptions double as extraction instructions — the model reads them
# when producing structured output, so they carry real weight.

class ConditionalActivity(BaseModel):
    """An assessment that happens only in SOME repetitions of a cycle.

    e.g. "imaging (CT/MRI) will be performed after cycles 2, 4 and 6" — the
    visit recurs every cycle but this assessment does not.
    """
    name: str = Field(description="The assessment / procedure name.")
    cycles: List[int] = Field(
        default_factory=list,
        description="The 1-based cycle numbers this assessment applies to, e.g. [2, 4, 6].")


class FieldEvidence(BaseModel):
    """Evidence IDs supporting one extracted field or field group."""

    field: str = Field(
        description="Supported field: name, timing, window, activities, arm, or period.")
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="IDs from the evidence packets that directly support this field.")


class RepeatMember(BaseModel):
    """One visit inside a repeating cycle."""
    name_template: str = Field(
        description="Visit name with '{cycle}' where the cycle number belongs, e.g. "
        "'Cycle {cycle} Day 1', 'Cycle {cycle} Intra-cycle Visit 2'.")
    source_day_label_template: Optional[str] = Field(
        default=None,
        description="Exact protocol timing label with '{cycle}' where applicable. "
        "null when the source did not print a distinct timing label.")
    day_within_cycle: int = Field(
        description="0-based day offset from the START of the cycle. The cycle's "
        "first/dosing day is 0; a visit 7 days later is 7.")
    day_end_within_cycle: Optional[int] = Field(
        default=None,
        description="0-based end-day offset for a multi-day member; null otherwise.")
    hour_offset: Optional[float] = None
    hour_offset_basis: Optional[Literal["absolute", "within_day"]] = None
    hour_end: Optional[float] = None
    visit_type: Optional[str] = Field(default=None, description="See ExtractedVisit.visit_type.")
    window_days: Optional[int] = Field(
        default=None, ge=0,
        description="Visit window as +/- days; null when the protocol does not state one.")
    window_before: Optional[int] = Field(default=None, ge=0)
    window_after: Optional[int] = Field(default=None, ge=0)
    arm: Optional[str] = None
    period: Optional[str] = None
    activities: List[str] = Field(
        default_factory=list,
        description="Assessments performed at this visit in EVERY cycle.")
    conditional_activities: List[ConditionalActivity] = Field(
        default_factory=list,
        description="Assessments performed only in specific cycles.")
    field_evidence: List[FieldEvidence] = Field(
        default_factory=list,
        description="Evidence IDs supporting this repeating visit's fields.")


class RepeatingBlock(BaseModel):
    """A cycle the protocol prints once and tells you to repeat.

    This is the single most important field in the schema. Use it whenever the
    Schedule of Assessments collapses repetition — a column headed 'Cycle 2 &
    Next Cycles', 'each subsequent cycle', 'Cycles 3-6', or prose like 'every
    3 weeks for 6 cycles'. Do NOT enumerate those cycles as individual visits;
    describe the block and the server will expand it exactly.
    """
    from_cycle: int = Field(description="First cycle number this block covers (1-based).")
    to_cycle: Optional[int] = Field(
        default=None,
        description="Last cycle number covered. Use null ONLY when the protocol is "
        "genuinely open-ended ('until disease progression', 'every 8th week "
        "thereafter') — the server will expand a bounded number and flag it.")
    cycle_length_days: int = Field(
        description="Length of one cycle in days. Read from the treatment plan when "
        "the schedule table does not state it (e.g. 'every 3 weekly' -> 21, "
        "'q4w' -> 28, '28-day cycle' -> 28).")
    first_cycle_start_day: int = Field(
        description="Absolute calendar offset from baseline (0 = baseline) on which "
        "cycle `from_cycle` STARTS. "
        "e.g. if cycle 1 starts at baseline and cycles are 21 days, then a block "
        "beginning at cycle 2 has first_cycle_start_day = 21.")
    members: List[RepeatMember] = Field(
        default_factory=list,
        description="The visits that occur within each cycle of this block.")


class ExtractedProcedure(BaseModel):
    """One procedure plus its own timing/window, separate from visit tolerance."""

    id: str = ""
    name: str
    timing: str = ""
    window: str = ""
    condition: str = ""
    constraints: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)


class ExtractedVisit(BaseModel):
    """One scheduled visit / timepoint from the Schedule of Assessments."""
    name: str = Field(
        description="Self-describing visit name. Use the protocol's own label and make "
        "structure explicit: 'Visit 1 - Screening', 'Cycle 2 Day 1', 'Period 2 Day 1', "
        "'Arm B - Week 4', 'Week 12', 'Early Termination', 'Unscheduled', 'Follow-up'.")
    visit_type: Optional[str] = Field(
        default=None,
        description="Category of the visit when stated or clearly inferable: 'Screening', "
        "'Baseline', 'Randomization', 'Treatment', 'Follow-up', 'End of Treatment', "
        "'End of Study', 'Early Termination', 'Unscheduled', or 'Telephonic' (a phone/"
        "telephone-icon contact). Use the protocol's own visit-type codes when given "
        "(e.g. 'SS' study-site, 'V' virtual, 'T/C' telephone). null if not determinable.")
    day_offset: Optional[int] = Field(
        default=None,
        description="Absolute calendar displacement in days from the baseline anchor, "
        "where 0 is the baseline date regardless of its printed study-day label. "
        "Screening / run-in visits before baseline are NEGATIVE. Convert Week/Month "
        "labels only when the protocol explicitly defines their anchor/cadence; Week 1 "
        "must not be assumed to mean seven days. For a calendar-date schedule, use the "
        "day count from the baseline/randomization date. "
        "Leave null when the visit's timing is expressed RELATIVE to another visit (use "
        "relative_to instead) or when the protocol genuinely does not specify a day "
        "(Early Termination, Unscheduled) — keep the visit either way.")
    source_day_label: Optional[str] = Field(
        default=None,
        description="Exact timing text printed by the protocol, such as 'Day 0', "
        "'Day 8', 'Week 4', 'Cycle 2 Day 1', or 'Unscheduled'. Do not rewrite it "
        "as an offset. null only when no timing label is present.")
    calendar_offset_value: Optional[float] = Field(
        default=None,
        description="Set only when day_offset was approximated from a calendar "
        "Month/Year label with no exact day count (30 days/month, 365 days/year). "
        "The protocol's own number, e.g. 3 for 'Month 3' (negative if before "
        "baseline). null whenever day_offset is exact or unset.")
    calendar_offset_unit: Optional[Literal["month", "year"]] = Field(
        default=None,
        description="Unit paired with calendar_offset_value: 'month' or 'year'. "
        "null whenever calendar_offset_value is null.")
    day_end: Optional[int] = Field(
        default=None,
        description="For a visit that spans MULTIPLE consecutive days as a single entry "
        "(e.g. 'Day 14-17', a period's 'Check-in / Day 1 / Check-out'), the absolute end "
        "day (same Day 1 = 0 basis). null for single-day visits.")
    hour_offset: Optional[float] = Field(
        default=None,
        description="For INTRA-DAY timepoints (PK sampling, hourly assessments), hours "
        "from dosing/Hour 0. May be negative for pre-dose. This is an absolute elapsed "
        "hour, so Hour 26 is 26 total hours and is not added on top of its containing "
        "day_offset. Only use for a genuinely hour-level schedule.")
    hour_offset_basis: Optional[Literal["absolute", "within_day"]] = Field(
        default=None,
        description="'absolute' when hour_offset is elapsed time from the schedule "
        "anchor. 'within_day' is reserved for an hour adjustment to day_offset.")
    hour_end: Optional[float] = Field(
        default=None,
        description="End of an hour RANGE, e.g. 'Hour -4 to Hour 0' -> hour_offset=-4, "
        "hour_end=0. null for a single timepoint.")
    window_days: Optional[int] = Field(
        default=None, ge=0,
        description="Visit window as a single +/- number of days (e.g. '+/- 3 days' -> 3). "
        "For an asymmetric window use the larger side here and also set window_before / "
        "window_after. null when no window is stated; never invent a default.")
    window_before: Optional[int] = Field(
        default=None,
        description="Days the visit may occur EARLY, when the protocol gives an asymmetric "
        "window (e.g. a '+3 days only' window -> window_before=0, window_after=3). null "
        "when the window is symmetric or unstated.")
    window_after: Optional[int] = Field(
        default=None,
        description="Days the visit may occur LATE for an asymmetric window. null when "
        "symmetric or unstated.")
    relative_to: Optional[str] = Field(
        default=None,
        description="When the protocol times this visit against ANOTHER visit rather than "
        "against baseline ('within 3 days after intra-cycle visit 3', '28 days after the "
        "last dose'), put that other visit's exact `name` here and the gap in "
        "relative_offset_days. The server resolves it to an absolute day.")
    relative_offset_days: Optional[int] = Field(
        default=None,
        description="Days after the `relative_to` visit (negative for before).")
    arm: Optional[str] = Field(
        default=None,
        description="Arm / cohort label when the protocol prints genuinely DIFFERENT "
        "schedules per arm. null when all arms share one schedule.")
    period: Optional[str] = Field(
        default=None,
        description="Period / phase label for crossover or multi-phase studies "
        "('Period 1', 'Washout 1', 'Extension'). null when not applicable.")
    activities: List[str] = Field(
        default_factory=list,
        description="Assessments / procedures marked (X or a footnote symbol) in this "
        "visit's column, using the protocol's own procedure names, deduplicated and "
        "concise (e.g. 'Vitals', 'ECG', 'PK sampling', 'Randomization').")
    procedures: List[ExtractedProcedure] = Field(
        default_factory=list,
        description="Structured activity details, including activity-level timing and windows.")
    operational_constraints: List[str] = Field(
        default_factory=list,
        description="Non-visit-window constraints such as housing minimums, infusion duration, "
                    "washout gaps, PK tolerances, and conditional rules.")
    canonical_event_id: Optional[str] = None
    extraction_warning: bool = False
    review_status: Literal["pending", "ok"] = "ok"
    field_evidence: List[FieldEvidence] = Field(
        default_factory=list,
        description="Evidence IDs supporting this visit's name, timing, window, "
                    "activities, arm, and period. Unsupported fields must remain null.")

    @model_validator(mode="after")
    def default_extracted_hour_semantics(self):
        # Extracted Hour N values are absolute elapsed time. Persist the mode so
        # scheduling cannot count the day portion twice. Historical database
        # rows lack this field and retain their legacy day-plus-hour behavior.
        if self.hour_offset is not None and self.hour_offset_basis is None:
            self.hour_offset_basis = "absolute"
        return self


class ScheduleDraft(BaseModel):
    """AI-authored schedule fields supported by Gemini's structured-output API."""
    schedule_kind: Optional[str] = Field(
        default=None,
        description="One of: 'linear' (fixed visit list), 'cyclic' (repeating cycles), "
        "'crossover' (periods + washout), 'multi_arm' (different schedule per arm), "
        "'intra_day' (hour-level timepoints only), 'none' (document has no schedule).")
    # Deliberately a plain int rather than Literal[0, 1]: Gemini's structured-output
    # Schema only accepts string enum values, so an integer Literal makes the SDK
    # reject the entire request before it is sent. The validator below keeps the
    # 0/1 domain that the rest of the pipeline relies on.
    anchor_study_day: Optional[int] = Field(
        default=None,
        description="Protocol study-day number on the baseline/randomization anchor "
        "date: must be 0 or 1. null when the document is ambiguous.")
    includes_day_zero: Optional[bool] = Field(
        default=None,
        description="Whether the protocol explicitly includes Day 0. This must be "
        "known to convert non-positive labels around a Day 1 anchor.")
    visits: List[ExtractedVisit] = Field(
        default_factory=list,
        description="Explicitly-scheduled visits: screening, baseline, the visits of any "
        "cycle printed in full, end-of-treatment, follow-up, Early Termination, "
        "Unscheduled. Do NOT enumerate cycles covered by a repeating_block.")
    repeating_blocks: List[RepeatingBlock] = Field(
        default_factory=list,
        description="Cycles the protocol collapsed instead of printing. See RepeatingBlock.")
    total_cycles: Optional[int] = Field(
        default=None,
        description="Planned maximum number of treatment cycles, when stated anywhere in "
        "the document (e.g. 'maximum 6 cycles').")
    assumptions: List[str] = Field(
        default_factory=list,
        description="Any inference you had to make that a reviewer should verify — a cycle "
        "length read from a different section, an open-ended tail you bounded, an "
        "ambiguous arm structure. One short sentence each. Be honest and specific.")
    source_notes: Optional[str] = Field(
        default=None,
        description="Where in the document the schedule came from (e.g. 'Appendix I, p42; "
        "cycle length from section 2.5, p15'). Helps the reviewer check your work.")
    classification: Optional[DocumentTaskClassification] = Field(
        default=None,
        description="AI classification completed before schedule extraction.")
    canonical_plan: Optional[CanonicalSchedulePlan] = Field(
        default=None,
        description="Version-2 schedule graph preserving temporal and protocol semantics.")
    evidence_facts: List[SourceEvidence] = Field(
        default_factory=list,
        description="Atomic source facts used by the canonical plan and compatibility rows.")

    @field_validator("anchor_study_day", mode="before")
    @classmethod
    def constrain_anchor_study_day(cls, value):
        """Enforce the 0/1 domain the schema can no longer express.

        An anchor outside that domain means the model misread the convention, so
        treat it as unknown — the downstream day math already handles null, and
        failing the parse would discard an otherwise usable schedule.
        """
        if isinstance(value, str):
            value = value.strip()
            value = int(value) if value in ("0", "1") else None
        if isinstance(value, bool) or value not in (0, 1):
            return None
        return value

    @model_validator(mode="after")
    def validate_day_numbering(self):
        if self.anchor_study_day == 0:
            if self.includes_day_zero is False:
                raise ValueError(
                    "A Day 0 anchor cannot use a convention that excludes Day 0")
            self.includes_day_zero = True
        return self


class ExtractedSchedule(ScheduleDraft):
    """Schedule draft plus server-authored verification metadata."""
    verification_status: Literal["not_run", "verified", "needs_review"] = "not_run"
    verification_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    verification_iterations: int = Field(default=0, ge=0)
    verification_issues: List[str] = Field(default_factory=list)
    verification_scores: dict[str, Optional[float]] = Field(default_factory=dict)
    canonical_validation: List[str] = Field(default_factory=list)
    requires_schedule_selection: bool = Field(
        default=False,
        description="True when this document prints more than one independent "
        "Schedule of Assessments (see classification.schedule_options) and no "
        "selection was made yet. Extraction stopped after classification; "
        "visits is empty and the caller must re-run with the chosen "
        "schedule_options[].id before a real schedule is produced.")


class CanonicalScheduleResponse(BaseModel):
    """Compact provider contract: the AI authors one schedule, not two copies."""

    schedule_kind: Optional[str] = None
    anchor_study_day: Optional[int] = None
    includes_day_zero: Optional[bool] = None
    canonical_plan: CanonicalSchedulePlan
    assumptions: List[str] = Field(default_factory=list)
    source_notes: Optional[str] = None

    @field_validator("anchor_study_day", mode="before")
    @classmethod
    def constrain_anchor_study_day(cls, value):
        if isinstance(value, str):
            value = value.strip()
            value = int(value) if value in ("0", "1") else None
        return value if not isinstance(value, bool) and value in (0, 1) else None


class ExtractedTrialDetails(BaseModel):
    """Creation-form fields read from a protocol before a trial exists."""
    ctri_number: str = ""
    title: str = ""
    phase: str = ""
    indications: List[str] = Field(default_factory=list)
    drug: str = ""
    duration: str = ""
    target_enrollment: Optional[int] = None
    total_visits: Optional[int] = None
    status: str = "active"


class ExtractionError(Exception):
    """Extraction attempted but failed (bad response, upstream error)."""


class ExtractionNotConfigured(ExtractionError):
    """No credentials/backend configured — surfaced to the caller as 503."""


class ExtractionUnavailable(ExtractionError):
    """Provider reachable but refusing work (billing, quota, rate limit).

    Separated from ExtractionError so the API can tell the sponsor *why* the
    button did nothing instead of a generic 'could not extract'.
    """


# ──────────────────────────── expansion (pure) ────────────────────────────
# Deterministic, no network, no API key — this is where all schedule arithmetic
# lives so it can be unit-tested against real protocols offline.
#
# _SIMPLE_DAY_LABEL/_SIMPLE_DAY_RANGE_LABEL, study_day_to_offset,
# simple_day_label_offset and simple_day_label_range_offsets now live in
# schedule_schema.py so the SAME printed-day-number cross-check protects both
# schedule shapes: this module's legacy flat-visits path below, and the
# canonical_plan path's build_row (schedule_schema.project_canonical_plan).


def normalize_extracted_timing(
    schedule: ExtractedSchedule,
) -> tuple[List[ExtractedVisit], List[str]]:
    """Return copied visits with evidence-backed simple Day labels normalized.

    Existing schedules with no numbering metadata remain untouched. When the
    extraction supplies both a simple label and a complete convention, the
    deterministic result replaces conflicting AI arithmetic and is recorded as
    a review warning.
    """
    visits = [visit.model_copy(deep=True) for visit in schedule.visits]
    warnings: List[str] = []
    for visit in visits:
        label = str(visit.source_day_label or "")
        exact_day_label = bool(
            _SIMPLE_DAY_LABEL.fullmatch(label)
            or _SIMPLE_DAY_RANGE_LABEL.fullmatch(label)
        )
        try:
            derived = simple_day_label_offset(
                visit.source_day_label,
                anchor_study_day=schedule.anchor_study_day,
                includes_day_zero=schedule.includes_day_zero,
            )
            derived_range = simple_day_label_range_offsets(
                visit.source_day_label,
                anchor_study_day=schedule.anchor_study_day,
                includes_day_zero=schedule.includes_day_zero,
            )
        except ValueError as exc:
            warnings.append(f"'{visit.name}' has invalid day numbering: {exc}.")
            visit.day_offset = None
            visit.day_end = None
            continue
        if derived_range is not None:
            start, end = derived_range
            if visit.day_offset not in (None, start) or visit.day_end not in (None, end):
                warnings.append(
                    f"'{visit.name}' timing was corrected deterministically from "
                    f"{visit.source_day_label} and flagged for review.")
            visit.day_offset, visit.day_end = start, end
            continue
        if derived is None:
            if exact_day_label and (
                schedule.anchor_study_day is None
                or schedule.includes_day_zero is None
            ):
                warnings.append(
                    f"'{visit.name}' uses {visit.source_day_label}, but the protocol's "
                    "Day 0/Day 1 convention is incomplete; retained the extracted "
                    "offset and flagged it for review.")
            continue
        if visit.day_offset is None:
            visit.day_offset = derived
        elif visit.day_offset != derived:
            warnings.append(
                f"'{visit.name}' has day_offset {visit.day_offset}, but "
                f"{visit.source_day_label} maps to {derived}; corrected it "
                "deterministically and flagged it for review.")
            visit.day_offset = derived
    return visits, warnings


def canonical_elapsed_time(
    day_offset: Optional[int],
    hour_offset: Optional[float],
    hour_offset_basis: Optional[str],
) -> timedelta:
    """Return elapsed time without double-counting absolute Hour 26 values.

    New extraction rows declare ``absolute``. A legacy row with no basis keeps
    its old day-plus-hour behavior so existing saved dates never move.
    """
    hours = float(hour_offset or 0)
    if not math.isfinite(hours):
        raise ValueError("hour_offset must be a finite number")
    if hour_offset_basis not in (None, "absolute", "within_day"):
        raise ValueError("hour_offset_basis must be absolute or within_day")
    absolute = hour_offset_basis == "absolute"
    if absolute:
        return timedelta(hours=hours)
    if day_offset is None:
        raise ValueError("Visit has no calculable day offset")
    return timedelta(days=int(day_offset), hours=hours)


def _visit_elapsed_seconds(visit: ExtractedVisit) -> float:
    """Comparable elapsed time for chronological ordering of dated visits."""
    try:
        return canonical_elapsed_time(
            visit.day_offset, visit.hour_offset, visit.hour_offset_basis,
        ).total_seconds()
    except (TypeError, ValueError):
        # Invalid arithmetic is retained for human review; deterministic day
        # order is still preferable to dropping the visit.
        if visit.day_offset is None:
            return math.inf
        return timedelta(days=visit.day_offset).total_seconds()

def _fill_template(template: str, cycle: int) -> str:
    """Substitute the cycle number into a member name template.

    Uses explicit replacement rather than str.format so a stray brace in a
    model-authored template can never raise.
    """
    out = template
    for token in ("{cycle}", "{c}", "{n}", "{CYCLE}"):
        out = out.replace(token, str(cycle))
    if str(cycle) not in out:
        # Template forgot the placeholder — disambiguate so cycles don't collide.
        out = f"{out} (Cycle {cycle})"
    return out


def _expand_blocks(schedule: ExtractedSchedule,
                   assumptions: List[str],
                   warnings: List[str]) -> List[ExtractedVisit]:
    """Turn each RepeatingBlock into concrete per-cycle visits."""
    out: List[ExtractedVisit] = []
    for block in schedule.repeating_blocks:
        if block.cycle_length_days <= 0:
            warnings.append(
                f"Ignored a repeating block starting at cycle {block.from_cycle}: "
                f"cycle length {block.cycle_length_days} is not a positive number of days.")
            continue
        if not block.members:
            warnings.append(
                f"Ignored a repeating block starting at cycle {block.from_cycle}: "
                "it listed no visits.")
            continue

        to_cycle = block.to_cycle
        if to_cycle is None:
            to_cycle = block.from_cycle + OPEN_ENDED_PREVIEW_CYCLES - 1
            if schedule.total_cycles and schedule.total_cycles >= block.from_cycle:
                # A protocol-stated maximum IS the schedule limit, so honour it.
                to_cycle = schedule.total_cycles
                assumptions.append(
                    f"Cycles {block.from_cycle}-{to_cycle} were expanded using the "
                    f"protocol's stated maximum of {schedule.total_cycles} cycles.")
            else:
                # No stated maximum: what follows is a preview, not the schedule.
                assumptions.append(
                    f"PREVIEW ONLY - the protocol leaves the schedule open-ended from "
                    f"cycle {block.from_cycle}. The rows shown are the first "
                    f"{OPEN_ENDED_PREVIEW_CYCLES} occurrences (to cycle {to_cycle}) for "
                    f"review; cycle {to_cycle} is NOT the last cycle. The schedule "
                    f"continues per the protocol until its stopping criteria are met. "
                    f"Confirm the real number before saving.")
        if to_cycle < block.from_cycle:
            warnings.append(
                f"Ignored a repeating block: last cycle ({to_cycle}) is before the "
                f"first ({block.from_cycle}).")
            continue

        for cycle in range(block.from_cycle, to_cycle + 1):
            cycle_start = (block.first_cycle_start_day
                           + (cycle - block.from_cycle) * block.cycle_length_days)
            for member in block.members:
                acts = list(member.activities)
                for cond in member.conditional_activities:
                    if cycle in (cond.cycles or []):
                        acts.append(cond.name)
                out.append(ExtractedVisit(
                    name=_fill_template(member.name_template, cycle),
                    source_day_label=(
                        _fill_template(member.source_day_label_template, cycle)
                        if member.source_day_label_template else None
                    ),
                    visit_type=member.visit_type,
                    day_offset=cycle_start + member.day_within_cycle,
                    day_end=(
                        cycle_start + member.day_end_within_cycle
                        if member.day_end_within_cycle is not None else None
                    ),
                    hour_offset=member.hour_offset,
                    hour_offset_basis=member.hour_offset_basis,
                    hour_end=member.hour_end,
                    window_days=member.window_days,
                    window_before=member.window_before,
                    window_after=member.window_after,
                    arm=member.arm,
                    period=member.period,
                    activities=acts,
                    field_evidence=member.field_evidence,
                ))
    return out


def _resolve_relative(visits: List[ExtractedVisit], warnings: List[str]) -> None:
    """Resolve visits timed against another visit into absolute day offsets.

    Runs to a fixed point so a chain (A -> B -> C) resolves, and stops rather
    than looping when a cycle is present.
    """
    by_name: dict[str, List[ExtractedVisit]] = {}
    for visit in visits:
        if visit.name:
            by_name.setdefault(visit.name.strip().lower(), []).append(visit)
    pending = [v for v in visits
               if v.day_offset is None and v.relative_to and v.relative_offset_days is not None]
    for _ in range(len(pending) + 1):
        progressed = False
        for v in list(pending):
            matches = by_name.get((v.relative_to or "").strip().lower(), [])
            scoped = [candidate for candidate in matches
                      if candidate.arm == v.arm and candidate.period == v.period]
            target = scoped[0] if len(scoped) == 1 else (
                matches[0] if len(matches) == 1 else None)
            if target is not None and target.day_offset is not None:
                v.day_offset = target.day_offset + int(v.relative_offset_days or 0)
                pending.remove(v)
                progressed = True
        if not pending or not progressed:
            break
    for v in pending:
        matches = by_name.get((v.relative_to or "").strip().lower(), [])
        ambiguity = " is ambiguous" if len(matches) > 1 else " has no resolvable date"
        warnings.append(
            f"'{v.name}' is scheduled relative to '{v.relative_to}', which{ambiguity} "
            "for its arm/period — set its day manually.")


def expand_schedule(schedule: ExtractedSchedule) -> ExtractedSchedule:
    """Expand declared structure into the flat visit list the app consumes.

    Pure and deterministic: same input always yields the same visits. Returns a
    NEW ExtractedSchedule; the input is not mutated.
    """
    assumptions: List[str] = list(schedule.assumptions)
    warnings: List[str] = []
    if schedule.canonical_plan is not None:
        # The AI authors one canonical graph.  Flat rows are a deterministic
        # compatibility projection and any model-authored duplicate rows are ignored.
        projected, projection_warnings = project_canonical_plan(
            schedule.canonical_plan,
            open_ended_preview_count=OPEN_ENDED_PREVIEW_CYCLES,
            anchor_study_day=schedule.anchor_study_day,
            includes_day_zero=schedule.includes_day_zero,
        )
        visits = [ExtractedVisit.model_validate(row) for row in projected]
        warnings.extend(projection_warnings)
        canonical_plan = schedule.canonical_plan
    else:
        # Historical/API callers may still provide flat visits.  Normalize those
        # first, then construct a canonical fallback from the normalized structure.
        visits, timing_warnings = normalize_extracted_timing(schedule)
        warnings.extend(timing_warnings)
        normalized = schedule.model_copy(update={"visits": visits})
        canonical_plan = canonical_from_flat(normalized)
        visits.extend(_expand_blocks(normalized, assumptions, warnings))
        _resolve_relative(visits, warnings)

    # Drop exact duplicates — a model that both enumerated cycle 2 AND described
    # it in a repeating block should not double-book the patient.
    seen: set = set()
    deduped: List[ExtractedVisit] = []
    for v in visits:
        key = (
            v.name.strip().lower(), v.day_offset, v.hour_offset,
            v.hour_offset_basis, v.day_end, v.hour_end,
            (v.arm or "").strip().lower(), (v.period or "").strip().lower(),
            (v.source_day_label or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(v)

    if len(deduped) > MAX_EXPANDED_VISITS:
        warnings.append(
            f"Schedule expanded to {len(deduped)} visits; kept the first "
            f"{MAX_EXPANDED_VISITS}. Check the cycle count before saving.")
        deduped = deduped[:MAX_EXPANDED_VISITS]

    # Chronological, with undated visits (ET / Unscheduled) last but preserved.
    deduped.sort(key=lambda v: (
        _visit_elapsed_seconds(v) == math.inf,
        _visit_elapsed_seconds(v),
    ))

    result = schedule.model_copy(update={
        "visits": deduped,
        "repeating_blocks": [],     # consumed
        "assumptions": assumptions + warnings,
        "verification_status": (
            "needs_review" if warnings else schedule.verification_status
        ),
        "verification_issues": list(schedule.verification_issues) + warnings,
    })
    canonical_issues = validate_canonical_plan(
        canonical_plan,
        ({item.evidence_id for item in result.evidence_facts}
         if result.evidence_facts else None),
    )
    return result.model_copy(update={
        "canonical_plan": canonical_plan,
        "canonical_validation": canonical_issues,
        "verification_status": (
            "needs_review" if warnings or canonical_issues else result.verification_status
        ),
        "verification_issues": list(result.verification_issues) + canonical_issues,
    })
