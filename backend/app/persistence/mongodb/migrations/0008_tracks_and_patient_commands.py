"""Schedule tracks and the append-only patient command ledger (P5-01 / P5-03)."""

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 8
NAME = "tracks_and_patient_commands"

_UUID = {"bsonType": "binData"}
_OPT_UUID = {"bsonType": ["binData", "null"]}
_STR = {"bsonType": "string"}
_OPT_STR = {"bsonType": ["string", "null"]}
_DATE = {"bsonType": "date"}
_INT = {"bsonType": ["int", "long"]}


def _schema(required, properties):
    return {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", *required],
        "properties": {"_id": _UUID, "doc_version": {"bsonType": "int"}, **properties},
    }}


COLLECTIONS = {
    # one row per track of a schedule version: the lookup / ownership projection
    # of the track definitions that live in the version's immutable content
    "schedule_tracks": (_schema(
        ["organization_id", "trial_id", "schedule_version_id", "code", "track"], {
            "organization_id": _UUID, "trial_id": _UUID, "schedule_version_id": _UUID,
            "code": _STR, "track": {"bsonType": "object"},
        }), [
        ([("schedule_version_id", ASCENDING), ("code", ASCENDING)], "uq_track_code", {"unique": True}),
        ([("organization_id", ASCENDING), ("trial_id", ASCENDING)], "ix_track_trial", {}),
    ]),
    "patient_commands": (_schema(
        ["organization_id", "patient_id", "schedule_version_id", "sequence", "command_type",
         "effective_at", "recorded_at", "actor_id", "reason", "payload", "payload_hash"], {
            "organization_id": _UUID, "patient_id": _UUID, "schedule_version_id": _UUID,
            "sequence": _INT, "command_type": {"enum": [
                "RECORD_ACTUAL", "CREATE_UNSCHEDULED", "RESCHEDULE_APPOINTMENT", "MARK_MISSED",
                "CANCEL_OCCURRENCE", "OVERRIDE_OCCURRENCE", "PAUSE_TRACK", "RESUME_TRACK",
                "REBASE_TRACK", "STOP_TRACK", "REVERSE"]},
            "track_code": _OPT_STR, "logical_key": _OPT_STR, "effective_at": _STR,
            "recorded_at": _DATE, "actor_id": _UUID, "reason": _STR,
            "payload": {"bsonType": "object"}, "payload_hash": _STR,
            "idempotency_key": _OPT_STR, "reverses_command_id": _OPT_UUID,
            "proposal_id": _OPT_UUID,
        }), [
        # two commands calculated against the same base cannot both be written
        ([("patient_id", ASCENDING), ("sequence", ASCENDING)], "uq_command_sequence", {"unique": True}),
        ([("organization_id", ASCENDING), ("patient_id", ASCENDING), ("idempotency_key", ASCENDING)],
         "uq_command_idempotency", {"unique": True, "partialFilterExpression": {
             "idempotency_key": {"$type": "string"}}}),
        # a command can be reversed once
        ([("patient_id", ASCENDING), ("reverses_command_id", ASCENDING)], "uq_command_reversal",
         {"unique": True, "partialFilterExpression": {"reverses_command_id": {"$type": "binData"}}}),
        ([("organization_id", ASCENDING), ("patient_id", ASCENDING), ("schedule_version_id", ASCENDING)],
         "ix_command_patient_version", {}),
    ]),
}


def apply(database) -> None:
    for name, (validator, indexes) in COLLECTIONS.items():
        ensure_collection(database, name, validator)
        for keys, index_name, options in indexes:
            database[name].create_index(keys, name=index_name, **options)
