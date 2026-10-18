"""Protocol identity gate (P2-01): identities, reviews, jobs, durable results."""

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 7
NAME = "protocol_identity"

_UUID = {"bsonType": "binData"}
_STR = {"bsonType": "string"}
_INT = {"bsonType": ["int", "long"]}
_DATE = {"bsonType": "date"}


def _schema(required, properties):
    return {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", *required],
        "properties": {"_id": _UUID, "doc_version": {"bsonType": "int"}, **properties},
    }}


COLLECTIONS = {
    "protocol_document_identities": (_schema(
        ["organization_id", "owner_user_id", "filename", "binary_sha256", "classification",
         "review_status", "revision", "schema_version", "created_at", "updated_at"], {
            "organization_id": _UUID, "owner_user_id": _STR, "filename": _STR,
            "binary_sha256": _STR, "classification": {"enum": [
                "MATCH", "MISMATCH", "UNCERTAIN", "DUPLICATE", "NEW_VERSION"]},
            "review_status": {"enum": [
                "NOT_REQUIRED", "PENDING_REVIEW", "APPROVED", "REJECTED"]},
            "revision": _INT, "schema_version": _INT, "created_at": _DATE, "updated_at": _DATE,
            "accepted_identifiers": {"bsonType": "array"},
        }), [
        # NOT unique: every upload keeps its own provenance and decision.
        ([("organization_id", ASCENDING), ("accepted_identifiers", ASCENDING)],
         "ix_identity_accepted", {}),
        ([("organization_id", ASCENDING), ("binary_sha256", ASCENDING)], "ix_identity_binary", {}),
        ([("organization_id", ASCENDING), ("text_fingerprint.sha256", ASCENDING)],
         "ix_identity_text", {}),
        ([("organization_id", ASCENDING), ("review_status", ASCENDING), ("created_at", ASCENDING)],
         "ix_identity_review_queue", {}),
        ([("organization_id", ASCENDING), ("trial_id", ASCENDING)], "ix_identity_trial", {}),
    ]),
    "identity_reviews": (_schema(
        ["organization_id", "identity_id", "actor_id", "action", "reason", "created_at"], {
            "organization_id": _UUID, "identity_id": _UUID, "actor_id": _STR,
            "action": _STR, "reason": _STR, "created_at": _DATE,
        }), [
        ([("organization_id", ASCENDING), ("identity_id", ASCENDING), ("created_at", ASCENDING)],
         "ix_review_identity", {}),
    ]),
    "identity_jobs": (_schema(
        ["organization_id", "kind", "claim_key", "identity_id", "state", "revision"], {
            "organization_id": _UUID, "kind": _STR, "claim_key": _STR, "identity_id": _UUID,
            "state": {"enum": ["PENDING", "CLAIMED", "COMPLETED", "FAILED"]}, "revision": _INT,
        }), [
        # the storage-enforced claim: one extraction / resume intent per key
        ([("organization_id", ASCENDING), ("claim_key", ASCENDING)], "uq_identity_job_claim",
         {"unique": True}),
    ]),
    "identity_extraction_results": (_schema(
        ["organization_id", "identity_id", "claim_key", "signature", "payload", "created_at"], {
            "organization_id": _UUID, "identity_id": _UUID, "claim_key": _STR,
            "signature": _STR, "payload": {"bsonType": "object"}, "created_at": _DATE,
        }), [
        ([("organization_id", ASCENDING), ("identity_id", ASCENDING)], "ix_result_identity", {}),
        ([("organization_id", ASCENDING), ("claim_key", ASCENDING)], "ix_result_claim", {}),
    ]),
}


def apply(database) -> None:
    for name, (validator, indexes) in COLLECTIONS.items():
        ensure_collection(database, name, validator)
        for keys, index_name, options in indexes:
            database[name].create_index(keys, name=index_name, **options)
