"""Mongo-backed durable job queue for the remote-audit worker.

No message broker (Redis/RabbitMQ/SQS) exists anywhere in this codebase
today; see docs/remote-audit/worker-architecture.md for why this module
exists instead of introducing one, and the documented upgrade path once
volume justifies a real broker.

Correctness properties this module provides:
- Atomic claim: two workers can never claim the same job (`find_one_and_update`
  on a `status="queued"` filter is a single atomic Mongo operation).
- Fencing token: every claim gets a fresh `lease_token`; `complete`/`fail`
  require the caller to present the token they were given, so a "zombie"
  worker that wakes up after its lease was reclaimed by the reconciliation
  sweep can never corrupt the new claimant's result.
- Retry with backoff, then dead-letter: a job that keeps failing is requeued
  with an increasing delay up to `max_attempts`, then moved to the DLQ with
  its full failure history — never silently dropped.
- Reconciliation: `reconcile_stuck` finds jobs whose lease expired without
  completion (the worker that claimed them died or hung) and makes them
  claimable again.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

JOBS_COLLECTION = "remote_audit_jobs"
DLQ_COLLECTION = "remote_audit_jobs_dlq"


def _obs():
    """Imported lazily: observability.queue_health reads this module's
    collections, so a module-level import here would be circular."""
    from . import observability

    return observability

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff, capped at 5 minutes: 2, 4, 8, 16, ... seconds."""
    return min(300.0, 2.0 ** attempt)


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    job_type: str
    payload: Dict[str, Any]
    attempts: int
    lease_token: str
    correlation_id: Optional[str] = None


class JobQueue:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._jobs = db[JOBS_COLLECTION]
        self._dlq = db[DLQ_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._jobs.create_index("id", unique=True)
        await self._jobs.create_index([("status", 1), ("job_type", 1), ("next_available_at", 1)])
        await self._jobs.create_index([("status", 1), ("lease_expires_at", 1)])

    async def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        *,
        max_attempts: int = 5,
        correlation_id: Optional[str] = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        now = _now()
        await self._jobs.insert_one({
            "id": job_id,
            "job_type": job_type,
            "payload": payload,
            "status": STATUS_QUEUED,
            "attempts": 0,
            "max_attempts": max_attempts,
            "next_available_at": now,
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "lease_token": None,
            "correlation_id": correlation_id,
            "failure_history": [],
            "created_at": now,
            "updated_at": now,
        })
        _obs().emit_metric(_obs().Metric.JOB_ENQUEUED, tags={"job_type": job_type})
        return job_id

    async def claim(
        self,
        worker_id: str,
        *,
        job_types: Optional[Sequence[str]] = None,
        lease_seconds: int = 300,
    ) -> Optional[ClaimedJob]:
        now = _now()
        query: Dict[str, Any] = {
            "status": STATUS_QUEUED,
            "next_available_at": {"$lte": now},
        }
        if job_types:
            query["job_type"] = {"$in": list(job_types)}
        lease_token = str(uuid.uuid4())
        doc = await self._jobs.find_one_and_update(
            query,
            {
                "$set": {
                    "status": STATUS_PROCESSING,
                    "claimed_by": worker_id,
                    "claimed_at": now,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "lease_token": lease_token,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            return None
        return ClaimedJob(
            id=doc["id"], job_type=doc["job_type"], payload=doc["payload"],
            attempts=doc["attempts"], lease_token=lease_token,
            correlation_id=doc.get("correlation_id"),
        )

    async def complete(self, job_id: str, lease_token: str) -> bool:
        """Returns False (does nothing) if the lease token doesn't match —
        i.e. this job was reclaimed by someone else since this caller's claim."""
        result = await self._jobs.update_one(
            {"id": job_id, "status": STATUS_PROCESSING, "lease_token": lease_token},
            {"$set": {"status": STATUS_DONE, "updated_at": _now()}},
        )
        if result.modified_count == 1:
            _obs().emit_metric(_obs().Metric.JOB_COMPLETED)
        return result.modified_count == 1

    async def fail(self, job_id: str, lease_token: str, error: str) -> str:
        """Requeues with backoff, or dead-letters if max_attempts is
        exhausted. Returns "requeued" or "dead_lettered"; returns
        "stale_lease" without changing anything if the lease token doesn't
        match (this caller's claim was already reclaimed)."""
        doc = await self._jobs.find_one({"id": job_id, "lease_token": lease_token})
        if doc is None:
            return "stale_lease"
        now = _now()
        failure_entry = {"attempt": doc["attempts"], "error": error, "failed_at": now}
        # The error text is NOT a metric tag: a handler's exception message can
        # carry an object key, a path, or a parsed field value, and a tag is a
        # log line by another name.
        _obs().emit_metric(_obs().Metric.JOB_FAILED, tags={"job_type": doc["job_type"]})
        if doc["attempts"] >= doc["max_attempts"]:
            await self._dead_letter(doc, failure_entry)
            return "dead_lettered"
        _obs().emit_metric(_obs().Metric.JOB_RETRIED, tags={"job_type": doc["job_type"]})
        await self._jobs.update_one(
            {"id": job_id, "lease_token": lease_token},
            {
                "$set": {
                    "status": STATUS_QUEUED,
                    "next_available_at": now + timedelta(seconds=_backoff_seconds(doc["attempts"])),
                    "claimed_by": None,
                    "lease_expires_at": None,
                    "lease_token": None,
                    "updated_at": now,
                },
                "$push": {"failure_history": failure_entry},
            },
        )
        return "requeued"

    async def reconcile_stuck(self) -> List[str]:
        """Finds jobs whose lease expired without completion (the worker
        that claimed them died or hung) and makes them claimable again, or
        dead-letters them if attempts are exhausted. Returns the ids acted on."""
        now = _now()
        acted_on: List[str] = []
        cursor = self._jobs.find({"status": STATUS_PROCESSING, "lease_expires_at": {"$lt": now}})
        async for doc in cursor:
            failure_entry = {
                "attempt": doc["attempts"], "error": "lease expired: worker died or hung",
                "failed_at": now,
            }
            if doc["attempts"] >= doc["max_attempts"]:
                await self._dead_letter(doc, failure_entry)
            else:
                await self._jobs.update_one(
                    {"id": doc["id"], "status": STATUS_PROCESSING, "lease_expires_at": doc["lease_expires_at"]},
                    {
                        "$set": {
                            "status": STATUS_QUEUED,
                            "next_available_at": now + timedelta(seconds=_backoff_seconds(doc["attempts"])),
                            "claimed_by": None,
                            "lease_expires_at": None,
                            "lease_token": None,
                            "updated_at": now,
                        },
                        "$push": {"failure_history": failure_entry},
                    },
                )
            acted_on.append(doc["id"])
        return acted_on

    async def _dead_letter(self, doc: Dict[str, Any], failure_entry: Dict[str, Any]) -> None:
        history = [*doc.get("failure_history", []), failure_entry]
        await self._dlq.insert_one({**doc, "failure_history": history, "dead_lettered_at": _now()})
        await self._jobs.delete_one({"id": doc["id"]})
        # A dead-lettered job means evidence a site submitted was never
        # verified, and it will not retry itself. This one pages someone.
        _obs().emit_metric(_obs().Metric.JOB_DEAD_LETTERED, tags={"job_type": doc["job_type"]})

    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self._jobs.find_one({"id": job_id}, {"_id": 0})

    async def get_dead_letter(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self._dlq.find_one({"id": job_id}, {"_id": 0})
