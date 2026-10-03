"""Scenario H7 end to end: AB and BA crossover with a 14-day washout (matrix 15-16).

"Sequence AB: Period 1 drug A, 14-day washout, Period 2 drug B. Sequence BA
reversed." Periods are epochs, the sequence is an assigned study dimension
(SEQUENCE IN [AB]), and Period 2 Day 1 = Period 1 last dose + 14 days. The
spec states no dates for H7; these use randomisation Mon 05-Oct-2026 and a
7-day dosing period (Day 1 .. Day 7).
"""

from datetime import date

import pytest

from _engine_cases import anchor, approved, visit
from _scenario_harness import check_invariants, new_trial, reminder_keys, statuses

from sqlalchemy import select

from app.db import models as db
from app.domain.schedule.models import ApplicabilityRule, Epoch, GenericDimension
from app.domain.schedule.timing import (
    AnchorReference, OffsetTiming, ProtocolDayTiming, TemporalAmount, TimeUnit,
)

RANDOMISATION = date(2026, 10, 5)
P1_LAST_DOSE_ACTUAL = date(2026, 10, 11)

AB_ONLY = ("P1_DAY1_DRUG_A:0", "P2_DAY1_DRUG_B:0")
BA_ONLY = ("P1_DAY1_DRUG_B:0", "P2_DAY1_DRUG_A:0")


def sequence(code: str) -> list[ApplicabilityRule]:
    return [ApplicabilityRule(dimension="SEQUENCE", values=[code])]


def days(value: int) -> TemporalAmount:
    return TemporalAmount(value=value, unit=TimeUnit.DAY)


def crossover():
    period_1 = Epoch(code="PERIOD_1", protocol_label="Period 1", display_name="Period 1",
                     sequence_number=1)
    washout = Epoch(code="WASHOUT", protocol_label="Washout", display_name="Washout",
                    sequence_number=2)
    period_2 = Epoch(code="PERIOD_2", protocol_label="Period 2", display_name="Period 2",
                     sequence_number=3)
    randomisation = AnchorReference(code="RANDOMISATION")
    washout_end = AnchorReference(code="P1_LAST_DOSE")

    def dosing_day(code: str, timing, epoch: Epoch, drug: str | None, rules=None):
        return visit(code, timing, display_name="Day 1" if "DAY1" in code else code.title(),
                     epoch_id=epoch.id, event_type="TREATMENT",
                     applicability=rules or [], metadata={"drug": drug} if drug else {})

    events = [
        # Period 1 Day 1: the drug depends on the sequence.
        dosing_day("P1_DAY1_DRUG_A", ProtocolDayTiming(reference=randomisation, day=1),
                   period_1, "A", sequence("AB")),
        dosing_day("P1_DAY1_DRUG_B", ProtocolDayTiming(reference=randomisation, day=1),
                   period_1, "B", sequence("BA")),
        # Period 1 last dose (Day 7), both sequences; anchors the washout.
        dosing_day("P1_LAST_DOSE", ProtocolDayTiming(reference=randomisation, day=7),
                   period_1, None),
        visit("WASHOUT_CHECK", OffsetTiming(reference=washout_end, offset=days(7)),
              epoch_id=washout.id, event_type="TELEPHONE_CONTACT", visit_mode="TELEPHONE"),
        # Period 2 Day 1 = Period 1 last dose + 14 days - the SAME name, "Day 1".
        dosing_day("P2_DAY1_DRUG_B", OffsetTiming(reference=washout_end, offset=days(14)),
                   period_2, "B", sequence("AB")),
        dosing_day("P2_DAY1_DRUG_A", OffsetTiming(reference=washout_end, offset=days(14)),
                   period_2, "A", sequence("BA")),
        dosing_day("P2_LAST_DOSE", OffsetTiming(reference=washout_end, offset=days(20)),
                   period_2, None),
    ]
    schedule = approved(events, anchors=[
        anchor("RANDOMISATION"),
        anchor("P1_LAST_DOSE", source_event_code="P1_LAST_DOSE",
               derivation_rule={"selection": "LAST"}),
    ], dimensions=[
        GenericDimension(code="AB", protocol_label="AB", display_name="Sequence AB",
                         dimension_type="SEQUENCE"),
        GenericDimension(code="BA", protocol_label="BA", display_name="Sequence BA",
                         dimension_type="SEQUENCE"),
    ])
    schedule.epochs = [period_1, washout, period_2]
    return schedule


@pytest.fixture()
def trial():
    trial, engine = new_trial()
    trial.publish(crossover())
    yield trial
    trial.session.close()
    engine.dispose()


def epoch_codes(trial, rows):
    epochs = {row.id: row.code for row in trial.session.scalars(select(db.Epoch))}
    events = {row.id: row.epoch_id for row in trial.session.scalars(select(db.Event))}
    return {key: epochs.get(events.get(row.event_definition_id)) for key, row in rows.items()}


