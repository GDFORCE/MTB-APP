"""Scenarios F2 and E9 through persistence (matrix 5 and 14, at service level).

F2: "Each cycle starts 21 days after Day 1 of the previous cycle." Cycle 1
Day 1 = Mon 05-Oct-2026; Cycle 2 actually given Mon 02-Nov (7 days late). The
actual starts come from visits the site RECORDED, not from a hand-built context.

E9: "Every 3 months for 2 years, then every 6 months to year 5, then yearly"
from end of therapy Mon 07-Dec-2026, persisted with a rolling horizon.
"""

from datetime import date

import pytest

from _engine_cases import anchor, approved, visit
from _scenario_harness import check_invariants, new_trial, reminder_keys

from app.domain.schedule.models import (
    CycleDependencyMode, CyclePlan, DependencyMode, RecurrencePlan, RecurrenceSegment,
    RecurrenceTermination, RepeatBlock, RollingHorizon,
)
from app.domain.schedule.timing import (
    AnchorReference, CycleSelector, CycleDayTiming, OffsetTiming, PositiveTemporalAmount,
    TemporalAmount, TimeUnit,
)

C1D1 = date(2026, 10, 5)
EOT = date(2026, 12, 7)


def cycle_schedule():
    events = [visit(f"DAY_{day}", CycleDayTiming(
        cycle_reference="CYCLE", cycle=CycleSelector(type="CURRENT"), day=day,
    ), event_type="TREATMENT") for day in (1, 8)]
    return approved(events, anchors=[anchor("C1D1")], repeat_blocks=[RepeatBlock(
        code="TREATMENT", protocol_label="Treatment", display_name="Treatment",
        event_codes=["DAY_1", "DAY_8"], dependency_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT,
        cycle_plan=CyclePlan(
            code="CYCLE", start_reference=AnchorReference(code="C1D1"),
            length=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
            dependency_mode=CycleDependencyMode.ACTUAL_PREVIOUS_CYCLE_START,
            start_event_code="DAY_1",
            termination=RecurrenceTermination(type="COUNT", count=6),
        ),
    )])


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


def cycle_rows(rows, code="DAY_1"):
    return sorted((row for key, row in rows.items() if key.startswith(f"{code}:")),
                  key=lambda row: row.occurrence_index)


def test_f2_each_cycle_follows_the_recorded_start_of_the_one_before(cleanup):
    trial = open_trial(cleanup, cycle_schedule())
    patient = trial.enrol("P-CYCLES", anchors={"C1D1": C1D1})

    # Nothing given yet: C1 and C2 have dates, C3 is blocked on C2's actual start.
    day_1 = cycle_rows(trial.rows(patient))
    assert [row.nominal_start_date for row in day_1[:2]] == [date(2026, 10, 5), date(2026, 10, 26)]
    assert day_1[2].status == "BLOCKED"
    assert day_1[2].dependency_result["waiting_for_actual"] == ["CYCLE_2_START"]
    assert len(day_1) == 3
    history = check_invariants(trial, patient, today=date(2026, 10, 1))

    # C1 given on time, C2 given 02-Nov (7 days late).
    trial.record_actual(patient, "DAY_1:0", C1D1)
    trial.record_actual(patient, "DAY_1:1", date(2026, 11, 2))
    trial.evaluate(patient)
    rows = trial.rows(patient)
    day_1 = cycle_rows(rows)
    assert day_1[2].status == "RESOLVED"
    assert day_1[2].nominal_start_date == date(2026, 11, 23)                  # C3
    assert day_1[2].generation_reason["cycle"]["start_basis"] == "FROM_ACTUAL_PREVIOUS_CYCLE"
    assert day_1[3].status == "BLOCKED"                                         # C4
    assert day_1[3].nominal_start_date is None
    assert day_1[3].dependency_result["waiting_for_actual"] == ["CYCLE_3_START"]
    # Days inside the late cycle count from its actual start.
    assert cycle_rows(rows, "DAY_8")[1].nominal_start_date == date(2026, 11, 9)
    # The completed C2 row keeps the date it was scheduled for; history does not move.
    assert day_1[1].status == "COMPLETED"
    assert day_1[1].nominal_start_date == date(2026, 10, 26)
    keys = reminder_keys(trial.projection(patient, date(2026, 11, 3)))
    assert "DAY_1:2" in keys and "DAY_1:3" not in keys
    history = check_invariants(trial, patient, today=date(2026, 11, 3), completed_before=history)

    # C3 actually given 23-Nov -> C4 is dated 14-Dec; C5 now waits for C4.
    trial.record_actual(patient, "DAY_1:2", date(2026, 11, 23))
    trial.evaluate(patient)
    day_1 = cycle_rows(trial.rows(patient))
    assert day_1[3].status == "RESOLVED"
    assert day_1[3].nominal_start_date == date(2026, 12, 14)
    assert day_1[4].status == "BLOCKED"
    assert day_1[4].dependency_result["waiting_for_actual"] == ["CYCLE_4_START"]
    check_invariants(trial, patient, today=date(2026, 11, 24), completed_before=history)


def months(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit=TimeUnit.MONTH)


def every(value: int, unit: TimeUnit = TimeUnit.MONTH) -> PositiveTemporalAmount:
    return PositiveTemporalAmount(value=value, unit=unit)


