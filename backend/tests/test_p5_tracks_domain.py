"""P5-01/P5-02/P5-03 (domain): tracks, rebase policies, command inputs, reversal.

Fixed dates; synthetic schedule. BASELINE = 1 Jan 2026.

  TREATMENT track  C1D1 (baseline +7 d)  and a weekly DOSE series
  LABS track       LAB_AFTER_TX = 7 d after the ACTUAL TREATMENT date of C1D1
  IMAGING track    IMAGING_BASELINE = baseline +30 d (fixed from the anchor)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.domain.schedule.commands import (
    CommandType, LedgerEntry, PauseTrack, RebaseTrack, ResumeTrack, StopTrack, check_reversal,
    command_inputs, validate_track_command,
)
from app.domain.schedule.models import (
    ClaimEvidence, Dependency, DependencyMode, HoldResumePolicy, LogicalOccurrenceState,
    PatientEventStatus, ProcessDefinition, ProcessKind, RecurrenceRule, RecurrenceTermination,
    ScheduleTrack, TrackLink, TrackRebasePolicy,
)
from app.domain.schedule.validator import ScheduleValidator
from test_p4_dependency_recurrence import build, dates, of, offset_event, run

BASELINE = date(2026, 1, 1)


def event_after_actual_treatment(code, days, source="C1D1"):
    from app.domain.schedule.models import Event
    return Event(
        code=code, protocol_label=code, display_name=code.title(), event_type="VISIT",
        timing={"type": "OFFSET",
                "reference": {"kind": "EVENT", "event_code": source,
                              "temporal_basis": "ACTUAL_TREATMENT"},
                "offset": {"value": days, "unit": "DAY"}},
        dependencies=[Dependency(source_event_code=source, dependency_type="TEMPORAL")],
        dependency_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)


def program(*, lab_policy=TrackRebasePolicy.PREVIOUS_ACTUAL_OCCURRENCE, with_link=True, confirmed=True,
            treat_policy=TrackRebasePolicy.MANUAL_REBASE, hold=HoldResumePolicy.SHIFT_BY_HOLD,
            imaging_window=False, unscheduled=False):
    c1d1 = offset_event("C1D1", 7)
    c1d1.event_type = "TREATMENT"
    dose = offset_event("DOSE", 14, recurrence=RecurrenceRule(
        interval={"value": 7, "unit": "DAY"}, start_reference={"kind": "ANCHOR", "code": "BASELINE"},
        termination=RecurrenceTermination(type="COUNT", count=4),
        basis="FIRST_RESOLVED_OCCURRENCE", basis_status="STATED"))
    lab = event_after_actual_treatment("LAB_AFTER_TX", 7)
    imaging = offset_event("IMAGING_BASELINE", 30)
    if imaging_window:
        from app.domain.schedule.timing import Window
        imaging.window = Window(before={"value": 3, "unit": "DAY"}, after={"value": 3, "unit": "DAY"})
    events = [c1d1, dose, lab, imaging]
    if unscheduled:
        from app.domain.schedule.models import Event
        from app.domain.schedule.timing import ProtocolDefinedTiming
        events.append(Event(
            code="UNPLANNED_VISIT", protocol_label="UNPLANNED", display_name="Unplanned Visit",
            event_type="UNSCHEDULED", timing=ProtocolDefinedTiming(), activation="ON_DEMAND",
            track_scope="GLOBAL"))
    schedule = build(events)
    state = "CONFIRMED" if confirmed else "PROPOSED"
    treat = ScheduleTrack(code="TREATMENT", track_type="TREATMENT", display_name="Treatment",
                          anchor_code="BASELINE", rebase_policy=treat_policy,
                          process_code="TREATMENT_PROC", stoppable=True, review_state=state)
    labs = ScheduleTrack(code="LABS", track_type="LABORATORY", display_name="Laboratory",
                         anchor_code="BASELINE", rebase_policy=lab_policy, review_state=state)
    imag = ScheduleTrack(code="IMAGING", track_type="IMAGING", display_name="Imaging",
                         anchor_code="BASELINE", rebase_policy=TrackRebasePolicy.FIXED_FROM_ANCHOR,
                         review_state=state)
    schedule.tracks = [treat, labs, imag]
    for code, track in (("C1D1", treat), ("DOSE", treat), ("LAB_AFTER_TX", labs),
                        ("IMAGING_BASELINE", imag)):
        next(item for item in schedule.events if item.code == code).track_id = track.id
        next(item for item in schedule.events if item.code == code).track_scope = "TRACK"
    evidence_id = schedule.evidence[0].id
    if imaging_window:
        schedule.claim_evidence.append(ClaimEvidence(
            evidence_id=evidence_id, claim_type="WINDOW", claim_entity_type="EVENT",
            claim_entity_id=imaging.id, claim_path="window", confidence=0.9))
    process = ProcessDefinition(
        code="TREATMENT_PROC", display_name="Treatment", kind=ProcessKind.INTERVENTION,
        event_codes=["C1D1", "DOSE"], hold_resume_policy=hold, evidence_refs=[evidence_id])
    schedule.processes = [process]
    schedule.claim_evidence.append(ClaimEvidence(
        evidence_id=evidence_id, claim_type="PROCESS", claim_entity_type="PROCESS",
        claim_entity_id=process.id, claim_path="definition", confidence=0.9))
    if with_link:
        schedule.track_links = [TrackLink(
            source_track_code="TREATMENT", target_track_code="LABS", relation="FOLLOWS_ACTUAL",
            review_state=state)]
    return schedule


def blocking(schedule):
    return {issue.issue_code for issue in ScheduleValidator().validate(schedule) if issue.blocking}


# ============================================================ P5-01 membership

def test_a_confirmed_track_aware_schedule_has_no_blockers():
    assert blocking(program()) == set()


def test_legacy_schedule_without_tracks_is_untouched_and_unclassified():
    legacy = build([offset_event("A", 1), offset_event("B", 2)])
    assert blocking(legacy) == set()
    result = run(legacy)
    assert "track" not in of(result, "A")[0].explanation


def test_unclassified_event_in_a_track_aware_schedule_blocks_with_a_path():
    schedule = program()
    extra = offset_event("EXTRA", 40)
    schedule.events.append(extra)
    issues = [i for i in ScheduleValidator().validate(schedule) if i.issue_code == "TRACK_MEMBERSHIP_MISSING"]
    assert issues and issues[0].details["field_path"] == "events/EXTRA/track_id"
    extra.track_scope = "GLOBAL"                       # explicit global classification is fine
    assert "TRACK_MEMBERSHIP_MISSING" not in blocking(schedule)


def test_foreign_or_missing_track_reference_blocks():
    schedule = program()
    next(e for e in schedule.events if e.code == "IMAGING_BASELINE").track_id = uuid4()
    assert "TRACK_REFERENCE_UNKNOWN" in blocking(schedule)


def test_unresolved_or_unconfirmed_policy_blocks_and_is_never_inferred_from_the_label():
    schedule = program()
    schedule.tracks[2].rebase_policy = None                      # "Imaging" says nothing by itself
    assert "TRACK_POLICY_UNRESOLVED" in blocking(schedule)
    assert "TRACK_NOT_CONFIRMED" in blocking(program(confirmed=False))


def test_cross_track_reference_needs_an_explicit_confirmed_link():
    assert "TRACK_CROSS_REFERENCE_UNDECLARED" in blocking(program(with_link=False))
    assert "TRACK_CROSS_REFERENCE_UNDECLARED" not in blocking(program())


def test_actual_reference_inside_a_fixed_track_conflicts_with_its_policy():
    schedule = program(lab_policy=TrackRebasePolicy.NO_REBASE)
    assert "TRACK_POLICY_BASIS_CONFLICT" in blocking(schedule)


def test_recurrence_basis_must_compose_with_the_track_policy():
    schedule = program(treat_policy=TrackRebasePolicy.PREVIOUS_ACTUAL_OCCURRENCE)
    conflicts = [i for i in ScheduleValidator().validate(schedule)
                 if i.issue_code == "TRACK_POLICY_BASIS_CONFLICT"]
    assert conflicts and conflicts[0].details["recurrence_basis"] == "FIRST_RESOLVED_OCCURRENCE"


def test_a_pause_process_must_cover_exactly_the_tracks_events():
    schedule = program()
    schedule.processes[0].event_codes = ["C1D1"]
    assert "TRACK_PROCESS_MISMATCH" in blocking(schedule)
    schedule.tracks[0].process_code = "NOPE"
    assert "TRACK_PROCESS_UNKNOWN" in blocking(schedule)


def test_tracks_round_trip_through_canonical_json():
    from app.domain.schedule.models import UniversalSchedule
    schedule = program()
    restored = UniversalSchedule.model_validate_json(schedule.model_dump_json())
    assert [t.code for t in restored.tracks] == ["TREATMENT", "LABS", "IMAGING"]
    assert restored.events[0].track_scope == "TRACK" and restored.events[0].track_id == schedule.tracks[0].id
    assert restored.track_links[0].relation == "FOLLOWS_ACTUAL"


# =================================== P5-02: delay moves treatment-tied labs, not imaging

def treatment_history(actual_treatment):
    return [LogicalOccurrenceState(
        event_code="C1D1", occurrence_index=0, actual_visit_date=date(2026, 1, 8),
        actual_treatment_date=actual_treatment)]


def test_treatment_delay_moves_only_the_labs_that_count_from_actual_treatment():
    schedule = program()
    on_time = run(schedule, history=treatment_history(date(2026, 1, 8)))
    delayed = run(schedule, history=treatment_history(date(2026, 1, 15)))
    assert dates(on_time, "LAB_AFTER_TX") == [date(2026, 1, 15)]
    assert dates(delayed, "LAB_AFTER_TX") == [date(2026, 1, 22)]           # + 7 d of delay
    assert dates(on_time, "IMAGING_BASELINE") == dates(delayed, "IMAGING_BASELINE") == [date(2026, 1, 31)]


def test_every_occurrence_says_which_track_and_why_it_moved_or_did_not():
    delayed = run(program(), history=treatment_history(date(2026, 1, 15)))
    lab = of(delayed, "LAB_AFTER_TX")[0].explanation["track"]
    assert lab["code"] == "LABS" and lab["movement"] == "FOLLOWS_ACTUAL"
    assert "actual treatment of C1D1" in lab["reason"]
    imaging = of(delayed, "IMAGING_BASELINE")[0].explanation["track"]
    assert imaging["code"] == "IMAGING" and imaging["rebase_policy"] == "FIXED_FROM_ANCHOR"
    assert imaging["movement"] == "FIXED" and "no actual date of another visit moves it" in imaging["reason"]


# ====================================================== P5-02: MANUAL_REBASE scope

def entry(kind, sequence, effective, payload, **extra):
    return LedgerEntry(id=f"cmd-{sequence}", type=kind, payload=payload, sequence=sequence,
                       effective_at=effective, **extra)


def with_inputs(schedule, ledger):
    inputs = command_inputs(schedule, ledger)
    return dict(process_transitions=inputs.process_transitions, track_rebases=inputs.track_rebases,
                occurrence_overrides=inputs.occurrence_overrides,
                occurrence_cancellations=inputs.occurrence_cancellations)


def test_manual_rebase_moves_only_the_selected_track():
    schedule = program()
    rebase = entry(CommandType.REBASE_TRACK, 1, date(2026, 1, 5),
                   {"track_code": "TREATMENT", "anchor_code": "BASELINE", "new_value": "2026-02-01"})
    moved = run(schedule, **with_inputs(schedule, [rebase]))
    assert dates(moved, "C1D1") == [date(2026, 2, 8)]                       # 1 Feb + 7 d
    assert dates(moved, "DOSE")[0] == date(2026, 2, 15)
    assert dates(moved, "IMAGING_BASELINE") == [date(2026, 1, 31)]          # other track: fixed
    assert of(moved, "C1D1")[0].explanation["track"]["rebase_policy"] == "MANUAL_REBASE"


def test_rebase_is_refused_for_a_track_whose_policy_forbids_it():
    schedule = program()
    problems = validate_track_command(
        schedule, [], RebaseTrack(track_code="IMAGING", anchor_code="BASELINE", new_value=date(2026, 2, 1)))
    assert [p["code"] for p in problems] == ["REBASE_NOT_ALLOWED"]
    wrong = validate_track_command(
        schedule, [], RebaseTrack(track_code="TREATMENT", anchor_code="OTHER", new_value=date(2026, 2, 1)))
    assert [p["code"] for p in wrong] == ["REBASE_ANCHOR_MISMATCH"]


def test_unconfirmed_track_cannot_be_commanded():
    problems = validate_track_command(program(confirmed=False), [], PauseTrack(track_code="TREATMENT"))
    assert [p["code"] for p in problems] == ["TRACK_POLICY_UNRESOLVED"]


# ================================================ P5-02/03: pause / resume / stop

def test_pause_resume_shift_by_hold_moves_the_selected_track_only():
    schedule = program(hold=HoldResumePolicy.SHIFT_BY_HOLD)
    ledger = [
        entry(CommandType.PAUSE_TRACK, 1, date(2026, 1, 20), {"track_code": "TREATMENT"}),
        entry(CommandType.RESUME_TRACK, 2, date(2026, 2, 3), {"track_code": "TREATMENT"}),
    ]
    assert dates(run(schedule), "DOSE") == [
        date(2026, 1, 15), date(2026, 1, 22), date(2026, 1, 29), date(2026, 2, 5)]
    result = run(schedule, **with_inputs(schedule, ledger))
    # the 15 Jan dose precedes the hold and is kept; the rest move later by the 14-day hold
    assert dates(result, "DOSE") == [
        date(2026, 1, 15), date(2026, 2, 5), date(2026, 2, 12), date(2026, 2, 19)]
    assert dates(result, "IMAGING_BASELINE") == [date(2026, 1, 31)]
    assert dates(result, "LAB_AFTER_TX") == dates(run(schedule), "LAB_AFTER_TX")


def test_resume_policy_left_to_a_person_must_be_chosen():
    schedule = program(hold=HoldResumePolicy.WAIT_FOR_DECISION)
    paused = [entry(CommandType.PAUSE_TRACK, 1, date(2026, 1, 20), {"track_code": "TREATMENT"})]
    problems = validate_track_command(schedule, paused, ResumeTrack(track_code="TREATMENT"))
    assert [p["code"] for p in problems] == ["RESUME_POLICY_REQUIRED"]
    chosen = validate_track_command(
        schedule, paused, ResumeTrack(track_code="TREATMENT", resume_policy="NOMINAL"))
    assert chosen == []


def test_track_state_machine_rules():
    schedule = program()
    paused = [entry(CommandType.PAUSE_TRACK, 1, date(2026, 1, 20), {"track_code": "TREATMENT"})]
    assert [p["code"] for p in validate_track_command(
        schedule, paused, PauseTrack(track_code="TREATMENT"))] == ["TRACK_NOT_ACTIVE"]
    assert [p["code"] for p in validate_track_command(
        schedule, [], ResumeTrack(track_code="TREATMENT"))] == ["TRACK_NOT_HELD"]
    schedule.tracks[0].stoppable = None
    assert [p["code"] for p in validate_track_command(
        schedule, [], StopTrack(track_code="TREATMENT"))] == ["TRACK_NOT_STOPPABLE"]


def test_stop_ends_the_tracks_future_obligations_and_keeps_other_tracks():
    schedule = program()
    stop = entry(CommandType.STOP_TRACK, 1, date(2026, 1, 10), {"track_code": "TREATMENT"})
    result = run(schedule, **with_inputs(schedule, [stop]))
    assert [item.status for item in of(result, "DOSE")] == [PatientEventStatus.CANCELLED] * 4
    assert [item.status for item in of(result, "C1D1")] == [PatientEventStatus.RESOLVED]   # before the stop
    assert dates(result, "IMAGING_BASELINE") == [date(2026, 1, 31)]
    assert [item.status for item in of(result, "IMAGING_BASELINE")] == [PatientEventStatus.RESOLVED]


# ================================================ P5-03: overrides and cancellations

def test_override_sits_beside_the_protocol_target_and_never_replaces_it():
    schedule = program()
    override = entry(
        CommandType.OVERRIDE_OCCURRENCE, 1, date(2026, 1, 12),
        {"logical_key": "IMAGING_BASELINE:0", "target_date": "2026-02-20", "authority": "PI Dr A"},
        authority="PI Dr A", reason="scanner unavailable")
    result = run(schedule, **with_inputs(schedule, [override]))
    item = of(result, "IMAGING_BASELINE")[0]
    assert item.timing.nominal_start == date(2026, 1, 31)              # protocol target unchanged
    operational = item.explanation["operational"]
    assert operational["protocol"]["target"] == "2026-01-31"
    assert operational["override"]["target_date"] == "2026-02-20"
    # no window is stated for this visit: the verdict is "cannot be evaluated"
    assert operational["override"]["outside_protocol_window"] is None
    assert operational["override"]["protocol_window"] == "UNKNOWN"
    assert operational["override"]["protocol_window_unchanged"] is True
    assert operational["effective_date"] == "2026-02-20"
    assert operational["override"]["authority"] == "PI Dr A"


def test_cancellation_keeps_the_target_and_never_cancels_a_recorded_outcome():
    schedule = program()
    cancel = entry(CommandType.CANCEL_OCCURRENCE, 1, date(2026, 1, 12),
                   {"logical_key": "IMAGING_BASELINE:0"}, reason="patient withdrew from imaging")
    item = of(run(schedule, **with_inputs(schedule, [cancel])), "IMAGING_BASELINE")[0]
    assert item.status == PatientEventStatus.CANCELLED
    assert item.timing.nominal_start == date(2026, 1, 31)
    assert item.explanation["operational"]["cancelled"]["reason"] == "patient withdrew from imaging"


# ============================================================== P5-03: reversal rules

def test_reversal_restores_the_effect_through_the_evaluator_not_a_snapshot():
    schedule = program()
    override = entry(CommandType.OVERRIDE_OCCURRENCE, 1, date(2026, 1, 12),
                     {"logical_key": "IMAGING_BASELINE:0", "target_date": "2026-02-20",
                      "authority": "PI"}, authority="PI", reason="r")
    reverse = LedgerEntry(id="cmd-2", type=CommandType.REVERSE, payload={"reverses_command_id": "cmd-1"},
                          sequence=2, effective_at=date(2026, 1, 13), reverses_command_id="cmd-1")
    assert check_reversal([override], "cmd-1") == []
    item = of(run(schedule, **with_inputs(schedule, [override, reverse])), "IMAGING_BASELINE")[0]
    assert "operational" not in item.explanation              # effect gone; ledger keeps both
    assert check_reversal([override, reverse], "cmd-1")[0]["code"] == "ALREADY_REVERSED"


@pytest.mark.parametrize("target_type,expected", [
    (CommandType.RECORD_ACTUAL, "FACT_NOT_REVERSIBLE"),
    (CommandType.MARK_MISSED, "FACT_NOT_REVERSIBLE"),
    (CommandType.REVERSE, "REVERSAL_OF_REVERSAL"),
])
def test_facts_and_reversals_are_not_reversible(target_type, expected):
    ledger = [LedgerEntry(id="x", type=target_type, payload={}, sequence=1, effective_at=BASELINE)]
    assert check_reversal(ledger, "x")[0]["code"] == expected
    assert check_reversal(ledger, "missing")[0]["code"] == "REVERSAL_TARGET_UNKNOWN"


def test_reversal_is_refused_while_a_newer_command_applies_to_the_same_track():
    ledger = [
        entry(CommandType.PAUSE_TRACK, 1, date(2026, 1, 20), {"track_code": "TREATMENT"}),
        entry(CommandType.RESUME_TRACK, 2, date(2026, 2, 3), {"track_code": "TREATMENT"}),
    ]
    refused = check_reversal(ledger, "cmd-1")
    assert refused[0]["code"] == "NEWER_COMMAND_DEPENDS" and "cmd-2" in refused[0]["message"]
    assert check_reversal(ledger, "cmd-2") == []


# ================================================== effective period / chronology (audit 1, 2)

def _targets(result):
    return {code: dates(result, code) for code in ("C1D1", "DOSE", "LAB_AFTER_TX", "IMAGING_BASELINE")}


def test_a_rebase_effective_after_the_evaluated_period_changes_nothing_in_it():
    schedule = program()
    future = entry(CommandType.REBASE_TRACK, 1, date(2027, 1, 1),
                   {"track_code": "TREATMENT", "anchor_code": "BASELINE", "new_value": "2026-01-08"})
    plain = _targets(run(schedule))
    moved = _targets(run(schedule, **with_inputs(schedule, [future])))
    assert moved == plain                     # nothing in the 2026 horizon moves
    assert dates(run(schedule), "C1D1") == [date(2026, 1, 8)]


def test_a_rebase_moves_only_occurrences_inside_its_effective_period():
    schedule = program()
    mid = entry(CommandType.REBASE_TRACK, 1, date(2026, 1, 25),
                {"track_code": "TREATMENT", "anchor_code": "BASELINE", "new_value": "2026-01-08"})
    base = dates(run(schedule), "DOSE")        # 15, 22, 29 Jan, 5 Feb
    moved = dates(run(schedule, **with_inputs(schedule, [mid])), "DOSE")
    # 15 Jan and 22 Jan precede the boundary (stable dates) and keep their targets
    assert moved[:2] == base[:2] == [date(2026, 1, 15), date(2026, 1, 22)]
    assert moved[2] == date(2026, 1, 29) + (date(2026, 1, 8) - BASELINE)   # rebased: +7 d
    assert dates(run(schedule, **with_inputs(schedule, [mid])), "C1D1") == [date(2026, 1, 8)]


def test_two_rebases_apply_each_to_its_own_period():
    schedule = program()
    first = entry(CommandType.REBASE_TRACK, 1, date(2026, 1, 1),
                  {"track_code": "TREATMENT", "anchor_code": "BASELINE", "new_value": "2026-01-02"})
    second = entry(CommandType.REBASE_TRACK, 2, date(2026, 1, 25),
                   {"track_code": "TREATMENT", "anchor_code": "BASELINE", "new_value": "2026-01-15"})
    doses = dates(run(schedule, **with_inputs(schedule, [first, second])), "DOSE")
    assert doses[0] == date(2026, 1, 16)       # first rebase: +1 d from base 15 Jan
    assert doses[2] == date(2026, 1, 29) + (date(2026, 1, 15) - BASELINE)


def test_a_reversal_ends_the_effect_at_its_own_date_and_keeps_the_earlier_period():
    schedule = program()
    rebase = entry(CommandType.REBASE_TRACK, 1, date(2026, 1, 1),
                   {"track_code": "TREATMENT", "anchor_code": "BASELINE", "new_value": "2026-01-08"})
    reverse = LedgerEntry(id="cmd-2", type=CommandType.REVERSE, payload={}, sequence=2,
                          effective_at=date(2026, 1, 25), reverses_command_id="cmd-1")
    doses = dates(run(schedule, **with_inputs(schedule, [rebase, reverse])), "DOSE")
    assert doses[0] == date(2026, 1, 22)       # 15 Jan + 7: before 25 Jan, still rebased
    assert doses[3] == date(2026, 2, 5)        # after the reversal: back to the protocol target
    retracted = LedgerEntry(id="cmd-2", type=CommandType.REVERSE, payload={}, sequence=2,
                            effective_at=date(2026, 1, 1), reverses_command_id="cmd-1")
    assert dates(run(schedule, **with_inputs(schedule, [rebase, retracted])), "DOSE") == \
        dates(run(schedule), "DOSE")


def test_a_resume_dated_before_the_pause_is_refused_not_accepted_as_ordinary():
    schedule = program()
    pause = entry(CommandType.PAUSE_TRACK, 1, date(2026, 1, 20), {"track_code": "TREATMENT"})
    refused = validate_track_command(
        schedule, [pause], ResumeTrack(track_code="TREATMENT"), date(2026, 1, 10))
    assert [p["code"] for p in refused] == ["TRACK_NOT_HELD"]
    assert "pause it ends" in refused[0]["message"]
    assert validate_track_command(
        schedule, [pause], ResumeTrack(track_code="TREATMENT"), date(2026, 1, 20)) == []
    assert validate_track_command(
        schedule, [pause], ResumeTrack(track_code="TREATMENT"), date(2026, 2, 3)) == []


def test_a_backdated_pause_may_not_break_a_recorded_timeline():
    schedule = program()
    ledger = [entry(CommandType.PAUSE_TRACK, 1, date(2026, 1, 20), {"track_code": "TREATMENT"}),
              entry(CommandType.RESUME_TRACK, 2, date(2026, 2, 3), {"track_code": "TREATMENT"})]
    inside_hold = validate_track_command(
        schedule, ledger, PauseTrack(track_code="TREATMENT"), date(2026, 1, 25))
    assert [p["code"] for p in inside_hold] == ["TRACK_NOT_ACTIVE"]
    before_all = validate_track_command(
        schedule, ledger, PauseTrack(track_code="TREATMENT"), date(2026, 1, 5))
    assert before_all and before_all[0]["code"] in ("TRACK_NOT_ACTIVE", "TRACK_NOT_HELD")


def test_a_future_dated_reversal_of_a_transition_is_refused_with_the_alternative():
    pause = entry(CommandType.PAUSE_TRACK, 1, date(2026, 1, 20), {"track_code": "TREATMENT"})
    refused = check_reversal([pause], "cmd-1", date(2026, 2, 1))
    assert refused[0]["code"] == "REVERSAL_PERIOD_UNSUPPORTED"
    assert check_reversal([pause], "cmd-1", date(2026, 1, 20)) == []


# ---- dependency selector semantics for new/edited drafts ---------------------------------

def test_a_draft_with_the_ambiguous_current_selector_is_blocked_with_a_path():
    schedule = program()
    lab = next(e for e in schedule.events if e.code == "LAB_AFTER_TX")
    lab.dependencies[0] = lab.dependencies[0].model_copy(update={"source_occurrence": "CURRENT"})
    blockers = [i for i in ScheduleValidator().validate(schedule) if i.blocking]
    hit = [i for i in blockers if i.issue_code == "DEPENDENCY_SELECTOR_AMBIGUOUS"]
    assert hit and hit[0].details["field_path"].endswith("/dependencies/0/source_occurrence")
    lab.dependencies[0] = lab.dependencies[0].model_copy(update={"source_occurrence": "FIRST"})
    assert "DEPENDENCY_SELECTOR_AMBIGUOUS" not in blocking(schedule)


def test_an_approved_legacy_schedule_with_current_keeps_evaluating():
    schedule = program()
    lab = next(e for e in schedule.events if e.code == "LAB_AFTER_TX")
    lab.dependencies[0] = lab.dependencies[0].model_copy(update={"source_occurrence": "CURRENT"})
    assert dates(run(schedule, history=treatment_history(date(2026, 1, 15))), "LAB_AFTER_TX")
