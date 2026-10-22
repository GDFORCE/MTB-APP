"""P10-04: explicit notification preferences and reminder notices (one document per reminder)."""
from ..migrate import ensure_collection

VERSION = 19
NAME = "reminders"


def apply(database):
    uuid, text, date = {"bsonType": "binData"}, {"bsonType": "string"}, {"bsonType": "date"}
    nullable = lambda spec: {"bsonType": [spec["bsonType"], "null"]}
    count = {"bsonType": ["int", "long"]}
    hhmm = {"bsonType": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$"}
    ensure_collection(database, "notification_preferences", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", "organization_id", "patient_id", "consent", "timezone",
                     "delivery_time", "lead_days", "recorded_by", "recorded_at", "revision"],
        "properties": {"_id": uuid, "doc_version": {"enum": [1]}, "organization_id": uuid, "patient_id": uuid,
                       "consent": {"enum": ["GRANTED", "DECLINED", "WITHDRAWN"]}, "timezone": text,
                       "delivery_time": hhmm, "lead_days": {"bsonType": ["int", "long"], "minimum": 0, "maximum": 30},
                       "quiet_start": {"bsonType": ["string", "null"]}, "quiet_end": {"bsonType": ["string", "null"]},
                       "recorded_by": uuid, "recorded_at": date, "revision": count}}})
    database.notification_preferences.create_index(
        [("organization_id", 1), ("patient_id", 1)], name="uq_preferences_patient", unique=True)
    ensure_collection(database, "reminder_notices", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", "organization_id", "patient_id", "reminder_key", "logical_key",
                     "event_code", "state", "scheduled_for", "nominal_date", "policy_state",
                     "requires_attendance", "message", "created_at"],
        "properties": {"_id": uuid, "doc_version": {"enum": [1]}, "organization_id": uuid, "patient_id": uuid,
                       "reminder_key": text, "logical_key": text, "event_code": text,
                       "state": {"enum": ["ACTIVE", "WITHDRAWN"]}, "scheduled_for": date, "nominal_date": text,
                       "policy_state": text, "requires_attendance": {"bsonType": "bool"}, "message": text,
                       "evaluation_id": nullable(uuid), "created_at": date, "withdrawn_at": nullable(date),
                       "withdrawn_reason": nullable(text)}}})
    # Unique only while ACTIVE: a reminder withdrawn for a moved date may legitimately come back.
    database.reminder_notices.create_index(
        [("organization_id", 1), ("patient_id", 1), ("reminder_key", 1)], name="uq_reminder_key_active",
        unique=True, partialFilterExpression={"state": "ACTIVE"})
    database.reminder_notices.create_index(
        [("organization_id", 1), ("patient_id", 1), ("state", 1), ("reminder_key", 1)], name="ix_reminder_active")
