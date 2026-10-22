"""Fenced extraction ownership without rewriting historical runs."""
import copy
import importlib

from ..migrate import ensure_collection

VERSION = 13
NAME = "extraction_run_leases"


def apply(database):
    original = importlib.import_module("app.persistence.mongodb.migrations.0002_authoring_and_schedules")
    validator = copy.deepcopy(original.COLLECTIONS["extraction_runs"][0])
    validator["$jsonSchema"]["properties"].update({
        "attempt_number": {"bsonType": ["int", "long"], "minimum": 0},
        "lease_token": {"bsonType": ["string", "null"]},
        "lease_expires_at": {"bsonType": ["date", "null"]},
    })
    ensure_collection(database, "extraction_runs", validator)
    database.extraction_runs.create_index([("status", 1), ("lease_expires_at", 1)], name="ix_run_expired_lease")
