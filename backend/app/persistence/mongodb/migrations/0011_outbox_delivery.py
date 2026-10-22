"""Add fenced delivery states without changing the original migration checksum."""
import importlib

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 11
NAME = "outbox_delivery"


def apply(database):
    original = importlib.import_module("app.persistence.mongodb.migrations.0004_commands_and_outbox")
    import copy
    validator = copy.deepcopy(original.COLLECTIONS["outbox"][0])
    properties = validator["$jsonSchema"]["properties"]
    properties["status"] = {"enum": ["PENDING", "PROCESSING", "SENT", "FAILED", "DEAD"]}
    properties.update({
        "available_at": {"bsonType": ["date", "null"]},
        "lease_expires_at": {"bsonType": ["date", "null"]},
        "lease_token": {"bsonType": ["string", "null"]},
        "max_attempts": {"bsonType": "int", "minimum": 1, "maximum": 20},
        "last_error_code": {"bsonType": ["string", "null"]},
    })
    ensure_collection(database, "outbox", validator)
    database["outbox"].create_index(
        [("status", ASCENDING), ("available_at", ASCENDING), ("created_at", ASCENDING)], name="ix_outbox_due")
    database["outbox"].create_index(
        [("status", ASCENDING), ("lease_expires_at", ASCENDING)], name="ix_outbox_expired_lease")
