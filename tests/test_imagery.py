"""Synthetic raster tests: real CRS/affine logic, no external network or AI."""
import io

import numpy as np
from PIL import Image
import pytest
from rasterio import Affine
from rasterio.io import MemoryFile

from imagery import SurveyImagery, _resampled_transform


def test_resampling_preserves_rotated_raster_extent():
    transform = Affine.translation(500000, 2670000) * Affine.rotation(12) * Affine.scale(0.1, -0.1)
    resized = Affine(*_resampled_transform(list(transform)[:6], 3200, 2400, 1600, 1200))
    assert resized * (1600, 1200) == pytest.approx(transform * (3200, 2400))
    assert resized * (0, 0) == pytest.approx(transform * (0, 0))


def test_rotated_geotiff_preserves_provenance_and_has_web_overlay(tmp_path):
    transform = Affine.translation(500000, 2670000) * Affine.rotation(12) * Affine.scale(0.2, -0.2)
    with MemoryFile() as memory:
        with memory.open(driver="GTiff", width=80, height=60, count=3,
                         dtype="uint8", crs="EPSG:32645", transform=transform) as dataset:
            dataset.write(np.full((3, 60, 80), 150, dtype=np.uint8))
        contents = memory.read()
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(contents, "rotated-survey.tif")
    assert source["original_crs"] == "EPSG:32645"
    assert source["original_transform"][:6] == pytest.approx(list(transform)[:6])
    assert source["crs"] == "EPSG:32645"
    assert source["transform"] == pytest.approx(list(transform)[:6])
    assert source["resampled"] is False
    assert source["preview_crs"] == "EPSG:3857"
    assert source["preview_transform"][1] == pytest.approx(0)
    assert source["preview_transform"][3] == pytest.approx(0)
    assert source["bounds"][0] < source["bounds"][2]
    assert source["bounds"][1] < source["bounds"][3]
    with Image.open(source["preview_path"]) as image:
        assert image.width == source["preview_width"]
        assert image.height == source["preview_height"]
    assert service.readiness()["available"] is False


