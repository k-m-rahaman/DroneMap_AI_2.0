"""Synthetic geometry checks; none of these constitutes a field benchmark."""
import copy
import json
import random
from pathlib import Path
import subprocess
import sys

import pytest

import evaluation
from evaluation import _maximum_matching, evaluate_buildings


def polygon(x=88.36, y=22.57, width=0.0001, height=0.0001, identifier=None):
    return {"type": "Feature", "id": identifier, "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[x, y], [x + width, y],
            [x + width, y + height], [x, y + height], [x, y]]]}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def test_identical_polygon_has_exact_match_and_zero_boundary_error():
    result = evaluate_buildings(collection(polygon(identifier="p")), collection(polygon(identifier="r")))
    assert result["true_positives"] == 1
    assert result["false_positives"] == result["false_negatives"] == 0
    assert result["precision"] == result["recall"] == result["f1"] == 1
    assert result["mean_matched_iou"] == pytest.approx(1)
    assert result["mean_matched_boundary_hausdorff_m"] == 0
    assert result["matches"][0]["reference_id"] == "r"
    json.dumps(result, allow_nan=False)


def test_duplicates_count_as_false_positives_and_missing_as_false_negatives():
    result = evaluate_buildings(collection(polygon(), polygon()), collection(polygon(), polygon(x=88.362)))
    assert (result["true_positives"], result["false_positives"], result["false_negatives"]) == (1, 1, 1)
    assert result["precision"] == result["recall"] == result["f1"] == 0.5
    assert len(result["unmatched_prediction_indices"]) == 1
    assert result["unmatched_reference_indices"] == [1]


def test_iou_threshold_and_metric_boundary_distance():
    prediction, reference = collection(polygon(x=88.36002)), collection(polygon())
    result = evaluate_buildings(prediction, reference, iou_threshold=0.5)
    assert result["true_positives"] == 1
    assert 0.65 < result["mean_matched_iou"] < 0.68
    assert 1.5 < result["mean_matched_boundary_hausdorff_m"] < 2.5
    stricter = evaluate_buildings(prediction, reference, iou_threshold=0.75)
    assert stricter["true_positives"] == 0
    assert stricter["mean_matched_iou"] is None


@pytest.mark.parametrize("predictions,references,expected", [
    (collection(), collection(), (None, None, None)),
    (collection(), collection(polygon()), (None, 0, 0)),
    (collection(polygon()), collection(), (0, None, 0)),
])
def test_empty_sets_do_not_invent_perfect_scores(predictions, references, expected):
    result = evaluate_buildings(predictions, references)
    assert tuple(result[key] for key in ("precision", "recall", "f1")) == expected
    assert result["mean_matched_iou"] is None
    assert result["mean_matched_boundary_hausdorff_m"] is None


@pytest.mark.parametrize("threshold", [0, -1, 1.01, True, float("nan"), "0.5"])
def test_invalid_thresholds_are_rejected(threshold):
    with pytest.raises(ValueError, match="threshold"):
        evaluate_buildings(collection(), collection(), iou_threshold=threshold)


def test_invalid_and_unclosed_geometries_are_rejected_without_repair():
    invalid = polygon()
    invalid["geometry"]["coordinates"] = [[[88.36, 22.57], [88.361, 22.571], [88.36, 22.571], [88.361, 22.57], [88.36, 22.57]]]
    with pytest.raises(ValueError, match="invalid geometry"):
        evaluate_buildings(collection(invalid), collection())
    unclosed = polygon()
    unclosed["geometry"]["coordinates"][0].pop()
    with pytest.raises(ValueError, match="closed rings"):
        evaluate_buildings(collection(unclosed), collection())


@pytest.mark.parametrize("bad_coordinate", [[181, 22], [88, float("inf")], [True, 22], [88, 22, 3]])
def test_invalid_coordinates_are_rejected(bad_coordinate):
    invalid = polygon()
    invalid["geometry"]["coordinates"][0][1] = bad_coordinate
    with pytest.raises(ValueError, match="finite WGS84"):
        evaluate_buildings(collection(invalid), collection())


def test_multipolygon_and_hole_are_supported():
    multi = polygon()
    multi["geometry"] = {"type": "MultiPolygon", "coordinates": [polygon()["geometry"]["coordinates"], polygon(x=88.362)["geometry"]["coordinates"]]}
    result = evaluate_buildings(collection(multi), collection(copy.deepcopy(multi)))
    assert result["true_positives"] == 1
    hole = polygon(width=.001, height=.001)
    hole["geometry"]["coordinates"].append(polygon(x=88.3602, y=22.5702)["geometry"]["coordinates"][0])
    assert evaluate_buildings(collection(hole), collection(hole))["mean_matched_iou"] == pytest.approx(1)


def test_projection_crs_and_resource_limits_are_explicit(monkeypatch):
    with pytest.raises(ValueError, match="100 km"):
        evaluate_buildings(collection(polygon(), polygon(x=90)), collection())
    with pytest.raises(ValueError, match="CRS"):
        evaluate_buildings({**collection(), "crs": {}}, collection())
    monkeypatch.setattr(evaluation, "MAX_FEATURES", 1)
    with pytest.raises(ValueError, match="at most"):
        evaluate_buildings(collection(polygon(), polygon()), collection())
    monkeypatch.setattr(evaluation, "MAX_CANDIDATES", 0)
    with pytest.raises(ValueError, match="candidates"):
        evaluate_buildings(collection(polygon()), collection(polygon()))


def test_matching_finds_augmenting_path_instead_of_greedy_under_count():
    matching = _maximum_matching([[(0, .9), (1, .8)], [(0, .8)]], 2)
    assert matching == [1, 0]
    assert _maximum_matching([[], []], 3) == [-1, -1]


def test_matching_long_chain_is_iterative_and_one_to_one():
    size = 1500
    adjacency = [[(index, .9), (index + 1, .8)] for index in range(size - 1)] + [[(0, .8)]]
    matches = _maximum_matching(adjacency, size)
    assert len(set(matches)) == size
    assert -1 not in matches


def test_matching_cardinality_agrees_with_exhaustive_small_graphs():
    generator = random.Random(1729)
    for _ in range(100):
        adjacency = [[(reference, .8) for reference in range(5) if generator.random() < .4] for _ in range(5)]
        def exhaustive(prediction, used):
            if prediction == len(adjacency):
                return 0
            return max([exhaustive(prediction + 1, used)] + [1 + exhaustive(prediction + 1, used | {reference})
                for reference, _ in adjacency[prediction] if reference not in used])
        matches = _maximum_matching(adjacency, 5)
        assert sum(reference >= 0 for reference in matches) == exhaustive(0, set())
        assigned = [reference for reference in matches if reference >= 0]
        assert len(assigned) == len(set(assigned))


def test_cli_writes_json_and_refuses_to_overwrite_input(tmp_path):
    path = tmp_path / "buildings.geojson"
    path.write_text(json.dumps(collection(polygon())), encoding="utf-8")
    output = tmp_path / "metrics.json"
    cli = Path(__file__).resolve().parents[1] / "tools" / "evaluate_buildings.py"
    result = subprocess.run([sys.executable, str(cli), str(path), str(path), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["true_positives"] == 1
    rejected = subprocess.run([sys.executable, str(cli), str(path), str(path), "--output", str(path)], capture_output=True, text=True)
    assert rejected.returncode == 2
    assert "overwrite" in rejected.stderr
    assert json.loads(path.read_text())["type"] == "FeatureCollection"
