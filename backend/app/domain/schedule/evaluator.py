from __future__ import annotations


from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from .tracks import (
    apply_overlays as apply_track_overlays, choose_rebased, rebase_variants,
)
from .dependency import (
    DependencyOutcomeKind, active_source_codes, aggregate as aggregate_dependency_outcomes,
    evaluate_dependencies, selected_indexes,
)
from .calendar_math import _is_aware, _normalise_wall_clock, add_amount  # noqa: F401 (re-exported)
from .condition import TruthValue, evaluate_condition
from .conditional import ConditionalPlan, EventActivation, build_conditional_plan
from .exceptions import ScheduleNotApprovedError, UnsupportedTimingError
from .temporal_basis import effective_temporal_basis
from .timing import TemporalBasis
from .feasibility import (
    KNOWN_CALENDAR_POLICIES,
    BaseWindow,
    FeasibilityResult,
    Resolved,
    UnsupportedConstraintError,
    evaluate_feasibility,
    interval_contains,
)
from .calendar_recurrence import (
    HoldWindow,
    expand_calendar,
    resolve_calendar,
)
from .facts import (
    AssignmentPeriod,
    OccurrenceFacts,
    annotate_occurrence,
    assignment_periods,
)
from .identity import activity_logical_key, episode_occurrence_index, occurrence_identity
from .processes import ProcessStates, apply_process_states, build_process_states
from .models import (
    RecurrenceBasis,
    LogicalOccurrenceState,
    Activity,
    ApplicabilityRule,
    BlockState,
    CalendarRecurrence,
    ConfinementEpisodeDefinition,
    ConfinementStatus,
    CycleDependencyMode,
    CyclePlan,
    EvaluatedConfinement,
    EvaluatedConfinementDay,
    DependencyMode,
    EvaluatedActivity,
    EvaluatedEvent,
    EvaluationResult,
    Event,
    FeasibilityStatus,
    MissingInput,
    OccurrenceFactKind,
    PatientActivityStatus,
    PatientContext,
    PatientEventStatus,
    RecurrencePlan,
    RecurrenceTermination,
    RepeatSummary,
    Requiredness,
    ResolvedTiming,
    RollingHorizon,
    ScheduleStatus,
    UniversalSchedule,
    UnscheduledOccurrence,
    cycle_start_anchor_code,
)
from .timing import (
    AbsoluteTiming,
    ActivityReference,
    ActivityValue,
    AnchorAcceptance,
    AnchorReference,
    AnchorValue,
    ApproximateTiming,
    ConstraintTiming,
    EventValue,
    CycleDayTiming,
    EventReference,
    NoLaterThanTiming,
    NominalWindowTiming,
    OffsetTiming,
    ProcessBoundaryValue,
    ProtocolDefinedTiming,
    ProtocolDayTiming,
    RangeTiming,
    TemporalAmount,
    TemporalReference,
    TimeUnit,
    TriggerNoLaterThan,
    TriggerOffset,
    TriggeredTiming,
    TriggerWithin,
    UnresolvedTiming,
    Window,
    WithinTiming,
)
from .validator import own_event_references
from .validator import (
    ScheduleValidator,
    event_temporal_references,
    timing_references,
    topological_event_codes,
)


Temporal = date | datetime


def _confinement_integrity(
    planned_admission: "Temporal | None",
    planned_discharge: "Temporal | None",
    actual_admission: "Temporal | None",
    actual_discharge: "Temporal | None",
) -> list[str]:
    """Doc 6 s26 rule 1: discharge must not precede admission."""
    findings: list[str] = []
    for label, admission, discharge in (
        ("planned", planned_admission, planned_discharge),
        ("actual", actual_admission, actual_discharge),
    ):
        if admission is not None and discharge is not None and not _before_or_equal(
                admission, discharge):
            findings.append(
                f"{label} discharge precedes {label} admission; the episode dates "
                "need review before the stay can be scheduled"
            )
    return findings


def _flag_overlapping_episodes(
    episodes: list[EvaluatedConfinement],
) -> list[EvaluatedConfinement]:
    """Doc 6 s26 rules 2 and 3: a patient cannot be in two stays at once.

    Overlap is judged on the dates the patient is actually committed to, so an
    episode that is NOT_APPLICABLE or still waiting on its anchor is ignored.
    """
    datable = [
        item for item in episodes
        if item.status not in {
            ConfinementStatus.NOT_APPLICABLE, ConfinementStatus.CANCELLED,
            ConfinementStatus.WAITING_FOR_ANCHOR,
        }
        and (item.actual_admission or item.planned_admission) is not None
    ]

    def span(item: EvaluatedConfinement) -> tuple[Temporal, Temporal]:
        start = item.actual_admission or item.planned_admission
        end = item.actual_discharge or item.planned_discharge or start
        assert start is not None
        return start, end

    conflicts: dict[str, list[str]] = {}
    for index, left in enumerate(datable):
        left_start, left_end = span(left)
        for right in datable[index + 1:]:
            right_start, right_end = span(right)
            if _before_or_equal(left_start, right_end) and _before_or_equal(
                    right_start, left_end):
                conflicts.setdefault(left.episode_code, []).append(right.episode_code)
                conflicts.setdefault(right.episode_code, []).append(left.episode_code)
    if not conflicts:
        return episodes

    updated: list[EvaluatedConfinement] = []
    for item in episodes:
        overlapping = conflicts.get(item.episode_code)
        if not overlapping:
            updated.append(item)
            continue
        explanation = dict(item.explanation)
        explanation["overlapping_episodes"] = sorted(set(overlapping))
        explanation["reason"] = (
            "this stay overlaps another confinement episode; a patient cannot be "
            "admitted to two stays at the same time"
        )
        updated.append(item.model_copy(update={
            "status": ConfinementStatus.WAITING_FOR_ANCHOR,
            "explanation": explanation,
        }))
    return updated


def _evaluate_on_demand(
    event: Event,
    context: PatientContext,
    base: dict[str, object],
    explanation: dict[str, object],
) -> list[EvaluatedEvent]:
    """Doc 10 s13-s15: an unscheduled definition, plus whatever was created.

    The definition itself is always returned so the schedule shows it exists and
    a site can create one. It is AVAILABLE_ON_DEMAND, so nothing downstream can
    read it as due, overdue, or missed. Each occurrence a site actually created
    is returned as a dated, completed visit carrying the definition's activities.
    """
    created = [
        item for item in context.unscheduled_occurrences
        if item.event_code == event.code
    ]
    definition_explanation = dict(explanation)
    definition_explanation["reason"] = (
        "this visit is defined by the protocol but occurs only when clinically "
        "indicated; it becomes due when a site creates one"
    )
    definition_explanation["on_demand"] = True
    output = [EvaluatedEvent(
        **{**base, "explanation": definition_explanation},
        status=PatientEventStatus.AVAILABLE_ON_DEMAND,
    )]
    for index, occurrence in enumerate(
            sorted(created, key=lambda item: _sort_value(item.occurred_on))):
        occurrence_explanation = dict(explanation)
        occurrence_explanation["reason"] = (
            f"unscheduled visit created by the site: {occurrence.reason}"
        )
        occurrence_explanation["unscheduled"] = {
            "reason": occurrence.reason,
            "recorded_at": occurrence.recorded_at.isoformat(),
        }
        mode = occurrence.visit_mode or event.visit_mode
        output.append(EvaluatedEvent(
            **{
                **base, "explanation": occurrence_explanation,
                "visit_mode": mode,
            },
            occurrence_index=index,
            status=PatientEventStatus.RESOLVED,
            unscheduled_reason=occurrence.reason,
            timing=ResolvedTiming(
                nominal_start=occurrence.occurred_on,
                nominal_end=occurrence.occurred_on,
                precision="EXACT",
            ),
            activities=evaluate_activities(event, context, occurrence_index=index),
        ))
    return output


def _with_block_reason(
    item: EvaluatedEvent,
    block_code: str,
    status: PatientEventStatus,
    condition_code: str,
    reason: str,
    block_state: BlockState,
) -> EvaluatedEvent:
    explanation = dict(item.explanation)
    explanation["block_state"] = {
        "block": block_code, "state": block_state.value, "condition": condition_code,
    }
    explanation["reason"] = reason
    return item.model_copy(update={"status": status, "explanation": explanation})


def _containing_pause(
    value: Temporal,
    pauses: list[tuple[Temporal, Temporal | None]],
) -> tuple[Temporal, Temporal | None] | None:
    """The pause period this date falls in, treating an open pause as unbounded.

    A pause covers its own start day (doc I1: held on 26-Oct, the C2 dose due
    26-Oct is PAUSED, not left due and then overdue) up to, not including, the
    day treatment resumes.
    """
    for start, end in pauses:
        if not _before_or_equal(start, value):
            continue
        if end is None or not _before_or_equal(end, value):
            return start, end
    return None


def _pause_days_before(
    value: Temporal,
    pauses: list[tuple[Temporal, Temporal | None]],
) -> int:
    """Total days the block was paused before this occurrence."""
    total = 0
    for start, end in pauses:
        if end is None or not _before_or_equal(end, value):
            continue
        left = start.date() if isinstance(start, datetime) else start
        right = end.date() if isinstance(end, datetime) else end
        total += (right - left).days
    return total


def _shift(value: Temporal | None, days: int) -> Temporal | None:
    return None if value is None else value + timedelta(days=days)


def _apply_resume_mode(
    item: EvaluatedEvent,
    block: "RepeatBlock",
    pauses: list[tuple[Temporal, Temporal | None]],
    resumed: Temporal,
) -> EvaluatedEvent:
    """Doc 3 s20: what the cadence does after a pause ends."""
    explanation = dict(item.explanation)
    explanation["resume"] = {
        "block": block.code, "mode": block.resume_mode,
        "resumed_on": resumed.isoformat(),
    }
    if block.resume_mode == "NOMINAL":
        # The grid never moved; the paused occurrences are simply gone.
        explanation["reason"] = (
            f"repeating block {block.code} resumed on its original schedule; "
            "occurrences during the pause were not made up"
        )
        return item.model_copy(update={"explanation": explanation})

    if block.resume_mode == "ACTUAL_RESUME":
        offset = _pause_days_before(
            item.timing.nominal_start if item.timing else resumed, pauses)
        if offset == 0 or item.timing is None:
            explanation["reason"] = (
                f"repeating block {block.code} restarted from the actual resume date"
            )
            return item.model_copy(update={"explanation": explanation})
        timing = item.timing.model_copy(update={
            "nominal_start": _shift(item.timing.nominal_start, offset),
            "nominal_end": _shift(item.timing.nominal_end, offset),
            "earliest": _shift(item.timing.earliest, offset),
            "latest": _shift(item.timing.latest, offset),
        })
        explanation["resume"]["shifted_days"] = offset
        explanation["reason"] = (
            f"repeating block {block.code} restarted from the actual resume date, "
            f"moving this occurrence {offset} days later"
        )
        return item.model_copy(update={"timing": timing, "explanation": explanation})

    # MANUAL or UNCLEAR: the protocol did not say, so no date is invented.
    explanation["reason"] = (
        f"repeating block {block.code} resumed but the protocol does not state "
        "whether the cadence continues on its original schedule or restarts from "
        "the resume date; confirm before scheduling"
    )
    return item.model_copy(update={
        "status": PatientEventStatus.UNRESOLVED,
        "timing": None,
        "explanation": explanation,
    })


def _cadence_text(amount: object) -> str:
    value = getattr(amount, "value", None)
    unit = getattr(amount, "unit", None)
    if value is None or unit is None:
        return "on a repeating schedule"
    word = str(unit).lower()
    if value == 1:
        return f"every {word[:-1] if word.endswith('s') else word}"
    return f"every {value} {word if word.endswith('s') else word + 's'}"


def _milestone_text(amount: TemporalAmount) -> str:
    """'+24 MONTH' reads as 'year 2'; anything else keeps its own unit."""
    if amount.unit == TimeUnit.MONTH and amount.value % 12 == 0:
        return f"year {amount.value // 12}"
    return f"{amount.unit.value.lower()} {amount.value}"


def _interval_text(amount: object) -> str:
    unit = getattr(amount, "unit", None)
    value = getattr(amount, "value", None)
    if (unit, value) in {(TimeUnit.YEAR, 1), (TimeUnit.MONTH, 12)}:
        return "yearly"
    return _cadence_text(amount)


def calendar_cadence_text(rule: CalendarRecurrence) -> str:
    """'every Mon, Tue, Wed, Thu, Fri' / 'every 2nd week on Tue'."""
    days = ", ".join(item.value.title() for item in rule.weekdays)
    if rule.interval_weeks == 1:
        return f"every {days}"
    return f"every {rule.interval_weeks} weeks on {days}"


def plan_segment_texts(plan: RecurrencePlan) -> list[str]:
    """One phrase per cadence of a phased plan, in order (doc E9)."""
    output: list[str] = []
    for segment in plan.segments:
        text = _interval_text(segment.interval)
        if segment.end_offset is not None:
            text += f" to {_milestone_text(segment.end_offset)}"
        elif segment.count is not None:
            text += f" for {segment.count} visits"
        output.append(text)
    return output


def plan_cadence_text(plan: RecurrencePlan) -> str:
    """'every 3 months to year 2, then every 6 months to year 5, then yearly'."""
    return ", then ".join(plan_segment_texts(plan))


def _termination_text(termination: object, noun: str = "occurrences") -> str:
    """Plain wording for how a repeat ends, never implying a cap we invented."""
    kind = getattr(termination, "type", None)
    if kind == "COUNT":
        count = getattr(termination, "count", None)
        return f"a protocol maximum of {count} {noun}"
    if kind == "DATE":
        value = getattr(termination, "termination_date", None)
        return f"until {value.isoformat()}" if value else "until a stated date"
    if kind == "EVENT":
        return f"until {getattr(termination, 'event_code', 'a later event')}"
    if kind == "CONDITION":
        return "until the protocol stopping condition is met"
    if kind == "RELATIVE":
        offset = termination.end_offset
        unit = offset.unit.value.lower()
        boundary = "through" if termination.inclusive else "before"
        return f"{boundary} {offset.value} {unit if abs(offset.value) == 1 else unit + 's'} after {termination.end_reference.code}"
    return "no stated maximum; the protocol continues while the patient remains on study"


def _apply_rolling_horizon(
    events: list[EvaluatedEvent],
    schedule: UniversalSchedule,
    plan: ConditionalPlan,
    rolling: RollingHorizon | None,
    horizon: date,
) -> tuple[list[EvaluatedEvent], list[RepeatSummary]]:
    """Doc 3 s7-s8: keep a bounded rolling set of upcoming occurrences.

    The cap governs what is MATERIALIZED, never how long the protocol runs. Every
    repeating rule that has more to come is reported in a RepeatSummary, so the
    caller can say "continues" rather than showing the last kept row as the end
    (doc 3 s2, s4, s36).
    """
    blocks_by_event = {
        code: block.code
        for block in schedule.repeat_blocks for code in block.event_codes
    }
    cycle_plans = schedule_cycle_plans(schedule)
    repeating = {
        event.code: event for event in schedule.events
        if event.repeats or _every_cycle_plan(event, cycle_plans) is not None
    }
    for code in plan.activations:
        if plan.activations[code].repeat_interval is not None and code in {
                event.code for event in schedule.events}:
            repeating.setdefault(code, next(
                event for event in schedule.events if event.code == code))

    as_of = (rolling.as_of if rolling and rolling.as_of else None) or date.today()
    limit = rolling.upcoming_limit if rolling else None

    summaries: list[RepeatSummary] = []
    kept: list[EvaluatedEvent] = []
    by_code: dict[str, list[EvaluatedEvent]] = {}
    for item in events:
        by_code.setdefault(item.event_code, []).append(item)

    for item_code, group in by_code.items():
        event = repeating.get(item_code)
        if event is None:
            kept.extend(group)
            continue

        activation = plan.activations.get(item_code)
        rule = event.recurrence
        phased = event.recurrence_plan
        weekly = event.calendar_recurrence
        cycles = _every_cycle_plan(event, cycle_plans)
        future_dated = [
            item for item in group
            if item.timing is not None and item.timing.nominal_start is not None
            and not _before_or_equal(item.timing.nominal_start, as_of)
            and item.status == PatientEventStatus.RESOLVED
        ]
        dropped: list[EvaluatedEvent] = []
        if limit is not None and len(future_dated) > limit:
            dropped = future_dated[limit:]
        dropped_ids = {id(item) for item in dropped}
        kept.extend(item for item in group if id(item) not in dropped_ids)

        # "Continues" is true when the protocol has more to give: either we chose
        # not to write it down, or the date horizon - not the protocol - ended it.
        open_ended = (
            rule is not None and rule.termination.type == "HORIZON"
        ) or (
            phased is not None and phased.termination is not None
            and phased.termination.type == "HORIZON"
        ) or (
            cycles is not None and cycles.termination.type == "HORIZON"
        ) or (
            weekly is not None and weekly.termination.type == "HORIZON"
        ) or (
            activation is not None and activation.repeat_until_resolved
            and activation.resolution_date is None
        )
        last = group[-1].timing.nominal_start if group and group[-1].timing else None
        horizon_bounded = bool(
            open_ended and last is not None and not dropped
            and _before_or_equal(_as_date(last), horizon)
        )
        if not dropped and not horizon_bounded:
            continue
        segments: list[str] = []
        if phased is not None:
            cadence = plan_cadence_text(phased)
            segments = plan_segment_texts(phased)
            last = phased.segments[-1]
            if phased.termination is not None:
                termination = _termination_text(phased.termination)
            elif last.end_offset is not None:
                boundary = "through" if last.end_inclusive else "before"
                termination = f"{boundary} {_milestone_text(last.end_offset)}"
            else:
                termination = f"after {last.count} visits in the final cadence"
        elif cycles is not None:
            cadence = f"every cycle ({_cadence_text(cycles.length)[len('every '):]})"
            termination = _termination_text(cycles.termination, "cycles")
        elif weekly is not None:
            cadence = calendar_cadence_text(weekly)
            termination = _termination_text(weekly.termination, "sessions")
        else:
            cadence_source = (
                rule.interval if rule is not None
                else (activation.repeat_interval if activation else None)
            )
            cadence = _cadence_text(cadence_source)
            termination = (
                _termination_text(rule.termination) if rule is not None
                else "until the condition is marked resolved"
            )
        summaries.append(RepeatSummary(
            event_code=item_code,
            block_code=blocks_by_event.get(item_code),
            cadence=cadence,
            termination=termination,
            segments=segments,
            materialized_count=len(group) - len(dropped),
            continues=True,
            next_unmaterialized=(
                dropped[0].timing.nominal_start if dropped and dropped[0].timing else None
            ),
        ))
    kept.sort(key=lambda item: (
        _sort_value(item.timing.nominal_start if item.timing else None),
        item.event_code, item.occurrence_index,
    ))
    return kept, summaries


