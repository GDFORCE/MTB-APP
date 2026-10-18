"""Conditions end to end through the condition workflow (matrix 18-19, 21; H3).

I1-I3 hold/resume with the spec's dates: 21-day cycles from Mon 05-Oct-2026
(C2 26-Oct, C3 16-Nov, C4 07-Dec), held 26-Oct, resumed 09-Nov-2026.
I7 two conditions confirmed for the same day, I8 a trial-scoped rule, and H3 a
response-based branch. Every change is a PI decision previewed and confirmed
through PatientScheduleImpactService.
"""

from datetime import date, timedelta

import pytest

from _engine_cases import anchor, approved, visit
from _scenario_harness import check_invariants, new_trial, reminder_keys, statuses

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.deviation import DeviationType
from app.domain.schedule.models import (
    ConditionalAction, ConditionalDefinition, ConditionState, RecurrenceBasis, RecurrenceRule,
    RecurrenceTermination, RepeatBlock, RuleScope,
)
from app.domain.schedule.timing import (
    AnchorReference, OffsetTiming, PositiveTemporalAmount, ProtocolDayTiming,
    TemporalAmount, TimeUnit,
)

FIRST_DOSE = date(2026, 10, 5)
HELD = date(2026, 10, 26)
RESUMED = date(2026, 11, 9)
CYCLES = [f"TREATMENT:{index}" for index in range(6)]
NOMINAL_GRID = [date(2026, 10, 5), date(2026, 10, 26), date(2026, 11, 16),
                date(2026, 12, 7), date(2026, 12, 28), date(2027, 1, 18)]


def flag(field: str) -> ComparisonCondition:
    return ComparisonCondition(operator="EQUALS", left=FieldOperand(field=field),
                               right=LiteralOperand(value=True))


def rule(code: str, *actions: tuple[str, str], scope: RuleScope = RuleScope.PATIENT,
         **extra) -> ConditionalDefinition:
    condition = flag(code.lower())
    return ConditionalDefinition(
        code=code, protocol_label=code, display_name=code.replace("_", " ").title(),
        scope=scope, condition=condition,
        actions=[ConditionalAction(action_type=kind, target_code=target, condition=condition,
                                   **extra) for kind, target in actions],
    )


def days(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit=TimeUnit.DAY)


def treatment():
    return visit("TREATMENT", ProtocolDayTiming(
        reference=AnchorReference(code="FIRST_DOSE"), day=1,
    ), event_type="TREATMENT", recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
        start_reference=AnchorReference(code="FIRST_DOSE"),
        termination=RecurrenceTermination(type="COUNT", count=6),
    ))


def block(resume_mode: str = "UNCLEAR") -> RepeatBlock:
    return RepeatBlock(
        code="TREATMENT_BLOCK", protocol_label="Treatment", display_name="Treatment",
        event_codes=["TREATMENT"], dependency_mode="NOMINAL", resume_mode=resume_mode)


def hold_resume_schedule(resume_mode: str):
    """"Grade 3 toxicity: hold treatment until <= grade 1; resume ..."."""
    return approved([treatment()], anchors=[anchor("FIRST_DOSE")], conditionals=[
        rule("GRADE_3_TOXICITY", ("PAUSE_BLOCK", "TREATMENT_BLOCK")),
        rule("TOXICITY_RECOVERED", ("RESUME_BLOCK", "TREATMENT_BLOCK")),
    ], repeat_blocks=[block(resume_mode)])


def open_trial(schedule):
    trial, engine = new_trial()
    trial.publish(schedule)
    return trial, engine


@pytest.fixture()
def cleanup():
    engines = []
    yield engines
    for trial, engine in engines:
        trial.session.close()
        engine.dispose()


def dates(rows, keys=CYCLES):
    return [rows[key].nominal_start_date for key in keys]


# --- I1-I3: hold, then resume ----------------------------------------------------

