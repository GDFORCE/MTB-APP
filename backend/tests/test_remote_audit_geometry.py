"""Unit tests for the pure geometry primitives underlying geofence
classification (app/services/remote_audit/geometry.py)."""
from __future__ import annotations

import math

import pytest

from app.services.remote_audit.geometry import (
    InvalidGeometryError,
    circle_to_polygon,
    geodesic_distance_m,
    min_distance_to_boundary_m,
    point_in_multipolygon,
    point_in_polygon,
    validate_multipolygon,
    validate_polygon,
)

# A simple ~200m x 200m square campus, roughly centered near a real-world
# latitude so the equirectangular projection's cos(lat) term is exercised.
SQUARE = [
    [
        (77.0000, 28.0000),
        (77.0020, 28.0000),
        (77.0020, 28.0018),
        (77.0000, 28.0018),
        (77.0000, 28.0000),
    ]
]
CENTER = (77.0010, 28.0009)
OUTSIDE_FAR = (77.0100, 28.0100)


def test_point_inside_square_is_inside():
    assert point_in_polygon(CENTER, SQUARE) is True


def test_point_far_outside_is_outside():
    assert point_in_polygon(OUTSIDE_FAR, SQUARE) is False


def test_point_exactly_on_vertex_counts_as_inside_st_covers_semantics():
    vertex = SQUARE[0][0]
    assert point_in_polygon(vertex, SQUARE) is True


def test_point_exactly_on_edge_counts_as_inside_st_covers_semantics():
    midpoint_of_bottom_edge = (77.0010, 28.0000)
    assert point_in_polygon(midpoint_of_bottom_edge, SQUARE) is True


def test_geodesic_distance_known_value_one_degree_latitude():
    # 1 degree of latitude is ~111.19 km everywhere on the sphere.
    d = geodesic_distance_m((0.0, 0.0), (0.0, 1.0))
    assert 111_000 < d < 111_400


def test_geodesic_distance_zero_for_identical_points():
    assert geodesic_distance_m((77.0, 28.0), (77.0, 28.0)) == 0.0


def test_distance_to_boundary_is_true_edge_distance_not_centroid_distance():
    """v1.3 correction #16: for an elongated rectangle, the centroid is far
    from a point near one short edge, but the true boundary distance is tiny.
    A centroid-distance shortcut would give a materially different (wrong)
    number here."""
    long_thin_rect = [
        [
            (0.0, 0.0),
            (0.0100, 0.0),   # ~1.1km long
            (0.0100, 0.0005),  # ~55m wide
            (0.0, 0.0005),
            (0.0, 0.0),
        ]
    ]
    point_near_short_edge = (0.00001, 0.00025)  # just inside, near the x=0 edge
    d = min_distance_to_boundary_m(point_near_short_edge, long_thin_rect)
    assert d < 5.0  # meters -- true edge distance is tiny
    # the centroid is roughly (0.005, 0.00025) -- a centroid-based distance
    # would be on the order of 550 meters, wildly different from the truth.


def test_polygon_with_hole_point_inside_hole_is_outside():
    donut = [
        SQUARE[0],
        [  # a hole in the middle of the square
            (77.0008, 28.0006),
            (77.0012, 28.0006),
            (77.0012, 28.0012),
            (77.0008, 28.0012),
            (77.0008, 28.0006),
        ],
    ]
    point_in_hole = (77.0010, 28.0009)
    point_in_ring_between = (77.0002, 28.0002)
    assert point_in_polygon(point_in_hole, donut) is False
    assert point_in_polygon(point_in_ring_between, donut) is True


def test_polygon_with_hole_point_on_hole_boundary_is_inside():
    donut = [
        SQUARE[0],
        [
            (77.0008, 28.0006),
            (77.0012, 28.0006),
            (77.0012, 28.0012),
            (77.0008, 28.0012),
            (77.0008, 28.0006),
        ],
    ]
    point_on_hole_edge = (77.0010, 28.0006)
    assert point_in_polygon(point_on_hole_edge, donut) is True


