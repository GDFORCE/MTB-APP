"""The deferred-adoption worker (P6-05): applies a decision at its approved point.

A patient moves "when C1D1:0 is performed", "at the start of cycle 3" or "on a date" only
because someone with the right decided so, after a preview, and this worker is what makes the
stated point actually bring it about. It is deliberately small:

* it changes nothing by itself - every attempt goes through ``AdoptionService.execute``, which
  re-checks the versions, hashes, mapping, authority plane and approved impact, takes a FENCED
  lease, and applies in one transaction;
* it re-checks, NOW, that the person who decided still holds ``amendment.adopt`` for this
  patient (a revoked grant, a suspended account or a lost assignment settles the decision as
  BLOCKED instead of applying it);
* it exists only where the MongoDB patient plane is configured, and it never invents a date:
  a decision whose point has not come is left waiting.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Awaitable, Callable

from app.application.amendment_adoption import AdoptionService
from app.application.errors import AmendmentBlockedError

#: ``(decision, patient) -> does the person who decided STILL hold the right, now?``
DeciderCheck = Callable[[Any, Any], Awaitable[bool]]

log = logging.getLogger("uctsm.amendment_worker")


async def process_due_once(
    uow: Any, still_authorised: DeciderCheck, *, as_of: date | None = None, limit: int = 100,
) -> list[dict[str, Any]]:
    """One pass over every open deferred decision. Returns one outcome row per decision."""
    service = AdoptionService(uow)
    outcomes: list[dict[str, Any]] = []
    for decision in await asyncio.to_thread(service.pending_execution, limit=limit):
        try:
            patient = await asyncio.to_thread(
                uow.read, lambda tx, d=decision: tx.patients.get(d.organization_id, d.patient_id))
            if not await still_authorised(decision, patient):
                row = await asyncio.to_thread(
                    service.refuse_deferred, organization_id=decision.organization_id,
                    decision_id=decision.id, code="DECIDER_NOT_AUTHORISED",
                    detail="the person who made this decision no longer holds the right to "
                           "adopt an amendment for this patient")
                outcomes.append({"id": str(decision.id), **row})
                continue
            row = await asyncio.to_thread(
                lambda d=decision: service.execute(
                    organization_id=d.organization_id, actor_id=d.actor_id, decision_id=d.id,
                    as_of=as_of, capabilities=("amendment.adopt",)))
            outcomes.append({"id": str(decision.id), **row})
        except AmendmentBlockedError as error:
            outcomes.append({"id": str(decision.id), "status": "REFUSED",
                             "blockers": error.blockers})
        except Exception:                                    # noqa: BLE001 - one decision never stops the pass
            log.exception("amendment worker: decision %s failed", decision.id)
            outcomes.append({"id": str(decision.id), "status": "ERROR"})
            try:        # a failing decision goes to the back of the rotation, not the front
                await asyncio.to_thread(
                    service.mark_checked, organization_id=decision.organization_id,
                    decision_id=decision.id)
            except Exception:                                # noqa: BLE001
                log.exception("amendment worker: could not rotate decision %s", decision.id)
    return outcomes


async def amendment_worker_loop(
    uow_provider: Callable[[], Any], still_authorised: DeciderCheck, *, interval_seconds: int,
) -> None:
    """Long-running startup task. ``interval_seconds <= 0`` disables it."""
    if interval_seconds <= 0:
        return
    while True:
        try:
            await process_due_once(uow_provider(), still_authorised)
        except Exception:                                    # noqa: BLE001
            log.exception("amendment worker pass failed")
        await asyncio.sleep(interval_seconds)
