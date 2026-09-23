"""Site geofence onboarding — Phase 3's "zero map interaction" flow: given a
geocoded address, try a real OpenStreetMap boundary first, fall back to a
circle at a policy default radius if none is found, and prepare a Google
Static Maps preview image for the sponsor's single Approve tap. Nobody
drags, draws, or resizes anything, regardless of which geometry source won.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote

from motor.motor_asyncio import AsyncIOMotorDatabase

from . import sites as sites_service
from .boundary_lookup import fetch_osm_boundary
from .geometry import Geometry, Point, circle_to_polygon

DEFAULT_RADIUS_M = 150.0
STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"

SOURCE_OSM = "osm"
SOURCE_CIRCLE = "circle"


@dataclass(frozen=True)
class GeofenceProposal:
    geofence_version: Dict[str, Any]
    source: str  # "osm" | "circle" -- invisible to the sponsor, recorded for provenance
    preview_url: Optional[str]  # None if Maps Static API isn't configured


async def propose_geofence_for_address(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    lat: float,
    lon: float,
    proposed_by: str,
    default_radius_m: float = DEFAULT_RADIUS_M,
) -> GeofenceProposal:
    """The one place a site's geofence gets proposed from an address. Tries
    OSM first (real boundary, free, no billing); a network error, no match,
    or an invalid OSM geometry are all treated identically as "use the
    circle" (boundary_lookup.fetch_osm_boundary already degrades to None for
    all three, per its own docstring) -- never surfaced as an error here."""
    geometry: Optional[Geometry] = await fetch_osm_boundary(lat, lon)
    source = SOURCE_OSM
    if geometry is None:
        geometry = {"type": "Polygon", "coordinates": circle_to_polygon((lon, lat), default_radius_m)}
        source = SOURCE_CIRCLE

    version = await sites_service.propose_geofence_version(
        db, site_id=site_id, geometry=geometry,
        buffer_m=default_radius_m if source == SOURCE_CIRCLE else 0.0,
        proposed_by=proposed_by,
    )
    preview_url = build_static_map_preview_url(center=(lon, lat), geometry=geometry)
    return GeofenceProposal(geofence_version=version, source=source, preview_url=preview_url)


def build_static_map_preview_url(*, center: Point, geometry: Geometry, size: str = "600x400") -> Optional[str]:
    """Returns None (never raises) if the Maps Static API isn't configured
    -- the approval screen shows the geocoded address as text with no image
    rather than failing to load, per the provider-dependency failure
    mapping (docs/remote-audit/provider-dependencies.md)."""
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        return None
    lon, lat = center
    ring = geometry["coordinates"][0] if geometry["type"] == "Polygon" else geometry["coordinates"][0][0]
    # Static Maps path points are "lat,lng", the reverse of this module's
    # (lon, lat) GeoJSON convention -- flipped here, at the one boundary
    # where an external API disagrees with our internal convention.
    path_points = "|".join(f"{point_lat},{point_lon}" for point_lon, point_lat in ring)
    params = {
        "size": size,
        "markers": f"color:red|{lat},{lon}",
        "path": f"color:0x0000ff80|weight:2|fillcolor:0x0000ff33|{path_points}",
        "key": api_key,
    }
    query = "&".join(f"{k}={quote(str(v), safe=',:|')}" for k, v in params.items())
    return f"{STATIC_MAPS_URL}?{query}"
