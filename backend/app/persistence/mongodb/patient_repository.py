"""Patient, append-only patient facts, evaluations and projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar
from uuid import UUID

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import BulkWriteError, DuplicateKeyError

from app.observability.correlation import stamp
from app.application.archive_records import ArchiveRecord, ArchiveRefusedError, EvaluationArchivedError
from app.application.errors import (
    DuplicateRecordError,
    RecordNotFoundError,
    StaleRevisionError,
)
from app.application.patients import (
    ActivityEntryRecord,
    AssignmentRecord,
    ConfinementEpisodeRecord,
    EvaluationRecord,
    ImpactProposalRecord,
    OccurrenceRecord,
    ParityRunRecord,
    PatientAnchorRecord,
    PatientConditionRecord,
    PatientEventRecord,
    PatientRecord,
    PatientScheduleRecord,
    PatientStateRecord,
    UnscheduledVisitRecord,
)
from app.application.cohort_records import CohortRecord
from app.application.reminder_records import NotificationPreferenceRecord, ReminderNoticeRecord
from app.application.migration_records import MigrationRunRecord, SourceMappingRecord
from app.application.records import CommandReceiptRecord, InboxRecord, OutboxRecord, Record

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
PROPOSALS = "schedule_impact_proposals"
ASSIGNMENTS = "patient_schedule_assignments"
ARCHIVES = "evaluation_archives"
INBOX = "outbox_inbox"
COHORTS = "cutover_cohorts"
REMINDER_PREFS = "notification_preferences"
REMINDER_NOTICES = "reminder_notices"
MIGRATION_RUNS = "migration_runs"
MIGRATION_MAPPINGS = "migration_source_mappings"
PARITY_RUNS = "parity_runs"
RECEIPTS = "command_receipts"
OUTBOX = "outbox"


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

    def find_by_external_id(
        self, organization_id: UUID, trial_id: UUID, external_patient_id: str,
    ) -> PatientRecord | None:
        doc = self._db[PATIENTS].find_one(
            {"organization_id": organization_id, "trial_id": trial_id,
             "external_patient_id": external_patient_id}, session=self._session)
        return from_document(PatientRecord, doc) if doc else None

    def list_page_for_trial(
        self, organization_id: UUID, trial_id: UUID, *, after_code: str | None, limit: int,
    ) -> list[PatientRecord]:
        query: dict[str, Any] = {"organization_id": organization_id, "trial_id": trial_id}
        if after_code is not None:
            query["patient_code"] = {"$gt": after_code}
        cursor = self._db[PATIENTS].find(query, session=self._session).sort(
            [("patient_code", ASCENDING), ("_id", ASCENDING)]).limit(limit)
        return [from_document(PatientRecord, doc) for doc in cursor]

    def find_owner_by_external_id(
        self, trial_id: UUID, external_patient_id: str,
    ) -> PatientRecord | None:
        docs = list(self._db[PATIENTS].find(
            {"trial_id": trial_id, "external_patient_id": external_patient_id},
            session=self._session).limit(2))
        # ambiguity (two owners for one external id) is refused, never first-match
        return from_document(PatientRecord, docs[0]) if len(docs) == 1 else None

    def list_for_organization(
        self, organization_id: UUID, *, trial_id: UUID | None = None,
    ) -> list[PatientRecord]:
        query: dict[str, Any] = {"organization_id": organization_id}
        if trial_id is not None:
            query["trial_id"] = trial_id
        return self._list(PATIENTS, PatientRecord, query,
                          [("patient_code", ASCENDING), ("_id", ASCENDING)])

    def count_by_schedule_version(
        self, organization_id: UUID, trial_id: UUID,
    ) -> dict[UUID, int]:
        pipeline = [
            {"$match": {"organization_id": organization_id, "trial_id": trial_id,
                       "current_schedule_version_id": {"$ne": None}}},
            {"$group": {"_id": "$current_schedule_version_id", "count": {"$sum": 1}}},
        ]
        return {row["_id"]: int(row["count"])
                for row in self._db[PATIENTS].aggregate(pipeline, session=self._session)}

    def save(self, record: PatientRecord) -> PatientRecord:
        document = to_document(record.model_copy(update={
            "revision": record.revision + 1, "updated_at": datetime.now(timezone.utc)}))
        # ``input_revision`` belongs to the clinical-input writers: a stale copy never rewrites it.
        fields = {key: value for key, value in document.items()
                  if key not in {"_id", "input_revision"}}
        stored = self._db[PATIENTS].find_one_and_update(
            {"_id": record.id, "organization_id": record.organization_id,
             "revision": record.revision}, {"$set": fields},
            return_document=ReturnDocument.AFTER, session=self._session)
        if stored is None:
            raise StaleRevisionError("the patient changed since it was read")
        return from_document(PatientRecord, stored)

    def guard_inputs(self, organization_id: UUID, patient_id: UUID, expected_input_revision: int) -> None:
        match: dict[str, Any] = {"_id": patient_id, "organization_id": organization_id}
        if expected_input_revision == 0:
            match["$or"] = [{"input_revision": 0}, {"input_revision": {"$exists": False}}]
        else:
            match["input_revision"] = expected_input_revision
        # A real write (not a read): it conflicts with any concurrent input writer.
        result = self._db[PATIENTS].update_one(
            match, {"$inc": {"input_revision": 0}}, session=self._session)
        if result.matched_count == 0:
            raise StaleRevisionError("the patient's clinical inputs changed since they were read")

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
    def _bump(self, patient_id: UUID) -> None:
        """Every clinical-input write advances the patient's input sequence in the same
        transaction (see ``PatientRecord.input_revision``)."""
        self._db[PATIENTS].update_one(
            {"_id": patient_id}, {"$inc": {"input_revision": 1}}, session=self._session)

    # anchors
    def add_anchor(self, record: PatientAnchorRecord) -> None:
        self._insert(ANCHORS, record, "patient anchor")
        self._bump(record.patient_id)

    def get_anchor(self, anchor_id: UUID) -> PatientAnchorRecord:
        doc = self._db[ANCHORS].find_one({"_id": anchor_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("patient anchor not found")
        return from_document(PatientAnchorRecord, doc)

    def confirm_anchor(self, anchor_id: UUID, metadata: dict) -> PatientAnchorRecord:
        from .codec import encode

        doc = self._db[ANCHORS].find_one_and_update(
            {"_id": anchor_id, "status": "PENDING_CONFIRMATION"},
            {"$set": {"status": "CONFIRMED", "metadata": encode(metadata)}},
            return_document=ReturnDocument.AFTER, session=self._session)
        if doc is None:
            raise StaleRevisionError("anchor candidate is no longer pending confirmation")
        self._bump(doc["patient_id"])
        return from_document(PatientAnchorRecord, doc)

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
        self._bump(record.patient_id)

    def list_states(self, patient_id: UUID) -> list[PatientStateRecord]:
        return self._list(STATES, PatientStateRecord, {"patient_id": patient_id},
                          [("effective_at", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)])

    # conditions
    def add_condition(self, record: PatientConditionRecord) -> None:
        self._insert(CONDITIONS, record, "patient condition")
        self._bump(record.patient_id)

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
        self._bump(record.patient_id)

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
        self._bump(record.patient_id)

    def list_unscheduled_visits(self, patient_id: UUID) -> list[UnscheduledVisitRecord]:
        return self._list(UNSCHEDULED, UnscheduledVisitRecord, {"patient_id": patient_id},
                          [("occurred_on", ASCENDING), ("_id", ASCENDING)])

    # occurrence facts, keyed by the LOGICAL visit
    def add_occurrence(self, record: OccurrenceRecord) -> None:
        self._insert(OCCURRENCES, record, "occurrence fact")
        self._bump(record.patient_id)

    def list_occurrences(
        self, patient_id: UUID, *, schedule_version_id: UUID | None = None,
    ) -> list[OccurrenceRecord]:
        query: dict[str, Any] = {"patient_id": patient_id}
        if schedule_version_id is not None:
            query["schedule_version_id"] = schedule_version_id
        return self._list(OCCURRENCES, OccurrenceRecord, query,
                          [("recorded_at", ASCENDING), ("_id", ASCENDING)])


class MongoImpactRepository(_Base):
    def add(self, record: ImpactProposalRecord) -> None:
        self._insert(PROPOSALS, record, "impact proposal")

    def get(self, proposal_id: UUID) -> ImpactProposalRecord:
        doc = self._db[PROPOSALS].find_one({"_id": proposal_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("impact proposal not found")
        return from_document(ImpactProposalRecord, doc)

    def save(self, record: ImpactProposalRecord) -> None:
        result = self._db[PROPOSALS].replace_one(
            {"_id": record.id}, to_document(record), session=self._session)
        if result.matched_count == 0:
            raise RecordNotFoundError("impact proposal not found")

    def add_assignment(self, record: AssignmentRecord) -> None:
        self._insert(ASSIGNMENTS, record, "schedule assignment")

    def list_for_patient(
        self, patient_id: UUID, *, status: str | None = None,
    ) -> list[ImpactProposalRecord]:
        query: dict[str, Any] = {"patient_id": patient_id}
        if status is not None:
            query["status"] = status
        return self._list(PROPOSALS, ImpactProposalRecord, query,
                          [("created_at", ASCENDING), ("_id", ASCENDING)])

    def list_assignments(self, patient_id: UUID) -> list[AssignmentRecord]:
        return self._list(ASSIGNMENTS, AssignmentRecord, {"patient_id": patient_id},
                          [("assigned_at", ASCENDING), ("_id", ASCENDING)])


class MongoArchiveRepository(_Base):
    def get(self, evaluation_id: UUID) -> ArchiveRecord | None:
        doc = self._db[ARCHIVES].find_one({"evaluation_id": evaluation_id}, session=self._session)
        return from_document(ArchiveRecord, doc) if doc else None

    def save(self, record: ArchiveRecord) -> None:
        document = to_document(record)
        self._db[ARCHIVES].replace_one(
            {"evaluation_id": record.evaluation_id}, document, upsert=True, session=self._session)


class MongoParityRepository(_Base):
    def add(self, record: ParityRunRecord) -> None:
        self._insert(PARITY_RUNS, record, "parity run")

    def get(self, organization_id: UUID, run_id: UUID) -> ParityRunRecord:
        doc = self._db[PARITY_RUNS].find_one(
            {"_id": run_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("parity run not found")
        return from_document(ParityRunRecord, doc)

    def save(self, record: ParityRunRecord) -> None:
        result = self._db[PARITY_RUNS].replace_one(
            {"_id": record.id}, to_document(record), session=self._session)
        if result.matched_count == 0:
            raise RecordNotFoundError("parity run not found")

    def list_for_trial(self, organization_id: UUID, trial_id: UUID) -> list[ParityRunRecord]:
        return self._list(PARITY_RUNS, ParityRunRecord,
                          {"organization_id": organization_id, "trial_id": trial_id},
                          [("ran_at", ASCENDING), ("_id", ASCENDING)])


class MongoCommandRepository(_Base):
    def find_receipt(
        self, organization_id: UUID, patient_id: UUID, idempotency_key: str,
    ) -> CommandReceiptRecord | None:
        doc = self._db[RECEIPTS].find_one(
            {"organization_id": organization_id, "patient_id": patient_id,
             "idempotency_key": idempotency_key}, session=self._session)
        return from_document(CommandReceiptRecord, doc) if doc else None

    def add_receipt(self, record: CommandReceiptRecord) -> None:
        self._insert(RECEIPTS, record, "command receipt")


class MongoOutboxRepository(_Base):
    def add(self, record: OutboxRecord) -> None:
        record = record.model_copy(update={"payload": stamp(record.payload)})
        self._insert(OUTBOX, record, "outbox record")

    def list_pending(self, *, limit: int = 100) -> list[OutboxRecord]:
        if limit <= 0:
            return []
        cursor = self._db[OUTBOX].find({"status": "PENDING"}, session=self._session).sort(
            [("created_at", ASCENDING), ("_id", ASCENDING)]).limit(max(1, min(limit, 1000)))
        return [from_document(OutboxRecord, row) for row in cursor]

    def claim(self, *, now: datetime, lease_seconds: int, token: str):
        exhausted = {"$expr": {"$gte": [{"$ifNull": ["$attempts", 0]},
                                        {"$ifNull": ["$max_attempts", 5]}]}}
        self._db[OUTBOX].update_many(
            {"status": "PROCESSING", "lease_expires_at": {"$lte": now}, **exhausted},
            {"$set": {"status": "DEAD", "lease_token": None, "lease_expires_at": None,
                      "last_error_code": "LEASE_RETRY_LIMIT"}}, session=self._session)
        query = {"$or": [
            {"status": "PENDING", "$or": [{"available_at": None}, {"available_at": {"$lte": now}}]},
            {"status": "PROCESSING", "lease_expires_at": {"$lte": now}}],
            "$expr": {"$lt": [{"$ifNull": ["$attempts", 0]}, {"$ifNull": ["$max_attempts", 5]}]}}
        row = self._db[OUTBOX].find_one_and_update(
            query, {"$set": {"status": "PROCESSING", "lease_token": token,
                             "lease_expires_at": now + timedelta(seconds=lease_seconds)}, "$inc": {"attempts": 1}},
            sort=[("created_at", ASCENDING), ("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER, session=self._session)
        return from_document(OutboxRecord, row) if row else None

    def settle(self, organization_id, record_id, token, *, now, status,
               available_at=None, error_code=None):
        if status not in {"SENT", "PENDING", "DEAD"}:
            raise ValueError("invalid outbox settlement")
        result = self._db[OUTBOX].update_one(
            {"_id": record_id, "organization_id": organization_id, "status": "PROCESSING",
             "lease_token": token, "lease_expires_at": {"$gt": now}},
            {"$set": {"status": status, "available_at": available_at,
                      "last_error_code": error_code, "lease_token": None, "lease_expires_at": None}},
            session=self._session)
        if result.matched_count != 1:
            raise StaleRevisionError("the outbox delivery lease was lost")

    def get(self, organization_id, record_id):
        doc = self._db[OUTBOX].find_one(
            {"_id": record_id, "organization_id": organization_id}, session=self._session)
        return from_document(OutboxRecord, doc) if doc else None

    def count_by_status(self):
        rows = self._db[OUTBOX].aggregate(
            [{"$group": {"_id": "$status", "n": {"$sum": 1}}}], session=self._session)
        return {row["_id"]: int(row["n"]) for row in rows}

    def list_dead(self, organization_id, *, limit=100):
        if limit <= 0:
            return []
        cursor = self._db[OUTBOX].find(
            {"organization_id": organization_id, "status": "DEAD"}, session=self._session).sort(
            [("created_at", ASCENDING), ("_id", ASCENDING)]).limit(min(limit, 1000))
        return [from_document(OutboxRecord, row) for row in cursor]

    def requeue_dead(self, organization_id, record_id, *, now):
        doc = self._db[OUTBOX].find_one_and_update(
            {"_id": record_id, "organization_id": organization_id, "status": "DEAD"},
            {"$set": {"status": "PENDING", "attempts": 0, "available_at": now, "last_error_code": None,
                      "lease_token": None, "lease_expires_at": None}},
            return_document=ReturnDocument.AFTER, session=self._session)
        if doc is None:
            raise RecordNotFoundError("no dead-lettered message")
        return from_document(OutboxRecord, doc)


class MongoInboxRepository(_Base):
    def find(self, consumer, dedupe_key):
        doc = self._db[INBOX].find_one(
            {"consumer": consumer, "dedupe_key": dedupe_key}, session=self._session)
        return from_document(InboxRecord, doc) if doc else None

    def add(self, record) -> None:
        self._insert(INBOX, record, "inbox record")


class MongoCohortRepository(_Base):
    def add(self, record) -> None:
        self._insert(COHORTS, record, "cohort")

    def get(self, organization_id, cohort_id):
        doc = self._db[COHORTS].find_one(
            {"_id": cohort_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("cohort not found")
        return from_document(CohortRecord, doc)

    def save(self, record, *, expected_revision):
        document = to_document(record.model_copy(update={"revision": expected_revision + 1}))
        fields = {key: value for key, value in document.items() if key != "_id"}
        stored = self._db[COHORTS].find_one_and_update(
            {"_id": record.id, "organization_id": record.organization_id, "revision": expected_revision},
            {"$set": fields}, return_document=ReturnDocument.AFTER, session=self._session)
        if stored is None:
            raise StaleRevisionError("the cohort changed since it was read")
        return from_document(CohortRecord, stored)

    def list_for_trial(self, organization_id, trial_id):
        return self._list(COHORTS, CohortRecord, {"organization_id": organization_id, "trial_id": trial_id},
                          [("name", ASCENDING), ("_id", ASCENDING)])

    def find_blocking_membership(self, external_patient_id):
        doc = self._db[COHORTS].find_one(
            {"state": "PILOT", "members.external_patient_id": external_patient_id}, session=self._session)
        return from_document(CohortRecord, doc) if doc else None


class MongoReminderRepository(_Base):
    def get_preferences(self, organization_id, patient_id):
        doc = self._db[REMINDER_PREFS].find_one(
            {"organization_id": organization_id, "patient_id": patient_id}, session=self._session)
        return from_document(NotificationPreferenceRecord, doc) if doc else None

    def save_preferences(self, record, *, expected_revision):
        if expected_revision is None:
            stored = record.model_copy(update={"revision": 1})
            self._insert(REMINDER_PREFS, stored, "notification preferences")
            return stored
        document = to_document(record.model_copy(update={"revision": expected_revision + 1}))
        fields = {key: value for key, value in document.items() if key != "_id"}
        saved = self._db[REMINDER_PREFS].find_one_and_update(
            {"organization_id": record.organization_id, "patient_id": record.patient_id,
             "revision": expected_revision}, {"$set": fields},
            return_document=ReturnDocument.AFTER, session=self._session)
        if saved is None:
            raise StaleRevisionError("the preferences changed since they were read")
        return from_document(NotificationPreferenceRecord, saved)

    def list_granted(self, *, after, limit):
        query = {"consent": "GRANTED"}
        if after is not None:
            query["patient_id"] = {"$gt": after}
        return self._list(REMINDER_PREFS, NotificationPreferenceRecord, query,
                          [("patient_id", ASCENDING)])[:max(0, min(limit, 1000))]

    def list_active(self, organization_id, patient_id):
        return self._list(REMINDER_NOTICES, ReminderNoticeRecord,
                          {"organization_id": organization_id, "patient_id": patient_id, "state": "ACTIVE"},
                          [("reminder_key", ASCENDING), ("_id", ASCENDING)])

    def add_notice(self, record) -> None:
        self._insert(REMINDER_NOTICES, record, "reminder")

    def withdraw(self, organization_id, patient_id, reminder_key, *, reason, at):
        result = self._db[REMINDER_NOTICES].update_one(
            {"organization_id": organization_id, "patient_id": patient_id, "reminder_key": reminder_key,
             "state": "ACTIVE"},
            {"$set": {"state": "WITHDRAWN", "withdrawn_at": at, "withdrawn_reason": reason}},
            session=self._session)
        if result.matched_count != 1:
            raise RecordNotFoundError("no active reminder")


class MongoMigrationRepository(_Base):
    def get_run(self, organization_id, run_key):
        doc = self._db[MIGRATION_RUNS].find_one(
            {"organization_id": organization_id, "run_key": run_key}, session=self._session)
        return from_document(MigrationRunRecord, doc) if doc else None

    def _upsert(self, collection: str, query: dict, record) -> None:
        """Replace the logical row; its _id (the first id it was stored under) never changes."""
        document = to_document(record)
        identity = document.pop("_id")
        self._db[collection].update_one(
            query, {"$set": document, "$setOnInsert": {"_id": identity}}, upsert=True, session=self._session)

    def save_run(self, record) -> None:
        self._upsert(MIGRATION_RUNS, {"organization_id": record.organization_id, "run_key": record.run_key}, record)

    def get_mapping(self, organization_id, source_system, source_id):
        doc = self._db[MIGRATION_MAPPINGS].find_one(
            {"organization_id": organization_id, "source_system": source_system, "source_id": source_id},
            session=self._session)
        return from_document(SourceMappingRecord, doc) if doc else None

    def save_mapping(self, record) -> None:
        self._upsert(MIGRATION_MAPPINGS, {"organization_id": record.organization_id,
                     "source_system": record.source_system, "source_id": record.source_id}, record)

    def list_mappings(self, organization_id, source_system):
        return self._list(
            MIGRATION_MAPPINGS, SourceMappingRecord,
            {"organization_id": organization_id, "source_system": source_system},
            [("source_id", ASCENDING), ("_id", ASCENDING)])


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

    def _guard_archived(self, evaluation_id: UUID) -> None:
        if self._db[ARCHIVES].find_one(
                {"evaluation_id": evaluation_id, "state": "ARCHIVED"},
                session=self._session) is not None:
            raise EvaluationArchivedError("the evaluation's projections are archived; restore them first")

    def list_events(self, evaluation_id: UUID) -> list[PatientEventRecord]:
        rows = self._list(
            EVENTS, PatientEventRecord, {"evaluation_id": evaluation_id},
            [("nominal_start_date", ASCENDING), ("occurrence_index", ASCENDING),
             ("event_code", ASCENDING), ("_id", ASCENDING)])
        if not rows:
            self._guard_archived(evaluation_id)
        return rows

    def list_confinements(self, evaluation_id: UUID) -> list[ConfinementEpisodeRecord]:
        rows = self._list(
            CONFINEMENTS, ConfinementEpisodeRecord, {"evaluation_id": evaluation_id},
            [("episode_code", ASCENDING), ("_id", ASCENDING)])
        if not rows:
            self._guard_archived(evaluation_id)
        return rows

    def purge_projections(self, evaluation_id: UUID) -> None:
        evaluation = self.get_evaluation(evaluation_id)
        pointer = self.get_pointer(evaluation.patient_id, evaluation.schedule_version_id)
        if pointer is not None and pointer.current_evaluation_id == evaluation_id:
            raise ArchiveRefusedError("the current evaluation's projections are never purged")
        self._db[EVENTS].delete_many({"evaluation_id": evaluation_id}, session=self._session)
        self._db[CONFINEMENTS].delete_many({"evaluation_id": evaluation_id}, session=self._session)

    def restore_projections(self, evaluation_id, events, confinements) -> None:
        if self._db[EVENTS].find_one({"evaluation_id": evaluation_id}, session=self._session)                 or self._db[CONFINEMENTS].find_one({"evaluation_id": evaluation_id}, session=self._session):
            raise DuplicateRecordError("projections already exist", key=str(evaluation_id))
        self._insert_many(EVENTS, events, "projection")
        self._insert_many(CONFINEMENTS, confinements, "confinement episode")

    def supersede_active_pointers(self, patient_id: UUID) -> int:
        result = self._db[POINTERS].update_many(
            {"patient_id": patient_id, "status": "ACTIVE"},
            {"$set": {"status": "SUPERSEDED", "updated_at": datetime.now(timezone.utc)}},
            session=self._session)
        return int(result.modified_count)

    def list_evaluations(self, patient_id: UUID, schedule_version_id: UUID) -> list[EvaluationRecord]:
        return self._list(
            EVALUATIONS, EvaluationRecord,
            {"patient_id": patient_id, "schedule_version_id": schedule_version_id},
            [("sequence", ASCENDING), ("evaluated_at", ASCENDING), ("_id", DESCENDING)])