@pytest.mark.parametrize("seq,first,second", [
    ("AB", "P1_DAY1_DRUG_A:0", "P2_DAY1_DRUG_B:0"),
    ("BA", "P1_DAY1_DRUG_B:0", "P2_DAY1_DRUG_A:0"),
])
def test_h7_sequence_runs_period_1_washout_period_2(trial, seq, first, second):
    patient = trial.enrol(f"P-{seq}", anchors={"RANDOMISATION": RANDOMISATION},
                          dimensions={"SEQUENCE": [seq]})
    other = BA_ONLY if seq == "AB" else AB_ONLY

    rows = trial.rows(patient)
    assert rows[first].nominal_start_date == date(2026, 10, 5)
    assert rows["P1_LAST_DOSE:0"].nominal_start_date == date(2026, 10, 11)
    assert statuses(rows, *other) == ["NOT_APPLICABLE"] * 2
    # Period 2 waits for the ACTUAL last dose of Period 1, not the planned one.
    assert rows[second].status == "WAITING_FOR_ANCHOR"
    assert rows[second].generation_reason["waiting_for_actual_anchor"] == ["P1_LAST_DOSE"]
    assert rows["WASHOUT_CHECK:0"].status == "WAITING_FOR_ANCHOR"
    history = check_invariants(trial, patient, today=date(2026, 10, 6))

    # Period 1 given: Day 1 on 05-Oct, last dose on 11-Oct.
    trial.record_actual(patient, first, RANDOMISATION)
    trial.record_actual(patient, "P1_LAST_DOSE:0", P1_LAST_DOSE_ACTUAL)
    trial.evaluate(patient)
    rows = trial.rows(patient)
    assert rows["WASHOUT_CHECK:0"].nominal_start_date == date(2026, 10, 18)
    assert rows[second].status == "RESOLVED"
    assert rows[second].nominal_start_date == date(2026, 10, 25)
    assert rows["P2_LAST_DOSE:0"].nominal_start_date == date(2026, 10, 31)

    # Identically named "Day 1" visits stay two separate occurrences: separate
    # keys, epochs, dates and completion.
    assert rows[first].status == "COMPLETED"
    assert rows[second].status == "RESOLVED"
    assert rows[first].logical_occurrence_id != rows[second].logical_occurrence_id
    periods = epoch_codes(trial, rows)
    assert (periods[first], periods["WASHOUT_CHECK:0"], periods[second]) == (
        "PERIOD_1", "WASHOUT", "PERIOD_2")
    names = {trial.session.get(db.Event, rows[key].event_definition_id).display_name
             for key in (first, second)}
    assert names == {"Day 1"}
    today = date(2026, 10, 20)
    keys = reminder_keys(trial.projection(patient, today))
    assert second in keys and first not in keys
    assert keys & set(other) == set()
    check_invariants(trial, patient, today=today, completed_before=history)


def test_h7_late_period_1_last_dose_moves_only_period_2(trial):
    patient = trial.enrol("P-AB-LATE", anchors={"RANDOMISATION": RANDOMISATION},
                          dimensions={"SEQUENCE": ["AB"]})
    trial.record_actual(patient, "P1_DAY1_DRUG_A:0", RANDOMISATION)
    trial.record_actual(patient, "P1_LAST_DOSE:0", date(2026, 10, 13))
    trial.evaluate(patient)
    history = check_invariants(trial, patient, today=date(2026, 10, 14))
    rows = trial.rows(patient)
    assert rows["P2_DAY1_DRUG_B:0"].nominal_start_date == date(2026, 10, 27)
    assert rows["P1_DAY1_DRUG_A:0"].nominal_start_date == date(2026, 10, 5)
    check_invariants(trial, patient, today=date(2026, 10, 14), completed_before=history)


def test_h7_sequence_assigned_after_enrolment(trial):
    """Before the sequence is assigned, sequence visits wait for the ASSIGNMENT."""
    patient = trial.enrol("P-SEQ-LATE", anchors={"RANDOMISATION": RANDOMISATION})
    rows = trial.rows(patient)
    for key in AB_ONLY + BA_ONLY:
        assert rows[key].status == "WAITING_FOR_ASSIGNMENT", key
        assert rows[key].generation_reason["waiting_for_assignment"] == ["SEQUENCE"]
    assert rows["P1_LAST_DOSE:0"].status == "RESOLVED"

    rows = trial.confirm(trial.preview(patient, assignment_change={"SEQUENCE": "BA"}))
    assert rows["P1_DAY1_DRUG_B:0"].nominal_start_date == RANDOMISATION
    assert statuses(rows, *AB_ONLY) == ["NOT_APPLICABLE"] * 2
    trial.session.refresh(patient)
    assert patient.dimension_values == {"SEQUENCE": ["BA"]}
    with pytest.raises(ValueError):
        trial.preview(patient, assignment_change={"SEQUENCE": "CA"})
    check_invariants(trial, patient, today=date(2026, 10, 6))
