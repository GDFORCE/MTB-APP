"""Geofence classification — spec §7.3, implemented over the pure geometry
primitives in geometry.py rather than PostGIS (see that module's docstring
and Production-Grade Implementation Plan v1.3 correction #16).

Pure logic: no DB/network I/O. Callers fetch the approved geofence geometry
(from `remote_audit_geofence_versions`, Mongo-stored, 2dsphere-indexed for
storage/lookup) and pass it in as plain data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .geometry import (
    Geometry,
    InvalidGeometryError,
    Point,
    distance_to_geometry_boundary_m,
    point_in_geometry,
    validate_geometry,
)


class GeoStatus(str, Enum):
    # Spec §7.3's five classification outcomes.
    WITHIN_BOUNDARY = "WITHIN_BOUNDARY"
    WITHIN_LOW_CONFIDENCE = "WITHIN_LOW_CONFIDENCE"
    NEAR_BOUNDARY = "NEAR_BOUNDARY"
    OUTSIDE_BOUNDARY = "OUTSIDE_BOUNDARY"
    SUSPICIOUS_REVIEW = "SUSPICIOUS_REVIEW"
    # Spec §7.3 step 1's "reject/hold" cases, named explicitly (not folded
    # into SUSPICIOUS_REVIEW) so a missing GPS fix in a basement is never
    # indistinguishable from a spoofing signal in downstream reporting
    # (spec §16.2: "GPS unavailable in basement/ICU -> Do not label fraud").
    LOCATION_MISSING = "LOCATION_MISSING"
    LOCATION_STALE = "LOCATION_STALE"
    LOCATION_LOW_ACCURACY = "LOCATION_LOW_ACCURACY"


@dataclass(frozen=True)
class GeofenceClassification:
    status: GeoStatus
    distance_m: Optional[float]
    accuracy_m: Optional[float]
    location_age_ms: Optional[int]
    geofence_version: str
    rule_version: str
    reasons: List[str] = field(default_factory=list)


RULE_VERSION = "geofence-rules-v1"


def classify(
    *,
    point: Optional[Point],
    accuracy_m: Optional[float],
    location_age_ms: Optional[int],
    geofence: Geometry,
    geofence_version: str,
    max_age_ms: int,
    max_usable_accuracy_m: float,
    near_boundary_threshold_m: float,
    critical_spoof_signal: bool,
) -> GeofenceClassification:
    """Implements spec §7.3 exactly:

    1. Reject/hold if location missing, invalid, stale, or accuracy exceeds
       the policy maximum.
    2. inside = ST_Covers(G, P) -- here, point_in_geometry (boundary-inclusive).
    3. d = minimum geodesic distance from P to G's boundary.
    4. uncertainty_crosses_boundary = (d <= accuracy_m).

    Then: critical spoof signal -> SUSPICIOUS_REVIEW; inside and not
    uncertainty_crosses_boundary -> WITHIN_BOUNDARY; inside and
    uncertainty_crosses_boundary -> WITHIN_LOW_CONFIDENCE; outside and
    d <= max(near_boundary_threshold_m, accuracy_m) -> NEAR_BOUNDARY;
    else -> OUTSIDE_BOUNDARY.
    """
    if point is None:
        return GeofenceClassification(
            status=GeoStatus.LOCATION_MISSING, distance_m=None, accuracy_m=accuracy_m,
            location_age_ms=location_age_ms, geofence_version=geofence_version,
            rule_version=RULE_VERSION,
            reasons=["Location was not provided or was invalid; not treated as fraud."],
        )
    if location_age_ms is not None and location_age_ms > max_age_ms:
        return GeofenceClassification(
            status=GeoStatus.LOCATION_STALE, distance_m=None, accuracy_m=accuracy_m,
            location_age_ms=location_age_ms, geofence_version=geofence_version,
            rule_version=RULE_VERSION,
            reasons=[f"Location fix is {location_age_ms}ms old, exceeding the {max_age_ms}ms policy maximum."],
        )
    if accuracy_m is not None and accuracy_m > max_usable_accuracy_m:
        return GeofenceClassification(
            status=GeoStatus.LOCATION_LOW_ACCURACY, distance_m=None, accuracy_m=accuracy_m,
            location_age_ms=location_age_ms, geofence_version=geofence_version,
            rule_version=RULE_VERSION,
            reasons=[f"Location accuracy {accuracy_m}m exceeds the {max_usable_accuracy_m}m usable maximum."],
        )

    try:
        validate_geometry(geofence)
    except InvalidGeometryError as exc:
        # A malformed stored geofence must never silently classify -- fail
        # loudly into review rather than guessing.
        return GeofenceClassification(
            status=GeoStatus.SUSPICIOUS_REVIEW, distance_m=None, accuracy_m=accuracy_m,
            location_age_ms=location_age_ms, geofence_version=geofence_version,
            rule_version=RULE_VERSION,
            reasons=[f"Stored geofence geometry is invalid ({exc.code}); classification withheld."],
        )

    inside = point_in_geometry(point, geofence)
    distance_m = distance_to_geometry_boundary_m(point, geofence)
    accuracy = accuracy_m or 0.0
    uncertainty_crosses_boundary = distance_m <= accuracy

    if critical_spoof_signal:
        return GeofenceClassification(
            status=GeoStatus.SUSPICIOUS_REVIEW, distance_m=distance_m, accuracy_m=accuracy_m,
            location_age_ms=location_age_ms, geofence_version=geofence_version,
            rule_version=RULE_VERSION,
            reasons=["A critical spoof/simulation signal was present alongside this location."],
        )
    if inside and not uncertainty_crosses_boundary:
        return GeofenceClassification(
            status=GeoStatus.WITHIN_BOUNDARY, distance_m=distance_m, accuracy_m=accuracy_m,
            location_age_ms=location_age_ms, geofence_version=geofence_version,
            rule_version=RULE_VERSION,
            reasons=[f"Point is inside the approved geofence, {distance_m:.1f}m from the boundary "
                     f"with {accuracy_m}m accuracy — the accuracy circle does not cross the boundary."],
        )
    if inside and uncertainty_crosses_boundary:
        return GeofenceClassification(
            status=GeoStatus.WITHIN_LOW_CONFIDENCE, distance_m=distance_m, accuracy_m=accuracy_m,
            location_age_ms=location_age_ms, geofence_version=geofence_version,
            rule_version=RULE_VERSION,
            reasons=[f"Point is inside the approved geofence, but its {accuracy_m}m accuracy circle "
                     f"crosses the boundary ({distance_m:.1f}m away) — treated as low confidence, not rejected."],
        )
    if distance_m <= max(near_boundary_threshold_m, accuracy):
        return GeofenceClassification(
            status=GeoStatus.NEAR_BOUNDARY, distance_m=distance_m, accuracy_m=accuracy_m,
            location_age_ms=location_age_ms, geofence_version=geofence_version,
            rule_version=RULE_VERSION,
            reasons=[f"Point is outside the geofence but only {distance_m:.1f}m from the boundary — "
                     f"within the {near_boundary_threshold_m}m near-boundary band or accuracy radius."],
        )
    return GeofenceClassification(
        status=GeoStatus.OUTSIDE_BOUNDARY, distance_m=distance_m, accuracy_m=accuracy_m,
        location_age_ms=location_age_ms, geofence_version=geofence_version,
        rule_version=RULE_VERSION,
        reasons=[f"Point is {distance_m:.1f}m outside the geofence boundary, beyond the near-boundary band."],
    )
