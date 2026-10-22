"""Independent metric route, containment, and QGC schema checks."""
import json
import math

import pytest
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform

from conftest import polygon
from missions import plan_survey


def test_metric_camera_calculations_route_containment_and_qgc_export():
    result = plan_survey({"geometry": polygon()})
    summary = result["summary"]
    assert summary["gsd_cm"] == pytest.approx(60 * 13.2 / 8.8 / 5472 * 100, abs=0.001)
    assert summary["trigger_spacing_m"] == pytest.approx(12)
    assert 0 < summary["line_spacing_m"] <= 27
    assert summary["waypoint_count"] >= 4
    assert summary["estimated_photos"] >= summary["waypoint_count"]
    assert summary["estimated_flight_seconds"] == pytest.approx(summary["route_distance_m"] / 5, abs=0.06)
    source = shape(polygon())
    center = source.centroid
    crs = CRS.from_proj4(f"+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m")
    projection = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    boundary = transform(projection.transform, source)
    route = transform(projection.transform, shape(result["geojson"]["features"][0]["geometry"]))
    assert boundary.buffer(0.001).covers(route)
    assert route.length == pytest.approx(summary["route_distance_m"], abs=0.01)
    plan = result["plan"]
    assert plan["fileType"] == "Plan" and plan["version"] == 1
    assert plan["mission"]["version"] == 2
    assert len(plan["mission"]["items"]) == summary["waypoint_count"]
    for index, item in enumerate(plan["mission"]["items"], 1):
        assert item["command"] == 16  # MAV_CMD_NAV_WAYPOINT only
        assert item["frame"] == 3  # relative-to-home altitude
        assert item["doJumpId"] == index
        assert len(item["params"]) == 7
        assert item["params"][6] == 60
    # Strict JSON: no Infinity/NaN or unsupported Shapely/numpy objects.
    json.dumps(result, allow_nan=False)


def test_grid_rotation_changes_bearing_without_changing_metric_camera_scale():
    first = plan_survey({"geometry": polygon(), "angle_deg": 0})
    rotated = plan_survey({"geometry": polygon(), "angle_deg": 45})
    assert first["geojson"] != rotated["geojson"]
    assert first["summary"]["gsd_cm"] == rotated["summary"]["gsd_cm"]
    first_line = rotated["geojson"]["features"][1]["geometry"]["coordinates"]
    assert abs(first_line[0][0] - first_line[1][0]) > 1e-6
    assert abs(first_line[0][1] - first_line[1][1]) > 1e-6


def test_higher_overlap_increases_transects_and_photos():
    lower = plan_survey({"geometry": polygon(), "front_overlap": 0.5, "side_overlap": 0.5})
    higher = plan_survey({"geometry": polygon(), "front_overlap": 0.9, "side_overlap": 0.9})
    assert higher["summary"]["transect_count"] > lower["summary"]["transect_count"]
    assert higher["summary"]["estimated_photos"] > lower["summary"]["estimated_photos"]


@pytest.mark.parametrize("settings", [
    {"altitude_m": math.nan}, {"altitude_m": 0}, {"speed_m_s": -1},
    {"front_overlap": 1}, {"side_overlap": True}, {"focal_length_mm": 0},
    {"image_width_px": 1024.5}, {"angle_deg": 181},
])
def test_invalid_camera_and_grid_values_are_rejected(settings):
    with pytest.raises(ValueError):
        plan_survey({"geometry": polygon(), **settings})


def test_holes_concave_area_and_disconnected_areas_are_rejected():
    with_hole = polygon()
    with_hole["coordinates"].append(polygon(0.0002)["coordinates"][0])
    # A detached inner ring, fully within outer shell.
    for point in with_hole["coordinates"][1]:
        point[0] += 0.0002
        point[1] += 0.0002
    with pytest.raises(ValueError, match="holes"):
        plan_survey({"geometry": with_hole})
    concave = {"type": "Polygon", "coordinates": [[
        [87.7, 24.08], [87.701, 24.08], [87.701, 24.0804],
        [87.7004, 24.0804], [87.7004, 24.081], [87.7, 24.081], [87.7, 24.08],
    ]]}
    with pytest.raises(ValueError, match="convex"):
        plan_survey({"geometry": concave})
    with pytest.raises(ValueError, match="contiguous"):
        plan_survey({"geometry": {"type": "MultiPolygon", "coordinates": [polygon()["coordinates"]]}})


def test_large_area_and_excessive_waypoint_density_are_rejected():
    with pytest.raises(ValueError, match="1 km"):
        plan_survey({"geometry": polygon(0.02)})
    with pytest.raises(ValueError, match="1,000 waypoints"):
        plan_survey({"geometry": polygon(0.004), "altitude_m": 10,
            "side_overlap": 0.95, "sensor_width_mm": 1, "focal_length_mm": 200})
