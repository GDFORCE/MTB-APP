"""Contract for the append-only patient command ledger (P14-05), on the fake AND real MongoDB."""
from datetime import date
from uuid import uuid4

import pytest

from app.application.errors import DuplicateRecordError, RecordNotFoundError
from app.application.patients import PatientCommandRecord

ORG, PATIENT, VERSION = uuid4(), uuid4(), uuid4()


def command(sequence, *, key=None, org=ORG, patient=PATIENT, **extra):
    return PatientCommandRecord(
        organization_id=org, patient_id=patient, schedule_version_id=VERSION, sequence=sequence,
        command_type="RESCHEDULE_APPOINTMENT", effective_at=date(2026, 1, 8), actor_id=uuid4(), reason="synthetic",
        idempotency_key=key, payload_hash="a" * 64, **extra)


def test_commands_round_trip_losslessly_and_list_in_sequence_order(uow):
    rows = [command(3, key="c"), command(1, key="a", payload={"to": "2026-01-09"}), command(2, key="b")]
    for row in rows:
        uow.run(lambda tx, row=row: tx.ledger.add(row))
    listed = uow.read(lambda tx: tx.ledger.list_for_patient(ORG, PATIENT))
    assert [item.sequence for item in listed] == [1, 2, 3]
    assert listed[0] == rows[1] and uow.read(lambda tx: tx.ledger.get(ORG, rows[0].id)) == rows[0]


def test_sequence_and_idempotency_key_are_each_unique_and_a_clash_writes_nothing(uow):
    uow.run(lambda tx: tx.ledger.add(command(1, key="once")))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.ledger.add(command(1, key="other")))
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.ledger.add(command(2, key="once")))
    assert [item.sequence for item in uow.read(lambda tx: tx.ledger.list_for_patient(ORG, PATIENT))] == [1]
    found = uow.read(lambda tx: tx.ledger.find_by_idempotency(ORG, PATIENT, "once"))
    assert found is not None and found.sequence == 1
    assert uow.read(lambda tx: tx.ledger.find_by_idempotency(ORG, PATIENT, "missing")) is None


def test_the_same_sequence_may_exist_for_another_patient_and_organizations_are_isolated(uow):
    other_patient, other_org = uuid4(), uuid4()
    mine = command(1, key="k")
    uow.run(lambda tx: tx.ledger.add(mine))
    uow.run(lambda tx: tx.ledger.add(command(1, key="k", patient=other_patient)))
    assert uow.read(lambda tx: tx.ledger.list_for_patient(other_org, PATIENT)) == []
    assert uow.read(lambda tx: tx.ledger.find_by_idempotency(other_org, PATIENT, "k")) is None
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.ledger.get(other_org, mine.id))


def test_a_ledger_row_is_never_updated_or_deleted_by_the_port(uow):
    assert not any(name in dir(uow.read(lambda tx: tx.ledger)) for name in ("save", "update", "delete", "remove"))


def test_every_store_rejects_an_unknown_command_type_at_the_record_boundary(uow):
    with pytest.raises(ValueError, match="unknown patient command type"):
        command(1, key="x").model_copy(update={}).__class__.model_validate(
            {**command(1, key="x").model_dump(), "command_type": "TELEPORT"})
