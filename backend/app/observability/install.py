"""Wire correlation, redacted logging, health checks and the (token-gated) metrics scrape."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Header, HTTPException, Response

from . import logs
from .correlation import CorrelationMiddleware
from .metrics import METRICS


def build_router(readiness: Callable[[], dict[str, bool]] | None = None) -> APIRouter:
    router = APIRouter()

    @router.get("/health/live", include_in_schema=False)
    async def live():
        return {"status": "live"}

    @router.get("/health/ready", include_in_schema=False)
    async def ready(response: Response):
        checks = readiness() if readiness else {}
        ok = all(checks.values())
        response.status_code = 200 if ok else 503
        return {"status": "ready" if ok else "degraded", "checks": checks,
                "versions": logs.version_metadata()}

    @router.get("/metrics", include_in_schema=False)
    async def metrics(authorization: str | None = Header(default=None)):
        expected = os.getenv("UCTSM_METRICS_TOKEN", "")
        if not expected:
            raise HTTPException(status_code=404)              # disabled unless a token is configured
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(status_code=401)
        return Response(METRICS.render(), media_type="text/plain; version=0.0.4")

    return router


def legacy_routes() -> dict[tuple[str, str], str]:
    """MIGRATE/ADAPT routes of the checked-in inventory, for deprecation telemetry."""
    path = Path(__file__).resolve().parents[3] / "docs" / "legacy-consumer-inventory.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["routes"]
    except (OSError, ValueError, KeyError):
        return {}
    return {(row["method"], row["path"]): row["classification"] for row in rows
            if row["classification"] in {"MIGRATE", "ADAPT", "RETIRE"}}


def install(app, *, readiness: Callable[[], dict[str, bool]] | None = None) -> None:
    logs.configure(json_logs=os.getenv("UCTSM_JSON_LOGS", "").strip().lower() in {"1", "true", "yes"})
    app.add_middleware(CorrelationMiddleware, metrics=METRICS, legacy_routes=legacy_routes())
    app.include_router(build_router(readiness))
