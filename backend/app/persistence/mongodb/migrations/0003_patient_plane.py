"""Patient plane: patients, append-only facts, evaluations, projections."""

from pymongo import ASCENDING

from ..migrate import ensure_collection

VERSION = 3
NAME = "patient_plane"

_UUID = {"bsonType": "binData"}
_OPT_UUID = {"bsonType": ["binData", "null"]}
_STR = {"bsonType": "string"}
_DATE = {"bsonType": "date"}
_INT = {"bsonType": ["int", "long"]}


def _schema(required, properties):
    return {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", *required],
        "properties": {"_id": _UUID, "doc_version": {"bsonType": "int"}, **properties},
    }}


def _scoped(extra_required, properties):
    return _schema(
        ["organization_id", "patient_id", *extra_required],
        {"organization_id": _UUID, "patient_id": _UUID, **properties})


COLLECTIONS = {
    "patients": (_schema(["organization_id", "trial_id", "patient_code", "status", "revision"], {
        "organization_id": _UUID, "trial_id": _UUID, "patient_code": _STR, "status": _STR,
        "current_schedule_version_id": _OPT_UUID, "revision": _INT,
    }), [
        ([("trial_id", ASCENDING), ("patient_code", ASCENDING)], "uq_patient_code", {"unique": True}),
        ([("organization_id", ASCENDING), ("trial_id", ASCENDING)], "ix_patient_trial", {}),
        ([("external_patient_id", ASCENDING)], "ix_patient_external", {}),
    ]),
    "patient_anchors": (_scoped(["anchor_definition_id", "anchor_code", "status", "recorded_at"], {
        "anchor_definition_id": _UUID, "anchor_code": _STR, "status": _STR, "recorded_at": _DATE,
    }), [
        ([("patient_id", ASCENDING), ("recorded_at", ASCENDING)], "ix_anchor_patient", {}),
    ]),
    "patient_states": (_scoped(["state_code", "effective_at"], {
        "state_code": _STR, "effective_at": _DATE,
    }), [
        ([("patient_id", ASCENDING), ("effective_at", ASCENDING)], "ix_state_patient_time", {}),
    ]),
    "patient_conditions": (_scoped(["condition_code", "state", "recorded_at"], {
        "condition_code": _STR, "state": _STR, "recorded_at": _DATE,
    }), [
        ([("patient_id", ASCENDING), ("superseded_by_id", ASCENDING), ("recorded_at", ASCENDING)],
         "ix_condition_current", {}),
    ]),
    "patient_activity_records": (_scoped(["event_code", "activity_code", "logical_key", "status"], {
        "event_code": _STR, "activity_code": _STR, "logical_key": _STR, "status": _STR,
    }), [
        ([("patient_id", ASCENDING), ("logical_key", ASCENDING), ("superseded_by_id", ASCENDING)],
         "ix_activity_entry_current", {}),
    ]),
    "patient_unscheduled_visits": (_scoped(["event_code", "occurred_on", "reason"], {
        "event_code": _STR, "occurred_on": _STR, "reason": _STR,
    }), [
        ([("patient_id", ASCENDING), ("occurred_on", ASCENDING)], "ix_unscheduled_patient", {}),
    ]),
    "patient_event_occurrences": (_scoped(
        ["schedule_version_id", "event_code", "occurrence_index", "logical_key", "status", "recorded_at"], {
            "schedule_version_id": _UUID, "event_code": _STR, "occurrence_index": _INT,
            "logical_key": _STR, "status": _STR, "recorded_at": _DATE,
        }), [
        ([("patient_id", ASCENDING), ("schedule_version_id", ASCENDING),
          ("event_code", ASCENDING), ("occurrence_index", ASCENDING), ("recorded_at", ASCENDING)],
         "ix_occurrence_logical", {}),
    ]),
    "schedule_evaluations": (_scoped(
        ["schedule_version_id", "evaluator_version", "status", "sequence", "evaluated_at"], {
            "schedule_version_id": _UUID, "evaluator_version": _STR, "status": _STR,
            "sequence": _INT, "evaluated_at": _DATE,
        }), [
        ([("patient_id", ASCENDING), ("idempotency_key", ASCENDING)], "uq_evaluation_idempotency",
         {"unique": True, "partialFilterExpression": {"idempotency_key": {"$type": "string"}}}),
        ([("patient_id", ASCENDING), ("schedule_version_id", ASCENDING), ("sequence", ASCENDING)],
         "uq_evaluation_sequence", {"unique": True}),
    ]),
    "patient_events": (_scoped(
        ["evaluation_id", "schedule_version_id", "event_code", "occurrence_index", "logical_key",
         "status"], {
            "evaluation_id": _UUID, "schedule_version_id": _UUID, "event_code": _STR,
            "occurrence_index": _INT, "logical_key": _STR, "status": _STR,
        }), [
        # one projection per logical visit per evaluation (P1-03)
        ([("evaluation_id", ASCENDING), ("event_code", ASCENDING), ("occurrence_index", ASCENDING)],
         "uq_projection_per_evaluation", {"unique": True}),
        ([("patient_id", ASCENDING), ("schedule_version_id", ASCENDING),
          ("event_code", ASCENDING), ("occurrence_index", ASCENDING)], "ix_event_logical", {}),
        ([("nominal_start_date", ASCENDING), ("latest_date", ASCENDING)], "ix_event_dates", {}),
        ([("logical_occurrence_id", ASCENDING)], "ix_event_logical_id", {}),
    ]),
    "patient_confinement_episodes": (_scoped(
        ["evaluation_id", "episode_code", "status"],
        {"evaluation_id": _UUID, "episode_code": _STR, "status": _STR}), [
        ([("evaluation_id", ASCENDING), ("episode_code", ASCENDING)], "uq_episode_per_evaluation",
         {"unique": True}),
    ]),
    "patient_schedules": (_scoped(["schedule_version_id", "status", "revision"], {
        "schedule_version_id": _UUID, "status": _STR, "revision": _INT,
        "current_evaluation_id": _OPT_UUID,
    }), [
        ([("patient_id", ASCENDING), ("schedule_version_id", ASCENDING)], "uq_patient_schedule",
         {"unique": True}),
    ]),
}


def apply(database) -> None:
    for name, (validator, indexes) in COLLECTIONS.items():
        ensure_collection(database, name, validator)
        for keys, index_name, options in indexes:
            database[name].create_index(keys, name=index_name, **options)
