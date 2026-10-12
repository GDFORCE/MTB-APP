"""Trial / protocol / protocol version / schedule definition / extraction run."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.application.authoring import (
    ExtractionRunRecord,
    ProtocolRecord,
    ProtocolVersionRecord,
    ScheduleDefinitionRecord,
    TrialRecord,
)
from app.application.errors import DuplicateRecordError, RecordNotFoundError
from app.application.records import Record

from .documents import from_document, to_document

R = TypeVar("R", bound=Record)

TRIALS = "trials"
PROTOCOLS = "protocols"
PROTOCOL_VERSIONS = "protocol_versions"
DEFINITIONS = "schedule_definitions"
RUNS = "extraction_runs"
RUN_TRACES = "extraction_run_traces"


class MongoAuthoringRepository:
    def __init__(self, database: Database, session: ClientSession | None = None):
        self._db = database
        self._session = session

    # -- helpers ----------------------------------------------------------
    def _insert(self, collection: str, record: Record, *, what: str) -> None:
        try:
            self._db[collection].insert_one(to_document(record), session=self._session)
        except DuplicateKeyError as error:
            raise DuplicateRecordError(f"{what} already exists", key=str(record.id)) from error

    def _one(self, collection: str, query: dict[str, Any], cls: type[R]) -> R | None:
        doc = self._db[collection].find_one(query, session=self._session)
        return from_document(cls, doc) if doc else None

    def _require(self, collection: str, query: dict[str, Any], cls: type[R], what: str) -> R:
        found = self._one(collection, query, cls)
        if found is None:
            raise RecordNotFoundError(f"{what} not found")
        return found

    # -- trials -----------------------------------------------------------
    def add_trial(self, record: TrialRecord) -> None:
        self._insert(TRIALS, record, what="trial")

    def get_trial(self, organization_id: UUID, trial_id: UUID) -> TrialRecord:
        return self._require(
            TRIALS, {"_id": trial_id, "organization_id": organization_id}, TrialRecord, "trial")

    def find_trial_by_external_id(self, organization_id: UUID, external_trial_id: str) -> TrialRecord | None:
        return self._one(
            TRIALS, {"organization_id": organization_id, "external_trial_id": external_trial_id},
            TrialRecord)

    def set_trial_read_mode(self, organization_id: UUID, trial_id: UUID, mode: str) -> None:
        result = self._db[TRIALS].update_one(
            {"_id": trial_id, "organization_id": organization_id},
            {"$set": {"schedule_read_mode": mode, "updated_at": datetime.now(timezone.utc)}},
            session=self._session)
        if result.matched_count == 0:
            raise RecordNotFoundError("trial not found")

    # -- protocols --------------------------------------------------------
    def add_protocol(self, record: ProtocolRecord) -> None:
        self._insert(PROTOCOLS, record, what="protocol")

    def get_protocol(self, protocol_id: UUID) -> ProtocolRecord:
        return self._require(PROTOCOLS, {"_id": protocol_id}, ProtocolRecord, "protocol")

    def find_protocol_for_trial(self, trial_id: UUID) -> ProtocolRecord | None:
        return self._one(PROTOCOLS, {"trial_id": trial_id}, ProtocolRecord)

    def set_current_protocol_version(self, protocol_id: UUID, protocol_version_id: UUID) -> None:
        result = self._db[PROTOCOLS].update_one(
            {"_id": protocol_id},
            {"$set": {"current_version_id": protocol_version_id,
                      "updated_at": datetime.now(timezone.utc)}},
            session=self._session)
        if result.matched_count == 0:
            raise RecordNotFoundError("protocol not found")

    # -- protocol versions ------------------------------------------------
    def add_protocol_version(self, record: ProtocolVersionRecord) -> None:
        self._insert(PROTOCOL_VERSIONS, record, what="protocol version")

    def get_protocol_version(self, protocol_version_id: UUID) -> ProtocolVersionRecord:
        return self._require(
            PROTOCOL_VERSIONS, {"_id": protocol_version_id}, ProtocolVersionRecord, "protocol version")

    def find_protocol_version_by_hash(
        self, protocol_id: UUID, document_hash: str,
    ) -> ProtocolVersionRecord | None:
        return self._one(
            PROTOCOL_VERSIONS, {"protocol_id": protocol_id, "document_hash": document_hash},
            ProtocolVersionRecord)

    def list_protocol_version_labels(self, protocol_id: UUID) -> list[str]:
        cursor = self._db[PROTOCOL_VERSIONS].find(
            {"protocol_id": protocol_id}, {"version_label": 1}, session=self._session)
        return [doc["version_label"] for doc in cursor]

    # -- schedule definitions ---------------------------------------------
    def add_definition(self, record: ScheduleDefinitionRecord) -> None:
        self._insert(DEFINITIONS, record, what="schedule definition")

    def get_definition(self, definition_id: UUID) -> ScheduleDefinitionRecord:
        return self._require(
            DEFINITIONS, {"_id": definition_id}, ScheduleDefinitionRecord, "schedule definition")

    def find_definition(
        self, protocol_version_id: UUID, name: str, schedule_type: str,
    ) -> ScheduleDefinitionRecord | None:
        return self._one(
            DEFINITIONS,
            {"protocol_version_id": protocol_version_id, "name": name, "schedule_type": schedule_type},
            ScheduleDefinitionRecord)

    # -- extraction runs ---------------------------------------------------
    def add_run(self, record: ExtractionRunRecord) -> None:
        self._insert(RUNS, self._without_trace(record), what="extraction run")
        if record.trace:
            self._db[RUN_TRACES].replace_one(
                {"_id": record.id}, {"_id": record.id, "doc_version": 1, "trace": record.trace},
                upsert=True, session=self._session)

    def get_run(self, run_id: UUID) -> ExtractionRunRecord:
        doc = self._db[RUNS].find_one({"_id": run_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("extraction run not found")
        return self._with_trace(doc)

    def find_run_by_idempotency(self, organization_id: UUID, key: str) -> ExtractionRunRecord | None:
        doc = self._db[RUNS].find_one(
            {"organization_id": organization_id, "idempotency_key": key}, session=self._session)
        return self._with_trace(doc) if doc else None

    def save_run(self, record: ExtractionRunRecord) -> None:
        document = to_document(self._without_trace(record))
        document["updated_at"] = datetime.now(timezone.utc)
        result = self._db[RUNS].replace_one({"_id": record.id}, document, session=self._session)
        if result.matched_count == 0:
            raise RecordNotFoundError("extraction run not found")
        self._db[RUN_TRACES].replace_one(
            {"_id": record.id}, {"_id": record.id, "doc_version": 1, "trace": record.trace},
            upsert=True, session=self._session)

    # The trace holds the accountability ledgers and is unbounded, so it lives in
    # its own collection and never counts against the run document's size.
    @staticmethod
    def _without_trace(record: ExtractionRunRecord) -> ExtractionRunRecord:
        return record.model_copy(update={"trace": {}})

    def _with_trace(self, doc: dict[str, Any]) -> ExtractionRunRecord:
        record = from_document(ExtractionRunRecord, doc)
        trace = self._db[RUN_TRACES].find_one({"_id": record.id}, session=self._session)
        return record.model_copy(update={"trace": (trace or {}).get("trace") or {}})
