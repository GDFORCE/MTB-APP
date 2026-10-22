"""P13-01: safe end-to-end correlation, redaction and metrics."""
import json
import logging
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.records import AuditRecord, OutboxRecord
from app.observability import correlation, logs
from app.observability.install import build_router, install
from app.observability.metrics import METRICS, Registry
from app.observability.redaction import REDACTED, redact, redact_text

pytest_plugins = ["tests.contract.conftest"]


def test_redaction_removes_secrets_and_identifiers_by_key_and_by_shape():
    clean = redact({"patient_name": "Jane Roe", "note": "free text", "payload": {"a": 1},
                    "nested": {"Authorization": "Bearer abcdef123456", "ok": "visit V2"},
                    "msg": "contact jane.roe@example.org or +44 7700 900123 via mongodb://u:p@host/db"})
    text = json.dumps(clean)
    for leaked in ("Jane Roe", "free text", "abcdef123456", "jane.roe@example.org", "900123", "u:p@host"):
        assert leaked not in text
    assert clean["nested"]["ok"] == "visit V2" and clean["patient_name"] == REDACTED
    assert redact_text("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk") == REDACTED
    deep = current = {}
    for _ in range(20): current["x"] = {}; current = current["x"]
    assert REDACTED in json.dumps(redact(deep))


def test_caller_supplied_correlation_ids_are_kept_only_when_safe():
    assert correlation.accept("req-12345678") == "req-12345678"
    for hostile in (None, "", "short", "has space in it", "<script>alert(1)</script>", "x" * 65, "jane.roe@example.org"):
        generated = correlation.accept(hostile)
        assert generated != hostile and len(generated) == 32


def make_app():
    app = FastAPI()

    @app.get("/patients/{patient_id}")
    def patient(patient_id: str):
        return {"correlation": correlation.current()}

    install(app, readiness=lambda: {"mongo": True})
    return app


def test_middleware_binds_echoes_and_counts_without_leaking_path_ids():
    client = TestClient(make_app())
    first = client.get("/patients/PATIENT-SECRET-77", headers={"X-Correlation-ID": "trace-abcdef12"})
    assert first.headers["x-correlation-id"] == "trace-abcdef12"
    assert first.json()["correlation"] == "trace-abcdef12"
    second = client.get("/patients/other", headers={"X-Correlation-ID": "bad id with spaces"})
    assert second.headers["x-correlation-id"] != "bad id with spaces" and len(second.headers["x-correlation-id"]) == 32
    rendered = METRICS.render()
    assert 'route="/patients/{patient_id}"' in rendered
    assert "PATIENT-SECRET-77" not in rendered
    assert correlation.current() is None                       # nothing leaks between requests


def test_health_ready_degrades_and_metrics_are_disabled_or_token_gated(monkeypatch):
    app = FastAPI(); state = {"ok": True}
    app.include_router(build_router(lambda: {"mongo": state["ok"]}))
    client = TestClient(app)
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 200 and set(ready.json()["versions"]) == {"policy", "evaluator", "schema"}
    state["ok"] = False
    assert client.get("/health/ready").status_code == 503
    monkeypatch.delenv("UCTSM_METRICS_TOKEN", raising=False)
    assert client.get("/metrics").status_code == 404
    monkeypatch.setenv("UCTSM_METRICS_TOKEN", "s3cret-token-value")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret-token-value"}).status_code == 200


def test_metrics_labels_are_closed_bounded_and_clean():
    registry = Registry()
    with pytest.raises(ValueError):
        registry.outbox.inc(result="x", patient="p1")           # unknown label rejected
    for i in range(700):
        registry.outbox.inc(result=f"r{i}")
    assert len(registry.outbox._series) <= 501                  # cardinality is capped
    fresh = Registry()
    fresh.outbox.inc(result='bad"label\nvalue')
    assert 'result="bad_label_value"' in fresh.outbox.render()[0]
    with pytest.raises(RuntimeError):
        with registry.timed("stage"):
            raise RuntimeError("boom")
    assert registry.evaluations.count(stage="stage", outcome="error") == 1


def test_json_logs_carry_correlation_and_versions_and_never_the_payload_or_error_text(capfd):
    logger = logging.getLogger("uctsm.test.observability")
    handler = logging.StreamHandler()
    handler.setFormatter(logs.JsonFormatter()); handler.addFilter(logs.CorrelationFilter())
    logger.addHandler(handler); logger.setLevel(logging.INFO); logger.propagate = False
    token = correlation.bind("trace-logtest01")
    try:
        try:
            raise ValueError("connection string mongodb://u:pw@host contains PHI Jane Roe")
        except ValueError:
            logger.error("failed for jane.roe@example.org", extra={"patient_name": "Jane Roe", "stage": "evaluate"},
                         exc_info=True)
    finally:
        correlation.unbind(token); logger.removeHandler(handler)
    line = json.loads(capfd.readouterr().err.strip().splitlines()[-1])
    assert line["correlation_id"] == "trace-logtest01" and set(line["versions"]) == {"policy", "evaluator", "schema"}
    assert line["fields"] == {"patient_name": REDACTED, "stage": "evaluate"}
    assert line["error"] == {"type": "ValueError"}
    assert "Jane Roe" not in json.dumps(line) and "pw@host" not in json.dumps(line)
    assert "example.org" not in json.dumps(line)


def test_correlation_id_reaches_audit_and_outbox_records_on_both_stores(uow):
    org = uuid4()
    token = correlation.bind("trace-persist01")
    try:
        entity = uuid4()
        uow.run(lambda tx: tx.audit.append(AuditRecord(
            organization_id=org, action="X", entity_type="T", entity_id=entity)))
        uow.run(lambda tx: tx.outbox.add(OutboxRecord(
            organization_id=org, aggregate_type="T", aggregate_id=entity, event_type="E",
            dedupe_key=uuid4().hex, payload={"k": 1})))
    finally:
        correlation.unbind(token)
    audit = uow.read(lambda tx: tx.audit.list_for_entity(org, "T", entity))
    assert audit[0].metadata["correlation_id"] == "trace-persist01"
    pending = uow.read(lambda tx: tx.outbox.list_pending())
    assert pending[0].payload == {"k": 1, "correlation_id": "trace-persist01"}
    # no id bound -> nothing is invented
    uow.run(lambda tx: tx.audit.append(AuditRecord(
        organization_id=org, action="Y", entity_type="T", entity_id=entity)))
    assert "correlation_id" not in uow.read(lambda tx: tx.audit.list_for_entity(org, "T", entity))[1].metadata
