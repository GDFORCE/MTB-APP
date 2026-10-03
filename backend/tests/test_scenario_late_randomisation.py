"""Scenario H2 end to end: run-in, then late randomisation to Arm B (matrix 13).

Registered Thu 01-Oct-2026 with no arm. The 4-week run-in is dated normally;
every arm visit waits for the assignment. Randomised to Arm B on Thu
29-Oct-2026 through ONE preview -> confirm that carries both the assignment and
the randomisation date.
"""

from datetime import date

import pytest

from _engine_cases import anchor, approved, visit
from _scenario_harness import check_invariants, new_trial, reminder_keys, statuses

from app.domain.schedule.deviation import DeviationType
from app.domain.schedule.models import ApplicabilityRule, StudyDimension
from app.domain.schedule.timing import AnchorReference, ProtocolDayTiming

REGISTRATION = date(2026, 10, 1)
RANDOMISATION = date(2026, 10, 29)

RUN_IN = ("RUN_IN_D1:0", "RUN_IN_D15:0", "RUN_IN_D28:0")
ARM_A = ("A_C1D1:0", "A_C2D1:0", "A_C3D1:0")
ARM_B = ("B_C1D1:0", "B_C1D8:0", "B_C1D15:0", "B_C2D1:0")


def day(code: str, anchor_code: str, number: int, arm: str | None = None):
    return visit(code, ProtocolDayTiming(
        reference=AnchorReference(code=anchor_code), day=number),
        applicability=[ApplicabilityRule(dimension="ARM", values=[arm])] if arm else [])


def schedule():
    return approved([
        day("RUN_IN_D1", "REGISTRATION", 1),
        day("RUN_IN_D15", "REGISTRATION", 15),
        day("RUN_IN_D28", "REGISTRATION", 28),
        # Arm A (paclitaxel): Day 1 every 3 weeks.
        day("A_C1D1", "RANDOMISATION", 1, "A"),
        day("A_C2D1", "RANDOMISATION", 22, "A"),
        day("A_C3D1", "RANDOMISATION", 43, "A"),
        # Arm B (nab-paclitaxel): Days 1, 8, 15; carboplatin Day 1.
        day("B_C1D1", "RANDOMISATION", 1, "B"),
        day("B_C1D8", "RANDOMISATION", 8, "B"),
        day("B_C1D15", "RANDOMISATION", 15, "B"),
        day("B_C2D1", "RANDOMISATION", 22, "B"),
    ], anchors=[anchor("REGISTRATION"), anchor("RANDOMISATION")], arms=[
        StudyDimension(code="A", protocol_label="Arm A", display_name="Arm A (paclitaxel)"),
        StudyDimension(code="B", protocol_label="Arm B", display_name="Arm B (nab-paclitaxel)"),
    ])


@pytest.fixture()
def trial():
    trial, engine = new_trial()
    trial.publish(schedule())
    yield trial
    trial.session.close()
    engine.dispose()


