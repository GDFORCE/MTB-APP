"""Observability and log redaction — spec §20, Phase 9.

The redaction tests here are the Phase 9 CI gate the plan requires. They are
not documentation: `test_real_pipeline_logs_contain_nothing_forbidden` runs the
actual document pipeline with logging captured and scans what came out, so a
future `log.info(f"...{manifest}")` added anywhere in that path fails this
suite rather than quietly duplicating evidence into the log store.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit import observability as obs  # noqa: E402

RUN = uuid.uuid4().hex[:8]


# ═══════════════════════════════════════════════════════════════════════════
# Redaction
# ═══════════════════════════════════════════════════════════════════════════

def test_raw_coordinates_are_redacted():
    """A precise coordinate pair places a person. It must not sit in a log
    aggregator that far more people can read than can read the evidence."""
    redacted = obs.redact({"lat": 28.6139, "lon": 77.2090, "accuracy_m": 8.5})
    assert redacted["lat"] == obs.REDACTED
    assert redacted["lon"] == obs.REDACTED
    # Accuracy is a quality measure, not a position — it stays, because it is
    # the useful half for diagnosing a bad fix.
    assert redacted["accuracy_m"] == 8.5


def test_tokens_secrets_and_signed_urls_are_redacted():
    redacted = obs.redact({
        "play_integrity_token": "eyJhbGciOiJSUzI1NiJ9.payload.signature",
        "step_up_token": "abc123",
        "api_key": "AIzaSyDexample",
        "presigned_url": "https://bucket.s3.amazonaws.com/k?X-Amz-Signature=deadbeef",
        "client_secret": "hunter2",
    })
    assert all(value == obs.REDACTED for value in redacted.values())


def test_document_and_evidence_content_is_redacted():
    redacted = obs.redact({
        "raw_response": '{"certificate_number": "CAL-1"}',
        "text": "Patient: Jane Doe, DOB 1970-01-01",
        "bytes": b"%PDF-1.7...",
        "media_type": "application/pdf",
    })
    assert redacted["raw_response"] == obs.REDACTED
    assert redacted["text"] == obs.REDACTED
    assert redacted["bytes"] == obs.REDACTED
    # The TYPE is safe and useful; the CONTENT is not.
    assert redacted["media_type"] == "application/pdf"


def test_personal_data_is_redacted():
    redacted = obs.redact({"email": "a@b.com", "phone": "+91...", "full_name": "Jane Doe",
                           "user_id": "u-123"})
    assert redacted["email"] == obs.REDACTED
    assert redacted["phone"] == obs.REDACTED
    assert redacted["full_name"] == obs.REDACTED
    # An opaque id is how you correlate a log line to an audit record without
    # putting the person in the log.
    assert redacted["user_id"] == "u-123"


def test_redaction_reaches_nested_structures():
    redacted = obs.redact({
        "evidence": {"manifest": {"lat": 1.234567, "nested": [{"secret": "x"}]}},
    })
    assert redacted["evidence"]["manifest"]["lat"] == obs.REDACTED
    assert redacted["evidence"]["manifest"]["nested"][0]["secret"] == obs.REDACTED


def test_deep_nesting_is_truncated_rather_than_recursing_forever():
    """A log call must never be the thing that blows the stack."""
    payload = current = {}
    for _ in range(50):
        current["child"] = {}
        current = current["child"]
    current["lat"] = 1.23456
    redacted = obs.redact(payload)
    # It terminates, and errs toward showing less.
    assert json.dumps(redacted, default=str)


def test_safe_operational_fields_survive_redaction():
    """Redaction that ate everything would make the logs useless, which is its
    own kind of failure."""
    redacted = obs.redact({
        "geo_status": "OUTSIDE_BOUNDARY", "attestation_tier": "UNAVAILABLE",
        "accuracy_m": 42.0, "location_age_ms": 1200, "evidence_id": "e-1",
        "audit_id": "a-1", "status": "REVIEW",
    })
    assert redacted["geo_status"] == "OUTSIDE_BOUNDARY"
    assert redacted["attestation_tier"] == "UNAVAILABLE"
    assert redacted["evidence_id"] == "e-1"
    assert redacted["status"] == "REVIEW"


# ═══════════════════════════════════════════════════════════════════════════
# The CI gate itself
# ═══════════════════════════════════════════════════════════════════════════

def test_the_gate_catches_each_forbidden_pattern():
    """If the detector doesn't detect, the gate is decorative."""
    samples = {
        "raw_gps_coordinates": "captured at 28.6139, 77.2090 today",
        "presigned_url": "GET https://b.s3.amazonaws.com/k?X-Amz-Signature=abc123",
        "jwt_token": "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NX0.dBjftJeZ4CVPmB92K",
        "inline_secret": 'api_key = "AIzaSyDexamplekey123"',
        "embedded_file_content": "data:application/pdf;base64,JVBERi0xLjc=",
        "pdf_content": "%PDF-1.7 stream...",
    }
    for rule, text in samples.items():
        found = dict(obs.find_forbidden_patterns(text))
        assert rule in found, f"{rule} was not detected in {text!r}"
        with pytest.raises(AssertionError):
            obs.assert_no_forbidden_patterns(text)


