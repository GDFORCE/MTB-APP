"""P11-03: pilot cohorts (shadow -> pilot -> rolled back). A cohort document is bounded to 200 members."""
from ..migrate import ensure_collection

VERSION = 18
NAME = "cutover_cohorts"


def apply(database):
    uuid, text, date = {"bsonType": "binData"}, {"bsonType": "string"}, {"bsonType": "date"}
    nullable = lambda spec: {"bsonType": [spec["bsonType"], "null"]}
    ensure_collection(database, "cutover_cohorts", {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "doc_version", "organization_id", "trial_id", "name", "state", "members",
                     "created_by", "created_at", "revision"],
        "properties": {
            "_id": uuid, "doc_version": {"enum": [1]}, "organization_id": uuid, "trial_id": uuid, "name": text,
            "state": {"enum": ["SHADOW", "PILOT", "ROLLED_BACK"]},
            "members": {"bsonType": "array", "maxItems": 200, "items": {
                "bsonType": "object", "required": ["external_patient_id", "patient_id"],
                "properties": {"external_patient_id": text, "patient_id": uuid,
                               "previous_authority": nullable(text)}}},
            "created_by": uuid, "promoted_by": nullable(uuid), "promoted_at": nullable(date),
            "rolled_back_by": nullable(uuid), "rolled_back_at": nullable(date),
            "created_at": date, "revision": {"bsonType": ["int", "long"]}}}})
    database.cutover_cohorts.create_index(
        [("organization_id", 1), ("trial_id", 1), ("name", 1)], name="uq_cohort_name", unique=True)
    database.cutover_cohorts.create_index(
        [("state", 1), ("members.external_patient_id", 1)], name="ix_cohort_member_state")
