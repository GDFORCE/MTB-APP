"""Integration tests for the Mongo-backed durable job queue
(app/services/remote_audit/job_queue.py) — Phase 1 exit criterion:
"a killed worker's in-flight job is recovered by the reconciliation sweep."

Runs against this repo's real dev MongoDB (same MONGO_URL/DB_NAME every other
backend test uses) — there is no in-memory Mongo substitute in this codebase.
Every job created here is scoped to a unique per-run job_type prefix and
cleaned up in a module-scoped fixture, so repeated runs never accumulate
junk in the shared dev database.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit.db import get_db  # noqa: E402
from app.services.remote_audit.job_queue import JobQueue  # noqa: E402

RUN_PREFIX = f"test-remote-audit-jobs-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def queue():
    db = get_db()
    q = JobQueue(db)
    await q.ensure_indexes()
    yield q
    await db["remote_audit_jobs"].delete_many({"job_type": {"$regex": f"^{RUN_PREFIX}"}})
    await db["remote_audit_jobs_dlq"].delete_many({"job_type": {"$regex": f"^{RUN_PREFIX}"}})
    db.client.close()


def job_type(name: str) -> str:
    return f"{RUN_PREFIX}.{name}"


@pytest.mark.asyncio
async def test_enqueue_then_claim_happy_path(queue: JobQueue):
    jt = job_type("happy")
    job_id = await queue.enqueue(jt, {"evidence_id": "ev-1"})
    claimed = await queue.claim("worker-1", job_types=[jt])
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.payload == {"evidence_id": "ev-1"}
    assert claimed.attempts == 1


@pytest.mark.asyncio
async def test_two_workers_can_never_claim_the_same_job(queue: JobQueue):
    jt = job_type("race")
    await queue.enqueue(jt, {"n": 1})
    first = await queue.claim("worker-a", job_types=[jt])
    second = await queue.claim("worker-b", job_types=[jt])
    assert first is not None
    assert second is None  # nothing left to claim -- the atomic claim worked


@pytest.mark.asyncio
async def test_complete_with_correct_lease_token_succeeds(queue: JobQueue):
    jt = job_type("complete")
    job_id = await queue.enqueue(jt, {})
    claimed = await queue.claim("worker-1", job_types=[jt])
    ok = await queue.complete(claimed.id, claimed.lease_token)
    assert ok is True
    stored = await queue.get(job_id)
    assert stored["status"] == "done"


@pytest.mark.asyncio
async def test_complete_with_wrong_lease_token_fails_closed(queue: JobQueue):
    jt = job_type("wrong-token")
    job_id = await queue.enqueue(jt, {})
    claimed = await queue.claim("worker-1", job_types=[jt])
    ok = await queue.complete(claimed.id, "not-the-real-token")
    assert ok is False
    stored = await queue.get(job_id)
    assert stored["status"] == "processing"  # untouched


@pytest.mark.asyncio
async def test_fail_below_max_attempts_requeues_with_backoff(queue: JobQueue):
    jt = job_type("requeue")
    job_id = await queue.enqueue(jt, {}, max_attempts=3)
    claimed = await queue.claim("worker-1", job_types=[jt])
    outcome = await queue.fail(claimed.id, claimed.lease_token, "transient error")
    assert outcome == "requeued"
    stored = await queue.get(job_id)
    assert stored["status"] == "queued"
    assert stored["next_available_at"] > datetime.now(timezone.utc)  # real backoff delay
    assert len(stored["failure_history"]) == 1


@pytest.mark.asyncio
async def test_fail_at_max_attempts_dead_letters_not_silently_drops(queue: JobQueue):
    jt = job_type("dlq")
    job_id = await queue.enqueue(jt, {}, max_attempts=1)
    claimed = await queue.claim("worker-1", job_types=[jt])
    outcome = await queue.fail(claimed.id, claimed.lease_token, "permanent error")
    assert outcome == "dead_lettered"
    assert await queue.get(job_id) is None  # gone from the live queue
    dlq_doc = await queue.get_dead_letter(job_id)
    assert dlq_doc is not None
    assert dlq_doc["failure_history"][-1]["error"] == "permanent error"


@pytest.mark.asyncio
async def test_reconciliation_recovers_a_job_whose_worker_died_mid_processing(queue: JobQueue):
    """The Phase 1 exit criterion, end to end: claim, simulate the worker
    dying (lease expires without complete/fail), reconcile, a second worker
    claims it, and the FIRST worker's stale lease token can no longer
    complete it (fencing token proof)."""
    jt = job_type("zombie")
    job_id = await queue.enqueue(jt, {}, max_attempts=5)

    first_claim = await queue.claim("worker-doomed", job_types=[jt])
    assert first_claim.attempts == 1

    # Simulate the worker dying: force its lease into the past directly,
    # exactly as if `lease_seconds` had elapsed with no complete()/fail() call.
    db = get_db()
    await db["remote_audit_jobs"].update_one(
        {"id": job_id},
        {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )

    recovered_ids = await queue.reconcile_stuck()
    assert job_id in recovered_ids

    # Reconciliation applies the same backoff as an app-level failure would
    # (defensive against a job type that reliably kills workers) -- fast-
    # forward past that delay the same way the lease expiry was simulated
    # above, rather than sleeping in the test.
    await db["remote_audit_jobs"].update_one(
        {"id": job_id},
        {"$set": {"next_available_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )

    second_claim = await queue.claim("worker-replacement", job_types=[jt])
    assert second_claim is not None
    assert second_claim.id == job_id
    assert second_claim.attempts == 2  # attempt count carried forward, not reset
    assert second_claim.lease_token != first_claim.lease_token

    # The zombie worker's stale token must never be able to complete the job
    # out from under the replacement worker's in-progress claim.
    stale_complete_ok = await queue.complete(job_id, first_claim.lease_token)
    assert stale_complete_ok is False

    real_complete_ok = await queue.complete(job_id, second_claim.lease_token)
    assert real_complete_ok is True


@pytest.mark.asyncio
async def test_reconciliation_dead_letters_a_zombie_job_past_max_attempts(queue: JobQueue):
    jt = job_type("zombie-exhausted")
    job_id = await queue.enqueue(jt, {}, max_attempts=1)
    await queue.claim("worker-doomed", job_types=[jt])
    db = get_db()
    await db["remote_audit_jobs"].update_one(
        {"id": job_id},
        {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    recovered_ids = await queue.reconcile_stuck()
    assert job_id in recovered_ids
    assert await queue.get(job_id) is None
    assert await queue.get_dead_letter(job_id) is not None