def test_winding_order_does_not_change_classification():
    """v1.3 correction #16: reversing vertex order must never flip the
    inside/outside result (winding-agnostic even-odd rule)."""
    reversed_square = [list(reversed(SQUARE[0]))]
    assert point_in_polygon(CENTER, SQUARE) == point_in_polygon(CENTER, reversed_square)
    assert point_in_polygon(OUTSIDE_FAR, SQUARE) == point_in_polygon(OUTSIDE_FAR, reversed_square)


def test_multipolygon_multi_building_campus():
    building_a = SQUARE[0]
    building_b = [
        (77.0100, 28.0100),
        (77.0120, 28.0100),
        (77.0120, 28.0118),
        (77.0100, 28.0118),
        (77.0100, 28.0100),
    ]
    campus = [[building_a], [building_b]]
    far_from_both_buildings = (77.5000, 28.5000)
    assert point_in_multipolygon(CENTER, campus) is True
    assert point_in_multipolygon((77.0110, 28.0110), campus) is True
    assert point_in_multipolygon(far_from_both_buildings, campus) is False


def test_valid_polygon_and_multipolygon_pass_validation():
    validate_polygon(SQUARE)
    validate_multipolygon([SQUARE])


def test_unclosed_ring_is_rejected():
    unclosed = [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]]
    with pytest.raises(InvalidGeometryError) as exc:
        validate_polygon(unclosed)
    assert exc.value.code == "RING_NOT_CLOSED"


def test_out_of_range_latitude_is_rejected():
    bad = [[(0.0, 0.0), (1.0, 0.0), (1.0, 91.0), (0.0, 91.0), (0.0, 0.0)]]
    with pytest.raises(InvalidGeometryError) as exc:
        validate_polygon(bad)
    assert exc.value.code == "LATITUDE_OUT_OF_RANGE"


def test_out_of_range_latitude_from_swapped_lat_lon_bug_is_rejected():
    """Guards the spec's own named edge case (§16.1): a lat/lon swap bug
    produces an out-of-range value that must be rejected, not silently
    accepted as a remote location -- here for a real-world point (Bangkok,
    ~13.75N 100.50E) whose true longitude magnitude exceeds 90, so swapping
    it into the latitude slot trips range validation."""
    correct_order = (100.50, 13.75)  # (lon, lat)
    swapped_as_if_lon_lat = (correct_order[1], correct_order[0])  # (13.75, 100.50) -- invalid lat
    swapped = [[swapped_as_if_lon_lat, (13.76, 100.50), (13.76, 100.51), (13.75, 100.51), swapped_as_if_lon_lat]]
    with pytest.raises(InvalidGeometryError) as exc:
        validate_polygon(swapped)
    assert exc.value.code == "LATITUDE_OUT_OF_RANGE"


def test_lat_lon_swap_within_valid_ranges_is_not_caught_by_range_validation_alone():
    """Documented, intentional limitation: when BOTH the true longitude and
    true latitude have magnitude <= 90 (as for many real-world locations,
    e.g. much of India), swapping them produces a value that is still valid
    for the other axis -- range validation cannot detect this class of bug.
    Range validation is one layer, not a complete defense; the spec's actual
    authoritative defense against a misplaced pin is REVIEWER MAP APPROVAL at
    site enrollment (spec §7.1 step 3), not automated range checking alone.
    """
    correct_order = (77.20, 28.60)  # New Delhi-ish (lon, lat), both <= 90 in magnitude
    swapped_as_if_lon_lat = (correct_order[1], correct_order[0])  # (28.60, 77.20) -- both still valid
    swapped = [[swapped_as_if_lon_lat, (28.61, 77.20), (28.61, 77.21), (28.60, 77.21), swapped_as_if_lon_lat]]
    validate_polygon(swapped)  # does not raise -- the documented gap


