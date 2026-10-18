"""Helpers over the append-only patient command ledger (P5-03).

Shared by the command service and by the older fact-recording entry points, so a
fact recorded through ``record_actual_occurrence`` is in the SAME ledger as one
recorded through a previewed command (one audit trail, no bypass).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from app.domain.schedule.command_impact import canonical_payload_hash
from app.domain.schedule.commands import COMMAND_SEMANTICS_VERSION, CommandType, LedgerEntry

from .patients import PatientCommandRecord
from .ports import UnitOfWork


def to_entry(record: PatientCommandRecord) -> LedgerEntry:
    return LedgerEntry(
        id=str(record.id), type=CommandType(record.command_type), payload=dict(record.payload),
        sequence=record.sequence, effective_at=record.effective_at,
        reverses_command_id=(str(record.reverses_command_id) if record.reverses_command_id else None),
        authority=record.authority, reason=record.reason)


def records_for(
    tx: UnitOfWork, organization_id: UUID, patient_id: UUID,
) -> list[PatientCommandRecord]:
    return tx.ledger.list_for_patient(organization_id, patient_id)


def entries_for(
    tx: UnitOfWork, organization_id: UUID, patient_id: UUID, schedule_version_id: UUID,
) -> list[LedgerEntry]:
    """Active inputs are scoped to the schedule version they were recorded on:
    an amendment is a new schedule version and starts without them."""
    return [to_entry(item) for item in records_for(tx, organization_id, patient_id)
            if item.schedule_version_id == schedule_version_id]


def next_sequence(records: list[PatientCommandRecord]) -> int:
    return max((item.sequence for item in records), default=0) + 1


def material_hash(
    *, patient_id: UUID, schedule_version_id: UUID, command_type: str, payload: dict[str, Any],
    effective_at: date, horizon: date | None = None,
) -> str:
    """Hash of the MEANING of a command: what, to whom, when. The reason text and
    the clock are not material (changing them is not a different operation)."""
    return canonical_payload_hash(
        COMMAND_SEMANTICS_VERSION, str(patient_id), str(schedule_version_id), command_type,
        payload, effective_at.isoformat(), horizon.isoformat() if horizon else None)


def append(
    tx: UnitOfWork, *, organization_id: UUID, patient_id: UUID, schedule_version_id: UUID,
    command_type: CommandType, payload: dict[str, Any], effective_at: date, actor_id: UUID,
    reason: str, authority: str | None = None, track_code: str | None = None,
    logical_key: str | None = None, before: dict[str, Any] | None = None,
    requested: dict[str, Any] | None = None, proposal_id: UUID | None = None,
    base_revision: str | None = None, idempotency_key: str | None = None,
    payload_hash: str, reverses_command_id: UUID | None = None,
    fact_refs: dict[str, Any] | None = None, result_evaluation_id: UUID | None = None,
    records: list[PatientCommandRecord] | None = None,
) -> PatientCommandRecord:
    existing = records if records is not None else records_for(tx, organization_id, patient_id)
    record = PatientCommandRecord(
        organization_id=organization_id, patient_id=patient_id,
        schedule_version_id=schedule_version_id, sequence=next_sequence(existing),
        command_type=command_type.value, track_code=track_code, logical_key=logical_key,
        payload=payload, effective_at=effective_at, actor_id=actor_id, authority=authority,
        reason=reason, before=before or {}, requested=requested or {}, proposal_id=proposal_id,
        base_revision=base_revision, idempotency_key=idempotency_key, payload_hash=payload_hash,
        reverses_command_id=reverses_command_id, fact_refs=fact_refs or {},
        result_evaluation_id=result_evaluation_id)
    tx.ledger.add(record)
    return record
