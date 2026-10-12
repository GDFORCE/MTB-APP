"""Authoring plane: trials, protocols, versions, definitions, runs, schedule versions."""

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 2
NAME = "authoring_and_schedules"

_UUID = {"bsonType": "binData"}
_OPT_UUID = {"bsonType": ["binData", "null"]}
_DATE = {"bsonType": "date"}
_OPT_DATE = {"bsonType": ["date", "null"]}
_STR = {"bsonType": "string"}
_OPT_STR = {"bsonType": ["string", "null"]}

SCHEDULE_STATUSES = [
    "DRAFT", "EXTRACTED", "VALIDATION_REQUIRED", "IN_REVIEW", "APPROVED",
    "REJECTED", "SUPERSEDED", "ARCHIVED",
]


def _schema(required, properties):
    return {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", *required],
        "properties": {"_id": _UUID, "doc_version": {"bsonType": "int"}, **properties},
    }}


COLLECTIONS = {
    "trials": (_schema(["organization_id", "schedule_read_mode", "created_at"], {
        "organization_id": _UUID,
        "schedule_read_mode": {"enum": ["LEGACY", "ENGINE"]},
        "created_at": _DATE,
    }), [
        ([("organization_id", ASCENDING), ("external_trial_id", ASCENDING)], "uq_trial_external",
         {"unique": True, "partialFilterExpression": {"external_trial_id": {"$type": "string"}}}),
    ]),
    "protocols": (_schema(["organization_id", "trial_id", "protocol_number"], {
        "organization_id": _UUID, "trial_id": _UUID, "protocol_number": _STR,
        "current_version_id": _OPT_UUID,
    }), [
        ([("trial_id", ASCENDING), ("protocol_number", ASCENDING)], "uq_protocol_number", {"unique": True}),
    ]),
    "protocol_versions": (_schema(
        ["organization_id", "trial_id", "protocol_id", "version_label", "document_hash"], {
            "organization_id": _UUID, "trial_id": _UUID, "protocol_id": _UUID,
            "version_label": _STR, "document_hash": _STR,
        }), [
        ([("protocol_id", ASCENDING), ("version_label", ASCENDING)], "uq_protocol_version_label",
         {"unique": True}),
        ([("protocol_id", ASCENDING), ("document_hash", ASCENDING)], "ix_protocol_version_hash", {}),
    ]),
    "schedule_definitions": (_schema(
        ["organization_id", "trial_id", "protocol_id", "protocol_version_id", "name", "schedule_type"], {
            "organization_id": _UUID, "trial_id": _UUID, "protocol_id": _UUID,
            "protocol_version_id": _UUID, "name": _STR, "schedule_type": _STR,
        }), [
        ([("protocol_version_id", ASCENDING), ("name", ASCENDING), ("schedule_type", ASCENDING)],
         "uq_definition_name", {"unique": True}),
    ]),
    "extraction_runs": (_schema(
        ["organization_id", "protocol_version_id", "status", "prompt_version", "document_hash"], {
            "organization_id": _UUID, "protocol_version_id": _UUID,
            "status": {"enum": ["QUEUED", "RUNNING", "PARTIAL", "COMPLETED", "FAILED"]},
            "prompt_version": _STR, "document_hash": _STR,
        }), [
        ([("organization_id", ASCENDING), ("idempotency_key", ASCENDING)], "uq_run_idempotency",
         {"unique": True, "partialFilterExpression": {"idempotency_key": {"$type": "string"}}}),
        ([("protocol_version_id", ASCENDING)], "ix_run_protocol_version", {}),
    ]),
    "extraction_run_traces": (_schema(["trace"], {"trace": {"bsonType": "object"}}), []),
    "schedule_versions": (_schema(
        ["organization_id", "trial_id", "protocol_id", "protocol_version_id",
         "schedule_definition_id", "version_number", "status", "content", "revision"], {
            "organization_id": _UUID, "trial_id": _UUID, "protocol_id": _UUID,
            "protocol_version_id": _UUID, "schedule_definition_id": _UUID,
            "version_number": {"bsonType": "int"},
            "status": {"enum": SCHEDULE_STATUSES},
            "content": {"bsonType": "object"},
            "revision": {"bsonType": ["int", "long"]},
        }), [
        ([("schedule_definition_id", ASCENDING), ("version_number", ASCENDING)], "uq_version_number",
         {"unique": True}),
        ([("organization_id", ASCENDING), ("status", ASCENDING), ("updated_at", ASCENDING)],
         "ix_version_status", {}),
        ([("extraction_run_id", ASCENDING)], "ix_version_run", {}),
        ([("trial_id", ASCENDING), ("status", ASCENDING)], "ix_version_trial", {}),
    ]),
    "schedule_events": (_schema(["organization_id", "schedule_version_id", "code", "event"], {
        "organization_id": _UUID, "schedule_version_id": _UUID, "code": _STR,
        "event": {"bsonType": "object"},
    }), [
        ([("schedule_version_id", ASCENDING), ("code", ASCENDING)], "uq_event_code", {"unique": True}),
    ]),
    "schedule_evidence": (_schema(["organization_id", "schedule_version_id", "seq", "evidence"], {
        "organization_id": _UUID, "schedule_version_id": _UUID, "seq": {"bsonType": ["int", "long"]},
        "evidence": {"bsonType": "object"},
    }), [
        ([("schedule_version_id", ASCENDING), ("seq", ASCENDING)], "ix_evidence_version", {}),
    ]),
    "schedule_claims": (_schema(["organization_id", "schedule_version_id", "seq", "claim"], {
        "organization_id": _UUID, "schedule_version_id": _UUID, "seq": {"bsonType": ["int", "long"]},
        "claim": {"bsonType": "object"},
    }), [
        ([("schedule_version_id", ASCENDING), ("seq", ASCENDING)], "ix_claim_version", {}),
    ]),
    "schedule_validation_issues": (_schema(
        ["organization_id", "schedule_version_id", "seq", "status", "issue"], {
            "organization_id": _UUID, "schedule_version_id": _UUID,
            "seq": {"bsonType": ["int", "long"]}, "status": _STR, "issue": {"bsonType": "object"},
        }), [
        ([("schedule_version_id", ASCENDING), ("status", ASCENDING)], "ix_issue_version_status", {}),
        ([("schedule_version_id", ASCENDING), ("seq", ASCENDING)], "ix_issue_version_seq", {}),
    ]),
    "schedule_review_decisions": (_schema(
        ["schedule_version_id", "decision", "reviewer_id", "created_at"], {
            "schedule_version_id": _UUID, "decision": _STR, "reviewer_id": _UUID, "created_at": _DATE,
        }), [
        ([("schedule_version_id", ASCENDING), ("created_at", ASCENDING)], "ix_decision_version", {}),
        ([("schedule_version_id", ASCENDING), ("reviewer_id", ASCENDING), ("decision", ASCENDING)],
         "ix_decision_reviewer", {}),
    ]),
}


def apply(database) -> None:
    for name, (validator, indexes) in COLLECTIONS.items():
        ensure_collection(database, name, validator)
        for keys, index_name, options in indexes:
            database[name].create_index(keys, name=index_name, **options)
