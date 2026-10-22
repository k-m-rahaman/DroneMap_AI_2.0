"""Map cache regressions with deterministic raster fixtures, never live AI."""

import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image
import pytest

from imagery import (
    ESRI_SERVICE,
    MAP_TILE_BASE_RESOLUTION,
    MAP_TILE_ORIGIN_X,
    MAP_TILE_ORIGIN_Y,
    MapCoverageUnavailable,
    SurveyImagery,
)


def _tile_bytes(column, row):
    """Pixel colors expose stitching/cropping offsets across real tile edges."""
    pixels = np.zeros((256, 256, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.arange(256, dtype=np.uint8)[None, :]
    pixels[:, :, 1] = np.arange(256, dtype=np.uint8)[:, None]
    pixels[:, :, 2] = (column + row) % 256
    output = io.BytesIO()
    Image.fromarray(pixels).save(output, format="PNG")
    return output.getvalue()


def _download_fixture(calls, *, fail_tiles=False):
    def download(url, **kwargs):
        calls.append(url)
        if "/export?" in url:
            return json.dumps({"href": "", "width": 0, "height": 0, "scale": 0}).encode()
        assert url.startswith(ESRI_SERVICE + "/tile/")
        if fail_tiles:
            raise RuntimeError("Fixture provider tile failure")
        assert parse_qs(urlparse(url).query) == {"blankTile": ["false"]}
        level, row, column = map(int, urlparse(url).path.split("/")[-3:])
        assert 0 <= level <= 19
        return _tile_bytes(column, row)
    return download


def test_cached_tiles_crop_on_pixel_edges_and_preserve_grid(tmp_path, monkeypatch):
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    resolution = MAP_TILE_BASE_RESOLUTION / 2 ** 19
    left, top = 300_000 * 256 + 100.25, 200_000 * 256 + 70.5
    bounds = [MAP_TILE_ORIGIN_X + left * resolution,
        MAP_TILE_ORIGIN_Y - (top + 300.5) * resolution,
        MAP_TILE_ORIGIN_X + (left + 400.1) * resolution,
        MAP_TILE_ORIGIN_Y - top * resolution]
    plan = service._tile_plan(bounds)
    calls = []
    monkeypatch.setattr(service, "_download", _download_fixture(calls))
    raster, affine, metadata = service._fetch_map_tiles(bounds)
    assert plan["level"] == metadata["tile_level"] == 19
    assert len(calls) == plan["tile_count"] == 4
    assert raster.shape == (plan["height"], plan["width"], 3)
    assert int(raster[0, 0, 0]) == plan["left"] % 256
    assert int(raster[0, 0, 1]) == plan["top"] % 256
    assert int(raster[-1, -1, 0]) == (plan["right"] - 1) % 256
    assert int(raster[-1, -1, 1]) == (plan["bottom"] - 1) % 256
    assert affine[0] == pytest.approx(resolution)
    assert affine[4] == pytest.approx(-resolution)
    assert affine[2] <= bounds[0] < affine[2] + resolution
    assert affine[5] - resolution < bounds[3] <= affine[5]
    # Binary64 subtraction at global Mercator coordinates can differ by a few
    # nanometres. Allow 0.1 micrometre, far below one source pixel.
    assert affine[2] + plan["width"] * resolution >= bounds[2] - 1e-7
    assert affine[5] - plan["height"] * resolution <= bounds[1] + 1e-7
    assert metadata["imagery_method"] == "cached_tile_mosaic"
    assert metadata["resampled"] is False


def test_cache_level_respects_both_pixel_and_tile_limits():
    plan = SurveyImagery._tile_plan([9_700_000, 2_750_000, 9_704_400, 2_754_400])
    assert plan["level"] <= 19
    assert max(plan["width"], plan["height"]) <= 2048
    assert plan["tile_count"] <= 64


def test_empty_export_falls_back_to_real_tile_retrieval(tmp_path, monkeypatch):
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    calls = []
    monkeypatch.setattr(service, "_download", _download_fixture(calls))
    # This test isolates imagery ingestion; it does not pretend to run a model.
    monkeypatch.setattr(service, "_load_model", lambda: object())
    monkeypatch.setattr(service, "scan_source", lambda source, confidence, min_area_sqm: {
        "source": source, "geojson": {"type": "FeatureCollection", "features": []},
        "inference": {"fixture_only": True, "minimum_area_sqm": min_area_sqm}})
    requested = [87.714, 24.088, 87.716, 24.090]
    result = service.scan_map(requested, min_area_sqm=7)
    assert result["inference"]["minimum_area_sqm"] == 7
    source = result["source"]
    assert source["requested_bounds"] == requested
    assert source["imagery_method"] == "cached_tile_mosaic"
    assert "no raster" in source["export_fallback_reason"]
    assert source["tile_count"] == len(calls) - 1
    assert source["crs"] == source["preview_crs"] == "EPSG:3857"
    assert source["bounds"][0] <= requested[0]
    assert source["bounds"][1] <= requested[1]
    assert source["bounds"][2] >= requested[2]
    assert source["bounds"][3] >= requested[3]
    assert source["acquisition_date"] is None
    assert all(url.startswith(ESRI_SERVICE + "/") for url in calls)


def test_failed_tile_does_not_save_a_partial_mosaic(tmp_path, monkeypatch):
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    calls = []
    monkeypatch.setattr(service, "_download", _download_fixture(calls, fail_tiles=True))
    monkeypatch.setattr(service, "_load_model", lambda: object())
    with pytest.raises(RuntimeError, match="Fixture provider tile failure"):
        service.scan_map([87.714, 24.088, 87.716, 24.090])
    assert not list(service.sources_dir.glob("*.png"))


def test_missing_tile_falls_back_entire_mosaic_with_native_transform(tmp_path, monkeypatch):
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    resolution = MAP_TILE_BASE_RESOLUTION / 2 ** 19
    bounds = [9_764_000, 2_764_000, 9_764_400, 2_764_300]
    first_plan = service._tile_plan(bounds)
    missing_tile = (first_plan["min_column"], first_plan["min_row"])
    calls = []

    def download(url, **kwargs):
        level, row, column = map(int, urlparse(url).path.split("/")[-3:])
        calls.append(level)
        assert parse_qs(urlparse(url).query) == {"blankTile": ["false"]}
        if level == 19 and (column, row) == missing_tile:
            raise MapCoverageUnavailable("Fixture missing tile")
        return _tile_bytes(column, row)

    monkeypatch.setattr(service, "_download", download)
    raster, affine, metadata = service._fetch_map_tiles(bounds)
    plan = service._tile_plan(bounds, max_level=18)
    assert set(calls) == {19, 18}
    assert metadata["tile_level"] == metadata["actual_tile_level"] == 18
    assert metadata["requested_tile_level"] == 19
    assert metadata["coverage_levels_unavailable"] == [19]
    assert "lower-resolution" in metadata["coverage_fallback_reason"]
    assert metadata["resampled"] is False
    assert raster.shape == (plan["height"], plan["width"], 3)
    assert affine == pytest.approx([resolution * 2, 0, MAP_TILE_ORIGIN_X + plan["left"] * resolution * 2,
        0, -resolution * 2, MAP_TILE_ORIGIN_Y - plan["top"] * resolution * 2])
    assert int(raster[0, 0, 0]) == plan["left"] % 256
    assert int(raster[-1, -1, 1]) == (plan["bottom"] - 1) % 256


def test_all_four_missing_levels_never_save_or_run_inference(tmp_path, monkeypatch):
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    levels = []

    def download(url, **kwargs):
        if "/export?" in url:
            return b'{"href":""}'
        levels.append(int(urlparse(url).path.split("/")[-3]))
        raise MapCoverageUnavailable("Fixture missing coverage")

    monkeypatch.setattr(service, "_download", download)
    monkeypatch.setattr(service, "_load_model", lambda: object())
    monkeypatch.setattr(service, "scan_source", lambda *args: pytest.fail("Missing imagery must never be scanned"))
    with pytest.raises(RuntimeError, match="No complete imagery coverage"):
        service.scan_map([87.714, 24.088, 87.716, 24.090])
    assert set(levels) == {19, 18, 17, 16}
    assert not list(service.sources_dir.iterdir())


def test_network_error_overrides_missing_coverage_without_lower_level(tmp_path, monkeypatch):
    service = SurveyImagery(tmp_path, tmp_path / "missing.pt")
    bounds = [9_764_000, 2_764_000, 9_764_400, 2_764_300]
    plan = service._tile_plan(bounds)
    calls = []

    def download(url, **kwargs):
        level, row, column = map(int, urlparse(url).path.split("/")[-3:])
        calls.append(level)
        if (column, row) == (plan["min_column"], plan["min_row"]):
            raise MapCoverageUnavailable("Fixture missing tile")
        raise RuntimeError("Fixture connection interrupted")

    monkeypatch.setattr(service, "_download", download)
    with pytest.raises(RuntimeError, match="connection interrupted"):
        service._fetch_map_tiles(bounds)
    assert set(calls) == {19}
    assert not list(service.sources_dir.iterdir())


@pytest.mark.parametrize("code, tile_request, missing", [(404, True, True), (403, True, False), (500, True, False), (404, False, False)])
def test_only_explicit_tile_404_is_coverage_unavailable(monkeypatch, code, tile_request, missing):
    url = ESRI_SERVICE + ("/tile/19/1/1?blankTile=false" if tile_request else "/export?f=json")

    def unavailable(*args, **kwargs):
        raise HTTPError(url, code, "Fixture unavailable", {}, None)

    monkeypatch.setattr("imagery.urlopen", unavailable)
    with pytest.raises(MapCoverageUnavailable if missing else RuntimeError) as error:
        SurveyImagery._download(url, max_bytes=1024)
    assert isinstance(error.value, MapCoverageUnavailable) is missing


def test_provider_blank_tile_header_is_never_used_as_imagery(monkeypatch):
    class BlankResponse(io.BytesIO):
        headers = {"blank-tile": "true"}

    monkeypatch.setattr("imagery.urlopen", lambda *args, **kwargs: BlankResponse(_tile_bytes(1, 1)))
    with pytest.raises(MapCoverageUnavailable):
        SurveyImagery._download(ESRI_SERVICE + "/tile/19/1/1?blankTile=false", max_bytes=1024)


def test_connection_failure_is_not_a_missing_tile(monkeypatch):
    def unavailable(*args, **kwargs):
        raise URLError("Fixture network down")

    monkeypatch.setattr("imagery.urlopen", unavailable)
    with pytest.raises(RuntimeError) as error:
        SurveyImagery._download(ESRI_SERVICE + "/tile/19/1/1?blankTile=false", max_bytes=1024)
    assert not isinstance(error.value, MapCoverageUnavailable)
