"""WP3 - scoped process states (RL-10, RL-11, AT-06, AT-07) and the
occurrence selectors built on them (START/END, LAST while unfinished).

Synthetic: drug A every 21 days and drug B every 7 days from Mon 05-Oct-2026,
survival follow-up every 3 months from the same date, and an unlinked
quality-of-life questionnaire.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from _engine_cases import anchor, approved, by_code, context, evaluate, visit
from test_engine_constraints import constrained, lower, schedule_with, shift

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.models import (
    ConditionalAction, ConditionalDefinition, ConditionState, CycleDependencyMode, CyclePlan,
    HoldResumePolicy, PatientConditionStatus, ProcessDefinition, ProcessKind, ProcessState,
    ProcessTarget, ProcessTransition, ProcessTransitionReason, RecurrenceRule,
    RecurrenceTermination, RepeatBlock,
)
from app.domain.schedule.timing import (
    AnchorReference, CycleDayTiming, CycleSelector, EventValue, OccurrenceSelector,
    OffsetTiming, PositiveTemporalAmount, ProcessBoundaryValue, TemporalAmount, TimeUnit,
)
from app.domain.schedule.validator import ScheduleValidator

START = date(2026, 10, 5)
RECORDED = datetime(2026, 12, 1, 9, tzinfo=timezone.utc)


def d(day: int, month: int = 10, year: int = 2026) -> date:
    return date(year, month, day)


def repeating(code: str, days: int | None = None, *, months: int | None = None,
              count: int | None = None, event_type: str = "TREATMENT"):
    interval = PositiveTemporalAmount(value=days or months, unit=TimeUnit.DAY if days else TimeUnit.MONTH)
    return visit(code, OffsetTiming(reference=AnchorReference(code="START"),
                                    offset=TemporalAmount(value=0, unit=TimeUnit.DAY)),
                 event_type=event_type, recurrence=RecurrenceRule(
                     interval=interval, start_reference=AnchorReference(code="START"),
                     include_start=days is not None,
                     termination=(RecurrenceTermination(type="COUNT", count=count) if count
                                  else RecurrenceTermination(type="HORIZON"))))


def process(code: str, kind: ProcessKind, events: list[str],
            policy: HoldResumePolicy = HoldResumePolicy.NOMINAL, **extra) -> ProcessDefinition:
    return ProcessDefinition(code=code, display_name=code.title(), kind=kind,
                             event_codes=events, hold_resume_policy=policy, **extra)


def three_processes(policy: HoldResumePolicy = HoldResumePolicy.NOMINAL, conditionals=None):
    events = [repeating("DRUG_A_DOSE", 21, count=6), repeating("DRUG_B_DOSE", 7, count=12),
              repeating("SURVIVAL", months=3, event_type="TELEPHONE_CONTACT"),
              repeating("QOL", 28, count=4, event_type="ASSESSMENT")]
    return approved(events, anchors=[anchor("START")], conditionals=conditionals, processes=[
        process("DRUG_A", ProcessKind.INTERVENTION, ["DRUG_A_DOSE"], policy),
        process("DRUG_B", ProcessKind.INTERVENTION, ["DRUG_B_DOSE"], policy),
        process("FOLLOW_UP", ProcessKind.FOLLOW_UP, ["SURVIVAL"], policy),
    ])


def change(target: ProcessTarget, state: ProcessState, when: date, tid: str,
           reason=ProcessTransitionReason.DECISION, **extra) -> ProcessTransition:
    values = {"recorded_at": RECORDED, "status": "CONFIRMED"}
    values.update(extra)
    return ProcessTransition(id=tid, target=target, to_state=state, effective_at=when,
                             reason=reason, **values)


def one(code: str) -> ProcessTarget:
    return ProcessTarget(type="PROCESS", process_code=code)


def run(schedule, transitions=(), *, horizon=d(31, 3, 2027), **extra):
    ctx = context(schedule, anchors={"START": START}, process_transitions=list(transitions), **extra)
    return by_code(evaluate(schedule, ctx, horizon=horizon))


def statuses(items):
    return [item.status.value for item in items]


def test_at07_stopping_drug_a_cancels_only_drug_a_obligations():
    schedule = three_processes()
    before = run(schedule)
    after = run(schedule, [change(one("DRUG_A"), ProcessState.STOPPED, d(20, 11), "stop-a")])
    assert statuses(after["DRUG_A_DOSE"]) == ["RESOLVED"] * 3 + ["CANCELLED"] * 3
    cancelled = after["DRUG_A_DOSE"][3]
    assert cancelled.explanation["process_state"]["process"] == "DRUG_A"
    assert cancelled.explanation["process_state"]["transition"] == "stop-a"
    for code in ("DRUG_B_DOSE", "SURVIVAL", "QOL"):
        assert [item.model_dump() for item in after[code]] == [item.model_dump() for item in before[code]]


def test_at07_a_condition_can_target_one_intervention_process():
    flag = ComparisonCondition(operator="EQUALS", left=FieldOperand(field="neuropathy"),
                               right=LiteralOperand(value=True))
    rule = ConditionalDefinition(
        code="NEUROPATHY", protocol_label="Grade 3 neuropathy", display_name="Neuropathy",
        condition=flag, actions=[ConditionalAction(
            action_type="STOP_PROCESS", target_code="DRUG_B", condition=flag)])
    schedule = three_processes(conditionals=[rule])
    result = run(schedule, conditions={"NEUROPATHY": PatientConditionStatus(
        condition_code="NEUROPATHY", state=ConditionState.ACTIVE, occurrence_date=d(20))})
    assert statuses(result["DRUG_B_DOSE"]).count("CANCELLED") == 9
    assert result["DRUG_B_DOSE"][3].explanation["process_state"]["source"] == "CONDITION"
    assert "CANCELLED" not in statuses(result["DRUG_A_DOSE"])
    assert "CANCELLED" not in statuses(result["SURVIVAL"])


def test_withdrawal_from_treatment_keeps_follow_up_and_consent_withdrawal_stops_everything():
    schedule = three_processes()
    treatment = run(schedule, [change(
        ProcessTarget(type="PROCESS_KIND", kind=ProcessKind.INTERVENTION),
        ProcessState.STOPPED, d(20), "wd-tx",
        reason=ProcessTransitionReason.WITHDRAWAL_FROM_TREATMENT)])
    assert "CANCELLED" in statuses(treatment["DRUG_A_DOSE"])
    assert "CANCELLED" in statuses(treatment["DRUG_B_DOSE"])
    assert "CANCELLED" not in statuses(treatment["SURVIVAL"])
    assert "CANCELLED" not in statuses(treatment["QOL"])

    consent = run(schedule, [change(
        ProcessTarget(type="PATIENT"), ProcessState.STOPPED, d(20), "wd-consent",
        reason=ProcessTransitionReason.WITHDRAWAL_OF_CONSENT)])
    for code in ("DRUG_A_DOSE", "DRUG_B_DOSE", "SURVIVAL", "QOL"):
        after = [item for item in consent[code] if item.timing.nominal_start > d(20)]
        assert after and all(item.status.value == "CANCELLED" for item in after), code
    assert consent["QOL"][1].explanation["process_state"]["reason"] == "WITHDRAWAL_OF_CONSENT"

    end = run(schedule, [change(one("FOLLOW_UP"), ProcessState.COMPLETED, d(1, 12), "eofu",
                                reason=ProcessTransitionReason.END_OF_FOLLOW_UP)])
    assert statuses(end["SURVIVAL"])[-1] == "CANCELLED"
    assert "CANCELLED" not in statuses(end["DRUG_A_DOSE"])


def test_participation_decisions_are_typed_not_guessed():
    with pytest.raises(ValueError, match="withdrawal of consent"):
        change(one("DRUG_A"), ProcessState.STOPPED, d(20), "x",
               reason=ProcessTransitionReason.WITHDRAWAL_OF_CONSENT)
    with pytest.raises(ValueError, match="end of follow-up"):
        change(one("FOLLOW_UP"), ProcessState.STOPPED, d(20), "x",
               reason=ProcessTransitionReason.END_OF_FOLLOW_UP)
    with pytest.raises(ValueError, match="resume policy"):
        change(one("DRUG_A"), ProcessState.HELD, d(20), "x",
               resume_policy=HoldResumePolicy.NOMINAL)


def test_only_confirmed_unsuperseded_transitions_act():
    schedule = three_processes()
    pending = run(schedule, [change(one("DRUG_A"), ProcessState.STOPPED, d(20), "p",
                                    status="PENDING_CONFIRMATION")])
    assert "CANCELLED" not in statuses(pending["DRUG_A_DOSE"])
    corrected = run(schedule, [
        change(one("DRUG_A"), ProcessState.STOPPED, d(20), "wrong"),
        change(one("DRUG_A"), ProcessState.STOPPED, d(20, 11), "right", supersedes="wrong")])
    assert statuses(corrected["DRUG_A_DOSE"]) == ["RESOLVED"] * 3 + ["CANCELLED"] * 3
    assert corrected["DRUG_A_DOSE"][3].explanation["process_state"]["transition"] == "right"


# --- AT-06: a delayed cycle during a hold, per stated policy -------------------

HOLD = [change(one("DRUG_A"), ProcessState.HELD, d(20), "hold"),
        change(one("DRUG_A"), ProcessState.ACTIVE, d(9, 11), "resume")]


@pytest.mark.parametrize("policy,expected", [
    # C2 (26-Oct) fell in the hold and is not made up; C3 stays on the grid.
    (HoldResumePolicy.NOMINAL, [("PAUSED", d(26)), ("RESOLVED", d(16, 11)), ("RESOLVED", d(7, 12))]),
    # Everything from the hold start moves by the 20 days held.
    (HoldResumePolicy.SHIFT_BY_HOLD, [("RESOLVED", d(15, 11)), ("RESOLVED", d(6, 12)), ("RESOLVED", d(27, 12))]),
    # C2 is given on the actual resume date; later cycles keep 21-day spacing.
    (HoldResumePolicy.RESTART_FROM_RESUME, [("RESOLVED", d(9, 11)), ("RESOLVED", d(30, 11)), ("RESOLVED", d(21, 12))]),
    # Nothing is invented while the decision is outstanding.
    (HoldResumePolicy.WAIT_FOR_DECISION, [("UNRESOLVED", None)] * 3),
    (HoldResumePolicy.NONE_STATED, [("UNRESOLVED", None)] * 3),
])
def test_at06_delayed_cycle_during_hold_per_policy(policy, expected):
    result = run(three_processes(policy), HOLD)["DRUG_A_DOSE"]
    assert (result[0].status.value, result[0].timing.nominal_start) == ("RESOLVED", START)
    assert [(item.status.value, item.timing.nominal_start if item.timing else None)
            for item in result[1:4]] == expected
    # Drug B is a different process: untouched by drug A's hold.
    assert "PAUSED" not in statuses(run(three_processes(policy), HOLD)["DRUG_B_DOSE"])


def test_at06_open_hold_pauses_and_a_resume_decision_resolves_an_open_policy():
    open_hold = run(three_processes(HoldResumePolicy.NONE_STATED), HOLD[:1])["DRUG_A_DOSE"]
    assert statuses(open_hold) == ["RESOLVED"] + ["PAUSED"] * 5
    decided = [HOLD[0], change(one("DRUG_A"), ProcessState.ACTIVE, d(9, 11), "resume",
                               resume_policy=HoldResumePolicy.RESTART_FROM_RESUME)]
    result = run(three_processes(HoldResumePolicy.NONE_STATED), decided)["DRUG_A_DOSE"]
    assert result[1].timing.nominal_start == d(9, 11)
    # A decision that contradicts a STATED policy is not silently applied.
    conflict = run(three_processes(HoldResumePolicy.NOMINAL), decided)["DRUG_A_DOSE"]
    assert conflict[1].status.value == "UNRESOLVED"
    assert conflict[1].explanation["process_state"]["notes"][0]["conflict"] == "RESUME_POLICY_CONFLICT"


def test_at06_actual_previous_cycle_dependency_follows_the_delayed_actual_start():
    """The other dependency policy: cycles follow the ACTUAL previous start."""
    plan = CyclePlan(code="CYCLE", start_reference=AnchorReference(code="START"),
                     length=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
                     dependency_mode=CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START,
                     termination=RecurrenceTermination(type="COUNT", count=4))
    day1 = visit("C_D1", CycleDayTiming(cycle_reference="CYCLE", cycle=CycleSelector(type="CURRENT"), day=1),
                 event_type="TREATMENT")
    block = RepeatBlock(code="CYCLES", protocol_label="Cycles", display_name="Cycles",
                        event_codes=["C_D1"], dependency_mode="ACTUAL_PREVIOUS_EVENT", cycle_plan=plan)
    schedule = approved([day1], anchors=[anchor("START")], repeat_blocks=[block])
    ctx = context(schedule, anchors={"START": START, "CYCLE_2_START": d(9, 11)})
    items = by_code(evaluate(schedule, ctx))["C_D1"]
    assert [item.timing.nominal_start for item in items[:3]] == [START, d(9, 11), d(30, 11)]


def test_resolving_a_hold_condition_keeps_the_process_held_unless_stated():
    flag = ComparisonCondition(operator="EQUALS", left=FieldOperand(field="tox"),
                               right=LiteralOperand(value=True))
    rule = ConditionalDefinition(
        code="TOX", protocol_label="Toxicity", display_name="Toxicity", condition=flag,
        actions=[ConditionalAction(action_type="HOLD_PROCESS", target_code="DRUG_A", condition=flag)])
    resolved = {"TOX": PatientConditionStatus(condition_code="TOX", state=ConditionState.RESOLVED,
                                              occurrence_date=d(20), resolution_date=d(9, 11))}
    held = run(three_processes(conditionals=[rule]), conditions=resolved)["DRUG_A_DOSE"]
    assert statuses(held) == ["RESOLVED"] + ["PAUSED"] * 5
    schedule = three_processes(conditionals=[rule])
    schedule.processes[0].resume_trigger = "CONDITION_RESOLUTION"
    resumed = run(schedule, conditions=resolved)["DRUG_A_DOSE"]
    assert statuses(resumed) == ["RESOLVED", "PAUSED"] + ["RESOLVED"] * 4


def test_validator_checks_processes():
    schedule = three_processes(HoldResumePolicy.NONE_STATED)
    schedule.processes[1].event_codes.append("DRUG_A_DOSE")
    schedule.processes[2].event_codes.append("MISSING")
    codes = {(item.issue_code, item.blocking) for item in ScheduleValidator().validate(schedule)}
    assert ("PROCESS_OBLIGATION_CONFLICT", True) in codes
    assert ("UNRESOLVED_REFERENCE", True) in codes
    assert ("PROCESS_RESUME_POLICY_UNSTATED", False) in codes


# --- selectors: process boundary, END, LAST while unfinished ---------------------

def scan_after_end_of_a():
    scan = visit("RESTAGING", constrained(lower(
        shift(ProcessBoundaryValue(process_code="DRUG_A", boundary="END"), 28), "after-end-a")),
        event_type="IMAGING")
    last_b = visit("B_WRAP_UP", constrained(lower(shift(EventValue(
        event_code="DRUG_B_DOSE", occurrence=OccurrenceSelector(type="LAST"),
        accepts="PLANNED_OR_ACTUAL"), 7), "after-last-b")))
    events = [repeating("DRUG_A_DOSE", 21, count=6), repeating("DRUG_B_DOSE", 7), scan, last_b]
    return schedule_with(events, anchors=[anchor("START")], processes=[
        process("DRUG_A", ProcessKind.INTERVENTION, ["DRUG_A_DOSE"]),
        process("DRUG_B", ProcessKind.INTERVENTION, ["DRUG_B_DOSE"])])


def test_process_end_is_unknown_until_the_process_actually_ends():
    schedule = scan_after_end_of_a()
    waiting = run(schedule)
    assert waiting["RESTAGING"][0].status.value == "WAITING_FOR_ANCHOR"
    assert waiting["RESTAGING"][0].missing_inputs[0].reason == "PROCESS_NOT_ENDED"
    stopped = run(schedule, [change(one("DRUG_A"), ProcessState.STOPPED, d(2, 11), "stop")])
    item = stopped["RESTAGING"][0]
    assert item.feasibility.value == "FEASIBLE"
    assert item.feasible_intervals[0].lower.value == d(30, 11)


def test_last_of_an_open_ended_series_stays_unknown_until_it_is_finished():
    schedule = scan_after_end_of_a()
    open_series = run(schedule)["B_WRAP_UP"][0]
    assert open_series.status.value == "WAITING_FOR_ANCHOR"
    assert open_series.missing_inputs[0].reason == "LAST_NOT_FINAL"
    stopped = run(schedule, [change(one("DRUG_B"), ProcessState.STOPPED, d(20), "stop-b")])
    wrap_up = stopped["B_WRAP_UP"][0]
    # Last remaining (planned) dose is 19-Oct; the cancelled ones do not count.
    assert wrap_up.feasible_intervals[0].lower.value == d(26)


def test_end_boundary_uses_the_planned_end_only_when_accepted():
    follow = visit("FOLLOW", constrained(lower(shift(EventValue(
        event_code="DRUG_A_DOSE", occurrence=OccurrenceSelector(type="FIRST"), boundary="END",
        accepts="PLANNED_OR_ACTUAL"), 1), "after-first-end")))
    actual_only = visit("ACTUAL_FOLLOW", constrained(lower(shift(EventValue(
        event_code="DRUG_A_DOSE", occurrence=OccurrenceSelector(type="FIRST"), boundary="END"), 1),
        "after-first-actual-end")))
    schedule = schedule_with([repeating("DRUG_A_DOSE", 21, count=6), follow, actual_only],
                             anchors=[anchor("START")])
    result = run(schedule)
    assert result["FOLLOW"][0].feasible_intervals[0].lower.value == d(6)
    assert result["ACTUAL_FOLLOW"][0].missing_inputs[0].reason == "END_NOT_RECORDED"
