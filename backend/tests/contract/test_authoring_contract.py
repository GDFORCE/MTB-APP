"""Contract for the authoring repository (fake and MongoDB)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.authoring import (
    ExtractionRunRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord,
)
from app.application.errors import DuplicateRecordError, RecordNotFoundError

from .support import ORG, seed_definition


def test_trial_lookup_is_scoped_to_the_organization(uow):
    trial = TrialRecord(organization_id=ORG, external_trial_id="ext-1", study_title="S")
    uow.run(lambda tx: tx.authoring.add_trial(trial))
    assert uow.read(lambda tx: tx.authoring.get_trial(ORG, trial.id)) == trial
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.authoring.get_trial(uuid4(), trial.id))
    assert uow.read(lambda tx: tx.authoring.find_trial_by_external_id(ORG, "ext-1")).id == trial.id
    assert uow.read(lambda tx: tx.authoring.find_trial_by_external_id(uuid4(), "ext-1")) is None


def test_an_external_trial_id_is_unique_per_organization(uow):
    first = TrialRecord(organization_id=ORG, external_trial_id="ext-dup")
    uow.run(lambda tx: tx.authoring.add_trial(first))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.authoring.add_trial(
            TrialRecord(organization_id=ORG, external_trial_id="ext-dup")))
    # trials without an external id never collide with one another
    uow.run(lambda tx: tx.authoring.add_trial(TrialRecord(organization_id=ORG)))
    uow.run(lambda tx: tx.authoring.add_trial(TrialRecord(organization_id=ORG)))


def test_read_mode_change_is_persisted(uow):
    trial = TrialRecord(organization_id=ORG)
    uow.run(lambda tx: tx.authoring.add_trial(trial))
    uow.run(lambda tx: tx.authoring.set_trial_read_mode(ORG, trial.id, "ENGINE"))
    assert uow.read(lambda tx: tx.authoring.get_trial(ORG, trial.id)).schedule_read_mode == "ENGINE"
    with pytest.raises(RecordNotFoundError):
        uow.run(lambda tx: tx.authoring.set_trial_read_mode(ORG, uuid4(), "ENGINE"))


def test_protocol_version_labels_and_hashes_resolve_within_a_protocol(uow):
    definition = seed_definition(uow)
    labels = uow.read(lambda tx: tx.authoring.list_protocol_version_labels(definition.protocol_id))
    assert labels == ["1"]
    found = uow.read(lambda tx: tx.authoring.find_protocol_version_by_hash(
        definition.protocol_id, "a" * 64))
    assert found is not None and found.id == definition.protocol_version_id
    assert uow.read(lambda tx: tx.authoring.find_protocol_version_by_hash(
        definition.protocol_id, "b" * 64)) is None
    clash = ProtocolVersionRecord(
        organization_id=ORG, trial_id=definition.trial_id, protocol_id=definition.protocol_id,
        version_label="1", document_name="x.pdf", document_uri="private://x", document_hash="c" * 64)
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.authoring.add_protocol_version(clash))


def test_the_current_protocol_version_pointer_moves_only_when_told(uow):
    definition = seed_definition(uow)
    protocol = uow.read(lambda tx: tx.authoring.get_protocol(definition.protocol_id))
    assert protocol.current_version_id is None
    uow.run(lambda tx: tx.authoring.set_current_protocol_version(
        definition.protocol_id, definition.protocol_version_id))
    assert uow.read(lambda tx: tx.authoring.get_protocol(
        definition.protocol_id)).current_version_id == definition.protocol_version_id
    assert uow.read(lambda tx: tx.authoring.find_protocol_for_trial(definition.trial_id)).id \
        == definition.protocol_id


def test_a_definition_is_found_by_version_name_and_type_and_is_unique(uow):
    definition = seed_definition(uow)
    found = uow.read(lambda tx: tx.authoring.find_definition(
        definition.protocol_version_id, definition.name, definition.schedule_type))
    assert found == definition
    twin = ScheduleDefinitionRecord(
        organization_id=ORG, trial_id=definition.trial_id, protocol_id=definition.protocol_id,
        protocol_version_id=definition.protocol_version_id, name=definition.name)
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.authoring.add_definition(twin))


def test_extraction_run_idempotency_and_trace_round_trip(uow):
    definition = seed_definition(uow)
    run = ExtractionRunRecord(
        organization_id=ORG, protocol_version_id=definition.protocol_version_id,
        idempotency_key="native-import:abc", prompt_version="uctsm-extractor.v1",
        document_hash="a" * 64, configuration={"k": 1})
    uow.run(lambda tx: tx.authoring.add_run(run))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.authoring.add_run(run.model_copy(update={"id": uuid4()})))
    found = uow.read(lambda tx: tx.authoring.find_run_by_idempotency(ORG, "native-import:abc"))
    assert found is not None and found.id == run.id and found.trace == {}

    done = found.model_copy(update={
        "status": "COMPLETED", "trace": {"options": [{"schedule_version_id": str(uuid4()),
                                                      "accountability": {"covered": 12}}]}})
    uow.run(lambda tx: tx.authoring.save_run(done))
    reloaded = uow.read(lambda tx: tx.authoring.get_run(run.id))
    assert reloaded.status == "COMPLETED"
    assert reloaded.trace == done.trace
    assert uow.read(lambda tx: tx.authoring.find_run_by_idempotency(uuid4(), "native-import:abc")) is None


def test_missing_records_raise_not_found(uow):
    for call in (
        lambda tx: tx.authoring.get_protocol(uuid4()),
        lambda tx: tx.authoring.get_protocol_version(uuid4()),
        lambda tx: tx.authoring.get_definition(uuid4()),
        lambda tx: tx.authoring.get_run(uuid4()),
        lambda tx: tx.schedules.get(uuid4()),
        lambda tx: tx.schedules.get_header(uuid4()),
    ):
        with pytest.raises(RecordNotFoundError):
            uow.read(call)
