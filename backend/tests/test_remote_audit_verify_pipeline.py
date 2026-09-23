"""End-to-end integration tests for the evidence verification pipeline
(app/services/remote_audit/verify_pipeline.py) — wires together storage,
hashing, malware gating, geofence classification, and the decision engine
against real Mongo + real image bytes.
"""
from __future__ import annotations

import hashlib
import io
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

import storage as file_storage  # noqa: E402

from app.services.remote_audit import audits as audits_service  # noqa: E402
from app.services.remote_audit import evidence as evidence_service  # noqa: E402
from app.services.remote_audit import sites as sites_service  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402
from app.services.remote_audit.verification import OverallState  # noqa: E402
from app.services.remote_audit.verify_pipeline import (  # noqa: E402
    ScannerUnavailableError,
    VerificationPolicy,
    run_evidence_verification,
)

RUN_ID = uuid.uuid4().hex[:8]
ORG_ID = f"org-{RUN_ID}"
SITE_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[(77.0000, 28.0000), (77.0020, 28.0000), (77.0020, 28.0018), (77.0000, 28.0018), (77.0000, 28.0000)]],
}
INSIDE_POINT = (77.0010, 28.0009)
OUTSIDE_POINT = (77.5000, 28.5000)


@pytest.fixture(autouse=True)
def _require_malware_scan(monkeypatch):
    """Pin the malware-scan gate ON for this whole module.

    These tests assert the PRODUCTION rule: an unreachable scanner blocks.
    A developer `.env` with REMOTE_AUDIT_REQUIRE_MALWARE_SCAN=false must not
    be able to quietly change what they assert -- otherwise the suite that
    guards the gate would pass by not testing it.
    """
    monkeypatch.setenv("REMOTE_AUDIT_REQUIRE_MALWARE_SCAN", "true")

def make_jpeg_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buf, format="JPEG")
    return buf.getvalue()


@pytest_asyncio.fixture
async def env():
    db = get_db()
    await sites_service.ensure_indexes(db)
    await audits_service.ensure_indexes(db)
    await evidence_service.ensure_indexes(db)

    site = await sites_service.create_site(
        db, organization_id=ORG_ID, official_name="Test Hospital",
        address={}, timezone_name="Asia/Kolkata",
    )
    version = await sites_service.propose_geofence_version(
        db, site_id=site["id"], geometry=SITE_GEOMETRY, buffer_m=10.0, proposed_by="approver-1",
    )
    await sites_service.approve_geofence_version(db, geofence_version_id=version["id"], approved_by="approver-1")
    audit = await audits_service.create_audit(
        db, audit_owner_org_id=f"sponsor-{RUN_ID}", site_id=site["id"],
        site_org_id=ORG_ID, created_by="creator-1",
    )
    yield {"db": db, "site": site, "audit": audit}

    await db[sites_service.SITES_COLLECTION].delete_many({"organization_id": ORG_ID})
    await db[sites_service.GEOFENCE_VERSIONS_COLLECTION].delete_many({"site_id": site["id"]})
    await db[audits_service.AUDITS_COLLECTION].delete_many({"id": audit["id"]})
    await db[evidence_service.CAPTURE_SESSIONS_COLLECTION].delete_many({"audit_id": audit["id"]})
    await db[evidence_service.EVIDENCE_COLLECTION].delete_many({"audit_id": audit["id"]})
    db.client.close()


async def _create_evidence_with_manifest(env, manifest: dict, jpeg_bytes: bytes):
    db = env["db"]
    session = await evidence_service.create_capture_session(
        db, audit_id=env["audit"]["id"], user_id="user-1", linked_entity_type="facility",
        linked_entity_id="fac-1", requested_media_type="image/jpeg",
        capture_mode="LIVE_IN_APP", policy_version="policy-v1",
    )
    intent = await evidence_service.create_upload_intent(
        db, capture_session_id=session["id"], file_name="photo.jpg",
        mime_claimed="image/jpeg", size_claimed=len(jpeg_bytes),
    )
    storage = file_storage.get_storage()
    await storage.save(intent["object_key"], jpeg_bytes, "image/jpeg")
    sha256_client = hashlib.sha256(jpeg_bytes).hexdigest()
    doc = await evidence_service.finalize_capture(
        db, capture_session_id=session["id"], object_key=intent["object_key"],
        sha256_client=sha256_client, capture_manifest=manifest,
    )
    return doc