def test_the_gate_passes_clean_output():
    clean = json.dumps({
        "metric": "remote_audit.verification.outcome", "value": 1.0,
        "tags": {"evidence_id": "e-1", "geo_status": "WITHIN_BOUNDARY",
                 "lat": obs.REDACTED, "token": obs.REDACTED},
    })
    obs.assert_no_forbidden_patterns(clean)


def test_a_redacted_secret_assignment_does_not_trip_the_gate():
    """Redaction and detection must agree, or a correctly-redacted line would
    fail CI and teach people to disable the gate."""
    obs.assert_no_forbidden_patterns('api_key: [redacted]')
    obs.assert_no_forbidden_patterns(json.dumps(obs.redact({"api_key": "AIzaSyDexample"})))


def test_a_redacted_payload_passes_the_gate_end_to_end():
    payload = {
        "lat": 28.6139, "lon": 77.2090,
        "play_integrity_token": "eyJhbGciOiJSUzI1NiJ9.abcdefghij.klmnopqrst",
        "presigned": "https://b.s3.amazonaws.com/k?X-Amz-Signature=deadbeefcafe",
    }
    assert obs.find_forbidden_patterns(json.dumps(payload)), "fixture must be dirty to be meaningful"
    obs.assert_no_forbidden_patterns(json.dumps(obs.redact(payload)))


# ═══════════════════════════════════════════════════════════════════════════
# Metric emission
# ═══════════════════════════════════════════════════════════════════════════

def test_metric_tags_are_redacted_on_the_way_out():
    """A metric tagged with a coordinate puts it in the log stream just as
    surely as a log line would."""
    event = obs.emit_metric(obs.Metric.GEO_OUTSIDE_BOUNDARY, 1,
                            tags={"lat": 28.6139, "audit_id": "a-1"})
    assert event.tags["lat"] == obs.REDACTED
    assert event.tags["audit_id"] == "a-1"


def test_emitted_metric_lines_pass_the_gate():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("remote_audit.metrics")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        obs.emit_metric(obs.Metric.LOCATION_ACCURACY_M, 8.5,
                        tags={"lat": 28.6139, "lon": 77.2090, "accuracy_m": 8.5})
        obs.emit_metric(obs.Metric.ATTESTATION_UNAVAILABLE, 1,
                        tags={"play_integrity_token": "eyJhbGciOiJSUzI1NiJ9.aaaaaaaa.bbbbbbbb"})
    finally:
        logger.removeHandler(handler)
    obs.assert_no_forbidden_patterns(stream.getvalue())


def test_every_metric_the_spec_asks_for_exists():
    """§20's list, pinned. A metric that quietly disappears takes its
    dashboard and its alert with it."""
    required = {
        "capture_session", "location.accuracy", "attestation", "verification.latency",
        "queue.depth", "job.dead_lettered", "malware", "query.open_age", "capa.overdue",
        "virtual_session.join", "decision.manual_override", "authz.denied",
    }
    names = {m.value for m in obs.Metric}
    for fragment in required:
        assert any(fragment in name for name in names), f"no metric covers {fragment}"