@pytest.mark.parametrize("resume_mode,after_resume,after_status", [
    # I1: resume on the original grid; C2 is not made up, C3 stays 16-Nov.
    ("NOMINAL", NOMINAL_GRID[2:], "RESOLVED"),
    # I2: restart from the resume date; everything after shifts by the 14 days held.
    ("ACTUAL_RESUME", [date(2026, 11, 30), date(2026, 12, 21), date(2027, 1, 11),
                       date(2027, 2, 1)], "RESOLVED"),
    # I3: the protocol does not say - no date is invented.
    ("UNCLEAR", [None, None, None, None], "UNRESOLVED"),
])
def test_i1_i3_hold_then_resume(cleanup, resume_mode, after_resume, after_status):
    trial, engine = open_trial(hold_resume_schedule(resume_mode))
    cleanup.append((trial, engine))
    patient = trial.enrol("P-HOLD", anchors={"FIRST_DOSE": FIRST_DOSE})
    assert dates(trial.rows(patient)) == NOMINAL_GRID
    trial.record_actual(patient, "TREATMENT:0", FIRST_DOSE)
    history = check_invariants(trial, patient, today=date(2026, 10, 6))

    # Held on 26-Oct: every cycle from C2 on is paused, none cancelled.
    proposal, rows = trial.condition(patient, "GRADE_3_TOXICITY", ConditionState.ACTIVE, HELD)
    changes = {item["logical_key"]: item["change"] for item in proposal.impact["events"]}
    assert changes["TREATMENT:1"] == "STATUS_CHANGED"
    assert changes["TREATMENT:0"] == "PROTECTED"
    assert statuses(rows, *CYCLES[1:]) == ["PAUSED"] * 5
    assert rows["TREATMENT:1"].generation_reason["block_state"]["condition"] == "GRADE_3_TOXICITY"
    assert reminder_keys(trial.projection(patient, date(2026, 10, 27))) == set()
    history = check_invariants(trial, patient, today=date(2026, 10, 27), completed_before=history)

    # Recovered on 09-Nov.
    _, rows = trial.condition(patient, "TOXICITY_RECOVERED", ConditionState.ACTIVE, RESUMED)
    assert rows["TREATMENT:0"].status == "COMPLETED"
    assert rows["TREATMENT:1"].status == "PAUSED"          # C2 is not made up
    assert statuses(rows, *CYCLES[2:]) == [after_status] * 4
    assert dates(rows, CYCLES[2:]) == after_resume
    for key in CYCLES[2:]:
        assert rows[key].generation_reason["resume"]["mode"] == resume_mode

    today = date(2026, 11, 10)
    keys = reminder_keys(trial.projection(patient, today))
    assert "TREATMENT:1" not in keys
    if after_status == "RESOLVED":
        assert set(CYCLES[2:]) <= keys
    else:
        assert keys == set()
    # The held C2 is never "missed", however long ago it was.
    report = trial.deviations(patient, today=date(2027, 6, 1))
    assert report["TREATMENT:1"].deviation_type == DeviationType.NOT_ASSESSABLE
    check_invariants(trial, patient, today=today, completed_before=history)


def test_i1_pending_toxicity_changes_nothing(cleanup):
    trial, engine = open_trial(hold_resume_schedule("NOMINAL"))
    cleanup.append((trial, engine))
    patient = trial.enrol("P-PENDING", anchors={"FIRST_DOSE": FIRST_DOSE})
    proposal, rows = trial.condition(
        patient, "GRADE_3_TOXICITY", ConditionState.PENDING_CONFIRMATION, HELD)
    assert proposal.impact["summary"]["STATUS_CHANGED"] == 0
    assert statuses(rows, *CYCLES) == ["RESOLVED"] * 6


def test_i1_resolving_the_hold_condition_keeps_the_held_cycle_held(cleanup):
    """WP3 semantics (was a strict xfail): resolution ENDS THE CONDITION EPISODE;
    it is not an implicit resume. The block stays held - no reminders, no
    overdue visits - until an explicit RESUME_BLOCK transition, unless the
    block states ``resume_trigger=CONDITION_RESOLUTION`` (next test)."""
    trial, engine = open_trial(hold_resume_schedule("NOMINAL"))
    cleanup.append((trial, engine))
    patient = trial.enrol("P-RESOLVED", anchors={"FIRST_DOSE": FIRST_DOSE})
    trial.condition(patient, "GRADE_3_TOXICITY", ConditionState.ACTIVE, HELD)
    _, rows = trial.condition(patient, "GRADE_3_TOXICITY", ConditionState.RESOLVED, HELD,
                              resolved=RESUMED)
    assert rows["TREATMENT:1"].status == "PAUSED"
    assert statuses(rows, *CYCLES[1:]) == ["PAUSED"] * 5
    assert rows["TREATMENT:2"].generation_reason["held_after_resolution"] == "GRADE_3_TOXICITY"
    assert "TREATMENT:1" not in reminder_keys(trial.projection(patient, date(2026, 11, 10)))
    # An explicit resume then applies the block's stated resume mode.
    _, rows = trial.condition(patient, "TOXICITY_RECOVERED", ConditionState.ACTIVE, RESUMED)
    assert rows["TREATMENT:1"].status == "PAUSED"
    assert statuses(rows, *CYCLES[2:]) == ["RESOLVED"] * 4
    assert dates(rows, CYCLES[2:]) == NOMINAL_GRID[2:]