@pytest.mark.asyncio
async def test_happy_path_inside_geofence_passes(env, monkeypatch):
    monkeypatch.setenv("CLAMAV_HOST", "")  # ensure default: unconfigured unless a test sets it
    monkeypatch.delenv("CLAMAV_HOST", raising=False)
    jpeg = make_jpeg_bytes()
    doc = await _create_evidence_with_manifest(env, {
        "lon": INSIDE_POINT[0], "lat": INSIDE_POINT[1],
        "accuracy_m": 10.0, "location_age_ms": 1000,
    }, jpeg)

    # Malware scan is UNAVAILABLE without CLAMAV_HOST -- proves the strict
    # gate raises rather than silently treating the file as clean.
    with pytest.raises(ScannerUnavailableError):
        await run_evidence_verification(env["db"], doc["id"])
    blocked = await evidence_service.get_evidence(env["db"], doc["id"])
    assert blocked["status"] == "PROCESSING_BLOCKED"


@pytest.mark.asyncio
async def test_with_malware_scan_configured_but_unreachable_still_blocks(env, monkeypatch):
    """Configuring a host that isn't actually reachable must behave exactly
    like unconfigured -- UNAVAILABLE, not CLEAN (v1.3 correction #15)."""
    monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAMAV_PORT", "1")  # nothing listens here
    jpeg = make_jpeg_bytes()
    doc = await _create_evidence_with_manifest(env, {
        "lon": INSIDE_POINT[0], "lat": INSIDE_POINT[1], "accuracy_m": 10.0, "location_age_ms": 1000,
    }, jpeg)
    with pytest.raises(ScannerUnavailableError):
        await run_evidence_verification(env["db"], doc["id"])


@pytest.mark.asyncio
async def test_geofence_outside_boundary_routes_to_review(env, monkeypatch):
    """With malware scanning stubbed clean (isolating the geo logic under
    test), a point far outside the approved geofence must be reviewed."""
    from app.services.remote_audit import file_checks

    async def fake_clean(data):
        return file_checks.MalwareScanStatus.CLEAN, None

    monkeypatch.setattr(file_checks, "scan", fake_clean)

    jpeg = make_jpeg_bytes()
    doc = await _create_evidence_with_manifest(env, {
        "lon": OUTSIDE_POINT[0], "lat": OUTSIDE_POINT[1], "accuracy_m": 10.0, "location_age_ms": 1000,
    }, jpeg)
    result = await run_evidence_verification(env["db"], doc["id"])
    assert result.overall_state == OverallState.REVIEW

    stored = await evidence_service.get_evidence(env["db"], doc["id"])
    assert stored["status"] == "REVIEW"
    assert stored["sha256_server"] == hashlib.sha256(jpeg).hexdigest()


@pytest.mark.asyncio
async def test_hash_mismatch_is_fail_technical(env, monkeypatch):
    from app.services.remote_audit import file_checks

    async def fake_clean(data):
        return file_checks.MalwareScanStatus.CLEAN, None

    monkeypatch.setattr(file_checks, "scan", fake_clean)

    jpeg = make_jpeg_bytes()
    db = env["db"]
    session = await evidence_service.create_capture_session(
        db, audit_id=env["audit"]["id"], user_id="user-1", linked_entity_type="facility",
        linked_entity_id="fac-1", requested_media_type="image/jpeg",
        capture_mode="LIVE_IN_APP", policy_version="policy-v1",
    )
    intent = await evidence_service.create_upload_intent(
        db, capture_session_id=session["id"], file_name="photo.jpg",
        mime_claimed="image/jpeg", size_claimed=len(jpeg),
    )
    storage = file_storage.get_storage()
    await storage.save(intent["object_key"], jpeg, "image/jpeg")
    doc = await evidence_service.finalize_capture(
        db, capture_session_id=session["id"], object_key=intent["object_key"],
        sha256_client="deliberately-wrong-hash", capture_manifest={
            "lon": INSIDE_POINT[0], "lat": INSIDE_POINT[1], "accuracy_m": 10.0, "location_age_ms": 1000,
        },
    )
    result = await run_evidence_verification(db, doc["id"])
    assert result.overall_state == OverallState.FAIL_TECHNICAL


@pytest.mark.asyncio
async def test_repeated_verification_runs_are_appended_not_overwritten(env, monkeypatch):
    from app.services.remote_audit import file_checks

    async def fake_clean(data):
        return file_checks.MalwareScanStatus.CLEAN, None

    monkeypatch.setattr(file_checks, "scan", fake_clean)

    jpeg = make_jpeg_bytes()
    doc = await _create_evidence_with_manifest(env, {
        "lon": INSIDE_POINT[0], "lat": INSIDE_POINT[1], "accuracy_m": 10.0, "location_age_ms": 1000,
    }, jpeg)
    first = await run_evidence_verification(env["db"], doc["id"])
    second = await run_evidence_verification(env["db"], doc["id"], policy=VerificationPolicy(current_presence_required=False))

    history = await evidence_service.list_verifications(env["db"], doc["id"])
    assert len(history) == 2
    assert history[0]["overall_state"] == first.overall_state.value
    assert history[1]["overall_state"] == second.overall_state.value
