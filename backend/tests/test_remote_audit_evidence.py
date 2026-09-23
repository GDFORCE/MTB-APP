"""Integration tests for the capture-session/upload-intent/finalize protocol
(app/services/remote_audit/evidence.py) — spec §19.2's A12/A13 (orphaned
upload reconciliation, expired nonce) plus the corrected upload sequencing
(v1.3 correction #6) and append-only verification versioning (correction #7).
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit import evidence as ev  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
AUDIT_ID = f"audit-{RUN_ID}"


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await ev.ensure_indexes(database)
    yield database
    await database[ev.CAPTURE_SESSIONS_COLLECTION].delete_many({"audit_id": AUDIT_ID})
    await database[ev.EVIDENCE_COLLECTION].delete_many({"audit_id": AUDIT_ID})
    await database[ev.EVIDENCE_VERIFICATIONS_COLLECTION].delete_many({"evidence_id": {"$regex": "^test-"}})
    database.client.close()


async def _session(db, **overrides):
    kwargs = dict(
        audit_id=AUDIT_ID, user_id="user-1", linked_entity_type="facility",
        linked_entity_id="fac-1", requested_media_type="image/jpeg",
        capture_mode="LIVE_IN_APP", policy_version="policy-v1",
    )
    kwargs.update(overrides)
    return await ev.create_capture_session(db, **kwargs)


@pytest.mark.asyncio
async def test_full_happy_path_capture_session_to_finalize(db):
    session = await _session(db)
    intent = await ev.create_upload_intent(
        db, capture_session_id=session["id"], file_name="photo.jpg",
        mime_claimed="image/jpeg", size_claimed=1024,
    )
    assert intent["server_mediated"] is True  # local storage backend in this test env

    evidence = await ev.finalize_capture(
        db, capture_session_id=session["id"], object_key=intent["object_key"],
        sha256_client="abc123", capture_manifest={"lat": 28.0, "lon": 77.0},
    )
    assert evidence["id"] == intent["evidence_id"]
    assert evidence["status"] == ev.STATUS_QUARANTINED

    stored = await ev.get_evidence(db, evidence["id"])
    assert stored["capture_session_id"] == session["id"]


@pytest.mark.asyncio
async def test_upload_intent_rejects_media_type_mismatch(db):
    session = await _session(db, requested_media_type="image/jpeg")
    with pytest.raises(ev.CaptureError) as exc:
        await ev.create_upload_intent(
            db, capture_session_id=session["id"], file_name="video.mp4",
            mime_claimed="video/mp4", size_claimed=1024,
        )
    assert exc.value.code == "MEDIA_TYPE_MISMATCH"


@pytest.mark.asyncio
async def test_upload_intent_rejects_oversized_claim(db):
    session = await _session(db)
    with pytest.raises(ev.CaptureError) as exc:
        await ev.create_upload_intent(
            db, capture_session_id=session["id"], file_name="huge.jpg",
            mime_claimed="image/jpeg", size_claimed=10**12, max_bytes=1000,
        )
    assert exc.value.code == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_display_filename_is_sanitized_never_used_as_storage_key(db):
    session = await _session(db)
    intent = await ev.create_upload_intent(
        db, capture_session_id=session["id"], file_name="../../etc/passwd.jpg",
        mime_claimed="image/jpeg", size_claimed=100,
    )
    assert ".." not in intent["object_key"]
    assert "/" not in intent["object_key"].split("originals/")[-1].replace(intent["evidence_id"], "")
    stored_session = await ev.get_capture_session(db, session["id"])
    # The actual security property: no path separator survives, so the
    # string can never be interpreted as a path -- a bare ".." substring
    # with no "/" or "\" around it is inert, not a traversal vector.
    assert "/" not in stored_session["display_name"]
    assert "\\" not in stored_session["display_name"]


# A13: expired capture nonce -> finalize rejected -----------------------------

@pytest.mark.asyncio
async def test_a13_expired_session_rejects_upload_intent(db):
    session = await _session(db, ttl_seconds=-1)  # already expired
    with pytest.raises(ev.CaptureError) as exc:
        await ev.create_upload_intent(
            db, capture_session_id=session["id"], file_name="photo.jpg",
            mime_claimed="image/jpeg", size_claimed=1024,
        )
    assert exc.value.code == "SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_a13_expired_session_rejects_finalize(db):
    session = await _session(db, ttl_seconds=5)
    intent = await ev.create_upload_intent(
        db, capture_session_id=session["id"], file_name="photo.jpg",
        mime_claimed="image/jpeg", size_claimed=1024,
    )
    # simulate the session expiring between upload-intent and finalize
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    with pytest.raises(ev.CaptureError) as exc:
        await ev.finalize_capture(
            db, capture_session_id=session["id"], object_key=intent["object_key"],
            sha256_client="abc", capture_manifest={}, now=future,
        )
    assert exc.value.code == "SESSION_EXPIRED"


# Replay: a second finalize on the same session must never create a duplicate -

@pytest.mark.asyncio
async def test_replayed_finalize_is_rejected_not_a_duplicate_evidence_row(db):
    session = await _session(db)
    intent = await ev.create_upload_intent(
        db, capture_session_id=session["id"], file_name="photo.jpg",
        mime_claimed="image/jpeg", size_claimed=1024,
    )
    await ev.finalize_capture(
        db, capture_session_id=session["id"], object_key=intent["object_key"],
        sha256_client="abc", capture_manifest={},
    )
    with pytest.raises(ev.CaptureError) as exc:
        await ev.finalize_capture(
            db, capture_session_id=session["id"], object_key=intent["object_key"],
            sha256_client="abc", capture_manifest={},
        )
    assert exc.value.code == "SESSION_ALREADY_CONSUMED"
    count = await db[ev.EVIDENCE_COLLECTION].count_documents({"capture_session_id": session["id"]})
    assert count == 1


# A12: orphaned upload (upload-intent succeeded, finalize never called) -----

@pytest.mark.asyncio
async def test_a12_orphaned_capture_session_is_reconciled(db):
    session = await _session(db, ttl_seconds=-1)  # expired without ever finalizing
    # Directly reserve an object key to simulate a completed upload-intent
    # (create_upload_intent itself would reject this session as expired).
    await db[ev.CAPTURE_SESSIONS_COLLECTION].update_one(
        {"id": session["id"]}, {"$set": {"reserved_object_key": "some/key.jpg", "reserved_evidence_id": "ev-orphan"}},
    )
    reconciled = await ev.reconcile_orphaned_uploads(db, older_than=datetime.now(timezone.utc))
    assert session["id"] in reconciled
    stored = await ev.get_capture_session(db, session["id"])
    assert stored["reserved_object_key"] is None
    assert stored["reserved_evidence_id"] is None


# Append-only/versioned verification results ---------------------------------

@pytest.mark.asyncio
async def test_verification_results_are_append_only_never_overwritten(db):
    evidence_id = f"test-{RUN_ID}"
    first = await ev.record_verification(
        db, evidence_id=evidence_id, overall_state="REVIEW",
        rule_set_version="v1", reasons=[{"code": "LOCATION_MISSING"}], signals={},
    )
    second = await ev.record_verification(
        db, evidence_id=evidence_id, overall_state="PASS",
        rule_set_version="v1", reasons=[], signals={},
    )
    assert first["verification_run_id"] == 1
    assert second["verification_run_id"] == 2
    assert second["supersedes_verification_id"] == first["id"]

    history = await ev.list_verifications(db, evidence_id)
    assert len(history) == 2
    assert history[0]["overall_state"] == "REVIEW"  # first run still retrievable, untouched
    assert history[1]["overall_state"] == "PASS"

    latest = await ev.latest_verification(db, evidence_id)
    assert latest["id"] == second["id"]


@pytest.mark.asyncio
async def test_duplicate_sha256_detection_excludes_self(db):
    a_session = await _session(db)
    a_intent = await ev.create_upload_intent(
        db, capture_session_id=a_session["id"], file_name="p1.jpg",
        mime_claimed="image/jpeg", size_claimed=100,
    )
    a = await ev.finalize_capture(
        db, capture_session_id=a_session["id"], object_key=a_intent["object_key"],
        sha256_client="x", capture_manifest={},
    )
    await ev.set_evidence_status(db, a["id"], "PROCESSING", sha256_server="samehash")
    b_session = await _session(db)
    b_intent = await ev.create_upload_intent(
        db, capture_session_id=b_session["id"], file_name="p2.jpg",
        mime_claimed="image/jpeg", size_claimed=100,
    )
    b = await ev.finalize_capture(
        db, capture_session_id=b_session["id"], object_key=b_intent["object_key"],
        sha256_client="y", capture_manifest={},
    )
    await ev.set_evidence_status(db, b["id"], "PROCESSING", sha256_server="samehash")

    assert await ev.find_duplicate_sha256(db, "samehash", exclude_evidence_id=b["id"]) is True
    assert await ev.find_duplicate_sha256(db, "samehash", exclude_evidence_id=a["id"]) is True
    assert await ev.find_duplicate_sha256(db, "uniquehash", exclude_evidence_id=b["id"]) is False
