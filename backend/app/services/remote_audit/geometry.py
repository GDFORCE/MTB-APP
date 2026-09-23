"""Pure geodesic/geometry primitives for geofence classification.

No PostGIS, no live Mongo geo query — deterministic Python math, chosen per
Production-Grade Implementation Plan v1.3 correction #16: "where Mongo's
built-in geospatial operators cannot exactly express part of the spec's
PostGIS-based algorithm, implement the equivalent deterministic geometry
logic explicitly in application code ... never silently approximate."

Scale assumption, stated explicitly rather than silently relied on: geofences
in this module are single hospital campuses (at most a few km across, per the
spec's own "multi-building campus" scenario) — well within the range where a
local equirectangular (flat-earth) projection introduces sub-meter error,
negligible next to the tens-of-meters GPS accuracy bands this module already
works with (spec §7.4). Point-to-point distance uses exact haversine.

GeoJSON coordinate convention throughout: (longitude, latitude), matching the
GeoJSON spec (RFC 7946) and this module's Mongo storage format.
"""
from __future__ import annotations

import math
from typing import Literal, Sequence, Tuple, TypedDict, Union

Point = Tuple[float, float]  # (lon, lat)
Ring = Sequence[Point]
PolygonCoords = Sequence[Ring]          # [exterior, *holes]
MultiPolygonCoords = Sequence[PolygonCoords]

EARTH_RADIUS_M = 6_371_000.0
# A ring whose bounding-box longitude span exceeds this is either an unsplit
# antimeridian crossing or a data error (e.g. swapped coordinates). Standard
# GIS practice is to pre-split antimeridian-crossing geometry into a
# MultiPolygon, so this module rejects the unsplit form rather than guessing.
MAX_PLAUSIBLE_LONGITUDE_SPAN_DEG = 180.0
_EPS = 1e-9


class Geometry(TypedDict):
    type: Literal["Polygon", "MultiPolygon"]
    coordinates: Union[PolygonCoords, MultiPolygonCoords]


class InvalidGeometryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── Validation ────────────────────────────────────────────────────────────

def _validate_point(p: Point) -> None:
    lon, lat = p
    if not (-180.0 <= lon <= 180.0):
        raise InvalidGeometryError("LONGITUDE_OUT_OF_RANGE", f"longitude {lon} out of [-180, 180]")
    if not (-90.0 <= lat <= 90.0):
        raise InvalidGeometryError("LATITUDE_OUT_OF_RANGE", f"latitude {lat} out of [-90, 90]")


def _orientation(p: Point, q: Point, r: Point) -> int:
    val = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
    if abs(val) < 1e-12:
        return 0
    return 1 if val > 0 else 2


def _on_segment(p: Point, q: Point, r: Point) -> bool:
    return (min(p[0], r[0]) - _EPS <= q[0] <= max(p[0], r[0]) + _EPS
            and min(p[1], r[1]) - _EPS <= q[1] <= max(p[1], r[1]) + _EPS)


