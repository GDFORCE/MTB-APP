"""Turns a queued extraction run into an executed one.

The API queues a run and returns immediately; the background worker calls
``execute_run``, which loads the stored document and hands its pages to
``extract_schedules``. Failures are recorded on the run rather than raised into
the request that started it.

No extraction engine is installed: the previous pipelines were removed ahead of
the adaptive extraction rebuild. ``extract_schedules`` is the single seam the
new engine plugs into; until then ``engine_available`` is False and queued runs
fail immediately with a clear reason.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db import models as db
from app.services.extraction_service import ExtractionService
from .contracts import DocumentPage, ExtractionResult
from .documents import load_pages
from .llm_client import ExtractionModelError, ExtractionNotConfigured

log = logging.getLogger(__name__)

ENGINE_UNAVAILABLE_MESSAGE = (
    "Automatic protocol extraction is being rebuilt and is not available yet."
)


def engine_available() -> bool:
    """Whether a run can execute at all, so callers can say so up front."""
    return False


def extract_schedules(
    pages: list[DocumentPage], *, pdf_bytes: bytes | None, document_hash: str,
    schedule_name: str = "Schedule of Assessments",
) -> list[tuple[dict[str, Any] | None, ExtractionResult]]:
    """One ``(schedule option, result)`` pair per independent Schedule of
    Assessments the document prints; the option is None for a single schedule.
    """
    raise ExtractionNotConfigured(ENGINE_UNAVAILABLE_MESSAGE)


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
        service = ExtractionService(session)
        service.start(run_id)
        # Engines make blocking model calls; keep them off the event loop.
        results = await run_in_threadpool(
            extract_schedules, pages, pdf_bytes=pdf_bytes,
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
