"""P9-05: metadata for archived evaluation projections (the cold payload lives in object storage)."""
from ..migrate import ensure_collection

VERSION = 15
NAME = "evaluation_archives"


def apply(database):
    uuid, text, date = {"bsonType": "binData"}, {"bsonType": "string"}, {"bsonType": "date"}
    nullable = lambda spec: {"bsonType": [spec["bsonType"], "null"]}
    ensure_collection(database, "evaluation_archives", {"$jsonSchema": {
        "bsonType": "object", "additionalProperties": False,
        "required": ["_id", "doc_version", "organization_id", "patient_id", "evaluation_id", "state",
                     "hold", "format", "event_count", "confinement_count", "created_at"],
        "properties": {
            "_id": uuid, "doc_version": {"enum": [1]}, "organization_id": uuid, "patient_id": uuid,
            "evaluation_id": uuid, "state": {"enum": ["HOT_HELD", "ARCHIVED", "RESTORED"]},
            "hold": {"bsonType": "bool"}, "hold_reason": nullable(text), "held_by": nullable(uuid),
            "object_key": nullable(text),
            "payload_hash": {"bsonType": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
            "event_count": {"bsonType": ["int", "long"]}, "confinement_count": {"bsonType": ["int", "long"]},
            "format": text, "archived_by": nullable(uuid), "archived_at": nullable(date),
            "restored_at": nullable(date), "created_at": date}}})
    database.evaluation_archives.create_index(
        [("evaluation_id", 1)], name="uq_archive_evaluation", unique=True)
    database.evaluation_archives.create_index(
        [("organization_id", 1), ("state", 1)], name="ix_archive_org_state")
