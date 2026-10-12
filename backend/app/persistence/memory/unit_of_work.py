from __future__ import annotations

import copy
from datetime import datetime
from typing import Callable, TypeVar
from uuid import UUID

from app.application.errors import DuplicateRecordError
from app.application.records import AuditRecord

T = TypeVar("T")


class _Store:
    def __init__(self) -> None:
        self.audit: dict[UUID, AuditRecord] = {}


class InMemoryAuditRepository:
    def __init__(self, store: _Store):
        self._store = store

    def append(self, record: AuditRecord) -> None:
        if record.id in self._store.audit:
            raise DuplicateRecordError(f"audit event {record.id} already exists", key=str(record.id))
        self._store.audit[record.id] = record.model_copy(deep=True)

    def list_for_entity(
        self, organization_id: UUID, entity_type: str, entity_id: UUID, *, limit: int = 500,
    ) -> list[AuditRecord]:
        rows = [item for item in self._store.audit.values()
                if (item.organization_id, item.entity_type, item.entity_id)
                == (organization_id, entity_type, entity_id)]
        rows.sort(key=lambda item: (item.created_at, str(item.id)))
        return [item.model_copy(deep=True) for item in rows[:limit]]

    def list_for_organization(
        self, organization_id: UUID, *, action: str | None = None,
        since: datetime | None = None, limit: int = 500,
    ) -> list[AuditRecord]:
        rows = [item for item in self._store.audit.values()
                if item.organization_id == organization_id
                and (action is None or item.action == action)
                and (since is None or item.created_at >= since)]
        rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        return [item.model_copy(deep=True) for item in rows[:limit]]


class InMemoryUnitOfWork:
    """Atomic like the real store: an exception restores the pre-run state."""

    def __init__(self) -> None:
        self._store = _Store()
        self.audit = InMemoryAuditRepository(self._store)

    def run(self, fn: Callable[["InMemoryUnitOfWork"], T]) -> T:
        snapshot = copy.deepcopy(self._store.__dict__)
        try:
            return fn(self)
        except BaseException:
            self._store.__dict__.clear()
            self._store.__dict__.update(snapshot)
            raise

    def read(self, fn: Callable[["InMemoryUnitOfWork"], T]) -> T:
        return fn(self)
