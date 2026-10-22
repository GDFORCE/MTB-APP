"""P11-02: resumable legacy -> UCTSM migration checkpoints and source mappings."""
from ..migrate import ensure_collection

VERSION = 17
NAME = "legacy_migration"


def apply(database):
    uuid, text, date = {"bsonType": "binData"}, {"bsonType": "string"}, {"bsonType": "date"}
    count = {"bsonType": ["int", "long"]}
    ensure_collection(database, "migration_runs", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", "organization_id", "run_key", "trial_id", "external_trial_id",
                     "source_system", "status", "counts", "started_by", "started_at", "updated_at", "revision"],
        "properties": {"_id": uuid, "doc_version": {"enum": [1]}, "organization_id": uuid, "run_key": text,
                       "trial_id": uuid, "external_trial_id": text, "source_system": text,
                       "status": {"enum": ["RUNNING", "COMPLETED", "PARTIAL"]},
                       "cursor": {"bsonType": ["string", "null"]}, "counts": {"bsonType": "object"},
                       "started_by": uuid, "started_at": date, "updated_at": date, "revision": count}}})
    database.migration_runs.create_index(
        [("organization_id", 1), ("run_key", 1)], name="uq_migration_run_key", unique=True)
    ensure_collection(database, "migration_source_mappings", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", "organization_id", "source_system", "source_id", "source_hash",
                     "status", "updated_at"],
        "properties": {"_id": uuid, "doc_version": {"enum": [1]}, "organization_id": uuid,
                       "source_system": text, "source_id": text,
                       "source_hash": {"bsonType": "string", "pattern": "^[a-f0-9]{64}$"},
                       "patient_id": {"bsonType": ["binData", "null"]},
                       "status": {"enum": ["MIGRATED", "RECONCILED", "DIFFERENCES", "FAILED"]},
                       "parity_verdict": {"bsonType": ["string", "null"]},
                       "compared": count, "matched": count,
                       "difference_codes": {"bsonType": "array"},
                       "error_code": {"bsonType": ["string", "null"]},
                       "run_id": {"bsonType": ["binData", "null"]}, "updated_at": date,
                       "extra": {"bsonType": "object"}}}})
    database.migration_source_mappings.create_index(
        [("organization_id", 1), ("source_system", 1), ("source_id", 1)],
        name="uq_migration_source", unique=True)
    database.migration_source_mappings.create_index(
        [("organization_id", 1), ("patient_id", 1)], name="ix_migration_patient",
        partialFilterExpression={"patient_id": {"$type": "binData"}})
