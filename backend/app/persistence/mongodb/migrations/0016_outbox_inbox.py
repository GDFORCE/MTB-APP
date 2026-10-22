"""P13-03: consumer-side inbox; unique per (consumer, dedupe_key) so a replay is applied once."""
from ..migrate import ensure_collection

VERSION = 16
NAME = "outbox_inbox"


def apply(database):
    uuid, text = {"bsonType": "binData"}, {"bsonType": "string"}
    ensure_collection(database, "outbox_inbox", {"$jsonSchema": {
        "bsonType": "object", "additionalProperties": False,
        "required": ["_id", "doc_version", "organization_id", "consumer", "dedupe_key", "outbox_id", "processed_at"],
        "properties": {"_id": uuid, "doc_version": {"enum": [1]}, "organization_id": uuid,
                       "consumer": text, "dedupe_key": text, "outbox_id": uuid,
                       "processed_at": {"bsonType": "date"}}}})
    database.outbox_inbox.create_index(
        [("consumer", 1), ("dedupe_key", 1)], name="uq_inbox_consumer_key", unique=True)
