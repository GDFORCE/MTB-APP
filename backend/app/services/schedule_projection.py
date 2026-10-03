"""Reminder and calendar projection from a confirmed patient plan.

Requirement sources: doc 1 sections 24-25, doc 2 sections 37-38, doc 3 sections
29-30, doc 6 sections 14-15 and 40-41, doc 10 sections 21-23.

Three rules drive everything here:

1. Only a RESOLVED, dated occurrence may produce a reminder. Anything waiting on an
   anchor or a condition has no date, so it cannot become due or overdue and cannot
   be reminded about.
2. The event type decides the wording. A telephone follow-up must never tell a
   patient to travel to the hospital.
3. A continuous confinement is ONE calendar entry. While the patient is admitted,
   internal study days must not generate further arrival reminders.

Projection is pure and keyed, so reconciling it against what was previously sent is
idempotent: re-running with unchanged inputs produces no churn, and a moved date
cancels the old entry rather than leaving the patient with two.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.schedule.models import VISIT_MODE_REQUIRES_ATTENDANCE, VISIT_MODE_WORDING
from app.domain.schedule.validator import CORE_EVENT_TYPES

# Doc s13: ONE canonical event-type vocabulary end to end. This module used to
# keep its own (SITE_VISIT/ONSITE/PHONE_CONTACT/REMOTE_VISIT/LAB_ONLY/
# IMAGING_ONLY/PROCEDURE_ONLY/INPATIENT_CONFINEMENT), which diverged from the
# vocabulary the extractor and adapter actually write (CORE_EVENT_TYPES:
# TELEPHONE_CONTACT, IMAGING, LABORATORY, PROCEDURE, INPATIENT_ADMISSION/
# _DISCHARGE, ...). A telephone-contact event therefore never matched
# "PHONE_CONTACT" here and silently fell through to the generic wording below -
# not wrong (the generic fallback never tells a patient to travel), but not
# what the system actually knew about the visit either. Keying on the same
# vocabulary the rest of the pipeline writes is what "one vocabulary" means.

# Event types where the patient is expected to travel to the site.
ATTENDANCE_EVENT_TYPES = {
    "SITE_VISIT", "VISIT", "LABORATORY", "IMAGING", "PROCEDURE",
    "ASSESSMENT", "SAFETY_ASSESSMENT", "TREATMENT", "INPATIENT_ADMISSION",
}

# Statuses that describe something the patient is actually expected to do.
ACTIONABLE_STATUSES = {"RESOLVED"}

PATIENT_WORDING: dict[str, str] = {
    "SITE_VISIT": "Your study visit is scheduled",
    "VISIT": "Your study visit is scheduled",
    "TELEPHONE_CONTACT": "Your study team is expected to contact you",
    "CONTACT": "Your study team is expected to contact you",
    "REMOTE": "Your remote study visit is scheduled",
    "HOME_VISIT": "A study-related home visit is scheduled",
    "LABORATORY": "Your laboratory appointment is scheduled",
    "IMAGING": "Your imaging assessment is scheduled",
    "PROCEDURE": "Your study procedure is scheduled",
    "ASSESSMENT": "Your study assessment is scheduled",
    "SAFETY_ASSESSMENT": "Your safety assessment is scheduled",
    "TREATMENT": "Your treatment visit is scheduled",
    "INPATIENT_ADMISSION": "Your study admission is scheduled",
    "INPATIENT_DISCHARGE": "Your study discharge is scheduled",
}

# A canonical event type this module has no specific wording for must still be
# a KNOWN, reviewed type (CORE_EVENT_TYPES) - not a silent typo. UNSCHEDULED and
# OTHER are deliberately absent from PATIENT_WORDING/ATTENDANCE_EVENT_TYPES:
# an unscheduled visit has no reminder at all (doc s22), and OTHER is the
# presentation-safe "we don't know how" the validator already treats as
# non-blocking - _wording()'s generic fallback covers both correctly.
assert set(PATIENT_WORDING) <= CORE_EVENT_TYPES
assert ATTENDANCE_EVENT_TYPES <= CORE_EVENT_TYPES


class FeasibleWindow(BaseModel):
    """One permitted interval of a constrained occurrence, as inclusive dates.
    ``None`` on a side means unbounded on that side."""

    start: date | None = None
    end: date | None = None


def feasibility_from_explanation(explanation: dict | None) -> tuple[str | None, list[FeasibleWindow]]:
    """Read the feasibility summary the evaluator stores in an occurrence's
    explanation (persisted as ``PatientEvent.generation_reason``) back into
    projection inputs. Returns (None, []) for legacy occurrences."""
    summary = (explanation or {}).get("feasibility")
    if not isinstance(summary, dict):
        return None, []
    windows = []
    for interval in summary.get("intervals") or []:
        lower = (interval.get("lower") or {}).get("value")
        upper = (interval.get("upper") or {}).get("value")
        windows.append(FeasibleWindow(
            start=date.fromisoformat(str(lower)[:10]) if lower else None,
            end=date.fromisoformat(str(upper)[:10]) if upper else None,
        ))
    status = summary.get("status")
    return (str(status) if status else None), windows


class ProjectedEvent(BaseModel):
    """The minimum a projection needs to know about one patient occurrence."""

    logical_key: str
    event_code: str
    # Doc s13/s33: no default. A default here is a default answer to "how does
    # the patient attend", and the one thing this must never do is guess
    # SITE_VISIT - that is precisely "tell a patient to travel based on an
    # ambiguous event type." Every real caller already states this explicitly;
    # an event type the extractor could not determine should be passed
    # through as "OTHER", not silently defaulted here.
    event_type: str
    # Doc 10 s2-s3/s21: a distinct field from event_type - a CT scan the
    # patient travels to is IMAGING *and* CLINIC, not a choice between them.
    # When set to a recognised value it is now authoritative over event_type
    # for wording/attendance (see _wording()); event_type alone was the field
    # a phone-conducted "ASSESSMENT" visit could hide behind with no way to
    # recover the fact that it was actually by telephone.
    visit_mode: str | None = None
    display_name: str
    status: str
    nominal_date: date | None = None
    earliest_date: date | None = None
    latest_date: date | None = None
    is_conditional: bool = False
    is_unscheduled: bool = False
    #: Constraint-set outcome (None for legacy rules). INCOMPLETE/INFEASIBLE
    #: occurrences never produce a reminder or a due/overdue state, whatever
    #: their status says.
    feasibility: str | None = None
    #: Every permitted interval when the set is disjoint; the flat
    #: earliest/latest dates then hold only one of them.
    feasible_windows: list[FeasibleWindow] = Field(default_factory=list)


class ProjectedConfinement(BaseModel):
    episode_code: str
    display_name: str
    status: str
    start_date: date | None = None
    end_date: date | None = None
    member_event_codes: list[str] = Field(default_factory=list)


class ReminderCandidate(BaseModel):
    key: str
    logical_key: str
    event_code: str
    audience: Literal["PATIENT", "SITE"] = "PATIENT"
    message: str
    nominal_date: date
    earliest_date: date | None = None
    latest_date: date | None = None
    policy_state: Literal["UPCOMING", "DUE_WINDOW", "OVERDUE"]
    requires_attendance: bool


class CalendarEntry(BaseModel):
    key: str
    kind: Literal["EVENT", "CONFINEMENT"]
    reference: str
    title: str
    start_date: date
    end_date: date
    all_day: bool = True


class SuppressedItem(BaseModel):
    logical_key: str
    reason: str


class ProjectionResult(BaseModel):
    reminders: list[ReminderCandidate] = Field(default_factory=list)
    calendar: list[CalendarEntry] = Field(default_factory=list)
    suppressed: list[SuppressedItem] = Field(default_factory=list)


class Reconciliation(BaseModel):
    """What must actually be sent, updated, or withdrawn."""

    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    cancelled: list[str] = Field(default_factory=list)


def _policy_state(
    candidate_start: date, candidate_end: date, today: date,
) -> Literal["UPCOMING", "DUE_WINDOW", "OVERDUE"]:
    if today < candidate_start:
        return "UPCOMING"
    return "DUE_WINDOW" if today <= candidate_end else "OVERDUE"


def _windowed_policy_state(
    windows: list[FeasibleWindow], today: date,
) -> Literal["UPCOMING", "DUE_WINDOW", "OVERDUE"]:
    """Due/overdue across disjoint permitted intervals: inside any interval is
    DUE_WINDOW, a gap between intervals is UPCOMING (the next interval is
    still permitted), and only after the last interval closes is it OVERDUE."""
    ordered = sorted(windows, key=lambda item: item.start or date.min)
    for window in ordered:
        if window.start is not None and today < window.start:
            return "UPCOMING"
        if window.end is None or today <= window.end:
            return "DUE_WINDOW"
    return "OVERDUE"


def _wording(event: ProjectedEvent) -> str:
    # visit_mode wins when it names a recognised mode: it is the more specific
    # signal (event_type answers "what kind of occasion", visit_mode answers
    # "how does the patient attend it") and the two are extracted and can be
    # populated independently, so event_type alone is never assumed to also
    # describe the mode.
    mode_lead = VISIT_MODE_WORDING.get((event.visit_mode or "").upper())
    lead = mode_lead or PATIENT_WORDING.get(
        event.event_type.upper(), "Your study activity is scheduled")
    return f"{lead}: {event.display_name}"


def _requires_attendance(event: ProjectedEvent) -> bool:
    mode = (event.visit_mode or "").upper()
    if mode in VISIT_MODE_WORDING:
        return mode in VISIT_MODE_REQUIRES_ATTENDANCE
    return event.event_type.upper() in ATTENDANCE_EVENT_TYPES


def project(
    events: list[ProjectedEvent],
    *,
    today: date,
    confinements: list[ProjectedConfinement] | None = None,
) -> ProjectionResult:
    """Project the confirmed patient plan into reminders and calendar entries."""
    confinements = confinements or []
    result = ProjectionResult()

    # An open confinement absorbs its member events: the patient is already there.
    absorbed: dict[str, ProjectedConfinement] = {}
    for episode in confinements:
        if episode.status in {"CANCELLED", "NOT_APPLICABLE", "WAITING_FOR_ANCHOR"}:
            continue
        for code in episode.member_event_codes:
            absorbed[code] = episode
        if episode.start_date is not None:
            result.calendar.append(CalendarEntry(
                key=f"calendar:confinement:{episode.episode_code}",
                kind="CONFINEMENT", reference=episode.episode_code,
                title=episode.display_name,
                start_date=episode.start_date,
                end_date=episode.end_date or episode.start_date,
            ))

    for event in events:
        if event.is_unscheduled:
            result.suppressed.append(SuppressedItem(
                logical_key=event.logical_key,
                reason="an unscheduled visit is available when clinically "
                       "indicated; it carries no reminder until a site creates one",
            ))
            continue
        if event.feasibility in {"INCOMPLETE", "INFEASIBLE"}:
            result.suppressed.append(SuppressedItem(
                logical_key=event.logical_key,
                reason=(
                    "its restrictions cannot be evaluated yet (an input is missing)"
                    if event.feasibility == "INCOMPLETE"
                    else "its restrictions leave no permitted date"),
            ))
            continue
        if event.status not in ACTIONABLE_STATUSES:
            result.suppressed.append(SuppressedItem(
                logical_key=event.logical_key,
                reason=f"status {event.status} has no confirmed date",
            ))
            continue
        # Doc A7/A8/D2: a window-only rule ("within 14 days before surgery")
        # resolves to a window with no single target day. Its reminder uses the
        # window start, exactly as a RANGE's does (whose nominal date already
        # IS its start). Without this a RESOLVED pre-op window was silently
        # never reminded about. A rule with only a deadline (NO_LATER_THAN) has
        # no start to remind from and stays suppressed.
        reminder_date = event.nominal_date or event.earliest_date
        if reminder_date is None:
            result.suppressed.append(SuppressedItem(
                logical_key=event.logical_key, reason="no calculated date",
            ))
            continue

        episode = absorbed.get(event.event_code)
        start = event.earliest_date or reminder_date
        end = event.latest_date or event.nominal_date or reminder_date
        if episode is not None:
            # The stay itself is the calendar entry and the arrival reminder; an
            # internal study day must not tell an admitted patient to come in.
            result.suppressed.append(SuppressedItem(
                logical_key=event.logical_key,
                reason=f"inside confinement episode {episode.episode_code}",
            ))
            continue

        result.calendar.append(CalendarEntry(
            key=f"calendar:event:{event.logical_key}", kind="EVENT",
            reference=event.logical_key, title=event.display_name,
            start_date=reminder_date,
            end_date=event.nominal_date or event.latest_date or reminder_date,
        ))
        result.reminders.append(ReminderCandidate(
            key=f"reminder:{event.logical_key}:{reminder_date.isoformat()}",
            logical_key=event.logical_key, event_code=event.event_code,
            message=_wording(event), nominal_date=reminder_date,
            earliest_date=event.earliest_date, latest_date=event.latest_date,
            policy_state=(
                _windowed_policy_state(event.feasible_windows, today)
                if len(event.feasible_windows) > 1 else _policy_state(start, end, today)),
            requires_attendance=_requires_attendance(event),
        ))
    return result


def reconcile(existing_keys: list[str], desired: list[ReminderCandidate]) -> Reconciliation:
    """Diff what is already scheduled against what the plan now requires.

    The reminder key contains the date, so a moved visit produces a cancel plus an
    add rather than leaving the patient reminded of both dates (doc 1 section 24).
    """
    existing = set(existing_keys)
    wanted = {item.key for item in desired}
    by_logical: dict[str, list[str]] = {}
    for key in existing:
        by_logical.setdefault(key.rsplit(":", 1)[0], []).append(key)

    output = Reconciliation()
    for item in desired:
        if item.key in existing:
            output.unchanged.append(item.key)
        elif by_logical.get(item.key.rsplit(":", 1)[0]):
            output.updated.append(item.key)
        else:
            output.added.append(item.key)
    output.cancelled = sorted(existing - wanted)
    return output
