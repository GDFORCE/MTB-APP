"""Geofence classification parity/acceptance suite (spec §7.3, §19.2 A1-A3).

Proves the Mongo + pure-Python classifier (app/services/remote_audit/geofence.py)
produces the same outcomes the spec's PostGIS-based algorithm would, per
Production-Grade Implementation Plan v1.3's "deliberate deviation" table and
correction #16. This suite is the Phase 1 exit criterion: nothing in Phase 2+
may ship until every case below passes.
"""
from __future__ import annotations

from app.services.remote_audit.geofence import GeoStatus, classify

SQUARE_GEOFENCE = {
    "type": "Polygon",
    "coordinates": [
        [
            (77.0000, 28.0000),
            (77.0020, 28.0000),
            (77.0020, 28.0018),
            (77.0000, 28.0018),
            (77.0000, 28.0000),
        ]
    ],
}
CENTER = (77.0010, 28.0009)
JUST_OUTSIDE = (77.0021, 28.0009)   # ~11m outside the east edge
FAR_OUTSIDE = (77.1000, 28.1000)    # tens of km away

DEFAULTS = dict(
    geofence=SQUARE_GEOFENCE,
    geofence_version="v1",
    max_age_ms=30_000,
    max_usable_accuracy_m=100.0,
    near_boundary_threshold_m=100.0,
    critical_spoof_signal=False,
)


def _classify(**overrides):
    kwargs = {**DEFAULTS, **overrides}
    return classify(**kwargs)


# 1-2: Polygon and MultiPolygon geometries ----------------------------------

def test_polygon_geometry_classifies_correctly():
    result = _classify(point=CENTER, accuracy_m=10.0, location_age_ms=5_000)
    assert result.status == GeoStatus.WITHIN_BOUNDARY


def test_multipolygon_geometry_classifies_correctly():
    multi = {"type": "MultiPolygon", "coordinates": [SQUARE_GEOFENCE["coordinates"]]}
    result = _classify(geofence=multi, point=CENTER, accuracy_m=10.0, location_age_ms=5_000)
    assert result.status == GeoStatus.WITHIN_BOUNDARY


# 3: boundary-inclusive behavior (ST_Covers semantics) -----------------------

def test_point_exactly_on_boundary_is_within_not_outside():
    boundary_point = (77.0010, 28.0000)  # on the south edge
    result = _classify(point=boundary_point, accuracy_m=5.0, location_age_ms=1_000)
    assert result.status in (GeoStatus.WITHIN_BOUNDARY, GeoStatus.WITHIN_LOW_CONFIDENCE)


# 4: point-in-polygon correctness against known fixtures ---------------------

def test_known_inside_and_outside_fixtures():
    assert _classify(point=CENTER, accuracy_m=5.0, location_age_ms=1_000).status == GeoStatus.WITHIN_BOUNDARY
    assert _classify(point=FAR_OUTSIDE, accuracy_m=5.0, location_age_ms=1_000).status == GeoStatus.OUTSIDE_BOUNDARY


# 5: geodesic distance in meters, within tolerance of a reference haversine --

def test_distance_reported_is_plausible_for_known_offset():
    result = _classify(point=JUST_OUTSIDE, accuracy_m=5.0, location_age_ms=1_000)
    assert result.status == GeoStatus.NEAR_BOUNDARY
    assert 5.0 < result.distance_m < 20.0  # ~11m expected


# 6: accuracy-radius crossing the boundary -----------------------------------

def test_low_accuracy_inside_point_crossing_boundary_is_low_confidence():
    near_edge_inside = (77.0019, 28.0009)  # ~11m inside the east edge
    result = _classify(point=near_edge_inside, accuracy_m=50.0, location_age_ms=1_000)
    assert result.status == GeoStatus.WITHIN_LOW_CONFIDENCE


def test_high_accuracy_inside_point_not_crossing_boundary_is_full_confidence():
    near_edge_inside = (77.0019, 28.0009)
    result = _classify(point=near_edge_inside, accuracy_m=5.0, location_age_ms=1_000)
    assert result.status == GeoStatus.WITHIN_BOUNDARY


# 7: near-boundary classification at the configured band --------------------

def test_outside_but_within_near_boundary_band():
    result = _classify(point=JUST_OUTSIDE, accuracy_m=5.0, location_age_ms=1_000,
                        near_boundary_threshold_m=100.0)
    assert result.status == GeoStatus.NEAR_BOUNDARY


def test_outside_beyond_near_boundary_band_and_accuracy():
    result = _classify(point=FAR_OUTSIDE, accuracy_m=5.0, location_age_ms=1_000,
                        near_boundary_threshold_m=100.0)
    assert result.status == GeoStatus.OUTSIDE_BOUNDARY


# 8: invalid/self-intersecting geometry never silently classifies -----------