def test_i1_block_stating_resolution_resumes_resumes_on_the_resolution_date(cleanup):
    schedule = hold_resume_schedule("NOMINAL")
    schedule.repeat_blocks[0].resume_trigger = "CONDITION_RESOLUTION"
    trial, engine = open_trial(schedule)
    cleanup.append((trial, engine))
    patient = trial.enrol("P-RESUME-ON-RES", anchors={"FIRST_DOSE": FIRST_DOSE})
    trial.condition(patient, "GRADE_3_TOXICITY", ConditionState.ACTIVE, HELD)
    _, rows = trial.condition(patient, "GRADE_3_TOXICITY", ConditionState.RESOLVED, HELD,
                              resolved=RESUMED)
    assert rows["TREATMENT:1"].status == "PAUSED"
    assert statuses(rows, *CYCLES[2:]) == ["RESOLVED"] * 4
    assert dates(rows, CYCLES[2:]) == NOMINAL_GRID[2:]


# --- I7: two conditions confirmed for the same day ----------------------------------

def same_day_schedule(*, reverse: bool = False):
    echo = visit("ECHO", OffsetTiming(reference=AnchorReference(code="FIRST_DOSE"), offset=days(28)),
                 event_type="IMAGING")
    survival = visit("SURVIVAL_FOLLOW_UP", OffsetTiming(
        reference=AnchorReference(code="PROGRESSION_DATE"), offset=days(30)),
        event_type="TELEPHONE_CONTACT", visit_mode="TELEPHONE")
    rules = [
        rule("GRADE_3_TOXICITY", ("PAUSE_BLOCK", "TREATMENT_BLOCK"), ("MANUAL_REVIEW", "ECHO")),
        rule("PROGRESSION", ("STOP_BLOCK", "TREATMENT_BLOCK"), ("CANCEL_EVENT", "ECHO"),
             ("ADD_EVENT", "SURVIVAL_FOLLOW_UP")),
    ]
    if reverse:
        rules.reverse()
    return approved([treatment(), echo, survival], anchors=[
        anchor("FIRST_DOSE"),
        anchor("PROGRESSION_DATE", anchor_type="CONDITION", source_condition_code="PROGRESSION"),
    ], conditionals=rules, repeat_blocks=[block("NOMINAL")])


def plan(rows) -> dict[str, tuple]:
    return {key: (row.status, row.nominal_start_date) for key, row in rows.items()}


