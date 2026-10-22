"""Refuse a LEGACY schedule write for a patient whose schedule now lives in UCTSM (P11-04).

Used by the legacy handlers in ``server.py`` that change a patient's schedule. It consults the pilot
cohort state in the UCTSM Mongo plane. When the plane is not configured there is no cutover and the
guard is a no-op; when it IS configured but cannot be reached, the write is refused (fail closed)
rather than risking two writable authorities. Reads are never guarded.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.application.cohort_cutover import CohortCutoverService
from app.observability.metrics import METRICS

log = logging.getLogger(__name__)


def _default_uow():
    from app.api.uctsm_mongo import get_plane_uow
    return get_plane_uow()


async def refuse_if_migrated(external_patient_id: str | None, *, route: str,
                             uow_factory: Callable[[], object] | None = None) -> None:
    if not external_patient_id:
        return
    if uow_factory is None:
        if not os.getenv("UCTSM_MONGO_URL", "").strip():
            return
        uow_factory = _default_uow
    try:
        block = await run_in_threadpool(
            lambda: CohortCutoverService(uow_factory()).legacy_write_block(external_patient_id))
    except Exception:                                    # noqa: BLE001 - fail closed, never echo the cause
        log.warning("legacy schedule write refused: schedule authority could not be verified",
                    extra={"route": route})
        METRICS.legacy_blocked.inc(route=route)
        raise HTTPException(503, "Schedule authority could not be verified, so this change was not saved. "
                                 "Try again shortly.") from None
    if block is not None:
        METRICS.legacy_blocked.inc(route=route)
        raise HTTPException(409, detail=block)