def test_invalid_stored_geometry_routes_to_suspicious_review_not_a_crash():
    bowtie = {"type": "Polygon", "coordinates": [[(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)]]}
    result = _classify(geofence=bowtie, point=(0.5, 0.5), accuracy_m=5.0, location_age_ms=1_000)
    assert result.status == GeoStatus.SUSPICIOUS_REVIEW


# 9: lat/lon order and range validation --------------------------------------

def test_swapped_lat_lon_geometry_out_of_range_is_caught_not_misclassified():
    """A swap that pushes a value out of its valid range (here, a true
    longitude of ~100.5 landing in the latitude slot) is caught before
    classification ever runs, per test_remote_audit_geometry.py's identical
    fixture -- see that file's paired test for the documented case where a
    swap stays in-range on both axes and range validation alone cannot help."""
    swapped = {"type": "Polygon", "coordinates": [[
        (13.75, 100.50), (13.76, 100.50), (13.76, 100.51), (13.75, 100.51), (13.75, 100.50),
    ]]}
    result = _classify(geofence=swapped, point=CENTER, accuracy_m=5.0, location_age_ms=1_000)
    assert result.status == GeoStatus.SUSPICIOUS_REVIEW


# 10: multi-building campuses -------------------------------------------------

def test_multi_building_campus_point_in_second_building_is_within():
    building_b = [(77.0100, 28.0100), (77.0120, 28.0100), (77.0120, 28.0118), (77.0100, 28.0118), (77.0100, 28.0100)]
    campus = {"type": "MultiPolygon", "coordinates": [SQUARE_GEOFENCE["coordinates"], [building_b]]}
    point_in_b = (77.0110, 28.0110)
    result = _classify(geofence=campus, point=point_in_b, accuracy_m=5.0, location_age_ms=1_000)
    assert result.status == GeoStatus.WITHIN_BOUNDARY


# 11: versioned geofences carried through, not recomputed against a "latest" --

def test_geofence_version_is_carried_through_unchanged():
    result = _classify(point=CENTER, accuracy_m=5.0, location_age_ms=1_000, geofence_version="site-1-v7")
    assert result.geofence_version == "site-1-v7"
    assert result.rule_version  # a rule-set version is always present (spec §15.1)


# 12: edge/boundary points ----------------------------------------------------

def test_point_on_corner_vertex_is_within():
    corner = SQUARE_GEOFENCE["coordinates"][0][0]
    result = _classify(point=corner, accuracy_m=5.0, location_age_ms=1_000)
    assert result.status in (GeoStatus.WITHIN_BOUNDARY, GeoStatus.WITHIN_LOW_CONFIDENCE)


# 13: precision/rounding cases do not flip a classification -----------------

def test_repeated_classification_is_stable():
    outcomes = {_classify(point=JUST_OUTSIDE, accuracy_m=5.0, location_age_ms=1_000).status for _ in range(20)}
    assert outcomes == {GeoStatus.NEAR_BOUNDARY}


# 14: location age/freshness threshold enforcement ---------------------------

def test_stale_location_is_rejected_before_geo_math_even_runs():
    result = _classify(point=CENTER, accuracy_m=5.0, location_age_ms=60_000, max_age_ms=30_000)
    assert result.status == GeoStatus.LOCATION_STALE
    assert result.distance_m is None


# 15: reduced-accuracy handling -----------------------------------------------

def test_accuracy_worse_than_policy_maximum_is_rejected_before_geo_math():
    result = _classify(point=CENTER, accuracy_m=500.0, location_age_ms=1_000, max_usable_accuracy_m=100.0)
    assert result.status == GeoStatus.LOCATION_LOW_ACCURACY
    assert result.distance_m is None


# 16: basement/poor-GPS fallback ----------------------------------------------

def test_missing_location_is_explicit_not_fraud():
    result = _classify(point=None, accuracy_m=None, location_age_ms=None)
    assert result.status == GeoStatus.LOCATION_MISSING
    assert "not treated as fraud" in result.reasons[0]


# 17: critical spoof/simulation signal overrides everything else ------------

def test_critical_spoof_signal_forces_suspicious_review_even_when_inside():
    result = _classify(point=CENTER, accuracy_m=5.0, location_age_ms=1_000, critical_spoof_signal=True)
    assert result.status == GeoStatus.SUSPICIOUS_REVIEW


# 18: reasons and rule_version are always populated (spec §15.1 contract) ---

def test_every_outcome_carries_human_readable_reasons_and_rule_version():
    for point, accuracy, age in [
        (CENTER, 5.0, 1_000),
        (FAR_OUTSIDE, 5.0, 1_000),
        (JUST_OUTSIDE, 5.0, 1_000),
        (None, None, None),
    ]:
        result = _classify(point=point, accuracy_m=accuracy, location_age_ms=age)
        assert result.reasons and all(isinstance(r, str) and r for r in result.reasons)
        assert result.rule_version
