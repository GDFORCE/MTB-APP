"""Archive metadata record and errors (kept apart from the service so ports can import them)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from .errors import PersistenceError
from .records import Record, utc_now

FORMAT = "uctsm-evaluation-archive.v1"
HOT, ARCHIVED, RESTORED = "HOT_HELD", "ARCHIVED", "RESTORED"


class EvaluationArchivedError(PersistenceError):
    """The projections of this evaluation are in cold storage; restore them explicitly."""


class ArchiveRefusedError(PersistenceError):
    """The evaluation is not eligible to be archived (reason in the message)."""


class ArchiveIntegrityError(PersistenceError):
    """Cold payload missing, damaged, or not the one recorded."""


class ArchiveRecord(Record):
    """Hot-store metadata for one evaluation's cold payload (or its hold)."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    patient_id: UUID
    evaluation_id: UUID
    state: str                       # HOT_HELD | ARCHIVED | RESTORED
    hold: bool = False
    hold_reason: str | None = None
    held_by: UUID | None = None
    object_key: str | None = None
    payload_hash: str | None = None
    event_count: int = 0
    confinement_count: int = 0
    format: str = FORMAT
    archived_by: UUID | None = None
    archived_at: datetime | None = None
    restored_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
