"""Play Integrity verdict classification — spec §3.3/§17.3, Phase 8 (Android).

The tests that matter most here are the BINDING ones. A verdict that verifies
cryptographically but belongs to a different capture, or a different app, or
was issued yesterday, is not a pass — it is a replayed or borrowed token. Every
one of those was accepted before the Phase 8 hardening; each now has a test
that fails against that earlier behaviour.

Pure classification, so no DB or network fixture is needed. The live Google
call is the one thing that genuinely needs a real service account, and it is
isolated in `_google_service_account_access_token` for exactly that reason.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.remote_audit.attestation import (  # noqa: E402
    AttestationTier,
    classify_play_integrity_verdict,
    verify_app_attest,
    verify_play_integrity_token,
)

NONCE = "a3f1c8e29b7d4506a3f1c8e29b7d4506"
PACKAGE = "com.gdforce.mytrialboard"
NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)


def verdict(
    *,
    nonce: str = NONCE,
    package: str = PACKAGE,
    app_verdict: str = "PLAY_RECOGNIZED",
    device: list | None = None,
    issued_at: datetime | None = None,
) -> dict:
    """A realistic decoded verdict, shaped like Google's actual response."""
    moment = issued_at or NOW
    return {
        "requestDetails": {
            "requestPackageName": package,
            "nonce": nonce,
            "timestampMillis": str(int(moment.timestamp() * 1000)),
        },
        "appIntegrity": {
            "appRecognitionVerdict": app_verdict,
            "packageName": package,
            "certificateSha256Digest": ["6a6a1474b5cbbb2b1aa57e0bc3"],
            "versionCode": "42",
        },
        "deviceIntegrity": {
            "deviceRecognitionVerdict": device if device is not None else ["MEETS_DEVICE_INTEGRITY"],
        },
    }


def classify(v: dict, *, expected_nonce: str | None = NONCE, expected_package: str | None = PACKAGE):
    return classify_play_integrity_verdict(
        v, expected_nonce=expected_nonce, expected_package=expected_package, now=NOW)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Nonce binding — the replay defence
# ═══════════════════════════════════════════════════════════════════════════

def test_a_matching_nonce_and_strong_device_verifies_high():
    result = classify(verdict())
    assert result.tier == AttestationTier.VERIFIED_HIGH


def test_a_verdict_for_a_different_capture_is_tampered_not_verified():
    """THE replay case. Before the binding check, a token lifted from one
    capture verified perfectly against every other one, and the whole control
    was decorative. A strong device verdict must not rescue it."""
    result = classify(verdict(nonce="ffffffffffffffffffffffffffffffff"))
    assert result.tier == AttestationTier.TAMPERED
    assert "replayed" in result.reason
    # Note what the verdict itself said — this is a perfect device integrity
    # result that is nonetheless rejected, which is the point.
    assert "MEETS_DEVICE_INTEGRITY" in result.raw_verdict["deviceIntegrity"]["deviceRecognitionVerdict"]


def test_a_verdict_with_no_nonce_cannot_be_bound_and_is_tampered():
    v = verdict()
    del v["requestDetails"]["nonce"]
    result = classify(v)
    assert result.tier == AttestationTier.TAMPERED
    assert "cannot be tied to this capture" in result.reason


def test_a_token_with_nothing_to_bind_it_to_is_not_a_pass():
    """A caller that supplies no expected nonce gets UNAVAILABLE, not
    VERIFIED. An unbound verdict is not evidence about anything."""
    result = classify(verdict(), expected_nonce=None)
    assert result.tier == AttestationTier.UNAVAILABLE
    assert "could not be bound" in result.reason


def test_nonce_comparison_is_exact():
    for wrong in (NONCE.upper(), NONCE[:-1], NONCE + "0", " " + NONCE):
        assert classify(verdict(nonce=wrong)).tier == AttestationTier.TAMPERED, wrong


# ═══════════════════════════════════════════════════════════════════════════
# 2. Package binding — is this verdict even about our app?
# ═══════════════════════════════════════════════════════════════════════════

def test_a_verdict_from_another_app_is_tampered():
    """A perfectly valid Play Integrity token from someone else's app proves
    nothing about ours."""
    result = classify(verdict(package="com.someone.else"))
    assert result.tier == AttestationTier.TAMPERED
    assert "com.someone.else" in result.reason


def test_a_mismatch_in_either_package_field_is_caught():
    v = verdict()
    v["appIntegrity"]["packageName"] = "com.someone.else"
    result = classify(v)
    assert result.tier == AttestationTier.TAMPERED
    assert "appIntegrity.packageName" in result.reason


# ═══════════════════════════════════════════════════════════════════════════
# 3. Freshness
# ═══════════════════════════════════════════════════════════════════════════

def test_a_stale_verdict_is_tampered():
    """A verdict from yesterday says nothing about the capture happening now."""
    result = classify(verdict(issued_at=NOW - timedelta(days=1)))
    assert result.tier == AttestationTier.TAMPERED
    assert "freshness window" in result.reason


def test_a_recent_verdict_passes_the_freshness_check():
    result = classify(verdict(issued_at=NOW - timedelta(seconds=90)))
    assert result.tier == AttestationTier.VERIFIED_HIGH


def test_a_verdict_from_the_future_is_also_rejected():
    """Clock skew of a few minutes is tolerated; a verdict hours ahead is a
    forged timestamp, not a slow watch."""
    result = classify(verdict(issued_at=NOW + timedelta(hours=3)))
    assert result.tier == AttestationTier.TAMPERED