def follow_up_schedule():
    plan = RecurrencePlan(
        start_reference=AnchorReference(code="END_OF_THERAPY"), include_start=False,
        segments=[
            RecurrenceSegment(interval=every(3), end_offset=months(24), end_inclusive=True),
            RecurrenceSegment(interval=every(6), end_offset=months(60), end_inclusive=True),
            RecurrenceSegment(interval=every(1, TimeUnit.YEAR)),
        ],
        termination=RecurrenceTermination(type="HORIZON"),
    )
    return approved([visit("FOLLOW_UP", OffsetTiming(
        reference=AnchorReference(code="END_OF_THERAPY"), offset=months(3),
    ), recurrence_plan=plan)], anchors=[anchor("C1D1"), anchor("END_OF_THERAPY")])


SEGMENT_1 = [date(2027, 3, 7), date(2027, 6, 7), date(2027, 9, 7), date(2027, 12, 7),
             date(2028, 3, 7), date(2028, 6, 7), date(2028, 9, 7), date(2028, 12, 7)]
SEGMENT_2 = [date(2029, 6, 7), date(2029, 12, 7), date(2030, 6, 7), date(2030, 12, 7),
             date(2031, 6, 7), date(2031, 12, 7)]
SEGMENT_3 = [date(2032, 12, 7), date(2033, 12, 7)]


def follow_rows(rows):
    return sorted((row for key, row in rows.items() if key.startswith("FOLLOW_UP:")),
                  key=lambda row: row.occurrence_index)


def test_e9_phased_follow_up_through_persistence(cleanup):
    trial = open_trial(cleanup, follow_up_schedule())
    horizon = date(2033, 12, 31)
    patient = trial.enrol("P-FOLLOW", anchors={"C1D1": C1D1}, horizon=horizon)
    rows = follow_rows(trial.rows(patient))
    assert [row.status for row in rows] == ["WAITING_FOR_ANCHOR"]

    # End of therapy recorded 07-Dec-2026 and confirmed.
    proposal, _ = trial.record_anchor(
        patient, "END_OF_THERAPY", EOT, "SOURCE_DOCUMENT", horizon=horizon)
    assert proposal.impact["summary"]["ADDED"] == len(SEGMENT_1 + SEGMENT_2 + SEGMENT_3) - 1
    rows = follow_rows(trial.rows(patient))
    assert [row.nominal_start_date for row in rows] == SEGMENT_1 + SEGMENT_2 + SEGMENT_3
    assert [row.occurrence_index for row in rows] == list(range(len(rows)))
    # The boundary visits appear exactly once, owned by the ending segment.
    boundary = next(row for row in rows if row.nominal_start_date == date(2028, 12, 7))
    assert boundary.generation_reason["recurrence"]["segment"] == 1
    first_six_monthly = next(row for row in rows if row.nominal_start_date == date(2029, 6, 7))
    assert first_six_monthly.generation_reason["recurrence"]["cadence"] == "every 6 months"
    history = check_invariants(trial, patient, today=date(2026, 12, 8), horizon=horizon)

    # First follow-up done a week late; nothing else moves, re-evaluation is stable.
    trial.record_actual(patient, "FOLLOW_UP:0", date(2027, 3, 14))
    trial.evaluate(patient, horizon=horizon)
    rows = follow_rows(trial.rows(patient))
    assert [row.nominal_start_date for row in rows] == SEGMENT_1 + SEGMENT_2 + SEGMENT_3
    assert rows[0].status == "COMPLETED"
    history = check_invariants(
        trial, patient, today=date(2027, 3, 15), completed_before=history, horizon=horizon)


def test_e9_rolling_horizon_persists_a_few_and_says_the_plan_continues(cleanup):
    trial = open_trial(cleanup, follow_up_schedule())
    patient = trial.enrol("P-ROLLING", anchors={"C1D1": C1D1, "END_OF_THERAPY": EOT},
                          horizon=date(2040, 12, 31))
    rolling = RollingHorizon(upcoming_limit=4, as_of=date(2026, 12, 8))
    trial.evaluate(patient, horizon=date(2040, 12, 31), rolling=rolling)
    rows = follow_rows(trial.rows(patient))
    current = [row for row in rows if row.status != "CANCELLED"]
    assert [row.nominal_start_date for row in current] == SEGMENT_1[:4]
    # The rows beyond the rolling window are withdrawn, not left as live visits.
    assert all(row.status == "CANCELLED" for row in rows[4:])

    summary = next(item for item in trial.result(
        patient, horizon=date(2040, 12, 31), rolling=rolling).repeat_summaries
        if item.event_code == "FOLLOW_UP")
    assert summary.cadence == "every 3 months to year 2, then every 6 months to year 5, then yearly"
    assert summary.segments == ["every 3 months to year 2", "every 6 months to year 5", "yearly"]
    assert summary.termination == (
        "no stated maximum; the protocol continues while the patient remains on study")
    assert summary.continues is True
    assert summary.materialized_count == 4
    assert summary.next_unmaterialized == date(2028, 3, 7)
    keys = reminder_keys(trial.projection(patient, date(2026, 12, 8)))
    assert keys == {f"FOLLOW_UP:{index}" for index in range(4)}
