"""audit_events: append-only audit trail (replaces uctsm_audit_events)."""

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 1
NAME = "audit_events"

VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "schema_version", "organization_id", "action",
                 "entity_type", "entity_id", "created_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "binData"},
        "schema_version": {"bsonType": "int"},
        "organization_id": {"bsonType": "binData"},
        "actor_id": {"bsonType": ["binData", "null"]},
        "action": {"bsonType": "string", "minLength": 1, "maxLength": 128},
        "entity_type": {"bsonType": "string", "minLength": 1, "maxLength": 64},
        "entity_id": {"bsonType": "binData"},
        "before": {},
        "after": {},
        "reason": {"bsonType": ["string", "null"]},
        "metadata": {"bsonType": "object"},
        "created_at": {"bsonType": "date"},
    },
}}


def apply(database) -> None:
    ensure_collection(database, "audit_events", VALIDATOR)
    audit = database["audit_events"]
    audit.create_index(
        [("organization_id", ASCENDING), ("entity_type", ASCENDING),
         ("entity_id", ASCENDING), ("created_at", ASCENDING)],
        name="ix_audit_entity")
    audit.create_index(
        [("organization_id", ASCENDING), ("action", ASCENDING), ("created_at", ASCENDING)],
        name="ix_audit_action")
