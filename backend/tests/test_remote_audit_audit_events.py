"""Phase 0 exit-criterion tests for the remote-audit audit-event schema
builder (app/services/remote_audit/audit_events.py).

Confirms the standardized payload shape maps cleanly onto the existing
`write_audit(user, action, detail, status=..., **ctx)` in backend/server.py
without requiring any change to that function — it already stores arbitrary
keyword context verbatim.
"""
from __future__ import annotations

from app.services.remote_audit.audit_events import RemoteAuditEvent


def test_minimal_event_produces_only_required_kwargs():
    event = RemoteAuditEvent(
        action="remote_audit.evidence.finalized",
        resource_type="evidence",
        resource_id="ev-1",
        result="success",
    )
    kwargs = event.as_write_audit_kwargs()
    assert kwargs["resource_type"] == "evidence"
    assert kwargs["target_id"] == "ev-1"
    assert kwargs["correlation_id"] == event.correlation_id
    # optional fields left unset never appear as None noise in the stored doc
    for optional_key in (
        "org_id", "site_id", "reason", "reason_code",
        "policy_version", "rule_version",
        "before_ref", "after_ref", "before_hash", "after_hash",
    ):
        assert optional_key not in kwargs


def test_full_event_carries_every_schema_field():
    event = RemoteAuditEvent(
        action="remote_audit.evidence.verification_completed",
        resource_type="evidence_verification",
        resource_id="ver-1",
        result="blocked",
        organization_id="org-1",
        site_id="site-1",
        reason="malware detected",
        reason_code="MALWARE_DETECTED",
        policy_version="template-v3",
        rule_version="geofence-rules-v2",
        before_ref="evidence/ev-1/verifications/ver-0",
        after_ref="evidence/ev-1/verifications/ver-1",
        before_hash="abc123",
        after_hash="def456",
    )
    kwargs = event.as_write_audit_kwargs()
    assert kwargs == {
        "resource_type": "evidence_verification",
        "target_id": "ver-1",
        "correlation_id": event.correlation_id,
        "org_id": "org-1",
        "site_id": "site-1",
        "reason": "malware detected",
        "reason_code": "MALWARE_DETECTED",
        "policy_version": "template-v3",
        "rule_version": "geofence-rules-v2",
        "before_ref": "evidence/ev-1/verifications/ver-0",
        "after_ref": "evidence/ev-1/verifications/ver-1",
        "before_hash": "abc123",
        "after_hash": "def456",
    }


def test_status_property_matches_result_for_write_audit_status_param():
    event = RemoteAuditEvent(
        action="remote_audit.decision.signed",
        resource_type="decision",
        resource_id="dec-1",
        result="failure",
    )
    assert event.status == "failure"


def test_correlation_id_defaults_to_a_fresh_uuid_per_event():
    event_a = RemoteAuditEvent(
        action="remote_audit.query.raised", resource_type="query",
        resource_id="q-1", result="success",
    )
    event_b = RemoteAuditEvent(
        action="remote_audit.query.raised", resource_type="query",
        resource_id="q-2", result="success",
    )
    assert event_a.correlation_id != event_b.correlation_id
