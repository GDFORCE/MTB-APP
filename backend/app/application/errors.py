"""Persistence-neutral errors raised by repositories.

Services and API handlers catch these, never a database driver's exceptions, so
the storage engine stays replaceable. ``RecordNotFoundError`` is a ``KeyError``
so existing ``except KeyError`` handlers (404 responses) keep working unchanged.
"""

from __future__ import annotations


class PersistenceError(Exception):
    """Base class for every error a repository raises on purpose."""


class RecordNotFoundError(PersistenceError, KeyError):
    """The addressed record does not exist (or is outside the caller's scope)."""


class DuplicateRecordError(PersistenceError):
    """A uniqueness rule was violated: same id, idempotency key, or logical key."""

    def __init__(self, message: str, *, key: str | None = None):
        super().__init__(message)
        self.key = key


class StaleRevisionError(PersistenceError):
    """Optimistic concurrency: the record changed since the caller read it."""


class IdempotencyConflictError(PersistenceError):
    """An idempotency key was reused with a DIFFERENT payload (HTTP 409)."""


class ScheduleVersionChangedError(PersistenceError):
    """The patient is no longer on the schedule version the caller expected, or an
    idempotency key was first used under a different version (HTTP 409). Not
    retried: re-running cannot make the versions agree."""


class ImmutableRecordError(PersistenceError):
    """An approved or append-only record was addressed by a write."""


class AmendmentIntegrityError(PersistenceError):
    """An approved schedule version no longer matches the hash recorded when it was
    approved. Nothing is adopted, simulated or compared from it (HTTP 409)."""


class AmendmentBlockedError(ValueError):
    """An amendment operation was refused because a precondition does not hold
    (unreviewed mapping, stale simulation, patient moved, ...). ``blockers`` lists
    each reason with a stable code, so a caller can show what to resolve."""

    status = 409

    def __init__(self, message: str, blockers: list[dict] | None = None):
        super().__init__(message)
        self.blockers = blockers or []
