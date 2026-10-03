"""Two-tier location check-in decision engine (pure logic, no I/O)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence, Tuple

LatLng = Tuple[float, float]

EARTH_RADIUS_M = 6_371_008.8


class Outcome(str, Enum):
    VERIFIED = "VERIFIED"
    VERIFIED_LENIENT = "VERIFIED_LENIENT"
    CAMPUS_ONLY = "CAMPUS_ONLY"
    PARTIAL_MANUAL_BACKUP = "PARTIAL_MANUAL_BACKUP"
    QR_FALLBACK = "QR_FALLBACK"
    DENIED_OUT_OF_BOUNDS = "DENIED_OUT_OF_BOUNDS"
    REJECTED_STALE = "REJECTED_STALE"


@dataclass(frozen=True)
class Thresholds:
    max_age_s: float = 45.0
    standard_radius_m: float = 20.0
    degraded_accuracy_m: float = 30.0
    degraded_radius_cap_m: float = 50.0
    # Not specified in the PRD; configurable in the UI.
    unresolvable_accuracy_m: float = 100.0


@dataclass(frozen=True)
class Fix:
    lat: float
    lng: float
    accuracy_m: Optional[float]
    age_s: Optional[float]


@dataclass
class Result:
    outcome: Outcome
    reasons: List[str] = field(default_factory=list)
    inside_campus: Optional[bool] = None
    distance_m: Optional[float] = None
    radius_used_m: Optional[float] = None
    signal_mode: Optional[str] = None


def haversine_m(a: LatLng, b: LatLng) -> float:
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def contains(polygon: Sequence[LatLng], point: LatLng) -> bool:
    """Ray-casting point-in-polygon, same semantics as google.maps.geometry.poly.containsLocation."""
    lat, lng = point
    inside = False
    n = len(polygon)
    for i in range(n):
        lat_i, lng_i = polygon[i]
        lat_j, lng_j = polygon[i - 1]
        if (lng_i > lng) != (lng_j > lng):
            cross_lat = lat_i + (lng - lng_i) * (lat_j - lat_i) / (lng_j - lng_i)
            if lat < cross_lat:
                inside = not inside
    return inside


def evaluate(fix: Fix, campus: Sequence[LatLng], target: Optional[LatLng],
             t: Thresholds = Thresholds()) -> Result:
    # Freshness filter
    if fix.age_s is None:
        return Result(Outcome.REJECTED_STALE,
                      ["Fix timestamp unknown, so freshness can't be proven."])
    if fix.age_s > t.max_age_s:
        return Result(Outcome.REJECTED_STALE,
                      [f"Fix is {fix.age_s:.0f}s old (limit {t.max_age_s:.0f}s). Possibly a cached parking-lot location."])

    # Precision filter
    if fix.accuracy_m is None:
        return Result(Outcome.QR_FALLBACK, ["Device reported no accuracy radius."])
    if fix.accuracy_m > t.unresolvable_accuracy_m:
        return Result(Outcome.QR_FALLBACK,
                      [f"Accuracy ±{fix.accuracy_m:.0f}m exceeds the resolvable limit ±{t.unresolvable_accuracy_m:.0f}m."])

    degraded = fix.accuracy_m > t.degraded_accuracy_m
    mode = "DEGRADED" if degraded else "STANDARD"
    reasons = [f"Fix age {fix.age_s:.0f}s, accuracy ±{fix.accuracy_m:.0f}m → {mode} signal."]

    # Tier 1
    inside = contains(campus, (fix.lat, fix.lng))
    if not inside:
        return Result(Outcome.DENIED_OUT_OF_BOUNDS, reasons + ["Tier 1: outside campus polygon."],
                      inside_campus=False, signal_mode=mode)
    reasons.append("Tier 1: inside campus polygon.")

    if target is None:
        return Result(Outcome.CAMPUS_ONLY, reasons + ["Tier 2 skipped: no department target selected."],
                      inside_campus=True, signal_mode=mode)

    # Tier 2
    dist = haversine_m((fix.lat, fix.lng), target)
    if dist <= t.standard_radius_m:
        return Result(Outcome.VERIFIED,
                      reasons + [f"Tier 2: {dist:.1f}m from department ≤ {t.standard_radius_m:.0f}m."],
                      True, dist, t.standard_radius_m, mode)

    if degraded:
        radius = min(max(fix.accuracy_m, t.standard_radius_m), t.degraded_radius_cap_m)
        if dist <= radius:
            return Result(Outcome.VERIFIED_LENIENT,
                          reasons + [f"Tier 2: {dist:.1f}m from department. Standard radius failed, "
                                     f"but the radius expanded to {radius:.0f}m for the weak signal."],
                          True, dist, radius, mode)
        return Result(Outcome.PARTIAL_MANUAL_BACKUP,
                      reasons + [f"Tier 2: {dist:.1f}m from department > expanded {radius:.0f}m."],
                      True, dist, radius, mode)

    return Result(Outcome.PARTIAL_MANUAL_BACKUP,
                  reasons + [f"Tier 2: {dist:.1f}m from department > {t.standard_radius_m:.0f}m with a good signal."],
                  True, dist, t.standard_radius_m, mode)
