"""Idempotent command receipts and the transactional outbox (P1-04)."""

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 4
NAME = "commands_and_outbox"

_UUID = {"bsonType": "binData"}
_STR = {"bsonType": "string"}
_DATE = {"bsonType": "date"}


def _schema(required, properties):
    return {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", *required],
        "properties": {"_id": _UUID, "doc_version": {"bsonType": "int"}, **properties},
    }}


COLLECTIONS = {
    "command_receipts": (_schema(
        ["organization_id", "patient_id", "idempotency_key", "command_type", "payload_hash", "result"], {
            "organization_id": _UUID, "patient_id": _UUID, "idempotency_key": _STR,
            "command_type": _STR, "payload_hash": _STR, "result": {"bsonType": "object"},
        }), [
        ([("organization_id", ASCENDING), ("patient_id", ASCENDING), ("idempotency_key", ASCENDING)],
         "uq_command_receipt", {"unique": True}),
    ]),
    "outbox": (_schema(
        ["organization_id", "aggregate_type", "aggregate_id", "event_type", "dedupe_key", "status",
         "created_at"], {
            "organization_id": _UUID, "aggregate_type": _STR, "aggregate_id": _UUID,
            "event_type": _STR, "dedupe_key": _STR, "status": {"enum": ["PENDING", "SENT", "FAILED"]},
            "created_at": _DATE,
        }), [
        ([("dedupe_key", ASCENDING)], "uq_outbox_dedupe", {"unique": True}),
        ([("status", ASCENDING), ("created_at", ASCENDING)], "ix_outbox_pending", {}),
    ]),
}


def apply(database) -> None:
    for name, (validator, indexes) in COLLECTIONS.items():
        ensure_collection(database, name, validator)
        for keys, index_name, options in indexes:
            database[name].create_index(keys, name=index_name, **options)
