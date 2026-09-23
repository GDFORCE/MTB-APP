"""Device/app attestation verification — Play Integrity (Android) and App
Attest (iOS), spec §3.3/§17.3.

Play Integrity is the one signal in the capture chain that an attacker running
a modified build cannot forge: the verdict is signed by Google and verified
here against Google's API, rather than the app vouching for itself.

THREE BINDINGS MAKE THE VERDICT MEAN ANYTHING (Phase 8):

  1. **Nonce** — the verdict echoes the nonce the app asked with. It must equal
     the capture session's own nonce, or the token belongs to a different
     capture. Without this check a valid token could be lifted from one
     capture and replayed against every other one, and the whole control
     becomes decorative.
  2. **Package name** — both `requestDetails.requestPackageName` and
     `appIntegrity.packageName` must be our app. A perfectly valid token from
     some other app proves nothing about ours.
  3. **Freshness** — a verdict from days ago says nothing about now.

A token that verifies cryptographically but fails any of those is reported
TAMPERED, not VERIFIED. "Google signed something" is not "Google signed this".

Env-gated per docs/remote-audit/provider-dependencies.md: unconfigured, or any
provider/network failure, returns AttestationTier.UNAVAILABLE — never an
exception that would break the capture pipeline, and never silently treated as
verified.

Honest scope note: Apple App Attest's full verification (CBOR/COSE parsing +
certificate-chain validation against Apple's root CA) is deliberately NOT
implemented. Getting that cryptographic validation wrong would be worse than
clearly deferring it, and it cannot be meaningfully tested without a real
device-produced attestation object. `verify_app_attest` always returns
UNAVAILABLE; the TODO is tracked here, not hidden.
"""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

import httpx

PACKAGE_NAME_ENV = "GOOGLE_PLAY_INTEGRITY_PACKAGE_NAME"
CREDENTIALS_PATH_ENV = "GOOGLE_PLAY_INTEGRITY_CREDENTIALS_PATH"
MAX_VERDICT_AGE_ENV = "REMOTE_AUDIT_ATTESTATION_MAX_AGE_SECONDS"

#: How stale a verdict may be. Generous enough for a slow upload over hospital
#: wifi, short enough that a token captured yesterday is useless.
DEFAULT_MAX_VERDICT_AGE_SECONDS = 600


class AttestationTier(str, Enum):
    VERIFIED_HIGH = "VERIFIED_HIGH"          # strong integrity verdict from the platform
    VERIFIED_MEDIUM = "VERIFIED_MEDIUM"      # legitimate but on an older/unsupported-OS device
    TAMPERED = "TAMPERED"                    # modified/unrecognized build, or a mis-bound verdict
    UNAVAILABLE = "UNAVAILABLE"              # not configured, provider outage, or no token supplied


@dataclass(frozen=True)
class AttestationResult:
    tier: AttestationTier
    raw_verdict: Optional[Dict[str, Any]] = None
    reason: str = ""


def _play_integrity_configured() -> bool:
    return bool(os.environ.get(CREDENTIALS_PATH_ENV) and os.environ.get(PACKAGE_NAME_ENV))


def _max_verdict_age_seconds() -> int:
    raw = os.environ.get(MAX_VERDICT_AGE_ENV)
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_VERDICT_AGE_SECONDS
    except ValueError:
        return DEFAULT_MAX_VERDICT_AGE_SECONDS