def _as_date(value: Temporal) -> date:
    return value.date() if isinstance(value, datetime) else value


def _sort_value(value: Temporal | None) -> tuple[int, date]:
    if value is None:
        return (1, date.max)
    return (0, _as_date(value))


def _elapsed_day(clinical_day: int) -> int:
    """Convert a clinical day number to days elapsed from Day 1 (there is no Day 0)."""
    return clinical_day - 1 if clinical_day > 0 else clinical_day


def _first_nominal(results: "list[EvaluatedEvent] | None") -> Temporal | None:
    for item in results or []:
        if item.timing is not None and item.timing.nominal_start is not None:
            return item.timing.nominal_start
    return None


def _first_actual(context: PatientContext, event_code: str) -> Temporal | None:
    values = context.actual_event_values.get(event_code) or []
    return min(values) if values else None


def _before_or_equal(left: Temporal, right: Temporal) -> bool:
    """Compare values that may mix dates and datetimes."""
    if isinstance(left, datetime) and not isinstance(right, datetime):
        return left.date() <= right
    if isinstance(right, datetime) and not isinstance(left, datetime):
        return left <= right.date()
    return left <= right


def _localise(value: Temporal, zone: tzinfo) -> Temporal:
    """Read a naive recorded date-time as local site time; dates stay dates."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value


def localise_context(context: PatientContext) -> PatientContext:
    """Doc K2: give every naive recorded date-time the site's time zone.

    Without a zone a naive 00:30 has no defined elapsed-time relationship to
    anything, so hour-based rules could not be computed correctly across a
    clock change. Values already carrying a zone are left as recorded.
    """
    if context.time_zone is None:
        return context
    zone = ZoneInfo(context.time_zone)
    localised = context.model_copy(deep=True)
    for mapping in (localised.anchors, localised.planned_anchors, localised.activity_actuals):
        for key, value in list(mapping.items()):
            mapping[key] = _localise(value, zone)
    for mapping in (localised.event_values, localised.actual_event_values):
        for key, values in list(mapping.items()):
            mapping[key] = [_localise(value, zone) for value in values]
    return localised


_UNIT_FAMILIES: dict[TimeUnit, tuple[TimeUnit, int]] = {
    TimeUnit.MINUTE: (TimeUnit.MINUTE, 1),
    TimeUnit.HOUR: (TimeUnit.MINUTE, 60),
    TimeUnit.DAY: (TimeUnit.DAY, 1),
    TimeUnit.WEEK: (TimeUnit.DAY, 7),
    TimeUnit.MONTH: (TimeUnit.MONTH, 1),
    TimeUnit.YEAR: (TimeUnit.MONTH, 12),
}


def nth_occurrence(
    origin: Temporal,
    interval: TemporalAmount,
    n: int,
    offset: TemporalAmount | None = None,
) -> Temporal:
    """Occurrence ``n`` of a repeat, counted from its ORIGINAL start (doc E6/E8).

    Adding the interval to the previous occurrence lets a month-end clamp
    stick: monthly from 31-Jan gives 28-Feb, then 28-Mar forever. Counting
    every occurrence from the start keeps the protocol's day: 28-Feb, 31-Mar,
    30-Apr; yearly from 29-Feb-2028 returns to 29-Feb in 2032. A stated
    first-occurrence offset is folded into the same single calculation when
    its unit is commensurable with the interval, so it cannot introduce a
    clamp of its own either. Days and weeks are unaffected mathematically.
    """
    if offset is None:
        return add_amount(origin, interval, n)
    offset_family, offset_scale = _UNIT_FAMILIES[offset.unit]
    interval_family, interval_scale = _UNIT_FAMILIES[interval.unit]
    if offset_family == interval_family:
        total = offset.value * offset_scale + n * interval.value * interval_scale
        return add_amount(origin, TemporalAmount(value=total, unit=offset_family))
    # Different calendars (e.g. "2 weeks after X, then monthly"): the offset
    # fixes the start once, and every repeat is still counted from that start.
    return add_amount(add_amount(origin, offset), interval, n)


def _sum_offsets(
    left: TemporalAmount | None, right: TemporalAmount,
) -> TemporalAmount | None:
    """Add two offsets in one calendar, or None when they cannot be combined
    exactly (weeks and months), so the caller falls back to a dated origin."""
    if left is None:
        return right
    left_family, left_scale = _UNIT_FAMILIES[left.unit]
    right_family, right_scale = _UNIT_FAMILIES[right.unit]
    if left_family != right_family:
        return None
    return TemporalAmount(
        value=left.value * left_scale + right.value * right_scale, unit=left_family)


def effective_window(event: Event) -> Window | None:
    """The window stated for this visit, wherever the schedule stated it.

    ``Event.window`` is the independent rule (doc B2/B3). A NOMINAL_WITH_WINDOW
    timing still supplies one for schedules written before that existed. No
    window is ever invented: an event stating neither has none (doc B4).
    """
    if event.window is not None:
        return event.window
    if isinstance(event.timing, NominalWindowTiming):
        return event.timing.window
    return None


def _window_bounds(
    target: Temporal, window: Window | None,
) -> tuple[Temporal | None, Temporal | None]:
    if window is None:
        return None, None
    return (
        add_amount(target, TemporalAmount(value=-window.before.value, unit=window.before.unit)),
        add_amount(target, TemporalAmount(value=window.after.value, unit=window.after.unit)),
    )


#: Resource limit on one interval series per evaluation. Never a clinical maximum.
RECURRENCE_SAFETY_CAP = 10000


@dataclass
class _Bound:
    """Where a repeat stops for this patient, resolved once per rule.

    Shared by plain recurrences, phased plans and treatment cycles so that
    "for 6 cycles", "until progression" and "for 2 years after X" mean the
    same thing whichever kind of repeat states them. ``status`` is set when
    the end itself cannot be decided yet; the repeat then gets no dates.
    """

    stop: Temporal
    #: How many occurrences THIS evaluation will generate: the protocol COUNT,
    #: never more than ``RECURRENCE_SAFETY_CAP`` (a resource limit, not clinical).
    max_count: int = 10000
    #: The protocol's own COUNT when it states one (may exceed the safety cap).
    protocol_count: int | None = None
    relative_end: Temporal | None = None
    inclusive: bool = True
    status: PatientEventStatus | None = None
    explanation: dict[str, object] = field(default_factory=dict)
    #: Set when an EVENT termination names an ending event that has no date yet:
    #: the series is projected only PROVISIONALLY up to the horizon.
    provisional: dict[str, object] | None = None

    def interrupted_by_cap(self) -> dict[str, object] | None:
        """Why the engine, not the protocol, ended the projection (or None)."""
        if self.protocol_count is not None and self.protocol_count > RECURRENCE_SAFETY_CAP:
            return {"limit": self.max_count, "protocol_count": self.protocol_count,
                    "incomplete_projection": True,
                    "note": "the protocol states more occurrences than the engine's safety "
                            "limit projects; the series is NOT complete here"}
        if self.protocol_count is None:
            return {"limit": self.max_count, "incomplete_projection": True,
                    "note": "the projection stopped at the engine's safety limit, "
                            "not at a protocol-stated end"}
        return None

    def allows(self, value: Temporal) -> bool:
        if not _before_or_equal(value, self.stop):
            return False
        if self.relative_end is None:
            return True
        if not _before_or_equal(value, self.relative_end):
            return False
        return self.inclusive or not _before_or_equal(self.relative_end, value)


def _undated(base: dict[str, object], bound: _Bound) -> EvaluatedEvent:
    explanation = {**base["explanation"], **bound.explanation}
    return EvaluatedEvent(**{**base, "explanation": explanation}, status=bound.status)


def _earlier(left: Temporal, right: Temporal) -> Temporal:
    return left if _before_or_equal(left, right) else right


def _resolve_termination(
    termination: RecurrenceTermination | None,
    context: PatientContext,
    horizon: date,
    *,
    effective_key: str,
    sample: Temporal,
) -> _Bound:
    bound = _Bound(stop=horizon)
    if termination is None:
        return bound
    if termination.type == "COUNT":
        bound.protocol_count = termination.count or 0
        bound.max_count = min(bound.protocol_count, RECURRENCE_SAFETY_CAP)
    elif termination.type == "DATE" and termination.termination_date is not None:
        bound.stop = _earlier(horizon, termination.termination_date)
    elif termination.type == "RELATIVE":
        end_anchor = _resolve_reference(termination.end_reference, context)
        if end_anchor is None:
            bound.status = PatientEventStatus.WAITING_FOR_ANCHOR
            bound.explanation = {
                "reason": "The recurrence end anchor has not been recorded.",
                "waiting_for_anchor": termination.end_reference.code,
            }
            return bound
        bound.relative_end = add_amount(end_anchor, termination.end_offset)
        bound.inclusive = bool(termination.inclusive)
    elif termination.type == "EVENT" and termination.event_code:
        values = context.event_values.get(termination.event_code, [])
        if values:
            bound.stop = _earlier(bound.stop, min(values))
        else:
            # The ending event has no date yet. The horizon bounds the projection
            # but is NOT the protocol's end: say so instead of looking complete.
            bound.provisional = {
                "type": "EVENT", "status": "AWAITING_END_EVENT",
                "event_code": termination.event_code, "provisional": True,
                "note": f"the series ends at {termination.event_code}, which has no date yet; "
                        "these occurrences are projected to the horizon and may stop earlier"}
    elif termination.type == "CONDITION" and termination.condition is not None:
        result = evaluate_condition(termination.condition, context.state)
        if result == TruthValue.UNKNOWN:
            bound.status = PatientEventStatus.WAITING_FOR_CONDITION
            return bound
        if result == TruthValue.TRUE:
            effective = context.state_effective_at.get(effective_key)
            if effective is None:
                bound.status = PatientEventStatus.UNRESOLVED
                bound.explanation = {
                    "reason": "termination condition is true but its effective time is unresolved",
                }
                return bound
            effective_value: Temporal = effective
            if isinstance(sample, date) and not isinstance(sample, datetime):
                effective_value = effective.date()
            bound.stop = _earlier(bound.stop, effective_value)
    return bound


#: Re-exported so existing importers (this module's own callers below, plus
#: tests) keep working unchanged now that the identity is built in one place
#: (AD-4f) rather than redefined here.
activity_actual_key = activity_logical_key


# --------------------------------------------------------------------------
# P1-02: an event reference counts from the date its temporal basis names.
# --------------------------------------------------------------------------


def _basis_key(reference: EventReference) -> str:
    basis = reference.temporal_basis
    if basis == TemporalBasis.ACTUAL_OR_PLANNED and reference.fallback_basis is not None:
        return f"{basis.value}/{reference.fallback_basis.value}:{reference.event_code}"
    return f"{basis.value}:{reference.event_code}"


def _history_by_code(context: PatientContext) -> dict[str, dict[int, LogicalOccurrenceState]]:
    index: dict[str, dict[int, LogicalOccurrenceState]] = {}
    for state in context.occurrence_history:
        index.setdefault(state.event_code, {})[state.occurrence_index] = state
    return index


def _select_position(values: list[Temporal], occurrence: str) -> int | None:
    if not values:
        return None
    if occurrence == "FIRST":
        return values.index(min(values))
    if occurrence == "LAST":
        return values.index(max(values))
    return len(values) - 1


def _manual_value(context: PatientContext, code: str) -> Temporal | None:
    manual = context.state.get("manual_dependency_dates")
    value = manual.get(code) if isinstance(manual, dict) else None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return date.fromisoformat(value)
    return value


def _basis_series(
    reference: EventReference, context: PatientContext,
    history: dict[str, dict[int, LogicalOccurrenceState]],
    planned: dict[str, dict[int, Temporal]],
) -> tuple[list[Temporal], list[str]]:
    """The dates a reference's basis resolves to, by occurrence index, and
    where each came from. A missing date is absent - never filled in."""
    basis, code = reference.temporal_basis, reference.event_code
    states = history.get(code, {})
    current = {index: value for index, value in planned.get(code, {}).items() if value is not None}
    # D3: the original is what the FIRST dating evaluation produced; when this
    # is that evaluation, the date it is producing now IS the original.
    original = {index: state.original_planned_date for index, state in states.items()
                if state.original_planned_date is not None}
    for index, value in current.items():
        original.setdefault(index, value)
    if states:
        actual_visit = {index: state.actual_visit_date for index, state in states.items()
                        if state.actual_visit_date is not None}
    else:  # a context built without history: the legacy per-code list
        actual_visit = dict(enumerate(sorted(context.actual_event_values.get(code) or [])))
    entries: dict[int, tuple[Temporal, str]] = {}
    if basis == TemporalBasis.CURRENT_PLANNED:
        entries = {index: (value, "CURRENT_PLANNED") for index, value in current.items()}
    elif basis == TemporalBasis.ORIGINAL_PLANNED:
        entries = {index: (value, "ORIGINAL_PLANNED") for index, value in original.items()}
    elif basis == TemporalBasis.ACTUAL_VISIT:
        entries = {index: (value, "ACTUAL_VISIT") for index, value in actual_visit.items()}
    elif basis == TemporalBasis.ACTUAL_TREATMENT:
        entries = {index: (state.actual_treatment_date, "ACTUAL_TREATMENT")
                   for index, state in states.items() if state.actual_treatment_date is not None}
    elif basis == TemporalBasis.ACTUAL_OR_PLANNED:
        entries = {index: (value, "ACTUAL_VISIT") for index, value in actual_visit.items()}
        fallback = original if reference.fallback_basis == TemporalBasis.ORIGINAL_PLANNED else current
        label = f"FALLBACK_{(reference.fallback_basis or TemporalBasis.CURRENT_PLANNED).value}"
        for index, value in fallback.items():
            entries.setdefault(index, (value, label))
    elif basis == TemporalBasis.MANUAL_ANCHOR:
        value = _manual_value(context, code)
        if value is not None:
            entries = {0: (value, "MANUAL_ANCHOR")}
    ordered = [entries[index] for index in sorted(entries)]
    return [item[0] for item in ordered], [item[1] for item in ordered]


def _prepare_basis_values(
    event: Event, context: PatientContext, run: "_Run | None",
) -> tuple[PatientContext, list[dict[str, object]]]:
    """Resolve every event reference of ``event`` to the series its basis names
    and report, per reference, which date was used and where it came from."""
    references = own_event_references(event)
    if not references:
        return context, []
    history = run.history if run is not None else _history_by_code(context)
    planned = run.planned_series if run is not None else {}
    dependencies = {item.source_event_code for item in event.dependencies}
    values = dict(context.resolved_basis_values)
    sources = dict(context.resolved_basis_sources)
    report: list[dict[str, object]] = []
    for reference in references:
        key = _basis_key(reference)
        if key not in values:
            effective = effective_temporal_basis(
                reference, owner_mode=event.dependency_mode,
                is_dependency=reference.event_code in dependencies)
            if effective is None:
                values[key], sources[key] = [], []   # basis unstated: never dated
            else:
                resolved = reference.model_copy(update={
                    "temporal_basis": effective,
                    "fallback_basis": (reference.fallback_basis
                                       if effective == TemporalBasis.ACTUAL_OR_PLANNED else None)})
                values[key], sources[key] = _basis_series(resolved, context, history, planned)
        series, origin = values[key], sources[key]
        position = _select_position(series, reference.occurrence)
        report.append({
            "event_code": reference.event_code,
            "temporal_basis": reference.temporal_basis.value,
            "fallback_basis": reference.fallback_basis.value if reference.fallback_basis else None,
            "occurrence": reference.occurrence,
            "value": series[position].isoformat() if position is not None else None,
            "source": origin[position] if position is not None else None,
            "fallback_used": bool(position is not None and origin[position].startswith("FALLBACK")),
        })
    return context.model_copy(
        update={"resolved_basis_values": values, "resolved_basis_sources": sources}), report


def _actual_recorded(
    event: Event, code: str, context: PatientContext, run: "_Run | None",
    indices: frozenset[int] | None = None,
) -> bool:
    """Is the actual that ACTUAL_PREVIOUS_EVENT waits for on ``code`` recorded?
    A rule counting from the treatment date waits for THAT date, not the visit.

    ``indices`` is the occurrence an exact dependency selector named: only that
    occurrence's own actual counts. None is the legacy event-level reading."""
    history = run.history if run is not None else _history_by_code(context)
    states = history.get(code, {})
    if indices is not None:
        states = {index: state for index, state in states.items() if index in indices}
    bases = {item.temporal_basis for item in own_event_references(event) if item.event_code == code}
    if TemporalBasis.ACTUAL_TREATMENT in bases and TemporalBasis.ACTUAL_VISIT not in bases:
        return any(state.actual_treatment_date is not None for state in states.values())
    if states and any(state.actual_visit_date is not None for state in states.values()):
        return True
    if indices is not None:
        return False        # never fall back to a code-wide list for a numbered source
    return bool(context.actual_event_values.get(code))


