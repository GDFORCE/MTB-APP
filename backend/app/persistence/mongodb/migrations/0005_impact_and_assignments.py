"""Schedule impact proposals and the append-only assignment history."""

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 5
NAME = "impact_and_assignments"

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
    "schedule_impact_proposals": (_schema(
        ["organization_id", "patient_id", "from_schedule_version_id", "to_schedule_version_id",
         "horizon", "input_hash", "status", "expires_at", "created_by"], {
            "organization_id": _UUID, "patient_id": _UUID, "from_schedule_version_id": _UUID,
            "to_schedule_version_id": _UUID, "horizon": _STR, "input_hash": _STR,
            "status": {"enum": ["PENDING", "CONFIRMED", "CANCELLED", "EXPIRED", "STALE"]},
            "expires_at": _DATE, "created_by": _UUID,
        }), [
        ([("patient_id", ASCENDING), ("status", ASCENDING)], "ix_proposal_patient_status", {}),
        ([("input_hash", ASCENDING)], "ix_proposal_hash", {}),
    ]),
    "patient_schedule_assignments": (_schema(
        ["organization_id", "patient_id", "schedule_version_id", "assignment_type", "assigned_at"], {
            "organization_id": _UUID, "patient_id": _UUID, "schedule_version_id": _UUID,
            "assignment_type": _STR, "assigned_at": _DATE,
        }), [
        ([("patient_id", ASCENDING), ("assigned_at", ASCENDING)], "ix_assignment_patient", {}),
        ([("impact_proposal_id", ASCENDING)], "ix_assignment_proposal", {}),
    ]),
}


def apply(database) -> None:
    for name, (validator, indexes) in COLLECTIONS.items():
        ensure_collection(database, name, validator)
        for keys, index_name, options in indexes:
            database[name].create_index(keys, name=index_name, **options)