def test_h2_run_in_then_randomised_to_arm_b(trial):
    patient = trial.enrol("P-RAND-1", anchors={"REGISTRATION": REGISTRATION})

    rows = trial.rows(patient)
    assert [rows[key].nominal_start_date for key in RUN_IN] == [
        date(2026, 10, 1), date(2026, 10, 15), date(2026, 10, 28)]
    for key in ARM_A + ARM_B:
        assert rows[key].status == "WAITING_FOR_ASSIGNMENT", key
        assert rows[key].nominal_start_date is None
        assert rows[key].generation_reason["waiting_for_assignment"] == ["ARM"]
    today = date(2026, 10, 10)
    assert reminder_keys(trial.projection(patient, today)) == set(RUN_IN)
    # Never "missed" while waiting for the randomisation, however late it is.
    report = trial.deviations(patient, today=date(2027, 3, 1))
    for key in ARM_A + ARM_B:
        assert report[key].deviation_type == DeviationType.NOT_ASSESSABLE, key
    trial.record_actual(patient, "RUN_IN_D1:0", REGISTRATION)
    trial.record_actual(patient, "RUN_IN_D15:0", date(2026, 10, 16))
    history = check_invariants(trial, patient, today=today)

    # Randomised to Arm B on 29-Oct: the assignment and the date in one preview.
    candidate = trial.anchor_candidate(patient, "RANDOMISATION", RANDOMISATION, "SOURCE_DOCUMENT")
    proposal = trial.preview(
        patient, anchor_candidate=candidate, assignment_change={"ARM": "B"},
        reason="Randomised to Arm B")
    assert proposal.impact["assignment_change"] == {"ARM": "B"}
    changes = {item["logical_key"]: item for item in proposal.impact["events"]}
    assert [changes[key]["after"]["date"] for key in ARM_B] == [
        "2026-10-29", "2026-11-05", "2026-11-12", "2026-11-19"]
    assert {changes[key]["after"]["status"] for key in ARM_A} == {"NOT_APPLICABLE"}
    assert changes["RUN_IN_D1:0"]["change"] == "PROTECTED"
    # A preview is a question, not an instruction.
    trial.session.refresh(patient)
    assert patient.arm_id is None
    assert trial.rows(patient)["B_C1D1:0"].status == "WAITING_FOR_ASSIGNMENT"

    rows = trial.confirm(proposal)
    assert [rows[key].nominal_start_date for key in ARM_B] == [
        date(2026, 10, 29), date(2026, 11, 5), date(2026, 11, 12), date(2026, 11, 19)]
    assert statuses(rows, *ARM_B) == ["RESOLVED"] * 4
    assert statuses(rows, *ARM_A) == ["NOT_APPLICABLE"] * 3
    assert all(rows[key].nominal_start_date is None for key in ARM_A)
    assert statuses(rows, "RUN_IN_D1:0", "RUN_IN_D15:0") == ["COMPLETED"] * 2

    # Audited: who, what it replaced, and the randomisation date as its own event.
    assigned = trial.audit("PATIENT_ASSIGNMENT_CHANGED")
    assert len(assigned) == 1
    assert assigned[0].before["arm_id"] is None
    assert assigned[0].after["arm_id"] is not None
    assert assigned[0].after["reason"] == "Randomised to Arm B"
    assert assigned[0].after["impact_proposal_id"] == str(proposal.id)
    confirmed = trial.audit("PATIENT_ANCHOR_CONFIRMED")
    assert confirmed[-1].after["anchor_code"] == "RANDOMISATION"
    assert confirmed[-1].after["value"] == "2026-10-29"
    assert trial.audit("PATIENT_SCHEDULE_VERSION_ASSIGNED")[-1].after["proposal_id"] == str(proposal.id)

    today = date(2026, 10, 30)
    keys = reminder_keys(trial.projection(patient, today))
    assert set(ARM_B) <= keys
    assert keys & set(ARM_A) == set()
    check_invariants(trial, patient, today=today, completed_before=history)


def test_h2_arm_a_comparison(trial):
    """For comparison: the same randomisation to Arm A gives 29-Oct, 19-Nov, 10-Dec."""
    patient = trial.enrol("P-RAND-2", anchors={"REGISTRATION": REGISTRATION})
    candidate = trial.anchor_candidate(patient, "RANDOMISATION", RANDOMISATION, "SOURCE_DOCUMENT")
    rows = trial.confirm(trial.preview(
        patient, anchor_candidate=candidate, assignment_change={"ARM": "A"}))
    assert [rows[key].nominal_start_date for key in ARM_A] == [
        date(2026, 10, 29), date(2026, 11, 19), date(2026, 12, 10)]
    assert statuses(rows, *ARM_B) == ["NOT_APPLICABLE"] * 4
    check_invariants(trial, patient, today=date(2026, 10, 30))


def test_h2_assignment_without_the_randomisation_date_still_waits_for_the_anchor(trial):
    """Arm known, date not recorded: arm visits wait for the ANCHOR, not assignment."""
    patient = trial.enrol("P-RAND-3", anchors={"REGISTRATION": REGISTRATION})
    rows = trial.confirm(trial.preview(patient, assignment_change={"ARM": "B"}))
    assert statuses(rows, *ARM_B) == ["WAITING_FOR_ANCHOR"] * 4
    assert statuses(rows, *ARM_A) == ["NOT_APPLICABLE"] * 3
    check_invariants(trial, patient, today=date(2026, 10, 30))
