"""Checkpoint and source-mapping records of the legacy -> UCTSM migration job (P11-02)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from .records import Record, utc_now

RUN_STATUSES = ("RUNNING", "COMPLETED", "PARTIAL")
MAPPING_STATUSES = ("MIGRATED", "RECONCILED", "DIFFERENCES", "FAILED")


class MigrationRunRecord(Record):
    """One resumable run, keyed by (organization, run_key). ``cursor`` is the last legacy id handled."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    run_key: str = Field(min_length=1, max_length=120)
    trial_id: UUID
    external_trial_id: str
    source_system: str
    status: str = "RUNNING"
    cursor: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    started_by: UUID
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    revision: int = 1


class SourceMappingRecord(Record):
    """Legacy id -> canonical id, with the hash of the source document that produced it."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    source_system: str
    source_id: str
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    patient_id: UUID | None = None
    status: str
    parity_verdict: str | None = None
    compared: int = 0
    matched: int = 0
    difference_codes: list[str] = Field(default_factory=list)
    error_code: str | None = None
    run_id: UUID | None = None
    updated_at: datetime = Field(default_factory=utc_now)
    extra: dict[str, Any] = Field(default_factory=dict)
