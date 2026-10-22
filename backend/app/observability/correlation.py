"""One safe correlation id per request/command, propagated to logs, audit and outbox (P13-01)."""
from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

HEADER = "x-correlation-id"
_SAFE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_current: ContextVar[str | None] = ContextVar("uctsm_correlation_id", default=None)


def new_id() -> str:
    return uuid4().hex


def accept(candidate: str | None) -> str:
    """A caller-supplied id is kept only when it cannot carry markup, spaces or PHI-shaped text."""
    return candidate if candidate and _SAFE.match(candidate) else new_id()


def current() -> str | None:
    return _current.get()


def bind(correlation_id: str) -> Token:
    return _current.set(correlation_id)


def unbind(token: Token) -> None:
    _current.reset(token)


class CorrelationMiddleware:
    """Pure ASGI middleware (no body buffering): binds the id, echoes it, counts the request."""

    def __init__(self, app, *, metrics=None, legacy_routes: dict | None = None):
        self.app = app
        self._metrics = metrics
        #: {(METHOD, route template): classification} from the legacy consumer inventory (P11-01)
        self._legacy = legacy_routes or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        correlation_id = accept(headers.get(HEADER))
        token = bind(correlation_id)
        status = {"code": 500}

        async def traced_send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [item for item in message["headers"] if item[0].lower() != HEADER.encode()]
                message["headers"].append((HEADER.encode(), correlation_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, traced_send)
        finally:
            if self._metrics is not None:
                # The route TEMPLATE, never the raw path: ids in a URL must not become labels.
                route = scope.get("route")
                template = getattr(route, "path", None) or "unmatched"
                self._metrics.requests.inc(method=scope["method"], route=template,
                                           status=f"{status['code'] // 100}xx")
                classification = self._legacy.get((scope["method"], template))
                if classification:
                    self._metrics.legacy_consumers.inc(route=template, method=scope["method"],
                                                       classification=classification)
            unbind(token)


def stamp(metadata: dict | None) -> dict:
    """``metadata`` with the current correlation id added when one is bound and none is set."""
    result = dict(metadata or {})
    bound = current()
    if bound and "correlation_id" not in result:
        result["correlation_id"] = bound
    return result
