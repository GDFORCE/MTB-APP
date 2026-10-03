"""Scenario M1-M2 end to end: an amendment while the patient has future visits
(matrix 20).

Version 1 is approved and a patient is enrolled (registration Thu 01-Oct-2026)
and attends two visits. Version 2 is then approved: it moves Visit 2 (already
done) and Visit 3 (still in the future), and adds a new visit. The patient
stays on version 1 until a reassignment is previewed and confirmed; then only
future changed visits move, and completed visits never do.
"""

from datetime import date

import pytest

from _engine_cases import anchor, approved, visit
from _scenario_harness import check_invariants, new_trial, reminder_keys

from app.domain.schedule.diff import ChangeType, compare_schedule_versions
from app.domain.schedule.timing import AnchorReference, ProtocolDayTiming

REGISTRATION = date(2026, 10, 1)


def version(number: int, days: dict[str, int]):
    events = [
        visit(code, ProtocolDayTiming(reference=AnchorReference(code="REGISTRATION"), day=day))
        for code, day in days.items()
    ]
    schedule = approved(events, anchors=[anchor("REGISTRATION")])
    schedule.schedule_metadata.version_number = number
    return schedule


V1 = {"V1": 1, "V2": 15, "V3": 29, "V4": 57, "FOLLOW_UP": 85}
V2 = {"V1": 1, "V2": 18, "V3": 36, "V3B": 43, "V4": 57, "FOLLOW_UP": 85}


@pytest.fixture()
def trial():
    trial, engine = new_trial()
    yield trial
    trial.session.close()
    engine.dispose()


def test_m1_m2_amendment_with_future_visits(trial):
    v1 = trial.publish(version(1, V1))
    patient = trial.enrol("P-AMEND", anchors={"REGISTRATION": REGISTRATION})
    trial.record_actual(patient, "V1:0", REGISTRATION)
    trial.record_actual(patient, "V2:0", date(2026, 10, 16))
    before = {key: (row.status, row.nominal_start_date) for key, row in trial.rows(patient).items()}
    assert before["V3:0"] == ("RESOLVED", date(2026, 10, 29))
    history = check_invariants(trial, patient, today=date(2026, 10, 20))

    # M1 - the amendment is approved. The enrolled patient stays on version 1,
    # even when re-evaluated; a new patient is enrolled onto version 2.
    v2 = trial.publish(version(2, V2))
    assert trial.current_version_id() == v2.schedule_version_id
    trial.evaluate(patient)
    trial.session.refresh(patient)
    assert patient.current_schedule_version_id == v1.schedule_version_id
    assert {key: (row.status, row.nominal_start_date)
            for key, row in trial.rows(patient).items()} == before
    newcomer = trial.enrol("P-NEW", anchors={"REGISTRATION": REGISTRATION})
    assert newcomer.current_schedule_version_id == v2.schedule_version_id
    assert trial.rows(newcomer)["V3:0"].nominal_start_date == date(2026, 11, 5)

    diff = compare_schedule_versions(v1, v2)
    typed = {(item.change_type, item.entity_code) for item in diff.typed_changes}
    assert {(ChangeType.TIMING_CHANGED, "V2"), (ChangeType.TIMING_CHANGED, "V3"),
            (ChangeType.VISIT_ADDED, "V3B")} <= typed
    assert not {code for kind, code in typed if code in {"V1", "V4", "FOLLOW_UP"}}

    # M2 - preview the move: exactly which future visits change.
    proposal = trial.preview(patient, target=v2.schedule_version_id, reason="Amendment 2")
    changes = {item["logical_key"]: item for item in proposal.impact["events"]}
    assert {key: item["change"] for key, item in changes.items()} == {
        "V1:0": "PROTECTED", "V2:0": "PROTECTED", "V3:0": "MOVED",
        "V3B:0": "ADDED", "V4:0": "UNCHANGED", "FOLLOW_UP:0": "UNCHANGED",
    }
    assert changes["V3:0"]["before"]["date"] == "2026-10-29"
    assert changes["V3:0"]["after"]["date"] == "2026-11-05"
    # V2 changed in the protocol but was already done: it keeps its v1 date.
    assert changes["V2:0"]["after"]["date"] == "2026-10-15"
    trial.session.refresh(patient)
    assert patient.current_schedule_version_id == v1.schedule_version_id

    rows = trial.confirm(proposal)
    trial.session.refresh(patient)
    assert patient.current_schedule_version_id == v2.schedule_version_id
    assert rows["V1:0"].status == rows["V2:0"].status == "COMPLETED"
    assert rows["V2:0"].nominal_start_date == date(2026, 10, 15)
    assert rows["V2:0"].logical_occurrence_id == before_ids(trial, v1, patient)["V2:0"]
    assert rows["V3:0"].nominal_start_date == date(2026, 11, 5)
    assert rows["V3B:0"].nominal_start_date == date(2026, 11, 12)
    assert rows["V4:0"].nominal_start_date == date(2026, 11, 26)
    assign = trial.audit("PATIENT_SCHEDULE_VERSION_ASSIGNED")[-1]
    assert assign.before["schedule_version_id"] == str(v1.schedule_version_id)
    assert assign.after["schedule_version_id"] == str(v2.schedule_version_id)
    keys = reminder_keys(trial.projection(patient, date(2026, 10, 21)))
    assert {"V3:0", "V3B:0", "V4:0", "FOLLOW_UP:0"} <= keys
    assert not keys & {"V1:0", "V2:0"}
    check_invariants(trial, patient, today=date(2026, 10, 21), completed_before=history)


def before_ids(trial, v1, patient):
    return {
        row.logical_key: row.logical_occurrence_id
        for row in trial.service().current_events(patient.id, v1.schedule_version_id)
    }


def test_m2_a_preview_made_before_new_data_is_stale(trial):
    trial.publish(version(1, V1))
    patient = trial.enrol("P-STALE", anchors={"REGISTRATION": REGISTRATION})
    v2 = trial.publish(version(2, V2))
    proposal = trial.preview(patient, target=v2.schedule_version_id, reason="Amendment 2")
    # The site records Visit 1 after the preview was generated.
    trial.record_actual(patient, "V1:0", REGISTRATION)
    with pytest.raises(ValueError, match="new impact preview"):
        trial.confirm(proposal)
    assert proposal.status == "STALE"
