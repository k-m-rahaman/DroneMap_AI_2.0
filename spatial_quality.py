"""Conservative spatial reconciliation and project topology checks.

These checks identify duplicate/overlapping mapped features. They do not infer
ownership or establish survey positional accuracy. Parcel gaps are not inferred
without an authoritative coverage boundary (roads/open areas can be intentional).
"""
from __future__ import annotations

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from geospatial import validate_geometry

MAX_FEATURES = 12_000
MAX_TOTAL_VERTICES = 250_000


def _prepare(features):
    if not isinstance(features, list) or len(features) > MAX_FEATURES:
        raise ValueError("Check at most 12,000 boundaries at a time; split larger projects.")
    normalized, polygons = [], []
    vertex_count = 0
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"Boundary {index + 1} must be a GeoJSON Feature.")
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            raise ValueError(f"Boundary {index + 1} has invalid properties.")
        geometry = feature.get("geometry")
        measured = validate_geometry(geometry)
        polygon = shape(measured["geometry"])
        pieces = [polygon] if polygon.geom_type == "Polygon" else polygon.geoms
        vertex_count += sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in pieces)
        if vertex_count > MAX_TOTAL_VERTICES:
            raise ValueError("Too many boundary vertices for one check; split the survey.")
        normalized.append({**feature, "geometry": measured["geometry"], "properties": dict(props)})
        polygons.append(polygon)
    if not polygons:
        return normalized, []
    west = min(p.bounds[0] for p in polygons)
    south = min(p.bounds[1] for p in polygons)
    east = max(p.bounds[2] for p in polygons)
    north = max(p.bounds[3] for p in polygons)
    if east - west > 5 or north - south > 5 or max(abs(south), abs(north)) > 85:
        raise ValueError("Spatial checks require a local survey within 5 degrees and 85 degrees latitude.")
    projection = Transformer.from_crs("EPSG:4326", f"+proj=aeqd +lat_0={(south+north)/2} +lon_0={(west+east)/2} +datum=WGS84 +units=m", always_xy=True)
    return normalized, [transform(projection.transform, p) for p in polygons]


def _kind(feature):
    return feature["properties"].get("feature_type", "building")


def _identifier(feature, index):
    return str(feature.get("id") or feature["properties"].get("id") or f"boundary-{index+1}")[:200]


def reconcile_proposals(proposals, existing):
    """Keep proposals unless they strongly match an existing building footprint."""
    normalized, polygons = _prepare(existing + proposals)
    offset = len(existing)
    tree = STRtree(polygons) if polygons else None
    kept, duplicates, accepted = [], [], set(range(offset))
    for index in range(offset, len(polygons)):
        candidate = polygons[index]
        best = None
        if _kind(normalized[index]) == "building":
            for other_index in sorted(map(int, tree.query(candidate))):
                if other_index not in accepted or _kind(normalized[other_index]) != "building":
                    continue
                other = polygons[other_index]
                intersection = candidate.intersection(other).area
                if intersection <= 0:
                    continue
                iou = intersection / (candidate.area + other.area - intersection)
                coverage = intersection / min(candidate.area, other.area)
                # Both size and shape must match. Adjacent touching buildings,
                # slight overlaps and small nested structures remain reviewable.
                if iou >= 0.55 and coverage >= 0.85 and (best is None or iou > best[0]):
                    best = (iou, coverage, other_index)
        if best:
            iou, coverage, other_index = best
            duplicates.append({"proposal_index": index-offset, "existing_id": _identifier(normalized[other_index], other_index),
                "iou": round(iou, 5), "smaller_coverage": round(coverage, 5)})
        else:
            accepted.add(index)
            kept.append(normalized[index])
    return {"type": "FeatureCollection", "features": kept, "duplicates": duplicates,
        "summary": {"received": len(proposals), "duplicates": len(duplicates), "kept": len(kept)}}


def project_quality(features, *, max_issues=500):
    normalized, polygons = _prepare(features)
    issues, pairs, truncated = [], 0, False
    tree = STRtree(polygons) if polygons else None
    for index, polygon in enumerate(polygons):
        for other_index in sorted(map(int, tree.query(polygon))):
            if other_index <= index or _kind(normalized[index]) != _kind(normalized[other_index]):
                continue
            pairs += 1
            if pairs > 100_000:
                raise ValueError("Too many intersecting boundaries for one check; split the survey.")
            other = polygons[other_index]
            area = polygon.intersection(other).area
            if area < 0.1 or area / min(polygon.area, other.area) < 0.001:
                continue
            iou = area / (polygon.area + other.area - area)
            if len(issues) >= max_issues:
                truncated = True
                continue
            issues.append({"kind": "duplicate" if iou >= 0.85 else "overlap", "feature_type": _kind(normalized[index]),
                "feature_ids": [_identifier(normalized[index], index), _identifier(normalized[other_index], other_index)],
                "names": [str(normalized[index]["properties"].get("name", ""))[:200], str(normalized[other_index]["properties"].get("name", ""))[:200]],
                "overlap_sqm": round(area, 4), "iou": round(iou, 5)})
    # Buildings naturally lie within parcels: summarize each layer separately.
    layers = {}
    for kind in sorted({_kind(f) for f in normalized}):
        selected = [p for f, p in zip(normalized, polygons) if _kind(f) == kind]
        total = sum(p.area for p in selected)
        covered = unary_union(selected).area
        layers[kind] = {"features": len(selected), "summed_area_sqm": round(total, 3), "covered_area_sqm": round(covered, 3),
            "overcount_sqm": round(max(0, total-covered), 3)}
    return {"issues": issues, "summary": {"features": len(normalized), "issues": len(issues), "truncated": truncated, "layers": layers},
        "limitations": ["Overlaps are review warnings; multi-level structures or intentional overlaps can be valid.",
            "No gap/coverage claim is made without an authoritative survey coverage boundary.",
            "Geometry checks do not establish land ownership or positional accuracy."]}