@pytest.mark.parametrize("day", [date(2026, 10, 20), HELD])
def test_i7_two_conditions_on_the_same_day_have_one_outcome(cleanup, day):
    """Pause + manual review and stop + cancel + add, all confirmed for one day.

    The result must not depend on which condition the PI confirms first, nor on
    the order the protocol's rules are listed in. Fixed precedence: cancelled
    beats manual review; a stop beats a pause for every later cycle.
    """
    outcomes = []
    for reverse in (False, True):
        trial, engine = open_trial(same_day_schedule(reverse=reverse))
        cleanup.append((trial, engine))
        for order in (("GRADE_3_TOXICITY", "PROGRESSION"), ("PROGRESSION", "GRADE_3_TOXICITY")):
            patient = trial.enrol(f"P-{reverse}-{order[0]}", anchors={"FIRST_DOSE": FIRST_DOSE})
            trial.record_actual(patient, "TREATMENT:0", FIRST_DOSE)
            history = check_invariants(trial, patient, today=date(2026, 10, 6))
            for code in order:
                _, rows = trial.condition(patient, code, ConditionState.ACTIVE, day)
            check_invariants(trial, patient, today=day, completed_before=history)
            outcomes.append(plan(rows))
            assert rows["ECHO:0"].status == "CANCELLED"
            assert rows["ECHO:0"].generation_reason["cancelled_by_condition"] == "PROGRESSION"
            assert rows["SURVIVAL_FOLLOW_UP:0"].nominal_start_date == day + timedelta(days=30)
            assert rows["TREATMENT:0"].status == "COMPLETED"
            assert statuses(rows, *CYCLES[2:]) == ["CANCELLED"] * 4
            # A cycle falling ON the day both happened is held, never left due.
            assert rows["TREATMENT:1"].status == ("PAUSED" if day == HELD else "CANCELLED")
            assert reminder_keys(trial.projection(patient, day)) == {"SURVIVAL_FOLLOW_UP:0"}
    assert all(item == outcomes[0] for item in outcomes[1:])


def test_i7_a_hold_before_a_stop_is_kept_whatever_the_rule_order(cleanup):
    """Held 26-Oct, progression 20-Nov. A stop listed before the pause rule used
    to discard the earlier hold, leaving C2/C3 due (and then overdue)."""
    outcomes = []
    for reverse in (False, True):
        trial, engine = open_trial(same_day_schedule(reverse=reverse))
        cleanup.append((trial, engine))
        patient = trial.enrol(f"P-ORDER-{reverse}", anchors={"FIRST_DOSE": FIRST_DOSE})
        trial.condition(patient, "PROGRESSION", ConditionState.ACTIVE, date(2026, 11, 20))
        _, rows = trial.condition(patient, "GRADE_3_TOXICITY", ConditionState.ACTIVE, HELD)
        assert statuses(rows, *CYCLES[1:3]) == ["PAUSED"] * 2
        assert statuses(rows, *CYCLES[3:]) == ["CANCELLED"] * 3
        check_invariants(trial, patient, today=date(2026, 11, 21))
        outcomes.append(plan(rows))
    assert outcomes[0] == outcomes[1]


# --- I8: a trial-level rule never touches the patient --------------------------------

def test_i8_trial_scoped_rule_confirmed_active_leaves_the_patient_unchanged(cleanup):
    schedule = approved([treatment(), visit("DSMC_SAFETY_REVIEW", OffsetTiming(
        reference=AnchorReference(code="FIRST_DOSE"), offset=days(0)))],
        anchors=[anchor("FIRST_DOSE")], conditionals=[rule(
            "FATAL_TOXICITY", ("STOP_BLOCK", "TREATMENT_BLOCK"),
            ("ADD_EVENT", "DSMC_SAFETY_REVIEW"), scope=RuleScope.TRIAL)],
        repeat_blocks=[block("NOMINAL")])
    trial, engine = open_trial(schedule)
    cleanup.append((trial, engine))
    patient = trial.enrol("P-TRIAL-RULE", anchors={"FIRST_DOSE": FIRST_DOSE})
    before = plan(trial.rows(patient))

    proposal, rows = trial.condition(
        patient, "FATAL_TOXICITY", ConditionState.ACTIVE, date(2026, 10, 20))
    summary = proposal.impact["summary"]
    assert summary["MOVED"] == summary["STATUS_CHANGED"] == summary["CANCELLED"] == 0
    assert plan(rows) == before
    assert dates(rows) == NOMINAL_GRID
    assert rows["DSMC_SAFETY_REVIEW:0"].status == "WAITING_FOR_CONDITION"
    assert rows["DSMC_SAFETY_REVIEW:0"].generation_reason["refused_trial_level_rule"] == {
        "rule": "FATAL_TOXICITY", "scope": "TRIAL"}
    # The recorded condition itself is kept (for the trial-level workflow).
    assert trial.audit("PATIENT_CONDITION_STATE_CHANGED")[-1].after["state"] == "ACTIVE"
    check_invariants(trial, patient, today=date(2026, 10, 21))


# --- H3: response-based branch ---------------------------------------------------------

