"""Evidence verification engine — spec §15 (decision table §15.2, rule set
§8.4). Pure logic: no DB/network I/O. Callers (the Phase-1 worker) gather
already-computed signals — the geofence classification (geofence.py), the
attestation result (attestation.py), file/hash/malware outcomes from the
worker's own checks — and pass them in as plain data.

Contract preserved exactly from spec §15.1: never expose only a bare score.
Every result carries `reasons` (human-readable) and `rule_set_version`, so a
reviewer can always see WHY, and a later algorithm update never silently
rewrites a historical audit's meaning.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from .attestation import AttestationTier
from .geofence import GeoStatus

RULE_SET_VERSION = "evidence-verification-v1"


class OverallState(str, Enum):
    PASS = "PASS"
    PASS_WITH_LIMITED_CONFIDENCE = "PASS_WITH_LIMITED_CONFIDENCE"
    REVIEW = "REVIEW"
    FAIL_TECHNICAL = "FAIL_TECHNICAL"
    HOLD = "HOLD"
    SUSPICIOUS_REVIEW = "SUSPICIOUS_REVIEW"


class CaptureMode(str, Enum):
    LIVE_IN_APP = "LIVE_IN_APP"
    GALLERY_HISTORICAL = "GALLERY_HISTORICAL"


@dataclass(frozen=True)
class Reason:
    code: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    explanation: str
    measured_value: Optional[Any] = None
    threshold: Optional[Any] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class VerificationSignals:
    """Every input this decision requires, per spec §8.4's rule set. Already
    computed by the caller -- this module makes no network/file/DB calls."""
    # File integrity (spec §8.4: FILE_HASH_MATCH, MIME_MATCH, MALWARE_SCAN)
    hash_match: bool
    mime_match: bool
    malware_detected: bool
    # Capture-session protocol (CAPTURE_SESSION_VALID)
    capture_session_replayed: bool
    wrong_site_or_user: bool
    # Device/app integrity (APP_DEVICE_ATTESTATION)
    attestation: AttestationTier
    # Geo (LOCATION_FRESHNESS, LOCATION_ACCURACY, MOCK_SIMULATION, GEOFENCE)
    geo_status: Optional[GeoStatus]
    android_is_mock: bool
    ios_is_simulated: bool
    # Supporting signals (EXIF_GPS_CONSISTENCY, TIMESTAMP_CONSISTENCY, DUPLICATE_*, IMAGE_QUALITY)
    exif_gps_present: bool
    exif_device_distance_m: Optional[float]
    exif_mismatch_threshold_m: float
    timestamp_plausible: bool
    duplicate_sha256: bool
    duplicate_visual_candidate: bool
    image_quality_acceptable: bool
    # Capture semantics (spec §8.3)
    capture_mode: CaptureMode
    current_presence_required: bool
    # Policy: does an app-tamper verdict block outright, or route to review?
    # (v1.3 correction #8 -- old-OS and tampered-app are different risks.)
    block_on_tampered_app: bool = False
    # Policy: does an UNAVAILABLE attestation route to REVIEW? Spec's own A9
    # scenario calls this "Configurable REVIEW/fallback" -- not unconditional.
    # Defaults to the conservative choice (True) because attestation is
    # UNAVAILABLE for every capture until Phase 8's native bridges exist, so
    # this is the safe default for the pilot/early phases; a deployment can
    # relax it once real attestation is live and "unavailable" genuinely
    # means a rare provider outage rather than the permanent default state.
    treat_attestation_unavailable_as_review: bool = True
    #: True when processing continued WITHOUT a scan because
    #: REMOTE_AUDIT_REQUIRE_MALWARE_SCAN is off (development only). Kept
    #: separate from `malware_detected` so "nobody scanned this" can never be
    #: confused with either "clean" or "infected" -- it is a third answer.
    malware_scan_skipped: bool = False


@dataclass(frozen=True)
class VerificationResult:
    overall_state: OverallState
    rule_set_version: str
    reasons: List[Reason]
    signals: Dict[str, Any]
    confidence_score: Optional[int] = None  # 0..100, optional display only -- reasons are mandatory


def _signal_summary(signals: VerificationSignals) -> Dict[str, Any]:
    return {
        "hash_match": signals.hash_match,
        "mime_match": signals.mime_match,
        "malware_status": (
            "DETECTED" if signals.malware_detected
            else "NOT_SCANNED" if signals.malware_scan_skipped
            else "CLEAN"
        ),
        "capture_session_replayed": signals.capture_session_replayed,
        "attestation_tier": signals.attestation.value,
        "geo_status": signals.geo_status.value if signals.geo_status else None,
        "android_is_mock": signals.android_is_mock,
        "ios_is_simulated": signals.ios_is_simulated,
        "exif_device_distance_m": signals.exif_device_distance_m,
        "duplicate_sha256": signals.duplicate_sha256,
        "duplicate_visual_candidate": signals.duplicate_visual_candidate,
        "capture_mode": signals.capture_mode.value,
    }


def evaluate(signals: VerificationSignals) -> VerificationResult:
    """Implements spec §15.2's decision table exactly, extended with the
    full §8.4 rule set as supporting reasons attached to whichever overall
    state is reached. Branch order matters -- earlier branches are strictly
    more severe and short-circuit the rest, exactly as the spec's pseudocode
    does."""
    reasons: List[Reason] = []

    # 1. Technical failure -- never passes through to review; this is not a
    #    fraud judgment, it's "the bytes are not trustworthy."
    if signals.malware_detected:
        reasons.append(Reason("MALWARE_DETECTED", "critical", "Malware scan detected a threat in the uploaded file.", source="worker"))
        return _result(OverallState.FAIL_TECHNICAL, reasons, signals)
    if not signals.hash_match:
        reasons.append(Reason("EVIDENCE_HASH_MISMATCH", "critical", "Server-computed hash does not match the claimed checksum.", source="worker"))
        return _result(OverallState.FAIL_TECHNICAL, reasons, signals)
    if not signals.mime_match:
        reasons.append(Reason("MIME_MISMATCH", "critical", "File content does not match its claimed type.", source="worker"))
        return _result(OverallState.FAIL_TECHNICAL, reasons, signals)

    # 1b. Never scanned. Not a failure -- we have no evidence against this
    #     file -- but it can never reach PASS either, because "nobody looked"
    #     is not "we looked and it was fine". Collected here and forced to
    #     REVIEW at the end rather than short-circuiting, so the reviewer
    #     still sees every other finding alongside it.
    if signals.malware_scan_skipped:
        reasons.append(Reason(
            "FILE_NOT_SCANNED", "high",
            "This file was never scanned for malware -- the scanner is disabled on this "
            "deployment. Do not open it outside a controlled environment.",
            source="worker"))

    # 2. Hold -- a replay or an identity/session mismatch; investigate before
    #    any further judgment is even meaningful.
    if signals.capture_session_replayed:
        reasons.append(Reason("REPLAY_REJECTED", "critical", "This capture session was already consumed.", source="server"))
        return _result(OverallState.HOLD, reasons, signals)
    if signals.wrong_site_or_user:
        reasons.append(Reason("SESSION_IDENTITY_MISMATCH", "critical", "Capture session does not match the requesting user/site.", source="server"))
        return _result(OverallState.HOLD, reasons, signals)

    # 3. Device/app tamper or mock-location signal -- review, or blocked
    #    outright if policy says a tampered app should never reach review
    #    (v1.3 correction #8: old-OS and tampered-app are different risks).
    if signals.attestation.value == AttestationTier.TAMPERED.value:
        reasons.append(Reason(
            "APP_TAMPER_VERDICT", "high",
            "Device/app integrity check indicates a modified or unrecognized app build.",
            source="attestation",
        ))
        if signals.block_on_tampered_app:
            return _result(OverallState.HOLD, reasons, signals)
        return _result(OverallState.REVIEW, reasons, signals)
    if signals.attestation == AttestationTier.UNAVAILABLE and signals.treat_attestation_unavailable_as_review:
        # v1.3 correction #15: attestation-unavailable is a per-provider
        # REVIEW/fallback tier, not silently ignored -- but it is also not
        # treated as "automatic fraud" (spec's own A9 acceptance scenario,
        # which explicitly calls this "Configurable REVIEW/fallback").
        reasons.append(Reason(
            "ATTESTATION_UNAVAILABLE", "medium",
            "Device/app attestation was not available for this capture.",
            source="attestation",
        ))
        return _result(OverallState.REVIEW, reasons, signals)
    if signals.android_is_mock or signals.ios_is_simulated:
        reasons.append(Reason(
            "LOCATION_SIMULATION_SIGNAL", "high",
            "The platform reported this location as mocked/simulated.",
            source="os",
        ))
        return _result(OverallState.REVIEW, reasons, signals)
    if signals.geo_status == GeoStatus.SUSPICIOUS_REVIEW:
        reasons.append(Reason("GEO_SUSPICIOUS_SIGNAL", "high", "A critical spoof/simulation signal was present with the location.", source="geo"))
        return _result(OverallState.SUSPICIOUS_REVIEW, reasons, signals)

    # 4. Live-capture geo outcomes.
    if signals.capture_mode == CaptureMode.LIVE_IN_APP:
        if signals.geo_status is None:
            reasons.append(Reason("LOCATION_MISSING", "medium", "No location was provided for this live capture; not treated as fraud.", source="geo"))
            return _result(OverallState.REVIEW, reasons, signals)
        if signals.geo_status in (GeoStatus.LOCATION_MISSING, GeoStatus.LOCATION_STALE, GeoStatus.LOCATION_LOW_ACCURACY):
            reasons.append(Reason(signals.geo_status.value, "medium", "Location freshness/accuracy did not meet policy for a live capture.", source="geo"))
            return _result(OverallState.REVIEW, reasons, signals)
        if signals.geo_status in (GeoStatus.OUTSIDE_BOUNDARY, GeoStatus.NEAR_BOUNDARY):
            reasons.append(Reason("LOCATION_OUTSIDE_BOUNDARY", "high", "Location is outside the approved geofence beyond uncertainty.", source="geo"))
            return _result(OverallState.REVIEW, reasons, signals)
        if signals.geo_status == GeoStatus.WITHIN_LOW_CONFIDENCE:
            reasons.append(Reason("LOCATION_LOW_CONFIDENCE", "low", "Inside the geofence, but the accuracy circle crosses the boundary.", source="geo"))
        elif signals.geo_status == GeoStatus.WITHIN_BOUNDARY:
            reasons.append(Reason("LOCATION_WITHIN_BOUNDARY", "info", "Inside the approved geofence with acceptable accuracy.", source="geo"))

    # 5. Gallery/historical capture -- confidence-ceiling rule from spec §8.3.
    if signals.capture_mode == CaptureMode.GALLERY_HISTORICAL:
        reasons.append(Reason(
            "GALLERY_CAPTURE", "info",
            "Historical/gallery evidence: current device location does not prove current presence.",
            source="policy",
        ))
        if signals.current_presence_required:
            return _result(OverallState.REVIEW, reasons, signals)
        _append_supporting_reasons(reasons, signals)
        return _result(OverallState.PASS_WITH_LIMITED_CONFIDENCE, reasons, signals)

    # 6. Supporting signals -- never independently fail the capture, but are
    #    always recorded (spec §8.4: EXIF/timestamp/duplicate/quality are
    #    "supporting"/"review flag only", never critical on their own).
    _append_supporting_reasons(reasons, signals)
    if signals.duplicate_sha256:
        return _result(OverallState.REVIEW, reasons, signals)

    return _result(OverallState.PASS, reasons, signals)


def _append_supporting_reasons(reasons: List[Reason], signals: VerificationSignals) -> None:
    if signals.exif_gps_present and signals.exif_device_distance_m is not None:
        if signals.exif_device_distance_m > signals.exif_mismatch_threshold_m:
            reasons.append(Reason(
                "EXIF_DEVICE_MISMATCH", "medium",
                "EXIF GPS differs from the device-reported location beyond the configured threshold.",
                measured_value=signals.exif_device_distance_m, threshold=signals.exif_mismatch_threshold_m,
                source="exif",
            ))
        else:
            reasons.append(Reason("EXIF_GPS_CONSISTENT", "info", "EXIF GPS is consistent with the device location.", source="exif"))
    elif not signals.exif_gps_present:
        reasons.append(Reason("EXIF_GPS_ABSENT", "info", "No EXIF GPS present -- a warning only, not a failure.", source="exif"))

    if not signals.timestamp_plausible:
        reasons.append(Reason("TIMESTAMP_IMPLAUSIBLE", "low", "EXIF/device/server timestamps are not mutually plausible.", source="timestamp"))

    if signals.duplicate_sha256:
        reasons.append(Reason("DUPLICATE_EXACT", "high", "This exact file (by SHA-256) was already used as evidence elsewhere.", source="worker"))
    if signals.duplicate_visual_candidate:
        reasons.append(Reason("DUPLICATE_VISUAL_CANDIDATE", "low", "Perceptually similar to prior evidence -- a review flag only.", source="worker"))
    if not signals.image_quality_acceptable:
        reasons.append(Reason("IMAGE_QUALITY_LOW", "low", "Image quality may be insufficient to verify the asset/label -- request recapture.", source="worker"))


def _apply_unscanned_floor(state: "OverallState", signals: "VerificationSignals") -> "OverallState":
    """An unscanned file may never present as a pass.

    Applied after the decision table rather than inside it so that skipping
    the scan changes only the CEILING -- every other finding is still computed
    and shown. PASS and PASS_WITH_LIMITED_CONFIDENCE become REVIEW; anything
    already worse than REVIEW stays as it is.
    """
    if not signals.malware_scan_skipped:
        return state
    if state in (OverallState.PASS, OverallState.PASS_WITH_LIMITED_CONFIDENCE):
        return OverallState.REVIEW
    return state


def _result(state: OverallState, reasons: List[Reason], signals: VerificationSignals) -> VerificationResult:
    # Applied here rather than at each return site, so no future branch can
    # forget it and hand back a PASS for a file nobody scanned.
    return VerificationResult(
        overall_state=_apply_unscanned_floor(state, signals),
        rule_set_version=RULE_SET_VERSION,
        reasons=reasons,
        signals=_signal_summary(signals),
    )
