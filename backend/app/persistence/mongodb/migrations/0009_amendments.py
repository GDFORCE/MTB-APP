"""Protocol amendments (P6): lineage, content hashes, pairs, mappings, simulations, decisions.

Adds the amendment collections and tightens ``schedule_versions``:

* new optional header fields (``based_on_schedule_version_id``, ``content_hash``,
  ``content_hash_version``, ``header_integrity_hash``) are typed, and an APPROVED version
  must carry a content hash. The collection moves to ``validationLevel: moderate`` so a
  historical APPROVED version stored BEFORE hashes existed (and so without one) is never
  rewritten or rejected by this migration - it stays readable and is reported by the
  service as ``UNVERIFIED_LEGACY`` rather than declared verified;
* uniqueness: one pair per ordered (organization, from, to); one mapping set per pair;
  one idempotency key per (organization, patient); and ONE OPEN decision per patient
  (a partial unique index on ``is_open``), which is what stops two concurrent
  adoptions / a deferred decision and a return from double-assigning.

Apply is idempotent (create-if-missing collections, collMod, named indexes).
"""

import importlib

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 9
NAME = "amendments"

_UUID = {"bsonType": "binData"}
_OPT_UUID = {"bsonType": ["binData", "null"]}
_STR = {"bsonType": "string"}
_OPT_STR = {"bsonType": ["string", "null"]}
_DATE = {"bsonType": "date"}
_OPT_DATE = {"bsonType": ["date", "null"]}
_INT = {"bsonType": ["int", "long"]}
_OBJ = {"bsonType": "object"}
_ARR = {"bsonType": "array"}

DECISION_STATUSES = [
    "WAITING", "EXECUTING", "APPLIED", "REMAINING", "NEEDS_RECONFIRMATION", "BLOCKED", "CANCELLED"]
SIMULATION_STATUSES = ["ACTIVE", "CONSUMED", "EXPIRED", "SUPERSEDED"]


def _schema(required, properties):
    return {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", *required],
        "properties": {"_id": _UUID, "doc_version": {"bsonType": "int"}, **properties},
    }}


