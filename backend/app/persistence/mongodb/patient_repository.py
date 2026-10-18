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
from app.application.records import CommandReceiptRecord, OutboxRecord, Record

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
        self._insert(OUTBOX, record, "outbox record")

    def list_pending(self, *, limit: int = 100) -> list[OutboxRecord]:
        return self._list(OUTBOX, OutboxRecord, {"status": "PENDING"},
                          [("created_at", ASCENDING), ("_id", ASCENDING)])[:limit]


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
