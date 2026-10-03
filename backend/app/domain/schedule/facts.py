"""Dated patient facts: occurrence facts and assignment history (WP3).

PT-01/PT-02/PT-07: planned, booked and performed values stay distinct; every
fact carries its effective and recorded time and an optional supersession
link. A correction supersedes by id - the corrected fact remains in history
but no longer acts. Nothing here reads a clock, so a date passing never turns
a plan or a booking into a performed fact.

RL-11/AT-11: assignment facts (arm, sequence, cohort ...) form a dated
history. A late entry with an earlier effective date changes the evaluation of
the affected period only; earlier facts are never rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .feasibility import interval_contains
from .models import (
    AssignmentFact,
    EvaluatedEvent,
    OccurrenceFact,
    OccurrenceFactKind,
)

FACT_SEMANTICS_VERSION = "uctsm-facts.v1"


def _recorded(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def effective_occurrence_facts(facts: list[OccurrenceFact]) -> list[OccurrenceFact]:
    """Facts that still act: not superseded by a later correction."""
    superseded = {item.supersedes for item in facts if item.supersedes}
    return sorted(
        (item for item in facts if item.id not in superseded),
        key=lambda item: (_recorded(item.recorded_at), item.id))


@dataclass
class OccurrenceFacts:
    """The acting facts of one patient, by logical occurrence."""

    outcome: dict[tuple[str, int], OccurrenceFact]
    booking: dict[tuple[str, int], OccurrenceFact]

    @classmethod
    def build(cls, facts: list[OccurrenceFact]) -> "OccurrenceFacts":
        outcome: dict[tuple[str, int], OccurrenceFact] = {}
        booking: dict[tuple[str, int], OccurrenceFact] = {}
        for item in effective_occurrence_facts(facts):
            key = (item.event_code, item.occurrence_index)
            if item.kind == OccurrenceFactKind.BOOKED:
                booking[key] = item
            else:
                # The latest recorded outcome wins; a PERFORMED correction of a
                # MISSED record (or vice versa) is expressed by supersession.
                outcome[key] = item
        return cls(outcome=outcome, booking=booking)

    def for_event(self, event_code: str) -> dict[int, OccurrenceFact]:
        return {index: fact for (code, index), fact in self.outcome.items() if code == event_code}


def annotate_occurrence(item: EvaluatedEvent, facts: OccurrenceFacts) -> EvaluatedEvent:
    """Attach performed and booked values without touching the protocol target.

    PT-04: target (``timing``), permitted set (``feasible_intervals`` or the
    window), booking and performed time are four different values. A booking
    outside the permitted set is FLAGGED; the rule is never adjusted to fit it.
    """
    key = (item.event_code, item.occurrence_index)
    outcome = facts.outcome.get(key)
    booking = facts.booking.get(key)
    if outcome is None and booking is None:
        return item
    update: dict[str, object] = {}
    explanation = dict(item.explanation)
    if outcome is not None and outcome.kind == OccurrenceFactKind.PERFORMED:
        update["actual_time"] = outcome.value
        explanation["performed"] = {
            "value": outcome.value.isoformat() if outcome.value else None,
            "fact": outcome.id, "recorded_at": _recorded(outcome.recorded_at).isoformat(),
            "supersedes": outcome.supersedes,
        }
    if booking is not None:
        booked = booking.value
        update["booked_time"] = booked
        outside = _outside_permitted(item, booked)
        update["booked_outside_permitted"] = outside
        explanation["booking"] = {
            "booked": booked.isoformat() if booked else None,
            "fact": booking.id,
            "within_permitted": None if outside is None else not outside,
            "semantics": FACT_SEMANTICS_VERSION,
        }
        if outside:
            explanation["booking"]["flag"] = "BOOKED_OUTSIDE_PERMITTED"
    update["explanation"] = explanation
    return item.model_copy(update=update)


def _outside_permitted(item: EvaluatedEvent, booked: date | datetime | None) -> bool | None:
    """True/False against the permitted dates; None when there are none yet."""
    if booked is None:
        return None
    if item.feasible_intervals:
        return not any(interval_contains(interval, booked) for interval in item.feasible_intervals)
    timing = item.timing
    if timing is None:
        return None
    value = _as_date(booked)
    lower = timing.earliest if timing.earliest is not None else (
        timing.nominal_start if timing.latest is None else None)
    upper = timing.latest if timing.latest is not None else (
        timing.nominal_end or timing.nominal_start if timing.earliest is None else None)
    if lower is None and upper is None:
        return None
    if lower is not None and value < _as_date(lower):
        return True
    if upper is not None and value > _as_date(upper):
        return True
    return False


# ---------------------------------------------------------------------------
# Assignment history
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssignmentPeriod:
    """One stretch of time with a fixed set of assignment values."""

    start: date | None  # None = from the start of participation
    end: date | None    # exclusive; None = open
    values: dict[str, str | None]
    fact_ids: tuple[str, ...]

    def contains(self, value: date) -> bool:
        if self.start is not None and value < self.start:
            return False
        return self.end is None or value < self.end


def effective_assignment_facts(facts: list[AssignmentFact]) -> list[AssignmentFact]:
    superseded = {item.supersedes for item in facts if item.supersedes}
    return [item for item in facts if item.id not in superseded]


def assignment_periods(facts: list[AssignmentFact]) -> list[AssignmentPeriod]:
    """Split the timeline at every effective date; each period carries the
    value in force for every dimension with history. Same-day facts for one
    dimension resolve by recording order (the later entry is the later
    decision), never by list position."""
    acting = sorted(
        effective_assignment_facts(facts),
        key=lambda item: (item.effective_from or date.min, _recorded(item.recorded_at), item.id))
    if not acting:
        return []
    boundaries = sorted({item.effective_from for item in acting if item.effective_from is not None})
    starts: list[date | None] = [None, *boundaries]
    periods: list[AssignmentPeriod] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else None
        values: dict[str, str | None] = {}
        ids: list[str] = []
        for item in acting:
            begins = item.effective_from or date.min
            if start is not None and begins > start:
                continue
            if start is None and item.effective_from is not None:
                continue
            values[item.dimension] = item.value
            ids.append(item.id)
        periods.append(AssignmentPeriod(start=start, end=end, values=values, fact_ids=tuple(ids)))
    return periods
