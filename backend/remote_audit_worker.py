"""Remote-audit worker process — the "isolated worker" side of the
API -> durable queue -> isolated worker -> structured result architecture
(docs/remote-audit/worker-architecture.md, Production-Grade Implementation
Plan v1.3 Phase 1).

Deliberately a SEPARATE OS process from the API (`server.py`/uvicorn) so that
CPU-heavy or untrusted parsing (added in Phase 2's evidence pipeline and
Phase 4's document pipeline) never runs inside the request/response path —
this file is what you run with `python remote_audit_worker.py`, not
something imported by the API app.

Job handlers are registered in `HANDLERS` below: "retention.sweep" (Phase 1),
"evidence.verify" (Phase 2), and "document.process" /
"document.expiry_sweep" (Phase 4) — see docs/remote-audit/worker-architecture.md's
dev-vs-production sandboxing note: this loop is the DEVELOPMENT baseline
(a plain subprocess/timeout wrapper around each job); production deployment
additionally runs this process inside the hardened container described
there (non-root, read-only rootfs, resource limits, no default egress).
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any, Awaitable, Callable, Dict

from dotenv import load_dotenv

load_dotenv(".env")

from app.services.remote_audit.db import get_db  # noqa: E402
from app.services.remote_audit.document_pipeline import (  # noqa: E402
    run_document_processing,
)
from app.services.remote_audit.documents import expire_due_documents  # noqa: E402
from app.services.remote_audit.job_queue import ClaimedJob, JobQueue  # noqa: E402
from app.services.remote_audit.queries import overdue_capas  # noqa: E402
from app.services.remote_audit.retention import sweep  # noqa: E402
from app.services.remote_audit.verify_pipeline import (  # noqa: E402
    ScannerUnavailableError,
    run_evidence_verification,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s remote_audit_worker: %(message)s")
log = logging.getLogger("remote_audit_worker")

POLL_INTERVAL_SECONDS = 5.0
RECONCILE_INTERVAL_SECONDS = 60.0
JOB_TIMEOUT_SECONDS = 120.0  # dev-mode hard timeout per job -- see module docstring

# Populated as Phase 2/4 land real retainable collections; empty for now, so
# a retention.sweep job is a correct, harmless no-op until there's anything
# to retain (Phase 1 exit criterion: the mechanism is proven, not that there
# is real data yet).
RETAINABLE_COLLECTIONS: Dict[str, Any] = {}


async def _handle_retention_sweep(_payload: Dict[str, Any]) -> None:
    db = get_db()
    for collection_name, (adapt, on_decision) in RETAINABLE_COLLECTIONS.items():
        decisions = await sweep(db, collection_name, adapt=adapt, on_decision=on_decision)
        log.info("retention sweep: %s -> %d item(s) evaluated", collection_name, len(decisions))


async def _handle_evidence_verify(payload: Dict[str, Any]) -> None:
    """Malware-scanner-unavailable is deliberately let through as an
    exception (ScannerUnavailableError) rather than swallowed here -- the
    job queue's normal fail()/retry/backoff mechanism is exactly the
    reconciliation loop spec §16.5 wants for 'attestation/scanner service
    temporarily unavailable' (v1.3 correction #15: the evidence stays
    QUARANTINED/PROCESSING_BLOCKED, never silently treated as clean)."""
    db = get_db()
    await run_evidence_verification(db, payload["evidence_id"])


async def _handle_document_process(payload: Dict[str, Any]) -> None:
    """Same deliberate non-handling of a missing scanner as evidence.verify:
    `ScannerUnavailableError` propagates so the queue retries with backoff
    while the document stays PROCESSING_BLOCKED and invisible to reviewers
    (v1.3 correction #15). Catching it here would be the one bug that turns
    "nobody scanned this" into "this looked fine"."""
    db = get_db()
    await run_document_processing(db, payload["document_id"])


async def _handle_document_expiry_sweep(payload: Dict[str, Any]) -> None:
    """Move ACCEPTED documents past their CONFIRMED expiry date to EXPIRED.

    Periodic rather than computed on read, so that an expired certificate is
    an expired certificate for everyone at once — including any scoring run
    that happens to look at it — instead of depending on who loaded the page.
    """
    db = get_db()
    expired = await expire_due_documents(db, audit_id=payload.get("audit_id"))
    if expired:
        log.info("document expiry sweep: %d document(s) expired: %s", len(expired), expired)


async def _handle_capa_overdue_sweep(payload: Dict[str, Any]) -> None:
    """Report CAPAs past their due date (spec §11.2).

    Deliberately reports rather than mutates: "overdue" is derived from the
    due date every time it is asked for (queries.is_overdue), so this sweep
    exists to drive reminders, not to stamp a flag that would be wrong the
    moment the clock moved and nobody had run it. Same shape as
    app/services/reminder_delivery.py's periodic projection.
    """
    db = get_db()
    overdue = await overdue_capas(db, audit_id=payload.get("audit_id"))
    if overdue:
        log.warning("CAPA overdue sweep: %d overdue CAPA(s): %s",
                    len(overdue), [c["id"] for c in overdue])


HANDLERS: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {
    "retention.sweep": _handle_retention_sweep,
    "evidence.verify": _handle_evidence_verify,
    "document.process": _handle_document_process,
    "document.expiry_sweep": _handle_document_expiry_sweep,
    "capa.overdue_sweep": _handle_capa_overdue_sweep,
}


async def _run_job(queue: JobQueue, worker_id: str, job: ClaimedJob) -> None:
    handler = HANDLERS.get(job.job_type)
    if handler is None:
        await queue.fail(job.id, job.lease_token, f"no handler registered for job_type={job.job_type!r}")
        return
    log.info("worker %s running job %s (%s), attempt %d", worker_id, job.id, job.job_type, job.attempts)
    try:
        await asyncio.wait_for(handler(job.payload), timeout=JOB_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await queue.fail(job.id, job.lease_token, f"job exceeded {JOB_TIMEOUT_SECONDS}s timeout")
        return
    except Exception as exc:  # noqa: BLE001 -- a job handler's failure must never crash the worker loop
        log.exception("worker %s: job %s (%s) failed", worker_id, job.id, job.job_type)
        await queue.fail(job.id, job.lease_token, f"{type(exc).__name__}: {exc}")
        return
    await queue.complete(job.id, job.lease_token)


async def run(worker_id: str = "remote-audit-worker-1") -> None:
    db = get_db()
    queue = JobQueue(db)
    await queue.ensure_indexes()
    log.info("remote-audit worker %s starting; registered job types: %s", worker_id, list(HANDLERS))

    stop = asyncio.Event()

    def _request_stop() -> None:
        log.info("shutdown requested; finishing in-flight work")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass  # signal handlers aren't available on some platforms (e.g. Windows + certain loops)

    last_reconcile = 0.0
    while not stop.is_set():
        now = loop.time()
        if now - last_reconcile >= RECONCILE_INTERVAL_SECONDS:
            recovered = await queue.reconcile_stuck()
            if recovered:
                log.warning("reconciliation recovered %d stuck job(s): %s", len(recovered), recovered)
            last_reconcile = now

        job = await queue.claim(worker_id, job_types=list(HANDLERS))
        if job is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        await _run_job(queue, worker_id, job)

    log.info("remote-audit worker %s stopped", worker_id)


if __name__ == "__main__":
    asyncio.run(run())
