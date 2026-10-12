"""Typed records shared between services and repositories.

Plain pydantic models with application-generated UUIDs; no database types.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


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
