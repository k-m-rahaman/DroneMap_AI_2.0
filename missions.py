"""Metric survey planning and QGroundControl waypoint-only plan export.

Plans are previews for a pilot to inspect in their ground station. They contain
no takeoff, arming, landing, mission-upload, or camera-trigger commands.
"""
from __future__ import annotations

import math

from pyproj import CRS, Transformer
from shapely.affinity import rotate
from shapely.geometry import LineString, mapping, shape
from shapely.ops import transform

from geospatial import validate_geometry

MAX_AREA_SQM = 1_000_000
MAX_WAYPOINTS = 1000


def _number(payload, name, default, lower, upper, *, integer=False):
    value = payload.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    if not lower <= value <= upper or (integer and int(value) != value):
        raise ValueError(f"{name} must be {'an integer ' if integer else ''}between {lower} and {upper}.")
    return int(value) if integer else float(value)


def plan_survey(payload):
    if not isinstance(payload, dict):
        raise ValueError("Survey planning settings must be an object.")
    measured = validate_geometry(payload.get("geometry"))
    geometry = shape(measured["geometry"])
    if geometry.geom_type != "Polygon":
        raise ValueError("Plan one contiguous polygon at a time.")
    if geometry.interiors:
        raise ValueError("Survey planning requires an area without holes. Plan separate convex sections around exclusions.")
    if measured["area_sqm"] > MAX_AREA_SQM:
        raise ValueError("Plan a survey area of at most 1 km² at a time.")
    if measured["area_sqm"] < 25:
        raise ValueError("Select a survey area of at least 25 m².")
    center = geometry.centroid
    if abs(center.y) > 80:
        raise ValueError("This local mission planner supports latitudes between 80° south and 80° north.")
    altitude = _number(payload, "altitude_m", 60, 10, 120)
    speed = _number(payload, "speed_m_s", 5, 0.5, 15)
    front = _number(payload, "front_overlap", 0.8, 0.1, 0.95)
    side = _number(payload, "side_overlap", 0.7, 0.1, 0.95)
    sensor_width = _number(payload, "sensor_width_mm", 13.2, 1, 100)
    sensor_height = _number(payload, "sensor_height_mm", 8.8, 1, 100)
    focal = _number(payload, "focal_length_mm", 8.8, 1, 200)
    image_width = _number(payload, "image_width_px", 5472, 100, 20_000, integer=True)
    image_height = _number(payload, "image_height_px", 3648, 100, 20_000, integer=True)
    angle = _number(payload, "angle_deg", payload.get("angle", 0), 0, 180)
    local_crs = CRS.from_proj4(f"+proj=aeqd +lat_0={center.y:.12f} +lon_0={center.x:.12f} +datum=WGS84 +units=m +no_defs")
    forward = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
    inverse = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True)
    local_polygon = transform(forward.transform, geometry)
    west, south, east, north = local_polygon.bounds
    if math.hypot(east - west, north - south) > 4000:
        raise ValueError("The survey extent must fit within 4 km. Split long corridors into smaller sections.")
    missing_area = local_polygon.convex_hull.area - local_polygon.area
    if missing_area > max(0.01, local_polygon.area * 1e-7):
        raise ValueError("Use a convex survey area so connecting flight segments stay inside it. Split concave areas into convex sections.")

    footprint_side = altitude * sensor_width / focal
    footprint_front = altitude * sensor_height / focal
    max_line_spacing = footprint_side * (1 - side)
    trigger_spacing = footprint_front * (1 - front)
    gsd_cm = max(footprint_side / image_width, footprint_front / image_height) * 100
    origin = local_polygon.centroid.coords[0]
    # angle_deg is a clockwise bearing from north. Rotating the AOI CCW by
    # that bearing allows north/south transects to be clipped in local metres.
    grid = rotate(local_polygon, angle, origin=origin)
    left, bottom, right, top = grid.bounds
    line_count = max(1, math.ceil((right - left) / max_line_spacing))
    if line_count * 2 > MAX_WAYPOINTS:
        raise ValueError("These settings exceed 1,000 waypoints. Reduce the area or overlap, or increase altitude.")
    actual_line_spacing = (right - left) / line_count
    local_routes = []
    route_coordinates = []
    photos = 0
    for index in range(line_count):
        x = left + (index + 0.5) * actual_line_spacing
        clipped = grid.intersection(LineString([(x, bottom - 1), (x, top + 1)]))
        if clipped.is_empty or clipped.length < 0.01:
            continue
        if clipped.geom_type != "LineString":
            raise ValueError("The area cannot be represented by a continuous survey grid. Split it into smaller convex sections.")
        segment = rotate(clipped, -angle, origin=origin)
        coordinates = [segment.coords[0], segment.coords[-1]]
        if index % 2:
            coordinates.reverse()
        local_routes.append(LineString(coordinates))
        route_coordinates.extend(coordinates)
        photos += math.ceil(segment.length / trigger_spacing) + 1
    if not local_routes or len(route_coordinates) > MAX_WAYPOINTS:
        raise ValueError("The selected area does not produce a valid survey route within the waypoint limit.")
    route = LineString(route_coordinates)
    # Numerical projection/rotation differences are tolerated by 1 mm only.
    if not local_polygon.buffer(0.001).covers(route):
        raise ValueError("A connecting flight segment falls outside the selected area. Use a smaller convex section.")
    geographic_route = transform(inverse.transform, route)
    geographic_segments = [transform(inverse.transform, segment) for segment in local_routes]
    first = geographic_route.coords[0]
    items = []
    for index, (longitude, latitude) in enumerate(geographic_route.coords, start=1):
        items.append({
            "AMSLAltAboveTerrain": None, "Altitude": altitude, "AltitudeMode": 1,
            "autoContinue": True, "command": 16, "doJumpId": index, "frame": 3,
            "params": [0, 0, 0, None, latitude, longitude, altitude], "type": "SimpleItem",
        })
    # Home AMSL is unknown without surveyed terrain/aircraft data. Zero is a
    # placeholder, clearly called out below, never a measured ground elevation.
    plan = {
        "fileType": "Plan", "groundStation": "QGroundControl", "version": 1,
        "geoFence": {"circles": [], "polygons": [], "version": 2},
        "rallyPoints": {"points": [], "version": 2},
        "mission": {
            "version": 2, "firmwareType": 12, "vehicleType": 2,
            "globalPlanAltitudeMode": 1, "cruiseSpeed": speed, "hoverSpeed": speed,
            "plannedHomePosition": [first[1], first[0], 0], "items": items,
        },
    }
    limitations = [
        "Planning preview only. Inspect aircraft, firmware, home elevation, altitude, route and return/landing procedures in a compatible ground station before use.",
        "Waypoints use metres above home (MAV_FRAME_GLOBAL_RELATIVE_ALT). Home AMSL elevation is an unknown zero placeholder; set the actual home in the ground station.",
        "Export uses a PX4 multirotor template. Select the actual aircraft and firmware in the ground station; DJI SDK connections are not supported by this MAVLink link.",
        "Route contains waypoints only: no arming, takeoff, landing, mission upload, camera triggering or flight execution is performed by this app.",
        "GSD, photo count and overlap assume level terrain, a nadir camera and the supplied physical camera parameters. Set camera triggering separately on the aircraft/controller.",
        "Flight time estimates cover the horizontal route at the selected speed, excluding ascent, descent, turns, wind and battery reserve. Obstacles, terrain, airspace and legal permissions are not assessed.",
    ]
    return {
        "summary": {
            "area_sqm": measured["area_sqm"], "estimated_flight_seconds": round(route.length / speed, 1),
            "route_distance_m": round(route.length, 2), "estimated_photos": photos,
            "gsd_cm": round(gsd_cm, 3), "line_spacing_m": round(actual_line_spacing, 3),
            "trigger_spacing_m": round(trigger_spacing, 3), "waypoint_count": len(items),
            "transect_count": len(local_routes), "altitude_m": altitude, "speed_m_s": speed,
            "angle_deg": angle,
        },
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"kind": "survey_route", "altitude_m": altitude}, "geometry": mapping(geographic_route)},
            *[{"type": "Feature", "properties": {"kind": "transect", "index": index + 1}, "geometry": mapping(segment)}
              for index, segment in enumerate(geographic_segments)],
        ]},
        "plan": plan, "limitations": limitations,
    }
