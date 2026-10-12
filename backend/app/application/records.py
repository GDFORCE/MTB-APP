"""Typed records shared between services and repositories.

Plain pydantic models with application-generated UUIDs; no database types.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


_clock_lock = threading.Lock()
_last_instant: datetime | None = None


def utc_now() -> datetime:
    """Current instant: millisecond precision and strictly increasing per process.

    BSON dates carry milliseconds, so a finer clock would read back different
    from what was written. Strict monotonicity keeps two facts recorded in the
    same millisecond in the order they were recorded (a correction must sort
    after the record it corrects).
    """
    global _last_instant
    now = datetime.now(timezone.utc)
    now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
    with _clock_lock:
        if _last_instant is not None and now <= _last_instant:
            now = _last_instant + timedelta(milliseconds=1)
        _last_instant = now
    return now


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CommandReceiptRecord(Record):
    """Proof that one idempotency key already produced one result."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    patient_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)
    command_type: str
    payload_hash: str
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class OutboxRecord(Record):
    """A notification-worthy fact, written in the same transaction as the
    change it describes. Consumers (reminders, calendars) are a later epic."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str
    status: str = "PENDING"
    attempts: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class AuditRecord(Record):
    """One append-only audit event (replaces ``uctsm_audit_events``)."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    actor_id: UUID | None = None
    action: str = Field(min_length=1, max_length=128)
    entity_type: str = Field(min_length=1, max_length=64)
    entity_id: UUID
    before: Any | None = None
    after: Any | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
