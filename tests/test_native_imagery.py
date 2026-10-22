"""Native-resolution georeference/data-integrity tests; no accuracy claims."""
import io
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
from pyproj import Transformer
import pytest
from rasterio import Affine
from rasterio.enums import ColorInterp
from rasterio.io import MemoryFile

from imagery import SurveyImagery


def geotiff(values, *, transform=None, colors=None, nodata=None, crs="EPSG:32645"):
    if values.ndim == 2:
        values = values[None]
    transform = transform or Affine(0.1, 0, 500000, 0, -0.1, 2670000)
    with MemoryFile() as memory:
        with memory.open(driver="GTiff", width=values.shape[2], height=values.shape[1], count=values.shape[0],
                dtype=values.dtype, crs=crs, transform=transform, compress="deflate", nodata=nodata) as dataset:
            dataset.write(values)
            if colors is not None:
                dataset.colorinterp = colors
        return memory.read()


class Values:
    def __init__(self, values):
        self.values = values

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.values


def result_for(polygons):
    return [SimpleNamespace(masks=SimpleNamespace(xy=[np.array(polygon, dtype=np.float32) for polygon in polygons]),
        boxes=SimpleNamespace(conf=Values([0.9] * len(polygons)), cls=Values([0] * len(polygons))))]


class Masks:
    def __init__(self, polygons):
        self.polygons = polygons
        self.tiles = []

    def predict(self, **kwargs):
        self.tiles.append(kwargs["source"].copy())
        return result_for(self.polygons)


