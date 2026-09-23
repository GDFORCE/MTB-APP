"""Metrics, structured logging and redaction — spec §20, Production-Grade
Implementation Plan v1.3 Phase 9.

This phase sits BEFORE Phase 10 on purpose: several Production-DoD items can
only be evaluated once monitoring is live to observe them.

THE PART THAT IS A SECURITY CONTROL, NOT A CONVENIENCE:

`redact()` is what stands between this module's logs and a second copy of the
data the whole system exists to protect. General logs must never contain raw
GPS coordinates, signed URLs, attestation tokens, document contents, secrets,
or unnecessary personal data — a log aggregator is a long-lived, widely-read,
weakly-access-controlled store, and evidence that is carefully gated in Mongo
is not gated at all once it has been printed.

So every value logged from this module goes through `redact()`, and Phase 9's
CI gate scans real log output from the integration suite for the forbidden
patterns. `assert_no_forbidden_patterns()` is that gate, importable so the
test can run it against captured output rather than reimplementing the rules.

Metrics are emitted through one `emit_metric()` seam rather than a vendor SDK.
No metrics backend exists in this codebase today; putting the call sites
behind one function means adopting StatsD/OpenTelemetry/CloudWatch later is a
driver change, not a sweep through every module.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

log = logging.getLogger("remote_audit.metrics")

METRICS_ENABLED_ENV = "REMOTE_AUDIT_METRICS_ENABLED"


class Metric(str, Enum):
    """Every metric spec §20 asks for. Named here rather than as inline
    strings so a typo is an AttributeError instead of a silently-missing
    dashboard series."""

    # Capture and evidence
    CAPTURE_SESSION_CREATED = "remote_audit.capture_session.created"
    CAPTURE_SESSION_FINALIZED = "remote_audit.capture_session.finalized"
    CAPTURE_SESSION_EXPIRED = "remote_audit.capture_session.expired"
    CAPTURE_SESSION_REPLAY_REJECTED = "remote_audit.capture_session.replay_rejected"
    LOCATION_ACCURACY_M = "remote_audit.location.accuracy_m"
    GEO_OUTSIDE_BOUNDARY = "remote_audit.geo.outside_boundary"
    ATTESTATION_UNAVAILABLE = "remote_audit.attestation.unavailable"
    ATTESTATION_FAILED = "remote_audit.attestation.failed"
    VERIFICATION_LATENCY_MS = "remote_audit.verification.latency_ms"
    VERIFICATION_OUTCOME = "remote_audit.verification.outcome"

    # Worker and queue
    JOB_ENQUEUED = "remote_audit.job.enqueued"
    JOB_COMPLETED = "remote_audit.job.completed"
    JOB_FAILED = "remote_audit.job.failed"
    JOB_RETRIED = "remote_audit.job.retried"
    JOB_DEAD_LETTERED = "remote_audit.job.dead_lettered"
    QUEUE_DEPTH = "remote_audit.queue.depth"
    QUEUE_OLDEST_AGE_SECONDS = "remote_audit.queue.oldest_age_seconds"

    # Files
    MALWARE_SCAN_UNAVAILABLE = "remote_audit.malware.scan_unavailable"
    MALWARE_DETECTED = "remote_audit.malware.detected"
    PARSER_FAILURE = "remote_audit.parser.failure"
    DOCUMENT_OUTCOME = "remote_audit.document.outcome"

    # Workflow
    QUERY_OPEN_AGE_DAYS = "remote_audit.query.open_age_days"
    CAPA_OVERDUE_COUNT = "remote_audit.capa.overdue_count"

    # Virtual visits
    VIRTUAL_SESSION_JOIN = "remote_audit.virtual_session.join"
    VIRTUAL_SESSION_JOIN_FAILED = "remote_audit.virtual_session.join_failed"
    VIRTUAL_SESSION_RECONNECT = "remote_audit.virtual_session.reconnect"
    VIRTUAL_SESSION_TURN_FAILURE = "remote_audit.virtual_session.turn_failure"

    # Decisions and authorization
    MANUAL_OVERRIDE = "remote_audit.decision.manual_override"
    AUTHORIZATION_DENIED = "remote_audit.authz.denied"
    CROSS_ORG_DENIED = "remote_audit.authz.cross_org_denied"


# ── Redaction ───────────────────────────────────────────────────────────────

#: Field names whose VALUES are never safe to log, at any nesting depth.
#: Matched case-insensitively on a substring, so `sha256_client`,
#: `play_integrity_token` and `client_secret` are all caught without listing
#: every variant.
FORBIDDEN_KEY_FRAGMENTS: Tuple[str, ...] = (
    "lat", "lon", "latitude", "longitude", "coordinate", "geometry",
    "token", "secret", "password", "authorization", "api_key", "apikey",
    "signed_url", "presigned", "signature", "credential",
    "email", "phone", "full_name", "address",
    "content", "bytes", "raw_response", "text", "body",
)

#: Keys that ARE safe despite matching a fragment above — opaque identifiers
#: and counts, not the data itself.
ALLOWED_KEY_EXACT: Tuple[str, ...] = (
    "accuracy_m", "location_age_ms", "geo_status", "attestation_tier",
    "content_type", "media_type", "body_size", "token_count",
)

REDACTED = "[redacted]"

#: Patterns the CI gate scans real log output for. Each one is something that
#: should never appear in a general log line no matter which module wrote it.
FORBIDDEN_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    # A bare coordinate pair, e.g. "28.6139, 77.2090" — precise enough to
    # place a person, which is exactly what must not sit in a log.
    ("raw_gps_coordinates",
     re.compile(r"-?\b\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}\b")),
    # AWS/S3 presigned URL signature parameters.
    ("presigned_url",
     re.compile(r"[?&](?:X-Amz-Signature|X-Amz-Credential|Signature|AWSAccessKeyId)=", re.I)),
    # A JWT: three base64url segments. Attestation tokens, step-up tokens and
    # LiveKit participant tokens all take this shape.
    ("jwt_token",
     re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    # An assignment of anything secret-shaped to a non-redacted value.
    ("inline_secret",
     re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|private[_-]?key)\b\s*[:=]\s*"
                r"(?!\[redacted\])[\"']?[A-Za-z0-9/+_\-]{8,}")),
    # Embedded file content.
    ("embedded_file_content", re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,")),
    ("pdf_content", re.compile(r"%PDF-\d")),
)


def _key_is_forbidden(key: str) -> bool:
    lowered = key.lower()
    if lowered in ALLOWED_KEY_EXACT:
        return False
    return any(fragment in lowered for fragment in FORBIDDEN_KEY_FRAGMENTS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return `value` with every sensitive field replaced by `[redacted]`.

    Recurses through dicts and lists. Depth is bounded because a log call must
    never be the thing that blows the stack on a deeply-nested payload —
    beyond the limit the whole subtree is redacted, which errs toward showing
    less rather than more.
    """
    if _depth > 6:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            key: (REDACTED if _key_is_forbidden(str(key)) else redact(val, _depth=_depth + 1))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    return value