def _exact_indices(
    event: Event, prior: dict[str, list[EvaluatedEvent]],
) -> dict[str, frozenset[int]]:
    """code -> occurrence indexes named by an exact selector (ANY is absent)."""
    found: dict[str, frozenset[int]] = {}
    for dependency in event.dependencies:
        source = [(item.occurrence_index, item.status) for item in prior.get(
            dependency.source_event_code, [])]
        chosen = selected_indexes(dependency, source)
        if chosen is not None:
            found[dependency.source_event_code] = found.get(
                dependency.source_event_code, frozenset()) | chosen
    return found


def _resolve_reference(
    reference: TemporalReference,
    context: PatientContext,
    *,
    activity_scope: tuple[str, int] | None = None,
) -> Temporal | None:
    if isinstance(reference, AnchorReference):
        actual = context.anchors.get(reference.code)
        if actual is not None:
            return actual
        # Doc C6: a planned value is used only by a rule that said it may be.
        if reference.accepts == AnchorAcceptance.PLANNED_OR_ACTUAL:
            return context.planned_anchors.get(reference.code)
        return None
    if isinstance(reference, ActivityReference):
        if activity_scope is None:
            return None
        event_code, occurrence_index = activity_scope
        return context.activity_actuals.get(
            activity_actual_key(event_code, occurrence_index, reference.activity_code)
        )
    key = _basis_key(reference)
    if key in context.resolved_basis_values:
        values = context.resolved_basis_values[key]
    else:  # not prepared by the evaluator (direct calls, older contexts): legacy list
        values = context.event_values.get(reference.event_code, [])
    position = _select_position(values, reference.occurrence)
    return values[position] if position is not None else None


def evaluate_timing(
    expression: object,
    context: PatientContext,
    *,
    activity_scope: tuple[str, int] | None = None,
) -> ResolvedTiming | None:
    if isinstance(expression, AbsoluteTiming):
        return ResolvedTiming(nominal_start=expression.value, nominal_end=expression.value, precision="EXACT")
    if isinstance(expression, OffsetTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        value = add_amount(reference, expression.offset)
        return ResolvedTiming(nominal_start=value, nominal_end=value, precision="EXACT")
    if isinstance(expression, ProtocolDayTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        elapsed_days = expression.day - 1 if expression.day > 0 else expression.day
        value = reference + timedelta(days=elapsed_days)
        return ResolvedTiming(
            nominal_start=value, nominal_end=value, precision="EXACT",
            constraints=[{"type": "CLINICAL_DAY", "protocol_day": expression.day}],
        )
    if isinstance(expression, RangeTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        return ResolvedTiming(
            nominal_start=add_amount(reference, expression.start),
            nominal_end=add_amount(reference, expression.end),
            earliest=add_amount(reference, expression.start),
            latest=add_amount(reference, expression.end),
            precision="CONSTRAINT",
        )
    if isinstance(expression, NominalWindowTiming):
        nominal = evaluate_timing(expression.nominal, context, activity_scope=activity_scope)
        if nominal is None or nominal.nominal_start is None:
            return None
        before = TemporalAmount(value=-expression.window.before.value, unit=expression.window.before.unit)
        after = TemporalAmount(value=expression.window.after.value, unit=expression.window.after.unit)
        return ResolvedTiming(
            nominal_start=nominal.nominal_start,
            nominal_end=nominal.nominal_end,
            earliest=add_amount(nominal.nominal_start, before),
            latest=add_amount(nominal.nominal_start, after),
            precision="EXACT",
        )
    if isinstance(expression, WithinTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        end = add_amount(reference, expression.duration, 1 if expression.direction == "AFTER" else -1)
        earliest, latest = sorted((reference, end))
        return ResolvedTiming(
            earliest=earliest, latest=latest, precision="CONSTRAINT",
            constraints=[{"type": "WITHIN", "direction": expression.direction}],
        )
    if isinstance(expression, NoLaterThanTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        return ResolvedTiming(
            latest=add_amount(reference, expression.duration), precision="CONSTRAINT",
            constraints=[{"type": "NO_LATER_THAN"}],
        )
    if isinstance(expression, TriggeredTiming):
        trigger_value = _resolve_reference(expression.trigger, context, activity_scope=activity_scope)
        if trigger_value is None:
            return None
        nested = expression.timing_after_trigger
        if isinstance(nested, TriggerOffset):
            result = add_amount(trigger_value, nested.offset)
            return ResolvedTiming(nominal_start=result, nominal_end=result, precision="EXACT")
        if isinstance(nested, TriggerWithin):
            end = add_amount(trigger_value, nested.duration, 1 if nested.direction == "AFTER" else -1)
            earliest, latest = sorted((trigger_value, end))
            return ResolvedTiming(earliest=earliest, latest=latest, precision="CONSTRAINT")
        if isinstance(nested, TriggerNoLaterThan):
            return ResolvedTiming(
                latest=add_amount(trigger_value, nested.duration), precision="CONSTRAINT",
                constraints=[{"type": "NO_LATER_THAN"}],
            )
    if isinstance(expression, CycleDayTiming):
        start = context.anchors.get(expression.cycle_reference)
        cycle_length = context.state.get("cycle_lengths", {}).get(expression.cycle_reference) if isinstance(context.state.get("cycle_lengths"), dict) else None
        if start is None or not isinstance(cycle_length, int) or cycle_length <= 0:
            return None
        cycle_number = expression.cycle.number
        if expression.cycle.type == "CURRENT":
            current = context.state.get("current_cycles", {})
            cycle_number = current.get(expression.cycle_reference) if isinstance(current, dict) else None
        if not isinstance(cycle_number, int) or cycle_number <= 0:
            return None
        value = start + timedelta(days=(cycle_number - 1) * cycle_length + expression.day - 1)
        return ResolvedTiming(nominal_start=value, nominal_end=value, precision="EXACT")
    if isinstance(expression, ConstraintTiming):
        # The timing IS its constraint set: its own window is unbounded and the
        # permitted set is computed by apply_constraints (never a nominal date).
        return ResolvedTiming(precision="CONSTRAINT", constraints=[{"type": "CONSTRAINED"}])
    if isinstance(expression, ApproximateTiming):
        raise UnsupportedTimingError("approximate timing requires an approved precision policy")
    if isinstance(expression, (ProtocolDefinedTiming, UnresolvedTiming)):
        raise UnsupportedTimingError("timing is not deterministically executable")
    raise UnsupportedTimingError(f"unsupported timing expression {type(expression).__name__}")


#: AD-7's own motivating example ("subjects aged >= 65") needs a comparable
#: numeric dimension that isn't tied to a StudyDimension code list. Sourced
#: from ``context.state["age"]`` rather than a computed age-from-DOB: no
#: patient model anywhere in this codebase carries a date of birth today, and
#: adding one is a real product decision (collecting and storing patient DOB),
#: not a taxonomy change - a caller that already knows the patient's age can
#: put it in ``state`` exactly like any other computed PATIENT_ATTRIBUTE.
_COMPARISON_OPERATORS = {
    "EQ": lambda left, right: left == right,
    "NE": lambda left, right: left != right,
    "LT": lambda left, right: left < right,
    "LTE": lambda left, right: left <= right,
    "GT": lambda left, right: left > right,
    "GTE": lambda left, right: left >= right,
}


def _coerce_threshold(raw: str, reference: object) -> object:
    """Parse a rule's string threshold to compare against an already-typed
    dimension value (AD-7: numeric/date-aware compare, not string compare)."""
    if isinstance(reference, bool):
        return raw
    if isinstance(reference, int):
        try:
            return int(raw)
        except ValueError:
            return raw
    if isinstance(reference, float):
        try:
            return float(raw)
        except ValueError:
            return raw
    if isinstance(reference, datetime):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return raw
    if isinstance(reference, date):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return raw
    return raw


def _kleene_and(left: TruthValue, right: TruthValue) -> TruthValue:
    if TruthValue.FALSE in (left, right):
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in (left, right) else TruthValue.TRUE


def _kleene_or(left: TruthValue, right: TruthValue) -> TruthValue:
    if TruthValue.TRUE in (left, right):
        return TruthValue.TRUE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in (left, right) else TruthValue.FALSE


def _evaluate_applicability(rules: list[ApplicabilityRule], context: PatientContext) -> TruthValue:
    if not rules:
        return TruthValue.TRUE
    results = _applicability_rule_results(rules, context)
    # Left-to-right fold: each rule's own ``combinator`` says how it joins
    # with the NEXT rule's result. Every existing rule list defaults every
    # entry to AND, which folds to exactly the old pure-AND behaviour.
    accumulated = results[0]
    for index in range(1, len(results)):
        combine = _kleene_and if rules[index - 1].combinator == "AND" else _kleene_or
        accumulated = combine(accumulated, results[index])
    return accumulated


#: Applicability dimensions that are an ASSIGNMENT the site makes (randomise,
#: allocate), as opposed to a clinical fact about the patient. Generic study
#: dimensions (a crossover SEQUENCE, a PART) are added per schedule.
ASSIGNMENT_DIMENSIONS = frozenset({"ARM", "COHORT", "POPULATION"})


def assignment_dimensions(schedule: UniversalSchedule) -> frozenset[str]:
    return ASSIGNMENT_DIMENSIONS | {item.dimension_type for item in schedule.dimensions}


def _awaited_assignments(
    rules: list[ApplicabilityRule],
    context: PatientContext,
    assignments: frozenset[str],
) -> list[str]:
    """The assignments an UNKNOWN applicability is waiting for - or nothing.

    Non-empty only when EVERY rule that could not be decided is an assignment
    dimension. If any undecided rule is a clinical attribute (age, WOCBP, a
    typed condition), the visit is still waiting on a clinical fact, so it
    stays WAITING_FOR_CONDITION rather than claiming randomisation alone will
    settle it.
    """
    unknown = [
        rule for rule, result in zip(rules, _applicability_rule_results(rules, context))
        if result == TruthValue.UNKNOWN
    ]
    if not unknown or any(
            rule.condition is not None or rule.dimension not in assignments
            for rule in unknown):
        return []
    return sorted({rule.dimension for rule in unknown})


def _applicability_rule_results(
    rules: list[ApplicabilityRule], context: PatientContext,
) -> list[TruthValue]:
    dimensions = {
        "ARM": context.arm_code,
        "COHORT": context.cohort_code,
        "POPULATION": context.population_code,
        "AGE": context.state.get("age"),
        **context.dimension_values,
    }
    results: list[TruthValue] = []
    for rule in rules:
        if rule.condition is not None:
            results.append(evaluate_condition(rule.condition, context.state))
            continue
        value = dimensions.get(rule.dimension)
        if rule.dimension in {"PATIENT_ATTRIBUTE", "CUSTOM"}:
            value = context.state.get(rule.field) if rule.field else None
        elif rule.dimension == "EPOCH":
            value = context.state.get("epoch")
        if value is None:
            results.append(TruthValue.UNKNOWN)
            continue
        if rule.operator in _COMPARISON_OPERATORS:
            threshold = _coerce_threshold(rule.values[0], value)
            try:
                included = _COMPARISON_OPERATORS[rule.operator](value, threshold)
            except TypeError:
                results.append(TruthValue.UNKNOWN)
                continue
            results.append(TruthValue.TRUE if included else TruthValue.FALSE)
            continue
        values = value if isinstance(value, list) else [value]
        included = any(str(item) in rule.values for item in values)
        if rule.operator == "NOT_IN":
            included = not included
        results.append(TruthValue.TRUE if included else TruthValue.FALSE)
    return results


def _missing_activity_anchor(timing: object) -> str | None:
    """Name the intra-day activity a timing expression waits on, if any."""
    for reference in (
        getattr(timing, "reference", None),
        getattr(timing, "trigger", None),
        getattr(getattr(timing, "nominal", None), "reference", None),
    ):
        if isinstance(reference, ActivityReference):
            return reference.activity_code
    return None


def _event_anchor_references(event: Event) -> list[AnchorReference]:
    """Every anchor an event's own date depends on, including its repeat rule."""
    references = [
        item for item in event_temporal_references(event)
        if isinstance(item, AnchorReference)
    ]
    if event.recurrence is not None:
        if isinstance(event.recurrence.start_reference, AnchorReference):
            references.append(event.recurrence.start_reference)
        if event.recurrence.termination.end_reference is not None:
            references.append(event.recurrence.termination.end_reference)
    if event.recurrence_plan is not None:
        if isinstance(event.recurrence_plan.start_reference, AnchorReference):
            references.append(event.recurrence_plan.start_reference)
        termination = event.recurrence_plan.termination
        if termination is not None and termination.end_reference is not None:
            references.append(termination.end_reference)
    if event.calendar_recurrence is not None:
        if isinstance(event.calendar_recurrence.start_reference, AnchorReference):
            references.append(event.calendar_recurrence.start_reference)
        if event.calendar_recurrence.termination.end_reference is not None:
            references.append(event.calendar_recurrence.termination.end_reference)
    return references


def _record_anchor_basis(
    event: Event, context: PatientContext, explanation: dict[str, object],
) -> list[str]:
    """Say which anchors dated this visit from a plan, and which wait on reality.

    Returns the anchors that have only a planned value while this event's rule
    accepts an actual one - the reason the visit is still undated (doc D2).
    """
    basis: dict[str, str] = {}
    waiting_for_actual: set[str] = set()
    for reference in _event_anchor_references(event):
        if reference.code in context.anchors:
            basis[reference.code] = "ACTUAL"
        elif reference.code in context.planned_anchors:
            if reference.accepts == AnchorAcceptance.PLANNED_OR_ACTUAL:
                basis[reference.code] = "PLANNED"
            else:
                waiting_for_actual.add(reference.code)
    if basis:
        explanation["anchor_basis"] = basis
    return sorted(waiting_for_actual)


def _waiting_for_actual_reason(codes: list[str]) -> str:
    return (
        f"waiting for the actual {', '.join(codes)} date; only a planned date is "
        "recorded and this visit counts from the date it actually happens"
    )


def schedule_cycle_plans(schedule: UniversalSchedule) -> dict[str, CyclePlan]:
    return {
        block.cycle_plan.code: block.cycle_plan
        for block in schedule.repeat_blocks if block.cycle_plan is not None
    }


def _cycle_plan_for(event: Event, plans: dict[str, CyclePlan]) -> CyclePlan | None:
    if isinstance(event.timing, CycleDayTiming):
        return plans.get(event.timing.cycle_reference)
    return None


def _every_cycle_plan(event: Event, plans: dict[str, CyclePlan]) -> CyclePlan | None:
    plan = _cycle_plan_for(event, plans)
    if plan is not None and event.timing.cycle.type == "CURRENT":
        return plan
    return None


@dataclass
class CycleStart:
    """One treatment cycle's start for this patient.

    ``start`` is None when the cycle cannot be dated yet; ``status`` then says
    why (BLOCKED waiting for the previous cycle's actual start, WAITING_FOR_ANCHOR
    for a manual entry, UNRESOLVED for an unstated mode).
    """

    number: int
    start: Temporal | None
    basis: str
    status: PatientEventStatus = PatientEventStatus.RESOLVED
    waiting_for: list[str] = field(default_factory=list)
    reason: str | None = None


def _actual_cycle_start(
    plan: CyclePlan, context: PatientContext, number: int, recorded_starts: list[Temporal],
) -> Temporal | None:
    recorded = context.anchors.get(cycle_start_anchor_code(plan.code, number))
    if recorded is not None:
        return recorded
    if number - 1 < len(recorded_starts):
        return recorded_starts[number - 1]
    if number == 1:
        # Cycle 1 starts at the plan's own anchor; it is actual when that anchor is.
        return context.anchors.get(plan.start_reference.code)
    return None


def cycle_starts(
    plan: CyclePlan, context: PatientContext, horizon: date,
) -> tuple[list[CycleStart], _Bound | None]:
    """Every cycle start this patient has, up to the plan's end or the horizon.

    Returns the starts and the plan's resolved end. The end is None when Cycle
    1's own anchor is not known yet, and carries a status when the plan's
    termination cannot be settled yet (no starts are listed then). The list stops at the
    first cycle that cannot be dated: in ACTUAL_PREVIOUS_CYCLE_START mode that
    cycle is returned BLOCKED on the previous cycle's actual start and no later
    cycle is listed, because none of them has a date to show.
    """
    origin = _resolve_reference(plan.start_reference, context)
    if origin is None:
        return [], None
    bound = _resolve_termination(
        plan.termination, context, horizon,
        effective_key=f"cycles:{plan.code}", sample=origin)
    if bound.status is not None:
        return [], bound
    recorded_starts = sorted(
        context.actual_event_values.get(plan.start_event_code or "", []) or [],
        key=_sort_value,
    )
    output: list[CycleStart] = []
    number = 1
    while number <= min(bound.max_count, 1000):
        own_actual = _actual_cycle_start(plan, context, number, recorded_starts)
        anchor_code = cycle_start_anchor_code(plan.code, number)
        if number == 1:
            # On the nominal grid Cycle 1 stays at its anchor; when cycles follow
            # reality, Cycle 1's days follow the day it was actually given.
            follows_actual = plan.dependency_mode != CycleDependencyMode.NOMINAL
            start = own_actual if follows_actual and own_actual is not None else origin
            item = CycleStart(1, start, "ACTUAL" if own_actual is not None else "PLANNED")
        elif plan.dependency_mode == CycleDependencyMode.NOMINAL:
            item = CycleStart(number, nth_occurrence(origin, plan.length, number - 1), "NOMINAL")
        elif plan.dependency_mode == CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START:
            previous = _actual_cycle_start(plan, context, number - 1, recorded_starts)
            if previous is None:
                waiting = cycle_start_anchor_code(plan.code, number - 1)
                output.append(CycleStart(
                    number, None, "WAITING_FOR_ACTUAL_PREVIOUS_CYCLE",
                    status=PatientEventStatus.BLOCKED, waiting_for=[waiting],
                    reason=(
                        f"cycle {number} starts {_cadence_text(plan.length)[len('every '):]} "
                        f"after cycle {number - 1} actually starts; waiting for the "
                        f"actual {waiting} date"
                    ),
                ))
                break
            if own_actual is not None:
                item = CycleStart(number, own_actual, "ACTUAL")
            else:
                item = CycleStart(number, add_amount(previous, plan.length), "FROM_ACTUAL_PREVIOUS_CYCLE")
        elif plan.dependency_mode == CycleDependencyMode.MANUAL:
            manual = own_actual or context.planned_anchors.get(anchor_code)
            if manual is None:
                output.append(CycleStart(
                    number, None, "WAITING_FOR_MANUAL_DATE",
                    status=PatientEventStatus.WAITING_FOR_ANCHOR, waiting_for=[anchor_code],
                    reason=f"cycle {number} starts on a date the site enters; {anchor_code} is not recorded",
                ))
                break
            item = CycleStart(number, manual, "ACTUAL" if own_actual is not None else "MANUAL")
        else:
            output.append(CycleStart(
                number, None, "UNCLEAR", status=PatientEventStatus.UNRESOLVED,
                reason=(
                    "the protocol does not state whether later cycles follow the "
                    "nominal grid or the actual previous cycle; confirm before scheduling"
                ),
            ))
            break
        if not bound.allows(item.start) or not _before_or_equal(item.start, horizon):
            break
        output.append(item)
        number += 1
    return output, bound


def publish_cycle_anchors(
    plans: dict[str, CyclePlan],
    recorded: PatientContext,
    working: PatientContext,
    horizon: date,
) -> None:
    """Expose every cycle start as ``<code>_<n>_START`` (doc F2).

    Actual starts go to ``anchors``; calculated ones only to ``planned_anchors``,
    so a rule counting from CYCLE_3_START with the default ACTUAL acceptance
    waits for Cycle 3 to really start. Values a person recorded are never
    overwritten.
    """
    for plan in plans.values():
        starts, _ = cycle_starts(plan, working, horizon)
        recorded_days = sorted(
            recorded.actual_event_values.get(plan.start_event_code or "", []) or [],
            key=_sort_value,
        )
        for item in starts:
            if item.start is None:
                continue
            code = cycle_start_anchor_code(plan.code, item.number)
            actual = _actual_cycle_start(plan, working, item.number, recorded_days)
            if actual is not None:
                if code not in recorded.anchors:
                    working.anchors[code] = actual
            elif code not in recorded.planned_anchors:
                working.planned_anchors[code] = item.start


def evaluate_activities(
    event: Event,
    context: PatientContext,
    *,
    occurrence_index: int,
) -> list[EvaluatedActivity]:
    """Resolve one visit occurrence's activities.

    Activity times resolve progressively: an activity anchored on another activity
    (dose, infusion end, previous sample) stays WAITING_FOR_ANCHOR with a named
    reason until that activity's ACTUAL time is recorded. No time is ever invented,
    and a recorded actual time is never overwritten by a recalculated planned time.
    """
    scope = (event.code, occurrence_index)
    # Doc s8: "MRI every second cycle" is an activity CONDITION referencing which
    # occurrence this is, not a different recurrence rule and not a second copy
    # of the event. occurrence_number is 1-based (cycle 1, cycle 2, ...) to match
    # how a protocol numbers cycles; it is visible only to the condition
    # evaluated for THIS occurrence's activities and is never written back onto
    # the shared patient context, so it cannot leak into another occurrence's
    # evaluation or into applicability/timing, which do not use it.
    condition_state = {**context.state, "occurrence_number": occurrence_index + 1}
    results: list[EvaluatedActivity] = []
    for activity in event.activities:
        key = (
            activity_actual_key(event.code, occurrence_index, activity.code)
            if activity.code else None
        )
        actual = context.activity_actuals.get(key) if key else None
        recorded_status = context.activity_statuses.get(key) if key else None
        base: dict[str, object] = {
            "activity_definition_id": activity.id,
            "activity_code": activity.code,
            "display_name": activity.display_name,
            "requiredness": activity.requiredness,
            "sequence_number": activity.sequence_number,
        }
        applicability = _evaluate_applicability(activity.applicability, context)
        condition = (
            TruthValue.TRUE if not activity.conditions
            else _combine([evaluate_condition(item, condition_state) for item in activity.conditions])
        )
        base["applicability_result"] = applicability.value
        base["condition_result"] = condition.value
        if activity.requiredness == Requiredness.NOT_APPLICABLE or applicability == TruthValue.FALSE:
            results.append(EvaluatedActivity(status=PatientActivityStatus.NOT_APPLICABLE, **base))
            continue
        if recorded_status == "NOT_DONE":
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.NOT_DONE, actual_time=actual, **base))
            continue
        explanation: dict[str, object] = {
            "evidence_refs": [str(item) for item in activity.evidence_refs],
        }
        if activity.timing is not None:
            explanation["timing"] = activity.timing.model_dump(mode="json")
        timing: ResolvedTiming | None = None
        if activity.timing is not None:
            try:
                timing = evaluate_timing(activity.timing, context, activity_scope=scope)
            except UnsupportedTimingError as error:
                explanation["reason"] = str(error)
                results.append(EvaluatedActivity(
                    status=PatientActivityStatus.UNRESOLVED, actual_time=actual,
                    explanation=explanation, **base,
                ))
                continue
        if actual is not None:
            explanation["result"] = timing.model_dump(mode="json") if timing else None
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.COMPLETED, timing=timing,
                actual_time=actual, explanation=explanation, **base,
            ))
            continue
        if condition == TruthValue.FALSE:
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.NOT_APPLICABLE, explanation=explanation, **base))
            continue
        if applicability == TruthValue.UNKNOWN or condition == TruthValue.UNKNOWN:
            explanation["reason"] = "applicability or condition is not yet known"
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.WAITING_FOR_CONDITION,
                explanation=explanation, **base,
            ))
            continue
        if activity.timing is not None and timing is None:
            waiting_on = _missing_activity_anchor(activity.timing)
            explanation["reason"] = (
                f"waiting for the actual time of {waiting_on}" if waiting_on
                else "required anchor or event reference is not available"
            )
            if waiting_on:
                explanation["waiting_for_activity"] = waiting_on
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.WAITING_FOR_ANCHOR,
                explanation=explanation, **base,
            ))
            continue
        if timing is not None:
            explanation["result"] = timing.model_dump(mode="json")
        results.append(EvaluatedActivity(
            status=PatientActivityStatus.RESOLVED, timing=timing,
            explanation=explanation, **base,
        ))
    by_id = {activity.id: activity for activity in event.activities}
    results = [
        apply_activity_constraints(by_id[item.activity_definition_id], item, context, scope)
        if item.activity_definition_id in by_id else item
        for item in results
    ]
    return sorted(
        results,
        key=lambda item: (item.sequence_number is None, item.sequence_number or 0),
    )


