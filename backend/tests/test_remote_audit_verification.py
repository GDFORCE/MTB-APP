"""Evidence verification engine tests (app/services/remote_audit/verification.py)
— spec §19.2's A1-A15 acceptance scenarios (A12/A13 are capture-session/
finalize-layer concerns, tested alongside the evidence pipeline instead of
here) plus full §15.2 decision-table branch coverage.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.remote_audit.attestation import AttestationTier  # noqa: E402
from app.services.remote_audit.geofence import GeoStatus  # noqa: E402
from app.services.remote_audit.verification import (  # noqa: E402
    CaptureMode,
    OverallState,
    VerificationSignals,
    evaluate,
)


def base_signals(**overrides) -> VerificationSignals:
    defaults = dict(
        hash_match=True, mime_match=True, malware_detected=False,
        capture_session_replayed=False, wrong_site_or_user=False,
        attestation=AttestationTier.VERIFIED_HIGH,
        geo_status=GeoStatus.WITHIN_BOUNDARY,
        android_is_mock=False, ios_is_simulated=False,
        exif_gps_present=True, exif_device_distance_m=5.0, exif_mismatch_threshold_m=100.0,
        timestamp_plausible=True, duplicate_sha256=False, duplicate_visual_candidate=False,
        image_quality_acceptable=True,
        capture_mode=CaptureMode.LIVE_IN_APP, current_presence_required=True,
        treat_attestation_unavailable_as_review=True,
    )
    defaults.update(overrides)
    return VerificationSignals(**defaults)


def reason_codes(result):
    return {r.code for r in result.reasons}


# A1: fresh GPS inside polygon, healthy attestation -> PASS ------------------

def test_a1_live_photo_fresh_gps_inside_boundary_healthy_attestation_passes():
    result = evaluate(base_signals())
    assert result.overall_state == OverallState.PASS


# A2: inside building, low-confidence accuracy crossing boundary -----------

def test_a2_within_low_confidence_is_not_automatically_rejected():
    result = evaluate(base_signals(geo_status=GeoStatus.WITHIN_LOW_CONFIDENCE))
    assert result.overall_state == OverallState.PASS
    assert "LOCATION_LOW_CONFIDENCE" in reason_codes(result)


# A3: 300m outside, good accuracy -> reviewer query --------------------------

def test_a3_outside_boundary_live_capture_is_reviewed():
    result = evaluate(base_signals(geo_status=GeoStatus.OUTSIDE_BOUNDARY))
    assert result.overall_state == OverallState.REVIEW
    assert "LOCATION_OUTSIDE_BOUNDARY" in reason_codes(result)


# A4: no EXIF GPS but fresh manifest GPS inside -> does not fail solely for EXIF -

def test_a4_missing_exif_gps_does_not_fail_when_manifest_location_is_good():
    result = evaluate(base_signals(exif_gps_present=False, exif_device_distance_m=None))
    assert result.overall_state == OverallState.PASS
    assert "EXIF_GPS_ABSENT" in reason_codes(result)


# A5: gallery photo, EXIF inside site but uploaded from elsewhere -----------

def test_a5_gallery_capture_without_current_presence_requirement_is_limited_confidence():
    result = evaluate(base_signals(capture_mode=CaptureMode.GALLERY_HISTORICAL, current_presence_required=False))
    assert result.overall_state == OverallState.PASS_WITH_LIMITED_CONFIDENCE
    assert "GALLERY_CAPTURE" in reason_codes(result)


def test_a5b_gallery_capture_when_current_presence_is_required_is_reviewed():
    result = evaluate(base_signals(capture_mode=CaptureMode.GALLERY_HISTORICAL, current_presence_required=True))
    assert result.overall_state == OverallState.REVIEW


# A6: EXIF differs 5km from fresh device GPS -> mismatch flagged, supporting only -

def test_a6_exif_device_mismatch_is_flagged_as_a_supporting_reason():
    result = evaluate(base_signals(exif_device_distance_m=5000.0, exif_mismatch_threshold_m=100.0))
    assert "EXIF_DEVICE_MISMATCH" in reason_codes(result)
    # supporting only -- does not by itself force FAIL_TECHNICAL/HOLD
    assert result.overall_state in (OverallState.PASS, OverallState.REVIEW)


# A7/A8: mock/simulated location -> high-risk review -------------------------

def test_a7_android_is_mock_forces_review():
    result = evaluate(base_signals(android_is_mock=True))
    assert result.overall_state == OverallState.REVIEW
    assert "LOCATION_SIMULATION_SIGNAL" in reason_codes(result)


def test_a8_ios_is_simulated_forces_review():
    result = evaluate(base_signals(ios_is_simulated=True))
    assert result.overall_state == OverallState.REVIEW


# A9: attestation unavailable, everything else good -> configurable REVIEW, not fraud -

def test_a9_attestation_unavailable_is_review_not_fail_technical_by_default():
    result = evaluate(base_signals(attestation=AttestationTier.UNAVAILABLE))
    assert result.overall_state == OverallState.REVIEW
    assert result.overall_state not in (OverallState.FAIL_TECHNICAL, OverallState.HOLD)
    assert "ATTESTATION_UNAVAILABLE" in reason_codes(result)


def test_a9b_attestation_unavailable_can_be_configured_to_pass_through():
    """'Configurable', per spec -- a deployment with real attestation live
    can choose not to force review on the rare genuine outage."""
    result = evaluate(base_signals(attestation=AttestationTier.UNAVAILABLE, treat_attestation_unavailable_as_review=False))
    assert result.overall_state == OverallState.PASS


# A10: client hash differs from server hash -> technical fail ---------------

def test_a10_hash_mismatch_is_fail_technical():
    result = evaluate(base_signals(hash_match=False))
    assert result.overall_state == OverallState.FAIL_TECHNICAL
    assert "EVIDENCE_HASH_MISMATCH" in reason_codes(result)


# A11: same SHA-256 reused at another site -> duplicate critical review flag -

def test_a11_duplicate_sha256_forces_review():
    result = evaluate(base_signals(duplicate_sha256=True))
    assert result.overall_state == OverallState.REVIEW
    assert "DUPLICATE_EXACT" in reason_codes(result)


# A14: reduced/approximate iOS location -> low confidence path --------------

def test_a14_low_accuracy_location_routes_to_review():
    result = evaluate(base_signals(geo_status=GeoStatus.LOCATION_LOW_ACCURACY))
    assert result.overall_state == OverallState.REVIEW


# A15: basement, no GPS -> low geo confidence, not automatic fraud ----------

def test_a15_missing_location_is_review_not_fraud():
    result = evaluate(base_signals(geo_status=None))
    assert result.overall_state == OverallState.REVIEW
    assert "LOCATION_MISSING" in reason_codes(result)
    assert result.overall_state != OverallState.FAIL_TECHNICAL


# Additional §15.2 branch coverage not directly named A1-A15 -----------------

def test_malware_detected_is_fail_technical_and_takes_priority_over_everything():
    result = evaluate(base_signals(malware_detected=True, hash_match=False, capture_session_replayed=True))
    assert result.overall_state == OverallState.FAIL_TECHNICAL
    assert "MALWARE_DETECTED" in reason_codes(result)


def test_mime_mismatch_is_fail_technical():
    result = evaluate(base_signals(mime_match=False))
    assert result.overall_state == OverallState.FAIL_TECHNICAL


def test_replayed_capture_session_is_hold():
    result = evaluate(base_signals(capture_session_replayed=True))
    assert result.overall_state == OverallState.HOLD
    assert "REPLAY_REJECTED" in reason_codes(result)


def test_wrong_site_or_user_is_hold():
    result = evaluate(base_signals(wrong_site_or_user=True))
    assert result.overall_state == OverallState.HOLD


def test_tampered_app_defaults_to_review_not_hold():
    result = evaluate(base_signals(attestation=AttestationTier.TAMPERED))
    assert result.overall_state == OverallState.REVIEW
    assert "APP_TAMPER_VERDICT" in reason_codes(result)


def test_tampered_app_can_be_policy_configured_to_block_outright():
    """v1.3 correction #8: a tampered app is a different risk than an old-OS
    device and MAY be configured to block, unlike old-OS which never does."""
    result = evaluate(base_signals(attestation=AttestationTier.TAMPERED, block_on_tampered_app=True))
    assert result.overall_state == OverallState.HOLD


def test_old_os_verified_medium_never_blocks_outright():
    """An old/unsupported-OS device (VERIFIED_MEDIUM) is a different risk
    than TAMPERED and must never be grouped with it (v1.3 correction #8)."""
    result = evaluate(base_signals(attestation=AttestationTier.VERIFIED_MEDIUM))
    assert result.overall_state == OverallState.PASS


def test_geo_suspicious_signal_overrides_everything_to_suspicious_review():
    result = evaluate(base_signals(geo_status=GeoStatus.SUSPICIOUS_REVIEW))
    assert result.overall_state == OverallState.SUSPICIOUS_REVIEW


def test_no_binary_gps_verified_equals_authentic_shortcut():
    """Explicit guard against the spec's central prohibited shortcut: a
    perfect geo result with a critical signal elsewhere must still be
    reviewed, and a bad geo result never gets silently overridden by good
    attestation."""
    good_geo_bad_attestation = evaluate(base_signals(geo_status=GeoStatus.WITHIN_BOUNDARY, attestation=AttestationTier.TAMPERED))
    assert good_geo_bad_attestation.overall_state != OverallState.PASS

    bad_geo_good_everything_else = evaluate(base_signals(geo_status=GeoStatus.OUTSIDE_BOUNDARY))
    assert bad_geo_good_everything_else.overall_state != OverallState.PASS


def test_every_result_carries_reasons_and_rule_set_version():
    """spec §15.1 contract: never expose only a bare score."""
    for overrides in (
        {},
        {"malware_detected": True},
        {"geo_status": None},
        {"duplicate_sha256": True},
    ):
        result = evaluate(base_signals(**overrides))
        assert result.reasons, f"no reasons for {overrides}"
        assert result.rule_set_version
        assert all(r.explanation for r in result.reasons)


# ── Unscanned files (REMOTE_AUDIT_REQUIRE_MALWARE_SCAN=false, dev only) ──────

def _skipped(**over):
    """A signals set that would otherwise PASS, with the scan skipped."""
    base = dict(
        hash_match=True, mime_match=True, malware_detected=False,
        capture_session_replayed=False, wrong_site_or_user=False,
        attestation=AttestationTier.VERIFIED_HIGH,
        geo_status=GeoStatus.WITHIN_BOUNDARY,
        android_is_mock=False, ios_is_simulated=False,
        exif_gps_present=True, exif_device_distance_m=5.0, exif_mismatch_threshold_m=100.0,
        timestamp_plausible=True, duplicate_sha256=False, duplicate_visual_candidate=False,
        image_quality_acceptable=True, capture_mode=CaptureMode.LIVE_IN_APP,
        current_presence_required=True,
        treat_attestation_unavailable_as_review=False,
        malware_scan_skipped=True,
    )
    base.update(over)
    return VerificationSignals(**base)


def test_an_unscanned_file_can_never_reach_pass():
    """The whole point of the dev flag: it removes the BLOCK, not the fact.
    "Nobody looked" is not "we looked and it was fine"."""
    result = evaluate(_skipped())
    assert result.overall_state == OverallState.REVIEW
    assert result.overall_state != OverallState.PASS
    assert any(r.code == "FILE_NOT_SCANNED" for r in result.reasons)


def test_an_unscanned_file_is_reported_NOT_SCANNED_never_CLEAN():
    """A reviewer reading the signals panel must not see 'CLEAN' for a file
    no scanner ever touched."""
    result = evaluate(_skipped())
    assert result.signals["malware_status"] == "NOT_SCANNED"
    assert result.signals["malware_status"] != "CLEAN"


def test_skipping_the_scan_lowers_the_ceiling_but_hides_nothing_else():
    """Every other finding is still computed and shown — the flag changes the
    ceiling, not the analysis."""
    result = evaluate(_skipped(geo_status=GeoStatus.OUTSIDE_BOUNDARY))
    codes = {r.code for r in result.reasons}
    assert "FILE_NOT_SCANNED" in codes
    assert any("GEO" in code or "BOUNDARY" in code for code in codes), codes


def test_a_real_failure_still_outranks_the_unscanned_floor():
    """The floor raises PASS to REVIEW. It must never LOWER a worse verdict
    to REVIEW and thereby hide it."""
    tampered = evaluate(_skipped(hash_match=False))
    assert tampered.overall_state == OverallState.FAIL_TECHNICAL

    replayed = evaluate(_skipped(capture_session_replayed=True))
    assert replayed.overall_state == OverallState.HOLD


def test_detected_malware_still_fails_even_with_the_flag_off():
    """The flag governs an ABSENT scanner. A scanner that ran and found
    something is untouched by it."""
    result = evaluate(_skipped(malware_detected=True, malware_scan_skipped=False))
    assert result.overall_state == OverallState.FAIL_TECHNICAL
    assert result.signals["malware_status"] == "DETECTED"


def test_scanning_is_required_by_default(monkeypatch):
    """The safe value is the default. A deployment that never sets the
    variable gets strict gating."""
    from app.services.remote_audit import file_checks

    monkeypatch.delenv(file_checks.REQUIRE_SCAN_ENV, raising=False)
    assert file_checks.scan_is_required() is True


def test_only_explicit_falsey_values_disable_scanning(monkeypatch):
    from app.services.remote_audit import file_checks

    for value in ("false", "False", "0", "no", "off"):
        monkeypatch.setenv(file_checks.REQUIRE_SCAN_ENV, value)
        assert file_checks.scan_is_required() is False, value
    # Anything else — including a typo — keeps the gate on.
    for value in ("true", "1", "yes", "", "flase", "maybe"):
        monkeypatch.setenv(file_checks.REQUIRE_SCAN_ENV, value)
        assert file_checks.scan_is_required() is True, value
