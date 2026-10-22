"""P14-01/P14-02: the collection catalog is complete and the database boundary rejects bad data."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo.errors import WriteError

from app.persistence.mongodb.catalog import CATALOG, INFRASTRUCTURE, markdown

pytest_plugins = ["tests.contract.conftest"]
DOC = Path(__file__).resolve().parents[2] / "docs" / "uctsm-mongo-collections.md"


def collections(database):
    return sorted(name for name in database.list_collection_names() if not name.startswith("system."))


def test_catalog_describes_exactly_the_migrated_collections_and_the_doc_is_current(_mongo_database):
    assert collections(_mongo_database) == sorted(CATALOG)
    for name, item in CATALOG.items():
        assert item.owner and item.purpose and item.bounded, name
        assert all(ref in CATALOG for ref in item.references), name
    assert DOC.read_text(encoding="utf-8") == markdown(), "run: python -m app.persistence.mongodb.catalog"


def validator_of(database, name):
    info = database.command("listCollections", filter={"name": name})["cursor"]["firstBatch"][0]
    return info.get("options", {}).get("validator")


def test_every_domain_collection_has_a_strict_validator_with_uuid_identity(_mongo_database):
    for name in sorted(set(CATALOG) - INFRASTRUCTURE):
        validator = validator_of(_mongo_database, name)
        assert validator, f"{name} has no validator"
        text = str(validator)
        assert "binData" in text and "ObjectId" not in text and "objectId" not in text, name
        info = _mongo_database.command("listCollections", filter={"name": name})["cursor"]["firstBatch"][0]
        # schedule_versions is deliberately "moderate" (migration 0009): hashless legacy documents
        # are not rejected on a later write, while every NEW insert is still validated.
        expected_level = "moderate" if name == "schedule_versions" else "strict"
        assert info["options"].get("validationLevel") == expected_level, name
        assert info["options"].get("validationAction") == "error", name


@pytest.mark.parametrize("bad", ["empty", "object_id", "string_id"])
def test_invalid_documents_are_rejected_at_the_database_boundary(_mongo_database, bad):
    document = {"empty": lambda: {"_id": uuid4()},
                "object_id": lambda: {"_id": ObjectId(), "doc_version": 1},
                "string_id": lambda: {"_id": str(uuid4()), "doc_version": 1}}[bad]
    for name in sorted(set(CATALOG) - INFRASTRUCTURE):
        with pytest.raises(WriteError):
            _mongo_database[name].insert_one(document())
        assert _mongo_database[name].count_documents({}) == 0


def test_duplicate_logical_records_are_prevented_by_unique_indexes(mongo_uow, _mongo_database):
    unique = {}
    for name in sorted(set(CATALOG) - INFRASTRUCTURE):
        unique[name] = [i["name"] for i in _mongo_database[name].list_indexes() if i.get("unique")]
    for name in ("patients", "schedule_versions", "schedule_evaluations", "patient_events", "outbox",
                 "outbox_inbox", "command_receipts", "evaluation_archives", "patient_commands",
                 "schedule_events"):
        assert unique[name], f"{name} has no unique logical key"
    # protocol_document_identities is deliberately NOT unique (migration 0007): every upload keeps its
    # own provenance and decision; duplicates are classified by the identity gate, not rejected.
    # one concrete duplicate through the real repository
    from app.application.records import OutboxRecord
    org, key = uuid4(), uuid4().hex
    row = OutboxRecord(organization_id=org, aggregate_type="T", aggregate_id=uuid4(), event_type="E", dedupe_key=key)
    mongo_uow.run(lambda tx: tx.outbox.add(row))
    clone = row.model_copy(update={"id": uuid4(), "created_at": datetime.now(timezone.utc)})
    from app.application.errors import DuplicateRecordError
    with pytest.raises(DuplicateRecordError):
        mongo_uow.run(lambda tx: tx.outbox.add(clone))
