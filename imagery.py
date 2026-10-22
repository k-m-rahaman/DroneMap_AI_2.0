"""Georeferenced image ingestion and real, optional building model inference.

Map imagery comes from Esri's documented MapServer export endpoint, never a
browser screenshot. Its returned extent, including any server aspect-ratio
adjustment, defines the raster affine transform. Imported GeoTIFFs are warped
to north-up Web Mercator for a truthful browser preview. An ordinary image
requires an explicit, user-supplied north-up WGS84 bounding box.
"""

from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import math
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, box, mapping
from shapely.ops import transform as transform_geometry

from geospatial import GEOD, validate_bounds, validate_geometry

ESRI_SERVICE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer"
ESRI_ATTRIBUTION = "Esri World Imagery; Esri, Maxar, Earthstar Geographics, and the GIS User Community"
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_SOURCE_PIXELS = 100_000_000
# Retained for callers importing this historic constant; native analysis is no
# longer resized to this edge. Only browser previews are reduced.
MAX_ANALYSIS_EDGE = 4096
MAX_PREVIEW_EDGE = 1600
MAP_IMAGE_EDGE = 2048
MAX_MAP_EDGE_METERS = 4000
TILE_SIZE = 768
TILE_OVERLAP = 128
MAX_TILES = 256
MAX_CANDIDATES = 2500
MAX_FEATURES = 1000
MAP_TILE_SIZE = 256
MAX_MAP_TILES = 64
MAX_MAP_TILE_LEVEL = 19
# Esri World Imagery's published Web Mercator cache grid (MapServer tileInfo).
MAP_TILE_ORIGIN_X = -20037508.342787
MAP_TILE_ORIGIN_Y = 20037508.342787
MAP_TILE_BASE_RESOLUTION = 156543.03392800014


def _resampled_transform(transform, old_width, old_height, new_width, new_height):
    """Scale all six affine terms as A * diag(old/new), preserving rotation."""
    a, b, c, d, e, f = map(float, transform[:6])
    sx, sy = old_width / new_width, old_height / new_height
    result = [a * sx, b * sy, c, d * sx, e * sy, f]
    # Keep Affine inputs usable with their normal ``affine * (x, y)`` API;
    # persisted metadata uses the JSON-compatible six-number form.
    if hasattr(transform, "a") and hasattr(transform, "determinant"):
        return transform.__class__(*result)
    return result