def _combine(values: list[TruthValue]) -> TruthValue:
    if TruthValue.FALSE in values:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in values else TruthValue.TRUE


def _evaluate_conditions(event: Event, context: PatientContext) -> TruthValue:
    if not event.conditions:
        return TruthValue.TRUE
    results = [evaluate_condition(condition, context.state) for condition in event.conditions]
    if TruthValue.FALSE in results:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in results else TruthValue.TRUE



# ---------------------------------------------------------------------------
# Composable constraints (RL-04/05/13, NF-03). Runs ONLY for rules carrying a
# constraint set (Event/Activity.constraints or a CONSTRAINED timing), so every
# legacy schedule evaluates exactly as before (NF-08).
# ---------------------------------------------------------------------------


def constraint_roots(owner: str, timing: object, constraints: object) -> list[tuple[str, object]]:
    roots: list[tuple[str, object]] = []
    if isinstance(timing, ConstraintTiming):
        roots.append((f"{owner}/timing/constraint", timing.constraint))
    if constraints is not None:
        roots.append((f"{owner}/constraints", constraints))
    return roots


class ContextValueSource:
    """Resolves constraint references against one patient occurrence.

    ACTUAL references read only recorded facts; PLANNED_OR_ACTUAL may fall back
    to a planned anchor or a CALCULATED target of an earlier-evaluated event.
    A planned value never satisfies an ACTUAL-only reference (PT-01).
    """

    def __init__(
        self,
        context: PatientContext,
        *,
        event_code: str,
        occurrence_index: int,
        prior: dict[str, list[EvaluatedEvent]] | None = None,
        own_planned: dict[int, Temporal | None] | None = None,
        run: "_Run | None" = None,
    ) -> None:
        self.context = context
        self.event_code = event_code
        self.occurrence_index = occurrence_index
        self.prior = prior
        self.own_planned = own_planned if own_planned is not None else {}
        self.run = run

    def process_boundary(self, value: ProcessBoundaryValue) -> Resolved:
        """START/END of a process from its CONFIRMED transitions only."""
        def missing(reason: str) -> Resolved:
            return Resolved(None, missing=[MissingInput(
                kind="PROCESS", code=value.process_code, occurrence=value.boundary,
                accepts="ACTUAL", reason=reason)])

        if self.run is None:
            return missing("PROCESS_STATE_UNAVAILABLE")
        timeline = self.run.processes.timelines.get(value.process_code)
        if timeline is None:
            return missing("PROCESS_NOT_DEFINED")
        change = timeline.started() if value.boundary == "START" else timeline.terminal()
        if change is None:
            return missing("PROCESS_NOT_STARTED" if value.boundary == "START" else "PROCESS_NOT_ENDED")
        return Resolved(change.effective_at, inputs=[{
            "kind": "PROCESS", "code": value.process_code, "boundary": value.boundary,
            "value": change.effective_at.isoformat(), "basis": "ACTUAL",
            "transition": change.id, "state": change.to_state.value}])

    def _planned_ends(self, code: str) -> dict[int, Temporal | None]:
        if self.prior is None:
            return {}
        return {
            item.occurrence_index: (
                item.timing.nominal_end or item.timing.nominal_start
                if item.status == PatientEventStatus.RESOLVED and item.timing is not None
                else None)
            for item in self.prior.get(code, [])
        }

    def _counted(self, code: str) -> set[int] | None:
        """Occurrence indexes that still count toward a series (a cancelled or
        inapplicable occurrence can never become the last one)."""
        if self.prior is None or code not in self.prior:
            return None
        return {
            item.occurrence_index for item in self.prior[code]
            if item.status not in {PatientEventStatus.CANCELLED, PatientEventStatus.NOT_APPLICABLE}
        }

    def anchor(self, value: AnchorValue) -> Resolved:
        actual = self.context.anchors.get(value.code)
        if actual is not None:
            return Resolved(actual, inputs=[{
                "kind": "ANCHOR", "code": value.code, "value": actual.isoformat(), "basis": "ACTUAL"}])
        planned = self.context.planned_anchors.get(value.code)
        if planned is not None and value.accepts == AnchorAcceptance.PLANNED_OR_ACTUAL:
            return Resolved(planned, inputs=[{
                "kind": "ANCHOR", "code": value.code, "value": planned.isoformat(), "basis": "PLANNED"}])
        return Resolved(None, missing=[MissingInput(
            kind="ANCHOR", code=value.code, accepts=value.accepts.value,
            reason="PLANNED_ONLY" if planned is not None else "NOT_RECORDED")])

    def _actual_by_index(self, code: str) -> dict[int, Temporal]:
        """Recorded actual dates keyed by the occurrence they belong to.

        Positional (the n-th recorded date is occurrence n) only for a context
        built without occurrence history; with history an occurrence that was
        missed or skipped can no longer shift the later ones (D5).
        """
        history = [item for item in self.context.occurrence_history if item.event_code == code]
        if history:
            return {item.occurrence_index: item.actual_visit_date for item in history
                    if item.actual_visit_date is not None}
        return dict(enumerate(sorted(self.context.actual_event_values.get(code) or [])))

    def _planned_series(self, code: str) -> dict[int, Temporal | None]:
        if code == self.event_code:
            return dict(self.own_planned)
        if self.prior is not None:
            return {
                item.occurrence_index: (
                    item.timing.nominal_start
                    if item.status == PatientEventStatus.RESOLVED and item.timing is not None
                    else None)
                for item in self.prior.get(code, [])
            }
        return dict(enumerate(self.context.event_values.get(code, [])))

    def event(self, value: EventValue) -> Resolved:
        code, selector = value.event_code, value.occurrence
        actual = self._actual_by_index(code)
        planned = self._planned_series(code)
        occurrence = selector.type if selector.type != "NUMBER" else f"NUMBER:{selector.number}"

        def missing(reason: str) -> Resolved:
            return Resolved(None, missing=[MissingInput(
                kind="EVENT", code=code, occurrence=occurrence,
                accepts=value.accepts.value, reason=reason)])

        def found(result: Temporal, basis: str, index: int) -> Resolved:
            return Resolved(result, inputs=[{
                "kind": "EVENT", "code": code, "occurrence": occurrence,
                "occurrence_index": index, "value": result.isoformat(), "basis": basis}])

        if value.boundary == "END":
            return self._end_of(value, occurrence, missing, found)
        if selector.type == "LAST":
            if self.run is not None and code in self.run.open_series:
                # The series has no final occurrence yet: the latest one so far
                # is not "the last" (design, temporal semantics).
                return missing("LAST_NOT_FINAL")
            counted = self._counted(code)
            indexes = [index for index in planned if counted is None or index in counted]
            count = max([(max(actual) + 1) if actual else 0, *(index + 1 for index in indexes)])
            if actual and all(position in actual for position in range(count)):
                return found(actual[count - 1], "ACTUAL", count - 1)
            if value.accepts == AnchorAcceptance.PLANNED_OR_ACTUAL and count:
                last = planned.get(count - 1)
                if last is not None:
                    return found(last, "PLANNED", count - 1)
            return missing("LAST_NOT_FINAL" if actual else "NOT_RECORDED")
        index = {
            "FIRST": 0,
            "NUMBER": (selector.number or 1) - 1,
            "CURRENT": self.occurrence_index,
            "PREVIOUS": self.occurrence_index - 1,
        }[selector.type]
        if index < 0:
            # "After the previous dose" restricts nothing for the first dose.
            return Resolved(None, vacuous=True, inputs=[{
                "kind": "EVENT", "code": code, "occurrence": occurrence,
                "note": "no previous occurrence; the restriction does not apply"}])
        if index in actual:
            return found(actual[index], "ACTUAL", index)
        planned_value = planned.get(index)
        if value.accepts == AnchorAcceptance.PLANNED_OR_ACTUAL:
            if planned_value is not None:
                return found(planned_value, "PLANNED", index)
            return missing("NO_PLANNED_TARGET" if index in planned else "OCCURRENCE_NOT_AVAILABLE")
        return missing("PLANNED_ONLY" if planned_value is not None else "NOT_RECORDED")

    def _end_of(self, value: EventValue, occurrence: str, missing, found) -> Resolved:
        """END boundary: planned from the nominal end, actual only from a
        recorded end (a performed start never proves the end)."""
        code, selector = value.event_code, value.occurrence
        if selector.type == "LAST" and self.run is not None and code in self.run.open_series:
            return missing("LAST_NOT_FINAL")
        ends = self._planned_ends(code)
        if selector.type == "LAST":
            counted = self._counted(code)
            indexes = sorted(index for index in ends if counted is None or index in counted)
            if not indexes:
                return missing("NOT_RECORDED")
            index = indexes[-1]
        else:
            index = {
                "FIRST": 0, "NUMBER": (selector.number or 1) - 1,
                "CURRENT": self.occurrence_index, "PREVIOUS": self.occurrence_index - 1,
            }[selector.type]
            if index < 0:
                return Resolved(None, vacuous=True, inputs=[{
                    "kind": "EVENT", "code": code, "occurrence": occurrence, "boundary": "END",
                    "note": "no previous occurrence; the restriction does not apply"}])
        fact = self.run.facts.outcome.get((code, index)) if self.run is not None else None
        if fact is not None and fact.kind == OccurrenceFactKind.PERFORMED and fact.end_value is not None:
            return found(fact.end_value, "ACTUAL", index)
        if value.accepts == AnchorAcceptance.PLANNED_OR_ACTUAL:
            planned_end = ends.get(index)
            if planned_end is not None:
                return found(planned_end, "PLANNED", index)
            return missing("NO_PLANNED_TARGET" if index in ends else "OCCURRENCE_NOT_AVAILABLE")
        return missing("END_NOT_RECORDED")

    def activity(self, value: ActivityValue) -> Resolved:
        key = activity_actual_key(self.event_code, self.occurrence_index, value.activity_code)
        actual = self.context.activity_actuals.get(key)
        if actual is None:
            return Resolved(None, missing=[MissingInput(
                kind="ACTIVITY", code=value.activity_code, accepts="ACTUAL",
                reason="NOT_RECORDED")])
        return Resolved(actual, inputs=[{
            "kind": "ACTIVITY", "code": value.activity_code,
            "value": actual.isoformat(), "basis": "ACTUAL"}])


