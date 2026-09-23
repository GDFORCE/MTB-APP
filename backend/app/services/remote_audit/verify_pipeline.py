"""Orchestrates one evidence item's verification run — reads the quarantined
object, hashes it, scans it, extracts EXIF GPS, classifies location against
the site's approved geofence, checks attestation, and calls the pure
decision engine (verification.py). This is the one place all of that comes
together; the worker job handler (remote_audit_worker.py) just calls
`run_evidence_verification` and reacts to the outcome.

Malware gating happens FIRST and is strict (v1.3 correction #15): an
unavailable scanner raises rather than returning a result, so the caller
(the worker) leaves the job to retry instead of ever producing a verdict for
an unscanned file.
"""
from __future__ import annotations

import hashlib
import logging
import time
import io
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

import storage as file_storage

log = logging.getLogger(__name__)

from . import audits as audits_service
from . import evidence as evidence_service
from . import file_checks
from . import sites as sites_service
from .attestation import verify_play_integrity_token
from .geofence import GeoStatus, classify as classify_geofence
from .verification import CaptureMode, VerificationResult, VerificationSignals, evaluate


class ScannerUnavailableError(RuntimeError):
    """Raised to make the worker retry the job later rather than ever
    producing a verdict for a file that was never actually scanned."""


@dataclass(frozen=True)
class VerificationPolicy:
    max_location_age_ms: int = 30_000
    max_usable_accuracy_m: float = 100.0
    near_boundary_threshold_m: float = 100.0
    exif_mismatch_threshold_m: float = 100.0
    block_on_tampered_app: bool = False
    treat_attestation_unavailable_as_review: bool = True
    current_presence_required: bool = True
    geofence_rule_version: str = "geofence-rules-v1"


