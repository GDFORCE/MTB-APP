"""Turns a queued extraction run into an executed one.

The API queues a run and returns immediately; the background worker calls
``execute_run``, which loads the stored document and hands its pages to
``extract_schedules``. Failures are recorded on the run rather than raised into
the request that started it.

``extract_schedules`` is reader -> compiler: the model writes cited schedule
facts (``reader.py``), deterministic code compiles them into one canonical
schedule per independent Schedule of Assessments (``compiler.py``). The
engine is available only when a provider and its key are configured
(``UCTSM_EXTRACTION_PROVIDER`` = anthropic | gemini | openai, plus
``ANTHROPIC_API_KEY``, ``GEMINI_API_KEY`` or ``OPENAI_API_KEY``); otherwise queued runs fail immediately with a clear
reason.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.application.ports import UnitOfWork

from app.db import models as db
from app.services.extraction_service import ExtractionService
from .compiler import compile_document
from .contracts import DocumentPage, ExtractionResult
from .documents import load_pages
from .facts import DocumentFacts, TrialMetadata
from .llm_client import (
    AnthropicClient, ExtractionModelError, ExtractionNotConfigured, GeminiClient, LLMClient,
    OpenAIClient,
)
from .artifacts import ArtifactStore, StorageArtifactStore
from .ledger import ACCOUNTABILITY_SCHEMA, UNCOMPILED
from .reader import ReaderLimits, read_document, read_trial_metadata
from .reconcile import RECONCILER_VERSION, reconcile

log = logging.getLogger(__name__)

ENGINE_UNAVAILABLE_MESSAGE = (
    "Automatic protocol extraction is not configured on this server: set "
    "UCTSM_EXTRACTION_PROVIDER (anthropic, gemini or openai) and the matching API key."
)

_PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY",
}


def configured_provider() -> str | None:
    """The explicitly selected provider, when its key is present.

    Explicit on purpose: a key set for another feature (remote-audit vision
    checks use the same keys) must not silently switch protocol extraction on.
    """
    provider = (os.getenv("UCTSM_EXTRACTION_PROVIDER") or "").strip().lower()
    key = _PROVIDER_KEYS.get(provider)
    if key is None or not (os.getenv(key) or "").strip():
        return None
    return provider


def engine_available() -> bool:
    """Whether a run can execute at all, so callers can say so up front."""
    return configured_provider() is not None


def engine_signature() -> str | None:
    """What a stored extraction result must have been produced by to be reused:
    provider, model, prompt/reader, reconciler and compiler versions. None when no
    provider is configured, so no stored result is ever called "compatible"
    against an engine that cannot be identified."""
    provider = configured_provider()
    if provider is None:
        return None
    from .compiler import COMPILER_VERSION
    from .reader import PROMPT_VERSION
    from .reconcile import RECONCILER_VERSION
    return "|".join([
        provider, (os.getenv("UCTSM_EXTRACTION_MODEL") or "").strip(), PROMPT_VERSION,
        RECONCILER_VERSION, COMPILER_VERSION, "uctsm.v1"])


def build_client(*, pdf_bytes: bytes | None = None) -> LLMClient:
    """The configured model client. ``pdf_bytes`` is attached only when some
    page has no usable text, so scanned pages can be read from the image."""
    provider = configured_provider()
    if provider is None:
        raise ExtractionNotConfigured(ENGINE_UNAVAILABLE_MESSAGE)
    if provider == "gemini":
        return GeminiClient(pdf_bytes=pdf_bytes)
    if provider == "openai":
        return OpenAIClient(pdf_bytes=pdf_bytes)
    return AnthropicClient(pdf_bytes=pdf_bytes)


def _client_for(pages: list[DocumentPage], pdf_bytes: bytes | None) -> LLMClient:
    # Every page is read from the PDF itself as well as from its text: tables
    # (the Schedule of Assessments above all) often extract as a fragment of
    # their cells, which a text-only read cannot detect or recover.
    return build_client(pdf_bytes=pdf_bytes)


@dataclass
class ProtocolExtraction:
    """One protocol read: trial details plus one result per schedule option."""

    details: TrialMetadata
    facts: DocumentFacts
    results: list[tuple[dict[str, Any] | None, ExtractionResult]]


def build_accountability(document: DocumentFacts, compiled: dict[str, Any] | None) -> dict[str, Any]:
    """The JSON agent/review surfaces read (shape ``ACCOUNTABILITY_SCHEMA``):
    coverage from the reader, dispositions from the compiler, relations and
    anchor reconciliation from the whole-document pass."""
    source = document.accountability or {}
    compiled = compiled or {}
    reconciliation = source.get("reconciliation") or {}
    coverage = source.get("coverage") or {
        "status": "NOT_ASSESSED", "counts": {}, "gaps": [], "units": [], "references": [],
        "notes": ["These facts were not produced by the document reader, so no coverage claim is made."]}
    statements = compiled.get("statements") or []
    uncompiled = [item for item in statements if item["disposition"] in UNCOMPILED]
    return {
        "schema": ACCOUNTABILITY_SCHEMA,
        "versions": {**(source.get("versions") or {}), "compiler": compiled.get("compiler"),
                     "reconciler": reconciliation.get("version")},
        "document": source.get("document"),
        "summary": {
            "coverage_status": coverage.get("status"),
            "coverage_gaps": len(coverage.get("gaps") or []),
            "statements": len(statements),
            "dispositions": compiled.get("dispositions") or {},
            "uncompiled_statements": len(uncompiled),
            "complete": coverage.get("status") == "COMPLETE" and not uncompiled,
        },
        "coverage": coverage,
        "statements": statements,
        "relations": compiled.get("relations") or [],
        "anchors": {
            "canonical": reconciliation.get("canonical_anchors") or [],
            "aliases": reconciliation.get("anchor_aliases") or [],
            "unmatched": reconciliation.get("unmatched_anchors") or [],
        },
        "grouped_visits": reconciliation.get("grouped_visits") or [],
        "occurrence_splits": reconciliation.get("occurrence_splits") or [],
    }


def extract_protocol(
    pages: list[DocumentPage], *, pdf_bytes: bytes | None, document_hash: str,
    schedule_name: str = "Schedule of Assessments", client: LLMClient | None = None,
    artifacts: ArtifactStore | None = None, limits: ReaderLimits | None = None,
) -> ProtocolExtraction:
    """Reader, whole-document reconciliation, then compiler. Blocking (model
    calls); run it off the event loop. ``artifacts`` makes the read resumable:
    completed calls for this document are reused, not repeated."""
    client = client or _client_for(pages, pdf_bytes)
    facts = read_document(pages, client, document_hash=document_hash, artifacts=artifacts, limits=limits)
    facts = reconcile(facts)
    facts.trace.append({"node": "document", "document_hash": document_hash})
    facts.trace.append({"node": "reconcile", "reconciler": RECONCILER_VERSION,
                        "duplicates_removed": len(facts.accountability["reconciliation"]["duplicates_removed"]),
                        "unmatched_anchors": len(facts.accountability["reconciliation"]["unmatched_anchors"])})
    results = compile_document(facts, schedule_name=schedule_name)
    for _option, result in results:
        result.extraction_trace[:0] = facts.trace
        result.accountability = build_accountability(facts, result.accountability)
    return ProtocolExtraction(details=facts.trial, facts=facts, results=results)


def extract_trial_details(
    pages: list[DocumentPage], *, pdf_bytes: bytes | None, client: LLMClient | None = None,
) -> TrialMetadata:
    """Only the Add-Trial form fields: one bounded model call."""
    return read_trial_metadata(pages, client or _client_for(pages, pdf_bytes))


def extract_schedules(
    pages: list[DocumentPage], *, pdf_bytes: bytes | None, document_hash: str,
    schedule_name: str = "Schedule of Assessments", client: LLMClient | None = None,
    artifacts: ArtifactStore | None = None, limits: ReaderLimits | None = None,
) -> list[tuple[dict[str, Any] | None, ExtractionResult]]:
    """One ``(schedule option, result)`` pair per independent Schedule of
    Assessments the document prints; the option is None for a single schedule.
    """
    if client is None and not engine_available():
        raise ExtractionNotConfigured(ENGINE_UNAVAILABLE_MESSAGE)
    return extract_protocol(
        pages, pdf_bytes=pdf_bytes, document_hash=document_hash,
        schedule_name=schedule_name, client=client, artifacts=artifacts, limits=limits,
    ).results


def storage_artifacts() -> ArtifactStore:
    """Artifacts in the app's file storage (local disk or S3)."""
    from storage import get_storage
    return StorageArtifactStore(get_storage())


