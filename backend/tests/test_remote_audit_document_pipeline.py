"""End-to-end document processing — spec §10, §16.8.

Wires real storage, real PDF bytes, real hashing and the real malware gate
together. The malware-scanner tests matter most: ClamAV is not configured in
this environment, which means every run of this suite genuinely exercises the
"scanner unavailable" path rather than a mocked stand-in — and the assertion
is that a document in that state is NOT shown to anyone.
"""
from __future__ import annotations

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

from app.services.remote_audit import document_pipeline as pipeline  # noqa: E402
from app.services.remote_audit import documents as d  # noqa: E402
from app.services.remote_audit import file_checks  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN = uuid.uuid4().hex[:8]
AUDIT_ID = f"audit-{RUN}-pipeline"

NO_VISION = pipeline.DocumentPolicy(enable_vision_extraction=False)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _require_malware_scan(monkeypatch):
    """Pin the malware-scan gate ON for this whole module.

    These tests assert the PRODUCTION rule: an unreachable scanner blocks.
    A developer `.env` with REMOTE_AUDIT_REQUIRE_MALWARE_SCAN=false must not
    be able to quietly change what they assert -- otherwise the suite that
    guards the gate would pass by not testing it.
    """
    monkeypatch.setenv("REMOTE_AUDIT_REQUIRE_MALWARE_SCAN", "true")

def make_pdf(text: str = "Calibration Certificate") -> bytes:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is required")
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(text.split("\n")):
        page.insert_text((72, 100 + i * 18), line)
    data = doc.tobytes()
    doc.close()
    return data


def make_jpeg() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color="white").save(buf, format="JPEG")
    return buf.getvalue()


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await d.ensure_indexes(database)
    yield database
    await database[d.DOCUMENTS_COLLECTION].delete_many({"audit_id": {"$regex": f"^audit-{RUN}"}})


async def stage(db, data: bytes, *, media_type="application/pdf", document_type="calibration_certificate"):
    """Create a document record and actually put its bytes in storage — the
    same two steps the API performs before enqueueing the worker job."""
    doc = await d.create_document(
        db, audit_id=AUDIT_ID, uploaded_by=f"user-{RUN}", document_type=document_type,
        file_name="cert.pdf" if media_type == "application/pdf" else "cert.jpg",
        mime_claimed=media_type, size_claimed=max(1, len(data)))
    await file_storage.get_storage().save(doc["object_key"], data, media_type)
    return doc


def scanner_is_configured() -> bool:
    import os
    return bool(os.environ.get("CLAMAV_HOST"))


# ── 1. The malware gate runs first and is strict ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.skipif(scanner_is_configured(), reason="requires NO scanner configured")
async def test_unavailable_scanner_blocks_and_never_makes_the_document_visible(db):
    """v1.3 correction #15. This is the single most important test in Phase 4:
    an unreachable scanner must leave the document invisible, NOT route it to
    review with a caveat, and NOT let it through as clean."""
    doc = await stage(db, make_pdf())

    with pytest.raises(pipeline.ScannerUnavailableError):
        await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    after = await d.get_document(db, doc["id"])
    assert after["status"] == d.STATUS_PROCESSING_BLOCKED
    assert d.is_visible_to_reviewer(after["status"]) is False
    # Crucially, no verdict was recorded — the pipeline did not decide
    # anything on the strength of an absent scan.
    assert await d.latest_check(db, doc["id"]) is None


@pytest.mark.asyncio
@pytest.mark.skipif(scanner_is_configured(), reason="requires NO scanner configured")
async def test_blocked_document_can_be_retried_and_stays_blocked(db):
    """The queue retries a blocked job. Repeated failures must keep the same
    safe state rather than eventually wearing the gate down."""
    doc = await stage(db, make_pdf())
    for _ in range(3):
        with pytest.raises(pipeline.ScannerUnavailableError):
            await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
        assert (await d.get_document(db, doc["id"]))["status"] == d.STATUS_PROCESSING_BLOCKED


@pytest.mark.asyncio
async def test_detected_malware_rejects_and_blocks_the_content(db, monkeypatch):
    async def fake_scan(_data):
        return file_checks.MalwareScanStatus.DETECTED, "Eicar-Test-Signature"

    monkeypatch.setattr(pipeline.file_checks, "scan", fake_scan)
    doc = await stage(db, make_pdf())

    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert result.outcome == d.STATUS_REJECTED
    assert result.content_blocked is True
    after = await d.get_document(db, doc["id"])
    assert after["status"] == d.STATUS_REJECTED
    assert after["content_blocked"] is True
    assert after["decision_reason_code"] == "MALWARE_DETECTED"
    # The record is visible so the site is told why; the bytes are not.
    assert d.is_visible_to_reviewer(after["status"]) is True
    assert any(f["code"] == "MALWARE_DETECTED" for f in result.findings)


