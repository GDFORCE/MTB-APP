"""Tests for the site geofence onboarding flow
(app/services/remote_audit/geofence_onboarding.py) — proves the OSM-first,
circle-fallback decision and the Static Maps preview URL builder, both with
and without the Maps Static API configured.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit import boundary_lookup, geofence_onboarding as go  # noqa: E402
from app.services.remote_audit import sites as sites_service  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402
from app.services.remote_audit.geometry import point_in_geometry  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
LAT, LON = 28.6139, 77.2090


@pytest_asyncio.fixture
async def env():
    db = get_db()
    await sites_service.ensure_indexes(db)
    site = await sites_service.create_site(
        db, organization_id=f"org-{RUN_ID}", official_name="Onboarding Test Hospital",
        address={}, timezone_name="Asia/Kolkata",
    )
    yield {"db": db, "site": site}
    await db[sites_service.SITES_COLLECTION].delete_many({"id": site["id"]})
    await db[sites_service.GEOFENCE_VERSIONS_COLLECTION].delete_many({"site_id": site["id"]})
    db.client.close()


@pytest.mark.asyncio
async def test_uses_osm_boundary_when_available(env, monkeypatch):
    osm_ring = [(77.208, 28.613), (77.210, 28.613), (77.210, 28.615), (77.208, 28.615), (77.208, 28.613)]

    async def fake_osm(lat, lon, **kwargs):
        return {"type": "Polygon", "coordinates": [osm_ring]}

    monkeypatch.setattr(boundary_lookup, "fetch_osm_boundary", fake_osm)
    monkeypatch.setattr(go, "fetch_osm_boundary", fake_osm)

    proposal = await go.propose_geofence_for_address(
        env["db"], site_id=env["site"]["id"], lat=LAT, lon=LON, proposed_by="user-1",
    )
    assert proposal.source == go.SOURCE_OSM
    assert proposal.geofence_version["geometry"]["coordinates"][0] == osm_ring


@pytest.mark.asyncio
async def test_falls_back_to_circle_when_no_osm_match(env, monkeypatch):
    async def no_match(lat, lon, **kwargs):
        return None

    monkeypatch.setattr(go, "fetch_osm_boundary", no_match)

    proposal = await go.propose_geofence_for_address(
        env["db"], site_id=env["site"]["id"], lat=LAT, lon=LON, proposed_by="user-1",
        default_radius_m=100.0,
    )
    assert proposal.source == go.SOURCE_CIRCLE
    # the center point itself must be inside the resulting circle geometry
    assert point_in_geometry((LON, LAT), proposal.geofence_version["geometry"]) is True


@pytest.mark.asyncio
async def test_falls_back_to_circle_when_osm_lookup_raises_unexpectedly(env, monkeypatch):
    """Even if the real fetch_osm_boundary somehow raised (it's designed not
    to, but this proves the onboarding flow doesn't assume that promise is
    unconditionally kept) -- actually fetch_osm_boundary itself guarantees
    no raise, so this exercises the real function end-to-end with an
    unreachable Overpass URL instead of mocking."""
    monkeypatch.setenv("OSM_OVERPASS_URL", "http://127.0.0.1:1/unreachable")
    proposal = await go.propose_geofence_for_address(
        env["db"], site_id=env["site"]["id"], lat=LAT, lon=LON, proposed_by="user-1",
    )
    assert proposal.source == go.SOURCE_CIRCLE


def test_static_map_preview_url_is_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    geometry = {"type": "Polygon", "coordinates": [[(77.0, 28.0), (77.001, 28.0), (77.001, 28.001), (77.0, 28.001), (77.0, 28.0)]]}
    assert go.build_static_map_preview_url(center=(77.0005, 28.0005), geometry=geometry) is None


def test_static_map_preview_url_contains_marker_and_path_when_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key-123")
    geometry = {"type": "Polygon", "coordinates": [[(77.0, 28.0), (77.001, 28.0), (77.001, 28.001), (77.0, 28.001), (77.0, 28.0)]]}
    url = go.build_static_map_preview_url(center=(77.0005, 28.0005), geometry=geometry)
    assert url is not None
    assert url.startswith(go.STATIC_MAPS_URL)
    assert "key=test-key-123" in url
    assert "markers=" in url
    assert "28.0005,77.0005" in url  # marker is lat,lng (flipped from our lon,lat convention)
    assert "path=" in url
