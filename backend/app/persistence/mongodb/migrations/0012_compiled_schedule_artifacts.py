"""Content/version-bound immutable validation cache; does not rewrite old approvals."""
from ..migrate import ensure_collection

VERSION = 12
NAME = "compiled_schedule_artifacts"


def apply(database):
    ensure_collection(database, "compiled_schedule_artifacts", {"$jsonSchema": {
        "bsonType": "object", "additionalProperties": False,
        "required": ["_id", "doc_version", "organization_id", "schedule_version_id", "cache_key", "artifact"],
        "properties": {"_id": {"bsonType": "binData"}, "doc_version": {"enum": [1]},
            "organization_id": {"bsonType": "binData"}, "schedule_version_id": {"bsonType": "binData"},
            "cache_key": {"bsonType": "string", "pattern": "^[a-f0-9]{64}$"},
            "artifact": {"bsonType": "object"}}}})
    database.compiled_schedule_artifacts.create_index(
        [("organization_id", 1), ("schedule_version_id", 1), ("cache_key", 1)],
        name="uq_compiled_version_key", unique=True)
