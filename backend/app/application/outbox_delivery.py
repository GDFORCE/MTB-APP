"""Fenced at-least-once delivery with stable recipient idempotency keys (P13-03).

External handlers run OUTSIDE the business transaction and must honor dedupe_key.
A lost HTTP response or process crash can cause a redelivery; the provider's own
idempotency support is required for one external effect. No network call occurs here.
"""
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from uuid import UUID

from ..authorization.catalog import Capability
from ..authorization.policy import Principal, ResourceContext, authorize
from ..observability.metrics import METRICS
from .errors import DuplicateRecordError, PersistenceError
from .ports import UnitOfWork
from .records import AuditRecord, InboxRecord, OutboxRecord


def publish_queue_gauges(uow: UnitOfWork) -> dict[str, int]:
    """Refresh the ``uctsm_queue_depth`` gauges from the store (call from the worker loop or a scrape hook)."""
    counts = uow.read(lambda tx: tx.outbox.count_by_status())
    for state in ("PENDING", "PROCESSING", "DEAD"):
        METRICS.queue_depth.set(counts.get(state, 0), queue="outbox", state=state.lower())
    return counts


class PermanentDeliveryError(Exception):
    """An invalid/unpermitted intent goes directly to the dead-letter state."""


class OutboxDelivery:
    def __init__(self, uow: UnitOfWork, *, clock: Callable[[], datetime] | None = None):
        self.uow = uow
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self, handler: Callable[[OutboxRecord], None], *, lease_seconds: int = 60,
                 consumer: str | None = None) -> dict:
        """``consumer`` names the inbox: a message it already processed (for example one replayed
        from the dead-letter state after a late success) is settled SENT WITHOUT calling the handler."""
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        token = uuid4().hex
        row = self.uow.run(lambda tx: tx.outbox.claim(
            now=self.clock(), lease_seconds=lease_seconds, token=token))
        if row is None:
            METRICS.outbox.inc(result="idle")
            return {"status": "IDLE"}
        error_code = None; available = None
        if consumer is not None and self.uow.read(lambda tx: tx.inbox.find(consumer, row.dedupe_key)):
            self.uow.run(lambda tx: tx.outbox.settle(
                row.organization_id, row.id, token, now=self.clock(), status="SENT"))
            METRICS.outbox.inc(result="duplicate_suppressed")
            return {"id": str(row.id), "status": "DUPLICATE_SUPPRESSED", "attempt": row.attempts,
                    "error_code": None}
        try:
            handler(row)
            status = "SENT"
        except PermanentDeliveryError:
            status = "DEAD"; error_code = "PERMANENT_DELIVERY_FAILURE"
        except Exception:  # No raw exception text or patient payload in diagnostics.
            status = "DEAD" if row.attempts >= row.max_attempts else "PENDING"
            error_code = "RETRYABLE_DELIVERY_FAILURE"
            available = self.clock() + timedelta(seconds=min(300, 2 ** min(row.attempts, 9)))
        def settle(tx):
            if status == "SENT" and consumer is not None:
                try:
                    tx.inbox.add(InboxRecord(
                        organization_id=row.organization_id, consumer=consumer,
                        dedupe_key=row.dedupe_key, outbox_id=row.id, processed_at=self.clock()))
                except DuplicateRecordError:
                    pass          # a concurrent worker recorded the same result first
            tx.outbox.settle(row.organization_id, row.id, token, now=self.clock(), status=status,
                             available_at=available, error_code=error_code)
        self.uow.run(settle)
        METRICS.outbox.inc(result=status.lower())
        return {"id": str(row.id), "status": status, "attempt": row.attempts,
                "error_code": error_code}


class ReplayDeniedError(PersistenceError):
    """The caller does not hold ``outbox.replay`` for this organization."""


class DeadLetterService:
    """Authorized, audited re-queue of dead-lettered messages (P13-03).

    Replay never edits the message: it only gives a DEAD message a fresh attempt budget. The
    consumer inbox (see ``OutboxDelivery.run_once(consumer=...)``) is what keeps a replay of an
    already-processed message from producing a second business result.
    """

    def __init__(self, uow: UnitOfWork, *, clock: Callable[[], datetime] | None = None):
        self.uow = uow
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _require(principal: Principal, organization_id: UUID) -> UUID:
        decision = authorize(principal, Capability.OUTBOX_REPLAY, ResourceContext())
        if decision.denied or principal.organization_id != str(organization_id):
            raise ReplayDeniedError("the caller may not replay messages of this organization")
        return UUID(principal.user_id)

    def list_dead(self, principal: Principal, organization_id: UUID, *, limit: int = 100):
        self._require(principal, organization_id)
        return [{"id": str(row.id), "event_type": row.event_type, "aggregate_type": row.aggregate_type,
                 "attempts": row.attempts, "last_error_code": row.last_error_code,
                 "created_at": row.created_at.isoformat()}
                for row in self.uow.read(lambda tx: tx.outbox.list_dead(organization_id, limit=limit))]

    def replay(self, principal: Principal, organization_id: UUID, outbox_id: UUID, *, reason: str) -> dict:
        actor = self._require(principal, organization_id)
        if not reason or not reason.strip():
            raise ValueError("a replay reason is required")

        def work(tx):
            before = tx.outbox.get(organization_id, outbox_id)
            row = tx.outbox.requeue_dead(organization_id, outbox_id, now=self.clock())
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor, action="OUTBOX_DEAD_LETTER_REPLAYED",
                entity_type="OUTBOX_MESSAGE", entity_id=outbox_id, reason=reason,
                before={"status": before.status, "attempts": before.attempts,
                        "last_error_code": before.last_error_code},
                after={"status": row.status, "dedupe_key": row.dedupe_key}))
            return {"id": str(row.id), "status": row.status, "dedupe_key": row.dedupe_key}
        return self.uow.run(work)