COLLECTIONS = {
    "amendment_pairs": (_schema(
        ["organization_id", "trial_id", "protocol_id", "from_schedule_version_id",
         "to_schedule_version_id", "from_content_hash", "to_content_hash",
         "content_hash_version", "change_set", "created_by"], {
            "organization_id": _UUID, "trial_id": _UUID, "protocol_id": _UUID,
            "from_schedule_version_id": _UUID, "to_schedule_version_id": _UUID,
            "from_content_hash": _STR, "to_content_hash": _STR, "content_hash_version": _STR,
            "change_set": _ARR, "change_summary": _OBJ, "created_by": _UUID,
        }), [
        ([("organization_id", ASCENDING), ("from_schedule_version_id", ASCENDING),
          ("to_schedule_version_id", ASCENDING)], "uq_pair_versions", {"unique": True}),
        ([("organization_id", ASCENDING), ("trial_id", ASCENDING)], "ix_pair_trial", {}),
    ]),
    "amendment_mapping_sets": (_schema(
        ["organization_id", "trial_id", "pair_id", "revision", "mappings", "mapping_revision",
         "engine_version"], {
            "organization_id": _UUID, "trial_id": _UUID, "pair_id": _UUID, "revision": _INT,
            "mappings": _ARR, "mapping_revision": _STR, "engine_version": _STR,
        }), [
        ([("pair_id", ASCENDING)], "uq_mapping_set_pair", {"unique": True}),
    ]),
    "amendment_mapping_decisions": (_schema(
        ["organization_id", "trial_id", "pair_id", "kind", "base_revision", "resulting_revision",
         "resulting_mapping_revision", "created_at"], {
            "organization_id": _UUID, "trial_id": _UUID, "pair_id": _UUID,
            "mapping_id": _OPT_UUID, "reviewer_id": _OPT_UUID,
            "kind": {"enum": ["PROPOSED", "CONFIRM", "REJECT", "REPLACE", "SET_RULES"]},
            "base_revision": _INT, "resulting_revision": _INT,
            "resulting_mapping_revision": _STR, "created_at": _DATE,
        }), [
        ([("organization_id", ASCENDING), ("pair_id", ASCENDING), ("created_at", ASCENDING)],
         "ix_mapping_decision_pair", {}),
    ]),
    "amendment_simulations": (_schema(
        ["organization_id", "trial_id", "patient_id", "pair_id", "from_schedule_version_id",
         "to_schedule_version_id", "binding_hash", "status", "created_by", "expires_at"], {
            "organization_id": _UUID, "trial_id": _UUID, "patient_id": _UUID, "pair_id": _UUID,
            "from_schedule_version_id": _UUID, "to_schedule_version_id": _UUID,
            "binding_hash": _STR, "status": {"enum": SIMULATION_STATUSES},
            "created_by": _UUID, "expires_at": _DATE,
        }), [
        ([("organization_id", ASCENDING), ("patient_id", ASCENDING), ("created_at", ASCENDING)],
         "ix_simulation_patient", {}),
    ]),
    "amendment_decisions": (_schema(
        ["organization_id", "trial_id", "patient_id", "pair_id", "from_schedule_version_id",
         "to_schedule_version_id", "decision_type", "mode", "status", "is_open", "material_hash",
         "reason", "actor_id", "revision"], {
            "organization_id": _UUID, "trial_id": _UUID, "patient_id": _UUID, "pair_id": _UUID,
            "from_schedule_version_id": _UUID, "to_schedule_version_id": _UUID,
            "decision_type": {"enum": ["ADOPT", "REMAIN", "RETURN"]}, "mode": _STR,
            "status": {"enum": DECISION_STATUSES}, "is_open": {"bsonType": "bool"},
            "material_hash": _STR, "reason": _STR, "actor_id": _UUID, "revision": _INT,
            "idempotency_key": _OPT_STR, "linked_decision_id": _OPT_UUID,
            "simulation_id": _OPT_UUID, "lease_token": _OPT_STR, "lease_expires_at": _OPT_DATE,
        }), [
        ([("organization_id", ASCENDING), ("patient_id", ASCENDING), ("idempotency_key", ASCENDING)],
         "uq_decision_idempotency", {"unique": True, "partialFilterExpression": {
             "idempotency_key": {"$type": "string"}}}),
        # the single open slot per patient: store-enforced, not a check-then-write
        ([("organization_id", ASCENDING), ("patient_id", ASCENDING)], "uq_decision_open_patient", {
            "unique": True, "partialFilterExpression": {"is_open": True}}),
        ([("organization_id", ASCENDING), ("trial_id", ASCENDING), ("is_open", ASCENDING)],
         "ix_decision_open", {}),
        ([("organization_id", ASCENDING), ("patient_id", ASCENDING), ("created_at", ASCENDING)],
         "ix_decision_patient", {}),
    ]),
}


def _schedule_versions_validator(database):
    previous = importlib.import_module(
        "app.persistence.mongodb.migrations.0002_authoring_and_schedules")
    schema = dict(previous.COLLECTIONS["schedule_versions"][0]["$jsonSchema"])
    properties = dict(schema["properties"])
    properties.update({
        "based_on_schedule_version_id": _OPT_UUID,
        "content_hash": _OPT_STR, "content_hash_version": _OPT_STR,
        "header_integrity_hash": _OPT_STR,
    })
    schema["properties"] = properties
    return {"$and": [
        {"$jsonSchema": schema},
        # an approved version carries the hash it was approved under
        {"$or": [{"status": {"$ne": "APPROVED"}}, {"content_hash": {"$type": "string"}}]},
    ]}


def apply(database) -> None:
    for name, (validator, indexes) in COLLECTIONS.items():
        ensure_collection(database, name, validator)
        for keys, index_name, options in indexes:
            database[name].create_index(keys, name=index_name, **options)
    # moderate: documents already stored without a hash are not rejected on later writes
    database.command(
        "collMod", "schedule_versions", validator=_schedule_versions_validator(database),
        validationLevel="moderate", validationAction="error")
    database["schedule_versions"].create_index(
        [("based_on_schedule_version_id", ASCENDING)], name="ix_version_based_on",
        partialFilterExpression={"based_on_schedule_version_id": {"$type": "binData"}})
