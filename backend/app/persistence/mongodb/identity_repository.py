"""MongoDB store for protocol identity (P2): identities, reviews, jobs, results.

Collections and their guarantees (indexes are created by migration 0005):

``protocol_document_identities``  one document per UPLOAD. No uniqueness on any
    hash: separate uploads keep separate provenance. Indexed for scoped
    identifier / hash / review-queue lookups.
``identity_reviews``              insert-only reviewer decisions.
``identity_jobs``                 UNIQUE (organization_id, claim_key): the storage
    enforced claim that makes concurrent uploads and repeated review resumes
    produce one extraction.
``identity_extraction_results``   durable, reusable extraction outputs.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar
from uuid import UUID

from pymongo import ASCENDING, DESCENDING, MongoClient, ReadPreference
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.application.errors import (
    DuplicateRecordError, RecordNotFoundError, StaleRevisionError,
)
from app.application.protocol_identity import (
    ExtractionResultRecord, IdentityJobRecord, IdentityReviewRecord,
    ProtocolDocumentIdentity, ReviewStatus,
)

from .documents import from_document, to_document

T = TypeVar("T")

IDENTITIES = "protocol_document_identities"
REVIEWS = "identity_reviews"
JOBS = "identity_jobs"
RESULTS = "identity_extraction_results"


class _Tx:
    def __init__(self, database: Database, session: ClientSession | None):
        self._db = database
        self._session = session

    # identities ---------------------------------------------------------
    def add_identity(self, record: ProtocolDocumentIdentity) -> None:
        try:
            self._db[IDENTITIES].insert_one(to_document(record), session=self._session)
        except DuplicateKeyError as error:
            raise DuplicateRecordError("identity already exists", key=str(record.id)) from error

    def get_identity(self, organization_id: UUID, identity_id: UUID) -> ProtocolDocumentIdentity:
        doc = self._db[IDENTITIES].find_one(
            {"_id": identity_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("protocol identity not found")
        return from_document(ProtocolDocumentIdentity, doc)

    def save_identity(self, record: ProtocolDocumentIdentity, *, expected_revision: int) -> None:
        document = to_document(record)
        result = self._db[IDENTITIES].replace_one(
            {"_id": record.id, "organization_id": record.organization_id,
             "revision": expected_revision}, document, session=self._session)
        if result.matched_count == 0:
            exists = self._db[IDENTITIES].count_documents(
                {"_id": record.id, "organization_id": record.organization_id},
                session=self._session)
            if not exists:
                raise RecordNotFoundError("protocol identity not found")
            raise StaleRevisionError("protocol identity changed concurrently")

    def list_accepted(self, organization_id: UUID, identifiers: list[str]):
        cursor = self._db[IDENTITIES].find(
            {"organization_id": organization_id, "accepted_identifiers": {"$in": identifiers},
             "review_status": {"$in": [ReviewStatus.NOT_REQUIRED.value, ReviewStatus.APPROVED.value]}},
            session=self._session).sort([("created_at", ASCENDING), ("_id", ASCENDING)]).limit(200)
        rows = [from_document(ProtocolDocumentIdentity, doc) for doc in cursor]
        return [row for row in rows if row.permitted]

    def list_for_review(self, organization_id: UUID, *, review_status: ReviewStatus | None, limit: int):
        query: dict[str, Any] = {"organization_id": organization_id}
        if review_status is not None:
            query["review_status"] = review_status.value
        else:
            query["review_status"] = {"$ne": ReviewStatus.NOT_REQUIRED.value}
        cursor = self._db[IDENTITIES].find(query, session=self._session).sort(
            [("created_at", DESCENDING), ("_id", DESCENDING)]).limit(limit)
        return [from_document(ProtocolDocumentIdentity, doc) for doc in cursor]

    # reviews ------------------------------------------------------------
    def add_review(self, record: IdentityReviewRecord) -> None:
        self._db[REVIEWS].insert_one(to_document(record), session=self._session)

    def list_reviews(self, organization_id: UUID, identity_id: UUID):
        cursor = self._db[REVIEWS].find(
            {"organization_id": organization_id, "identity_id": identity_id},
            session=self._session).sort([("created_at", ASCENDING), ("_id", ASCENDING)]).limit(500)
        return [from_document(IdentityReviewRecord, doc) for doc in cursor]

    # jobs ---------------------------------------------------------------
    def find_job(self, organization_id: UUID, claim_key: str):
        doc = self._db[JOBS].find_one(
            {"organization_id": organization_id, "claim_key": claim_key}, session=self._session)
        return from_document(IdentityJobRecord, doc) if doc else None

    def add_job(self, record: IdentityJobRecord) -> None:
        try:
            self._db[JOBS].insert_one(to_document(record), session=self._session)
        except DuplicateKeyError as error:
            raise DuplicateRecordError("job already claimed", key=record.claim_key) from error

    def save_job(self, record: IdentityJobRecord, *, expected_revision: int) -> None:
        result = self._db[JOBS].replace_one(
            {"_id": record.id, "organization_id": record.organization_id,
             "revision": expected_revision}, to_document(record), session=self._session)
        if result.matched_count == 0:
            raise StaleRevisionError("job changed concurrently")

    # results ------------------------------------------------------------
    def add_result(self, record: ExtractionResultRecord) -> None:
        self._db[RESULTS].insert_one(to_document(record), session=self._session)

    def get_result(self, organization_id: UUID, result_id: UUID) -> ExtractionResultRecord:
        doc = self._db[RESULTS].find_one(
            {"_id": result_id, "organization_id": organization_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("extraction result not found")
        return from_document(ExtractionResultRecord, doc)


class MongoIdentityStore:
    """Transactions need a replica set (the same requirement as the UCTSM port)."""

    def __init__(self, client: MongoClient, database: Database):
        self._client = client
        self._database = database

    def run(self, fn: Callable[[_Tx], T]) -> T:
        with self._client.start_session() as session:
            return session.with_transaction(
                lambda active: fn(_Tx(self._database, active)),
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
                read_preference=ReadPreference.PRIMARY)

    def read(self, fn: Callable[[_Tx], T]) -> T:
        return fn(_Tx(self._database, None))