def _extract_exif_gps(data: bytes) -> Optional[Tuple[float, float]]:
    """Best-effort EXIF GPS extraction (JPEG/TIFF-family only -- PNG/video
    have no equivalent). Never raises: any failure means 'no EXIF GPS',
    which is a warning-only supporting signal, never a hard failure
    (spec §16.3: 'missing EXIF is warning only')."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        exif = image.getexif()
        if not exif:
            return None
        gps_ifd = exif.get_ifd(0x8825)
        if not gps_ifd:
            return None
        lat = _dms_to_decimal(gps_ifd.get(2), gps_ifd.get(1))
        lon = _dms_to_decimal(gps_ifd.get(4), gps_ifd.get(3))
        if lat is None or lon is None:
            return None
        return (lon, lat)
    except Exception:  # noqa: BLE001 -- any parse failure means "no EXIF GPS available"
        return None


def _dms_to_decimal(dms, ref) -> Optional[float]:
    if not dms or not ref:
        return None
    try:
        degrees, minutes, seconds = dms
        value = float(degrees) + float(minutes) / 60.0 + float(seconds) / 3600.0
    except (TypeError, ValueError):
        return None
    if ref in ("S", "W"):
        value = -value
    return value


def _geodesic_distance_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    from .geometry import geodesic_distance_m
    return geodesic_distance_m(a, b)


async def run_evidence_verification(
    db: AsyncIOMotorDatabase,
    evidence_id: str,
    *,
    policy: Optional[VerificationPolicy] = None,
) -> VerificationResult:
    from . import observability as obs

    started_at = time.monotonic()
    policy = policy or VerificationPolicy()
    doc = await evidence_service.get_evidence(db, evidence_id)
    if doc is None:
        raise ValueError(f"evidence {evidence_id!r} not found")

    storage = file_storage.get_storage()
    data, _content_type = await storage.open(doc["object_key"])

    # 1. Malware gate FIRST -- strict, never best-effort (v1.3 correction #15).
    scan_status, _detail = await file_checks.scan(data)
    scan_skipped = False
    if scan_status == file_checks.MalwareScanStatus.UNAVAILABLE:
        obs.emit_metric(obs.Metric.MALWARE_SCAN_UNAVAILABLE, tags={"kind": "evidence"})
        if file_checks.scan_is_required():
            await evidence_service.set_evidence_status(db, evidence_id, "PROCESSING_BLOCKED")
            raise ScannerUnavailableError("malware scanner unavailable; evidence remains blocked pending retry")
        # Dev/demo only. Processing continues, but the evidence is marked as
        # never-scanned and routed to review -- it is NEVER reported clean.
        scan_skipped = True
        log.warning("evidence %s processed WITHOUT a malware scan (%s=false)",
                    evidence_id, file_checks.REQUIRE_SCAN_ENV)
    malware_detected = scan_status == file_checks.MalwareScanStatus.DETECTED
    if malware_detected:
        obs.emit_metric(obs.Metric.MALWARE_DETECTED, tags={"kind": "evidence"})

    # 2. Real hash/MIME, computed from the actual bytes.
    sha256_server = hashlib.sha256(data).hexdigest()
    mime_match = file_checks.sniff_media_type(data, doc["media_type"])
    hash_match = sha256_server == doc.get("sha256_client")
    duplicate_sha256 = await evidence_service.find_duplicate_sha256(
        db, sha256_server, exclude_evidence_id=evidence_id)

    # 3. Geo classification against the site's approved geofence.
    manifest: Dict[str, Any] = doc.get("capture_manifest") or {}
    geo_status: Optional[GeoStatus] = None
    exif_device_distance_m: Optional[float] = None
    exif_gps = _extract_exif_gps(data)
    device_point = None
    if manifest.get("lon") is not None and manifest.get("lat") is not None:
        device_point = (manifest["lon"], manifest["lat"])

    audit = await audits_service.get_audit(db, doc["audit_id"])
    geofence = await sites_service.get_active_geofence(db, audit["site_id"]) if audit else None
    if geofence is not None:
        classification = classify_geofence(
            point=device_point,
            accuracy_m=manifest.get("accuracy_m"),
            location_age_ms=manifest.get("location_age_ms"),
            geofence=geofence["geometry"],
            geofence_version=f"{geofence['site_id']}-v{geofence['version']}",
            max_age_ms=policy.max_location_age_ms,
            max_usable_accuracy_m=policy.max_usable_accuracy_m,
            near_boundary_threshold_m=policy.near_boundary_threshold_m,
            critical_spoof_signal=bool(manifest.get("android_is_mock") or manifest.get("ios_is_simulated")),
        )
        geo_status = classification.status
    if exif_gps is not None and device_point is not None:
        exif_device_distance_m = _geodesic_distance_m(exif_gps, device_point)

    # 4. Attestation (env-gated; UNAVAILABLE until Phase 8's native bridge exists).
    # The nonce comes from the SERVER's stored capture session, never from the
    # manifest. A client-supplied nonce would let an attacker send a replayed
    # token together with the nonce that token was minted for, and the binding
    # check would happily agree with itself.
    capture_session = await evidence_service.get_capture_session(db, doc["capture_session_id"])
    session_nonce = capture_session.get("nonce") if capture_session else None
    attestation_result = await verify_play_integrity_token(
        manifest.get("play_integrity_token"), expected_nonce=session_nonce)

    signals = VerificationSignals(
        hash_match=hash_match,
        mime_match=mime_match,
        malware_detected=malware_detected,
        malware_scan_skipped=scan_skipped,
        capture_session_replayed=False,  # already enforced atomically at finalize() -- never reaches here
        wrong_site_or_user=False,        # authorization already resolved by the route layer before enqueue
        attestation=attestation_result.tier,
        geo_status=geo_status,
        android_is_mock=bool(manifest.get("android_is_mock")),
        ios_is_simulated=bool(manifest.get("ios_is_simulated")),
        exif_gps_present=exif_gps is not None,
        exif_device_distance_m=exif_device_distance_m,
        exif_mismatch_threshold_m=policy.exif_mismatch_threshold_m,
        timestamp_plausible=True,  # timestamp cross-checking is a documented follow-up, not yet implemented
        duplicate_sha256=duplicate_sha256,
        duplicate_visual_candidate=False,  # perceptual-hash matching is Phase 12 (Advanced Assurance)
        image_quality_acceptable=True,     # blur/exposure analysis is a documented follow-up, not yet implemented
        capture_mode=CaptureMode(doc["capture_mode"]),
        current_presence_required=policy.current_presence_required,
        block_on_tampered_app=policy.block_on_tampered_app,
        treat_attestation_unavailable_as_review=policy.treat_attestation_unavailable_as_review,
    )
    result = evaluate(signals)

    await evidence_service.record_verification(
        db, evidence_id=evidence_id, overall_state=result.overall_state.value,
        rule_set_version=result.rule_set_version,
        reasons=[r.__dict__ for r in result.reasons], signals=result.signals,
    )
    await evidence_service.set_evidence_status(db, evidence_id, result.overall_state.value, sha256_server=sha256_server)

    # Metrics last, so a metrics problem can never stop a verdict being
    # recorded. Tags carry the CLASSIFICATION, never the coordinates.
    obs.emit_metric(obs.Metric.VERIFICATION_LATENCY_MS, (time.monotonic() - started_at) * 1000.0)
    obs.emit_metric(obs.Metric.VERIFICATION_OUTCOME,
                    tags={"outcome": result.overall_state.value,
                          "capture_mode": doc["capture_mode"]})
    if manifest.get("accuracy_m") is not None:
        obs.emit_metric(obs.Metric.LOCATION_ACCURACY_M, float(manifest["accuracy_m"]))
    if geo_status is not None and geo_status.value == "OUTSIDE_BOUNDARY":
        obs.emit_metric(obs.Metric.GEO_OUTSIDE_BOUNDARY, tags={"audit_id": doc["audit_id"]})
    if attestation_result.tier.value == "UNAVAILABLE":
        obs.emit_metric(obs.Metric.ATTESTATION_UNAVAILABLE)
    elif attestation_result.tier.value == "TAMPERED":
        obs.emit_metric(obs.Metric.ATTESTATION_FAILED, tags={"tier": attestation_result.tier.value})
    return result