def test_self_intersecting_polygon_is_rejected():
    bowtie = [[(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)]]
    with pytest.raises(InvalidGeometryError) as exc:
        validate_polygon(bowtie)
    assert exc.value.code == "SELF_INTERSECTING_RING"


def test_unsplit_antimeridian_crossing_is_rejected():
    """v1.3 correction #16: a geofence naively crossing +/-180 degrees longitude
    must be rejected with a clear code, not misclassified."""
    crossing = [[(179.0, 10.0), (-179.0, 10.0), (-179.0, 11.0), (179.0, 11.0), (179.0, 10.0)]]
    with pytest.raises(InvalidGeometryError) as exc:
        validate_polygon(crossing)
    assert exc.value.code == "UNSPLIT_ANTIMERIDIAN_CROSSING"


def test_too_few_vertices_is_rejected():
    with pytest.raises(InvalidGeometryError) as exc:
        validate_polygon([[(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)]])
    assert exc.value.code == "RING_TOO_SHORT"


def test_repeated_evaluation_is_deterministic_precision_stability():
    """v1.3 correction #16: precision/rounding must not flip a classification
    between runs -- this is pure deterministic math, so it never should."""
    results = {point_in_polygon(CENTER, SQUARE) for _ in range(50)}
    assert results == {True}
    distances = {round(min_distance_to_boundary_m(CENTER, SQUARE), 6) for _ in range(50)}
    assert len(distances) == 1


# ── circle_to_polygon (Phase 3: zero-map-interaction geofence default) ──────

def test_circle_to_polygon_vertices_are_at_the_correct_radius():
    center = (77.0000, 28.0000)
    radius_m = 150.0
    polygon = circle_to_polygon(center, radius_m, sides=64)
    ring = polygon[0]
    for vertex in ring[:-1]:  # exclude the closing duplicate of the first point
        distance = geodesic_distance_m(center, vertex)
        assert abs(distance - radius_m) < 0.5, f"vertex {vertex} is {distance}m from center, expected ~{radius_m}m"


def test_circle_to_polygon_is_closed_and_passes_validation():
    polygon = circle_to_polygon((77.0, 28.0), 100.0, sides=32)
    ring = polygon[0]
    assert ring[0] == ring[-1]
    validate_polygon(polygon)  # must not raise


def test_circle_to_polygon_center_point_is_inside():
    center = (77.0, 28.0)
    polygon = circle_to_polygon(center, 100.0)
    assert point_in_polygon(center, polygon) is True


def test_circle_to_polygon_point_well_outside_radius_is_outside():
    center = (77.0, 28.0)
    polygon = circle_to_polygon(center, 100.0)
    far_point = (77.01, 28.01)  # roughly 1.5km away
    assert point_in_polygon(far_point, polygon) is False


def test_circle_to_polygon_classification_distance_matches_radius_approximately():
    """A point placed exactly `radius_m` + a small delta from center should
    report a boundary distance close to that delta -- proving the
    circle-as-polygon's distance math is consistent with a true circle, not
    just its inside/outside test."""
    center = (77.0, 28.0)
    radius_m = 200.0
    polygon = circle_to_polygon(center, radius_m, sides=128)
    # A point 250m from center along a cardinal direction (due east).
    lat0_rad = math.radians(center[1])
    dlon_deg = math.degrees(250.0 / (6_371_000.0 * math.cos(lat0_rad)))
    point_250m_east = (center[0] + dlon_deg, center[1])
    distance = min_distance_to_boundary_m(point_250m_east, polygon)
    assert abs(distance - 50.0) < 1.0  # 250m out, 200m radius -> ~50m outside the boundary


def test_circle_to_polygon_rejects_non_positive_radius():
    with pytest.raises(InvalidGeometryError) as exc:
        circle_to_polygon((77.0, 28.0), 0.0)
    assert exc.value.code == "INVALID_RADIUS"
    with pytest.raises(InvalidGeometryError):
        circle_to_polygon((77.0, 28.0), -10.0)
