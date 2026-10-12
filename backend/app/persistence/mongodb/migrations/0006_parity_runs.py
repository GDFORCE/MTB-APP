"""Recorded engine-vs-operational comparisons that gate a trial cutover."""

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 6
NAME = "parity_runs"

_UUID = {"bsonType": "binData"}
_STR = {"bsonType": "string"}
_DATE = {"bsonType": "date"}

_VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "doc_version", "organization_id", "trial_id", "verdict", "ran_at"],
    "properties": {
        "_id": _UUID, "doc_version": {"bsonType": "int"}, "organization_id": _UUID,
        "trial_id": _UUID, "verdict": _STR, "ran_at": _DATE,
    },
}}


def apply(database) -> None:
    ensure_collection(database, "parity_runs", _VALIDATOR)
    database["parity_runs"].create_index(
        [("organization_id", ASCENDING), ("trial_id", ASCENDING), ("ran_at", ASCENDING)],
        name="ix_parity_trial_ran")
