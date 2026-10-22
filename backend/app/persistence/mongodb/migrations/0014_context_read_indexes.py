"""P9-03: indexes that cover both the filter and the sort of every patient-context read.

The 0003/0005 indexes serve the filter but leave a blocking in-memory sort (the `_id`
tie-breaker and secondary sort keys are not indexed). These named, additive indexes remove it
so context loading stays bounded as a patient's history grows.
"""
from ..migrate import ensure_collection  # noqa: F401  (collections already exist)

VERSION = 14
NAME = "context_read_indexes"

INDEXES = {
    "patient_anchors": [([("patient_id", 1), ("recorded_at", 1), ("_id", 1)], "ix_ctx_anchor")],
    "patient_states": [([("patient_id", 1), ("effective_at", 1), ("created_at", 1), ("_id", 1)], "ix_ctx_state")],
    "patient_conditions": [([("patient_id", 1), ("superseded_by_id", 1), ("recorded_at", 1), ("_id", 1)], "ix_ctx_condition")],
    "patient_activity_records": [([("patient_id", 1), ("superseded_by_id", 1), ("recorded_at", 1), ("_id", 1)], "ix_ctx_activity")],
    "patient_unscheduled_visits": [([("patient_id", 1), ("occurred_on", 1), ("_id", 1)], "ix_ctx_unscheduled")],
    "patient_event_occurrences": [
        ([("patient_id", 1), ("recorded_at", 1), ("_id", 1)], "ix_ctx_occurrence"),
        ([("patient_id", 1), ("schedule_version_id", 1), ("recorded_at", 1), ("_id", 1)], "ix_ctx_occurrence_version")],
    "patient_schedule_assignments": [([("patient_id", 1), ("assigned_at", 1), ("_id", 1)], "ix_ctx_assignment")],
    "patient_events": [([("evaluation_id", 1), ("nominal_start_date", 1), ("occurrence_index", 1),
                         ("event_code", 1), ("_id", 1)], "ix_ctx_projection")],
    "patient_confinement_episodes": [([("evaluation_id", 1), ("episode_code", 1), ("_id", 1)], "ix_ctx_confinement")],
    "schedule_evaluations": [([("patient_id", 1), ("schedule_version_id", 1), ("sequence", 1),
                               ("evaluated_at", 1), ("_id", -1)], "ix_ctx_evaluation_history")],
}


def apply(database):
    for collection, specs in INDEXES.items():
        for keys, name in specs:
            database[collection].create_index(keys, name=name)