def response_schedule():
    maintenance = visit("MAINTENANCE", OffsetTiming(
        reference=AnchorReference(code="CR_DATE"), offset=TemporalAmount(value=8, unit=TimeUnit.WEEK),
    ), recurrence=RecurrenceRule(
        interval=PositiveTemporalAmount(value=8, unit=TimeUnit.WEEK),
        start_reference=AnchorReference(code="CR_DATE"),
        termination=RecurrenceTermination(type="HORIZON"),
        # Maintenance "every 8 weeks, the first one 8 weeks after CR". A start offset that
        # disagrees with the event's own timing, or an unstated basis, is ambiguous and cannot
        # be approved; the first occurrence (CR + 8 weeks) and the series basis are stated
        # here explicitly, as a reviewer would, instead of left to a legacy reading.
        start_offset=TemporalAmount(value=8, unit=TimeUnit.WEEK),
        basis=RecurrenceBasis.FIXED_ANCHOR_SERIES, basis_status="STATED",
    ), event_type="TREATMENT")
    survival = visit("SURVIVAL_FOLLOW_UP", OffsetTiming(
        reference=AnchorReference(code="PROGRESSION_DATE"), offset=days(30)),
        event_type="TELEPHONE_CONTACT", visit_mode="TELEPHONE")
    return approved([treatment(), maintenance, survival], anchors=[
        anchor("FIRST_DOSE"),
        anchor("CR_DATE", anchor_type="CONDITION", source_condition_code="RESPONSE_CR"),
        anchor("PROGRESSION_DATE", anchor_type="CONDITION", source_condition_code="PROGRESSION"),
    ], conditionals=[
        rule("RESPONSE_CR", ("STOP_BLOCK", "TREATMENT_BLOCK"), ("ADD_EVENT", "MAINTENANCE")),
        rule("PROGRESSION", ("STOP_BLOCK", "TREATMENT_BLOCK"), ("ADD_EVENT", "SURVIVAL_FOLLOW_UP")),
    ], repeat_blocks=[block("NOMINAL")])


def test_h3_complete_response_stops_treatment_and_starts_maintenance(cleanup):
    trial, engine = open_trial(response_schedule())
    cleanup.append((trial, engine))
    patient = trial.enrol("P-CR", anchors={"FIRST_DOSE": FIRST_DOSE})
    for index, given in enumerate(NOMINAL_GRID[:3]):
        trial.record_actual(patient, f"TREATMENT:{index}", given)
    history = check_invariants(trial, patient, today=date(2026, 11, 17))
    rows = trial.rows(patient)
    assert rows["MAINTENANCE:0"].status == "WAITING_FOR_CONDITION"
    assert rows["SURVIVAL_FOLLOW_UP:0"].status == "WAITING_FOR_CONDITION"

    # A response that is only pending does nothing.
    cr = date(2026, 11, 20)
    proposal, rows = trial.condition(patient, "RESPONSE_CR", ConditionState.PENDING_CONFIRMATION, cr)
    assert proposal.impact["summary"]["STATUS_CHANGED"] == proposal.impact["summary"]["MOVED"] == 0
    assert statuses(rows, *CYCLES[3:]) == ["RESOLVED"] * 3

    # The PI confirms CR on 20-Nov.
    _, rows = trial.condition(patient, "RESPONSE_CR", ConditionState.ACTIVE, cr)
    assert statuses(rows, *CYCLES[:3]) == ["COMPLETED"] * 3
    assert statuses(rows, *CYCLES[3:]) == ["CANCELLED"] * 3
    maintenance = sorted((row for key, row in rows.items() if key.startswith("MAINTENANCE:")),
                         key=lambda row: row.occurrence_index)
    assert [row.nominal_start_date for row in maintenance][:3] == [
        date(2027, 1, 15), date(2027, 3, 12), date(2027, 5, 7)]
    assert all(row.status == "RESOLVED" for row in maintenance)
    # The progression branch is untouched.
    assert rows["SURVIVAL_FOLLOW_UP:0"].status == "WAITING_FOR_CONDITION"
    keys = reminder_keys(trial.projection(patient, date(2026, 11, 21)))
    assert "MAINTENANCE:0" in keys
    assert keys & set(CYCLES) == set()
    check_invariants(trial, patient, today=date(2026, 11, 21), completed_before=history)