async def _clear_identity(
    *, organization_id: UUID, version, pages: list[DocumentPage], pdf_bytes: bytes | None,
    document_hash: str, expected: list[str],
) -> None:
    """P2-02 worker gate: the stored document must pass protocol identity
    verification before a run starts, whatever queued it. Raises
    ``IdentityBlockedError`` (recorded on the run as FAILED, no model call)."""
    import hashlib

    import protocol_identity_runtime as gate

    binary = hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes else document_hash
    content_type = "application/pdf" if pdf_bytes else "text/plain"
    await run_in_threadpool(lambda: gate.clear_stored_document(
        organization_id=organization_id, owner=str(getattr(version, "uploaded_by", "") or ""),
        filename=version.document_name or "protocol", pages=pages, binary_sha256=binary,
        content_type=content_type, expected_values=expected,
        protocol_id=version.protocol_id, protocol_version_id=version.id))


async def execute_run(session: Session, run_id: UUID) -> list[UUID] | None:
    """Run one queued extraction to completion.

    Returns the new schedule version ids, or None when extraction failed. The
    failure is already recorded on the run, so the caller does not have to.
    """
    run = session.get(db.ExtractionRun, run_id)
    if run is None:
        raise KeyError("extraction run not found")
    version = session.get(db.ProtocolVersion, run.protocol_version_id)
    if version is None:
        raise KeyError("protocol version not found")

    try:
        pages, pdf_bytes = await load_pages(version.document_uri)
        protocol = session.get(db.Protocol, version.protocol_id)
        await _clear_identity(
            organization_id=run.organization_id, version=version, pages=pages,
            pdf_bytes=pdf_bytes, document_hash=run.document_hash,
            expected=[protocol.protocol_number] if protocol is not None else [])
        service = ExtractionService(session)
        service.start(run_id)
        # Engines make blocking model calls; keep them off the event loop.
        results = await run_in_threadpool(
            extract_schedules, pages, pdf_bytes=pdf_bytes,
            document_hash=run.document_hash,
            schedule_name=version.document_name or "Schedule of Assessments",
            artifacts=storage_artifacts(),
        )
        version_ids = service.complete_many(run_id, results)
        session.commit()
        return version_ids
    except (ExtractionModelError, Exception) as error:  # noqa: BLE001
        session.rollback()
        failed = session.get(db.ExtractionRun, run_id, with_for_update=True)
        if failed is not None and failed.status not in {"COMPLETED"}:
            failed.status = "FAILED"
            failed.error_details = {
                "type": type(error).__name__, "message": str(error)[:2000],
            }
            session.commit()
        log.exception("extraction run %s failed", run_id)
        return None


