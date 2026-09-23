"""Tests for OSM boundary lookup (app/services/remote_audit/boundary_lookup.py)
— exactly the fixture cases the plan calls for: a real match, a no-match, a
malformed/self-intersecting OSM geometry, and a network timeout. All must
degrade to None (fall back to the circle), never raise.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.remote_audit import boundary_lookup as bl  # noqa: E402

LAT, LON = 28.6139, 77.2090  # New Delhi-ish


def make_way(coords_lat_lon, way_type="way"):
    return {"type": way_type, "geometry": [{"lat": lat, "lon": lon} for lat, lon in coords_lat_lon]}


SQUARE_WAY = make_way([(28.0, 77.0), (28.0, 77.001), (28.001, 77.001), (28.001, 77.0), (28.0, 77.0)])
LARGER_WAY = make_way([(28.0, 77.0), (28.0, 77.01), (28.01, 77.01), (28.01, 77.0), (28.0, 77.0)])


@pytest.mark.asyncio
async def test_real_match_returns_the_largest_way_as_a_valid_polygon(monkeypatch):
    async def fake_query(lat, lon, radius_m):
        return {"elements": [SQUARE_WAY, LARGER_WAY]}

    monkeypatch.setattr(bl, "_query_overpass", fake_query)
    geometry = await bl.fetch_osm_boundary(LAT, LON)
    assert geometry is not None
    assert geometry["type"] == "Polygon"
    # the larger of the two candidate ways is picked (both fixtures are
    # already pre-closed rings, so no extra closing point is appended)
    assert len(geometry["coordinates"][0]) == len(LARGER_WAY["geometry"])
    assert geometry["coordinates"][0][0] == (77.0, 28.0)


@pytest.mark.asyncio
async def test_no_match_returns_none(monkeypatch):
    async def fake_query(lat, lon, radius_m):
        return {"elements": []}

    monkeypatch.setattr(bl, "_query_overpass", fake_query)
    assert await bl.fetch_osm_boundary(LAT, LON) is None


@pytest.mark.asyncio
async def test_relation_only_result_is_treated_as_no_match(monkeypatch):
    """v1.3-documented scope: RELATION-tagged campuses are not reconstructed
    in v1 -- a relation-only result must fall back exactly like no match."""
    async def fake_query(lat, lon, radius_m):
        return {"elements": [{"type": "relation", "members": []}]}

    monkeypatch.setattr(bl, "_query_overpass", fake_query)
    assert await bl.fetch_osm_boundary(LAT, LON) is None


@pytest.mark.asyncio
async def test_malformed_self_intersecting_osm_geometry_falls_back_not_crashes(monkeypatch):
    bowtie_way = make_way([(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)])

    async def fake_query(lat, lon, radius_m):
        return {"elements": [bowtie_way]}

    monkeypatch.setattr(bl, "_query_overpass", fake_query)
    assert await bl.fetch_osm_boundary(LAT, LON) is None


@pytest.mark.asyncio
async def test_too_few_nodes_is_skipped_as_a_candidate(monkeypatch):
    tiny_way = make_way([(28.0, 77.0), (28.0, 77.001)])

    async def fake_query(lat, lon, radius_m):
        return {"elements": [tiny_way]}

    monkeypatch.setattr(bl, "_query_overpass", fake_query)
    assert await bl.fetch_osm_boundary(LAT, LON) is None


@pytest.mark.asyncio
async def test_network_timeout_returns_none_not_an_exception(monkeypatch):
    async def fake_query(lat, lon, radius_m):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(bl, "_query_overpass", fake_query)
    assert await bl.fetch_osm_boundary(LAT, LON) is None


@pytest.mark.asyncio
async def test_http_error_returns_none_not_an_exception(monkeypatch):
    async def fake_query(lat, lon, radius_m):
        raise httpx.HTTPStatusError("bad gateway", request=None, response=None)

    monkeypatch.setattr(bl, "_query_overpass", fake_query)
    assert await bl.fetch_osm_boundary(LAT, LON) is None