async def verify_play_integrity_token(
    token: Optional[str],
    *,
    expected_nonce: Optional[str] = None,
    now: Optional[datetime] = None,
) -> AttestationResult:
    """Decode a Play Integrity token via Google's REST API and verify it is
    bound to THIS capture.

    `expected_nonce` is the capture session's nonce. It is optional only so
    the pre-Phase-8 callers that never had a token keep working; when a token
    IS present and no nonce is supplied, the result is downgraded rather than
    trusted — a verdict nobody bound to anything is not evidence.

    Returns UNAVAILABLE (never raises) if unconfigured, no token was supplied,
    or the provider call fails for any reason — per the per-provider failure
    mapping in docs/remote-audit/provider-dependencies.md, that is a REVIEW-tier
    signal, not a hard block.
    """
    if not token:
        return AttestationResult(AttestationTier.UNAVAILABLE, reason="no attestation token supplied")
    if not _play_integrity_configured():
        return AttestationResult(AttestationTier.UNAVAILABLE, reason="Play Integrity not configured")

    package_name = os.environ[PACKAGE_NAME_ENV]
    try:
        access_token = await _google_service_account_access_token(
            os.environ[CREDENTIALS_PATH_ENV],
            scope="https://www.googleapis.com/auth/playintegrity",
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"https://playintegrity.googleapis.com/v1/{package_name}:decodeIntegrityToken",
                json={"integrityToken": token},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 -- any provider failure degrades to UNAVAILABLE, never raises
        return AttestationResult(
            AttestationTier.UNAVAILABLE, reason=f"Play Integrity call failed: {type(exc).__name__}: {exc}",
        )

    verdict = payload.get("tokenPayloadExternal") or payload
    return classify_play_integrity_verdict(
        verdict, expected_nonce=expected_nonce, expected_package=package_name, now=now)


def classify_play_integrity_verdict(
    verdict: Dict[str, Any],
    *,
    expected_nonce: Optional[str] = None,
    expected_package: Optional[str] = None,
    now: Optional[datetime] = None,
) -> AttestationResult:
    """Turn a decoded verdict into a tier.

    Binding checks run FIRST. A verdict that says MEETS_DEVICE_INTEGRITY about
    a different capture, or a different app, is not a pass — it is a replayed
    or borrowed token, which is a stronger signal of trouble than a weak
    device verdict about the right capture.

    Then v1.3 correction #8: an old/unsupported-OS device is a different risk
    from a tampered/unrecognized app build, and the two never collapse into
    one outcome.
    """
    # `or {}` is not enough: a non-empty string is truthy, so a malformed
    # `{"appIntegrity": "nonsense"}` would sail through and blow up on .get().
    # This runs inside the capture pipeline on provider-supplied JSON, so the
    # type has to be checked, not assumed.
    def _mapping(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    request_details = _mapping(verdict.get("requestDetails"))
    app_integrity = _mapping(verdict.get("appIntegrity"))
    raw_device = _mapping(verdict.get("deviceIntegrity")).get("deviceRecognitionVerdict")
    device_integrity = raw_device if isinstance(raw_device, list) else []

    # ── 1. Nonce binding: is this verdict about THIS capture? ─────────────
    echoed_nonce = request_details.get("nonce")
    if expected_nonce is not None:
        if not echoed_nonce:
            return AttestationResult(
                AttestationTier.TAMPERED, raw_verdict=verdict,
                reason="verdict carries no nonce, so it cannot be tied to this capture")
        # Constant-time: the nonce is a capture-session secret, and a timing
        # oracle on the comparison would leak it a character at a time.
        if not hmac.compare_digest(str(echoed_nonce), str(expected_nonce)):
            return AttestationResult(
                AttestationTier.TAMPERED, raw_verdict=verdict,
                reason="verdict nonce does not match this capture session (replayed token)")
    elif echoed_nonce:
        # A token arrived but the caller gave us nothing to bind it to. Not an
        # accusation — but not a pass either.
        return AttestationResult(
            AttestationTier.UNAVAILABLE, raw_verdict=verdict,
            reason="no expected nonce supplied, so this verdict could not be bound to a capture")

    # ── 2. Package binding: is this verdict about OUR app? ────────────────
    if expected_package is not None:
        request_package = request_details.get("requestPackageName")
        app_package = app_integrity.get("packageName")
        for label, actual in (("requestPackageName", request_package), ("appIntegrity.packageName", app_package)):
            if actual and actual != expected_package:
                return AttestationResult(
                    AttestationTier.TAMPERED, raw_verdict=verdict,
                    reason=f"{label} is {actual!r}, not {expected_package!r}")

    # ── 3. Freshness ──────────────────────────────────────────────────────
    timestamp_millis = request_details.get("timestampMillis")
    if timestamp_millis is not None:
        try:
            issued_at = datetime.fromtimestamp(int(timestamp_millis) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return AttestationResult(
                AttestationTier.TAMPERED, raw_verdict=verdict,
                reason=f"unreadable verdict timestamp: {timestamp_millis!r}")
        reference = now or datetime.now(timezone.utc)
        age_seconds = abs((reference - issued_at).total_seconds())
        if age_seconds > _max_verdict_age_seconds():
            return AttestationResult(
                AttestationTier.TAMPERED, raw_verdict=verdict,
                reason=f"verdict is {int(age_seconds)}s old, beyond the "
                       f"{_max_verdict_age_seconds()}s freshness window")

    # ── 4. The verdict itself ─────────────────────────────────────────────
    app_verdict = app_integrity.get("appRecognitionVerdict", "")
    if app_verdict in ("UNRECOGNIZED_VERSION", "UNEVALUATED"):
        return AttestationResult(
            AttestationTier.TAMPERED, raw_verdict=verdict,
            reason=f"app recognition verdict: {app_verdict}")
    if "MEETS_DEVICE_INTEGRITY" in device_integrity:
        return AttestationResult(
            AttestationTier.VERIFIED_HIGH, raw_verdict=verdict, reason="strong device integrity")
    if device_integrity:  # some recognition signal, but not the strongest tier
        return AttestationResult(
            AttestationTier.VERIFIED_MEDIUM, raw_verdict=verdict,
            reason=f"partial device integrity: {device_integrity}")
    return AttestationResult(
        AttestationTier.TAMPERED, raw_verdict=verdict, reason="no recognized device integrity signal")


#: Kept as a private alias so existing callers and tests that referenced the
#: old name keep working; the public entry point is the unprefixed function.
_classify_play_integrity_verdict = classify_play_integrity_verdict


async def _google_service_account_access_token(credentials_path: str, *, scope: str) -> str:
    """Minimal OAuth2 service-account token fetch. Isolated in its own
    function so it's the one thing Phase 8 needs a real Google Cloud service
    account to exercise end-to-end; the rest of this module is exercised via
    recorded verdicts."""
    import google.auth.transport.requests
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(credentials_path, scopes=[scope])
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials.token


async def verify_app_attest(attestation_object: Optional[bytes], *, key_id: Optional[str] = None) -> AttestationResult:
    """NOT YET IMPLEMENTED (tracked honestly, not hidden): full App Attest
    verification requires CBOR/COSE parsing and certificate-chain validation
    against Apple's App Attest root CA, and cannot be meaningfully tested
    without a real device-produced attestation object -- deferred alongside the
    native iOS bridge that produces one. Always returns UNAVAILABLE, which is
    the correct, safe REVIEW-tier fallback per the per-provider failure mapping
    regardless of why."""
    return AttestationResult(
        AttestationTier.UNAVAILABLE,
        reason="App Attest verification not yet implemented",
    )
