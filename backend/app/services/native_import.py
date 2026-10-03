"""Give an operational trial its canonical schedule versions, extracted by
the native LLM pipeline directly - no legacy-plan translation step.

Sibling to ``canonical_bridge.py``, not a modification of it: that module's
translation path (``extraction_result_from_plan``, reading a legacy
``CanonicalSchedulePlan``) stays untouched and independently revertible while
this one is new. This module reuses ``canonical_bridge``'s Trial/Protocol
resolution (``_trial``/``_protocol``) since that part of the shape - one
UCTSM Trial per operational trial, one Protocol per Trial - is identical
regardless of which pipeline produced the schedule; only protocol version and
schedule persistence differ, because here the source is a real uploaded PDF
with an already-computed ``ExtractionResult`` per independent schedule
option, not a plan dict to translate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.models import UniversalSchedule, ValidationIssue
from app.extraction.contracts import ExtractionResult
from app.services.canonical_bridge import _protocol, _trial
from app.services.extraction_service import ExtractionService

PROVIDER = "mtb.native_extraction"
PROMPT_VERSION = "uctsm-extractor.v1"


@dataclass(frozen=True)
class ImportedSchedule:
    """One canonical draft produced from one detected schedule option."""

    external_schedule_definition_id: str
    schedule_definition_id: UUID
    schedule_version_id: UUID
    version_number: int
    status: str
    name: str
    created: bool


def _protocol_version(
    session: Session, protocol: db.Protocol, *, document_hash: str,
    document_name: str, document_uri: str, version_label: str, actor_id: UUID,
) -> db.ProtocolVersion:
    """Differs from ``canonical_bridge``'s sibling only in what it is given:
    the real uploaded PDF's storage key and content hash, not a fingerprint
    of the legacy pipeline's already-translated plan - so a natively
    extracted protocol version points at a genuinely resolvable, auditable
    document (``canonical_bridge``'s synthetic
    ``private://protocol-extractions/{id}`` URI is never actually opened).

    Every schedule option detected in one document shares this ONE protocol
    version, unlike ``canonical_bridge``'s per-definition versions - options
    are all read from the same real PDF, so one document identity is the
    correct shape, and ``ScheduleDefinition``'s own uniqueness (protocol
    version + name + type) already assumes several schedules can share one
    protocol version.
    """
    row = session.scalar(select(db.ProtocolVersion).where(
        db.ProtocolVersion.protocol_id == protocol.id,
        db.ProtocolVersion.document_hash == document_hash,
    ))
    if row is not None:
        return row
    taken = set(session.scalars(select(db.ProtocolVersion.version_label).where(
        db.ProtocolVersion.protocol_id == protocol.id,
    )))
    label = version_label or f"Extracted {len(taken) + 1}"
    if label in taken:
        base_label, suffix = label, 2
        while label in taken:
            label = f"{base_label} ({suffix})"
            suffix += 1
    row = db.ProtocolVersion(
        protocol_id=protocol.id, version_label=label,
        document_name=document_name, document_uri=document_uri,
        document_hash=document_hash, uploaded_by=actor_id,
        extraction_status="COMPLETED", metadata_json={"source": PROVIDER},
    )
    session.add(row)
    session.flush()
    # An amendment must never silently replace the version already in use for
    # enrolment; that switch is an explicit approval step, not an import effect.
    if protocol.current_version_id is None:
        protocol.current_version_id = row.id
    return row


def _existing_version_for_run(session: Session, run_id: UUID) -> db.ScheduleVersion | None:
    return session.scalar(select(db.ScheduleVersion).where(
        db.ScheduleVersion.extraction_run_id == run_id,
    ))


def import_native_schedules(
    session: Session,
    *,
    organization_id: UUID,
    actor_id: UUID,
    external_trial_id: str,
    protocol_number: str,
    study_title: str | None,
    document_uri: str,
    document_name: str,
    document_hash: str,
    version_label: str = "",
    definitions: Iterable[dict[str, Any]],
) -> tuple[db.Trial, list[ImportedSchedule]]:
    """Import every natively extracted schedule option as a canonical draft
    version - counterpart to ``canonical_bridge.import_schedule_definitions``
    for definitions carrying an already-computed ``native_schedule``/
    ``native_issues`` payload (produced once, synchronously, by
    ``runner.extract_schedules`` at upload time - see
    ``server.py``'s ``/protocols/extract``) instead of a legacy
    ``CanonicalSchedulePlan`` to translate. No model call happens here.

    A definition with no ``native_schedule`` is skipped, same discipline
    ``canonical_bridge`` applies to a definition with no ``canonical_plan``:
    an empty schedule would look approvable while promising a patient nothing.
    """
    trial = _trial(
        session, organization_id=organization_id, external_trial_id=external_trial_id,
        study_title=study_title, protocol_id=protocol_number, actor_id=actor_id,
    )
    protocol = _protocol(session, trial, protocol_number or external_trial_id)
    protocol_version = _protocol_version(
        session, protocol, document_hash=document_hash,
        document_name=document_name, document_uri=document_uri,
        version_label=version_label, actor_id=actor_id,
    )
    service = ExtractionService(session)
    imported: list[ImportedSchedule] = []

    for definition in definitions:
        native_schedule = definition.get("native_schedule")
        external_id = str(definition.get("id") or "")
        if not isinstance(native_schedule, dict) or not native_schedule.get("events") or not external_id:
            continue

        run = service.queue(
            organization_id=organization_id, protocol_version=protocol_version,
            idempotency_key=f"native-import:{external_id}", provider=PROVIDER,
            model_name=str(definition.get("model_name") or "") or None,
            model_version=None, prompt_version=PROMPT_VERSION,
            configuration={"external_schedule_definition_id": external_id},
        )
        session.flush()
        already = _existing_version_for_run(session, run.id)
        if already is not None:
            # A retried Add Trial must reuse the draft the reviewer may already
            # be working on, not silently create a second one beside it.
            schedule_definition = session.get(db.ScheduleDefinition, already.schedule_definition_id)
            imported.append(ImportedSchedule(
                external_schedule_definition_id=external_id,
                schedule_definition_id=already.schedule_definition_id,
                schedule_version_id=already.id, version_number=already.version_number,
                status=already.status,
                name=schedule_definition.name if schedule_definition else "Primary",
                created=False,
            ))
            continue

        schedule = UniversalSchedule.model_validate(native_schedule)
        issues = [ValidationIssue.model_validate(item) for item in definition.get("native_issues") or []]
        # The ledgers computed at upload travel with the draft onto its run.
        accountability = definition.get("native_accountability")
        result = ExtractionResult(schedule=schedule, issues=issues, extraction_trace=[],
                                  accountability=accountability if isinstance(accountability, dict) else None)
        schedule_version_id = service.complete_many(run.id, [(None, result)])[0]
        version = session.get(db.ScheduleVersion, schedule_version_id)
        assert version is not None
        schedule_definition = session.get(db.ScheduleDefinition, version.schedule_definition_id)
        if schedule_definition is not None:
            # Keeps the operational document and the canonical definition
            # joined, which the parity check and the read-mode switch look up.
            schedule_definition.external_schedule_definition_id = external_id
        imported.append(ImportedSchedule(
            external_schedule_definition_id=external_id,
            schedule_definition_id=version.schedule_definition_id,
            schedule_version_id=version.id, version_number=version.version_number,
            status=version.status,
            name=schedule_definition.name if schedule_definition else "Primary",
            created=True,
        ))

    return trial, imported
