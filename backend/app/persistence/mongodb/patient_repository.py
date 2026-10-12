"""Patient, append-only patient facts, evaluations and projections."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import BulkWriteError, DuplicateKeyError

from app.application.errors import (
    DuplicateRecordError,
    RecordNotFoundError,
    StaleRevisionError,
)
from app.application.patients import (
    ActivityEntryRecord,
    ConfinementEpisodeRecord,
    EvaluationRecord,
    OccurrenceRecord,
    PatientAnchorRecord,
    PatientConditionRecord,
    PatientEventRecord,
    PatientRecord,
    PatientScheduleRecord,
    PatientStateRecord,
    UnscheduledVisitRecord,
)
from app.application.records import Record

from .documents import from_document, to_document

R = TypeVar("R", bound=Record)

PATIENTS = "patients"
ANCHORS = "patient_anchors"
STATES = "patient_states"
CONDITIONS = "patient_conditions"
ACTIVITY_ENTRIES = "patient_activity_records"
UNSCHEDULED = "patient_unscheduled_visits"
OCCURRENCES = "patient_event_occurrences"
EVALUATIONS = "schedule_evaluations"
EVENTS = "patient_events"
CONFINEMENTS = "patient_confinement_episodes"
POINTERS = "patient_schedules"


def _duplicate(error: Exception) -> bool:
    if isinstance(error, DuplicateKeyError):
        return True
    if isinstance(error, BulkWriteError):
        return any(item.get("code") == 11000 for item in error.details.get("writeErrors", []))
    return False


class _Base:
    def __init__(self, database: Database, session: ClientSession | None):
        self._db = database
        self._session = session

    def _insert(self, collection: str, record: Record, what: str) -> None:
        try:
            self._db[collection].insert_one(to_document(record), session=self._session)
        except DuplicateKeyError as error:
            raise DuplicateRecordError(f"{what} already exists", key=str(record.id)) from error

    def _insert_many(self, collection: str, records: list[Record], what: str) -> None:
        if not records:
            return
        try:
            self._db[collection].insert_many(
                [to_document(item) for item in records], ordered=True, session=self._session)
        except (DuplicateKeyError, BulkWriteError) as error:
            if not _duplicate(error):
                raise
            raise DuplicateRecordError(f"duplicate {what}", key=what) from error

    def _list(
        self, collection: str, cls: type[R], query: dict[str, Any], sort: list[tuple[str, int]],
    ) -> list[R]:
        cursor = self._db[collection].find(query, session=self._session).sort(sort)
        return [from_document(cls, doc) for doc in cursor]


class MongoPatientRepository(_Base):
    def add(self, record: PatientRecord) -> None:
        self._insert(PATIENTS, record, "patient")

    def get(self, organization_id: UUID, patient_id: UUID) -> PatientRecord:
        doc = self._db[PATIENTS].find_one(
            {"_id": patient_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("patient not found")
        return from_document(PatientRecord, doc)

    def list_for_trial(self, organization_id: UUID, trial_id: UUID) -> list[PatientRecord]:
        return self._list(
            PATIENTS, PatientRecord, {"organization_id": organization_id, "trial_id": trial_id},
            [("patient_code", ASCENDING)])

    def pin_schedule_version(
        self, organization_id: UUID, patient_id: UUID, schedule_version_id: UUID,
    ) -> PatientRecord:
        doc = self._db[PATIENTS].find_one_and_update(
            {"_id": patient_id, "organization_id": organization_id},
            {"$set": {"current_schedule_version_id": schedule_version_id,
                      "updated_at": datetime.now(timezone.utc)}, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER, session=self._session)
        if doc is None:
            raise RecordNotFoundError("patient not found")
        return from_document(PatientRecord, doc)


class MongoPatientFactsRepository(_Base):
    # anchors
    def add_anchor(self, record: PatientAnchorRecord) -> None:
        self._insert(ANCHORS, record, "patient anchor")

    def list_anchors(
        self, patient_id: UUID, *, statuses: set[str] | None = None,
    ) -> list[PatientAnchorRecord]:
        query: dict[str, Any] = {"patient_id": patient_id}
        if statuses is not None:
            query["status"] = {"$in": sorted(statuses)}
        return self._list(ANCHORS, PatientAnchorRecord, query,
                          [("recorded_at", ASCENDING), ("_id", ASCENDING)])

    # states
    def append_state(self, record: PatientStateRecord) -> None:
        self._insert(STATES, record, "patient state")

    def list_states(self, patient_id: UUID) -> list[PatientStateRecord]:
        return self._list(STATES, PatientStateRecord, {"patient_id": patient_id},
                          [("effective_at", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)])

    # conditions
    def add_condition(self, record: PatientConditionRecord) -> None:
        self._insert(CONDITIONS, record, "patient condition")

    def list_current_conditions(self, patient_id: UUID) -> list[PatientConditionRecord]:
        return self._list(
            CONDITIONS, PatientConditionRecord,
            {"patient_id": patient_id, "superseded_by_id": None},
            [("recorded_at", ASCENDING), ("_id", ASCENDING)])

    def supersede_condition(self, condition_id: UUID, superseded_by_id: UUID) -> None:
        result = self._db[CONDITIONS].update_one(
            {"_id": condition_id, "superseded_by_id": None},
            {"$set": {"superseded_by_id": superseded_by_id}}, session=self._session)
        if result.matched_count == 0:
            raise RecordNotFoundError("current condition record not found")

    # intra-day activity entries
    def add_activity_entry(self, record: ActivityEntryRecord) -> None:
        self._insert(ACTIVITY_ENTRIES, record, "activity record")

    def list_current_activity_entries(self, patient_id: UUID) -> list[ActivityEntryRecord]:
        return self._list(
            ACTIVITY_ENTRIES, ActivityEntryRecord,
            {"patient_id": patient_id, "superseded_by_id": None},
            [("recorded_at", ASCENDING), ("_id", ASCENDING)])

    def supersede_activity_entries(
        self, patient_id: UUID, logical_key: str, superseded_by_id: UUID,
    ) -> list[ActivityEntryRecord]:
        query = {"patient_id": patient_id, "logical_key": logical_key,
                 "superseded_by_id": None, "_id": {"$ne": superseded_by_id}}
        previous = self._list(ACTIVITY_ENTRIES, ActivityEntryRecord, query,
                              [("recorded_at", ASCENDING), ("_id", ASCENDING)])
        if previous:
            self._db[ACTIVITY_ENTRIES].update_many(
                {"_id": {"$in": [item.id for item in previous]}},
                {"$set": {"superseded_by_id": superseded_by_id}}, session=self._session)
        return previous

    # unscheduled visits
    def add_unscheduled_visit(self, record: UnscheduledVisitRecord) -> None:
        self._insert(UNSCHEDULED, record, "unscheduled visit")

    def list_unscheduled_visits(self, patient_id: UUID) -> list[UnscheduledVisitRecord]:
        return self._list(UNSCHEDULED, UnscheduledVisitRecord, {"patient_id": patient_id},
                          [("occurred_on", ASCENDING), ("_id", ASCENDING)])

    # occurrence facts, keyed by the LOGICAL visit
    def add_occurrence(self, record: OccurrenceRecord) -> None:
        self._insert(OCCURRENCES, record, "occurrence fact")

    def list_occurrences(
        self, patient_id: UUID, *, schedule_version_id: UUID | None = None,
    ) -> list[OccurrenceRecord]:
        query: dict[str, Any] = {"patient_id": patient_id}
        if schedule_version_id is not None:
            query["schedule_version_id"] = schedule_version_id
        return self._list(OCCURRENCES, OccurrenceRecord, query,
                          [("recorded_at", ASCENDING), ("_id", ASCENDING)])


class MongoEvaluationRepository(_Base):
    def find_by_idempotency(self, patient_id: UUID, key: str) -> EvaluationRecord | None:
        doc = self._db[EVALUATIONS].find_one(
            {"patient_id": patient_id, "idempotency_key": key}, session=self._session)
        return from_document(EvaluationRecord, doc) if doc else None

    def get_evaluation(self, evaluation_id: UUID) -> EvaluationRecord:
        doc = self._db[EVALUATIONS].find_one({"_id": evaluation_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("evaluation not found")
        return from_document(EvaluationRecord, doc)

    def get_pointer(self, patient_id: UUID, schedule_version_id: UUID) -> PatientScheduleRecord | None:
        doc = self._db[POINTERS].find_one(
            {"patient_id": patient_id, "schedule_version_id": schedule_version_id},
            session=self._session)
        return from_document(PatientScheduleRecord, doc) if doc else None

    def count_evaluations(self, patient_id: UUID, schedule_version_id: UUID) -> int:
        return self._db[EVALUATIONS].count_documents(
            {"patient_id": patient_id, "schedule_version_id": schedule_version_id},
            session=self._session)

    def save_evaluation(
        self, evaluation: EvaluationRecord, events: list[PatientEventRecord],
        confinements: list[ConfinementEpisodeRecord], *, expected_pointer_revision: int | None,
    ) -> PatientScheduleRecord:
        self._insert(EVALUATIONS, evaluation, "evaluation")
        self._insert_many(EVENTS, list(events), "patient event")
        self._insert_many(CONFINEMENTS, list(confinements), "confinement episode")
        now = datetime.now(timezone.utc)
        if expected_pointer_revision is None:
            pointer = PatientScheduleRecord(
                organization_id=evaluation.organization_id, patient_id=evaluation.patient_id,
                schedule_version_id=evaluation.schedule_version_id,
                current_evaluation_id=evaluation.id, generation_run_id=evaluation.id,
                revision=1)
            try:
                self._db[POINTERS].insert_one(to_document(pointer), session=self._session)
            except DuplicateKeyError as error:
                raise StaleRevisionError(
                    "the patient schedule was created by a concurrent evaluation") from error
            return pointer
        doc = self._db[POINTERS].find_one_and_update(
            {"patient_id": evaluation.patient_id, "schedule_version_id": evaluation.schedule_version_id,
             "revision": expected_pointer_revision},
            {"$set": {"current_evaluation_id": evaluation.id, "generation_run_id": evaluation.id,
                      "status": "ACTIVE", "updated_at": now, "generated_at": now},
             "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER, session=self._session)
        if doc is None:
            raise StaleRevisionError("the patient schedule changed since it was read")
        return from_document(PatientScheduleRecord, doc)

    def list_events(self, evaluation_id: UUID) -> list[PatientEventRecord]:
        return self._list(
            EVENTS, PatientEventRecord, {"evaluation_id": evaluation_id},
            [("nominal_start_date", ASCENDING), ("occurrence_index", ASCENDING),
             ("event_code", ASCENDING), ("_id", ASCENDING)])

    def list_confinements(self, evaluation_id: UUID) -> list[ConfinementEpisodeRecord]:
        return self._list(
            CONFINEMENTS, ConfinementEpisodeRecord, {"evaluation_id": evaluation_id},
            [("episode_code", ASCENDING), ("_id", ASCENDING)])

    def list_evaluations(self, patient_id: UUID, schedule_version_id: UUID) -> list[EvaluationRecord]:
        return self._list(
            EVALUATIONS, EvaluationRecord,
            {"patient_id": patient_id, "schedule_version_id": schedule_version_id},
            [("sequence", ASCENDING), ("evaluated_at", ASCENDING), ("_id", DESCENDING)])