def _base_window(timing: ResolvedTiming | None, source_id: str) -> BaseWindow:
    if timing is None:
        return BaseWindow(source_id=source_id, known=False)
    if timing.earliest is not None or timing.latest is not None:
        return BaseWindow(source_id=source_id, lower=timing.earliest, upper=timing.latest)
    if timing.nominal_start is not None:
        return BaseWindow(
            source_id=source_id, lower=timing.nominal_start,
            upper=timing.nominal_end or timing.nominal_start)
    return BaseWindow(source_id=source_id)


def _timing_missing_inputs(
    timing: object, context: PatientContext, owner: str,
    activity_scope: tuple[str, int] | None = None,
) -> list[MissingInput]:
    """Name the references a legacy timing is still waiting on."""
    output: list[MissingInput] = []
    if not isinstance(timing, ConstraintTiming):
        for reference in timing_references(timing):
            if _resolve_reference(reference, context, activity_scope=activity_scope) is not None:
                continue
            if isinstance(reference, AnchorReference):
                output.append(MissingInput(
                    kind="ANCHOR", code=reference.code, accepts=reference.accepts.value,
                    reason=("PLANNED_ONLY" if reference.code in context.planned_anchors
                            else "NOT_RECORDED")))
            elif isinstance(reference, EventReference):
                output.append(MissingInput(
                    kind="EVENT", code=reference.event_code, occurrence=reference.occurrence,
                    reason="NOT_RECORDED"))
            elif isinstance(reference, ActivityReference):
                output.append(MissingInput(
                    kind="ACTIVITY", code=reference.activity_code, accepts="ACTUAL",
                    reason="NOT_RECORDED"))
    return output or [MissingInput(kind="TIMING", code=owner, reason="TIMING_REFERENCE_MISSING")]


def _feasibility_fields(result: FeasibilityResult) -> dict[str, object]:
    return {
        "feasibility": result.status,
        "feasible_intervals": result.intervals,
        "missing_inputs": result.missing_inputs,
        "conflicting_constraints": result.conflicting_constraints,
        "constraint_trace": result.trace,
        "constraint_relations": result.relations,
    }


def _describe_missing(items: list[MissingInput]) -> str:
    return ", ".join(sorted({
        f"{item.code or item.kind} ({item.reason.lower().replace('_', ' ')})" for item in items}))


def _timing_from_feasible(
    timing: ResolvedTiming, result: FeasibilityResult,
) -> tuple[ResolvedTiming, dict[str, object]]:
    """Carry a FEASIBLE set onto the flat timing fields without widening it.

    The flat earliest/latest pair can hold ONE interval. When the nominal
    target lies inside the set, the interval containing it is used; otherwise
    the first interval is, and the nominal target is dropped (a constraint that
    excludes the protocol target leaves no target - none is invented). A
    CONSTRAINT-precision timing (range/window-only) never had a target, so its
    nominal fields are cleared: a range start is not a planned date. Every
    interval stays in ``feasible_intervals`` and in ``timing.constraints``.
    """
    notes: dict[str, object] = {}
    intervals = result.intervals
    chosen = intervals[0]
    nominal = timing.nominal_start if timing.precision != "CONSTRAINT" else None
    if nominal is not None:
        containing = [item for item in intervals if interval_contains(item, nominal)]
        if containing:
            chosen = containing[0]
        else:
            notes["nominal_target_excluded"] = nominal.isoformat()
            nominal = None
    if len(intervals) > 1:
        notes["disjoint_intervals"] = len(intervals)
    constraints = [*timing.constraints, {
        "type": "FEASIBLE_SET",
        "intervals": [item.model_dump(mode="json") for item in intervals],
    }]
    updated = timing.model_copy(update={
        "nominal_start": nominal,
        "nominal_end": timing.nominal_end if nominal is not None else None,
        "earliest": chosen.lower.value,
        "latest": chosen.upper.value,
        "constraints": constraints,
    })
    return updated, notes


def apply_event_constraints(
    event: Event,
    results: list[EvaluatedEvent],
    context: PatientContext,
    prior: dict[str, list[EvaluatedEvent]],
    *,
    run: "_Run | None" = None,
) -> list[EvaluatedEvent]:
    roots = constraint_roots(event.code, event.timing, event.constraints)
    if not roots:
        return results
    repeats = event.repeats
    own_planned: dict[int, Temporal | None] = {}
    output: list[EvaluatedEvent] = []
    for item in sorted(results, key=lambda value: value.occurrence_index):
        if item.status == PatientEventStatus.RESOLVED:
            base = _base_window(item.timing, f"{event.code}/timing")
        elif item.status == PatientEventStatus.WAITING_FOR_ANCHOR and not repeats:
            base = BaseWindow(
                source_id=f"{event.code}/timing", known=False,
                missing=_timing_missing_inputs(event.timing, context, event.code))
        else:
            output.append(item)
            continue
        source = ContextValueSource(
            context, event_code=event.code, occurrence_index=item.occurrence_index,
            prior=prior, own_planned=own_planned, run=run)
        explanation = dict(item.explanation)
        try:
            result = evaluate_feasibility(roots, source, base=base)
        except UnsupportedConstraintError as error:
            explanation["reason"] = str(error)
            own_planned[item.occurrence_index] = None
            output.append(item.model_copy(update={
                "status": PatientEventStatus.UNRESOLVED, "timing": None,
                "explanation": explanation}))
            continue
        explanation["feasibility"] = result.summary()
        update: dict[str, object] = _feasibility_fields(result)
        if result.status == FeasibilityStatus.FEASIBLE and item.timing is not None:
            timing, notes = _timing_from_feasible(item.timing, result)
            explanation.update(notes)
            explanation["result"] = timing.model_dump(mode="json")
            update["timing"] = timing
        elif result.status == FeasibilityStatus.INCOMPLETE:
            explanation["reason"] = (
                "waiting for " + _describe_missing(result.missing_inputs)
                + "; the known bounds are kept but the visit is not dated until every "
                "restriction can be evaluated")
            update.update(status=PatientEventStatus.WAITING_FOR_ANCHOR, timing=None)
        else:
            explanation["reason"] = (
                "the restrictions on this visit leave no permitted date; conflicting "
                "constraints: " + ", ".join(result.conflicting_constraints))
            update.update(status=PatientEventStatus.UNRESOLVED, timing=None)
        update["explanation"] = explanation
        updated = item.model_copy(update=update)
        own_planned[item.occurrence_index] = (
            updated.timing.nominal_start
            if updated.status == PatientEventStatus.RESOLVED and updated.timing is not None
            else None)
        output.append(updated)
    return output


def apply_activity_constraints(
    activity: Activity,
    evaluated: EvaluatedActivity,
    context: PatientContext,
    scope: tuple[str, int],
) -> EvaluatedActivity:
    owner = f"{scope[0]}/{activity.code or activity.id}"
    roots = constraint_roots(owner, activity.timing, activity.constraints)
    if not roots:
        return evaluated
    if evaluated.status == PatientActivityStatus.RESOLVED:
        base = (_base_window(evaluated.timing, f"{owner}/timing")
                if activity.timing is not None else None)
    elif evaluated.status == PatientActivityStatus.WAITING_FOR_ANCHOR and activity.timing is not None:
        base = BaseWindow(
            source_id=f"{owner}/timing", known=False,
            missing=_timing_missing_inputs(activity.timing, context, owner, activity_scope=scope))
    else:
        return evaluated
    source = ContextValueSource(context, event_code=scope[0], occurrence_index=scope[1])
    explanation = dict(evaluated.explanation)
    try:
        result = evaluate_feasibility(roots, source, base=base)
    except UnsupportedConstraintError as error:
        explanation["reason"] = str(error)
        return evaluated.model_copy(update={
            "status": PatientActivityStatus.UNRESOLVED, "timing": None,
            "explanation": explanation})
    explanation["feasibility"] = result.summary()
    update: dict[str, object] = _feasibility_fields(result)
    if result.status == FeasibilityStatus.FEASIBLE:
        timing = evaluated.timing or ResolvedTiming(precision="CONSTRAINT")
        timing, notes = _timing_from_feasible(timing, result)
        explanation.update(notes)
        explanation["result"] = timing.model_dump(mode="json")
        update["timing"] = timing
    elif result.status == FeasibilityStatus.INCOMPLETE:
        explanation["reason"] = "waiting for " + _describe_missing(result.missing_inputs)
        update.update(status=PatientActivityStatus.WAITING_FOR_ANCHOR, timing=None)
    else:
        explanation["reason"] = (
            "the restrictions on this activity leave no permitted time; conflicting "
            "constraints: " + ", ".join(result.conflicting_constraints))
        update.update(status=PatientActivityStatus.UNRESOLVED, timing=None)
    update["explanation"] = explanation
    return evaluated.model_copy(update=update)


@dataclass
class _Run:
    """Per-evaluation WP3 inputs, resolved once (never stored on the evaluator)."""

    schedule: UniversalSchedule
    facts: OccurrenceFacts
    processes: ProcessStates
    periods: list[AssignmentPeriod]
    #: Events whose series has no final occurrence yet (LAST stays unknown).
    open_series: set[str] = field(default_factory=set)
    #: Dates planned so far in THIS evaluation, by event code and occurrence index.
    planned_series: dict[str, dict[int, Temporal]] = field(default_factory=dict)
    #: What is already known about each logical occurrence (P1-02).
    history: dict[str, dict[int, LogicalOccurrenceState]] = field(default_factory=dict)


def _open_series(
    schedule: UniversalSchedule,
    plan: ConditionalPlan,
    cycle_plans: dict[str, CyclePlan],
    processes: ProcessStates,
) -> set[str]:
    """Series whose LAST occurrence cannot be known yet (design: "LAST in an
    unfinished series can be unknown").

    Open-ended by rule - HORIZON termination (or a CONDITION one that has not
    fired), or a conditional repeat until resolution that is unresolved - and
    not ended by a terminal state of its process. A materialization horizon is
    never the end of a series.
    """
    output: set[str] = set()
    for event in schedule.events:
        termination = None
        if event.recurrence is not None:
            termination = event.recurrence.termination
        elif event.recurrence_plan is not None:
            termination = event.recurrence_plan.termination
        elif event.calendar_recurrence is not None:
            termination = event.calendar_recurrence.termination
        else:
            cycles = _every_cycle_plan(event, cycle_plans)
            termination = cycles.termination if cycles is not None else None
        open_ended = termination is not None and termination.type in {"HORIZON", "CONDITION"}
        activation = plan.activations.get(event.code)
        if activation is not None and activation.repeat_until_resolved and activation.resolution_date is None:
            open_ended = True
        if not open_ended:
            continue
        timeline = processes.process_for(event.code)
        if (timeline is not None and timeline.terminal() is not None) or processes.consent_withdrawn:
            continue
        output.add(event.code)
    return output


def _with_assignment_values(context: PatientContext, values: dict[str, str | None]) -> PatientContext:
    """A copy of the context carrying one assignment period's values."""
    updated = context.model_copy(deep=True)
    named = {"ARM": "arm_code", "COHORT": "cohort_code", "POPULATION": "population_code"}
    for dimension, value in values.items():
        if dimension in named:
            setattr(updated, named[dimension], value)
        # ``dimension_values`` may also carry a named dimension (the
        # assignment workflow writes both), and it wins in applicability, so
        # it must hold the period's value too.
        if value is None:
            updated.dimension_values.pop(dimension, None)
        else:
            updated.dimension_values[dimension] = [value]
    return updated


def _period_json(period: AssignmentPeriod) -> dict[str, object]:
    return {
        "start": period.start.isoformat() if period.start else None,
        "end": period.end.isoformat() if period.end else None,
        "values": dict(period.values), "facts": list(period.fact_ids),
    }


def _item_date(item: EvaluatedEvent) -> date | None:
    timing = item.timing
    if timing is None:
        return None
    value = timing.nominal_start or timing.earliest or timing.latest
    return _as_date(value) if value is not None else None


def _reindex(items: list[EvaluatedEvent], episode_index: int, condition_code: str) -> list[EvaluatedEvent]:
    """Give one condition episode's occurrences their own identities (AT-10)."""
    output = []
    for item in items:
        index = episode_occurrence_index(episode_index, item.occurrence_index)
        explanation = dict(item.explanation)
        explanation["condition_episode"] = {
            "condition": condition_code, "episode": episode_index,
            **occurrence_identity(index),
        }
        output.append(item.model_copy(update={"occurrence_index": index, "explanation": explanation}))
    return output


