"""One pass of the plane's background duties (P10-04, P13-02/03): queue gauges, reminder sync, outbox delivery.

Meant to be called from a worker loop or a scheduler; it holds no state and is safe to run concurrently with
itself (every step is idempotent). A failure for one patient is counted and logged by code, never raised, so one
bad record cannot stall the rest.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Callable

from app.observability.metrics import METRICS

from .outbox_delivery import OutboxDelivery, publish_queue_gauges
from .ports import UnitOfWork
from .records import OutboxRecord
from .reminder_sync import ReminderSyncService

CONSUMER = "reminders"


def run_cycle(uow: UnitOfWork, *, now: datetime, today: date, batch: int = 200,
              deliver: Callable[[OutboxRecord], None] | None = None, max_deliveries: int = 100) -> dict[str, int]:
    summary = {"patients": 0, "created": 0, "withdrawn": 0, "failed": 0, "delivered": 0}
    service, after = ReminderSyncService(uow), None
    while True:
        page = uow.read(lambda tx: tx.reminders.list_granted(after=after, limit=batch))
        if not page:
            break
        for prefs in page:
            after = prefs.patient_id
            summary["patients"] += 1
            try:
                outcome = service.sync(prefs.organization_id, prefs.patient_id, now=now, today=today)
                summary["created"] += len(outcome.created)
                summary["withdrawn"] += len(outcome.withdrawn)
            except Exception as error:                      # noqa: BLE001 - counted by code, never the message
                summary["failed"] += 1
                METRICS.job_failures.inc(job="reminder_sync", code=type(error).__name__[:40])
        if len(page) < batch:
            break
    if deliver is not None:
        delivery = OutboxDelivery(uow, clock=lambda: now)
        for _ in range(max_deliveries):
            result = delivery.run_once(deliver, consumer=CONSUMER)
            if result["status"] == "IDLE":
                break
            summary["delivered"] += result["status"] in {"SENT", "DUPLICATE_SUPPRESSED"}
    publish_queue_gauges(uow)
    return summary
