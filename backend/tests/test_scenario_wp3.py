"""WP3 acceptance probes end to end (AT-05..AT-11, AT-19, PT-04).

Every scenario goes through the harness path a real trial uses: persist a
draft -> approve -> enrol -> record facts / decisions (impact preview ->
confirm where the workflow has one) -> re-evaluate -> read the PERSISTED plan,
the reminder projection and the deviation report. Synthetic protocols only.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from _engine_cases import anchor, approved, visit
from _scenario_harness import check_invariants, new_trial, reminder_keys

from app.db import models as db
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.models import (
    ApplicabilityRule, CalendarRecurrence, ConditionalAction, ConditionalDefinition,
    ConditionState, CountBasis, CycleDependencyMode, CyclePlan, DependencyMode, HolidayCalendar,
    HolidayPolicy, HoldResumePolicy, MissedOccurrencePolicy, OccurrenceFactKind,
    ProcessDefinition, ProcessKind, ProcessState, ProcessTarget, ProcessTransition,
    ProcessTransitionReason, RecurrencePlan, RecurrenceRule, RecurrenceSegment,
    RecurrenceTermination, RepeatBlock, StudyDimension, Weekday,
)
from app.domain.schedule.timing import (
    AnchorAcceptance, AnchorReference, CycleDayTiming, CycleSelector, OffsetTiming,
    PositiveTemporalAmount, TemporalAmount, TimeUnit, Window, NonNegativeTemporalAmount,
)
from app.services.schedule_service import PatientScheduleService

MON_FRI = [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI]


def d(day: int, month: int = 10, year: int = 2026) -> date:
    return date(year, month, day)


def at(code: str, days: int = 0, accepts=AnchorAcceptance.ACTUAL) -> OffsetTiming:
    return OffsetTiming(reference=AnchorReference(code=code, accepts=accepts),
                        offset=TemporalAmount(value=days, unit=TimeUnit.DAY))


@pytest.fixture()
def cleanup():
    opened = []
    yield opened
    for trial, engine in opened:
        trial.session.close()
        engine.dispose()


def open_trial(cleanup, schedule):
    trial, engine = new_trial()
    cleanup.append((trial, engine))
    trial.publish(schedule)
    return trial


def fact(trial, patient, key, kind, value=None, **extra):
    row = PatientScheduleService(trial.session).record_occurrence_fact(
        patient.id, organization_id=trial.organization_id, logical_key=key, kind=kind,
        value=value, actor_id=trial.actor_id, **extra)
    trial.session.commit()
    return row


def transition(target: str, state: ProcessState, when: date, reason=ProcessTransitionReason.DECISION,
               **extra) -> ProcessTransition:
    return ProcessTransition(target=ProcessTarget(type="PROCESS", process_code=target),
                             to_state=state, effective_at=when, status="PENDING_CONFIRMATION",
                             reason=reason, authority="PI", **extra)


# --- AT-05: weekday series, Friday start, missed fraction, holiday -------------

def radiotherapy():
    rt = visit("RT", at("RT_START"), event_type="TREATMENT", calendar_recurrence=CalendarRecurrence(
        start_reference=AnchorReference(code="RT_START"), weekdays=MON_FRI,
        termination=RecurrenceTermination(type="COUNT", count=10),
        count_basis=CountBasis.DELIVERED, holiday_policy=HolidayPolicy.SKIP,
        holiday_calendar_ref="SITE_A", missed_policy=MissedOccurrencePolicy.NONE_STATED))
    calendar = HolidayCalendar(code="SITE_A", version="2026.1", business_weekdays=MON_FRI,
                               dates=[d(12)])
    return approved([rt], anchors=[anchor("RT_START")], holiday_calendars=[calendar])


def test_at05_friday_start_missed_fraction_and_holiday(cleanup):
    trial = open_trial(cleanup, radiotherapy())
    stored = trial.session.get(db.ScheduleVersion, trial.current_version_id())
    assert stored.metadata_json["_uctsm_holiday_calendars"][0]["code"] == "SITE_A"
    patient = trial.enrol("P-RT", anchors={"RT_START": d(2)})
    rows = trial.rows(patient)
    assert [rows[f"RT:{index}"].nominal_start_date for index in range(10)] == [
        d(2), d(5), d(6), d(7), d(8), d(9), d(13), d(14), d(15), d(16)]
    identity = {key: row.logical_occurrence_id for key, row in rows.items()}
    fact(trial, patient, "RT:0", OccurrenceFactKind.PERFORMED, d(2))
    fact(trial, patient, "RT:1", OccurrenceFactKind.MISSED)
    history = check_invariants(trial, patient, today=d(6))
    rows = {key: row for key, row in trial.rows(patient).items()}
    trial.evaluate(patient)
    rows = trial.rows(patient)
    assert rows["RT:1"].status == "MISSED"
    assert rows["RT:10"].nominal_start_date == d(19)
    assert rows["RT:10"].generation_reason["calendar_recurrence"]["replaces_session"] == 2
    # Every earlier session kept its identity and date.
    for key in (f"RT:{index}" for index in range(10)):
        assert rows[key].logical_occurrence_id == identity[key]
    check_invariants(trial, patient, today=d(6), completed_before=history)
    # A missed session is never reminded about, however long ago it was due.
    assert "RT:1" not in reminder_keys(trial.projection(patient, d(5)))


# --- AT-06 / AT-07: scoped processes through the decision workflow ---------------

def two_drugs_and_follow_up(policy=HoldResumePolicy.RESTART_FROM_RESUME):
    def every(code, days, count, event_type="TREATMENT"):
        return visit(code, at("START"), event_type=event_type, recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=days, unit=TimeUnit.DAY),
            start_reference=AnchorReference(code="START"),
            termination=RecurrenceTermination(type="COUNT", count=count)))
    return approved(
        [every("DRUG_A", 21, 6), every("DRUG_B", 7, 12),
         every("FOLLOW_UP", 28, 6, event_type="TELEPHONE_CONTACT")],
        anchors=[anchor("START")], processes=[
            ProcessDefinition(code="A", display_name="Drug A", kind=ProcessKind.INTERVENTION,
                              event_codes=["DRUG_A"], hold_resume_policy=policy),
            ProcessDefinition(code="B", display_name="Drug B", kind=ProcessKind.INTERVENTION,
                              event_codes=["DRUG_B"], hold_resume_policy=policy),
            ProcessDefinition(code="FU", display_name="Follow-up", kind=ProcessKind.FOLLOW_UP,
                              event_codes=["FOLLOW_UP"], hold_resume_policy=policy)])


def test_at07_stopping_one_intervention_keeps_the_other_and_follow_up(cleanup):
    trial = open_trial(cleanup, two_drugs_and_follow_up())
    patient = trial.enrol("P-STOP-A", anchors={"START": d(5)})
    before = trial.rows(patient)
    proposal = trial.preview(patient, process_transition=transition("A", ProcessState.STOPPED, d(20, 11)),
                             reason="PI stops drug A")
    changes = {item["logical_key"]: item["change"] for item in proposal.impact["events"]}
    assert {key for key, value in changes.items() if value != "UNCHANGED"} == {
        "DRUG_A:3", "DRUG_A:4", "DRUG_A:5"}
    rows = trial.confirm(proposal)
    assert [rows[f"DRUG_A:{i}"].status for i in range(6)] == ["RESOLVED"] * 3 + ["CANCELLED"] * 3
    for key, row in rows.items():
        if not key.startswith("DRUG_A"):
            assert (row.status, row.nominal_start_date) == (before[key].status, before[key].nominal_start_date)
    stored = trial.session.scalars(select(db.PatientState).where(
        db.PatientState.state_code == "uctsm:process_transition")).all()
    assert len(stored) == 1 and stored[0].state_value["status"] == "CONFIRMED"
    assert stored[0].state_value["authority"] == "PI"
    assert trial.audit("PATIENT_PROCESS_TRANSITION_RECORDED")
    check_invariants(trial, patient, today=d(21, 11))


@pytest.mark.parametrize("policy,expected", [
    (HoldResumePolicy.NOMINAL, [("PAUSED", d(26)), ("RESOLVED", d(16, 11))]),
    (HoldResumePolicy.SHIFT_BY_HOLD, [("RESOLVED", d(15, 11)), ("RESOLVED", d(6, 12))]),
    (HoldResumePolicy.RESTART_FROM_RESUME, [("RESOLVED", d(9, 11)), ("RESOLVED", d(30, 11))]),
    (HoldResumePolicy.WAIT_FOR_DECISION, [("UNRESOLVED", None), ("UNRESOLVED", None)]),
])
def test_at06_delayed_cycle_during_a_hold_follows_the_stated_policy(cleanup, policy, expected):
    trial = open_trial(cleanup, two_drugs_and_follow_up(policy))
    patient = trial.enrol("P-HOLD-A", anchors={"START": d(5)})
    trial.confirm(trial.preview(patient, process_transition=transition("A", ProcessState.HELD, d(20))))
    held = trial.rows(patient)
    assert held["DRUG_A:1"].status == "PAUSED"
    assert "DRUG_A:1" not in reminder_keys(trial.projection(patient, d(27)))
    rows = trial.confirm(trial.preview(patient, process_transition=transition(
        "A", ProcessState.ACTIVE, d(9, 11))))
    assert [(rows[key].status, rows[key].nominal_start_date if rows[key].status != "UNRESOLVED" else None)
            for key in ("DRUG_A:1", "DRUG_A:2")] == expected
    assert rows["DRUG_B:3"].status == "RESOLVED"  # drug B was never held
    check_invariants(trial, patient, today=d(10, 11))


# --- AT-08: planned start, actual start, correction ------------------------------

def cycles_after_a_planned_start():
    day1 = visit("DAY_1", CycleDayTiming(cycle_reference="CYCLE", cycle=CycleSelector(type="CURRENT"), day=1),
                 event_type="TREATMENT")
    pre = visit("PRE_CHECK", at("TREATMENT_START", -3, AnchorAcceptance.PLANNED_OR_ACTUAL),
                event_type="LABORATORY")
    post = visit("POST_START_CALL", at("TREATMENT_START", 7), event_type="TELEPHONE_CONTACT",
                 visit_mode="TELEPHONE")
    block = RepeatBlock(code="CYCLES", protocol_label="Cycles", display_name="Cycles",
                        event_codes=["DAY_1"], dependency_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT,
                        cycle_plan=CyclePlan(
                            code="CYCLE", start_reference=AnchorReference(code="TREATMENT_START"),
                            length=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
                            dependency_mode=CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START,
                            start_event_code="DAY_1",
                            termination=RecurrenceTermination(type="COUNT", count=4)))
    return approved([day1, pre, post], anchors=[anchor("TREATMENT_START"), anchor("ENROLLED")],
                    repeat_blocks=[block])


def test_at08_planned_then_actual_then_corrected_start(cleanup):
    trial = open_trial(cleanup, cycles_after_a_planned_start())
    patient = trial.enrol("P-AT08", anchors={"ENROLLED": d(1)})
    _, rows = trial.record_anchor(patient, "TREATMENT_START", d(12), "PLANNED")
    assert rows["PRE_CHECK:0"].nominal_start_date == d(9)
    assert rows["POST_START_CALL:0"].status == "WAITING_FOR_ANCHOR"
    # PT-07: the planned date passing proves nothing; the actual-only rule waits.
    trial.evaluate(patient)
    assert trial.rows(patient)["POST_START_CALL:0"].status == "WAITING_FOR_ANCHOR"
    assert "POST_START_CALL:0" not in reminder_keys(trial.projection(patient, d(30)))

    _, rows = trial.record_anchor(patient, "TREATMENT_START", d(13), "CLINICAL_RECORD")
    assert rows["POST_START_CALL:0"].nominal_start_date == d(20)
    fact(trial, patient, "DAY_1:0", OccurrenceFactKind.PERFORMED, d(13))
    trial.evaluate(patient)
    fact(trial, patient, "DAY_1:1", OccurrenceFactKind.PERFORMED, d(5, 11))
    trial.evaluate(patient)
    rows = trial.rows(patient)
    assert rows["DAY_1:2"].nominal_start_date == d(26, 11)
    history = check_invariants(trial, patient, today=d(6, 11))

    # Correction: cycle 2 was actually given on 04-Nov.
    first_record = trial.session.scalars(select(db.PatientEventOccurrence).where(
        db.PatientEventOccurrence.actual_date == d(5, 11))).one()
    fact(trial, patient, "DAY_1:1", OccurrenceFactKind.PERFORMED, d(4, 11),
         supersedes_occurrence_id=first_record.id, reason="date transcribed wrongly")
    trial.evaluate(patient)
    rows = trial.rows(patient)
    assert rows["DAY_1:2"].nominal_start_date == d(25, 11)     # future impact updated
    assert rows["DAY_1:1"].status == "COMPLETED"               # performed history kept
    assert trial.session.get(db.PatientEventOccurrence, first_record.id) is not None
    assert trial.audit("PATIENT_OCCURRENCE_FACT_CORRECTED")
    check_invariants(trial, patient, today=d(6, 11), completed_before=history)


# --- PT-04: booked slot outside the permitted window --------------------------

def test_pt04_booking_outside_the_window_is_flagged_not_absorbed(cleanup):
    visit_ = visit("WEEK_4", at("START", 28), window=Window(
        before=NonNegativeTemporalAmount(value=2, unit=TimeUnit.DAY),
        after=NonNegativeTemporalAmount(value=2, unit=TimeUnit.DAY)))
    trial = open_trial(cleanup, approved([visit_], anchors=[anchor("START")]))
    patient = trial.enrol("P-BOOK", anchors={"START": d(5)})
    fact(trial, patient, "WEEK_4:0", OccurrenceFactKind.BOOKED, d(9, 11))
    trial.evaluate(patient)
    row = trial.rows(patient)["WEEK_4:0"]
    assert (row.nominal_start_date, row.earliest_date, row.latest_date) == (d(2, 11), d(31), d(4, 11))
    assert row.status == "RESOLVED"
    assert row.generation_reason["booking"]["flag"] == "BOOKED_OUTSIDE_PERMITTED"
    live = trial.result(patient).events[0]
    assert live.booked_time == d(9, 11) and live.booked_outside_permitted is True
    assert live.actual_time is None


# --- AT-09: phased follow-up, open-ended, horizon extension -------------------

def test_at09_horizon_extension_keeps_identities_and_the_boundary_once(cleanup):
    plan = RecurrencePlan(
        start_reference=AnchorReference(code="EOT"), include_start=False,
        segments=[RecurrenceSegment(interval=PositiveTemporalAmount(value=3, unit=TimeUnit.MONTH),
                                    end_offset=TemporalAmount(value=12, unit=TimeUnit.MONTH),
                                    end_inclusive=True),
                  RecurrenceSegment(interval=PositiveTemporalAmount(value=6, unit=TimeUnit.MONTH))],
        termination=RecurrenceTermination(type="HORIZON"))
    fu = visit("FU", at("EOT"), event_type="TELEPHONE_CONTACT", visit_mode="TELEPHONE",
               recurrence_plan=plan)
    trial = open_trial(cleanup, approved([fu], anchors=[anchor("EOT")]))
    patient = trial.enrol("P-FU", anchors={"EOT": d(5)}, horizon=d(1, 12, 2027))
    short = trial.rows(patient)
    trial.evaluate(patient, horizon=d(1, 12, 2029))
    long = trial.rows(patient)
    for key, row in short.items():
        assert (long[key].nominal_start_date, long[key].logical_occurrence_id) == (
            row.nominal_start_date, row.logical_occurrence_id)
    dates_ = sorted(row.nominal_start_date for row in long.values())
    assert dates_.count(d(5, 10, 2027)) == 1              # the 12-month boundary, once
    assert dates_[:6] == [d(5, 1, 2027), d(5, 4, 2027), d(5, 7, 2027), d(5, 10, 2027),
                          d(5, 4, 2028), d(5, 10, 2028)]
    summary = trial.result(patient, horizon=d(1, 12, 2029)).repeat_summaries[0]
    assert summary.continues is True   # the horizon is not the end of follow-up


# --- AT-10: a condition recurs ---------------------------------------------------

def test_at10_recurrence_is_a_new_episode_through_the_workflow(cleanup):
    low = ComparisonCondition(operator="LT", left=FieldOperand(field="anc"), right=LiteralOperand(value=1000))
    ok = ComparisonCondition(operator="GTE", left=FieldOperand(field="anc"), right=LiteralOperand(value=1000))
    rule = ConditionalDefinition(
        code="ANC_LOW", protocol_label="ANC < 1000", display_name="Low ANC", condition=low,
        resolution_condition=ok, actions=[ConditionalAction(
            action_type="REPEAT_EVENT", target_code="CBC", condition=low,
            parameters={"interval": {"value": 7, "unit": "DAY"}})])
    cbc = visit("CBC", at("ANC_ONSET", 0), event_type="LABORATORY")
    schedule = approved([cbc], anchors=[anchor("ANC_ONSET", anchor_type="CONDITION",
                                                source_condition_code="ANC_LOW")],
                        conditionals=[rule])
    trial = open_trial(cleanup, schedule)
    patient = trial.enrol("P-ANC")
    trial.condition(patient, "ANC_LOW", ConditionState.ACTIVE, d(5))
    _, rows = trial.condition(patient, "ANC_LOW", ConditionState.RESOLVED, d(5), resolved=d(20))
    trial.record_actual(patient, "CBC:0", d(12))
    first = {key: (row.status, row.nominal_start_date, row.logical_occurrence_id)
             for key, row in trial.rows(patient).items() if key in {"CBC:0", "CBC:1"}}
    _, rows = trial.condition(patient, "ANC_LOW", ConditionState.RECURRED, d(10, 11), index=1)
    assert {key: (rows[key].status, rows[key].nominal_start_date, rows[key].logical_occurrence_id)
            for key in first} == first
    assert rows["CBC:1000"].nominal_start_date == d(17, 11)
    assert rows["CBC:1000"].generation_reason["condition_episode"]["episode"] == 1


# --- AT-11: late crossover with an earlier effective date --------------------------

def test_at11_late_crossover_re_evaluates_only_the_affected_period(cleanup):
    def arm_visit(code, arm):
        return visit(code, at("START"), applicability=[ApplicabilityRule(dimension="ARM", values=[arm])],
                     recurrence=RecurrenceRule(
                         interval=PositiveTemporalAmount(value=14, unit=TimeUnit.DAY),
                         start_reference=AnchorReference(code="START"),
                         termination=RecurrenceTermination(type="COUNT", count=8)))
    arms = [StudyDimension(code=code, protocol_label=code, display_name=code) for code in ("A", "B")]
    trial = open_trial(cleanup, approved([arm_visit("A_VISIT", "A"), arm_visit("B_VISIT", "B")],
                                         anchors=[anchor("START")], arms=arms))
    patient = trial.enrol("P-X", anchors={"START": d(5)}, arm="A")
    trial.record_actual(patient, "A_VISIT:0", d(5))
    trial.record_actual(patient, "A_VISIT:1", d(19))
    history = check_invariants(trial, patient, today=d(1, 12))
    # Entered on 1-Dec, effective 2-Nov.
    proposal = trial.preview(patient, assignment_change={"ARM": "B"},
                             assignment_effective_from=d(2, 11), reason="crossover entered late")
    assert proposal.impact["assignment_effective_from"] == "2026-11-02"
    rows = trial.confirm(proposal)
    assert rows["A_VISIT:0"].status == rows["A_VISIT:1"].status == "COMPLETED"
    assert rows["A_VISIT:2"].status == "CANCELLED"                  # 02-Nov onwards is arm B
    assert rows["B_VISIT:2"].nominal_start_date == d(2, 11)
    assert "B_VISIT:1" not in rows or rows["B_VISIT:1"].status != "RESOLVED"
    facts = [row.state_value for row in trial.session.scalars(select(db.PatientState).where(
        db.PatientState.state_code == "uctsm:assignment_fact").order_by(db.PatientState.effective_at))]
    assert [(item["value"], item["effective_from"]) for item in facts] == [
        ("A", None), ("B", "2026-11-02")]
    assert trial.audit("PATIENT_ASSIGNMENT_FACT_RECORDED")
    assert trial.audit("PATIENT_ASSIGNMENT_CHANGED")
    check_invariants(trial, patient, today=d(1, 12), completed_before=history)


# --- AT-19: month-end semantics persisted and visible ---------------------------

def test_at19_month_end_clamp_is_visible_in_the_persisted_plan(cleanup):
    monthly = visit("MONTHLY", at("START"), recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=1, unit=TimeUnit.MONTH),
        start_reference=AnchorReference(code="START"), include_start=False,
        termination=RecurrenceTermination(type="COUNT", count=2)))
    trial = open_trial(cleanup, approved([monthly], anchors=[anchor("START")]))
    patient = trial.enrol("P-M", anchors={"START": d(31, 1, 2027)})
    rows = trial.rows(patient)
    assert rows["MONTHLY:0"].nominal_start_date == d(28, 2, 2027)
    assert rows["MONTHLY:0"].generation_reason["recurrence"]["calendar_policy"]["stated"] is False
    assert rows["MONTHLY:1"].nominal_start_date == d(31, 3, 2027)


# --- persistence round trip (no migration: existing JSON columns) -------------

def test_wp3_schedule_fields_round_trip_through_the_repository(cleanup):
    from app.db.repositories import ScheduleRepository

    base = radiotherapy()
    schedule = approved(base.events, anchors=base.anchors, holiday_calendars=base.holiday_calendars,
                        processes=[ProcessDefinition(
                            code="RT", display_name="Radiotherapy", kind=ProcessKind.INTERVENTION,
                            event_codes=["RT"], hold_resume_policy=HoldResumePolicy.NOMINAL,
                            resume_trigger="EXPLICIT_RESUME")])
    trial = open_trial(cleanup, schedule)
    loaded = ScheduleRepository(trial.session).get(trial.current_version_id())
    assert loaded.events[0].calendar_recurrence == schedule.events[0].calendar_recurrence
    assert loaded.events[0].recurrence is None and loaded.events[0].recurrence_plan is None
    assert loaded.holiday_calendars == schedule.holiday_calendars
    assert [item.model_dump() for item in loaded.processes] == [
        item.model_dump() for item in schedule.processes]
    # replace_event keeps the calendar rule too.
    edited = loaded.events[0].model_copy(update={"display_name": "Radiotherapy fraction"})
    ScheduleRepository(trial.session).replace_event(loaded.schedule_version_id, edited)
    trial.session.flush()
    again = ScheduleRepository(trial.session).get(loaded.schedule_version_id)
    assert again.events[0].calendar_recurrence == schedule.events[0].calendar_recurrence