class ScheduleEvaluator:
    version = "uctsm-evaluator.v1"

    def _evaluate_with_history(
        self,
        event: Event,
        context: PatientContext,
        prior: dict[str, list[EvaluatedEvent]],
        horizon: date,
        *,
        plan: ConditionalPlan,
        assignments: frozenset[str],
        cycle_plans: dict[str, CyclePlan],
        run: _Run,
    ) -> list[EvaluatedEvent]:
        """Evaluate one event over its assignment periods and condition episodes.

        RL-11/AT-11: with dated assignment history, each occurrence is judged
        against the assignment in force ON ITS DATE; a late crossover changes
        only the period after its effective date.
        Without history (every existing context) this is ``_evaluate_event``.
        """
        def once(ctx: PatientContext) -> list[EvaluatedEvent]:
            results = self._evaluate_event(
                event, ctx, prior, horizon, plan=plan, assignments=assignments,
                cycle_plans=cycle_plans, run=run)
            return self._episode_identity(event, plan, results)

        history_dimensions = {dimension for period in run.periods for dimension in period.values}
        uses_history = any(
            rule.dimension in history_dimensions for rule in event.applicability)
        if not run.periods or not uses_history:
            return once(context)
        kept: list[EvaluatedEvent] = []
        seen: set[int] = set()
        undated: list[EvaluatedEvent] = []
        last = len(run.periods) - 1
        for number, period in enumerate(run.periods):
            results = once(_with_assignment_values(context, period.values))
            for item in results:
                when = _item_date(item)
                if when is None:
                    if number == last:
                        undated.append((item, period))
                    continue
                if not period.contains(when) or item.occurrence_index in seen:
                    continue
                seen.add(item.occurrence_index)
                explanation = dict(item.explanation)
                explanation["assignment_period"] = _period_json(period)
                kept.append(item.model_copy(update={"explanation": explanation}))
        for item, period in undated:
            if item.occurrence_index in seen:
                continue
            if kept and item.status == PatientEventStatus.NOT_APPLICABLE:
                # Applicable earlier in the history: the dated occurrences of
                # that period stand; nothing is due under the current one.
                continue
            explanation = dict(item.explanation)
            explanation["assignment_period"] = _period_json(period)
            kept.append(item.model_copy(update={"explanation": explanation}))
            seen.add(item.occurrence_index)
        return sorted(kept, key=lambda item: item.occurrence_index)

    @staticmethod
    def _episode_identity(
        event: Event, plan: ConditionalPlan, results: list[EvaluatedEvent],
    ) -> list[EvaluatedEvent]:
        """AT-10: an ADD_EVENT visit of a LATER condition episode gets that
        episode's own identities, so it can never reuse - and overwrite - the
        first episode's persisted occurrence. Episode 0 keeps its numbering
        (NF-08). Repeats are numbered per episode in ``_episode_repeats``."""
        activation = plan.activations.get(event.code)
        if activation is None or activation.repeat_interval is not None or not activation.episode_index:
            return results
        return _reindex(results, activation.episode_index, activation.condition_code)

    def evaluate(
        self,
        schedule: UniversalSchedule,
        context: PatientContext,
        *,
        horizon: date,
        rolling: RollingHorizon | None = None,
        today: date | None = None,
    ) -> EvaluationResult:
        if schedule.schedule_metadata.status != ScheduleStatus.APPROVED:
            raise ScheduleNotApprovedError("patient schedules require an approved schedule version")
        if context.schedule_version_id != schedule.schedule_version_id:
            raise ValueError("patient context is pinned to a different schedule version")
        blocking = ScheduleValidator.blocking(
            ScheduleValidator().validate(schedule, evaluating_approved=True))
        if blocking:
            raise ValueError("approved schedule failed integrity validation")

        events_by_code = {event.code: event for event in schedule.events}
        evaluated: list[EvaluatedEvent] = []
        results_by_code: dict[str, list[EvaluatedEvent]] = {}
        snapshot = context
        context = localise_context(context)
        working_context = context.model_copy(deep=True)
        cycle_plans = schedule_cycle_plans(schedule)

        # WHAT becomes applicable is decided first; WHEN it happens is decided below.
        plan = build_conditional_plan(schedule, working_context)
        for anchor in schedule.anchors:
            if anchor.source_condition_code is None:
                continue
            value = plan.condition_anchors.get(anchor.source_condition_code)
            if value is not None:
                working_context.anchors[anchor.code] = value
        blocks_by_event = {
            code: block
            for block in schedule.repeat_blocks for code in block.event_codes
        }
        assignments = assignment_dimensions(schedule)
        if cycle_plans:
            publish_cycle_anchors(cycle_plans, context, working_context, horizon)
        # WP3 facts. Every structure is empty for a context without them, and
        # each step below is then a no-op (NF-08).
        run = _Run(
            schedule=schedule,
            facts=OccurrenceFacts.build(context.occurrence_facts),
            processes=build_process_states(
                schedule, [*context.process_transitions, *plan.process_transitions]),
            periods=assignment_periods(context.assignment_history),
            history=_history_by_code(context),
        )
        run.open_series = _open_series(schedule, plan, cycle_plans, run.processes)

        for code in topological_event_codes(schedule):
            event = events_by_code[code]
            # P5-02: a MANUAL_REBASE moves the track anchor for this track only and
            # only for occurrences inside its effective period (stable-date rule).
            event_context = working_context
            event_results = self._evaluate_with_history(
                event, event_context, results_by_code, horizon, plan=plan,
                assignments=assignments, cycle_plans=cycle_plans, run=run,
            )
            variants = rebase_variants(event, working_context, schedule)
            if variants:
                event_results = choose_rebased(event_results, [
                    (rebase, self._evaluate_with_history(
                        event, scoped, results_by_code, horizon, plan=plan,
                        assignments=assignments, cycle_plans=cycle_plans, run=run))
                    for rebase, scoped in variants])
            event_results = apply_event_constraints(
                event, event_results, working_context, results_by_code, run=run)
            event_results = self._apply_block_state(
                event_results, plan, blocks_by_event.get(code))
            event_results = apply_process_states(
                code, event_results, run.processes, run.facts.outcome,
                holds_applied=event.calendar_recurrence is not None)
            event_results = [annotate_occurrence(item, run.facts) for item in event_results]
            # P5-01/P5-03: track metadata and the operational layers (override,
            # cancellation) beside - never instead of - the protocol target.
            event_results = apply_track_overlays(event, event_results, schedule, event_context)
            # Dependent events below see the FULL series; only what is written
            # down is trimmed, and that happens once at the end.
            evaluated.extend(event_results)
            results_by_code[code] = event_results
            dates = [item.timing.nominal_start for item in event_results if item.timing and item.timing.nominal_start]
            if dates:
                working_context.event_values[code] = dates
                run.planned_series[code] = {
                    item.occurrence_index: item.timing.nominal_start
                    for item in event_results if item.timing and item.timing.nominal_start}
            self._derive_event_anchors(schedule, code, event_results, context, working_context)
            if cycle_plans:
                # A cycle's Cycle 1 anchor may itself come from a visit just dated.
                publish_cycle_anchors(cycle_plans, context, working_context, horizon)
        evaluated, repeat_summaries = _apply_rolling_horizon(
            evaluated, schedule, plan, rolling, horizon)
        return EvaluationResult(
            repeat_summaries=repeat_summaries,
            schedule_version_id=schedule.schedule_version_id,
            patient_id=context.patient_id,
            evaluator_version=self.version,
            input_snapshot=snapshot.model_dump(mode="json"),
            events=evaluated,
            confinements=self._evaluate_confinements(
                schedule, working_context, results_by_code, plan,
                today=today or date.today()),
        )

    @staticmethod
    def _derive_event_anchors(
        schedule: UniversalSchedule,
        code: str,
        event_results: list[EvaluatedEvent],
        recorded: PatientContext,
        working_context: PatientContext,
    ) -> None:
        """Set anchors that come from an event (``Anchor.source_event_code``).

        Doc C6: only what actually happened is actual. A date the site recorded
        for the source event is actual; a date this evaluation merely CALCULATED
        for it is a plan, so it lands in ``planned_anchors`` and dates only rules
        that accept a planned value. A value recorded directly on the anchor
        always wins over one derived here, because a person entered it.

        A calculated date is used as a plan only when it is an exact target:
        a RANGE's start is the opening of a window, not the day surgery is
        planned for, so a range-timed event yields no planned anchor at all.

        "Last" is only actual once no scheduled occurrence remains outstanding:
        the second of six doses is the latest dose given, not the last dose.
        """
        actual_dates = recorded.actual_event_values.get(code) or []
        outstanding = [
            item for item in event_results
            if item.status == PatientEventStatus.RESOLVED
            and item.timing is not None and item.timing.nominal_start is not None
        ]
        planned_dates = [
            item.timing.nominal_start for item in outstanding
            if item.timing.precision == "EXACT"
        ]
        for anchor in schedule.anchors:
            if anchor.source_event_code != code:
                continue
            selection = str((anchor.derivation_rule or {}).get("selection", "LAST")).upper()
            pick = min if selection == "FIRST" else max
            actual_is_final = selection == "FIRST" or len(actual_dates) >= len(outstanding)
            if anchor.code not in recorded.anchors and actual_dates and actual_is_final:
                working_context.anchors[anchor.code] = pick(actual_dates)
            if anchor.code not in recorded.planned_anchors and planned_dates:
                working_context.planned_anchors[anchor.code] = pick(planned_dates)

    def _evaluate_confinements(
        self,
        schedule: UniversalSchedule,
        context: PatientContext,
        results: dict[str, list[EvaluatedEvent]],
        plan: ConditionalPlan,
        *,
        today: date,
    ) -> list[EvaluatedConfinement]:
        """Resolve continuous inpatient stays as one parent episode each.

        A protocol that lists Day -1 through Day 3 while the patient stays admitted
        is ONE episode with four study days, not four hospital visits. Admission,
        dosing, and discharge stay distinct events so the dosing anchor is never
        replaced by the admission time.
        """
        output: list[EvaluatedConfinement] = []
        for definition in schedule.confinement_episodes:
            output.append(
                self._evaluate_confinement(definition, context, results, plan, today=today))
        return _flag_overlapping_episodes(output)

    def _evaluate_confinement(
        self,
        definition: ConfinementEpisodeDefinition,
        context: PatientContext,
        results: dict[str, list[EvaluatedEvent]],
        plan: ConditionalPlan,
        *,
        today: date,
    ) -> EvaluatedConfinement:
        base = {
            "episode_definition_id": definition.id,
            "episode_code": definition.code,
            "display_name": definition.display_name,
        }
        if _evaluate_applicability(definition.applicability, context) != TruthValue.TRUE:
            return EvaluatedConfinement(status=ConfinementStatus.NOT_APPLICABLE, **base)
        conditions = [evaluate_condition(item, context.state) for item in definition.conditions]
        if TruthValue.FALSE in conditions:
            return EvaluatedConfinement(status=ConfinementStatus.NOT_APPLICABLE, **base)

        planned_admission = _first_nominal(results.get(definition.admission_event_code))
        planned_discharge = _first_nominal(results.get(definition.discharge_event_code))
        actual_admission = _first_actual(context, definition.admission_event_code)
        actual_discharge = _first_actual(context, definition.discharge_event_code)

        # A conditional extension moves the expected discharge; it never creates a
        # second episode and never closes the current one.
        extended_by: list[str] = []
        for extension in plan.confinement_extensions:
            if extension.episode_code != definition.code or planned_discharge is None:
                continue
            planned_discharge = add_amount(planned_discharge, extension.extension)
            extended_by.append(extension.condition_code)

        explanation: dict[str, object] = {
            "admission_event": definition.admission_event_code,
            "discharge_event": definition.discharge_event_code,
            "dose_events": list(definition.dose_event_codes),
            "evidence_refs": [str(item) for item in definition.evidence_refs],
        }
        if actual_discharge is not None:
            status = ConfinementStatus.DISCHARGED
        elif actual_admission is not None:
            # The patient stays confined until an actual discharge is recorded, even
            # if the planned discharge date has already passed - but a passed date
            # with nothing recorded still needs a CRC to look, so it is surfaced as
            # its own status rather than sitting silently inside IN_CONFINEMENT.
            if planned_discharge is not None and today >= _as_date(planned_discharge):
                status = ConfinementStatus.DISCHARGE_PLANNED
            else:
                status = ConfinementStatus.EXTENDED if extended_by else ConfinementStatus.IN_CONFINEMENT
        elif planned_admission is None:
            status = ConfinementStatus.WAITING_FOR_ANCHOR
            explanation["reason"] = (
                f"admission event {definition.admission_event_code} has no resolved date")
        else:
            status = ConfinementStatus.UPCOMING

        day_origin = actual_admission or planned_admission
        ordered = sorted(definition.days, key=lambda item: item.relative_day)
        # Clinical day numbering has no Day 0, so Day -1 is one day before Day 1.
        first_elapsed = _elapsed_day(ordered[0].relative_day) if ordered else 0
        days = []
        for day in ordered:
            value = None
            if day_origin is not None:
                offset = _elapsed_day(day.relative_day) - first_elapsed
                value = add_amount(
                    day_origin, TemporalAmount(value=offset, unit=TimeUnit.DAY))
            days.append(EvaluatedConfinementDay(
                day_label=day.day_label, relative_day=day.relative_day,
                scheduled_date=value, activity_codes=list(day.activity_codes),
            ))
        # Doc 6 s26 rule 1: an impossible stay is reported, never silently accepted.
        integrity = _confinement_integrity(
            planned_admission, planned_discharge, actual_admission, actual_discharge)
        if integrity:
            explanation["integrity_findings"] = integrity
            status = ConfinementStatus.WAITING_FOR_ANCHOR
            explanation["reason"] = integrity[0]
        return EvaluatedConfinement(
            status=status, planned_admission=planned_admission,
            actual_admission=actual_admission, planned_discharge=planned_discharge,
            actual_discharge=actual_discharge, extended_by=extended_by,
            days=days, explanation=explanation, **base,
        )

    def _conditional_gate(
        self,
        event: Event,
        plan: ConditionalPlan,
        base: dict[str, object],
        explanation: dict[str, object],
    ) -> list[EvaluatedEvent] | None:
        """Apply conditional consequences before any date is calculated.

        An untriggered conditional visit must never receive a date, because a dated
        visit becomes due, then overdue, and generates reminders for something the
        protocol may never require of this patient.
        """
        condition_code = plan.cancelled_event_codes.get(event.code)
        if condition_code is not None:
            explanation["reason"] = f"cancelled by condition {condition_code}"
            explanation["cancelled_by_condition"] = condition_code
            return [EvaluatedEvent(status=PatientEventStatus.CANCELLED, **base)]
        condition_code = plan.manual_review_event_codes.get(event.code)
        if condition_code is not None:
            explanation["reason"] = (
                f"condition {condition_code} requires manual review before scheduling")
            explanation["manual_review_for_condition"] = condition_code
            return [EvaluatedEvent(status=PatientEventStatus.UNRESOLVED, **base)]
        refused_rule = plan.refused_event_codes.get(event.code)
        if refused_rule is not None:
            # Doc I8: said out loud, so a trial-level rule that was recorded as
            # active is never mistaken for one that silently failed to act.
            explanation["refused_trial_level_rule"] = {
                "rule": refused_rule, "scope": plan.refused_rules.get(refused_rule),
            }
        if plan.is_conditional(event.code) and not plan.is_active(event.code):
            explanation["reason"] = "protocol condition has not occurred for this patient"
            explanation["conditional"] = True
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_CONDITION, **base)]
        return None

    def _expand_conditional_repeats(
        self,
        event: Event,
        plan: ConditionalPlan,
        horizon: date,
        base: dict[str, object],
    ) -> list[EvaluatedEvent]:
        """Generate 'repeat every N days until resolved' occurrences (doc 2 s17).

        Generation stops at the recorded resolution date, so once the PI/CRC marks
        the condition resolved no further repeat visit is produced. While the
        condition remains active, generation is bounded by the evaluation horizon.
        """
        activation = plan.activations[event.code]
        output = self._episode_repeats(event, activation, horizon, base)
        if not output:
            explanation = dict(base["explanation"])
            explanation["reason"] = "condition is resolved; no further repeat is due"
            return [EvaluatedEvent(
                **{**base, "explanation": explanation},
                status=PatientEventStatus.NOT_APPLICABLE,
            )]
        return output

    @staticmethod
    def _episode_repeats(
        event: Event, activation: "EventActivation", horizon: date, base: dict[str, object],
    ) -> list[EvaluatedEvent]:
        interval = activation.repeat_interval
        current: Temporal = activation.occurrence_date
        stop: Temporal = horizon
        if isinstance(current, datetime) and isinstance(stop, date) and not isinstance(stop, datetime):
            stop = datetime.combine(stop, datetime.max.time(), tzinfo=current.tzinfo)
        resolution = activation.resolution_date
        if resolution is not None:
            if isinstance(current, datetime) and not isinstance(resolution, datetime):
                resolution = datetime.combine(resolution, datetime.max.time(), tzinfo=current.tzinfo)
            elif isinstance(resolution, datetime) and not isinstance(current, datetime):
                resolution = resolution.date()
            stop = min(stop, resolution)
        output: list[EvaluatedEvent] = []
        index = 0
        origin = current
        window = effective_window(event)
        # Counted from the condition's occurrence date, never from the previous
        # repeat, so a monthly repeat cannot drift onto a clamped month-end.
        current = nth_occurrence(origin, interval, 1)
        while current <= stop and index < 1000:
            earliest, latest = _window_bounds(current, window)
            timing = ResolvedTiming(
                nominal_start=current, nominal_end=current, earliest=earliest,
                latest=latest, precision="EXACT")
            explanation = dict(base["explanation"])
            explanation["conditional_repeat"] = {
                "condition": activation.condition_code,
                "index": index,
                "result": timing.model_dump(mode="json"),
            }
            occurrence_index = index
            if activation.episode_index:
                occurrence_index = episode_occurrence_index(activation.episode_index, index)
                explanation["condition_episode"] = {
                    "condition": activation.condition_code,
                    "episode": activation.episode_index,
                    **occurrence_identity(occurrence_index),
                }
            output.append(EvaluatedEvent(
                **{**base, "explanation": explanation},
                occurrence_index=occurrence_index, status=PatientEventStatus.RESOLVED,
                timing=timing, activities=[],
            ))
            index += 1
            current = nth_occurrence(origin, interval, index + 1)
        return output

    @staticmethod
    def _apply_block_state(
        results: list[EvaluatedEvent],
        plan: ConditionalPlan,
        block: "RepeatBlock | None",
    ) -> list[EvaluatedEvent]:
        """Apply stop, pause and resume to a repeating block (doc 3 s19-s20).

        Occurrences already in the past are untouched: stopping a treatment branch
        cancels what has not happened, it does not erase completed cycles.

        Resume is the part the protocol has to answer. Once a paused block comes
        back, either the cadence stayed on its original grid (NOMINAL) or it
        restarts from the day the patient actually resumed (ACTUAL_RESUME). When
        the protocol does not say, no date is invented: the occurrences after the
        resume go to UNRESOLVED for a reviewer.
        """
        if block is None:
            return results
        directive = plan.block_directives.get(block.code)
        if directive is None:
            return results

        stopped_from: Temporal | None = None
        for change in directive.changes:
            if change.state == BlockState.STOPPED:
                stopped_from = change.effective_date
                break
        pauses = directive.paused_intervals()
        resumed = directive.resumed_on()
        if stopped_from is None and not pauses:
            return results

        updated: list[EvaluatedEvent] = []
        for item in results:
            start_date = item.timing.nominal_start if item.timing else None
            if start_date is None:
                updated.append(item)
                continue

            if stopped_from is not None and not _before_or_equal(start_date, stopped_from):
                updated.append(_with_block_reason(
                    item, block.code, PatientEventStatus.CANCELLED,
                    directive.condition_code,
                    f"repeating block {block.code} was stopped by condition "
                    f"{directive.condition_code}",
                    BlockState.STOPPED,
                ))
                continue

            paused_in = _containing_pause(start_date, pauses)
            if paused_in is not None:
                held = _with_block_reason(
                    item, block.code, PatientEventStatus.PAUSED, directive.condition_code,
                    f"repeating block {block.code} is paused; this occurrence is not "
                    "due while the patient is off the cadence",
                    BlockState.PAUSED,
                )
                resolved_by = plan.held_after_resolution.get(block.code)
                if resolved_by is not None and paused_in[1] is None:
                    explanation = dict(held.explanation)
                    explanation["held_after_resolution"] = resolved_by
                    explanation["reason"] = (
                        f"condition {resolved_by} is resolved, but resolution does not resume "
                        f"block {block.code}; it stays paused until an explicit resume is recorded")
                    held = held.model_copy(update={"explanation": explanation})
                updated.append(held)
                continue

            if resumed is None or _before_or_equal(start_date, resumed):
                updated.append(item)
                continue
            updated.append(_apply_resume_mode(item, block, pauses, resumed))
        return updated

    def _evaluate_event(
        self,
        event: Event,
        context: PatientContext,
        prior: dict[str, list[EvaluatedEvent]],
        horizon: date,
        *,
        plan: ConditionalPlan | None = None,
        assignments: frozenset[str] | None = None,
        cycle_plans: dict[str, CyclePlan] | None = None,
        run: "_Run | None" = None,
    ) -> list[EvaluatedEvent]:
        plan = plan or ConditionalPlan()
        applicability = _evaluate_applicability(event.applicability, context)
        condition = _evaluate_conditions(event, context)
        explanation: dict[str, object] = {
            "rule": event.code,
            "timing": event.timing.model_dump(mode="json"),
            "evidence_refs": [str(item) for item in event.evidence_refs],
        }
        base = {
            "event_definition_id": event.id,
            "event_code": event.code,
            "applicability_result": applicability.value,
            "condition_result": condition.value,
            "explanation": explanation,
            "visit_mode": event.visit_mode,
            "allowed_visit_modes": list(event.allowed_visit_modes),
        }
        if applicability == TruthValue.FALSE or condition == TruthValue.FALSE:
            return [EvaluatedEvent(status=PatientEventStatus.NOT_APPLICABLE, **base)]
        if event.activation == "ON_DEMAND":
            # Doc 10 s13-s15: a protocol-defined unscheduled visit is available,
            # never due. It gets a date only when the site creates one.
            return _evaluate_on_demand(event, context, base, explanation)
        conditional_result = self._conditional_gate(event, plan, base, explanation)
        if conditional_result is not None:
            return conditional_result
        if applicability == TruthValue.UNKNOWN and condition != TruthValue.UNKNOWN:
            awaited = _awaited_assignments(
                event.applicability, context,
                assignments if assignments is not None else ASSIGNMENT_DIMENSIONS)
            if awaited:
                # Doc H2: e.g. every arm visit before a late randomisation.
                explanation["reason"] = (
                    f"waiting for the patient's {', '.join(awaited)} assignment")
                explanation["waiting_for_assignment"] = awaited
                return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ASSIGNMENT, **base)]
        if applicability == TruthValue.UNKNOWN or condition == TruthValue.UNKNOWN:
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_CONDITION, **base)]
        # P4-01: each dependency's own CONDITION is decided before its source is
        # looked at. A false condition removes the dependency (it can then not
        # block, and is never asked for an actual or a manual date); an unknown
        # one waits for the condition, never becoming false.
        outcomes = evaluate_dependencies(
            event, state=context.state,
            source_statuses={
                code: [(item.occurrence_index, item.status) for item in items]
                for code, items in prior.items()})
        dependency_detail = [item.as_dict() for item in outcomes]
        verdict = aggregate_dependency_outcomes(outcomes)
        if verdict in (DependencyOutcomeKind.BLOCKED, DependencyOutcomeKind.WAITING_FOR_SOURCE):
            blocked = [item.source_event_code for item in outcomes if item.outcome in (
                DependencyOutcomeKind.BLOCKED, DependencyOutcomeKind.WAITING_FOR_SOURCE)]
            return [EvaluatedEvent(
                status=PatientEventStatus.BLOCKED,
                dependency_result={"blocked_by": blocked, "outcome": verdict.value,
                                   "dependencies": dependency_detail}, **base,
            )]
        if verdict == DependencyOutcomeKind.WAITING_FOR_CONDITION:
            waiting = [item.source_event_code for item in outcomes
                       if item.outcome == DependencyOutcomeKind.WAITING_FOR_CONDITION]
            explanation["reason"] = "waiting for the condition on a dependency: " + "; ".join(
                item.reason for item in outcomes
                if item.outcome == DependencyOutcomeKind.WAITING_FOR_CONDITION)
            return [EvaluatedEvent(
                status=PatientEventStatus.WAITING_FOR_CONDITION,
                dependency_result={"waiting_for_condition": waiting, "outcome": verdict.value,
                                   "dependencies": dependency_detail}, **base,
            )]
        active_codes = active_source_codes(outcomes)
        timing_context = context
        dependency_result: dict[str, object] = {
            "mode": event.dependency_mode.value if event.dependency_mode else DependencyMode.NOMINAL.value,
        }
        if dependency_detail:
            dependency_result["dependencies"] = dependency_detail
        if event.dependency_mode == DependencyMode.ACTUAL_PREVIOUS_EVENT:
            exact = _exact_indices(event, prior)
            missing_actuals = [
                code for code in active_codes
                if not _actual_recorded(event, code, context, run, exact.get(code))
            ]
            if missing_actuals:
                return [EvaluatedEvent(
                    status=PatientEventStatus.BLOCKED,
                    dependency_result={
                        **dependency_result, "waiting_for_actual": missing_actuals,
                    }, **base,
                )]
            timing_context = context.model_copy(deep=True)
            history_by_code = run.history if run is not None else _history_by_code(context)
            for code in active_codes:
                if code in exact:
                    # numbered source: ONLY that occurrence's own actual date
                    wanted = sorted(exact[code])
                    states = history_by_code.get(code, {})
                    values = [states[index].actual_visit_date for index in wanted
                              if index in states and states[index].actual_visit_date is not None]
                    timing_context.event_values[code] = values
                elif context.actual_event_values.get(code):
                    timing_context.event_values[code] = list(context.actual_event_values[code])
        elif event.dependency_mode == DependencyMode.MANUAL:
            manual = context.state.get("manual_dependency_dates")
            manual_values = manual if isinstance(manual, dict) else {}
            missing_manual = [code for code in active_codes if code not in manual_values]
            if missing_manual:
                return [EvaluatedEvent(
                    status=PatientEventStatus.WAITING_FOR_ANCHOR,
                    dependency_result={
                        **dependency_result, "waiting_for_manual_date": missing_manual,
                    }, **base,
                )]
            timing_context = context.model_copy(deep=True)
            for code in active_codes:
                value = manual_values[code]
                if isinstance(value, str):
                    try:
                        value = datetime.fromisoformat(value)
                    except ValueError:
                        value = date.fromisoformat(value)
                timing_context.event_values[code] = [value]
        waiting_for_actual = _record_anchor_basis(event, timing_context, explanation)
        timing_context, basis_report = _prepare_basis_values(event, timing_context, run)
        if basis_report:
            explanation["temporal_basis"] = basis_report
        cycle_plan = _cycle_plan_for(event, cycle_plans or {})
        if cycle_plan is not None:
            return self._expand_cycle_days(
                event, cycle_plan, timing_context, horizon,
                {**base, "dependency_result": dependency_result})
        if event.recurrence_plan is not None:
            return self._expand_recurrence_plan(
                event, timing_context, horizon,
                {**base, "dependency_result": dependency_result})
        if event.calendar_recurrence is not None:
            return self._expand_calendar_recurrence(
                event, timing_context, horizon,
                {**base, "dependency_result": dependency_result}, run)
        try:
            timing = evaluate_timing(event.timing, timing_context)
        except UnsupportedTimingError as error:
            explanation["reason"] = str(error)
            return [EvaluatedEvent(status=PatientEventStatus.UNRESOLVED, **base)]
        if timing is None:
            explanation["reason"] = "required anchor or event reference is not available"
            if waiting_for_actual:
                explanation["reason"] = _waiting_for_actual_reason(waiting_for_actual)
                explanation["waiting_for_actual_anchor"] = waiting_for_actual
            missing_basis = [item for item in basis_report if item["value"] is None]
            if missing_basis and not waiting_for_actual:
                explanation["waiting_for_temporal_basis"] = missing_basis
                explanation["reason"] = "waiting for " + ", ".join(
                    f"the {str(item['temporal_basis']).lower().replace('_', ' ')} date of "
                    f"{item['event_code']}" for item in missing_basis)
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR,
                dependency_result=dependency_result, **base)]
        activation = plan.activations.get(event.code)
        if activation is not None and activation.repeat_interval is not None:
            return self._expand_conditional_repeats(
                event, plan, horizon, {**base, "dependency_result": dependency_result})
        if event.recurrence is None:
            if event.window is not None and timing.precision == "EXACT" and timing.nominal_start is not None:
                earliest, latest = _window_bounds(timing.nominal_start, event.window)
                timing = timing.model_copy(update={"earliest": earliest, "latest": latest})
            explanation["result"] = timing.model_dump(mode="json")
            return [EvaluatedEvent(
                status=PatientEventStatus.RESOLVED, timing=timing,
                dependency_result=dependency_result,
                activities=evaluate_activities(event, timing_context, occurrence_index=0),
                **base,
            )]
        recurrence_base = {**base, "dependency_result": dependency_result}
        return self._expand_recurrence(
            event, timing, timing_context, horizon, recurrence_base, run)

    def _expand_recurrence(
        self,
        event: Event,
        first: ResolvedTiming,
        context: PatientContext,
        horizon: date,
        base: dict[str, object],
        run: "_Run | None" = None,
    ) -> list[EvaluatedEvent]:
        rule = event.recurrence
        assert rule is not None
        if rule.basis is not None or rule.basis_status == "UNCONFIRMED":
            return self._expand_basis_recurrence(event, first, context, horizon, base, run)
        origin = _resolve_reference(rule.start_reference, context)
        if origin is None:
            waiting_for_actual = _record_anchor_basis(event, context, base["explanation"])
            if waiting_for_actual:
                base["explanation"]["reason"] = _waiting_for_actual_reason(waiting_for_actual)
                base["explanation"]["waiting_for_actual_anchor"] = waiting_for_actual
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
        current = origin
        if rule.start_offset is not None:
            current = add_amount(origin, rule.start_offset)
        bound = _resolve_termination(
            rule.termination, context, horizon,
            effective_key=f"recurrence:{event.code}", sample=current)
        if bound.status is not None:
            return [_undated(base, bound)]
        if rule.calendar_policy_ref is not None and rule.calendar_policy_ref not in KNOWN_CALENDAR_POLICIES:
            base["explanation"]["reason"] = (
                f"calendar policy {rule.calendar_policy_ref!r} is not one the engine executes; "
                "the series is not dated until it is")
            return [EvaluatedEvent(status=PatientEventStatus.UNRESOLVED, **base)]
        output: list[EvaluatedEvent] = []
        index = 0
        # Occurrence numbering from the original start: step 0 is the start
        # itself, so a rule that excludes the start begins at step 1.
        first_step = 0 if rule.include_start else 1
        window = effective_window(event)
        monthly = rule.interval.unit in (TimeUnit.MONTH, TimeUnit.YEAR)
        intended_day = _as_date(
            add_amount(origin, rule.start_offset) if rule.start_offset is not None else origin).day

        def occurrence(step: int) -> Temporal:
            return nth_occurrence(origin, rule.interval, step, rule.start_offset)

        # A schedule that predates ``RecurrenceRule.basis`` keeps its dates. When
        # the event's own timing disagrees with where this series starts, that is
        # surfaced (never silently resolved by moving dates).
        legacy_note: dict[str, object] = {"status": "UNSTATED_LEGACY", "basis": None}
        series_start = occurrence(0)
        if first.precision == "EXACT" and first.nominal_start is not None \
                and _as_date(first.nominal_start) != _as_date(series_start):
            legacy_note["start_conflict"] = {
                "event_timing": _as_date(first.nominal_start).isoformat(),
                "series_start": _as_date(series_start).isoformat(),
                "dates_changed": False,
            }

        current = occurrence(first_step)
        while index < bound.max_count and bound.allows(current):
            earliest, latest = _window_bounds(current, window)
            timing = ResolvedTiming(
                nominal_start=current, nominal_end=current, earliest=earliest,
                latest=latest, precision="EXACT",
            )
            explanation = dict(base["explanation"])
            explanation["recurrence"] = {"index": index, "result": timing.model_dump(mode="json"),
                                         "basis": legacy_note}
            if monthly and _as_date(current).day < intended_day:
                # AT-19: a month-end clamp is a material calendar policy. The
                # date is the engine's existing MONTH_END_CLAMP.v1 result (NF-08),
                # and whether the protocol stated that policy stays visible.
                explanation["recurrence"]["calendar_policy"] = {
                    "policy": rule.calendar_policy_ref or "MONTH_END_CLAMP.v1",
                    "stated": rule.calendar_policy_ref is not None,
                    "month_end_clamped": True,
                }
            if bound.provisional is not None:
                explanation["recurrence"]["termination"] = bound.provisional
            output.append(EvaluatedEvent(
                **{**base, "explanation": explanation},
                occurrence_index=index, status=PatientEventStatus.RESOLVED, timing=timing,
                activities=evaluate_activities(event, context, occurrence_index=index),
            ))
            index += 1
            current = occurrence(first_step + index)
        if output and index >= bound.max_count and bound.allows(current):
            marker = bound.interrupted_by_cap()
            if marker is not None:
                last = dict(output[-1].explanation)
                recurrence = dict(last.get("recurrence") or {})
                recurrence["engine_safety_cap"] = marker
                last["recurrence"] = recurrence
                output[-1] = output[-1].model_copy(update={"explanation": last})
        return output

    def _expand_basis_recurrence(
        self,
        event: Event,
        first: ResolvedTiming,
        context: PatientContext,
        horizon: date,
        base: dict[str, object],
        run: "_Run | None",
    ) -> list[EvaluatedEvent]:
        """An interval series with an EXPLICIT basis (P4-02/P4-03).

        Occurrence 0 is the event's own resolved timing (so a first visit "+14
        days" is never restarted at the raw anchor and its offset is never
        applied twice); later occurrences follow ``rule.basis``. Nothing here
        invents a date for an actual that has not been recorded.
        """
        rule = event.recurrence
        assert rule is not None
        basis = rule.basis
        explanation = base["explanation"]
        basis_note: dict[str, object] = {
            "status": rule.basis_status or "STATED",
            "basis": basis.value if basis is not None else None}
        if rule.actual_source is not None:
            basis_note["actual_source"] = rule.actual_source

        def refuse(reason: str, status: PatientEventStatus = PatientEventStatus.UNRESOLVED,
                   **extra: object) -> list[EvaluatedEvent]:
            explanation["reason"] = reason
            explanation["recurrence_basis"] = {**basis_note, **extra}
            return [EvaluatedEvent(status=status, **base)]

        if rule.basis_status == "UNCONFIRMED":
            return refuse(
                "the recurrence basis was proposed without protocol evidence and is not "
                "confirmed; the series is not dated until a reviewer states it")
        if rule.calendar_policy_ref is not None and rule.calendar_policy_ref not in KNOWN_CALENDAR_POLICIES:
            return refuse(
                f"calendar policy {rule.calendar_policy_ref!r} is not one the engine executes; "
                "the series is not dated until it is")

        anchor = _resolve_reference(rule.start_reference, context)
        first_date: Temporal | None = (
            first.nominal_start if first.precision == "EXACT" else None)

        if basis is RecurrenceBasis.FIXED_ANCHOR_SERIES:
            if anchor is None:
                waiting = _record_anchor_basis(event, context, explanation)
                if waiting:
                    explanation["reason"] = _waiting_for_actual_reason(waiting)
                    explanation["waiting_for_actual_anchor"] = waiting
                return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
            grid_origin: Temporal = anchor
            start = nth_occurrence(grid_origin, rule.interval, 0, rule.start_offset)
            if first_date is not None and _as_date(first_date) != _as_date(start):
                return refuse(
                    "the event's own timing does not fall on slot 0 of the fixed anchor grid "
                    f"({_as_date(first_date).isoformat()} vs {_as_date(start).isoformat()}); "
                    "a reviewer must reconcile the first occurrence and the grid",
                    event_timing=_as_date(first_date).isoformat(),
                    grid_start=_as_date(start).isoformat())

            def value_at(step: int) -> Temporal:
                return nth_occurrence(grid_origin, rule.interval, step, rule.start_offset)
        else:
            if first_date is None:
                if first.precision != "EXACT" and first.nominal_start is not None:
                    return refuse(
                        "the first occurrence is not dated exactly by the event's timing, so the "
                        "series cannot be counted from it")
                return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
            start = first_date
            if rule.start_offset is not None and anchor is not None:
                declared = add_amount(anchor, rule.start_offset)
                if _as_date(declared) != _as_date(start):
                    return refuse(
                        "the recurrence's start offset disagrees with the event's own timing "
                        f"({_as_date(declared).isoformat()} vs {_as_date(start).isoformat()}); "
                        "the first occurrence is not moved to either until a reviewer chooses",
                        event_timing=_as_date(start).isoformat(),
                        start_offset=_as_date(declared).isoformat())

            def value_at(step: int) -> Temporal:
                return add_amount(start, rule.interval, step)

        bound = _resolve_termination(
            rule.termination, context, horizon,
            effective_key=f"recurrence:{event.code}", sample=start)
        if bound.status is not None:
            return [_undated(base, bound)]

        history = run.history if run is not None else _history_by_code(context)
        window = effective_window(event)
        monthly = rule.interval.unit in (TimeUnit.MONTH, TimeUnit.YEAR)
        intended_day = _as_date(start).day
        first_step = 0 if rule.include_start else 1
        output: list[EvaluatedEvent] = []
        index = 0
        last_planned: Temporal | None = None

        while index < bound.max_count:
            source_note: dict[str, object] | None = None
            if basis is RecurrenceBasis.PREVIOUS_ACTUAL_OCCURRENCE:
                if index == 0:
                    current = value_at(first_step)
                else:
                    previous = history.get(event.code, {}).get(index - 1)
                    actual = None
                    if previous is not None:
                        actual = (previous.actual_visit_date
                                  if rule.actual_source == "ACTUAL_VISIT"
                                  else previous.actual_treatment_date)
                    if actual is None:
                        explanation_waiting = dict(explanation)
                        explanation_waiting["reason"] = (
                            f"waiting for the {str(rule.actual_source).lower().replace('_', ' ')} "
                            f"date of occurrence {index - 1} before occurrence {index} can be dated")
                        explanation_waiting["recurrence"] = {
                            "index": index, "basis": basis_note,
                            "waiting_for_actual_occurrence": {
                                "event_code": event.code, "occurrence_index": index - 1,
                                "source": rule.actual_source,
                                "previous_status": previous.status if previous else None},
                        }
                        output.append(EvaluatedEvent(
                            **{**base, "explanation": explanation_waiting},
                            occurrence_index=index, status=PatientEventStatus.WAITING_FOR_ANCHOR))
                        return output
                    current = add_amount(actual, rule.interval)
                    source_note = {"from": rule.actual_source, "occurrence_index": index - 1,
                                   "date": actual.isoformat()}
            elif basis is RecurrenceBasis.PREVIOUS_PLANNED_OCCURRENCE:
                if index == 0:
                    current = value_at(first_step)
                else:
                    assert last_planned is not None
                    current = add_amount(last_planned, rule.interval)
                    source_note = {"from": "PLANNED", "occurrence_index": index - 1,
                                   "date": _as_date(last_planned).isoformat()}
            else:
                current = value_at(first_step + index)
            if not bound.allows(current):
                break
            last_planned = current
            earliest, latest = _window_bounds(current, window)
            timing = ResolvedTiming(
                nominal_start=current, nominal_end=current, earliest=earliest,
                latest=latest, precision="EXACT")
            occurrence_explanation = dict(explanation)
            detail: dict[str, object] = {
                "index": index, "result": timing.model_dump(mode="json"), "basis": basis_note}
            if source_note is not None:
                detail["counted_from"] = source_note
            if monthly and _as_date(current).day < intended_day:
                detail["calendar_policy"] = {
                    "policy": rule.calendar_policy_ref or "MONTH_END_CLAMP.v1",
                    "stated": rule.calendar_policy_ref is not None,
                    "month_end_clamped": True,
                }
            if bound.provisional is not None:
                detail["termination"] = bound.provisional
            occurrence_explanation["recurrence"] = detail
            output.append(EvaluatedEvent(
                **{**base, "explanation": occurrence_explanation},
                occurrence_index=index, status=PatientEventStatus.RESOLVED, timing=timing,
                activities=evaluate_activities(event, context, occurrence_index=index)))
            index += 1
        else:
            # The loop ended on the engine's resource-safety cap, not on a
            # protocol boundary: say so on the last occurrence.
            marker = bound.interrupted_by_cap()
            if output and marker is not None:
                last = dict(output[-1].explanation)
                recurrence = dict(last.get("recurrence") or {})
                recurrence["engine_safety_cap"] = marker
                last["recurrence"] = recurrence
                output[-1] = output[-1].model_copy(update={"explanation": last})
        return output

    def _expand_recurrence_plan(
        self,
        event: Event,
        context: PatientContext,
        horizon: date,
        base: dict[str, object],
    ) -> list[EvaluatedEvent]:
        """Expand a phased follow-up plan into one continuous series (doc E9).

        Each segment is counted from its own start - the plan anchor plus the
        previous segment's end offset - with ``nth_occurrence``, so a monthly
        cadence never drifts onto a clamped month-end and a boundary is
        computed exactly once. The previous segment's ``end_inclusive`` decides
        who owns a visit falling on the boundary: inclusive, it belongs to the
        ending segment and the next one starts one interval later; exclusive,
        the next segment's first visit is the boundary itself. So a boundary
        visit is never duplicated and never dropped.
        """
        plan = event.recurrence_plan
        assert plan is not None
        explanation = base["explanation"]
        if plan.include_start is None or any(
                segment.end_offset is not None and segment.end_inclusive is None
                for segment in plan.segments):
            explanation["reason"] = (
                "the follow-up plan does not state whether a boundary visit belongs "
                "to the segment that ends or the one that starts; confirm before scheduling"
            )
            return [EvaluatedEvent(status=PatientEventStatus.UNRESOLVED, **base)]
        anchor = _resolve_reference(plan.start_reference, context)
        if anchor is None:
            waiting_for_actual = _record_anchor_basis(event, context, explanation)
            if waiting_for_actual:
                explanation["reason"] = _waiting_for_actual_reason(waiting_for_actual)
                explanation["waiting_for_actual_anchor"] = waiting_for_actual
            else:
                explanation["reason"] = "required anchor or event reference is not available"
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
        bound = _resolve_termination(
            plan.termination, context, horizon,
            effective_key=f"recurrence:{event.code}", sample=anchor)
        if bound.status is not None:
            return [_undated(base, bound)]
        window = effective_window(event)
        output: list[EvaluatedEvent] = []
        origin: Temporal = anchor
        origin_offset = plan.start_offset
        first_step = 0 if plan.include_start else 1
        index = 0
        finished = False
        for number, segment in enumerate(plan.segments, start=1):
            end = add_amount(anchor, segment.end_offset) if segment.end_offset is not None else None
            step = first_step
            taken = 0
            while segment.count is None or taken < segment.count:
                value = nth_occurrence(origin, segment.interval, step, origin_offset)
                if end is not None and (not _before_or_equal(value, end) or (
                        not segment.end_inclusive and _before_or_equal(end, value))):
                    break
                if index >= min(bound.max_count, 10000) or not bound.allows(value):
                    finished = True
                    break
                earliest, latest = _window_bounds(value, window)
                timing = ResolvedTiming(
                    nominal_start=value, nominal_end=value, earliest=earliest,
                    latest=latest, precision="EXACT",
                )
                occurrence_explanation = dict(explanation)
                occurrence_explanation["recurrence"] = {
                    "index": index, "segment": number, "segment_occurrence": taken,
                    "cadence": _interval_text(segment.interval),
                    "result": timing.model_dump(mode="json"),
                }
                output.append(EvaluatedEvent(
                    **{**base, "explanation": occurrence_explanation},
                    occurrence_index=index, status=PatientEventStatus.RESOLVED, timing=timing,
                    activities=evaluate_activities(event, context, occurrence_index=index),
                ))
                index += 1
                taken += 1
                step += 1
            if finished:
                break
            if segment.end_offset is not None:
                origin, origin_offset = anchor, segment.end_offset
                first_step = 1 if segment.end_inclusive else 0
            elif segment.count is not None:
                last = step - 1
                spent = TemporalAmount(value=segment.interval.value * last, unit=segment.interval.unit)
                combined = _sum_offsets(origin_offset, spent)
                if combined is not None:
                    origin_offset = combined
                else:
                    origin = nth_occurrence(origin, segment.interval, last, origin_offset)
                    origin_offset = None
                first_step = 1
            else:
                break
        return output

    def _expand_calendar_recurrence(
        self,
        event: Event,
        context: PatientContext,
        horizon: date,
        base: dict[str, object],
        run: "_Run | None",
    ) -> list[EvaluatedEvent]:
        """Expand a weekday series (WEEKDAY_GRID.v1) - see calendar_recurrence.

        Each slot keeps its ordinal as ``occurrence_index`` whatever date it
        lands on. Holds of the event's process are laid back onto the grid
        here, so ``apply_process_states`` only applies stops for it.
        """
        rule = event.calendar_recurrence
        assert rule is not None
        explanation = base["explanation"]
        origin = _resolve_reference(rule.start_reference, context)
        if origin is None:
            waiting_for_actual = _record_anchor_basis(event, context, explanation)
            if waiting_for_actual:
                explanation["reason"] = _waiting_for_actual_reason(waiting_for_actual)
                explanation["waiting_for_actual_anchor"] = waiting_for_actual
            else:
                explanation["reason"] = "required anchor or event reference is not available"
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
        start = add_amount(origin, rule.start_offset) if rule.start_offset is not None else origin
        bound = _resolve_termination(
            rule.termination, context, horizon,
            effective_key=f"recurrence:{event.code}", sample=start)
        if bound.status is not None:
            return [_undated(base, bound)]
        schedule_calendars = run.schedule.holiday_calendars if run is not None else []
        calendar = resolve_calendar(rule, schedule_calendars, context.holiday_calendars)
        holds: list[HoldWindow] = []
        hold_notes: list[dict[str, object]] = []
        if run is not None:
            timeline = run.processes.process_for(event.code)
            if timeline is not None:
                holds, hold_notes = timeline.holds()
        expansion = expand_calendar(
            rule, start, calendar=calendar,
            facts=run.facts.for_event(event.code) if run is not None else {},
            max_count=bound.protocol_count if rule.termination.type == "COUNT" else None,
            allows=lambda value: bound.allows(value),
            horizon=horizon, holds=holds)
        series = dict(expansion.notes)
        if hold_notes:
            series["hold_notes"] = hold_notes
        if expansion.status is not None:
            explanation["reason"] = expansion.reason
            explanation["calendar_recurrence"] = series
            return [EvaluatedEvent(
                status=expansion.status, missing_inputs=expansion.missing, **base)]
        window = effective_window(event)
        output: list[EvaluatedEvent] = []
        for slot in expansion.slots:
            item_explanation = dict(explanation)
            item_explanation["calendar_recurrence"] = {**series, **slot.explanation()}
            if slot.reason:
                item_explanation["reason"] = slot.reason
            timing = None
            if slot.date is not None:
                earliest, latest = _window_bounds(slot.date, window)
                timing = ResolvedTiming(
                    nominal_start=slot.date, nominal_end=slot.date, earliest=earliest,
                    latest=latest, precision="EXACT",
                    constraints=[{"type": "CALENDAR_SLOT", "session": slot.ordinal + 1}])
                item_explanation["result"] = timing.model_dump(mode="json")
            output.append(EvaluatedEvent(
                **{**base, "explanation": item_explanation},
                occurrence_index=slot.ordinal, status=slot.status, timing=timing,
                missing_inputs=list(slot.missing),
                activities=(
                    evaluate_activities(event, context, occurrence_index=slot.ordinal)
                    if timing is not None else []),
            ))
        if not output:
            explanation["reason"] = "the calendar series has no session within its bounds"
            explanation["calendar_recurrence"] = series
            return [EvaluatedEvent(status=PatientEventStatus.NOT_APPLICABLE, **base)]
        return output

    def _expand_cycle_days(
        self,
        event: Event,
        cycle_plan: CyclePlan,
        context: PatientContext,
        horizon: date,
        base: dict[str, object],
    ) -> list[EvaluatedEvent]:
        """Date a Cycle-N-Day-D visit from its cycle's own start (doc F1-F5).

        Every-cycle visits ("Days 1, 8 and 15 of each cycle") get one
        occurrence per cycle, numbered cycle - 1, so an activity condition on
        ``occurrence_number`` means the cycle number (doc F5). A cycle that
        cannot be dated yet appears once, undated, with the reason - and in
        ACTUAL_PREVIOUS_CYCLE_START mode nothing after it is listed, because a
        later cycle has no date until the one before it actually starts.
        """
        timing = event.timing
        assert isinstance(timing, CycleDayTiming)
        explanation = base["explanation"]
        dependency = {
            **base["dependency_result"],
            "mode": cycle_plan.dependency_mode.value, "cycle_plan": cycle_plan.code,
        }
        base = {**base, "dependency_result": dependency}
        starts, bound = cycle_starts(cycle_plan, context, horizon)
        if bound is None:
            code = cycle_plan.start_reference.code
            if code in context.planned_anchors and (
                    cycle_plan.start_reference.accepts == AnchorAcceptance.ACTUAL):
                explanation["reason"] = _waiting_for_actual_reason([code])
                explanation["waiting_for_actual_anchor"] = [code]
            else:
                explanation["reason"] = f"cycle 1 start {code} is not available"
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
        if bound.status is not None:
            return [_undated(base, bound)]
        every_cycle = timing.cycle.type == "CURRENT"
        target = timing.cycle.number or 0
        if every_cycle:
            selected = starts
        else:
            selected = [item for item in starts if item.number == target]
            if not selected:
                pending = starts[-1] if starts and starts[-1].start is None else None
                if pending is not None and pending.number <= target:
                    selected = [pending]
                elif (cycle_plan.termination.type == "COUNT"
                        and target > (cycle_plan.termination.count or 0)):
                    explanation["reason"] = (
                        f"the protocol states {cycle_plan.termination.count} cycles; "
                        f"cycle {target} does not exist"
                    )
                    return [EvaluatedEvent(status=PatientEventStatus.NOT_APPLICABLE, **base)]
        window = effective_window(event)
        output: list[EvaluatedEvent] = []
        for item in selected:
            index = item.number - 1 if every_cycle else 0
            item_explanation = dict(explanation)
            item_explanation["cycle"] = {
                "plan": cycle_plan.code, "number": item.number, "day": timing.day,
                "anchor": cycle_start_anchor_code(cycle_plan.code, item.number),
                "start": item.start.isoformat() if item.start is not None else None,
                "start_basis": item.basis,
                "dependency_mode": cycle_plan.dependency_mode.value,
            }
            if item.start is None:
                item_explanation["reason"] = item.reason
                waiting_key = (
                    "waiting_for_actual" if item.status == PatientEventStatus.BLOCKED
                    else "waiting_for_manual_date"
                )
                item_dependency = (
                    {**dependency, waiting_key: item.waiting_for} if item.waiting_for
                    else dependency
                )
                output.append(EvaluatedEvent(
                    **{**base, "explanation": item_explanation,
                       "dependency_result": item_dependency},
                    occurrence_index=index, status=item.status,
                ))
                continue
            value = add_amount(item.start, TemporalAmount(value=timing.day - 1, unit=TimeUnit.DAY))
            if not bound.allows(value):
                if every_cycle:
                    break
                continue
            earliest, latest = _window_bounds(value, window)
            resolved = ResolvedTiming(
                nominal_start=value, nominal_end=value, earliest=earliest,
                latest=latest, precision="EXACT",
                constraints=[{"type": "CYCLE_DAY", "cycle": item.number, "day": timing.day}],
            )
            item_explanation["result"] = resolved.model_dump(mode="json")
            output.append(EvaluatedEvent(
                **{**base, "explanation": item_explanation},
                occurrence_index=index, status=PatientEventStatus.RESOLVED, timing=resolved,
                activities=evaluate_activities(event, context, occurrence_index=index),
            ))
        return output
