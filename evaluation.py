"""Evaluate building footprints against independent references, without inference.

GeoJSON is WGS84 longitude/latitude. All overlap and distance calculations use a
local metric projection. Results measure agreement with the supplied reference;
they cannot establish ownership, survey accuracy, or reference quality.
"""
from __future__ import annotations

import math
from collections import deque

from pyproj import CRS, Geod, Transformer
from shapely.geometry import shape
from shapely.ops import transform
from shapely.strtree import STRtree

MAX_FEATURES = 10000
MAX_VERTICES = 500000
MAX_CANDIDATES = 250000
MAX_EXTENT_METRES = 100000


def _geometries(collection, label):
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
        raise ValueError(f"{label} must be a GeoJSON FeatureCollection.")
    if "crs" in collection:
        raise ValueError(f"{label}: remove legacy CRS metadata and reproject to WGS84 longitude/latitude first.")
    features = collection.get("features")
    if not isinstance(features, list) or len(features) > MAX_FEATURES:
        raise ValueError(f"{label} requires at most {MAX_FEATURES} features.")
    geometries, identifiers, vertex_count = [], [], 0
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"{label} feature {index} is not a GeoJSON Feature.")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"{label} feature {index} must be a Polygon or MultiPolygon.")
        coordinates = geometry.get("coordinates")
        polygons = [coordinates] if geometry["type"] == "Polygon" else coordinates
        if not isinstance(polygons, list) or not polygons:
            raise ValueError(f"{label} feature {index} has no polygon coordinates.")
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError(f"{label} feature {index} has no rings.")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError(f"{label} feature {index} requires closed rings of at least four positions.")
                vertex_count += len(ring)
                if vertex_count > MAX_VERTICES:
                    raise ValueError(f"{label} exceeds the {MAX_VERTICES} vertex limit; evaluate smaller geographic areas.")
                for point in ring:
                    if not isinstance(point, (list, tuple)) or len(point) != 2 or any(
                        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                        for value in point
                    ) or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90:
                        raise ValueError(f"{label} feature {index} requires finite WGS84 [longitude, latitude] coordinates.")
        try:
            footprint = shape(geometry)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} feature {index} has invalid coordinates.") from exc
        if footprint.is_empty or not footprint.is_valid or footprint.area <= 0:
            raise ValueError(f"{label} feature {index} has empty, degenerate, or invalid geometry; correct it before evaluation.")
        geometries.append(footprint)
        # Index is always included in matches: duplicate or absent IDs remain unambiguous.
        identifier = feature.get("id", feature.get("properties", {}).get("id") if isinstance(feature.get("properties"), dict) else None)
        identifiers.append(identifier if isinstance(identifier, (str, int)) and not isinstance(identifier, bool) else None)
    return geometries, identifiers


def _metric_projection(geometries):
    west = min(geometry.bounds[0] for geometry in geometries)
    south = min(geometry.bounds[1] for geometry in geometries)
    east = max(geometry.bounds[2] for geometry in geometries)
    north = max(geometry.bounds[3] for geometry in geometries)
    if east - west > 180:
        raise ValueError("Split antimeridian-crossing evaluation data into local areas.")
    _, _, diagonal = Geod(ellps="WGS84").inv(west, south, east, north)
    if diagonal > MAX_EXTENT_METRES or max(abs(south), abs(north)) >= 85:
        raise ValueError("Evaluate local areas below 100 km extent and outside polar regions separately.")
    latitude, longitude = (south + north) / 2, (west + east) / 2
    crs = CRS.from_proj4(f"+proj=aeqd +lat_0={latitude:.12f} +lon_0={longitude:.12f} +datum=WGS84 +units=m +no_defs")
    return Transformer.from_crs("EPSG:4326", crs, always_xy=True), crs.to_string()