def test_metric_names_are_unique():
    names = [m.value for m in obs.Metric]
    assert len(names) == len(set(names))


# ═══════════════════════════════════════════════════════════════════════════
# Alerting
# ═══════════════════════════════════════════════════════════════════════════

def test_every_alert_rule_states_why_it_exists():
    """An alert nobody can justify is an alert everyone learns to ignore."""
    for rule in obs.ALERT_RULES:
        assert rule.rationale.strip(), f"{rule.metric.value} has no stated rationale"
        assert rule.severity in ("page", "ticket")
        assert rule.direction in ("above", "below")
        assert rule.window_minutes > 0


def test_the_paging_set_is_deliberately_small():
    paging = obs.paging_rules()
    assert 0 < len(paging) <= 6, "too many paging alerts means none of them get read"


def test_the_things_that_stall_an_audit_page_someone():
    """A scanner outage and a dead-lettered job both mean evidence a site
    submitted is going nowhere, silently and correctly. Someone has to know."""
    paging_metrics = {rule.metric for rule in obs.paging_rules()}
    assert obs.Metric.MALWARE_SCAN_UNAVAILABLE in paging_metrics
    assert obs.Metric.JOB_DEAD_LETTERED in paging_metrics
    assert obs.Metric.MALWARE_DETECTED in paging_metrics


def test_expected_conditions_ticket_rather_than_page():
    """Attestation is UNAVAILABLE for every capture until Phase 8's native
    bridges ship. Paging on the expected state trains people to ignore pages."""
    rule = next(r for r in obs.rules_for(obs.Metric.ATTESTATION_UNAVAILABLE))
    assert rule.severity == "ticket"


def test_alert_evaluation_respects_direction():
    above = next(r for r in obs.ALERT_RULES if r.direction == "above")
    assert obs.evaluate_alert(above, above.threshold + 1) is True
    assert obs.evaluate_alert(above, above.threshold) is False


# ═══════════════════════════════════════════════════════════════════════════
# Against the real pipeline
# ═══════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def db():
    from app.services.remote_audit import documents as d
    from app.services.remote_audit.db import get_db

    database = get_db()
    await d.ensure_indexes(database)
    yield database
    await database[d.DOCUMENTS_COLLECTION].delete_many({"audit_id": {"$regex": f"^audit-{RUN}"}})


@pytest.mark.asyncio
async def test_real_pipeline_logs_contain_nothing_forbidden(db, monkeypatch):
    """The gate, run against real output rather than a fixture.

    A future `log.info(f"...{manifest}")` anywhere in document processing fails
    here instead of quietly writing evidence into the log store.
    """
    import storage as file_storage
    from app.services.remote_audit import document_pipeline as pipeline
    from app.services.remote_audit import documents as d
    from app.services.remote_audit import file_checks

    fitz = pytest.importorskip("fitz", reason="PyMuPDF is required")
    doc_bytes = fitz.open()
    page = doc_bytes.new_page()
    page.insert_text((72, 100), "Calibration Certificate\nDue Date: 2027-05-04")
    payload = doc_bytes.tobytes()
    doc_bytes.close()

    async def clean(_data):
        return file_checks.MalwareScanStatus.CLEAN, None
    monkeypatch.setattr(pipeline.file_checks, "scan", clean)

    record = await d.create_document(
        db, audit_id=f"audit-{RUN}-logs", uploaded_by="u", document_type="calibration_certificate",
        file_name="cert.pdf", mime_claimed="application/pdf", size_claimed=len(payload))
    await file_storage.get_storage().save(record["object_key"], payload, "application/pdf")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        await pipeline.run_document_processing(
            db, record["id"], policy=pipeline.DocumentPolicy(enable_vision_extraction=False))
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    obs.assert_no_forbidden_patterns(stream.getvalue())


@pytest.mark.asyncio
async def test_queue_health_reports_depth_and_age(db):
    from app.services.remote_audit.job_queue import JobQueue

    queue = JobQueue(db)
    await queue.ensure_indexes()
    health = await obs.queue_health(db)
    assert health["depth"] >= 0
    assert health["oldest_age_seconds"] >= 0