def test_an_unreadable_timestamp_is_rejected_rather_than_ignored():
    v = verdict()
    v["requestDetails"]["timestampMillis"] = "not-a-number"
    result = classify(v)
    assert result.tier == AttestationTier.TAMPERED
    assert "unreadable verdict timestamp" in result.reason


# ═══════════════════════════════════════════════════════════════════════════
# 4. The verdict itself — old device vs tampered build (v1.3 correction #8)
# ═══════════════════════════════════════════════════════════════════════════

def test_an_old_institutional_device_is_medium_not_tampered():
    """A hospital's five-year-old Android tablet is not an attacker. It fails
    the strongest tier and reaches a REVIEW-tier outcome, which is a different
    thing from being accused of tampering."""
    result = classify(verdict(device=["MEETS_BASIC_INTEGRITY"]))
    assert result.tier == AttestationTier.VERIFIED_MEDIUM
    assert result.tier != AttestationTier.TAMPERED


def test_a_modified_app_build_is_tampered():
    result = classify(verdict(app_verdict="UNRECOGNIZED_VERSION"))
    assert result.tier == AttestationTier.TAMPERED
    assert "UNRECOGNIZED_VERSION" in result.reason


def test_an_unevaluated_app_is_tampered():
    assert classify(verdict(app_verdict="UNEVALUATED")).tier == AttestationTier.TAMPERED


def test_no_device_integrity_signal_at_all_is_tampered():
    """An emulator typically produces an empty verdict list."""
    result = classify(verdict(device=[]))
    assert result.tier == AttestationTier.TAMPERED
    assert "no recognized device integrity signal" in result.reason


def test_the_two_failure_modes_reach_distinguishable_reasons():
    """Correction #8 requires old-OS and tampered-build to be separable, not
    collapsed under one outcome a policy can't tell apart."""
    old_device = classify(verdict(device=["MEETS_BASIC_INTEGRITY"]))
    tampered_build = classify(verdict(app_verdict="UNRECOGNIZED_VERSION"))
    assert old_device.tier != tampered_build.tier
    assert old_device.reason != tampered_build.reason


# ═══════════════════════════════════════════════════════════════════════════
# 5. Ordering — binding beats the verdict
# ═══════════════════════════════════════════════════════════════════════════

def test_binding_failures_are_reported_ahead_of_a_weak_verdict():
    """A replayed token about a weak device is reported as a replay, not as a
    weak device. The first is an attack; the second is an old phone, and
    conflating them would hide the attack in the noise."""
    result = classify(verdict(nonce="0" * 32, device=["MEETS_BASIC_INTEGRITY"]))
    assert result.tier == AttestationTier.TAMPERED
    assert "replayed" in result.reason


# ═══════════════════════════════════════════════════════════════════════════
# 6. Availability and graceful degradation
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_no_token_is_unavailable_not_a_failure():
    """The normal case today: no native bridge in this build, so no token.
    Routed to review, never treated as an accusation."""
    result = await verify_play_integrity_token(None, expected_nonce=NONCE)
    assert result.tier == AttestationTier.UNAVAILABLE
    assert "no attestation token" in result.reason


@pytest.mark.asyncio
async def test_an_unconfigured_deployment_is_unavailable_not_verified(monkeypatch):
    """The failure that would matter most: an unconfigured server must never
    treat a token it cannot check as good."""
    monkeypatch.delenv("GOOGLE_PLAY_INTEGRITY_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("GOOGLE_PLAY_INTEGRITY_PACKAGE_NAME", raising=False)
    result = await verify_play_integrity_token("a-real-looking-token", expected_nonce=NONCE)
    assert result.tier == AttestationTier.UNAVAILABLE
    assert result.tier != AttestationTier.VERIFIED_HIGH


@pytest.mark.asyncio
async def test_a_provider_outage_degrades_rather_than_raising(monkeypatch):
    """An exception escaping here would break the capture pipeline for every
    tenant over a Google outage."""
    monkeypatch.setenv("GOOGLE_PLAY_INTEGRITY_CREDENTIALS_PATH", "/nonexistent/creds.json")
    monkeypatch.setenv("GOOGLE_PLAY_INTEGRITY_PACKAGE_NAME", PACKAGE)
    result = await verify_play_integrity_token("token", expected_nonce=NONCE)
    assert result.tier == AttestationTier.UNAVAILABLE
    assert "Play Integrity call failed" in result.reason


@pytest.mark.asyncio
async def test_app_attest_is_honestly_unavailable_not_silently_passing():
    """iOS is deliberately not implemented. It must report that, not return
    something that reads as a pass."""
    result = await verify_app_attest(b"\x00\x01")
    assert result.tier == AttestationTier.UNAVAILABLE
    assert "not yet implemented" in result.reason


def test_the_freshness_window_is_configurable(monkeypatch):
    monkeypatch.setenv("REMOTE_AUDIT_ATTESTATION_MAX_AGE_SECONDS", "30")
    stale = classify(verdict(issued_at=NOW - timedelta(seconds=60)))
    assert stale.tier == AttestationTier.TAMPERED
    fresh = classify(verdict(issued_at=NOW - timedelta(seconds=10)))
    assert fresh.tier == AttestationTier.VERIFIED_HIGH


def test_a_malformed_verdict_never_raises():
    """Whatever Google returns, this has to produce a tier rather than an
    exception — it runs inside the capture pipeline."""
    for payload in ({}, {"requestDetails": None}, {"appIntegrity": "nonsense"},
                    {"deviceIntegrity": {"deviceRecognitionVerdict": None}}):
        result = classify_play_integrity_verdict(payload, expected_nonce=None, expected_package=None)
        assert result.tier in tuple(AttestationTier)
