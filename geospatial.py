"""Validation and WGS84 ellipsoidal measurements for local survey geometry."""

from __future__ import annotations

import math
from numbers import Real

from pyproj import Geod
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

GEOD = Geod(ellps="WGS84")
MAX_VERTICES = 20_000


def validate_bounds(bounds, *, mercator=False):
    """Return a finite local [west, south, east, north] geographic extent."""
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise ValueError("Bounds must be [west, south, east, north].")
    if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in bounds):
        raise ValueError("Bounds must contain four finite numbers.")
    west, south, east, north = map(float, bounds)
    latitude_limit = 85.05112878 if mercator else 90.0
    if not (-180 <= west < east <= 180 and -latitude_limit <= south < north <= latitude_limit):
        raise ValueError("Bounds must be ordered, within valid longitude/latitude ranges, and not cross the antimeridian.")
    if east - west >= 180:
        raise ValueError("Use a local survey extent smaller than 180 degrees of longitude.")
    return [west, south, east, north]


def _check_coordinates(coordinates, geometry_type):
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates
    if not isinstance(polygons, (list, tuple)) or not polygons:
        raise ValueError("A polygon must contain at least one exterior ring.")
    count = 0
    for polygon in polygons:
        if not isinstance(polygon, (list, tuple)) or not polygon:
            raise ValueError("Each polygon must contain an exterior ring.")
        for ring in polygon:
            if not isinstance(ring, (list, tuple)) or len(ring) < 4:
                raise ValueError("Each polygon ring needs at least four positions including closure.")
            if ring[0] != ring[-1]:
                raise ValueError("Polygon rings must be closed (first and last positions equal).")
            for position in ring:
                count += 1
                if count > MAX_VERTICES:
                    raise ValueError(f"Geometry exceeds the {MAX_VERTICES:,}-vertex limit.")
                if not isinstance(position, (list, tuple)) or len(position) != 2:
                    raise ValueError("Positions must be [longitude, latitude] pairs.")
                if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in position):
                    raise ValueError("Geometry contains a non-finite or non-numeric coordinate.")
                lon, lat = position
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise ValueError("Geometry coordinates are outside WGS84 longitude/latitude ranges.")


def _ring_measure(ring):
    longitude, latitude = zip(*ring.coords)
    area, perimeter = GEOD.polygon_area_perimeter(longitude, latitude)
    return abs(float(area)), float(perimeter)


def validate_geometry(geometry):
    """Reject invalid GeoJSON and measure all polygons and holes geodesically.

    This validates geometry, not ownership or positional survey accuracy. Inputs
    crossing the antimeridian are outside this local cadastral workspace's scope.
    Perimeter includes exterior boundaries and hole boundaries.
    """
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError("Geometry must be a GeoJSON Polygon or MultiPolygon.")
    _check_coordinates(geometry.get("coordinates"), geometry["type"])
    try:
        polygon = shape(geometry)
    except (ValueError, TypeError, IndexError) as exc:
        raise ValueError("Malformed polygon geometry.") from exc
    if polygon.is_empty or polygon.area <= 0:
        raise ValueError("Geometry is empty or degenerate.")
    if not polygon.is_valid:
        raise ValueError(f"Invalid polygon: {explain_validity(polygon)}")
    west, south, east, north = polygon.bounds
    validate_bounds([west, south, east, north])
    pieces = [polygon] if isinstance(polygon, Polygon) else list(polygon.geoms)
    normalized = [orient(piece, sign=1.0) for piece in pieces]
    total_area = 0.0
    total_perimeter = 0.0
    for piece in normalized:
        area, perimeter = _ring_measure(piece.exterior)
        for hole in piece.interiors:
            hole_area, hole_perimeter = _ring_measure(hole)
            area -= hole_area
            perimeter += hole_perimeter
        total_area += area
        total_perimeter += perimeter
    if not math.isfinite(total_area) or total_area <= 0 or not math.isfinite(total_perimeter):
        raise ValueError("Geometry has no measurable positive geodesic area.")
    result_geometry = normalized[0] if geometry["type"] == "Polygon" else MultiPolygon(normalized)
    # JSON round trip converts Shapely tuples and any scalar subclasses into
    # plain JSON containers; all measurement values are native Python floats.
    import json
    return {
        "geometry": json.loads(json.dumps(mapping(result_geometry))),
        "area_sqm": round(float(total_area), 4),
        "perimeter_m": round(float(total_perimeter), 4),
        "bbox": [float(v) for v in result_geometry.bounds],
    }
