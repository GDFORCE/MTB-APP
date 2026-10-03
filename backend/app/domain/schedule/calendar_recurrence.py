"""Weekday (calendar) recurrence - WEEKDAY_GRID.v1 (RL-07, RL-08, RL-09, AT-05).

"Radiotherapy Monday to Friday, 30 fractions" is a series of SLOTS on named
weekdays. This module expands one ``CalendarRecurrence`` for one patient into
slots with stable identities and applies only the policies the protocol
stated:

* the grid: weekdays in weeks counted from the week (Mon-Sun) containing the
  start; ``interval_weeks`` keeps every n-th such week;
* holidays, from a calendar supplied as data (``HolidayPolicy``);
* missed sessions and the count basis (``MissedOccurrencePolicy`` /
  ``CountBasis``) - a missed slot keeps its identity, a replacement is a new
  ordinal linked to the slot it replaces;
* holds of the owning process (``HoldResumePolicy``), laid back onto the grid.

Whenever a material policy is unstated, only the slots whose existence or date
depends on it are UNRESOLVED; everything else stays dated (RL-09). Nothing
here reads a clock: a slot date passing never makes a session performed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .models import (
    WEEKDAY_INDEX,
    CalendarRecurrence,
    CountBasis,
    HolidayCalendar,
    HolidayPolicy,
    HoldResumePolicy,
    MissedOccurrencePolicy,
    MissingInput,
    OccurrenceFact,
    OccurrenceFactKind,
    PatientEventStatus,
)

#: Hard bound on materialized slots for one series (a resource bound, never a
#: protocol maximum - RL-14).
MAX_SLOTS = 1000


@dataclass(frozen=True)
class HoldWindow:
    """One hold of the process owning the series, in date terms.

    ``end`` is the actual resume date (None while still held). ``policy`` is the
    effective resume policy: the process's stated one, or the one a resume
    decision chose.
    """

    start: date
    end: date | None
    policy: HoldResumePolicy
    process_code: str
    transition_ids: tuple[str, ...] = ()


@dataclass
class CalendarSlot:
    ordinal: int
    date: date | None
    status: PatientEventStatus
    #: SLOT (one of the planned series), REPLACEMENT (appended because a slot
    #: was missed or held and the stated policy/basis says so) or CONTINGENT
    #: (would exist only under a policy the protocol did not state).
    kind: str = "SLOT"
    replaces: int | None = None
    reason: str | None = None
    candidate_date: date | None = None
    missing: list[MissingInput] = field(default_factory=list)
    notes: dict[str, object] = field(default_factory=dict)

    def explanation(self) -> dict[str, object]:
        output: dict[str, object] = {
            "session": self.ordinal + 1,
            "kind": self.kind,
            "slot_date": self.date.isoformat() if self.date else None,
        }
        if self.replaces is not None:
            output["replaces_session"] = self.replaces + 1
        if self.candidate_date is not None:
            output["candidate_date"] = self.candidate_date.isoformat()
        output.update(self.notes)
        return output


@dataclass
class CalendarExpansion:
    slots: list[CalendarSlot]
    #: Series-level outcome when no slot can be produced at all.
    status: PatientEventStatus | None = None
    reason: str | None = None
    missing: list[MissingInput] = field(default_factory=list)
    notes: dict[str, object] = field(default_factory=dict)
    #: True when the protocol has more to give than was materialized.
    continues: bool = False


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def resolve_calendar(
    rule: CalendarRecurrence,
    schedule_calendars: list[HolidayCalendar],
    site_calendars: list[HolidayCalendar],
) -> HolidayCalendar | None:
    """The calendar a rule names; a site calendar overrides a schedule one."""
    if rule.holiday_calendar_ref is None:
        return None
    by_code = {item.code: item for item in schedule_calendars}
    by_code.update({item.code: item for item in site_calendars})
    return by_code.get(rule.holiday_calendar_ref)


class _Grid:
    """The weekday grid, re-anchorable after a hold moves the series."""

    def __init__(self, rule: CalendarRecurrence, origin: date) -> None:
        self.weekdays = {WEEKDAY_INDEX[item] for item in rule.weekdays}
        self.interval = rule.interval_weeks
        self.anchor(origin)

    def anchor(self, origin: date) -> None:
        self.monday = origin - timedelta(days=origin.weekday())

    def eligible(self, value: date) -> bool:
        if value.weekday() not in self.weekdays:
            return False
        return ((value - self.monday).days // 7) % self.interval == 0

    def next_on_or_after(self, value: date) -> date:
        current = value
        for _ in range(7 * self.interval + 7):
            if self.eligible(current):
                return current
            current += timedelta(days=1)
        raise AssertionError("a weekday grid always has an eligible day")  # pragma: no cover


def expand_calendar(
    rule: CalendarRecurrence,
    start: date | datetime,
    *,
    calendar: HolidayCalendar | None,
    facts: dict[int, OccurrenceFact],
    max_count: int | None,
    allows: Callable[[date], bool],
    horizon: date,
    holds: list[HoldWindow] | None = None,
) -> CalendarExpansion:
    """Expand one weekday series into slots (see module docstring)."""
    notes: dict[str, object] = {
        "calendar_policy": rule.calendar_policy_ref,
        "weekdays": [item.value for item in rule.weekdays],
        "interval_weeks": rule.interval_weeks,
        "holiday_policy": rule.holiday_policy.value,
        "missed_policy": rule.missed_policy.value,
        "count_basis": rule.count_basis.value if rule.count_basis else None,
    }
    if rule.holiday_calendar_ref is not None and calendar is None:
        return CalendarExpansion(
            slots=[], status=PatientEventStatus.UNRESOLVED,
            reason=(
                f"holiday calendar {rule.holiday_calendar_ref!r} was not supplied for this "
                "schedule or site; every session date depends on it"),
            missing=[MissingInput(
                kind="CALENDAR", code=rule.holiday_calendar_ref,
                reason="HOLIDAY_CALENDAR_MISSING")],
            notes=notes)
    if calendar is not None:
        notes["holiday_calendar"] = {"code": calendar.code, "version": calendar.version}
    else:
        notes["holiday_calendar"] = None
    holidays = set(calendar.dates) if calendar is not None else set()
    business = (
        {WEEKDAY_INDEX[item] for item in calendar.business_weekdays}
        if calendar is not None else set())
    holds = sorted(holds or [], key=lambda item: item.start)
    grid = _Grid(rule, _as_date(start))
    cursor = _as_date(start)
    slots: list[CalendarSlot] = []
    #: Deficits to cover after the planned series: (ordinal, cause).
    deficits: list[tuple[int, str]] = []
    covered = 0
    consumed_holds: set[int] = set()
    blocked_by_decision: HoldWindow | None = None
    planned = max_count if max_count is not None else None
    continues = False

    def next_business_after(value: date) -> date:
        current = value + timedelta(days=1)
        for _ in range(366):
            if current.weekday() in business and current not in holidays:
                return current
            current += timedelta(days=1)
        raise ValueError("holiday calendar leaves no business day within a year")

    def place() -> tuple[date | None, CalendarSlot | None]:
        """Find the next slot date from the cursor, applying the holiday policy.
        Returns (date, None) or (None, an UNRESOLVED slot template)."""
        nonlocal cursor
        for _ in range(MAX_SLOTS):
            candidate = grid.next_on_or_after(cursor)
            if candidate not in holidays:
                cursor = candidate + timedelta(days=1)
                return candidate, None
            if rule.holiday_policy == HolidayPolicy.SKIP:
                skipped = notes.setdefault("skipped_holidays", [])
                assert isinstance(skipped, list)
                skipped.append(candidate.isoformat())
                cursor = candidate + timedelta(days=1)
                continue
            cursor = candidate + timedelta(days=1)
            if rule.holiday_policy == HolidayPolicy.SHIFT_NEXT_BUSINESS_DAY:
                target = next_business_after(candidate)
                if grid.eligible(target) and target not in holidays:
                    return None, CalendarSlot(
                        ordinal=-1, date=None, status=PatientEventStatus.UNRESOLVED,
                        candidate_date=candidate,
                        reason=(
                            f"{candidate.isoformat()} is a holiday and the next business day "
                            f"{target.isoformat()} already holds a session of this series; "
                            "the move needs review"),
                        missing=[MissingInput(kind="CALENDAR_POLICY", code=calendar.code if calendar else None,
                                              reason="SHIFT_TARGET_OCCUPIED")],
                        notes={"holiday": candidate.isoformat(), "shift_target": target.isoformat()})
                return target, CalendarSlot(
                    ordinal=-1, date=target, status=PatientEventStatus.RESOLVED,
                    candidate_date=candidate,
                    notes={"holiday": candidate.isoformat(), "shifted_to": target.isoformat()})
            return None, CalendarSlot(
                ordinal=-1, date=None, status=PatientEventStatus.UNRESOLVED,
                candidate_date=candidate,
                reason=(
                    f"session falls on the holiday {candidate.isoformat()} and the protocol "
                    "does not state whether it is skipped, moved or given; confirm before "
                    "scheduling this session"),
                missing=[MissingInput(kind="CALENDAR_POLICY", code=calendar.code if calendar else None,
                                      reason="HOLIDAY_POLICY_REQUIRED")],
                notes={"holiday": candidate.isoformat()})
        raise ValueError("calendar leaves no eligible session day")  # pragma: no cover

    def target_total() -> int | None:
        if planned is None:
            return None
        return planned + covered

    ordinal = 0
    pending_extra: tuple[int, str, str] | None = None
    while ordinal < MAX_SLOTS:
        total = target_total()
        if pending_extra is None and total is not None and ordinal >= total:
            if not deficits or blocked_by_decision is not None:
                break
            cause_ordinal, cause = deficits.pop(0)
            covered += 1
            extra_kind, extra_status = cause.split(":", 1)
            pending_extra = (cause_ordinal, extra_kind, extra_status)
        if blocked_by_decision is not None:
            # Every later session's date depends on the resume decision.
            hold = blocked_by_decision
            slots.append(CalendarSlot(
                ordinal=ordinal, date=None, status=PatientEventStatus.UNRESOLVED,
                reason=(
                    f"process {hold.process_code} resumed but its resume policy is "
                    f"{hold.policy.value.lower().replace('_', ' ')}; this session waits for "
                    "a decision on how the series continues"),
                missing=[MissingInput(
                    kind="PROCESS", code=hold.process_code, reason="RESUME_POLICY_REQUIRED")]))
            pending_extra = None
            ordinal += 1
            if total is None:
                continues = True
                break
            continue
        value, template = place()
        if value is not None and value > horizon:
            continues = True
            break
        if value is not None and not allows(value):
            break
        # Holds of the owning process.
        restart = False
        held_open = False
        held_nominal = False
        if value is not None:
            for index, hold in enumerate(holds):
                if index in consumed_holds or value < hold.start:
                    continue
                if hold.end is None:
                    held_open = True
                    break
                if value >= hold.end:
                    continue
                if hold.policy == HoldResumePolicy.NOMINAL:
                    held_nominal = True
                    break
                if hold.policy in {HoldResumePolicy.SHIFT_BY_HOLD, HoldResumePolicy.RESTART_FROM_RESUME}:
                    consumed_holds.add(index)
                    new_cursor = (
                        value + (hold.end - hold.start)
                        if hold.policy == HoldResumePolicy.SHIFT_BY_HOLD else hold.end)
                    cursor = new_cursor
                    grid.anchor(new_cursor)
                    moved = notes.setdefault("hold_moves", [])
                    assert isinstance(moved, list)
                    moved.append({
                        "process": hold.process_code, "policy": hold.policy.value,
                        "session": ordinal + 1, "from": value.isoformat(),
                        "search_from": new_cursor.isoformat()})
                    restart = True
                    break
                blocked_by_decision = hold
                break
        if restart or blocked_by_decision is not None:
            continue  # the same ordinal is placed again / marked undecided above
        if template is not None:
            slot = CalendarSlot(
                ordinal=ordinal, date=template.date, status=template.status,
                reason=template.reason, candidate_date=template.candidate_date,
                missing=list(template.missing), notes=dict(template.notes))
        else:
            slot = CalendarSlot(ordinal=ordinal, date=value, status=PatientEventStatus.RESOLVED)
        if pending_extra is not None:
            cause_ordinal, extra_kind, extra_status = pending_extra
            slot.kind = extra_kind
            slot.replaces = cause_ordinal
            if extra_status == "UNRESOLVED" and slot.status == PatientEventStatus.RESOLVED:
                slot.status = PatientEventStatus.UNRESOLVED
                slot.candidate_date = slot.date
                slot.date = None
                slot.reason = _contingent_reason(rule, cause_ordinal)
                slot.missing.append(MissingInput(
                    kind="CALENDAR_POLICY", code=None,
                    reason=("COUNT_BASIS_REQUIRED" if rule.count_basis is None
                            else "MISSED_POLICY_REQUIRED")))
            pending_extra = None
        fact = facts.get(ordinal)
        performed = fact is not None and fact.kind == OccurrenceFactKind.PERFORMED
        if slot.status == PatientEventStatus.RESOLVED and not performed and (held_open or held_nominal):
            slot.status = PatientEventStatus.PAUSED
            slot.reason = (
                "the series is held; this session is not due until treatment resumes"
                if held_open else
                "held: the protocol keeps the nominal grid, so this session is not made up "
                "by moving it")
        if performed and slot.status != PatientEventStatus.RESOLVED:
            # It happened: a performed fact outranks any undecided policy.
            slot.date = slot.date or slot.candidate_date or _as_date(fact.value)
            slot.status = PatientEventStatus.RESOLVED
            slot.reason = None
            slot.missing = []
        if fact is not None and fact.kind == OccurrenceFactKind.MISSED:
            slot.status = PatientEventStatus.MISSED
            slot.reason = "recorded as missed; the session keeps its number"
        slots.append(slot)
        if planned is not None:
            cause = _deficit(rule, slot, held_nominal, fact)
            if cause is not None:
                deficits.append((ordinal, cause))
        ordinal += 1
    else:
        continues = True
    fractions = 0
    for slot in slots:
        if slot.status == PatientEventStatus.RESOLVED and slot.date is not None:
            fractions += 1
            slot.notes["delivery_ordinal"] = fractions
    return CalendarExpansion(slots=slots, notes=notes, continues=continues)


def _deficit(
    rule: CalendarRecurrence,
    slot: CalendarSlot,
    held: bool,
    fact: OccurrenceFact | None,
) -> str | None:
    """What (if anything) must be appended because this slot is not delivered.

    Returns ``"<KIND>:<STATUS>"`` - REPLACEMENT:RESOLVED when the stated policy
    or basis requires another session, CONTINGENT:UNRESOLVED when that depends
    on a policy the protocol did not state - or None.
    """
    performed = fact is not None and fact.kind == OccurrenceFactKind.PERFORMED
    if performed:
        return None
    basis = rule.count_basis
    missed = slot.status == PatientEventStatus.MISSED
    holiday_unresolved = (
        slot.status == PatientEventStatus.UNRESOLVED and slot.kind == "SLOT"
        and slot.candidate_date is not None and not missed)
    if missed:
        if basis == CountBasis.DELIVERED:
            return "REPLACEMENT:RESOLVED"
        if rule.missed_policy == MissedOccurrencePolicy.APPEND_REPLACEMENT:
            return "REPLACEMENT:RESOLVED"
        if basis == CountBasis.SLOTS and rule.missed_policy == MissedOccurrencePolicy.NO_REPLACEMENT:
            return None
        return "CONTINGENT:UNRESOLVED"
    if held and slot.status == PatientEventStatus.PAUSED:
        if basis == CountBasis.DELIVERED:
            return "REPLACEMENT:RESOLVED"
        if basis == CountBasis.SLOTS:
            return None
        return "CONTINGENT:UNRESOLVED"
    if holiday_unresolved:
        if basis == CountBasis.SLOTS:
            return None
        return "CONTINGENT:UNRESOLVED"
    return None


def _contingent_reason(rule: CalendarRecurrence, cause_ordinal: int) -> str:
    if rule.count_basis is None:
        return (
            f"session {cause_ordinal + 1} was not delivered and the protocol does not state "
            "whether the count is of slots or of delivered sessions; whether this extra "
            "session exists needs review")
    return (
        f"session {cause_ordinal + 1} was not delivered and the protocol does not state "
        "whether a missed session is replaced; whether this extra session exists needs review")
