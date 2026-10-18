"""The amendment repository contract (P6) - one suite, run on the in-memory fake AND real
MongoDB: scoped uniqueness, guarded revisions, the single open decision per patient, and
cross-tenant refusal."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.application.amendments import (
    AdoptionDecisionRecord, AmendmentMappingSetRecord, AmendmentPairRecord,
    AmendmentSimulationRecord, DecisionStatus, DecisionType, MappingDecisionKind,
    MappingDecisionRecord, SimulationStatus,
)
from app.application.errors import DuplicateRecordError, RecordNotFoundError, StaleRevisionError
from app.application.records import utc_now

ORG, OTHER_ORG = uuid4(), uuid4()


def pair(org=ORG, trial=None, a=None, b=None):
    return AmendmentPairRecord(
        organization_id=org, trial_id=trial or uuid4(), protocol_id=uuid4(),
        from_schedule_version_id=a or uuid4(), to_schedule_version_id=b or uuid4(),
        from_content_hash="a" * 64, to_content_hash="b" * 64, content_hash_version="schedule-content.v1",
        change_set=[{"change_type": "TIMING_CHANGED"}], created_by=uuid4())


def mapping_set(record):
    return AmendmentMappingSetRecord(
        organization_id=record.organization_id, trial_id=record.trial_id, pair_id=record.id,
        mappings=[{"id": str(uuid4())}], mapping_revision="r1", engine_version="event-mapping.v1")


def decision(patient=None, org=ORG, *, key=None, open_=True, status=DecisionStatus.WAITING):
    return AdoptionDecisionRecord(
        organization_id=org, trial_id=uuid4(), patient_id=patient or uuid4(), pair_id=uuid4(),
        from_schedule_version_id=uuid4(), to_schedule_version_id=uuid4(),
        decision_type=DecisionType.ADOPT, mode="ADOPT_ON_DATE", status=status, is_open=open_,
        material_hash="m" * 64, reason="synthetic", actor_id=uuid4(), idempotency_key=key)


def test_a_pair_is_unique_per_ordered_versions_and_scoped_to_its_organization(uow):
    first = pair()
    uow.run(lambda tx: tx.amendments.add_pair(first))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.amendments.add_pair(pair(
            a=first.from_schedule_version_id, b=first.to_schedule_version_id)))
    # the reverse order is a different pair, and another organization may hold its own
    uow.run(lambda tx: tx.amendments.add_pair(pair(
        a=first.to_schedule_version_id, b=first.from_schedule_version_id)))
    uow.run(lambda tx: tx.amendments.add_pair(pair(
        org=OTHER_ORG, a=first.from_schedule_version_id, b=first.to_schedule_version_id)))
    assert uow.read(lambda tx: tx.amendments.get_pair(ORG, first.id)).change_set == first.change_set
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.amendments.get_pair(OTHER_ORG, first.id))
    assert uow.read(lambda tx: tx.amendments.find_pair(
        OTHER_ORG, first.from_schedule_version_id, first.to_schedule_version_id)).organization_id == OTHER_ORG
    assert uow.read(lambda tx: tx.amendments.find_pair(ORG, uuid4(), uuid4())) is None
    assert len(uow.read(lambda tx: tx.amendments.list_pairs_for_trial(ORG, first.trial_id))) == 1


def test_a_mapping_set_is_one_per_pair_and_changes_only_by_revision(uow):
    record = pair()
    uow.run(lambda tx: tx.amendments.add_pair(record))
    created = mapping_set(record)
    uow.run(lambda tx: tx.amendments.add_mapping_set(created))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.amendments.add_mapping_set(mapping_set(record)))
    saved = uow.run(lambda tx: tx.amendments.save_mapping_set(
        created.model_copy(update={"mapping_revision": "r2"}), expected_revision=1))
    assert saved.revision == 2 and saved.mapping_revision == "r2"
    with pytest.raises(StaleRevisionError):                      # a second writer with the old revision
        uow.run(lambda tx: tx.amendments.save_mapping_set(
            created.model_copy(update={"mapping_revision": "r3"}), expected_revision=1))
    stored = uow.read(lambda tx: tx.amendments.get_mapping_set(ORG, record.id))
    assert (stored.revision, stored.mapping_revision) == (2, "r2")
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.amendments.get_mapping_set(OTHER_ORG, record.id))
    with pytest.raises(RecordNotFoundError):
        uow.run(lambda tx: tx.amendments.save_mapping_set(
            created.model_copy(update={"organization_id": OTHER_ORG}), expected_revision=2))


def test_review_decisions_are_append_only_and_listed_oldest_first(uow):
    record = pair()
    rows = [MappingDecisionRecord(
        organization_id=ORG, trial_id=record.trial_id, pair_id=record.id, kind=kind,
        reviewer_id=None if kind == MappingDecisionKind.PROPOSED else uuid4(), reason="r",
        base_revision=index, resulting_revision=index + 1, resulting_mapping_revision=f"r{index}",
        created_at=utc_now() + timedelta(milliseconds=index))
        for index, kind in enumerate((MappingDecisionKind.PROPOSED, MappingDecisionKind.CONFIRM))]
    for row in rows:
        uow.run(lambda tx, row=row: tx.amendments.add_mapping_decision(row))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.amendments.add_mapping_decision(rows[0]))
    listed = uow.read(lambda tx: tx.amendments.list_mapping_decisions(ORG, record.id))
    assert [item.kind for item in listed] == [MappingDecisionKind.PROPOSED, MappingDecisionKind.CONFIRM]
    assert uow.read(lambda tx: tx.amendments.list_mapping_decisions(OTHER_ORG, record.id)) == []


def simulation(patient):
    now = utc_now()
    return AmendmentSimulationRecord(
        organization_id=ORG, trial_id=uuid4(), patient_id=patient, pair_id=uuid4(),
        from_schedule_version_id=uuid4(), to_schedule_version_id=uuid4(),
        adoption_point={"mode": "ADOPT_IMMEDIATELY"}, as_of=now.date(), horizon=now.date(),
        bindings={"x": 1}, binding_hash="h" * 64, created_by=uuid4(), created_at=now,
        expires_at=now + timedelta(minutes=30))


def test_a_simulation_status_moves_only_from_the_expected_state(uow):
    patient = uuid4()
    sim = simulation(patient)
    uow.run(lambda tx: tx.amendments.add_simulation(sim))
    decision_id = uuid4()
    done = uow.run(lambda tx: tx.amendments.set_simulation_status(
        ORG, sim.id, SimulationStatus.CONSUMED.value, expected={SimulationStatus.ACTIVE.value},
        consumed_by_decision_id=decision_id))
    assert str(getattr(done.status, "value", done.status)) == "CONSUMED"
    assert done.consumed_by_decision_id == decision_id
    with pytest.raises(StaleRevisionError):                     # already used: a second use is refused
        uow.run(lambda tx: tx.amendments.set_simulation_status(
            ORG, sim.id, SimulationStatus.CONSUMED.value, expected={SimulationStatus.ACTIVE.value}))
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.amendments.get_simulation(OTHER_ORG, sim.id))
    with pytest.raises(RecordNotFoundError):
        uow.run(lambda tx: tx.amendments.set_simulation_status(
            OTHER_ORG, sim.id, SimulationStatus.EXPIRED.value, expected={SimulationStatus.CONSUMED.value}))
    assert [s.id for s in uow.read(lambda tx: tx.amendments.list_simulations_for_patient(ORG, patient))] == [sim.id]
    assert uow.read(lambda tx: tx.amendments.list_simulations_for_patient(OTHER_ORG, patient)) == []


def test_a_patient_has_at_most_one_open_decision_enforced_by_the_store(uow):
    patient = uuid4()
    first = decision(patient)
    uow.run(lambda tx: tx.amendments.add_decision(first))
    with pytest.raises(DuplicateRecordError):                   # a second OPEN decision
        uow.run(lambda tx: tx.amendments.add_decision(decision(patient)))
    # a closed one, another patient's and another organization's own are all fine
    uow.run(lambda tx: tx.amendments.add_decision(
        decision(patient, open_=False, status=DecisionStatus.REMAINING)))
    uow.run(lambda tx: tx.amendments.add_decision(decision()))
    uow.run(lambda tx: tx.amendments.add_decision(decision(patient, org=OTHER_ORG)))
    assert uow.read(lambda tx: tx.amendments.find_open_decision(ORG, patient)).id == first.id
    assert uow.read(lambda tx: tx.amendments.find_open_decision(OTHER_ORG, patient)).organization_id == OTHER_ORG
    # closing the first frees the slot
    closed = uow.run(lambda tx: tx.amendments.save_decision(
        first.model_copy(update={"status": DecisionStatus.CANCELLED, "is_open": False}), expected_revision=1))
    assert closed.revision == 2
    uow.run(lambda tx: tx.amendments.add_decision(decision(patient)))
    # reopening a closed decision while another is open is refused too
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.amendments.save_decision(
            closed.model_copy(update={"status": DecisionStatus.WAITING, "is_open": True}),
            expected_revision=2))


def test_decision_idempotency_is_scoped_and_revisions_are_guarded(uow):
    patient = uuid4()
    first = decision(patient, key="k-1", open_=False, status=DecisionStatus.REMAINING)
    uow.run(lambda tx: tx.amendments.add_decision(first))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.amendments.add_decision(
            decision(patient, key="k-1", open_=False, status=DecisionStatus.REMAINING)))
    uow.run(lambda tx: tx.amendments.add_decision(
        decision(uuid4(), key="k-1", open_=False, status=DecisionStatus.REMAINING)))   # another patient
    found = uow.read(lambda tx: tx.amendments.find_decision_by_idempotency(ORG, patient, "k-1"))
    assert found.id == first.id
    assert uow.read(lambda tx: tx.amendments.find_decision_by_idempotency(OTHER_ORG, patient, "k-1")) is None
    with pytest.raises(StaleRevisionError):
        uow.run(lambda tx: tx.amendments.save_decision(
            first.model_copy(update={"reason": "later"}), expected_revision=7))
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.amendments.get_decision(OTHER_ORG, first.id))
    assert [d.id for d in uow.read(lambda tx: tx.amendments.list_decisions_for_patient(ORG, patient))] == [first.id]
    assert uow.read(lambda tx: tx.amendments.list_open_decisions(OTHER_ORG)) == []


def test_enum_and_date_fields_round_trip_losslessly(uow):
    patient = uuid4()
    row = decision(patient).model_copy(update={
        "effective_date": utc_now().date(), "trigger_state": {"due": False},
        "lease_expires_at": utc_now() + timedelta(minutes=5), "impact_signature": {"A:0>A:0": "PRESENT"}})
    uow.run(lambda tx: tx.amendments.add_decision(row))
    back = uow.read(lambda tx: tx.amendments.get_decision(ORG, row.id))
    assert back.effective_date == row.effective_date and back.status == DecisionStatus.WAITING
    assert back.lease_expires_at is not None and back.trigger_state == {"due": False}
    assert back.impact_signature == {"A:0>A:0": "PRESENT"}
