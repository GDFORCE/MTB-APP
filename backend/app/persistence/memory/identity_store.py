"""In-memory reference implementation of the identity store.

A transaction is a snapshot taken under one lock and restored when the callback
raises, so rollback semantics match the Mongo store's for single-process tests.
It proves behaviour, not storage-level concurrency (that needs real MongoDB).
"""

from __future__ import annotations

import copy
import threading
from typing import Callable, TypeVar
from uuid import UUID

from app.application.errors import (
    DuplicateRecordError, RecordNotFoundError, StaleRevisionError,
)
from app.application.protocol_identity import (
    ExtractionResultRecord, IdentityJobRecord, IdentityReviewRecord,
    ProtocolDocumentIdentity, ReviewStatus,
)

T = TypeVar("T")


class _State:
    def __init__(self) -> None:
        self.identities: dict[UUID, ProtocolDocumentIdentity] = {}
        self.reviews: list[IdentityReviewRecord] = []
        self.jobs: dict[tuple[UUID, str], IdentityJobRecord] = {}
        self.results: dict[UUID, ExtractionResultRecord] = {}


class _Tx:
    def __init__(self, state: _State):
        self._s = state

    def add_identity(self, record: ProtocolDocumentIdentity) -> None:
        if record.id in self._s.identities:
            raise DuplicateRecordError("identity already exists", key=str(record.id))
        self._s.identities[record.id] = record.model_copy(deep=True)

    def get_identity(self, organization_id: UUID, identity_id: UUID) -> ProtocolDocumentIdentity:
        row = self._s.identities.get(identity_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("protocol identity not found")
        return row.model_copy(deep=True)

    def save_identity(self, record: ProtocolDocumentIdentity, *, expected_revision: int) -> None:
        row = self._s.identities.get(record.id)
        if row is None or row.organization_id != record.organization_id:
            raise RecordNotFoundError("protocol identity not found")
        if row.revision != expected_revision:
            raise StaleRevisionError("protocol identity changed concurrently")
        self._s.identities[record.id] = record.model_copy(deep=True)

    def list_accepted(self, organization_id: UUID, identifiers: list[str]):
        wanted = set(identifiers)
        rows = [row for row in self._s.identities.values()
                if row.organization_id == organization_id and row.permitted
                and wanted & set(row.accepted_identifiers)]
        rows.sort(key=lambda row: (row.created_at, str(row.id)))
        return [row.model_copy(deep=True) for row in rows]

    def list_for_review(self, organization_id: UUID, *, review_status: ReviewStatus | None, limit: int):
        rows = [row for row in self._s.identities.values()
                if row.organization_id == organization_id
                and (review_status is None or row.review_status is review_status)
                and row.review_status is not ReviewStatus.NOT_REQUIRED]
        rows.sort(key=lambda row: (row.created_at, str(row.id)), reverse=True)
        return [row.model_copy(deep=True) for row in rows[:limit]]

    def add_review(self, record: IdentityReviewRecord) -> None:
        self._s.reviews.append(record.model_copy(deep=True))

    def list_reviews(self, organization_id: UUID, identity_id: UUID):
        rows = [row for row in self._s.reviews
                if row.organization_id == organization_id and row.identity_id == identity_id]
        rows.sort(key=lambda row: (row.created_at, str(row.id)))
        return [row.model_copy(deep=True) for row in rows]

    def find_job(self, organization_id: UUID, claim_key: str):
        row = self._s.jobs.get((organization_id, claim_key))
        return row.model_copy(deep=True) if row else None

    def add_job(self, record: IdentityJobRecord) -> None:
        key = (record.organization_id, record.claim_key)
        if key in self._s.jobs:
            raise DuplicateRecordError("job already claimed", key=record.claim_key)
        self._s.jobs[key] = record.model_copy(deep=True)

    def save_job(self, record: IdentityJobRecord, *, expected_revision: int) -> None:
        key = (record.organization_id, record.claim_key)
        row = self._s.jobs.get(key)
        if row is None:
            raise RecordNotFoundError("job not found")
        if row.revision != expected_revision:
            raise StaleRevisionError("job changed concurrently")
        self._s.jobs[key] = record.model_copy(deep=True)

    def add_result(self, record: ExtractionResultRecord) -> None:
        self._s.results[record.id] = record.model_copy(deep=True)

    def get_result(self, organization_id: UUID, result_id: UUID) -> ExtractionResultRecord:
        row = self._s.results.get(result_id)
        if row is None or row.organization_id != organization_id:
            raise RecordNotFoundError("extraction result not found")
        return row.model_copy(deep=True)


class InMemoryIdentityStore:
    def __init__(self) -> None:
        self._state = _State()
        self._lock = threading.RLock()

    def run(self, fn: Callable[[_Tx], T]) -> T:
        with self._lock:
            snapshot = copy.deepcopy(self._state)
            try:
                return fn(_Tx(self._state))
            except BaseException:
                self._state = snapshot
                raise

    def read(self, fn: Callable[[_Tx], T]) -> T:
        with self._lock:
            return fn(_Tx(self._state))
