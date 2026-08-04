"""Deterministic expansion of a declared protocol structure into visit templates.

These tests are modelled on the real protocols in the reference corpus and run
fully offline — no API key, no database, no network. `protocol_extraction`
imports `anthropic` lazily, so importing it here is safe.

Corpus cases represented below:
  * PICN (CLR_10_13)      — 'Cycle 2 & Next Cycles' collapsed column, cycle
                             length stated 27 pages away from the table
  * 48. Protocol.pdf      — cadence CHANGES mid-study, open-ended tail
                             ('every 6th week for Cycles 1-6 and every 8th
                             week thereafter')
  * Ipratropium crossover — periods + washout
  * Fever synopsis        — hour-level timepoints only
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402

from protocol_extraction import (  # noqa: E402
    OPEN_ENDED_CYCLE_CAP,
    ConditionalActivity,
    ExtractedSchedule,
    ExtractedVisit,
    RepeatMember,
    RepeatingBlock,
    expand_schedule,
)


def days(schedule):
    return [v.day_offset for v in schedule.visits]


def names(schedule):
    return [v.name for v in schedule.visits]


# ────────────────────────────── the PICN case ──────────────────────────────

def picn_schedule() -> ExtractedSchedule:
    """CLR_10_13: screening, a fully-printed cycle 1, then 'Cycle 2 & Next Cycles'.

    Cycle length 21 days and 'maximum 6 cycles' come from the treatment-plan
    prose, not the appendix table. Intra-cycle visits sit 7 days apart.
    """
    members = [
        RepeatMember(name_template="Cycle {cycle} Day 1", day_within_cycle=0,
                     visit_type="Treatment", activities=["Study drug administration"]),
        RepeatMember(name_template="Cycle {cycle} Intra-cycle Visit 1", day_within_cycle=7,
                     visit_type="Treatment", activities=["Hematology"]),
        RepeatMember(name_template="Cycle {cycle} Intra-cycle Visit 2", day_within_cycle=14,
                     visit_type="Treatment", activities=["Hematology"]),
        RepeatMember(
            name_template="Cycle {cycle} Intra-cycle Visit 3", day_within_cycle=18,
            visit_type="Treatment", activities=["Hematology"],
            # Imaging only after cycles 2, 4 and 6 (protocol pp. 4, 24, 30, 35).
            conditional_activities=[ConditionalActivity(name="Imaging (CT/MRI)",
                                                        cycles=[2, 4, 6])]),
    ]
    return ExtractedSchedule(
        schedule_kind="cyclic",
        total_cycles=6,
        visits=[
            ExtractedVisit(name="Screening", day_offset=-14, visit_type="Screening",
                           activities=["Informed consent", "Medical history"]),
            ExtractedVisit(name="Cycle 1 Day 1", day_offset=0, visit_type="Treatment",
                           activities=["Randomization", "Study drug administration"]),
            ExtractedVisit(name="Cycle 1 Intra-cycle Visit 1", day_offset=7),
            ExtractedVisit(name="Cycle 1 Intra-cycle Visit 2", day_offset=14),
            ExtractedVisit(name="Cycle 1 Intra-cycle Visit 3", day_offset=18),
        ],
        repeating_blocks=[
            RepeatingBlock(from_cycle=2, to_cycle=6, cycle_length_days=21,
                           first_cycle_start_day=21, members=members),
        ],
    )


def test_picn_collapsed_column_expands_to_the_full_schedule():
    """The whole point: 9 printed columns must become 25 real visits."""
    out = expand_schedule(picn_schedule())
    # 1 screening + 4 visits x 6 cycles = 25
    assert len(out.visits) == 25
    assert out.visits[0].name == "Screening"
    assert out.visits[0].day_offset == -14


def test_picn_cycle_arithmetic_is_correct():
    out = expand_schedule(picn_schedule())
    by_name = {v.name: v.day_offset for v in out.visits}
    # Cycle C starts at (C-1)*21; intra-cycle visits at +7/+14/+18.
    assert by_name["Cycle 1 Day 1"] == 0
    assert by_name["Cycle 2 Day 1"] == 21
    assert by_name["Cycle 3 Day 1"] == 42
    assert by_name["Cycle 6 Day 1"] == 105
    assert by_name["Cycle 6 Intra-cycle Visit 3"] == 123


def test_picn_visits_come_out_in_chronological_order():
    out = expand_schedule(picn_schedule())
    ordered = [d for d in days(out) if d is not None]
    assert ordered == sorted(ordered)


def test_conditional_assessment_lands_only_in_its_cycles():
    """Imaging happens after cycles 2, 4, 6 — not every cycle."""
    out = expand_schedule(picn_schedule())
    with_imaging = {v.name for v in out.visits
                    if any("Imaging" in a for a in v.activities)}
    assert with_imaging == {
        "Cycle 2 Intra-cycle Visit 3",
        "Cycle 4 Intra-cycle Visit 3",
        "Cycle 6 Intra-cycle Visit 3",
    }


def test_recurring_assessment_lands_in_every_cycle():
    out = expand_schedule(picn_schedule())
    hematology = [v for v in out.visits if "Hematology" in v.activities]
    assert len(hematology) == 15          # 3 intra-cycle visits x 5 expanded cycles


# ─────────────────── changing cadence + open-ended tails ───────────────────

def test_two_blocks_model_a_cadence_change():
    """'every 6th week for Cycles 1-6 and every 8th week thereafter' (48. Protocol)."""
    sched = ExtractedSchedule(
        schedule_kind="cyclic",
        repeating_blocks=[
            RepeatingBlock(from_cycle=1, to_cycle=6, cycle_length_days=42,
                           first_cycle_start_day=0,
                           members=[RepeatMember(name_template="Cycle {cycle} Imaging",
                                                 day_within_cycle=0)]),
            RepeatingBlock(from_cycle=7, to_cycle=9, cycle_length_days=56,
                           first_cycle_start_day=252,
                           members=[RepeatMember(name_template="Cycle {cycle} Imaging",
                                                 day_within_cycle=0)]),
        ],
    )
    out = expand_schedule(sched)
    assert days(out) == [0, 42, 84, 126, 168, 210, 252, 308, 364]


def test_open_ended_block_is_bounded_and_flagged():
    sched = ExtractedSchedule(
        repeating_blocks=[
            RepeatingBlock(from_cycle=1, to_cycle=None, cycle_length_days=28,
                           first_cycle_start_day=0,
                           members=[RepeatMember(name_template="Cycle {cycle} Day 1",
                                                 day_within_cycle=0)]),
        ],
    )
    out = expand_schedule(sched)
    assert len(out.visits) == OPEN_ENDED_CYCLE_CAP
    assert any("open-ended" in a for a in out.assumptions)


def test_open_ended_block_prefers_a_stated_total_cycle_count():
    sched = ExtractedSchedule(
        total_cycles=4,
        repeating_blocks=[
            RepeatingBlock(from_cycle=1, to_cycle=None, cycle_length_days=28,
                           first_cycle_start_day=0,
                           members=[RepeatMember(name_template="Cycle {cycle} Day 1",
                                                 day_within_cycle=0)]),
        ],
    )
    out = expand_schedule(sched)
    assert len(out.visits) == 4
    assert any("maximum of 4 cycles" in a for a in out.assumptions)


# ───────────────────────────── relative anchors ─────────────────────────────

def test_relative_visit_resolves_against_its_target():
    """'scheduled within 3 days after intra-cycle visit 3' (PICN p24)."""
    sched = ExtractedSchedule(visits=[
        ExtractedVisit(name="Intra-cycle Visit 3", day_offset=18),
        ExtractedVisit(name="Post-cycle Review",
                       relative_to="Intra-cycle Visit 3", relative_offset_days=3),
    ])
    out = expand_schedule(sched)
    assert {v.name: v.day_offset for v in out.visits}["Post-cycle Review"] == 21


def test_relative_chain_resolves_transitively():
    sched = ExtractedSchedule(visits=[
        ExtractedVisit(name="A", day_offset=10),
        ExtractedVisit(name="B", relative_to="A", relative_offset_days=5),
        ExtractedVisit(name="C", relative_to="B", relative_offset_days=7),
    ])
    out = expand_schedule(sched)
    assert {v.name: v.day_offset for v in out.visits} == {"A": 10, "B": 15, "C": 22}


def test_relative_target_matching_ignores_case_and_padding():
    sched = ExtractedSchedule(visits=[
        ExtractedVisit(name="End of Treatment", day_offset=100),
        ExtractedVisit(name="Safety Follow-up",
                       relative_to="  end of treatment ", relative_offset_days=28),
    ])
    out = expand_schedule(sched)
    assert {v.name: v.day_offset for v in out.visits}["Safety Follow-up"] == 128


def test_unresolvable_relative_visit_is_kept_and_flagged():
    """A visit we cannot date must never be silently dropped."""
    sched = ExtractedSchedule(visits=[
        ExtractedVisit(name="Follow-up", relative_to="Nonexistent Visit",
                       relative_offset_days=7),
    ])
    out = expand_schedule(sched)
    assert names(out) == ["Follow-up"]
    assert out.visits[0].day_offset is None
    assert any("no resolvable date" in a for a in out.assumptions)


def test_circular_relative_references_terminate():
    sched = ExtractedSchedule(visits=[
        ExtractedVisit(name="A", relative_to="B", relative_offset_days=1),
        ExtractedVisit(name="B", relative_to="A", relative_offset_days=1),
    ])
    out = expand_schedule(sched)          # must not hang
    assert all(v.day_offset is None for v in out.visits)
    assert len(out.visits) == 2


# ──────────────────────── undated + ordering behaviour ────────────────────────

def test_undated_visits_are_kept_and_sorted_last():
    """Early Termination / Unscheduled have no day but are real visits."""
    sched = ExtractedSchedule(visits=[
        ExtractedVisit(name="Early Termination", visit_type="Early Termination"),
        ExtractedVisit(name="Screening", day_offset=-7),
        ExtractedVisit(name="Unscheduled", visit_type="Unscheduled"),
        ExtractedVisit(name="Baseline", day_offset=0),
    ])
    out = expand_schedule(sched)
    assert names(out)[:2] == ["Screening", "Baseline"]
    assert set(names(out)[2:]) == {"Early Termination", "Unscheduled"}
    assert all(v.day_offset is None for v in out.visits[2:])


def test_negative_screening_days_sort_before_baseline():
    sched = ExtractedSchedule(visits=[
        ExtractedVisit(name="Baseline", day_offset=0),
        ExtractedVisit(name="Screening", day_offset=-28),
    ])
    assert names(expand_schedule(sched)) == ["Screening", "Baseline"]


def test_intra_day_timepoints_order_within_the_same_day():
    """Fever synopsis: the only schedule is hour-level."""
    sched = ExtractedSchedule(
        schedule_kind="intra_day",
        visits=[
            ExtractedVisit(name="Hour 26", day_offset=1, hour_offset=26),
            ExtractedVisit(name="Hour -4 to Hour 0", day_offset=0,
                           hour_offset=-4, hour_end=0),
            ExtractedVisit(name="Hour 0 to Hour 24", day_offset=0, hour_offset=0),
        ],
    )
    out = expand_schedule(sched)
    assert names(out) == ["Hour -4 to Hour 0", "Hour 0 to Hour 24", "Hour 26"]


def test_crossover_periods_are_preserved():
    """Ipratropium: periods and washouts with continuous absolute offsets."""
    sched = ExtractedSchedule(
        schedule_kind="crossover",
        visits=[
            ExtractedVisit(name="Period 1 Check-in", day_offset=0, period="Period 1"),
            ExtractedVisit(name="Period 1 Day 1", day_offset=1, period="Period 1"),
            ExtractedVisit(name="Washout 1", day_offset=2, day_end=8, period="Washout 1"),
            ExtractedVisit(name="Period 2 Check-in", day_offset=9, period="Period 2"),
        ],
    )
    out = expand_schedule(sched)
    assert [v.period for v in out.visits] == [
        "Period 1", "Period 1", "Washout 1", "Period 2"]
    assert out.visits[2].day_end == 8


# ─────────────────────────── robustness / guards ───────────────────────────

def test_duplicate_visits_are_collapsed():
    """A model that both enumerates cycle 2 and describes it must not double-book."""
    sched = ExtractedSchedule(
        visits=[ExtractedVisit(name="Cycle 2 Day 1", day_offset=21)],
        repeating_blocks=[
            RepeatingBlock(from_cycle=2, to_cycle=2, cycle_length_days=21,
                           first_cycle_start_day=21,
                           members=[RepeatMember(name_template="Cycle {cycle} Day 1",
                                                 day_within_cycle=0)]),
        ],
    )
    out = expand_schedule(sched)
    assert len(out.visits) == 1


def test_nonpositive_cycle_length_is_rejected_not_expanded():
    sched = ExtractedSchedule(repeating_blocks=[
        RepeatingBlock(from_cycle=1, to_cycle=3, cycle_length_days=0,
                       first_cycle_start_day=0,
                       members=[RepeatMember(name_template="Cycle {cycle}",
                                             day_within_cycle=0)]),
    ])
    out = expand_schedule(sched)
    assert out.visits == []
    assert any("not a positive number of days" in a for a in out.assumptions)


def test_block_with_no_members_is_rejected():
    sched = ExtractedSchedule(repeating_blocks=[
        RepeatingBlock(from_cycle=1, to_cycle=3, cycle_length_days=21,
                       first_cycle_start_day=0, members=[]),
    ])
    out = expand_schedule(sched)
    assert out.visits == []
    assert any("listed no visits" in a for a in out.assumptions)


def test_inverted_cycle_range_is_rejected():
    sched = ExtractedSchedule(repeating_blocks=[
        RepeatingBlock(from_cycle=5, to_cycle=2, cycle_length_days=21,
                       first_cycle_start_day=0,
                       members=[RepeatMember(name_template="Cycle {cycle}",
                                             day_within_cycle=0)]),
    ])
    out = expand_schedule(sched)
    assert out.visits == []
    assert any("before the first" in a for a in out.assumptions)


def test_member_template_without_a_placeholder_still_disambiguates():
    """Otherwise every cycle collapses into one visit at dedupe time."""
    sched = ExtractedSchedule(repeating_blocks=[
        RepeatingBlock(from_cycle=1, to_cycle=3, cycle_length_days=21,
                       first_cycle_start_day=0,
                       members=[RepeatMember(name_template="Treatment Visit",
                                             day_within_cycle=0)]),
    ])
    out = expand_schedule(sched)
    assert len(out.visits) == 3
    assert len(set(names(out))) == 3


def test_runaway_expansion_is_capped():
    sched = ExtractedSchedule(repeating_blocks=[
        RepeatingBlock(from_cycle=1, to_cycle=5000, cycle_length_days=1,
                       first_cycle_start_day=0,
                       members=[RepeatMember(name_template="Cycle {cycle}",
                                             day_within_cycle=0)]),
    ])
    out = expand_schedule(sched)
    assert len(out.visits) == 400
    assert any("expanded to" in a for a in out.assumptions)


def test_document_with_no_schedule_stays_empty():
    """A GCP checklist or consent form must produce nothing, not a guess."""
    out = expand_schedule(ExtractedSchedule(schedule_kind="none"))
    assert out.visits == []


def test_expansion_is_pure_and_repeatable():
    sched = picn_schedule()
    first = expand_schedule(sched)
    second = expand_schedule(sched)
    assert days(first) == days(second)
    assert names(first) == names(second)
    # the caller's object is untouched
    assert len(sched.visits) == 5
    assert len(sched.repeating_blocks) == 1


def test_model_assumptions_are_preserved_alongside_server_warnings():
    sched = ExtractedSchedule(
        assumptions=["Cycle length read from section 2.5."],
        repeating_blocks=[
            RepeatingBlock(from_cycle=1, to_cycle=None, cycle_length_days=21,
                           first_cycle_start_day=0,
                           members=[RepeatMember(name_template="Cycle {cycle}",
                                                 day_within_cycle=0)]),
        ],
    )
    out = expand_schedule(sched)
    assert "Cycle length read from section 2.5." in out.assumptions
    assert any("open-ended" in a for a in out.assumptions)


def test_blocks_are_consumed_by_expansion():
    out = expand_schedule(picn_schedule())
    assert out.repeating_blocks == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