def test_plain_image_missing_or_reversed_extent_is_rejected(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    with pytest.raises(ValueError):
        service.inspect_upload(buffer.getvalue(), "unregistered.png")
    with pytest.raises(ValueError):
        service.inspect_upload(buffer.getvalue(), "unregistered.png", [88, 24, 87, 25])


def test_explicit_extent_preview_has_correct_web_mercator_transform(tmp_path):
    from pyproj import Transformer

    buffer = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(buffer, format="PNG")
    bounds = [87.7, 24.08, 87.702, 24.081]
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(buffer.getvalue(), "registered.png", bounds)
    project = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    west, south = project.transform(bounds[0], bounds[1])
    east, north = project.transform(bounds[2], bounds[3])
    transform = Affine(*source["preview_transform"])
    assert source["preview_crs"] == "EPSG:3857"
    assert transform * (0, 0) == pytest.approx((west, north))
    assert transform * (source["preview_width"], source["preview_height"]) == pytest.approx((east, south))


def test_known_stub_mask_follows_source_affine_and_retains_confidence(tmp_path):
    """The stub verifies coordinate wiring; it makes no model-quality claim."""
    from types import SimpleNamespace

    class Values:
        def __init__(self, values):
            self.values = values

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return self.values

    class KnownMaskModel:
        def predict(self, **kwargs):
            return [SimpleNamespace(
                masks=SimpleNamespace(xy=[np.array([[10, 10], [30, 10], [30, 30], [10, 30]], dtype=np.float32)]),
                boxes=SimpleNamespace(conf=Values([0.81]), cls=Values([0])),
            )]

    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(buffer.getvalue(), "known.png", [87.7, 24.08, 87.701, 24.081])
    service._model = KnownMaskModel()
    result = service.scan_source(source, confidence=0.35)
    assert len(result["geojson"]["features"]) == 1
    feature = result["geojson"]["features"][0]
    assert feature["properties"]["confidence"] == pytest.approx(0.81)
    assert feature["properties"]["feature_type"] == "building"
    assert feature["properties"]["source_id"] == source["id"]
    longitudes, latitudes = zip(*feature["geometry"]["coordinates"][0])
    assert min(longitudes) == pytest.approx(87.7001)
    assert max(longitudes) == pytest.approx(87.7003)
    assert min(latitudes) == pytest.approx(24.0807)
    assert max(latitudes) == pytest.approx(24.0809)


def test_overlapping_tiles_deduplicate_same_known_building(tmp_path):
    from types import SimpleNamespace

    class Values:
        def __init__(self, values):
            self.values = values

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return self.values

    class DuplicateMaskModel:
        calls = 0

        def predict(self, **kwargs):
            # width 1000 uses windows beginning at x=0 and x=232.
            x = 300 if self.calls == 0 else 68
            self.calls += 1
            return [SimpleNamespace(
                masks=SimpleNamespace(xy=[np.array([[x, 10], [x+40, 10], [x+40, 50], [x, 50]], dtype=np.float32)]),
                boxes=SimpleNamespace(conf=Values([0.85 if self.calls == 1 else 0.75]), cls=Values([0])),
            )]

    buffer = io.BytesIO()
    Image.new("RGB", (1000, 100), "white").save(buffer, format="PNG")
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(buffer.getvalue(), "two-tiles.png", [87.7, 24.08, 87.71, 24.081])
    service._model = DuplicateMaskModel()
    result = service.scan_source(source)
    assert result["inference"]["tiles_processed"] == 2
    assert result["inference"]["duplicate_masks_removed"] == 1
    assert len(result["geojson"]["features"]) == 1
    assert result["geojson"]["features"][0]["properties"]["confidence"] == pytest.approx(0.85)


def _mask_service(tmp_path, masks, *, resolution=1, width=140, height=100):
    """Known segmentation outputs exercise filtering, never model accuracy."""
    from types import SimpleNamespace

    class Values:
        def __init__(self, values):
            self.values = values

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return self.values

    class KnownMasks:
        def predict(self, **kwargs):
            return [SimpleNamespace(
                masks=SimpleNamespace(xy=[np.array(points, dtype=np.float32) for points in masks]),
                boxes=SimpleNamespace(conf=Values([0.85] * len(masks)), cls=Values([0] * len(masks))),
            )]

    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service._save_source(np.full((height, width, 3), 150, dtype=np.uint8),
        name="Synthetic georeferenced image", kind="drone", crs="EPSG:3857",
        transform=[resolution, 0, 0, 0, -resolution, height * resolution], attribution="Test fixture")
    service._model = KnownMasks()
    return service, source


def _rectangle(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def test_multiple_nearby_houses_remain_separate_with_overlapping_masks(tmp_path):
    # The first two predictions overlap slightly, as neighboring roof masks can.
    masks = [_rectangle(10, 10, 30, 30), _rectangle(28, 10, 48, 30), _rectangle(80, 40, 110, 70)]
    service, source = _mask_service(tmp_path, masks)
    result = service.scan_source(source)
    features = result["geojson"]["features"]
    assert len(features) == 3
    assert result["inference"]["duplicate_masks_removed"] == 0
    assert all(feature["properties"]["area_sqm"] > 390 for feature in features)
    assert all(feature["properties"]["needs_review"] for feature in features)
    assert all(feature["properties"]["quality_flags"] == [] for feature in features)
    assert sorted(feature["properties"]["mask_area_px"] for feature in features) == [400, 400, 900]


def test_minimum_area_uses_ground_square_metres_and_can_be_disabled(tmp_path):
    service, source = _mask_service(tmp_path, [_rectangle(10, 10, 30, 30), _rectangle(50, 10, 80, 40)], resolution=0.1)
    # 400 px at 0.1 m/pixel is just under 4 geodesic m²; 900 px is about 9 m².
    filtered = service.scan_source(source)
    assert len(filtered["geojson"]["features"]) == 1
    assert filtered["inference"]["small_area_masks_removed"] == 1
    assert filtered["inference"]["minimum_area_sqm"] == 4
    assert filtered["geojson"]["features"][0]["properties"]["area_sqm"] == pytest.approx(8.94, rel=0.01)
    unfiltered = service.scan_source(source, min_area_sqm=0)
    assert len(unfiltered["geojson"]["features"]) == 2
    assert unfiltered["inference"]["small_area_masks_removed"] == 0


def test_source_boundary_masks_are_flagged_without_being_discarded(tmp_path):
    service, source = _mask_service(tmp_path, [_rectangle(-5, 10, 30, 30)])
    feature = service.scan_source(source)["geojson"]["features"][0]
    assert feature["properties"]["touches_source_edge"] is True
    assert feature["properties"]["touches_tile_edge"] is False
    assert feature["properties"]["quality_flags"] == ["source_image_edge"]
    assert min(position[0] for position in feature["geometry"]["coordinates"][0]) == pytest.approx(source["bounds"][0])


def test_complete_mask_is_preferred_over_duplicate_cut_by_inference_window():
    from shapely.geometry import Polygon

    partial = {"polygon": Polygon(_rectangle(700, 10, 768, 80)), "confidence": 0.95, "truncated": True}
    complete = {"polygon": Polygon(_rectangle(700, 10, 800, 80)), "confidence": 0.80, "truncated": False}
    next_house = {"polygon": Polygon(_rectangle(805, 10, 850, 80)), "confidence": 0.85, "truncated": False}
    kept = SurveyImagery._deduplicate([partial, complete, next_house])
    assert len(kept) == 2
    assert any(item is complete for item in kept)
    assert any(item is next_house for item in kept)
    assert not any(item is partial for item in kept)


@pytest.mark.parametrize("minimum", [-1, 10001, float("nan"), float("inf"), True, None, "invalid"])
def test_invalid_minimum_area_is_rejected_before_loading_or_download(tmp_path, minimum):
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    with pytest.raises(ValueError, match="Minimum building area"):
        service.scan_source({}, min_area_sqm=minimum)
    with pytest.raises(ValueError, match="Minimum building area"):
        service.scan_map([], min_area_sqm=minimum)
