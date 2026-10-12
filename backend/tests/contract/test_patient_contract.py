"""Contract for the patient plane: patients, append-only facts, evaluations."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.errors import (
    DuplicateRecordError, RecordNotFoundError, StaleRevisionError,
)
from app.application.patients import (
    ActivityEntryRecord, ConfinementEpisodeRecord, EvaluationRecord, OccurrenceRecord,
    PatientActivityEntry, PatientAnchorRecord, PatientConditionRecord, PatientEventRecord,
    PatientRecord, PatientStateRecord, UnscheduledVisitRecord,
)

from .support import ORG

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def new_patient(**extra) -> PatientRecord:
    return PatientRecord(organization_id=ORG, trial_id=extra.pop("trial_id", uuid4()),
                         patient_code=extra.pop("patient_code", "P-001"), **extra)


# ---------------------------------------------------------------- patients
def test_a_patient_code_is_unique_within_a_trial(uow):
    trial = uuid4()
    first = new_patient(trial_id=trial)
    uow.run(lambda tx: tx.patients.add(first))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.patients.add(new_patient(trial_id=trial)))
    uow.run(lambda tx: tx.patients.add(new_patient(trial_id=uuid4())))  # another trial: fine
    assert [p.patient_code for p in uow.read(lambda tx: tx.patients.list_for_trial(ORG, trial))] == ["P-001"]


def test_a_patient_is_only_visible_to_its_organization(uow):
    patient = new_patient(dimension_values={"ARM": ["A"]}, arm_code="A")
    uow.run(lambda tx: tx.patients.add(patient))
    assert uow.read(lambda tx: tx.patients.get(ORG, patient.id)) == patient
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.patients.get(uuid4(), patient.id))


def test_pinning_a_schedule_version_bumps_the_revision(uow):
    patient, version = new_patient(), uuid4()
    uow.run(lambda tx: tx.patients.add(patient))
    pinned = uow.run(lambda tx: tx.patients.pin_schedule_version(ORG, patient.id, version))
    assert pinned.current_schedule_version_id == version and pinned.revision == 1
    with pytest.raises(RecordNotFoundError):
        uow.run(lambda tx: tx.patients.pin_schedule_version(ORG, uuid4(), version))


# ------------------------------------------------------------------- facts
def test_anchors_come_back_oldest_first_and_filter_by_status(uow):
    patient = uuid4()
    definition = uuid4()

    def anchor(code, minutes, status):
        return PatientAnchorRecord(
            organization_id=ORG, patient_id=patient, anchor_definition_id=definition,
            anchor_code=code, value_date=date(2026, 1, 1), status=status, recorded_at=at(minutes))

    def write(tx):
        tx.facts.add_anchor(anchor("RANDOMISATION", 5, "CONFIRMED"))
        tx.facts.add_anchor(anchor("SURGERY", 1, "PENDING_CONFIRMATION"))
        tx.facts.add_anchor(anchor("BASELINE", 3, "PROVISIONAL"))

    uow.run(write)
    everything = uow.read(lambda tx: tx.facts.list_anchors(patient))
    assert [a.anchor_code for a in everything] == ["SURGERY", "BASELINE", "RANDOMISATION"]
    usable = uow.read(lambda tx: tx.facts.list_anchors(patient, statuses={"CONFIRMED", "PROVISIONAL"}))
    assert [a.anchor_code for a in usable] == ["BASELINE", "RANDOMISATION"]


def test_anchor_values_keep_their_date_or_instant_kind(uow):
    patient = uuid4()
    instant = datetime(2026, 2, 3, 8, 15, tzinfo=timezone.utc)

    def write(tx):
        tx.facts.add_anchor(PatientAnchorRecord(
            organization_id=ORG, patient_id=patient, anchor_definition_id=uuid4(), anchor_code="D",
            value_date=date(2026, 2, 3), status="CONFIRMED", recorded_at=at(1)))
        tx.facts.add_anchor(PatientAnchorRecord(
            organization_id=ORG, patient_id=patient, anchor_definition_id=uuid4(), anchor_code="T",
            value_datetime=instant, status="CONFIRMED", recorded_at=at(2)))

    uow.run(write)
    date_only, timed = uow.read(lambda tx: tx.facts.list_anchors(patient))
    assert date_only.value_date == date(2026, 2, 3) and date_only.value_datetime is None
    assert timed.value_datetime == instant and timed.value_date is None


def test_states_are_ordered_by_effective_time(uow):
    patient = uuid4()

    def state(code, minutes, value):
        return PatientStateRecord(
            organization_id=ORG, patient_id=patient, state_code=code, state_value=value,
            effective_at=at(minutes), created_at=at(minutes))

    def write(tx):
        tx.facts.append_state(state("WEIGHT", 9, {"kg": 71}))
        tx.facts.append_state(state("WEIGHT", 2, {"kg": 70}))

    uow.run(write)
    rows = uow.read(lambda tx: tx.facts.list_states(patient))
    assert [r.state_value for r in rows] == [{"kg": 70}, {"kg": 71}]


def test_a_superseded_condition_leaves_the_current_view_but_stays_stored(uow):
    patient = uuid4()
    first = PatientConditionRecord(
        organization_id=ORG, patient_id=patient, condition_code="PREGNANT", state="ACTIVE",
        occurrence_date=date(2026, 1, 9), recorded_at=at(1))
    correction = PatientConditionRecord(
        organization_id=ORG, patient_id=patient, condition_code="PREGNANT", state="NOT_OCCURRED",
        recorded_at=at(5), reason="entered in error")

    def write(tx):
        tx.facts.add_condition(first)
        tx.facts.add_condition(correction)
        tx.facts.supersede_condition(first.id, correction.id)

    uow.run(write)
    current = uow.read(lambda tx: tx.facts.list_current_conditions(patient))
    assert [c.id for c in current] == [correction.id]
    with pytest.raises(RecordNotFoundError):  # already superseded
        uow.run(lambda tx: tx.facts.supersede_condition(first.id, uuid4()))


def test_activity_corrections_supersede_and_report_what_they_replaced(uow):
    patient, key = uuid4(), "DAY_1#0#ECG"

    def entry(minutes, status, actual=None):
        return ActivityEntryRecord(
            organization_id=ORG, patient_id=patient, event_code="DAY_1", activity_code="ECG",
            logical_key=key, status=status, actual_time=actual, recorded_at=at(minutes))

    first, second = entry(1, "COMPLETED", at(1)), entry(2, "COMPLETED", at(30))

    def write(tx):
        tx.facts.add_activity_entry(first)
        tx.facts.add_activity_entry(second)
        return tx.facts.supersede_activity_entries(patient, key, second.id)

    replaced = uow.run(write)
    assert [r.id for r in replaced] == [first.id]
    current = uow.read(lambda tx: tx.facts.list_current_activity_entries(patient))
    assert [r.id for r in current] == [second.id]
    assert current[0].actual_time == at(30)


def test_unscheduled_visits_are_ordered_by_date(uow):
    patient = uuid4()

    def visit(day):
        return UnscheduledVisitRecord(
            organization_id=ORG, patient_id=patient, event_definition_id=uuid4(),
            event_code="UNSCHEDULED", occurred_on=date(2026, 3, day), reason="symptoms")

    uow.run(lambda tx: [tx.facts.add_unscheduled_visit(visit(9)), tx.facts.add_unscheduled_visit(visit(2))])
    rows = uow.read(lambda tx: tx.facts.list_unscheduled_visits(patient))
    assert [r.occurred_on for r in rows] == [date(2026, 3, 2), date(2026, 3, 9)]


def test_occurrence_facts_belong_to_the_logical_visit_and_filter_by_version(uow):
    patient, v1, v2 = uuid4(), uuid4(), uuid4()

    def fact(version, minutes, actual, status="COMPLETED"):
        return OccurrenceRecord(
            organization_id=ORG, patient_id=patient, schedule_version_id=version,
            event_code="VISIT_2", occurrence_index=0, logical_key="VISIT_2:0",
            occurrence_type="VISIT", scheduled_date=date(2026, 10, 10), actual_date=actual,
            status=status, recorded_at=at(minutes), metadata={"fact_kind": "PERFORMED"})

    def write(tx):
        tx.facts.add_occurrence(fact(v1, 4, date(2026, 10, 13)))
        tx.facts.add_occurrence(fact(v2, 2, None, status="MISSED"))

    uow.run(write)
    everything = uow.read(lambda tx: tx.facts.list_occurrences(patient))
    assert [f.schedule_version_id for f in everything] == [v2, v1]
    only_v1 = uow.read(lambda tx: tx.facts.list_occurrences(patient, schedule_version_id=v1))
    assert [f.actual_date for f in only_v1] == [date(2026, 10, 13)]
    assert only_v1[0].metadata == {"fact_kind": "PERFORMED"}


# ------------------------------------------------------------- evaluations
def evaluation(patient, version, *, sequence=1, key=None) -> EvaluationRecord:
    return EvaluationRecord(
        organization_id=ORG, patient_id=patient, schedule_version_id=version,
        idempotency_key=key, evaluator_version="uctsm-eval.test", sequence=sequence,
        started_at=at(sequence), completed_at=at(sequence), evaluated_at=at(sequence),
        input_snapshot={"anchors": {"BASELINE": "2026-01-01"}}, output_summary={"count": 2})


def projection(ev, code, index, start, *, status="RESOLVED") -> PatientEventRecord:
    return PatientEventRecord(
        organization_id=ORG, patient_id=ev.patient_id, evaluation_id=ev.id,
        schedule_version_id=ev.schedule_version_id, event_definition_id=uuid4(),
        event_code=code, occurrence_index=index, logical_key=f"{code}:{index}", status=status,
        nominal_start_date=start, earliest_date=start - timedelta(days=1),
        latest_date=start + timedelta(days=1), timing_resolution={"precision": "EXACT"},
        generation_reason={"reason": "x"},
        activities=[PatientActivityEntry(
            activity_definition_id=uuid4(), activity_code="ECG", logical_key=f"{code}#{index}#ECG",
            status="PENDING", requiredness="REQUIRED", planned_time=at(10))])


def test_an_evaluation_is_stored_with_its_projections_and_pointer(uow):
    patient, version = uuid4(), uuid4()
    ev = evaluation(patient, version, key="run-1")
    events = [projection(ev, "VISIT_2", 0, date(2026, 10, 10)),
              projection(ev, "VISIT_1", 0, date(2026, 10, 1))]
    episode = ConfinementEpisodeRecord(
        organization_id=ORG, patient_id=patient, evaluation_id=ev.id, episode_definition_id=uuid4(),
        episode_code="STAY", logical_key="STAY", status="PLANNED", planned_admission=date(2026, 10, 1))
    pointer = uow.run(lambda tx: tx.evaluations.save_evaluation(
        ev, events, [episode], expected_pointer_revision=None))
    assert pointer.current_evaluation_id == ev.id and pointer.revision == 1

    loaded = uow.read(lambda tx: tx.evaluations.list_events(ev.id))
    assert [e.event_code for e in loaded] == ["VISIT_1", "VISIT_2"]  # by planned date
    assert loaded[0].activities[0].planned_time == at(10)
    assert loaded[0].nominal_start_date == date(2026, 10, 1)
    assert uow.read(lambda tx: tx.evaluations.find_by_idempotency(patient, "run-1")).id == ev.id
    assert uow.read(lambda tx: tx.evaluations.list_confinements(ev.id))[0].planned_admission \
        == date(2026, 10, 1)
    assert uow.read(lambda tx: tx.evaluations.get_pointer(patient, version)).revision == 1


def test_a_new_evaluation_keeps_the_previous_one_and_moves_only_the_pointer(uow):
    patient, version = uuid4(), uuid4()
    first = evaluation(patient, version, sequence=1)
    second = evaluation(patient, version, sequence=2)
    uow.run(lambda tx: tx.evaluations.save_evaluation(
        first, [projection(first, "V", 0, date(2026, 10, 10))], [], expected_pointer_revision=None))
    uow.run(lambda tx: tx.evaluations.save_evaluation(
        second, [projection(second, "V", 0, date(2026, 10, 13))], [], expected_pointer_revision=1))
    pointer = uow.read(lambda tx: tx.evaluations.get_pointer(patient, version))
    assert pointer.current_evaluation_id == second.id and pointer.revision == 2
    history = uow.read(lambda tx: tx.evaluations.list_evaluations(patient, version))
    assert [e.id for e in history] == [first.id, second.id]
    old = uow.read(lambda tx: tx.evaluations.list_events(first.id))
    assert old[0].nominal_start_date == date(2026, 10, 10)  # untouched
    assert uow.read(lambda tx: tx.evaluations.count_evaluations(patient, version)) == 2


def test_a_stale_pointer_revision_is_refused_and_writes_nothing(uow):
    patient, version = uuid4(), uuid4()
    first = evaluation(patient, version, sequence=1)
    uow.run(lambda tx: tx.evaluations.save_evaluation(
        first, [projection(first, "V", 0, date(2026, 10, 10))], [], expected_pointer_revision=None))
    loser = evaluation(patient, version, sequence=2)
    with pytest.raises(StaleRevisionError):
        uow.run(lambda tx: tx.evaluations.save_evaluation(
            loser, [projection(loser, "V", 0, date(2026, 10, 20))], [], expected_pointer_revision=0))
    assert uow.read(lambda tx: tx.evaluations.count_evaluations(patient, version)) == 1
    assert uow.read(lambda tx: tx.evaluations.list_events(loser.id)) == []


def test_two_creators_of_the_first_pointer_cannot_both_win(uow):
    patient, version = uuid4(), uuid4()
    first = evaluation(patient, version, sequence=1)
    uow.run(lambda tx: tx.evaluations.save_evaluation(first, [], [], expected_pointer_revision=None))
    rival = evaluation(patient, version, sequence=2)
    with pytest.raises(StaleRevisionError):
        uow.run(lambda tx: tx.evaluations.save_evaluation(rival, [], [], expected_pointer_revision=None))


def test_an_idempotency_key_and_a_sequence_number_are_each_used_once(uow):
    patient, version = uuid4(), uuid4()
    first = evaluation(patient, version, sequence=1, key="k")
    uow.run(lambda tx: tx.evaluations.save_evaluation(first, [], [], expected_pointer_revision=None))
    reused_key = evaluation(patient, version, sequence=2, key="k")
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.evaluations.save_evaluation(reused_key, [], [], expected_pointer_revision=1))
    reused_sequence = evaluation(patient, version, sequence=1, key="other")
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.evaluations.save_evaluation(
            reused_sequence, [], [], expected_pointer_revision=1))


def test_one_evaluation_cannot_project_the_same_visit_twice(uow):
    patient, version = uuid4(), uuid4()
    ev = evaluation(patient, version)
    twins = [projection(ev, "V", 0, date(2026, 10, 10)), projection(ev, "V", 0, date(2026, 10, 11))]
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.evaluations.save_evaluation(ev, twins, [], expected_pointer_revision=None))
    assert uow.read(lambda tx: tx.evaluations.find_by_idempotency(patient, "none")) is None
    assert uow.read(lambda tx: tx.evaluations.count_evaluations(patient, version)) == 0
    assert uow.read(lambda tx: tx.evaluations.get_pointer(patient, version)) is None


def test_the_same_visit_may_appear_in_every_evaluation(uow):
    patient, version = uuid4(), uuid4()
    first, second = evaluation(patient, version, sequence=1), evaluation(patient, version, sequence=2)
    uow.run(lambda tx: tx.evaluations.save_evaluation(
        first, [projection(first, "V", 0, date(2026, 10, 10))], [], expected_pointer_revision=None))
    uow.run(lambda tx: tx.evaluations.save_evaluation(
        second, [projection(second, "V", 0, date(2026, 10, 10))], [], expected_pointer_revision=1))
    assert len(uow.read(lambda tx: tx.evaluations.list_events(first.id))) == 1
    assert len(uow.read(lambda tx: tx.evaluations.list_events(second.id))) == 1


def test_a_missing_evaluation_is_not_found(uow):
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.evaluations.get_evaluation(uuid4()))