@pytest.mark.asyncio
async def test_nothing_is_parsed_before_the_scan_completes(db, monkeypatch):
    """Ordering is the control, not an implementation detail. If the scanner
    says DETECTED, the PDF parser must never have been invoked."""
    parsed = {"called": False}

    def spy_inspect(_data):
        parsed["called"] = True
        raise AssertionError("inspect_pdf must not run before a clean scan")

    async def fake_scan(_data):
        return file_checks.MalwareScanStatus.DETECTED, "X"

    monkeypatch.setattr(pipeline.file_checks, "scan", fake_scan)
    monkeypatch.setattr(pipeline.pdf_inspect, "inspect_pdf", spy_inspect)

    doc = await stage(db, make_pdf())
    await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
    assert parsed["called"] is False


# ── 2/3. Real bytes, real structure ─────────────────────────────────────────

@pytest.fixture
def clean_scanner(monkeypatch):
    async def fake_scan(_data):
        return file_checks.MalwareScanStatus.CLEAN, None
    monkeypatch.setattr(pipeline.file_checks, "scan", fake_scan)
    return fake_scan


@pytest.mark.asyncio
async def test_clean_unsigned_pdf_reaches_technically_valid_not_accepted(db, clean_scanner):
    """Processing can never produce ACCEPTED. Acceptance is a human act."""
    doc = await stage(db, make_pdf("Annual Inspection Report"))
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert result.outcome in (d.STATUS_TECHNICALLY_VALID, d.STATUS_REVIEW_REQUIRED)
    assert result.outcome != d.STATUS_ACCEPTED
    after = await d.get_document(db, doc["id"])
    assert after["status"] != d.STATUS_ACCEPTED
    assert after["sha256_server"] == result.sha256_server
    assert result.checks["malware_status"] == "CLEAN"


@pytest.mark.asyncio
async def test_a_file_that_is_not_the_type_it_claims_is_flagged(db, clean_scanner):
    """A JPEG uploaded as application/pdf. The claimed content-type is never
    trusted — only the real magic bytes are."""
    doc = await stage(db, make_jpeg(), media_type="application/pdf")
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert result.checks["mime_match"] is False
    assert any(f["code"] == "MIME_MISMATCH" for f in result.findings)
    assert result.outcome == d.STATUS_REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_a_byte_identical_reupload_is_flagged_as_duplicate(db, clean_scanner):
    data = make_pdf("Duplicate Certificate")
    first = await stage(db, data)
    await pipeline.run_document_processing(db, first["id"], policy=NO_VISION)

    second = await stage(db, data)
    result = await pipeline.run_document_processing(db, second["id"], policy=NO_VISION)

    assert result.checks["duplicate_sha256"] is True
    assert any(f["code"] == "DUPLICATE_DOCUMENT" for f in result.findings)


@pytest.mark.asyncio
async def test_garbage_bytes_do_not_crash_the_worker(db, clean_scanner):
    """§16.8: a malformed file is input, not an incident. The worker must
    produce a verdict rather than an exception — a crash would take down
    processing for every other tenant's documents too."""
    for payload in (b"%PDF-1.7 garbage", b"%PDF-" + b"\x00" * 4096, b"%PDF-1.4" + b"\xff" * 2000):
        doc = await stage(db, payload)
        result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
        assert result.outcome == d.STATUS_REVIEW_REQUIRED
        assert any(f["code"] in ("PDF_UNREADABLE", "MIME_MISMATCH") for f in result.findings)


@pytest.mark.asyncio
async def test_an_empty_file_is_handled(db, clean_scanner):
    doc = await stage(db, b"")
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
    assert result.outcome == d.STATUS_REVIEW_REQUIRED


# ── 4. Signature routing ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signature_present_but_unverified_routes_to_review(db, clean_scanner, monkeypatch):
    """Correction #11: SIGNATURE_UNKNOWN / untrusted must route to
    REVIEW_REQUIRED, never quietly through to TECHNICALLY_VALID."""
    from app.services.remote_audit.signatures import (
        SignatureFinding, SignatureReport, SignatureTrust,
    )

    def fake_validate(_data, **_kwargs):
        return SignatureReport(
            overall=SignatureTrust.PRESENT_UNVERIFIED, anchors_configured=0,
            signatures=[SignatureFinding(
                field_name="Signature1", trust=SignatureTrust.PRESENT_UNVERIFIED, intact=True,
                chain_trusted=False, covers_whole_document=True, modification_level=None,
                signing_time=None, timestamp_trusted=False, revocation_checked=False,
                revoked=False, signer_common_name="Lab", signer_cert_not_before=None,
                signer_cert_not_after=None, cert_valid_at_signing_time=False,
                reasons=["NO_TRUST_ANCHORS_CONFIGURED"])])

    monkeypatch.setattr(pipeline.signature_service, "validate_pdf_signatures", fake_validate)
    doc = await stage(db, make_pdf())
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert result.outcome == d.STATUS_REVIEW_REQUIRED
    assert any(f["code"] == "SIGNATURE_PRESENT_UNVERIFIED" for f in result.findings)


