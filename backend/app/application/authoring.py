"""Records of the authoring plane: trial, protocol, extraction run, schedule version.

These replace the SQLAlchemy rows the services passed around. Scope fields
(``organization_id``, ``trial_id``, ``protocol_id``) are carried on every record
so a query is scoped without a join (P14-01).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from .records import Record, utc_now


class TrialRecord(Record):
    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    protocol_id: str | None = None
    study_title: str | None = None
    indication: str | None = None
    drug_name: str | None = None
    sponsor_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    external_trial_id: str | None = None
    #: Where this trial's visit dates are READ from (LEGACY | ENGINE).
    schedule_read_mode: str = "LEGACY"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProtocolRecord(Record):
    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    protocol_number: str
    current_version_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProtocolVersionRecord(Record):
    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    protocol_id: UUID
    version_label: str
    amendment_number: str | None = None
    effective_date: date | None = None
    document_name: str
    document_uri: str
    document_hash: str
    uploaded_by: UUID | None = None
    extraction_status: str = "PENDING"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ScheduleDefinitionRecord(Record):
    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    protocol_id: UUID
    protocol_version_id: UUID
    name: str
    description: str | None = None
    schedule_type: str = "PRIMARY"
    external_schedule_definition_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExtractionRunRecord(Record):
    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    protocol_version_id: UUID
    idempotency_key: str | None = None
    status: str = "QUEUED"
    provider: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    prompt_version: str
    schema_version: str = "uctsm.v1"
    document_hash: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    error_details: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScheduleVersionHeader(Record):
    """Lifecycle and lineage of one schedule version (not its content)."""

    id: UUID
    organization_id: UUID
    trial_id: UUID
    protocol_id: UUID
    protocol_version_id: UUID
    schedule_definition_id: UUID
    version_number: int
    status: str
    #: P6-01: the earlier APPROVED version this one amends (set once, before approval).
    #: The protocol/amendment reference is ``protocol_version_id`` (its version label,
    #: amendment number and document hash live on the protocol version).
    based_on_schedule_version_id: UUID | None = None
    #: P6-01: SHA-256 of the version's content, written in the SAME guarded write that
    #: approves it. A later change to an approved version is detectable against it.
    content_hash: str | None = None
    #: the algorithm contract ``content_hash`` was computed under (None = hashless legacy)
    content_hash_version: str | None = None
    #: digest of the approval header (lineage, effective date, approver, hash), written
    #: in the same guarded write; ``effective_from`` is not part of the content hash.
    header_integrity_hash: str | None = None
    extraction_run_id: UUID | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    #: P3-04 maker-checker provenance (see ``app.authorization.maker_checker``).
    last_material_editor_id: UUID | None = None
    last_material_edit_at: datetime | None = None
    material_editor_ids: list[UUID] = Field(default_factory=list)
    effective_from: date | None = None
    rejection_reason: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    schema_version: str = "uctsm.v1"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    #: Optimistic-concurrency counter, bumped by every header change.
    revision: int = 0


class ReviewDecisionRecord(Record):
    """Append-only reviewer decision (CONFIRM / CORRECT / APPROVE / REJECT / RESOLVE)."""

    id: UUID = Field(default_factory=uuid4)
    schedule_version_id: UUID
    entity_type: str | None = None
    entity_id: UUID | None = None
    field_path: str | None = None
    decision: str
    previous_value: Any | None = None
    new_value: Any | None = None
    reviewer_id: UUID
    reason: str | None = None
    comment: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


#: A version in one of these states is immutable (SQL trigger 0001's contract).
FROZEN_STATUSES = frozenset({"APPROVED", "SUPERSEDED"})