def _maximum_matching(adjacency, reference_count):
    """Hopcroft-Karp, iterative DFS: O(E sqrt(V)), no dense all-pairs matrix.

    Candidate lists are ordered by IoU; cardinality is maximized, while the sum
    of IoUs is not globally optimized. Each footprint can be matched only once.
    """
    left = [-1] * len(adjacency)
    right = [-1] * reference_count
    infinity = len(adjacency) + reference_count + 1
    while True:
        distances = [0 if match < 0 else infinity for match in left]
        queue = deque(index for index, match in enumerate(left) if match < 0)
        shortest = infinity
        while queue:
            prediction = queue.popleft()
            if distances[prediction] + 1 > shortest:
                continue
            for reference, _ in adjacency[prediction]:
                partner = right[reference]
                if partner < 0:
                    shortest = distances[prediction] + 1
                elif distances[partner] == infinity:
                    distances[partner] = distances[prediction] + 1
                    queue.append(partner)
        if shortest == infinity:
            break
        offsets = [0] * len(adjacency)
        for root, match in enumerate(left):
            if match >= 0:
                continue
            stack, edges = [root], []
            while stack:
                prediction = stack[-1]
                descended = False
                while offsets[prediction] < len(adjacency[prediction]):
                    reference, _ = adjacency[prediction][offsets[prediction]]
                    offsets[prediction] += 1
                    partner = right[reference]
                    if partner < 0 and distances[prediction] + 1 == shortest:
                        for p, r in zip(stack, edges + [reference]):
                            left[p], right[r] = r, p
                        stack.clear()
                        descended = True
                        break
                    if partner >= 0 and distances[partner] == distances[prediction] + 1:
                        stack.append(partner)
                        edges.append(reference)
                        descended = True
                        break
                if not descended:
                    distances[prediction] = infinity
                    stack.pop()
                    if edges:
                        edges.pop()
    return left


def evaluate_buildings(predictions, references, *, iou_threshold=0.5):
    """Return JSON-serializable metrics. Undefined metrics are null, not perfect.

    At most 10,000 features/500,000 vertices per set and 250,000 intersecting
    candidate pairs are accepted. Split large/dense regions before evaluation.
    """
    if isinstance(iou_threshold, bool) or not isinstance(iou_threshold, (int, float)) or not math.isfinite(iou_threshold) or not 0 < iou_threshold <= 1:
        raise ValueError("IoU threshold must be greater than zero and at most one.")
    predicted, prediction_ids = _geometries(predictions, "Predictions")
    expected, reference_ids = _geometries(references, "References")
    projection = None
    if predicted or expected:
        transformer, projection = _metric_projection(predicted + expected)
        predicted = [transform(transformer.transform, geometry) for geometry in predicted]
        expected = [transform(transformer.transform, geometry) for geometry in expected]
    adjacency = [[] for _ in predicted]
    candidate_count = 0
    if predicted and expected:
        tree = STRtree(expected)
        for index, geometry in enumerate(predicted):
            candidates = tree.query(geometry, predicate="intersects")
            candidate_count += len(candidates)
            if candidate_count > MAX_CANDIDATES:
                raise ValueError("Too many overlapping candidates; split the evaluation into smaller geographic areas.")
            for reference_index in candidates:
                reference_index = int(reference_index)
                reference = expected[reference_index]
                intersection = geometry.intersection(reference).area
                union = geometry.area + reference.area - intersection
                iou = intersection / union if union > 0 else 0.0
                if iou + 1e-12 >= iou_threshold:
                    adjacency[index].append((reference_index, iou))
            adjacency[index].sort(key=lambda item: (-item[1], item[0]))
    pairs = _maximum_matching(adjacency, len(expected))
    matches = []
    for prediction_index, reference_index in enumerate(pairs):
        if reference_index < 0:
            continue
        iou = next(value for reference, value in adjacency[prediction_index] if reference == reference_index)
        matches.append({"prediction_index": prediction_index, "prediction_id": prediction_ids[prediction_index],
            "reference_index": reference_index, "reference_id": reference_ids[reference_index], "iou": iou,
            "boundary_hausdorff_m": predicted[prediction_index].boundary.hausdorff_distance(expected[reference_index].boundary)})
    tp = len(matches)
    fp, fn = len(predicted) - tp, len(expected) - tp
    denominator = 2 * tp + fp + fn
    return {
        "schema_version": 1, "iou_threshold": float(iou_threshold),
        "prediction_count": len(predicted), "reference_count": len(expected),
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": tp / len(predicted) if predicted else None,
        "recall": tp / len(expected) if expected else None,
        "f1": 2 * tp / denominator if denominator else None,
        "mean_matched_iou": sum(item["iou"] for item in matches) / tp if tp else None,
        "mean_matched_boundary_hausdorff_m": sum(item["boundary_hausdorff_m"] for item in matches) / tp if tp else None,
        "matching": "maximum-cardinality one-to-one IoU-threshold matching; IoU-prioritized adjacency, not maximum-sum IoU",
        "candidate_pairs": candidate_count, "metric_crs": projection,
        "matches": matches,
        "unmatched_prediction_indices": [index for index, reference in enumerate(pairs) if reference < 0],
        "unmatched_reference_indices": sorted(set(range(len(expected))) - {item["reference_index"] for item in matches}),
        "limitations": ["Agreement with supplied reference footprints; no cadastral or ground-position certification.",
            "Boundary error is discrete Hausdorff distance between matched polygon boundaries in metres, not survey checkpoint RMSE.",
            "No average precision, confidence calibration, or training/test independence is inferred."],
    }
