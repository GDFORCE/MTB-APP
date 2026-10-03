"""WP3 - weekday (calendar) recurrence (RL-07, RL-08, RL-09, AT-05, AT-19).

Synthetic examples only. 2026-10-02 is a Friday; 2026-10-12 (Monday) is the
holiday in the supplied site calendar.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from _engine_cases import anchor, approved, by_code, context, evaluate, visit

from app.domain.schedule.models import (
    CalendarRecurrence, CountBasis, HolidayCalendar, HolidayPolicy, HoldResumePolicy,
    MissedOccurrencePolicy, OccurrenceFact, OccurrenceFactKind, ProcessDefinition,
    ProcessKind, ProcessState, ProcessTarget, ProcessTransition, ProcessTransitionReason,
    RecurrenceRule, RecurrenceTermination, Weekday,
)
from app.domain.schedule.timing import (
    AnchorReference, OffsetTiming, PositiveTemporalAmount, TemporalAmount, TimeUnit,
)
from app.domain.schedule.validator import ScheduleValidator
from app.domain.schedule import capabilities

FRIDAY = date(2026, 10, 2)
HOLIDAY = date(2026, 10, 12)
MON_FRI = [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI]
SITE = HolidayCalendar(code="SITE_A", version="2026.1", business_weekdays=MON_FRI,
                       dates=[HOLIDAY])
RECORDED = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)


def series(count: int | None = 10, *, basis=None, holiday=HolidayPolicy.NONE_STATED,
           missed=MissedOccurrencePolicy.NONE_STATED, calendar_ref: str | None = "SITE_A",
           weekdays=None, interval_weeks: int = 1, termination=None) -> CalendarRecurrence:
    return CalendarRecurrence(
        start_reference=AnchorReference(code="RT_START"),
        weekdays=weekdays or MON_FRI, interval_weeks=interval_weeks,
        termination=termination or RecurrenceTermination(type="COUNT", count=count),
        count_basis=basis, holiday_policy=holiday, holiday_calendar_ref=calendar_ref,
        missed_policy=missed)


def rt_schedule(rule: CalendarRecurrence, *, calendars=(SITE,), processes=None):
    event = visit("RT", OffsetTiming(reference=AnchorReference(code="RT_START"),
                                     offset=TemporalAmount(value=0, unit=TimeUnit.DAY)),
                  event_type="TREATMENT", calendar_recurrence=rule)
    return approved([event], anchors=[anchor("RT_START")], holiday_calendars=list(calendars),
                    processes=processes)


def missed(index: int, fact_id: str = "m1") -> OccurrenceFact:
    return OccurrenceFact(id=fact_id, event_code="RT", occurrence_index=index,
                          kind=OccurrenceFactKind.MISSED, recorded_at=RECORDED)


def run(schedule, *, facts=(), transitions=(), horizon=date(2027, 12, 31), **extra):
    ctx = context(schedule, anchors={"RT_START": FRIDAY}, occurrence_facts=list(facts),
                  process_transitions=list(transitions), **extra)
    return by_code(evaluate(schedule, ctx, horizon=horizon))["RT"]


def plan(items):
    return [(item.occurrence_index, item.status.value,
             item.timing.nominal_start if item.timing else None) for item in items]


def dates(items):
    return [item.timing.nominal_start for item in items if item.timing is not None]


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


def test_friday_start_mon_to_fri_counts_slots_from_the_start_day():
    items = run(rt_schedule(series(10, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SKIP)))
    assert dates(items) == [d(2), d(5), d(6), d(7), d(8), d(9), d(13), d(14), d(15), d(16)]
    assert [item.occurrence_index for item in items] == list(range(10))
    assert items[6].explanation["calendar_recurrence"]["session"] == 7
    assert items[0].explanation["calendar_recurrence"]["skipped_holidays"] == ["2026-10-12"]
    assert items[0].explanation["calendar_recurrence"]["holiday_calendar"] == {
        "code": "SITE_A", "version": "2026.1"}


def test_unstated_holiday_policy_blocks_only_the_holiday_session():
    items = run(rt_schedule(series(10, basis=CountBasis.SLOTS)))
    statuses = [item.status.value for item in items]
    assert statuses == ["RESOLVED"] * 6 + ["UNRESOLVED"] + ["RESOLVED"] * 3
    holiday_session = items[6]
    assert holiday_session.timing is None
    assert holiday_session.missing_inputs[0].reason == "HOLIDAY_POLICY_REQUIRED"
    assert holiday_session.explanation["calendar_recurrence"]["candidate_date"] == "2026-10-12"
    assert dates(items) == [d(2), d(5), d(6), d(7), d(8), d(9), d(13), d(14), d(15)]


def test_unstated_holiday_policy_with_delivered_basis_adds_a_contingent_session():
    items = run(rt_schedule(series(10, basis=CountBasis.DELIVERED)))
    assert len(items) == 11
    extra = items[10]
    assert extra.status.value == "UNRESOLVED" and extra.timing is None
    assert extra.explanation["calendar_recurrence"]["kind"] == "CONTINGENT"
    assert extra.explanation["calendar_recurrence"]["replaces_session"] == 7


def test_shift_next_business_day_moves_a_weekly_session_and_keeps_its_identity():
    rule = series(4, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SHIFT_NEXT_BUSINESS_DAY,
                  weekdays=[Weekday.MON])
    items = run(rt_schedule(rule))
    assert plan(items) == [
        (0, "RESOLVED", d(5)), (1, "RESOLVED", d(13)), (2, "RESOLVED", d(19)),
        (3, "RESOLVED", d(26))]
    assert items[1].explanation["calendar_recurrence"]["shifted_to"] == "2026-10-13"


def test_shift_onto_an_occupied_day_needs_review_instead_of_doubling_up():
    rule = series(10, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SHIFT_NEXT_BUSINESS_DAY)
    items = run(rt_schedule(rule))
    assert items[6].status.value == "UNRESOLVED"
    assert items[6].missing_inputs[0].reason == "SHIFT_TARGET_OCCUPIED"
    assert items[7].timing.nominal_start == d(13)


@pytest.mark.parametrize("basis,policy,expected", [
    # 30 slots: a missed slot is consumed; no replacement stated -> none.
    (CountBasis.SLOTS, MissedOccurrencePolicy.NO_REPLACEMENT, []),
    # Slots with an explicit replacement policy: appended, linked to session 3.
    (CountBasis.SLOTS, MissedOccurrencePolicy.APPEND_REPLACEMENT, [(10, "RESOLVED", d(19))]),
    # Delivered fractions: the count basis itself requires another session.
    (CountBasis.DELIVERED, MissedOccurrencePolicy.NONE_STATED, [(10, "RESOLVED", d(19))]),
    # Unstated basis: only the extra session waits for review.
    (None, MissedOccurrencePolicy.NONE_STATED, [(10, "UNRESOLVED", None)]),
    (CountBasis.SLOTS, MissedOccurrencePolicy.NONE_STATED, [(10, "UNRESOLVED", None)]),
])
def test_rl08_slots_and_delivered_fractions_differ_only_after_a_miss(basis, policy, expected):
    rule = series(10, basis=basis, holiday=HolidayPolicy.SKIP, missed=policy)
    before = run(rt_schedule(rule))
    after = run(rt_schedule(rule), facts=[missed(2)])
    # Before any miss the two bases are the same ten dates.
    assert dates(before) == [d(2), d(5), d(6), d(7), d(8), d(9), d(13), d(14), d(15), d(16)]
    # The missed session keeps its number and date; nothing else moves.
    assert plan(after)[:10] == [
        (index, "MISSED" if index == 2 else "RESOLVED", value)
        for index, value in enumerate(dates(before))]
    assert plan(after)[10:] == expected
    if expected:
        assert after[10].explanation["calendar_recurrence"]["replaces_session"] == 3


def test_at05_friday_start_missed_first_session_and_holiday_with_delivered_basis():
    rule = series(10, basis=CountBasis.DELIVERED, holiday=HolidayPolicy.SKIP)
    items = run(rt_schedule(rule), facts=[missed(0)])
    assert plan(items)[0] == (0, "MISSED", d(2))
    assert dates(items) == [d(2), d(5), d(6), d(7), d(8), d(9), d(13), d(14), d(15), d(16), d(19)]
    delivered = [item for item in items if item.status.value == "RESOLVED"]
    assert len(delivered) == 10
    assert [item.explanation["calendar_recurrence"]["delivery_ordinal"] for item in delivered] == list(range(1, 11))


def test_performed_correction_of_a_miss_removes_the_replacement():
    rule = series(10, basis=CountBasis.DELIVERED, holiday=HolidayPolicy.SKIP)
    corrected = OccurrenceFact(id="p1", event_code="RT", occurrence_index=2,
                               kind=OccurrenceFactKind.PERFORMED, value=d(6),
                               recorded_at=RECORDED.replace(hour=10), supersedes="m1")
    items = run(rt_schedule(rule), facts=[missed(2), corrected])
    assert len(items) == 10
    assert items[2].status.value == "RESOLVED" and items[2].actual_time == d(6)
    assert items[2].explanation["performed"]["supersedes"] == "m1"


def test_missing_holiday_calendar_leaves_the_series_undated():
    items = run(rt_schedule(series(10, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SKIP),
                            calendars=()))
    assert len(items) == 1 and items[0].status.value == "UNRESOLVED"
    assert items[0].missing_inputs[0].reason == "HOLIDAY_CALENDAR_MISSING"


def test_site_calendar_supplied_with_the_patient_is_used():
    schedule = rt_schedule(series(10, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SKIP),
                           calendars=())
    items = run(schedule, holiday_calendars=[SITE])
    assert d(12) not in dates(items) and len(items) == 10


def test_every_other_tuesday_counts_weeks_from_the_start_week():
    rule = series(3, basis=CountBasis.SLOTS, weekdays=[Weekday.TUE], interval_weeks=2,
                  calendar_ref=None)
    assert dates(run(rt_schedule(rule))) == [d(13), d(27), d(10, 11)]


def test_horizon_extension_appends_with_stable_identities():
    rule = series(termination=RecurrenceTermination(type="HORIZON"), calendar_ref="SITE_A",
                  holiday=HolidayPolicy.SKIP, weekdays=[Weekday.MON, Weekday.THU])
    short = run(rt_schedule(rule), horizon=d(31))
    long = run(rt_schedule(rule), horizon=d(30, 11))
    assert plan(long)[:len(short)] == plan(short)
    assert len(long) > len(short)


def test_rl09_validator_blocks_contradictions_and_warns_on_unstated_policies():
    def codes(rule, calendars=(SITE,)):
        issues = ScheduleValidator().validate(rt_schedule(rule, calendars=calendars))
        return {item.issue_code: item.blocking for item in issues}

    conflict = codes(series(10, basis=CountBasis.DELIVERED,
                            missed=MissedOccurrencePolicy.NO_REPLACEMENT, holiday=HolidayPolicy.SKIP))
    assert conflict["CALENDAR_COUNT_BASIS_CONFLICT"] is True
    no_calendar = codes(series(10, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SKIP,
                               calendar_ref=None))
    assert no_calendar["CALENDAR_HOLIDAY_CALENDAR_REQUIRED"] is True
    unstated = codes(series(10))
    assert unstated == {"CALENDAR_HOLIDAY_POLICY_UNSTATED": False,
                        "CALENDAR_COUNT_BASIS_UNSTATED": False,
                        "CALENDAR_MISSED_POLICY_UNSTATED": False}
    site_only = codes(series(10, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SKIP,
                             missed=MissedOccurrencePolicy.NO_REPLACEMENT), calendars=())
    assert site_only == {"CALENDAR_HOLIDAY_CALENDAR_NOT_IN_SCHEDULE": False}


def test_calendar_operators_are_declared_and_reported():
    for family, operator in (
            ("RECURRENCE", "CALENDAR"), ("CALENDAR_POLICY", "WEEKDAY_GRID.v1"),
            ("HOLIDAY_POLICY", "SHIFT_NEXT_BUSINESS_DAY"), ("COUNT_BASIS", "DELIVERED"),
            ("MISSED_OCCURRENCE_POLICY", "APPEND_REPLACEMENT")):
        assert capabilities.is_executable(family, operator)
        assert capabilities.lookup(family, operator).version == capabilities.WP3_VERSION
    report = capabilities.capability_report(rt_schedule(series(10, basis=CountBasis.SLOTS)))
    assert report["fully_executable"]
    assert report["operators_used"]["RECURRENCE:CALENDAR"] == 1


# --- holds of the owning process laid back onto the grid (AT-06 for weekdays) ---

def rt_process(policy: HoldResumePolicy) -> ProcessDefinition:
    return ProcessDefinition(code="RT", display_name="Radiotherapy",
                             kind=ProcessKind.INTERVENTION, event_codes=["RT"],
                             hold_resume_policy=policy)


def transition(state: ProcessState, when: date, tid: str, **extra) -> ProcessTransition:
    return ProcessTransition(
        id=tid, target=ProcessTarget(type="PROCESS", process_code="RT"), to_state=state,
        effective_at=when, recorded_at=RECORDED, status="CONFIRMED",
        reason=ProcessTransitionReason.DECISION, **extra)


HOLD = [transition(ProcessState.HELD, d(3), "h"), transition(ProcessState.ACTIVE, d(7), "r")]


@pytest.mark.parametrize("policy,expected", [
    (HoldResumePolicy.NOMINAL, [(1, "PAUSED", d(5)), (2, "PAUSED", d(6)), (3, "RESOLVED", d(7))]),
    # Moved by the 4-day hold: session 2 was due Mon 5 -> searched from Fri 9.
    (HoldResumePolicy.SHIFT_BY_HOLD, [(1, "RESOLVED", d(9)), (2, "RESOLVED", d(13)), (3, "RESOLVED", d(14))]),
    # Restarted from the actual resume date, Wed 7.
    (HoldResumePolicy.RESTART_FROM_RESUME, [(1, "RESOLVED", d(7)), (2, "RESOLVED", d(8)), (3, "RESOLVED", d(9))]),
    (HoldResumePolicy.WAIT_FOR_DECISION, [(1, "UNRESOLVED", None), (2, "UNRESOLVED", None), (3, "UNRESOLVED", None)]),
])
def test_hold_policies_give_distinct_session_dates(policy, expected):
    schedule = rt_schedule(series(6, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SKIP),
                           processes=[rt_process(policy)])
    items = run(schedule, transitions=HOLD)
    assert plan(items)[0] == (0, "RESOLVED", d(2))
    assert plan(items)[1:4] == expected


def test_a_resume_decision_supplies_the_policy_the_protocol_left_open():
    schedule = rt_schedule(series(6, basis=CountBasis.SLOTS, holiday=HolidayPolicy.SKIP),
                           processes=[rt_process(HoldResumePolicy.WAIT_FOR_DECISION)])
    decided = [HOLD[0], transition(ProcessState.ACTIVE, d(7), "r",
                                   resume_policy=HoldResumePolicy.RESTART_FROM_RESUME)]
    assert dates(run(schedule, transitions=decided))[1:3] == [d(7), d(8)]


def test_open_hold_pauses_remaining_sessions_and_nominal_hold_extends_delivered_series():
    schedule = rt_schedule(series(6, basis=CountBasis.DELIVERED, holiday=HolidayPolicy.SKIP),
                           processes=[rt_process(HoldResumePolicy.NOMINAL)])
    held = run(schedule, transitions=HOLD[:1])
    assert [item.status.value for item in held] == ["RESOLVED"] + ["PAUSED"] * 5
    resumed = run(schedule, transitions=HOLD)
    # Two sessions lost to the nominal-grid hold are delivered at the end.
    assert [item.status.value for item in resumed].count("RESOLVED") == 6
    assert [item.explanation["calendar_recurrence"]["kind"] for item in resumed][-2:] == [
        "REPLACEMENT", "REPLACEMENT"]


# --- AT-19: month-end semantics stay visible -------------------------------

def monthly(policy: str | None):
    event = visit("FU", OffsetTiming(reference=AnchorReference(code="START"),
                                     offset=TemporalAmount(value=0, unit=TimeUnit.DAY)),
                  recurrence=RecurrenceRule(
                      interval=PositiveTemporalAmount(value=1, unit=TimeUnit.MONTH),
                      start_reference=AnchorReference(code="START"), include_start=False,
                      termination=RecurrenceTermination(type="COUNT", count=3),
                      calendar_policy_ref=policy))
    schedule = approved([event], anchors=[anchor("START")])
    return schedule, context(schedule, anchors={"START": date(2027, 1, 31)})


def test_at19_month_end_clamp_is_marked_stated_or_not():
    schedule, ctx = monthly(None)
    items = by_code(evaluate(schedule, ctx))["FU"]
    assert [item.timing.nominal_start for item in items] == [
        date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)]
    assert items[0].explanation["recurrence"]["calendar_policy"] == {
        "policy": "MONTH_END_CLAMP.v1", "stated": False, "month_end_clamped": True}
    assert "calendar_policy" not in items[1].explanation["recurrence"]
    schedule, ctx = monthly("MONTH_END_CLAMP.v1")
    items = by_code(evaluate(schedule, ctx))["FU"]
    assert items[0].explanation["recurrence"]["calendar_policy"]["stated"] is True


def test_at19_unknown_calendar_policy_is_an_unsupported_operator():
    schedule, _ = monthly("MONTH_END_ROLL_FORWARD.v9")
    issues = [item for item in ScheduleValidator().validate(schedule)
              if item.issue_code == "UNSUPPORTED_OPERATOR"]
    assert issues and issues[0].details["family"] == "CALENDAR_POLICY"