async def execute_run_on_ports(uow: "UnitOfWork", run_id: UUID) -> list[UUID] | None:
    """``execute_run`` on the repository ports (no SQLAlchemy session).

    Every state change is its own unit of work, and none is held open across the
    model calls: the run is marked RUNNING and committed, the slow blocking read
    happens with no transaction, and completion commits all drafts together.
    Failures are recorded on the run rather than raised.
    """
    from app.application.extraction import ExtractionService as PortExtraction

    service = PortExtraction(uow)
    try:
        run = uow.read(lambda tx: tx.authoring.get_run(run_id))
    except KeyError as error:
        raise KeyError("extraction run not found") from error
    try:
        version = uow.read(lambda tx: tx.authoring.get_protocol_version(run.protocol_version_id))
    except KeyError as error:
        raise KeyError("protocol version not found") from error

    try:
        pages, pdf_bytes = await load_pages(version.document_uri)
        try:
            protocol = uow.read(lambda tx: tx.authoring.get_protocol(version.protocol_id))
            expected = [protocol.protocol_number]
        except KeyError:
            expected = []
        await _clear_identity(
            organization_id=run.organization_id, version=version, pages=pages,
            pdf_bytes=pdf_bytes, document_hash=run.document_hash, expected=expected)
        service.start(run_id)
        results = await run_in_threadpool(
            extract_schedules, pages, pdf_bytes=pdf_bytes,
            document_hash=run.document_hash,
            schedule_name=version.document_name or "Schedule of Assessments",
            artifacts=storage_artifacts(),
        )
        return service.complete_many(run_id, results)
    except (ExtractionModelError, Exception) as error:  # noqa: BLE001
        service.fail(run_id, error)
        log.exception("extraction run %s failed", run_id)
        return None