def rectangle(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def test_rotated_native_mask_and_separate_preview_have_correct_placement(tmp_path):
    transform = Affine.translation(500000, 2670000) * Affine.rotation(23) * Affine.scale(0.2, -0.2)
    contents = geotiff(np.full((3, 80, 100), 160, dtype="uint8"), transform=transform)
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(contents, "rotated.tif")
    service._model = Masks([rectangle(20, 20, 50, 50)])
    feature = service.scan_source(source, min_area_sqm=0)["geojson"]["features"][0]
    project = Transformer.from_crs("EPSG:32645", "EPSG:4326", always_xy=True)
    expected = [project.transform(*(transform * (x, y))) for x, y in rectangle(20, 20, 50, 50)]
    coordinates = feature["geometry"]["coordinates"][0][:-1]
    for point in expected:
        assert any(np.allclose(point, coordinate, rtol=0, atol=1e-8) for coordinate in coordinates)
    assert source["width"] == 100 and source["height"] == 80
    assert source["transform"] == pytest.approx(list(transform)[:6])
    assert Path(source["raster_path"]).read_bytes() == contents
    assert source["preview_crs"] == "EPSG:3857" and source["preview_transform"][1] == 0
    with Image.open(source["preview_path"]) as preview:
        alpha = np.asarray(preview)[:, :, 3]
        assert (alpha == 0).any() and (alpha == 255).any()


@pytest.mark.parametrize("kind", ["tiff", "png"])
def test_detail_beyond_4096_is_seen_at_original_pixel_resolution(tmp_path, kind):
    values = np.full((3, 80, 5200), 90, dtype="uint8")
    # A narrow, high-contrast object far beyond the old resize threshold.
    values[:, 20:30, 4700:4710] = np.array([220, 35, 5])[:, None, None]
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    if kind == "tiff":
        source = service.inspect_upload(geotiff(values), "native.tif")
    else:
        buffer = io.BytesIO()
        Image.fromarray(values.transpose(1, 2, 0)).save(buffer, "PNG")
        source = service.inspect_upload(buffer.getvalue(), "native.png", [88, 24, 88.0052, 24.00008])

    class PixelDetector:
        calls = []

        def predict(self, **kwargs):
            tile = kwargs["source"]
            self.calls.append(tile.shape)
            ys, xs = np.where(np.all(tile == [5, 35, 220], axis=2))
            polygons = [rectangle(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)] if xs.size else []
            return result_for(polygons)

    model = PixelDetector()
    service._model = model
    result = service.scan_source(source, min_area_sqm=0)
    assert source["width"] == 5200 and source["height"] == 80 and source["resampled"] is False
    assert result["inference"]["native_resolution"] is True
    assert result["inference"]["tiles_processed"] == 8
    assert len(result["geojson"]["features"]) == 1
    assert result["geojson"]["features"][0]["properties"]["mask_area_px"] == 100
    assert max(shape[1] for shape in model.calls) <= 768


def test_declared_rgb_order_is_used_for_preview_and_model(tmp_path):
    values = np.empty((3, 80, 100), dtype="uint8")
    values[0], values[1], values[2] = 30, 210, 80
    colors = (ColorInterp.blue, ColorInterp.red, ColorInterp.green)
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(geotiff(values, colors=colors), "b-r-g.tif")
    service._model = Masks([])
    service.scan_source(source)
    assert source["rgb_bands"] == [2, 3, 1]
    assert service._model.tiles[0][30, 30].tolist() == [30, 80, 210]  # Ultralytics BGR
    with Image.open(source["preview_path"]) as image:
        center = np.asarray(image)[image.height // 2, image.width // 2]
        assert center.tolist() == [210, 80, 30, 255]


def test_ambiguous_multispectral_bands_are_rejected(tmp_path):
    values = np.full((4, 80, 100), 100, dtype="uint8")
    colors = (ColorInterp.undefined,) * 4
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    with pytest.raises(ValueError, match="declare RGB"):
        service.inspect_upload(geotiff(values, colors=colors), "unlabelled-four-band.tif")


def test_single_band_grayscale_and_source_wide_uint16_stretch(tmp_path):
    values = np.tile(np.linspace(0, 65535, 100, dtype="uint16"), (80, 1))
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(geotiff(values), "gray16.tif")
    service._model = Masks([])
    service.scan_source(source)
    tile = service._model.tiles[0]
    assert tile.shape == (80, 100, 3)
    assert np.array_equal(tile[:, :, 0], tile[:, :, 1]) and np.array_equal(tile[:, :, 1], tile[:, :, 2])
    assert tile[20, 0, 0] == 0 and tile[20, -1, 0] == 255
    assert 120 <= tile[20, 50, 0] <= 135
    assert len(source["radiometric_ranges"]) == 1


@pytest.mark.parametrize("invalid_kind", ["nodata", "alpha", "nan"])
def test_invalid_raster_pixels_cannot_become_building_proposals(tmp_path, invalid_kind):
    values = np.full((3, 80, 100), 150, dtype="float32" if invalid_kind == "nan" else "uint8")
    values[:, :, :40] = np.nan if invalid_kind == "nan" else 0
    if invalid_kind == "alpha":
        values = np.concatenate([values, np.full((1, 80, 100), 255, dtype="uint8")])
        values[3, :, :40] = 0
        contents = geotiff(values, colors=(ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha))
    else:
        contents = geotiff(values, nodata=np.nan if invalid_kind == "nan" else 0,
            colors=(ColorInterp.red, ColorInterp.green, ColorInterp.blue))
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(contents, "masked.tif")
    service._model = Masks([rectangle(10, 10, 25, 25), rectangle(50, 10, 70, 30), rectangle(35, 40, 60, 60)])
    result = service.scan_source(source, min_area_sqm=0)
    assert len(result["geojson"]["features"]) == 1
    assert result["geojson"]["features"][0]["properties"]["mask_area_px"] == 400
    assert result["inference"]["nodata_masks_removed"] == 2
    assert np.all(service._model.tiles[0][:, :40] == 0)


def test_transparent_png_pixels_are_excluded_and_legacy_png_still_scans(tmp_path):
    rgba = np.full((80, 100, 4), 255, dtype="uint8")
    rgba[:, :40, 3] = 0
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, "PNG")
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(buffer.getvalue(), "alpha.png", [88, 24, 88.001, 24.0008])
    service._model = Masks([rectangle(10, 10, 25, 25), rectangle(50, 10, 70, 30)])
    # Old saved sources lacked the format/resampling metadata.
    source.pop("raster_format")
    source.pop("resampled")
    result = service.scan_source(source, min_area_sqm=0)
    assert len(result["geojson"]["features"]) == 1
    assert result["inference"]["nodata_masks_removed"] == 1


def test_source_aoi_limits_work_and_masks_are_clipped_at_its_boundary(tmp_path, monkeypatch):
    import imagery

    # 5200 pixels requires eight native windows. A smaller AOI needs only one.
    transform = Affine(0.000001, 0, 88, 0, -0.000001, 24.00008)
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(geotiff(np.full((3, 80, 5200), 150, dtype="uint8"), transform=transform, crs="EPSG:4326"), "long.tif")
    service._model = Masks([rectangle(0, 10, 50, 30)])
    monkeypatch.setattr(imagery, "MAX_TILES", 2)
    with pytest.raises(ValueError, match="will not be downsampled"):
        service.scan_source(source)
    bounds = [88.0046, 24, 88.0051, 24.00008]
    result = service.scan_source(source, min_area_sqm=0, bounds=bounds)
    assert result["inference"]["tiles_processed"] == 1
    assert result["inference"]["scan_bounds"] == bounds
    assert 4599 <= result["inference"]["scan_window_px"][0] <= 4600
    feature = result["geojson"]["features"][0]
    assert "scan_aoi_edge" in feature["properties"]["quality_flags"]
    assert min(point[0] for point in feature["geometry"]["coordinates"][0]) >= bounds[0] - 1e-9
    assert max(point[0] for point in feature["geometry"]["coordinates"][0]) <= bounds[2] + 1e-9
    with pytest.raises(ValueError, match="does not overlap"):
        service.scan_source(source, bounds=[89, 25, 89.001, 25.001])


def test_native_raster_metadata_mismatch_is_rejected(tmp_path):
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    source = service.inspect_upload(geotiff(np.full((3, 80, 100), 150, dtype="uint8")), "native.tif")
    source["transform"][2] += 10
    service._model = Masks([])
    with pytest.raises(ValueError, match="transform does not match"):
        service.scan_source(source)
