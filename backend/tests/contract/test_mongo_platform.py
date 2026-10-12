"""MongoDB-only platform behaviour: validators, indexes, migrations, retries."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError, OperationFailure, WriteError

from app.application.records import AuditRecord
from app.persistence.mongodb.migrate import LEDGER, run_migrations

pytestmark = pytest.mark.usefixtures("mongo_client")


def _db(mongo_uow):
    return mongo_uow._database


def test_migrations_are_idempotent_and_recorded(mongo_uow):
    database = _db(mongo_uow)
    assert run_migrations(database) == []  # fixture already applied everything
    rows = list(database[LEDGER].find({}, {"_id": 0, "version": 1, "status": 1}).sort("version"))
    assert rows == [{"version": 1, "status": "COMPLETED"}, {"version": 2, "status": "COMPLETED"}]


def test_an_applied_migration_that_was_edited_is_refused(mongo_uow):
    database = _db(mongo_uow)
    database[LEDGER].update_one({"version": 1}, {"$set": {"checksum": "tampered"}})
    with pytest.raises(RuntimeError, match="edited after it was applied"):
        run_migrations(database)


def test_a_half_applied_migration_resumes(mongo_uow):
    database = _db(mongo_uow)
    database[LEDGER].update_one({"version": 1}, {"$set": {"status": "RUNNING"}})
    assert run_migrations(database) == [1]
    assert database[LEDGER].count_documents({"status": "COMPLETED"}) == 2


def test_the_validator_rejects_a_malformed_audit_document(mongo_uow):
    database = _db(mongo_uow)
    with pytest.raises(WriteError):
        database["audit_events"].insert_one({"_id": uuid4(), "action": "no org, no entity"})


def test_the_validator_rejects_a_string_where_an_instant_is_required(mongo_uow):
    database = _db(mongo_uow)
    good = AuditRecord(organization_id=uuid4(), action="A", entity_type="T", entity_id=uuid4())
    from app.persistence.mongodb.audit_repository import _to_document

    doc = _to_document(good)
    doc["created_at"] = "2026-01-01"
    with pytest.raises(WriteError):
        database["audit_events"].insert_one(doc)


def test_ids_are_uuids_not_object_ids(mongo_uow):
    from bson.binary import Binary

    record = AuditRecord(organization_id=uuid4(), action="A", entity_type="T", entity_id=uuid4())
    mongo_uow.run(lambda tx: tx.audit.append(record))
    raw = _db(mongo_uow).get_collection("audit_events").find_one({"_id": record.id})
    assert raw["_id"] == record.id
    assert type(raw["_id"]).__name__ == "UUID"
    assert not isinstance(raw["_id"], Binary) or raw["_id"].subtype == 4


def test_a_transient_transaction_error_reruns_the_callback_exactly_once_committed(mongo_uow):
    attempts = []
    record = AuditRecord(organization_id=uuid4(), action="RETRY", entity_type="T", entity_id=uuid4())

    def write(tx):
        attempts.append(1)
        tx.audit.append(record)
        if len(attempts) == 1:
            error = OperationFailure("simulated write conflict")
            error._add_error_label("TransientTransactionError")
            raise error

    mongo_uow.run(write)
    assert len(attempts) == 2
    rows = mongo_uow.read(lambda tx: tx.audit.list_for_entity(record.organization_id, "T", record.entity_id))
    assert len(rows) == 1


def test_nested_units_of_work_are_refused(mongo_uow):
    with pytest.raises(RuntimeError, match="nested"):
        mongo_uow.run(lambda tx: tx.run(lambda inner: None))


def test_a_duplicate_key_inside_a_transaction_aborts_all_of_it(mongo_uow):
    org, entity = uuid4(), uuid4()
    keep = AuditRecord(organization_id=org, action="KEEP", entity_type="T", entity_id=entity)
    seed = AuditRecord(organization_id=org, action="SEED", entity_type="T", entity_id=entity)
    mongo_uow.run(lambda tx: tx.audit.append(seed))

    def write(tx):
        tx.audit.append(keep)
        tx.audit.append(seed)  # duplicate id

    with pytest.raises(Exception):
        mongo_uow.run(write)
    rows = mongo_uow.read(lambda tx: tx.audit.list_for_entity(org, "T", entity))
    assert [item.action for item in rows] == ["SEED"]