def _fit_dimensions(width, height, maximum):
    scale = min(1.0, maximum / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _transform_point(transform, x, y):
    a, b, c, d, e, f = transform
    return a * x + b * y + c, d * x + e * y + f


def _window_positions(length):
    if length <= TILE_SIZE:
        return [0]
    positions = list(range(0, length - TILE_SIZE + 1, TILE_SIZE - TILE_OVERLAP))
    if positions[-1] != length - TILE_SIZE:
        positions.append(length - TILE_SIZE)
    return positions


class SurveyImagery:
    def __init__(self, data_dir: Path, model_path: Path):
        self.data_dir = Path(data_dir).resolve()
        self.sources_dir = self.data_dir / "sources"
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = Path(model_path).resolve()
        self._model = None
        self._model_error = None
        self._loading = False
        self._model_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def readiness(self):
        if not self.model_path.is_file():
            return {"available": False, "state": "missing_weights", "message": "Building model weights are missing. Imagery import and manual editing remain available."}
        if self._model_error:
            return {"available": False, "state": "error", "message": self._model_error}
        if self._loading:
            return {"available": False, "state": "loading", "message": "Loading the building model on CPU."}
        if self._model is not None:
            return {"available": True, "state": "ready", "message": "Building footprint model ready on CPU. It does not detect land ownership parcels."}
        try:
            dependencies_present = importlib.util.find_spec("ultralytics") is not None and importlib.util.find_spec("torch") is not None
        except (ImportError, ValueError):
            dependencies_present = False
        if not dependencies_present:
            return {"available": False, "state": "dependencies_missing", "message": "AI packages are not installed. Install requirements-ai.txt to enable building extraction."}
        return {"available": True, "state": "not_loaded", "message": "Building model present; it will load on CPU when the first scan starts."}

    def _load_model(self):
        with self._model_lock:
            if self._model is not None:
                return self._model
            status = self.readiness()
            if not status["available"]:
                raise RuntimeError(status["message"])
            self._loading = True
            try:
                from ultralytics import YOLO
                model = YOLO(str(self.model_path), task="segment")
                names = model.names
                labels = list(names.values()) if isinstance(names, dict) else list(names)
                if len(labels) != 1 or "building" not in str(labels[0]).lower():
                    raise RuntimeError("The configured weights must be the supplied Building-only segmentation model.")
                self._model = model
            except Exception as exc:
                self._model_error = f"Building model could not load: {str(exc)[:250]}. No detections were generated."
                raise RuntimeError(self._model_error) from exc
            finally:
                self._loading = False
            return self._model

    @staticmethod
    def _confidence(value):
        try:
            result = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Confidence must be a number between 0.05 and 0.95.") from exc
        if not math.isfinite(result) or not 0.05 <= result <= 0.95:
            raise ValueError("Confidence must be between 0.05 and 0.95.")
        return result

    @staticmethod
    def _minimum_area(value):
        try:
            if isinstance(value, bool):
                raise ValueError()
            result = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Minimum building area must be a number between 0 and 10000 square metres.") from exc
        if not math.isfinite(result) or not 0 <= result <= 10000:
            raise ValueError("Minimum building area must be between 0 and 10000 square metres; use 0 to disable this filter.")
        return result

    @staticmethod
    def _download(url, *, max_bytes, timeout=30):
        request = Request(url, headers={"User-Agent": "DroneMap-Urban-Survey/3.0", "Accept": "application/json,image/png,image/jpeg"})
        try:
            with urlopen(request, timeout=timeout) as response:
                data = response.read(max_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Esri imagery service could not be reached. Check the connection and try a smaller area.") from exc
        if len(data) > max_bytes:
            raise RuntimeError("Imagery service response exceeded the download limit.")
        return data

    def _source_metadata(self, width, height, *, name, kind, crs, transform, attribution, extra=None, source_id=None):
        source_id = source_id or uuid.uuid4().hex
        raster_path = self.sources_dir / f"{source_id}_analysis.png"
        preview_path = self.sources_dir / f"{source_id}_preview.png"
        if width < 2 or height < 2:
            raise ValueError("Survey images need at least 2 pixels in each dimension.")
        if width * height > MAX_SOURCE_PIXELS:
            raise ValueError("Image exceeds the 100-million-pixel source limit.")
        transform = [float(v) for v in transform]
        if len(transform) != 6 or not all(math.isfinite(v) for v in transform) or abs(transform[0] * transform[4] - transform[1] * transform[3]) <= 1e-20:
            raise ValueError("Source affine transform is invalid or singular.")
        to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        # Densify the actual raster edges, including rotated rasters. A native
        # projected edge need not stay straight when transformed to WGS84.
        edge = np.linspace(0, 1, 33)
        pixels = [(width * t, 0) for t in edge] + [(width, height * t) for t in edge] + [(width * t, height) for t in edge] + [(0, height * t) for t in edge]
        corners = [to_wgs84.transform(*_transform_point(transform, x, y)) for x, y in pixels]
        bounds = [min(p[0] for p in corners), min(p[1] for p in corners), max(p[0] for p in corners), max(p[1] for p in corners)]
        validate_bounds(bounds, mercator=True)
        # Resolution is estimated geodesically at the image center. It describes
        # the exported/resampled raster, not the provider's native accuracy.
        center = to_wgs84.transform(*_transform_point(transform, width / 2, height / 2))
        next_x = to_wgs84.transform(*_transform_point(transform, width / 2 + 1, height / 2))
        next_y = to_wgs84.transform(*_transform_point(transform, width / 2, height / 2 + 1))
        x_resolution = abs(GEOD.inv(*center, *next_x)[2])
        y_resolution = abs(GEOD.inv(*center, *next_y)[2])
        source = {
            "id": source_id, "name": str(name)[:180], "kind": kind,
            "bounds": [float(v) for v in bounds], "crs": str(crs), "transform": transform,
            "width": int(width), "height": int(height),
            "resolution_m": round((x_resolution + y_resolution) / 2, 5),
            "resolution_xy_m": [round(x_resolution, 5), round(y_resolution, 5)],
            "resolution_note": "Analysis pixel spacing at image center; not a claim of survey positional accuracy.",
            "attribution": attribution, "created_at": datetime.now(timezone.utc).isoformat(),
            "acquisition_date": None, "preview_path": str(preview_path), "raster_path": str(raster_path),
            "analysis_window_count": len(_window_positions(width)) * len(_window_positions(height)),
            "maximum_windows_per_scan": MAX_TILES,
        }
        source["requires_scan_aoi"] = source["analysis_window_count"] > MAX_TILES
        if extra:
            source.update(extra)
        return source

    @staticmethod
    def _preview_grid(source):
        projected = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        xmin, ymin = projected.transform(*source["bounds"][:2])
        xmax, ymax = projected.transform(*source["bounds"][2:])
        ratio = (xmax - xmin) / (ymax - ymin)
        maximum = min(MAX_PREVIEW_EDGE, max(source["width"], source["height"]))
        width = maximum if ratio >= 1 else max(1, round(maximum * ratio))
        height = maximum if ratio <= 1 else max(1, round(maximum / ratio))
        return width, height, [(xmax - xmin) / width, 0, xmin, 0, -(ymax - ymin) / height, ymax]

    def _finish_source(self, source, preview, preview_transform):
        preview.save(source["preview_path"], "PNG")
        source["preview_width"], source["preview_height"] = preview.size
        source["preview_crs"] = "EPSG:3857"
        source["preview_transform"] = [float(v) for v in preview_transform]
        source["preview_bounds"] = source["bounds"][:]
        (self.sources_dir / f"{source['id']}.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
        return source

    def _save_source(self, rgb, *, name, kind, crs, transform, attribution, extra=None, source_id=None, valid_mask=None):
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
        height, width = rgb.shape[:2]
        source = self._source_metadata(width, height, name=name, kind=kind, crs=crs, transform=transform,
            attribution=attribution, extra=extra, source_id=source_id)
        source["raster_format"] = "png"
        if valid_mask is None:
            valid_mask = np.ones((height, width), dtype=bool)
        valid_mask = np.asarray(valid_mask, dtype=bool)
        if valid_mask.shape != (height, width):
            raise ValueError("Raster validity mask does not match its dimensions.")
        rgb[~valid_mask] = 0
        image = Image.fromarray(rgb, mode="RGB")
        if not valid_mask.all():
            image.putalpha(Image.fromarray(valid_mask.astype(np.uint8) * 255))
        image.save(source["raster_path"], "PNG")
        preview_transform = transform
        if CRS.from_user_input(crs).to_epsg() != 3857 or transform[1] != 0 or transform[3] != 0 or transform[0] <= 0 or transform[4] >= 0:
            # Leaflet places image overlays in Web Mercator. Warp the pixels as
            # well as the corners, otherwise geographic rasters stretch inside
            # that overlay even when its four corner coordinates are correct.
            try:
                from affine import Affine
                from rasterio.enums import Resampling
                from rasterio.warp import reproject
                preview_width, preview_height, preview_transform = self._preview_grid(source)
                warped = np.zeros((4, preview_height, preview_width), dtype=np.uint8)
                for index in range(3):
                    reproject(source=rgb[:, :, index], destination=warped[index], src_transform=Affine(*transform), src_crs=crs,
                        dst_transform=Affine(*preview_transform), dst_crs="EPSG:3857", resampling=Resampling.bilinear, num_threads=2)
                reproject(source=valid_mask.astype(np.uint8) * 255, destination=warped[3], src_transform=Affine(*transform), src_crs=crs,
                    dst_transform=Affine(*preview_transform), dst_crs="EPSG:3857", resampling=Resampling.nearest, num_threads=2)
                preview = Image.fromarray(warped.transpose(1, 2, 0), mode="RGBA")
            except ImportError as exc:
                raise RuntimeError("Rasterio is required for an accurately projected map preview. Install the base requirements.") from exc
        else:
            preview = image.copy()
            preview.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE), Image.Resampling.LANCZOS)
            preview_transform = _resampled_transform(transform, width, height, *preview.size)
        return self._finish_source(source, preview, preview_transform)

    def inspect_upload(self, contents: bytes, filename: str, bounds=None):
        if not contents:
            raise ValueError("The uploaded image is empty.")
        if len(contents) > MAX_UPLOAD_BYTES:
            raise ValueError("Image exceeds the 64 MiB upload limit.")
        if isinstance(bounds, str):
            try:
                bounds = json.loads(bounds)
            except json.JSONDecodeError as exc:
                raise ValueError("Image bounds must be a JSON array [west,south,east,north].") from exc
        filename = Path(str(filename).replace("\\", "/")).name or "Imported imagery"
        is_tiff = contents[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
        if is_tiff:
            source = self._inspect_geotiff(contents, filename)
            if source is not None:
                return source
        if bounds is None:
            raise ValueError("This image has no usable GeoTIFF CRS/affine transform. Supply its explicit north-up [west,south,east,north] extent; map zoom is not an image georeference.")
        west, south, east, north = validate_bounds(bounds, mercator=True)
        try:
            with Image.open(io.BytesIO(contents)) as image:
                if image.width * image.height > MAX_SOURCE_PIXELS:
                    raise ValueError("Image exceeds the 100-million-pixel source limit.")
                # The stated bounds describe the displayed, EXIF-oriented image.
                image = ImageOps.exif_transpose(image)
                original_width, original_height = image.size
                original_transform = [(east - west) / original_width, 0, west, 0, -(north - south) / original_height, north]
                source = self._source_metadata(original_width, original_height, name=filename, kind="drone", crs="EPSG:4326", transform=original_transform,
                    attribution="User-supplied imagery; georeference supplied by the uploader.", extra={
                        "original_width": original_width, "original_height": original_height, "original_crs": "EPSG:4326", "original_transform": original_transform,
                        "georeference_method": "User-specified north-up WGS84 extent", "resampled": False, "raster_format": "png",
                        "limitations": ["Extent supplied by the user; no ground-control or orthorectification validation is performed."]})
                # PIL needs the decoded original for EXIF orientation, but no
                # full-size NumPy RGBA/RGB copies are needed. Subsequent reads
                # and the projected preview use bounded rasterio windows.
                mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
                image.convert(mode).save(source["raster_path"], "PNG")
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Unsupported or corrupt image. Use PNG/JPEG or a georeferenced GeoTIFF.") from exc
        import rasterio
        with rasterio.Env(GDAL_CACHEMAX=64 * 1024 * 1024), rasterio.open(source["raster_path"]) as dataset:
            preview, preview_transform = self._raster_preview(dataset, source, [1, 2, 3], override_georeference=True)
        return self._finish_source(source, preview, preview_transform)

    @staticmethod
    def _rgb_indexes(dataset):
        """Use declared color interpretation instead of guessing band order."""
        from rasterio.enums import ColorInterp

        colors = list(dataset.colorinterp)
        if all(colors.count(color) == 1 for color in (ColorInterp.red, ColorInterp.green, ColorInterp.blue)):
            return [colors.index(color) + 1 for color in (ColorInterp.red, ColorInterp.green, ColorInterp.blue)]
        if colors.count(ColorInterp.gray) == 1 and all(color in (ColorInterp.gray, ColorInterp.alpha) for color in colors):
            return [colors.index(ColorInterp.gray) + 1]
        if len(colors) == 1 and colors[0] == ColorInterp.undefined:
            return [1]
        raise ValueError("GeoTIFF must declare RGB color bands or a grayscale band. Ambiguous/multispectral or palette data must be converted to an explicit RGB GeoTIFF before import.")

    @staticmethod
    def _render_bands(values, ranges=None):
        """Apply a single source-wide radiometric mapping to bounded reads."""
        raw = np.asarray(values.data)
        valid = ~np.ma.getmaskarray(values).any(axis=0) & np.isfinite(raw).all(axis=0)
        if raw.dtype == np.uint8 and ranges is None:
            bands = raw.copy()
        else:
            bands = np.zeros(raw.shape, dtype=np.uint8)
            for index, band in enumerate(raw):
                low, high = ranges[index] if ranges is not None else (0, 255)
                if high > low:
                    safe = np.where(valid, band, low).astype(np.float32)
                    bands[index] = np.clip((safe - low) / (high - low) * 255, 0, 255).astype(np.uint8)
        bands[:, ~valid] = 0
        if len(bands) == 1:
            bands = np.repeat(bands, 3, axis=0)
        return bands.transpose(1, 2, 0), valid

    def _raster_preview(self, dataset, source, indexes, ranges=None, *, override_georeference=False):
        from affine import Affine
        from rasterio.enums import ColorInterp, Resampling
        from rasterio.vrt import WarpedVRT

        width, height, transform = self._preview_grid(source)
        options = {}
        if override_georeference:
            options = {"src_crs": source["crs"], "src_transform": Affine(*source["transform"])}
        # The output grid is small regardless of native dimensions. GDAL reads
        # and warps native blocks, with a bounded cache/warp memory allowance.
        with WarpedVRT(dataset, crs="EPSG:3857", transform=Affine(*transform), width=width, height=height,
                resampling=Resampling.bilinear, add_alpha=ColorInterp.alpha not in dataset.colorinterp, warp_mem_limit=64, **options) as preview_dataset:
            values = preview_dataset.read(indexes, masked=True)
            rgb, valid = self._render_bands(values, ranges)
            valid &= np.all(preview_dataset.read_masks(indexes) > 0, axis=0)
        return Image.fromarray(np.dstack([rgb, valid.astype(np.uint8) * 255])), transform

    def _inspect_geotiff(self, contents, filename):
        try:
            import rasterio
            from affine import Affine
            from rasterio.enums import Resampling
            from rasterio.io import MemoryFile
        except ImportError as exc:
            raise RuntimeError("Rasterio is required to import GeoTIFF imagery. Install the base requirements.") from exc
        try:
            with rasterio.Env(GDAL_CACHEMAX=64 * 1024 * 1024), MemoryFile(contents) as memory_file, memory_file.open() as dataset:
                if not dataset.crs or dataset.transform == Affine.identity():
                    return None
                if dataset.width * dataset.height > MAX_SOURCE_PIXELS:
                    raise ValueError("GeoTIFF exceeds the 100-million-pixel source limit.")
                if dataset.width < 2 or dataset.height < 2:
                    raise ValueError("GeoTIFF needs at least 2 pixels in each dimension.")
                original_transform = list(dataset.transform)[:6]
                if not all(math.isfinite(v) for v in original_transform) or abs(dataset.transform.determinant) <= 1e-20:
                    raise ValueError("GeoTIFF affine transform is invalid or singular.")
                indexes = self._rgb_indexes(dataset)
                if any(np.dtype(dataset.dtypes[index - 1]).kind not in "uif" for index in indexes):
                    raise ValueError("GeoTIFF color bands must contain real numeric pixels, not complex values.")
                # Statistics are sampled once and applied identically to every
                # native window. No 100 MP image is materialized at ingestion.
                sample_width, sample_height = _fit_dimensions(dataset.width, dataset.height, 1024)
                sample = dataset.read(indexes, out_shape=(len(indexes), sample_height, sample_width), masked=True, resampling=Resampling.nearest)
                ranges = None
                radiometry = "Original 8-bit pixel values, read at native resolution."
                if any(np.dtype(dataset.dtypes[index - 1]) != np.uint8 for index in indexes):
                    ranges = []
                    for band in sample:
                        finite = band.compressed()
                        finite = finite[np.isfinite(finite)]
                        low, high = np.percentile(finite, [2, 98]) if finite.size else (0.0, 0.0)
                        ranges.append([float(low), float(high)])
                    radiometry = "Non-8-bit color bands stretched by source-wide sampled 2nd–98th percentiles; native pixel geometry is unchanged."
                source = self._source_metadata(dataset.width, dataset.height, name=filename, kind="drone", crs=dataset.crs.to_string(),
                    transform=original_transform, attribution="User-supplied georeferenced GeoTIFF.", extra={
                        "original_width": dataset.width, "original_height": dataset.height, "original_crs": dataset.crs.to_string(),
                        "original_transform": [float(v) for v in original_transform], "georeference_method": "Embedded native GeoTIFF CRS and affine transform; original pixels retained for windowed analysis",
                        "resampled": False, "raster_format": "geotiff", "rgb_bands": indexes, "radiometric_ranges": ranges,
                        "radiometry": radiometry, "limitations": ["GeoTIFF positioning is used as supplied; survey control accuracy has not been independently validated.",
                            "Non-RGB data is rejected; non-8-bit imagery is normalized for this RGB model, which may affect detection quality."]})
                source["raster_path"] = str(self.sources_dir / f"{source['id']}_native.tif")
                # A separate small, north-up EPSG:3857 view serves Leaflet.
                # WarpedVRT keeps native-file reads bounded and respects alpha
                # and nodata, including the empty wedges of rotated imagery.
                preview, preview_transform = self._raster_preview(dataset, source, indexes, ranges)
                Path(source["raster_path"]).write_bytes(contents)
                return self._finish_source(source, preview, preview_transform)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Could not read GeoTIFF georeference or raster: {str(exc)[:180]}") from exc

    def scan_map(self, bounds, confidence=0.35, min_area_sqm=4):
        confidence = self._confidence(confidence)
        min_area_sqm = self._minimum_area(min_area_sqm)
        west, south, east, north = validate_bounds(bounds, mercator=True)
        middle_lat = (south + north) / 2
        ground_width = abs(GEOD.inv(west, middle_lat, east, middle_lat)[2])
        ground_height = abs(GEOD.inv((west + east) / 2, south, (west + east) / 2, north)[2])
        if max(ground_width, ground_height) > MAX_MAP_EDGE_METERS:
            raise ValueError("Choose a map area no more than 4 km wide and 4 km high; zoom in or draw a smaller AOI.")
        if min(ground_width, ground_height) < 10:
            raise ValueError("Choose a map area at least 10 m wide and 10 m high.")
        self._load_model()
        project = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        xmin, ymin = project.transform(west, south)
        xmax, ymax = project.transform(east, north)
        requested_bounds = [west, south, east, north]
        try:
            rgb, affine, metadata = self._fetch_map_export([xmin, ymin, xmax, ymax])
        except RuntimeError as export_error:
            try:
                rgb, affine, metadata = self._fetch_map_tiles([xmin, ymin, xmax, ymax])
                metadata["export_fallback_reason"] = str(export_error)
            except RuntimeError as tile_error:
                raise RuntimeError(f"World Imagery could not be obtained. Export: {export_error} Cached tiles: {tile_error}") from tile_error
        source = self._save_source(rgb, name="Map AOI · Esri World Imagery", kind="map", crs="EPSG:3857",
            transform=affine, attribution=ESRI_ATTRIBUTION,
            extra={"requested_bounds": requested_bounds, "provider_url": ESRI_SERVICE, **metadata,
                "limitations": ["Satellite imagery acquisition date and native positional accuracy are unknown.",
                    "Raster pixel spacing does not imply native imagery resolution or cadastral survey accuracy."]})
        return self.scan_source(source, confidence, min_area_sqm)

    def _fetch_map_export(self, projected_bounds):
        """Read the exported pixels and the server's actual, adjusted extent."""
        xmin, ymin, xmax, ymax = projected_bounds
        ratio = (xmax - xmin) / (ymax - ymin)
        width = MAP_IMAGE_EDGE if ratio >= 1 else max(2, round(MAP_IMAGE_EDGE * ratio))
        height = MAP_IMAGE_EDGE if ratio <= 1 else max(2, round(MAP_IMAGE_EDGE / ratio))
        query = urlencode({"f": "json", "bbox": f"{xmin},{ymin},{xmax},{ymax}", "bboxSR": 3857,
            "imageSR": 3857, "size": f"{width},{height}", "format": "png32", "transparent": "false", "dpi": 96})
        try:
            exported = json.loads(self._download(f"{ESRI_SERVICE}/export?{query}", max_bytes=1_000_000))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Esri returned an unreadable export response.") from exc
        if not isinstance(exported, dict):
            raise RuntimeError("Esri returned an invalid export response.")
        if "error" in exported:
            raise RuntimeError(f"Esri map export failed: {str(exported['error'].get('message', 'service error'))[:180]}")
        href = exported.get("href")
        if not href or not isinstance(href, str) or not exported.get("width") or not exported.get("height"):
            raise RuntimeError("World Imagery export returned no raster image.")
        extent = exported.get("extent", {})
        spatial_reference = extent.get("spatialReference", {})
        if spatial_reference.get("latestWkid", spatial_reference.get("wkid")) not in (3857, 102100, 102113):
            raise RuntimeError("Esri export did not return the requested Web Mercator reference system.")
        try:
            xmin, ymin, xmax, ymax = [float(extent[key]) for key in ("xmin", "ymin", "xmax", "ymax")]
            if not all(math.isfinite(v) for v in (xmin, ymin, xmax, ymax)) or xmin >= xmax or ymin >= ymax:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Esri export returned an invalid image extent.") from exc
        parsed = urlparse(href)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".arcgisonline.com"):
            raise RuntimeError("Esri returned an unexpected image download host.")
        image_bytes = self._download(href, max_bytes=32 * 1024 * 1024)
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                if image.width > MAP_IMAGE_EDGE or image.height > MAP_IMAGE_EDGE:
                    raise RuntimeError("Esri export exceeded the configured image dimensions.")
                if image.width < 2 or image.height < 2:
                    raise RuntimeError("Esri export returned an empty or undersized image.")
                rgb = np.array(image.convert("RGB"))
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            raise RuntimeError("Esri export did not contain a usable raster image.") from exc
        return rgb, [(xmax - xmin) / width, 0, xmin, 0, -(ymax - ymin) / height, ymax], {
            "imagery_method": "mapserver_export", "georeference_method": "Esri export response extent in EPSG:3857"}

    @staticmethod
    def _tile_plan(projected_bounds):
        """Choose a real cache level and cover the AOI on that level's pixel grid.

        The selected level comes from the physical AOI and the cache grid;
        browser zoom has no role. Outward pixel rounding covers the requested
        extent and is preserved in the source affine instead of silently
        stretching the imagery back onto the unrounded input bounds.
        """
        xmin, ymin, xmax, ymax = projected_bounds
        if not all(math.isfinite(v) for v in projected_bounds) or xmin >= xmax or ymin >= ymax:
            raise ValueError("A valid projected map extent is required.")
        for level in range(MAX_MAP_TILE_LEVEL, -1, -1):
            resolution = MAP_TILE_BASE_RESOLUTION / (2 ** level)
            world_pixels = MAP_TILE_SIZE * (2 ** level)
            left = max(0, math.floor((xmin - MAP_TILE_ORIGIN_X) / resolution))
            right = min(world_pixels, math.ceil((xmax - MAP_TILE_ORIGIN_X) / resolution))
            top = max(0, math.floor((MAP_TILE_ORIGIN_Y - ymax) / resolution))
            bottom = min(world_pixels, math.ceil((MAP_TILE_ORIGIN_Y - ymin) / resolution))
            width, height = right - left, bottom - top
            if width < 2 or height < 2:
                continue
            min_column, max_column = left // MAP_TILE_SIZE, (right - 1) // MAP_TILE_SIZE
            min_row, max_row = top // MAP_TILE_SIZE, (bottom - 1) // MAP_TILE_SIZE
            tile_count = (max_column - min_column + 1) * (max_row - min_row + 1)
            if max(width, height) <= MAP_IMAGE_EDGE and tile_count <= MAX_MAP_TILES:
                return {"level": level, "resolution": resolution, "left": left, "right": right, "top": top, "bottom": bottom,
                    "width": width, "height": height, "min_column": min_column, "max_column": max_column,
                    "min_row": min_row, "max_row": max_row, "tile_count": tile_count}
        raise RuntimeError("The selected area cannot be covered within the 2048-pixel / 64-tile map limit.")

    def _fetch_map_tiles(self, projected_bounds):
        """Mosaic genuine cached tiles from the fixed official Esri service."""
        plan = self._tile_plan(projected_bounds)
        columns = range(plan["min_column"], plan["max_column"] + 1)
        rows = range(plan["min_row"], plan["max_row"] + 1)
        mosaic = Image.new("RGB", (len(columns) * MAP_TILE_SIZE, len(rows) * MAP_TILE_SIZE))
        def fetch_tile(column, row):
            url = f"{ESRI_SERVICE}/tile/{plan['level']}/{row}/{column}"
            contents = self._download(url, max_bytes=2 * 1024 * 1024, timeout=20)
            try:
                with Image.open(io.BytesIO(contents)) as tile:
                    if tile.size != (MAP_TILE_SIZE, MAP_TILE_SIZE):
                        raise RuntimeError("Esri returned a tile with unexpected dimensions.")
                    return column, row, tile.convert("RGB").copy()
            except (UnidentifiedImageError, OSError) as exc:
                raise RuntimeError("Esri returned an unreadable cached tile; no partial mosaic was used.") from exc
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="survey-map-tile") as executor:
            futures = [executor.submit(fetch_tile, column, row) for row in rows for column in columns]
            try:
                for future in as_completed(futures):
                    column, row, image = future.result()
                    mosaic.paste(image, ((column - plan["min_column"]) * MAP_TILE_SIZE, (row - plan["min_row"]) * MAP_TILE_SIZE))
            except Exception as exc:
                for future in futures:
                    future.cancel()
                if isinstance(exc, RuntimeError):
                    raise
                raise RuntimeError(f"Cached tile download failed: {str(exc)[:180]}") from exc
        crop_left = plan["left"] - plan["min_column"] * MAP_TILE_SIZE
        crop_top = plan["top"] - plan["min_row"] * MAP_TILE_SIZE
        cropped = mosaic.crop((crop_left, crop_top, crop_left + plan["width"], crop_top + plan["height"]))
        resolution = plan["resolution"]
        affine = [resolution, 0, MAP_TILE_ORIGIN_X + plan["left"] * resolution,
            0, -resolution, MAP_TILE_ORIGIN_Y - plan["top"] * resolution]
        return np.array(cropped), affine, {
            "imagery_method": "cached_tile_mosaic", "georeference_method": "Esri Web Mercator cache grid, cropped at actual raster pixel edges",
            "tile_level": plan["level"], "tile_count": plan["tile_count"], "tile_size": MAP_TILE_SIZE,
            "native_tile_spacing_3857_m": resolution, "tile_range": {
                "columns": [plan["min_column"], plan["max_column"]], "rows": [plan["min_row"], plan["max_row"]]},
            "tile_origin_3857": [MAP_TILE_ORIGIN_X, MAP_TILE_ORIGIN_Y], "resampled": False,
        }

    def _trusted_raster_path(self, source):
        try:
            raster_path = Path(source["raster_path"]).resolve()
            raster_path.relative_to(self.sources_dir.resolve())
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid source raster path.") from exc
        if not raster_path.is_file():
            raise ValueError("The stored source raster is missing. Import the imagery again.")
        return raster_path

    @contextmanager
    def _raster_reader(self, raster_path, source):
        """Yield a bounded RGB/mask window reader for new and legacy sources."""
        import rasterio
        from rasterio.windows import Window

        with rasterio.Env(GDAL_CACHEMAX=64 * 1024 * 1024), rasterio.open(raster_path) as dataset:
            if dataset.width != source["width"] or dataset.height != source["height"]:
                raise ValueError("Stored raster dimensions do not match its georeference.")
            if dataset.width * dataset.height > MAX_SOURCE_PIXELS:
                raise ValueError("Stored raster exceeds the 100-million-pixel source limit.")
            native = source.get("raster_format") == "geotiff" or raster_path.suffix.lower() in (".tif", ".tiff")
            if native:
                if not dataset.crs or CRS.from_user_input(source["crs"]) != dataset.crs:
                    raise ValueError("Stored native raster CRS does not match its georeference.")
                if not np.allclose(list(dataset.transform)[:6], source["transform"], rtol=1e-12, atol=1e-10):
                    raise ValueError("Stored native raster transform does not match its georeference.")
                indexes = self._rgb_indexes(dataset)
                ranges = source.get("radiometric_ranges")
                if ranges is not None and (len(ranges) != len(indexes) or any(len(pair) != 2 or not all(math.isfinite(float(v)) for v in pair) for pair in ranges)):
                    raise ValueError("Stored raster radiometric ranges are invalid.")
            else:
                # Existing *_analysis.png files retain their historic affine.
                # They are never reinterpreted as the original GeoTIFF.
                indexes = [1] if dataset.count == 1 else [1, 2, 3]
                if dataset.count not in (1, 3, 4):
                    raise ValueError("Stored analysis image does not have RGB or grayscale pixels.")
                ranges = None

            def read_window(x, y, width, height):
                window = Window(x, y, width, height)
                values = dataset.read(indexes, window=window, masked=True)
                rgb, valid = self._render_bands(values, ranges)
                valid &= np.all(dataset.read_masks(indexes, window=window) > 0, axis=0)
                rgb[~valid] = 0
                return rgb, valid

            yield read_window

    @staticmethod
    def _scan_region(source, bounds):
        width, height = int(source["width"]), int(source["height"])
        if bounds is None:
            return (0, 0, width, height), None
        from affine import Affine

        west, south, east, north = validate_bounds(bounds, mercator=True)
        project = Transformer.from_crs("EPSG:4326", source["crs"], always_xy=True)
        inverse = ~Affine(*source["transform"])
        edge = np.linspace(0, 1, 33)
        coordinates = ([(west + (east - west) * t, south) for t in edge]
            + [(east, south + (north - south) * t) for t in edge]
            + [(east - (east - west) * t, north) for t in edge]
            + [(west, north - (north - south) * t) for t in edge])
        pixels = [inverse * project.transform(lon, lat) for lon, lat in coordinates]
        if not np.isfinite(pixels).all():
            raise ValueError("Selected AOI cannot be projected into this imagery.")
        polygon = Polygon(pixels)
        if not polygon.is_valid:
            raise ValueError("Selected AOI cannot be represented in the source CRS. Choose a smaller area.")
        polygon = polygon.intersection(box(0, 0, width, height))
        if polygon.is_empty or polygon.area < 4:
            raise ValueError("Selected AOI does not overlap at least four source pixels. Select an area within the imported imagery.")
        left, top, right, bottom = polygon.bounds
        return (max(0, math.floor(left)), max(0, math.floor(top)), min(width, math.ceil(right)), min(height, math.ceil(bottom))), polygon

    @staticmethod
    def _deduplicate(candidates):
        kept = []
        # Favor complete masks over masks cut by an internal inference window.
        for candidate in sorted(candidates, key=lambda item: (not item["truncated"], item["confidence"], item["polygon"].area), reverse=True):
            polygon = candidate["polygon"]
            west, south, east, north = polygon.bounds
            duplicate = False
            for previous in kept:
                other = previous["polygon"]
                other_west, other_south, other_east, other_north = previous["pixel_bounds"]
                if east <= other_west or west >= other_east or north <= other_south or south >= other_north:
                    continue
                overlap = polygon.intersection(other).area
                if overlap <= 0:
                    continue
                if overlap / min(polygon.area, other.area) >= 0.8 or overlap / (polygon.area + other.area - overlap) >= 0.45:
                    duplicate = True
                    break
            if not duplicate:
                candidate["pixel_bounds"] = (west, south, east, north)
                kept.append(candidate)
        return kept

    def scan_source(self, source: dict, confidence=0.35, min_area_sqm=4, bounds=None):
        confidence = self._confidence(confidence)
        min_area_sqm = self._minimum_area(min_area_sqm)
        raster_path = self._trusted_raster_path(source)
        try:
            affine = [float(v) for v in source["transform"]]
            if len(affine) != 6 or not all(math.isfinite(v) for v in affine) or abs(affine[0] * affine[4] - affine[1] * affine[3]) <= 1e-20:
                raise ValueError()
            geographic = Transformer.from_crs(source["crs"], "EPSG:4326", always_xy=True)
            width, height = int(source["width"]), int(source["height"])
            if width < 2 or height < 2 or width * height > MAX_SOURCE_PIXELS:
                raise ValueError()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Stored source has invalid georeference metadata.") from exc
        (left, top, right, bottom), pixel_aoi = self._scan_region(source, bounds)
        windows = [(left + x, top + y) for y in _window_positions(bottom - top) for x in _window_positions(right - left)]
        if len(windows) > MAX_TILES:
            raise ValueError(f"This scan requires {len(windows)} native-resolution inference windows; the limit is {MAX_TILES}. Draw a smaller AOI within the imagery or split the source. The image will not be downsampled.")
        model = self._load_model()
        started = time.monotonic()
        candidates = []
        invalid_count = 0
        small_pixel_count = 0
        nodata_count = 0
        empty_tiles = 0
        try:
            from shapely import make_valid
            from affine import Affine
            from rasterio.features import rasterize
            with self._inference_lock, self._raster_reader(raster_path, source) as read_window:
                for x, y in windows:
                    tile, valid = read_window(x, y, min(TILE_SIZE, right - x), min(TILE_SIZE, bottom - y))
                    if not valid.any():
                        empty_tiles += 1
                        continue
                    # Ultralytics accepts NumPy images in BGR channel order.
                    results = model.predict(source=np.ascontiguousarray(tile[:, :, ::-1]), conf=confidence, imgsz=TILE_SIZE,
                        device="cpu", verbose=False, retina_masks=True, max_det=300)
                    for result in results:
                        if result.masks is None:
                            continue
                        masks = result.masks.xy
                        scores = result.boxes.conf.detach().cpu().tolist()
                        classes = result.boxes.cls.detach().cpu().tolist()
                        for points, score, class_id in zip(masks, scores, classes):
                            if int(class_id) != 0 or len(points) < 3:
                                continue
                            points = np.asarray(points, dtype=np.float64)
                            if not np.isfinite(points).all():
                                invalid_count += 1
                                continue
                            touches_edge = ((x > left and points[:, 0].min() <= 2) or (y > top and points[:, 1].min() <= 2)
                                or (x + tile.shape[1] < right and points[:, 0].max() >= tile.shape[1] - 2)
                                or (y + tile.shape[0] < bottom and points[:, 1].max() >= tile.shape[0] - 2))
                            points += [x, y]
                            polygon = Polygon(points)
                            if not polygon.is_valid:
                                polygon = make_valid(polygon)
                            polygon = polygon.intersection(box(x, y, x + tile.shape[1], y + tile.shape[0]))
                            if pixel_aoi is not None:
                                polygon = polygon.intersection(pixel_aoi)
                            pieces = [polygon] if polygon.geom_type == "Polygon" else [part for part in getattr(polygon, "geoms", []) if part.geom_type == "Polygon"]
                            for piece in pieces:
                                if piece.is_empty or piece.area < 4:
                                    small_pixel_count += 1
                                    continue
                                if not valid.all():
                                    covered = rasterize([(mapping(piece), 1)], out_shape=valid.shape,
                                        transform=Affine.translation(x, y), fill=0, all_touched=True, dtype="uint8") > 0
                                    if np.any(covered & ~valid):
                                        # A model hallucination over alpha/nodata
                                        # must not become a georeferenced roof.
                                        # Discard rather than invent a clipped
                                        # footprint at an invalid-data boundary.
                                        nodata_count += 1
                                        continue
                                piece_bounds = piece.bounds
                                source_edge = piece_bounds[0] <= 1 or piece_bounds[1] <= 1 or piece_bounds[2] >= width - 1 or piece_bounds[3] >= height - 1
                                candidates.append({"polygon": piece, "confidence": float(score), "truncated": bool(touches_edge),
                                    "source_edge": bool(source_edge), "aoi_edge": bool(pixel_aoi is not None and piece.distance(pixel_aoi.boundary) <= 1)})
                                if len(candidates) > MAX_CANDIDATES:
                                    raise RuntimeError("More than 2500 candidate masks were detected. Scan a smaller area.")
        except (RuntimeError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError(f"Building inference failed: {str(exc)[:220]}. No simulated detections were substituted.") from exc
        unique = self._deduplicate(candidates)
        features = []
        small_area_count = 0
        def pixel_to_geo(x, y, z=None):
            a, b, c, d, e, f = affine
            x_array, y_array = np.asarray(x), np.asarray(y)
            return geographic.transform(a * x_array + b * y_array + c, d * x_array + e * y_array + f)
        for candidate in unique:
            try:
                geographic_polygon = transform_geometry(pixel_to_geo, candidate["polygon"])
                measured = validate_geometry(json.loads(json.dumps(mapping(geographic_polygon))))
            except (ValueError, TypeError):
                invalid_count += 1
                continue
            # Apply the size threshold after the real source affine and WGS84
            # measurement. A pixel threshold alone changes physical meaning
            # between map, resampled and drone rasters.
            if measured["area_sqm"] < min_area_sqm:
                small_area_count += 1
                continue
            quality_flags = []
            if candidate["truncated"]:
                quality_flags.append("inference_tile_edge")
            if candidate["source_edge"]:
                quality_flags.append("source_image_edge")
            if candidate.get("aoi_edge"):
                quality_flags.append("scan_aoi_edge")
            features.append({"type": "Feature", "id": f"proposal-{uuid.uuid4().hex}", "geometry": measured["geometry"],
                "properties": {"name": "Building footprint", "feature_type": "building", "status": "draft", "source_id": source["id"],
                    "confidence": round(candidate["confidence"], 5), "area_sqm": measured["area_sqm"], "perimeter_m": measured["perimeter_m"],
                    "mask_area_px": round(candidate["polygon"].area, 2), "quality_flags": quality_flags,
                    "needs_review": True, "touches_tile_edge": candidate["truncated"], "touches_source_edge": candidate["source_edge"],
                    "notes": "AI building proposal. Review against source imagery and field observations; this is not an ownership parcel."}})
            if len(features) > MAX_FEATURES:
                raise RuntimeError("More than 1000 building proposals were detected. Split this survey into smaller areas.")
        return {"source": source, "geojson": {"type": "FeatureCollection", "features": features}, "inference": {
            "state": "completed", "model": self.model_path.name, "classes": ["building"], "device": "cpu", "confidence_threshold": confidence,
            "tile_size": TILE_SIZE, "tile_overlap": TILE_OVERLAP, "tiles_processed": len(windows) - empty_tiles, "tiles_planned": len(windows),
            "empty_nodata_tiles_skipped": empty_tiles, "nodata_masks_removed": nodata_count, "raw_candidates": len(candidates),
            "native_resolution": source.get("resampled") is False, "analysis_resampled": bool(source.get("resampled", False)),
            "scan_bounds": list(bounds) if bounds is not None else None, "scan_window_px": [left, top, right, bottom],
            "minimum_mask_area_px": 4, "small_pixel_masks_removed": small_pixel_count,
            "minimum_area_sqm": min_area_sqm, "small_area_masks_removed": small_area_count,
            "duplicate_masks_removed": len(candidates) - len(unique), "invalid_geometries_skipped": invalid_count, "detections": len(features),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "limitations": ["Building footprints only; parcels, roads and land-use are not inferred by this model.",
                "Overlapping windows reduce tile-edge loss but masks may still be incomplete; all proposals require review.",
                "The minimum-area filter suppresses small artifacts; it can also exclude small real structures. It does not measure detection accuracy.",
                "Original pixels and native affine are used for new imports; only browser previews are reduced. Older saved sources retain their historic analysis resolution.",
                "Masks touching nodata or transparent pixels are discarded; roofs at invalid-data boundaries may require manual tracing."]}}