def find_forbidden_patterns(text: str) -> List[Tuple[str, str]]:
    """Return `(rule_name, matched_text)` for everything forbidden in `text`."""
    found: List[Tuple[str, str]] = []
    for name, pattern in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text):
            found.append((name, match.group(0)))
    return found


def assert_no_forbidden_patterns(text: str) -> None:
    """The Phase 9 CI gate, as an importable assertion.

    The test that guards log redaction runs this against real captured output
    from the integration suite, so the rules live in one place rather than
    being restated (and drifting) in the test.
    """
    found = find_forbidden_patterns(text)
    if found:
        summary = "; ".join(f"{name}: {sample[:40]!r}" for name, sample in found[:5])
        raise AssertionError(
            f"log output contains {len(found)} forbidden pattern(s): {summary}")


# ── Emission ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MetricEvent:
    name: str
    value: float
    tags: Dict[str, Any]


def metrics_enabled() -> bool:
    return os.environ.get(METRICS_ENABLED_ENV, "true").strip().lower() not in ("0", "false", "no")


def emit_metric(
    metric: Metric, value: float = 1.0, *, tags: Optional[Dict[str, Any]] = None,
) -> MetricEvent:
    """Record one metric.

    Structured JSON to a named logger today, because no metrics backend exists
    in this codebase yet. Every call site goes through here so that adopting
    StatsD/OpenTelemetry/CloudWatch later is a change to this function, not a
    sweep through every module that measures something.

    Tags are redacted on the way out: a metric tagged with a raw coordinate or
    a token would put it in the log stream just as surely as a log line would.
    """
    safe_tags = redact(dict(tags or {}))
    event = MetricEvent(name=metric.value, value=float(value), tags=safe_tags)
    if metrics_enabled():
        log.info(json.dumps({"metric": event.name, "value": event.value, "tags": event.tags},
                            default=str, sort_keys=True))
    return event


def log_event(
    logger: logging.Logger, level: int, message: str, **fields: Any,
) -> None:
    """Structured logging with redaction applied to every field.

    Use this instead of `logger.info(f"... {payload}")` anywhere near evidence,
    documents, locations or tokens. The message itself is NOT redacted — it is
    a constant written by us; the interpolated values are what needs guarding,
    and keeping them in `fields` is what makes that possible.
    """
    logger.log(level, "%s %s", message,
               json.dumps(redact(fields), default=str, sort_keys=True))


