"""Turns a queued extraction run into an executed one.

The API queues a run and returns immediately, because reading a two-hundred page
protocol takes minutes. This module is what the background worker calls: it
fetches the stored document, turns it into pages, builds the model-backed
provider, and hands both to the pipeline.

Failures are recorded on the run rather than raised into the request that
started it. A sponsor who uploads a protocol while the model provider is down
should see "extraction failed, and why" against their upload - not a silent
QUEUED row that never moves.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.models import ValidationIssue
from app.services.extraction_service import ExtractionService
from .claude_provider import ClaudeExtractionProvider
from .graph import DocumentPage, ExtractionResult, run_extraction
from .llm_client import AnthropicClient, ExtractionModelError, GeminiClient, LLMClient

log = logging.getLogger(__name__)

#: Reject oversized input explicitly until section retrieval is implemented;
#: silently truncating the document can drop visits or referenced footnotes.
MAX_DOCUMENT_CHARS = 600_000


def pages_from_pdf(pdf_bytes: bytes) -> list[DocumentPage]:
    """Read a PDF into per-page text.

    Page numbers are 1-based and preserved, because every piece of evidence the
    model cites is checked against a page a reviewer will open.

    AD-2a: each page is also classified by how much embedded text it actually
    has. A page with little or none is a scanned/image page, and pypdfium2's
    text layer alone has nothing useful to offer it - that classification is
    what lets ``build_provider`` decide whether this document needs the
    native-PDF vision fallback at all, rather than attaching every protocol's
    full PDF to every model call regardless of need.
    """
    from protocol_document_index import _extract_pdf_text_pages, _text_status

    return [
        DocumentPage(page_number=index + 1, text=text, text_status=_text_status(text))
        for index, text in enumerate(_extract_pdf_text_pages(pdf_bytes))
    ]


def pages_from_text(text: str) -> list[DocumentPage]:
    """Split plain text on form feeds, falling back to a single page."""
    parts = text.split("\f") if "\f" in text else [text]
    return [
        DocumentPage(page_number=index + 1, text=part)
        for index, part in enumerate(parts)
    ]


#: AD-2a: extraction must never proceed on empty text as though the page said
#: nothing - this marker tells the model exactly which pages that applies to,
#: rather than letting a blank ``page.text`` silently read as "no content
#: here" the same way a genuinely empty page would.
_NO_TEXT_MARKER = (
    "\n[This page has little or no embedded text - it is a scanned or "
    "image-only page. Read it from the attached PDF's own page image "
    "instead of from this text block.]"
)


def document_text(pages: list[DocumentPage]) -> str:
    """The document as the model sees it, with page markers it can cite."""
    rendered: list[str] = []
    used = 0
    for page in pages:
        marker = _NO_TEXT_MARKER if page.text_status != "text" else ""
        block = f"\n--- page {page.page_number} ---\n{page.text}{marker}"
        if used + len(block) > MAX_DOCUMENT_CHARS:
            raise ExtractionModelError(
                f"Document exceeds the extraction text limit at page {page.page_number}. "
                "No pages were silently omitted. Split the document into complete "
                "schedule sections including their footnotes and referenced timing rules."
            )
        rendered.append(block)
        used += len(block)
    return "".join(rendered)


#: Which LLMClient answers extraction calls. "anthropic" (default) or
#: "gemini" - both implement the identical LLMClient contract, so this is
#: purely a transport swap; the assembler (claude_provider.py) and every
#: prompt are unchanged either way. Not auto-detected from which API key
#: happens to be set: an operator who has both configured must get the one
#: they asked for, not a silent pick.
def _selected_provider() -> str:
    return (os.getenv("UCTSM_EXTRACTION_PROVIDER") or "anthropic").strip().lower()


def build_client(
    *, document_text: str = "", pdf_bytes: bytes | None = None,
) -> LLMClient:
    if _selected_provider() == "gemini":
        return GeminiClient(document_text=document_text, pdf_bytes=pdf_bytes)
    return AnthropicClient(document_text=document_text, pdf_bytes=pdf_bytes)


def source_first_enabled() -> bool:
    return os.getenv("UCTSM_EXTRACTION_PIPELINE", "category").strip().lower() == "source_first"


def build_provider(
    pages: list[DocumentPage], *, schedule_name: str = "Schedule of Assessments",
    pdf_bytes: bytes | None = None,
) -> ClaudeExtractionProvider:
    """The model-backed provider, configured from the environment.

    AD-2a: the native PDF is attached only when at least one page actually
    needs it (no embedded text to extract). A born-digital protocol with a
    normal text layer on every page - the common case - never pays for or
    sends the extra document bytes; a scanned or mixed one always can, since
    both providers' own native PDF understanding reads a page image where
    pypdfium2's text layer has nothing.
    """
    needs_image_fallback = any(page.text_status != "text" for page in pages)
    client = build_client(
        document_text=document_text(pages),
        pdf_bytes=pdf_bytes if needs_image_fallback else None,
    )
    return ClaudeExtractionProvider(client, schedule_name=schedule_name)


def run_extraction_for_all_options(
    client: LLMClient, pages: list[DocumentPage], *, document_hash: str,
    schedule_name: str = "Schedule of Assessments",
) -> list[tuple[dict[str, Any] | None, ExtractionResult]]:
    """Run the extraction pipeline once per independent Schedule of Assessments
    the document prints.

    Takes an already-built client, same separation build_provider already
    keeps between "construct the client from pages/env" and "run the
    pipeline against it" - so this is directly testable with a scripted
    client, the same way run_extraction already is.

    A document with a single schedule (the overwhelming majority) costs
    exactly what a single ungated run costs today: one classification call,
    one pipeline run. A document with several independent schedules extracts
    every one of them, each with its own full pipeline run, reusing the SAME
    client (and so the same cached document) across every option rather than
    paying the document-caching setup cost again per option.

    The pipeline is synchronous (LLMClient.complete is a blocking call), so
    options run sequentially rather than concurrently - kept simple rather
    than adding thread-pool fan-out this session has no way to load-test.
    """
    pipeline = os.getenv("UCTSM_EXTRACTION_PIPELINE", "category").strip().lower()
    if pipeline == "source_first":
        from .source_first import run_source_first
        return run_source_first(client, pages, document_hash=document_hash, schedule_name=schedule_name)
    if pipeline != "category":
        raise ExtractionModelError(f"Unknown UCTSM extraction pipeline: {pipeline}")
    probe = ClaudeExtractionProvider(client, schedule_name=schedule_name)
    options = probe.detect_schedule_options([page.model_dump() for page in pages])
    if not options:
        return [(None, run_extraction(probe, document_hash=document_hash, pages=pages))]

    results: list[tuple[dict[str, Any] | None, ExtractionResult]] = []
    for option in options:
        scoped = ClaudeExtractionProvider(client, schedule_name=schedule_name, schedule_option=option)
        try:
            result = run_extraction(scoped, document_hash=document_hash, pages=pages)
        except Exception as error:  # noqa: BLE001 - one bad option must not sink the batch
            log.warning("extraction failed for schedule option %s (%s): %s",
                        option.get("id"), option.get("label"), error)
            result = ExtractionResult(schedule=None, issues=[ValidationIssue(
                issue_code="SCHEDULE_OPTION_FAILED", severity="ERROR", blocking=True,
                message=f"Extraction failed for {option.get('label') or option.get('id')}: {error}",
                details={"option": option, "origin": "EXTRACTION"},
            )], extraction_trace=[])
        results.append((option, result))
        if result.schedule is not None:
            result.schedule.schedule_metadata.name = option["label"]
    failures = [issue for _, result in results if result.schedule is None for issue in result.issues]
    # A surviving table must not imply the entire uploaded protocol was read.
    for _, result in results:
        if result.schedule is not None:
            result.issues.extend(failures)
    return results


def provider_configured() -> bool:
    """Whether extraction can run at all, so callers can say so up front."""
    if _selected_provider() == "gemini":
        return bool(os.getenv("GEMINI_API_KEY"))
    return bool(os.getenv("ANTHROPIC_API_KEY"))


async def load_pages(document_uri: str) -> tuple[list[DocumentPage], bytes | None]:
    """Fetch a stored protocol document and turn it into pages.

    Returns the raw PDF bytes alongside the pages (``None`` for a plain-text
    upload) so ``build_provider`` can attach them for the image fallback -
    without re-fetching the document a second time just to get the bytes
    ``pages_from_pdf`` already read once.
    """
    from storage import get_storage

    key = document_uri.split("://", 1)[-1] if "://" in document_uri else document_uri
    data, content_type = await get_storage().open(key)
    if "pdf" in (content_type or "").lower() or document_uri.lower().endswith(".pdf"):
        return pages_from_pdf(data), data
    return pages_from_text(data.decode("utf-8", errors="replace")), None


async def execute_run(session: Session, run_id: UUID) -> list[UUID] | None:
    """Run one queued extraction to completion.

    Returns the new schedule version ids - one per independent Schedule of
    Assessments the document printed (see run_extraction_for_all_options) -
    or None when extraction failed. The failure is already recorded on the
    run, so the caller does not have to.
    """
    run = session.get(db.ExtractionRun, run_id)
    if run is None:
        raise KeyError("extraction run not found")
    version = session.get(db.ProtocolVersion, run.protocol_version_id)
    if version is None:
        raise KeyError("protocol version not found")

    try:
        pages, pdf_bytes = await load_pages(version.document_uri)
        needs_image_fallback = any(page.text_status != "text" for page in pages)
        client = build_client(
            document_text=document_text(pages),
            pdf_bytes=pdf_bytes if needs_image_fallback or source_first_enabled() else None,
        )
        service = ExtractionService(session)
        service.start(run_id)
        # LLMClient.complete is a blocking call (the sync Anthropic/Gemini
        # SDK, unlike the async client the legacy extraction pipeline uses) -
        # offloaded to a thread so a multi-minute, possibly multi-schedule
        # extraction does not stall the event loop for every other request
        # this process is serving at the same time.
        results = await run_in_threadpool(
            run_extraction_for_all_options, client, pages,
            document_hash=run.document_hash,
            schedule_name=version.document_name or "Schedule of Assessments",
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
