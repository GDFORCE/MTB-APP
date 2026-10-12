"""Repository and unit-of-work ports (P14-05).

Python ``Protocol`` interfaces. There is ONE production implementation
(``app.persistence.mongodb``); ``app.persistence.memory`` holds fakes for fast
unit tests, exercised by the same contract suite.

Rules every implementation follows:
* identities are application-generated UUIDs, never database object ids;
* a method that appends history has no update or delete counterpart;
* conditional writes (status guards, revisions) raise ``StaleRevisionError`` /
  ``ImmutableRecordError`` instead of silently doing nothing;
* repositories never commit - the unit of work does.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol, TypeVar
from uuid import UUID

from .records import AuditRecord

T = TypeVar("T")


class AuditRepository(Protocol):
    def append(self, record: AuditRecord) -> None:
        """Insert-only. Raises ``DuplicateRecordError`` on a repeated id."""
        ...

    def list_for_entity(
        self, organization_id: UUID, entity_type: str, entity_id: UUID, *, limit: int = 500,
    ) -> list[AuditRecord]:
        """Oldest first."""
        ...

    def list_for_organization(
        self, organization_id: UUID, *, action: str | None = None,
        since: datetime | None = None, limit: int = 500,
    ) -> list[AuditRecord]:
        """Newest first."""
        ...


class UnitOfWork(Protocol):
    """One atomic business operation.

    ``run`` executes ``fn`` inside a transaction and commits when it returns.
    ``fn`` MUST be deterministic and free of external side effects: a transient
    transaction error re-runs it from the start.
    """

    audit: AuditRepository

    def run(self, fn: "Callable[[UnitOfWork], T]") -> T: ...

    def read(self, fn: "Callable[[UnitOfWork], T]") -> T:
        """Run read-only work outside a transaction."""
        ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