def _segments_intersect(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    """Standard orientation-based proper/touching segment intersection test."""
    o1, o2 = _orientation(a1, a2, b1), _orientation(a1, a2, b2)
    o3, o4 = _orientation(b1, b2, a1), _orientation(b1, b2, a2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(a1, b1, a2):
        return True
    if o2 == 0 and _on_segment(a1, b2, a2):
        return True
    if o3 == 0 and _on_segment(b1, a1, b2):
        return True
    if o4 == 0 and _on_segment(b1, a2, b2):
        return True
    return False


def _ring_is_self_intersecting(ring: Ring) -> bool:
    n = len(ring) - 1  # ring is closed: last point duplicates the first
    edges = [(ring[i], ring[i + 1]) for i in range(n)]
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            # Adjacent edges (including the wrap-around pair) share an
            # endpoint by construction -- that shared vertex is not itself a
            # self-intersection.
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            if _segments_intersect(*edges[i], *edges[j]):
                return True
    return False


def _validate_ring(ring: Ring) -> None:
    if len(ring) < 4:
        raise InvalidGeometryError(
            "RING_TOO_SHORT", "a ring needs at least 4 positions (closed, 3 distinct vertices)")
    if ring[0] != ring[-1]:
        raise InvalidGeometryError(
            "RING_NOT_CLOSED", "a GeoJSON ring's first and last positions must match")
    for p in ring:
        _validate_point(p)
    lons = [p[0] for p in ring]
    if max(lons) - min(lons) > MAX_PLAUSIBLE_LONGITUDE_SPAN_DEG:
        raise InvalidGeometryError(
            "UNSPLIT_ANTIMERIDIAN_CROSSING",
            "ring spans more than 180 degrees of longitude -- split antimeridian-crossing "
            "geometry into a MultiPolygon before submitting it",
        )
    if _ring_is_self_intersecting(ring):
        raise InvalidGeometryError("SELF_INTERSECTING_RING", "ring edges must not cross themselves")


def validate_polygon(polygon: PolygonCoords) -> None:
    if not polygon:
        raise InvalidGeometryError("EMPTY_POLYGON", "a polygon needs at least an exterior ring")
    for ring in polygon:
        _validate_ring(ring)


def validate_multipolygon(multipolygon: MultiPolygonCoords) -> None:
    if not multipolygon:
        raise InvalidGeometryError("EMPTY_MULTIPOLYGON", "a MultiPolygon needs at least one polygon")
    for polygon in multipolygon:
        validate_polygon(polygon)


def validate_geometry(geometry: Geometry) -> None:
    if geometry["type"] == "Polygon":
        validate_polygon(geometry["coordinates"])  # type: ignore[arg-type]
    elif geometry["type"] == "MultiPolygon":
        validate_multipolygon(geometry["coordinates"])  # type: ignore[arg-type]
    else:
        raise InvalidGeometryError("UNSUPPORTED_GEOMETRY_TYPE", f"unsupported type {geometry['type']!r}")


# ── Point-in-polygon (boundary-inclusive, winding-order-agnostic) ──────────

def _point_on_segment(p: Point, a: Point, b: Point, tolerance_deg: float = 1e-9) -> bool:
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > tolerance_deg:
        return False
    return (min(a[0], b[0]) - tolerance_deg <= p[0] <= max(a[0], b[0]) + tolerance_deg
            and min(a[1], b[1]) - tolerance_deg <= p[1] <= max(a[1], b[1]) + tolerance_deg)


def _point_on_ring_boundary(point: Point, ring: Ring) -> bool:
    n = len(ring) - 1
    return any(_point_on_segment(point, ring[i], ring[i + 1]) for i in range(n))


def _point_in_ring(point: Point, ring: Ring) -> bool:
    """Even-odd ray-casting rule: winding-direction-agnostic by construction
    (v1.3 correction #16 -- vertex order can never flip the result), and
    boundary-inclusive (a point exactly on an edge counts as inside, matching
    PostGIS ST_Covers semantics rather than a strict ST_Contains)."""
    if _point_on_ring_boundary(point, ring):
        return True
    x, y = point
    n = len(ring) - 1
    inside = False
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y):
            x_intersect = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_intersect:
                inside = not inside
    return inside


def point_in_polygon(point: Point, polygon: PolygonCoords) -> bool:
    """True if `point` is inside the exterior ring and outside every hole.
    A point on a hole's boundary counts as inside (it is on the boundary of
    the exterior-minus-hole region, which ST_Covers-style semantics treat as
    covered)."""
    exterior, *holes = polygon
    if not _point_in_ring(point, exterior):
        return False
    for hole in holes:
        if _point_in_ring(point, hole) and not _point_on_ring_boundary(point, hole):
            return False
    return True


def point_in_multipolygon(point: Point, multipolygon: MultiPolygonCoords) -> bool:
    return any(point_in_polygon(point, polygon) for polygon in multipolygon)


def point_in_geometry(point: Point, geometry: Geometry) -> bool:
    if geometry["type"] == "Polygon":
        return point_in_polygon(point, geometry["coordinates"])  # type: ignore[arg-type]
    return point_in_multipolygon(point, geometry["coordinates"])  # type: ignore[arg-type]


# ── Distance ─────────────────────────────────────────────────────────────

def geodesic_distance_m(a: Point, b: Point) -> float:
    """Exact haversine great-circle distance in meters between two points."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _local_xy(point: Point, origin: Point) -> Tuple[float, float]:
    """Equirectangular projection onto a local tangent plane centered at
    `origin`, in meters -- see module docstring for the stated scale bound."""
    lon, lat = point
    lon0, lat0 = origin
    x = math.radians(lon - lon0) * math.cos(math.radians(lat0)) * EARTH_RADIUS_M
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def _point_to_segment_distance_m(point: Point, a: Point, b: Point, origin: Point) -> float:
    px, py = _local_xy(point, origin)
    ax, ay = _local_xy(a, origin)
    bx, by = _local_xy(b, origin)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    closest_x, closest_y = ax + t * dx, ay + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def min_distance_to_boundary_m(point: Point, polygon: PolygonCoords) -> float:
    """True minimum distance from `point` to the polygon's boundary --
    every ring (exterior and holes), never a point-to-centroid shortcut,
    which would be a materially different and wrong number for anything but
    a near-circular polygon (v1.3 correction #16)."""
    origin = point
    return min(
        _point_to_segment_distance_m(point, ring[i], ring[i + 1], origin)
        for ring in polygon
        for i in range(len(ring) - 1)
    )


def min_distance_to_boundary_m_multi(point: Point, multipolygon: MultiPolygonCoords) -> float:
    return min(min_distance_to_boundary_m(point, polygon) for polygon in multipolygon)


def distance_to_geometry_boundary_m(point: Point, geometry: Geometry) -> float:
    if geometry["type"] == "Polygon":
        return min_distance_to_boundary_m(point, geometry["coordinates"])  # type: ignore[arg-type]
    return min_distance_to_boundary_m_multi(point, geometry["coordinates"])  # type: ignore[arg-type]


def circle_to_polygon(center: Point, radius_m: float, *, sides: int = 64) -> PolygonCoords:
    """Approximates a circle as a closed N-sided polygon ring, in the same
    local equirectangular projection as `_local_xy`/`_point_to_segment_distance_m`
    above (accurate at hospital-campus scale, per this module's stated scale
    assumption). This is the ONE place a circle becomes a polygon — reused
    both to feed the exact same `point_in_geometry`/`distance_to_geometry_boundary_m`
    classification code unchanged, and to supply the vertex list for the
    Static Maps preview image's `path=` parameter (Phase 3), so there is no
    second implementation that could drift from this one.
    """
    if radius_m <= 0:
        raise InvalidGeometryError("INVALID_RADIUS", "radius_m must be positive")
    lon0, lat0 = center
    _validate_point(center)
    lat0_rad = math.radians(lat0)
    points = []
    for i in range(sides):
        angle = 2 * math.pi * i / sides
        dx = radius_m * math.cos(angle)
        dy = radius_m * math.sin(angle)
        dlat_deg = math.degrees(dy / EARTH_RADIUS_M)
        dlon_deg = math.degrees(dx / (EARTH_RADIUS_M * math.cos(lat0_rad)))
        points.append((lon0 + dlon_deg, lat0 + dlat_deg))
    points.append(points[0])  # close the ring, per GeoJSON convention
    return [points]
