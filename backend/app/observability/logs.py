"""Structured, correlated, redacted logging (P13-01)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import correlation
from .redaction import redact, redact_text

_RESERVED = set(logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__) | {"message", "asctime"}
_versions: dict[str, str] = {}


def version_metadata() -> dict[str, str]:
    """Policy / evaluator / schema versions, resolved once (None of them is a secret)."""
    if not _versions:
        from app.authorization.catalog import POLICY_VERSION
        from app.domain.schedule.evaluator import ScheduleEvaluator
        from app.persistence.mongodb.migrate import discover
        _versions.update({"policy": POLICY_VERSION, "evaluator": str(ScheduleEvaluator.version),
                          "schema": str(max(module.VERSION for module in discover()))})
    return dict(_versions)


class CorrelationFilter(logging.Filter):
    """Stamps every record with the correlation id and scrubs the message and its arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation.current()
        if not getattr(record, "_redacted", False):
            record.msg = redact_text(str(record.getMessage()))
            record.args = ()
            for key in [k for k in record.__dict__ if k not in _RESERVED and k != "correlation_id"]:
                setattr(record, key, redact(record.__dict__[key]))
            record._redacted = True
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname, "logger": record.name, "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None) or correlation.current(),
            "versions": version_metadata(),
        }
        extras = {k: v for k, v in record.__dict__.items()
                  if k not in _RESERVED and k not in {"correlation_id", "_redacted"}}
        if extras:
            body["fields"] = redact(extras)
        if record.exc_info:
            # Type and location only: the exception text may embed a payload or a connection URL.
            error_type = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            body["error"] = {"type": error_type}
        return json.dumps(body, ensure_ascii=False, default=str)


def configure(*, json_logs: bool = False, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(isinstance(item, CorrelationFilter) for item in root.filters):
        root.addFilter(CorrelationFilter())
    for handler in root.handlers:
        if not any(isinstance(item, CorrelationFilter) for item in handler.filters):
            handler.addFilter(CorrelationFilter())
        if json_logs:
            handler.setFormatter(JsonFormatter())
    root.setLevel(level)