# ── Alert thresholds (spec §20.2) ───────────────────────────────────────────

@dataclass(frozen=True)
class AlertRule:
    metric: Metric
    #: Fires when the measured value crosses this in the stated direction.
    threshold: float
    direction: str  # "above" | "below"
    window_minutes: int
    severity: str   # "page" | "ticket"
    rationale: str


#: What actually pages someone, and why. Written down here rather than living
#: only in a monitoring console so the reasoning is reviewable in code review
#: and versioned with the thing it monitors.
ALERT_RULES: Tuple[AlertRule, ...] = (
    AlertRule(
        Metric.MALWARE_SCAN_UNAVAILABLE, threshold=1, direction="above", window_minutes=15,
        severity="page",
        rationale="Evidence and documents stay invisible while the scanner is down. The system "
                  "is behaving correctly and the audit is stalled — someone has to know."),
    AlertRule(
        Metric.JOB_DEAD_LETTERED, threshold=1, direction="above", window_minutes=60,
        severity="page",
        rationale="A dead-lettered job means evidence a site submitted was never verified. It "
                  "will not retry itself."),
    AlertRule(
        Metric.QUEUE_OLDEST_AGE_SECONDS, threshold=900, direction="above", window_minutes=5,
        severity="page",
        rationale="A backlog older than 15 minutes means sites are waiting on verdicts and the "
                  "worker is not keeping up."),
    AlertRule(
        Metric.MALWARE_DETECTED, threshold=1, direction="above", window_minutes=5,
        severity="page",
        rationale="Someone uploaded malware to a clinical system. Security needs to see this "
                  "immediately, not in a weekly report."),
    AlertRule(
        Metric.CROSS_ORG_DENIED, threshold=5, direction="above", window_minutes=10,
        severity="page",
        rationale="Repeated cross-organization denials look like enumeration, not fat fingers."),
    AlertRule(
        Metric.ATTESTATION_UNAVAILABLE, threshold=50, direction="above", window_minutes=60,
        severity="ticket",
        rationale="Expected until Phase 8's native bridges ship. Worth a ticket so it is noticed "
                  "when it changes, not a page that everyone learns to ignore."),
    AlertRule(
        Metric.GEO_OUTSIDE_BOUNDARY, threshold=20, direction="above", window_minutes=60,
        severity="ticket",
        rationale="A spike usually means a geofence is wrong, not that a site moved. Worth "
                  "investigating before it generates findings against innocent sites."),
    AlertRule(
        Metric.MANUAL_OVERRIDE, threshold=10, direction="above", window_minutes=1440,
        severity="ticket",
        rationale="A high override rate means the engine disagrees with reviewers systematically. "
                  "That is a signal about the rules, not about the reviewers."),
    AlertRule(
        Metric.VIRTUAL_SESSION_JOIN_FAILED, threshold=10, direction="above", window_minutes=30,
        severity="ticket",
        rationale="Join failures concentrated in time usually mean a TURN/provider problem "
                  "rather than many individually broken networks."),
)


def rules_for(metric: Metric) -> List[AlertRule]:
    return [rule for rule in ALERT_RULES if rule.metric == metric]


def evaluate_alert(rule: AlertRule, measured: float) -> bool:
    return measured > rule.threshold if rule.direction == "above" else measured < rule.threshold


def paging_rules() -> List[AlertRule]:
    """The rules that wake a human. Kept small on purpose — an alert everyone
    has learned to ignore is worse than no alert."""
    return [rule for rule in ALERT_RULES if rule.severity == "page"]


async def queue_health(db: Any) -> Dict[str, Any]:
    """Depth and oldest-age for the job queue, ready to emit.

    Reported rather than computed on read elsewhere so the number a dashboard
    shows and the number an alert fires on are the same number.
    """
    from datetime import datetime, timezone

    from .job_queue import JOBS_COLLECTION

    pending = await db[JOBS_COLLECTION].count_documents({"status": {"$in": ["pending", "claimed"]}})
    oldest = await db[JOBS_COLLECTION].find_one(
        {"status": {"$in": ["pending", "claimed"]}}, {"_id": 0, "created_at": 1},
        sort=[("created_at", 1)])
    age_seconds = 0.0
    if oldest and oldest.get("created_at"):
        created = oldest["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (datetime.now(timezone.utc) - created).total_seconds())

    emit_metric(Metric.QUEUE_DEPTH, pending)
    emit_metric(Metric.QUEUE_OLDEST_AGE_SECONDS, age_seconds)
    return {"depth": pending, "oldest_age_seconds": age_seconds}
