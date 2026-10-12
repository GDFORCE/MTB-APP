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


class ImmutableRecordError(PersistenceError):
    """An approved or append-only record was addressed by a write."""