@pytest.mark.asyncio
async def test_a_trusted_signature_raises_no_finding(db, clean_scanner, monkeypatch):
    from app.services.remote_audit.signatures import SignatureReport, SignatureTrust

    def fake_validate(_data, **_kwargs):
        return SignatureReport(overall=SignatureTrust.TRUSTED, anchors_configured=1, signatures=[])

    monkeypatch.setattr(pipeline.signature_service, "validate_pdf_signatures", fake_validate)
    doc = await stage(db, make_pdf())
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert not any(f["code"].startswith("SIGNATURE_") for f in result.findings)


@pytest.mark.asyncio
async def test_an_unreadable_signature_is_not_reported_as_unsigned(db, clean_scanner, monkeypatch):
    from app.services.remote_audit.signatures import SignatureReport, SignatureTrust

    def fake_validate(_data, **_kwargs):
        return SignatureReport(overall=SignatureTrust.UNKNOWN, anchors_configured=1,
                               error="could not read signatures")

    monkeypatch.setattr(pipeline.signature_service, "validate_pdf_signatures", fake_validate)
    doc = await stage(db, make_pdf())
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert any(f["code"] == "SIGNATURE_UNKNOWN" for f in result.findings)
    assert result.outcome == d.STATUS_REVIEW_REQUIRED


# ── 5. Extraction is a suggestion ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_text_layer_extraction_lands_in_extracted_not_confirmed(db, clean_scanner):
    doc = await stage(db, make_pdf(
        "Calibration Certificate\nCertificate No: CAL-2026-771\n"
        "Calibration Date: 2026-01-15\nDue Date: 2027-01-15"))
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert result.extraction is not None
    assert result.extraction["source"] == "pdf_text_layer"
    after = await d.get_document(db, doc["id"])
    assert after["extracted"]["fields"]["certificate_number"] == "CAL-2026-771"
    # Extracted is never confirmed, and never drives expiry on its own.
    assert after["confirmed"] is None
    assert after["expires_on"] is None


@pytest.mark.asyncio
async def test_vision_provider_failure_degrades_to_manual_entry(db, clean_scanner, monkeypatch):
    """Correction #15's per-provider rule: an unavailable extraction model is
    not a processing failure. The reviewer types the fields instead."""
    async def boom(**_kwargs):
        raise RuntimeError("provider exploded")

    import app.services.remote_audit.vision_extraction as ve
    monkeypatch.setattr(ve, "analyze_certificate_photo", boom)

    # A scan with no text layer, so the vision path is the only one available.
    doc = await stage(db, make_jpeg(), media_type="image/jpeg")
    result = await pipeline.run_document_processing(
        db, doc["id"], policy=pipeline.DocumentPolicy(enable_vision_extraction=True))

    assert result.outcome != d.STATUS_PROCESSING_FAILED
    assert result.extraction is None


# ── Ambiguous dates ─────────────────────────────────────────────────────────

def test_ambiguous_slash_date_is_never_silently_interpreted():
    """`03/04/2026` is 3 April or 4 March depending on the reader. Guessing
    would move a calibration expiry by a month without anyone noticing."""
    iso, code = pipeline.classify_date_string("03/04/2026")
    assert iso is None
    assert code == "AMBIGUOUS_DATE_FORMAT"


def test_unambiguous_slash_date_is_parsed():
    """Only one reading is possible when a component exceeds 12, so refusing
    it would be uselessly conservative."""
    assert pipeline.classify_date_string("25/12/2026")[0] == "2026-12-25"
    assert pipeline.classify_date_string("12/25/2026")[0] == "2026-12-25"


def test_iso_date_is_parsed_exactly():
    assert pipeline.classify_date_string("2026-09-17")[0] == "2026-09-17"
    assert pipeline.classify_date_string("Due: 2026-09-17 (see overleaf)")[0] == "2026-09-17"


def test_nonsense_dates_report_a_code_rather_than_a_value():
    assert pipeline.classify_date_string("sometime next spring") == (None, "UNRECOGNIZED_DATE_FORMAT")
    assert pipeline.classify_date_string("2026-13-45")[0] is None


