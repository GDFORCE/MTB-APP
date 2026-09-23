"""Real hospital-boundary lookup via OpenStreetMap's Overpass API — Phase 3's
"try a real boundary before falling back to a circle" decision.

Google's APIs don't offer this (Places API returns only a point and a
rectangular display viewport; Google's Open Buildings dataset needs a
separate Earth Engine/BigQuery pipeline and gives a building rooftop, not a
property boundary, anyway). OSM sometimes has a real, community-mapped
hospital outline for free, with no account/key — but coverage is
inconsistent, so this always degrades to "no boundary found" rather than
raising, and the caller (site onboarding, Phase 3) falls back to the
circle-with-default-radius flow.

Scope, stated explicitly: only WAY-tagged hospital outlines are handled.
Some larger hospital complexes are mapped in OSM as multi-way RELATIONs
(outer/inner ring composition), which this module does not attempt to
reconstruct — an incomplete best-guess reconstruction would be worse than
falling back to the circle, so a relation-only match is treated the same as
no match at all in v1.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from .geometry import Geometry, InvalidGeometryError, Point, validate_geometry

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_SEARCH_RADIUS_M = 500
_USER_AGENT = "MyTrialBoard-RemoteAudit/1.0 (site geofence onboarding)"


def _overpass_url() -> str:
    return os.environ.get("OSM_OVERPASS_URL", DEFAULT_OVERPASS_URL)


def _build_query(lat: float, lon: float, radius_m: int) -> str:
    return (
        f'[out:json][timeout:15];'
        f'(way["amenity"="hospital"](around:{radius_m},{lat},{lon});'
        f'way["healthcare"="hospital"](around:{radius_m},{lat},{lon}););'
        f'out geom;'
    )


async def _query_overpass(lat: float, lon: float, radius_m: int) -> Dict[str, Any]:
    """Isolated so tests can monkeypatch the network call directly, the same
    pattern as vision_extraction.py's `_call_claude_vision`/`_call_gemini_vision`."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            _overpass_url(), data={"data": _build_query(lat, lon, radius_m)},
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        return response.json()


def _way_to_ring(way: Dict[str, Any]) -> Optional[List[Point]]:
    nodes = way.get("geometry")
    if not nodes or len(nodes) < 3:
        return None
    ring = [(node["lon"], node["lat"]) for node in nodes]
    if ring[0] != ring[-1]:
        ring.append(ring[0])  # OSM way geometry isn't always given pre-closed
    if len(ring) < 4:
        return None
    return ring


def _ring_area_approx(ring: List[Point]) -> float:
    """Shoelace formula on raw (lon, lat) -- not a true metric area, but a
    consistent relative-size measure for picking the largest of several
    candidate ways near the same point."""
    area = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


async def fetch_osm_boundary(
    lat: float, lon: float, *, search_radius_m: int = DEFAULT_SEARCH_RADIUS_M,
) -> Optional[Geometry]:
    """Returns a real hospital boundary near (lat, lon) if OSM has one
    mapped as a way, else None -- never raises into the caller (a network
    error, no match, or a match that fails geometry validation are all
    treated identically as "no boundary found," per Phase 3's design: the
    circle fallback is the expected common case, not an error path)."""
    try:
        raw = await _query_overpass(lat, lon, search_radius_m)
    except Exception:  # noqa: BLE001 -- any network/provider failure means "try the fallback"
        return None

    candidates: List[List[Point]] = []
    for element in raw.get("elements", []):
        if element.get("type") != "way":
            continue
        ring = _way_to_ring(element)
        if ring is not None:
            candidates.append(ring)
    if not candidates:
        return None

    best_ring = max(candidates, key=_ring_area_approx)
    geometry: Geometry = {"type": "Polygon", "coordinates": [best_ring]}
    try:
        validate_geometry(geometry)
    except InvalidGeometryError:
        return None
    return geometry