@pytest.mark.asyncio
async def test_an_ambiguous_extracted_date_forces_review(db, clean_scanner):
    doc = await stage(db, make_pdf(
        "Calibration Certificate\nCertificate No: CAL-9\nDue Date: 03/04/2026"))
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert result.outcome == d.STATUS_REVIEW_REQUIRED
    assert any(f["code"] == "AMBIGUOUS_DATE_FORMAT" for f in result.findings)
    # The raw string is preserved so the reviewer can see what was printed.
    assert result.extraction["fields"]["due_date_raw"] == "03/04/2026"
    assert result.extraction["fields"]["due_date"] is None


@pytest.mark.asyncio
async def test_a_due_date_before_the_calibration_date_is_flagged(db, clean_scanner):
    doc = await stage(db, make_pdf(
        "Calibration Certificate\nCalibration Date: 2026-06-01\nDue Date: 2026-01-01"))
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
    assert any(f["code"] == "DUE_BEFORE_CALIBRATION" for f in result.findings)


@pytest.mark.asyncio
async def test_an_already_expired_certificate_is_flagged_at_upload(db, clean_scanner):
    doc = await stage(db, make_pdf(
        "Calibration Certificate\nCalibration Date: 2019-01-01\nDue Date: 2020-01-01"))
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
    assert any(f["code"] == "CERTIFICATE_ALREADY_EXPIRED" for f in result.findings)
    assert result.outcome == d.STATUS_REVIEW_REQUIRED


# ── Append-only re-runs ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reprocessing_adds_a_check_run_and_keeps_the_previous_one(db, clean_scanner):
    doc = await stage(db, make_pdf("Report"))
    await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
    first = await d.latest_check(db, doc["id"])

    await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
    second = await d.latest_check(db, doc["id"])

    assert second["check_run_id"] == first["check_run_id"] + 1
    assert second["supersedes_check_id"] == first["id"]
    assert len(await d.list_checks(db, doc["id"])) == 2


@pytest.mark.asyncio
async def test_reprocessing_refuses_to_overrule_a_human_decision(db, clean_scanner):
    """A worker re-run is a recomputation. Recomputation must never un-accept
    a document a reviewer accepted, or resurrect one they rejected — those are
    decisions, and a stale job landing late must not undo them."""
    doc = await stage(db, make_pdf("Report"))
    await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)
    current = await d.get_document(db, doc["id"])
    await d.transition(db, doc["id"], d.STATUS_ACCEPTED, expected_status=current["status"])

    with pytest.raises(pipeline.DocumentAlreadyDecidedError):
        await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert (await d.get_document(db, doc["id"]))["status"] == d.STATUS_ACCEPTED


# ── The development escape hatch (REMOTE_AUDIT_REQUIRE_MALWARE_SCAN=false) ───

@pytest.mark.asyncio
@pytest.mark.skipif(scanner_is_configured(), reason="requires NO scanner configured")
async def test_with_scanning_disabled_a_document_becomes_visible_but_marked_unscanned(
    db, monkeypatch,
):
    """The dev flag removes the BLOCK, not the fact.

    The document reaches a reviewer so the feature can be demonstrated without
    standing up ClamAV — but it is labelled NOT_SCANNED, carries a high-severity
    finding, and is forced to review. It is never reported CLEAN.
    """
    monkeypatch.setenv("REMOTE_AUDIT_REQUIRE_MALWARE_SCAN", "false")
    doc = await stage(db, make_pdf("Annual Inspection Report"))

    # No exception: processing continues rather than blocking.
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert result.outcome == d.STATUS_REVIEW_REQUIRED
    assert result.checks["malware_status"] == "NOT_SCANNED"
    assert result.checks["malware_status"] != "CLEAN"
    finding = next(f for f in result.findings if f["code"] == "FILE_NOT_SCANNED")
    assert finding["severity"] == "high"

    after = await d.get_document(db, doc["id"])
    # Visible to a reviewer, which is the whole point of the flag...
    assert d.is_visible_to_reviewer(after["status"]) is True
    # ...but never presented as technically valid.
    assert after["status"] != d.STATUS_TECHNICALLY_VALID


@pytest.mark.asyncio
async def test_detected_malware_still_blocks_with_scanning_disabled(db, monkeypatch):
    """The flag governs an ABSENT scanner. A scanner that ran and found
    something is completely unaffected by it."""
    monkeypatch.setenv("REMOTE_AUDIT_REQUIRE_MALWARE_SCAN", "false")

    async def detected(_data):
        return file_checks.MalwareScanStatus.DETECTED, "Eicar-Test-Signature"
    monkeypatch.setattr(pipeline.file_checks, "scan", detected)

    doc = await stage(db, make_pdf())
    result = await pipeline.run_document_processing(db, doc["id"], policy=NO_VISION)

    assert result.outcome == d.STATUS_REJECTED
    assert result.content_blocked is True
