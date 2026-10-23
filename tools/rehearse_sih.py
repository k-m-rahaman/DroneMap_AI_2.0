"""Repeat the hardware-free SIH workflow with real, offline model inference.

This tool never opens the private workspace database. Each run creates a new
isolated public-demo workspace. Inputs, generated imagery and raw geometries
stay in the ignored .demo-runtime directory; only aggregate checks are public.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import csv
import hashlib
import io
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_RUNS = ROOT / ".demo-runtime" / "rehearsals"
DEFAULT_SOURCE = ROOT / ".demo-runtime" / "visitor-data" / "sources" / "2b95f22b139c4d21b9b7c1f40092a048.json"
NOTICE = "Workflow rehearsal only; no measured detection accuracy, drone capture, flight or legal boundary validation."


class RehearsalError(ValueError):
    pass


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def reserve_run(directory):
    """Use only a fresh descendant of the ignored rehearsal sandbox; no cleanup."""
    directory = Path(directory).resolve()
    sandbox = PRIVATE_RUNS.resolve()
    if directory == sandbox or not directory.is_relative_to(sandbox):
        raise RehearsalError("Run directory must be a new subdirectory of .demo-runtime/rehearsals.")
    if directory.exists():
        raise RehearsalError("Run directory already exists; choose a new directory. Existing runs are never overwritten.")
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def read_sample(metadata_path):
    metadata_path = Path(metadata_path).resolve()
    if not metadata_path.is_file():
        raise RehearsalError("Sample metadata is missing. Supply a saved public-demo map source with --source-metadata.")
    source_root = (ROOT / ".demo-runtime" / "visitor-data" / "sources").resolve()
    if not metadata_path.is_relative_to(source_root):
        raise RehearsalError("Only an explicit public-demo source sidecar under .demo-runtime/visitor-data/sources is accepted.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("kind") != "map" or metadata.get("resampled") is not False:
        raise RehearsalError("Use an original, unresampled public-demo map source; this is not a drone-capture test.")
    raster = Path(metadata.get("raster_path", "")).resolve()
    if not raster.is_relative_to(source_root) or not raster.is_file():
        raise RehearsalError("Sample raster must exist beside its public-demo metadata; stale or external paths are refused.")
    affine = metadata.get("transform")
    if not isinstance(affine, list) or len(affine) != 6 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in affine):
        raise RehearsalError("Sample must include six finite native affine coefficients.")
    if abs(affine[0] * affine[4] - affine[1] * affine[3]) < 1e-20 or not metadata.get("crs"):
        raise RehearsalError("Sample georeference is missing or singular.")
    return metadata, raster


def create_derived_geotiff(metadata, raster, target):
    """Wrap the same RGB pixels in native CRS/affine; never resample imagery."""
    import numpy as np
    from PIL import Image
    from affine import Affine
    import rasterio
    from rasterio.enums import ColorInterp

    with Image.open(raster) as image:
        rgb = np.asarray(image.convert("RGB"))
    height, width, _ = rgb.shape
    if [width, height] != [metadata.get("width"), metadata.get("height")]:
        raise RehearsalError("Original raster dimensions disagree with sample metadata.")
    with rasterio.open(target, "w", driver="GTiff", width=width, height=height, count=3,
                       dtype="uint8", crs=metadata["crs"], transform=Affine(*metadata["transform"]), compress="deflate") as dataset:
        dataset.write(rgb.transpose(2, 0, 1))
        dataset.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue)
        dataset.update_tags(AREA_OR_POINT="Area", REHEARSAL_DERIVATION="Satellite/map sample packaged as GeoTIFF; not drone capture",
                            SOURCE_ATTRIBUTION=str(metadata.get("attribution", "Unknown")),
                            SOURCE_ID=str(metadata.get("id", "")))
    with rasterio.open(target) as dataset:
        if not np.array_equal(dataset.read(), rgb.transpose(2, 0, 1)):
            raise RehearsalError("Derived GeoTIFF changed source pixels.")
    return width, height


def pdf_structure(document):
    """Check the PDF catalog, pages, traditional xref entries and EOF.

    This checks exported fpdf2 structure; it does not claim visual layout QA.
    """
    if not document.startswith(b"%PDF-") or not document.rstrip().endswith(b"%%EOF"):
        raise RehearsalError("PDF header or end marker is invalid.")
    start = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", document)
    if not start or document[int(start.group(1)):][:4] != b"xref":
        raise RehearsalError("PDF cross-reference location is invalid.")
    section = document[int(start.group(1)) + 4:document.find(b"trailer", int(start.group(1)))]
    lines = section.strip().splitlines()
    if not lines or len(lines[0].split()) != 2:
        raise RehearsalError("PDF cross-reference table is invalid.")
    first, count = map(int, lines[0].split())
    if first != 0 or count != len(lines) - 1:
        raise RehearsalError("PDF cross-reference size is invalid.")
    for number, line in enumerate(lines[1:]):
        fields = line.split()
        if len(fields) != 3:
            raise RehearsalError("PDF cross-reference entry is invalid.")
        if fields[2] == b"n" and not document[int(fields[0]):].startswith(f"{number} {int(fields[1])} obj".encode()):
            raise RehearsalError("PDF object offset is invalid.")
    pages = len(re.findall(rb"/Type\s*/Page\b", document))
    if pages < 1 or not re.search(rb"/Type\s*/Catalog\b", document):
        raise RehearsalError("PDF pages or catalog are missing.")
    return {"pages": pages, "bytes": len(document), "xref_objects": count - 1, "visual_layout_verified": False}


@contextmanager
def offline_only():
    attempts = {"blocked": 0}

    def refused(*args, **kwargs):
        attempts["blocked"] += 1
        raise RehearsalError("Network access is disabled during the offline rehearsal.")
    original_pair = socket.socketpair
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    internal_pair = threading.local()

    def socketpair(*args, **kwargs):
        # Windows asyncio creates a loopback socketpair for its internal wakeup
        # pipe. Permit only connections made synchronously inside that standard
        # library operation, never application-initiated network requests.
        internal_pair.active = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            internal_pair.active = False

    def guarded_connect(connection, address):
        if getattr(internal_pair, "active", False):
            return original_connect(connection, address)
        return refused()

    def guarded_connect_ex(connection, address):
        if getattr(internal_pair, "active", False):
            return original_connect_ex(connection, address)
        return refused()
    # TestClient uses ASGI directly. No maps, model downloads or external calls.
    with patch("socket.socketpair", socketpair), patch("socket.socket.connect", guarded_connect), patch("socket.socket.connect_ex", guarded_connect_ex), \
         patch("socket.socket.sendto", refused), patch("socket.create_connection", refused), \
         patch("socket.getaddrinfo", refused), patch("urllib.request.urlopen", refused), \
         patch("httpx.HTTPTransport.handle_request", refused), \
         patch("httpx.AsyncHTTPTransport.handle_async_request", refused), ExitStack() as optional_transports:
        # Requests comes with the optional AI installation. Core-only safety
        # checks must still run without importing an absent optional package.
        if importlib.util.find_spec("requests") is not None:
            optional_transports.enter_context(patch("requests.adapters.HTTPAdapter.send", refused))
        yield attempts
        if attempts["blocked"]:
            raise RehearsalError("An external network request was attempted; the rehearsal cannot pass as offline.")


def expect(response, code=200):
    if response.status_code != code:
        raise RehearsalError(f"Workflow endpoint returned HTTP {response.status_code}; expected {code}: {response.text[:300]}")
    return response


def run_rehearsal(directory, metadata_path, model_path):
    started = time.monotonic()
    report = {"format": "dronemap-sih-rehearsal", "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "passed": False, "notice": NOTICE, "checks": [], "metrics": {}, "limitations": [
                  "A saved map image was wrapped as a GeoTIFF; no actual drone or new imagery acquisition was used.",
                  "Model proposal counts and confidence are not accuracy measurements; independent reference labels are still required.",
                  "This exercises the backend through an in-process HTTP client; browser usability and PDF appearance require separate checks.",
                  "Raw imagery, polygons, temporary sessions and workspace database remain private and are not redistributed."]}
    metadata, raster = read_sample(metadata_path)
    model_path = Path(model_path).resolve()
    if not model_path.is_file():
        raise RehearsalError("Model weights are missing; provide a local --model path. No model is downloaded or fabricated.")
    directory = reserve_run(directory)
    report["model_sha256"] = digest(model_path)
    report["source"] = {"origin": "existing public-demo map sample", "attribution": metadata.get("attribution"),
                        "crs": metadata["crs"], "pixel_resampling": False, "raster_sha256": digest(raster)}
    (directory / "runtime").mkdir()
    run_environment = {key: str(directory / "runtime") for key in ("YOLO_CONFIG_DIR", "MPLCONFIGDIR", "TEMP", "TMP")}
    run_environment.update(DRONEMAP_DATA_DIR=str(directory / "workspace"), DRONEMAP_PUBLIC_DEMO="1",
                           YOLO_AUTOINSTALL="false", YOLO_OFFLINE="true")
    sys.path.insert(0, str(ROOT))

    def checked(name, detail=None):
        report["checks"].append({"name": name, "passed": True, "detail": detail})

    try:
        with patch.dict(os.environ, run_environment), offline_only() as network_attempts:
            from fastapi.testclient import TestClient
            from shapely.geometry import shape, box, mapping
            from shapely.affinity import translate
            from imagery import SurveyImagery
            from main import create_app
            app = create_app(directory / "workspace", imagery_service=SurveyImagery(directory / "workspace", model_path), public_demo=True)
            derived = directory / "derived-map-sample-not-drone.tif"
            width, height = create_derived_geotiff(metadata, raster, derived)
            write_json(directory / "original-source-metadata.json", metadata)
            checked("derived_geotiff_retains_native_pixels", {"width": width, "height": height, "crs": metadata["crs"]})
            with TestClient(app) as client:
                status = expect(client.get("/api/status")).json()
                if status["public_demo"] is not True or status["setup_required"]:
                    raise RehearsalError("Workspace was not opened in isolated public-demo mode.")
                entry = expect(client.post("/api/demo/start")).json()
                project_id = entry["project"]["id"]
                checked("isolated_guest_entry")
                imported = expect(client.post(f"/api/projects/{project_id}/sources", files={"file": (derived.name, derived.read_bytes(), "image/tiff")}), 201).json()
                if imported["width"] != width or imported["height"] != height or imported["transform"] != metadata["transform"]:
                    raise RehearsalError("Import failed to retain the original raster geometry.")
                checked("geotiff_import_native_geometry")
                preview = expect(client.get(imported["preview_url"]))
                if not preview.content.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RehearsalError("Source preview is not a valid PNG.")
                settings = {"confidence": 0.5, "min_area_sqm": 4}
                scan_started = time.monotonic()
                scan = expect(client.post(f"/api/sources/{imported['id']}/scan", json=settings)).json()
                scan_seconds = round(time.monotonic() - scan_started, 3)
                predictions = scan["geojson"]["features"]
                geometries = [shape(feature["geometry"]) for feature in predictions]
                unique = len({g.wkb for g in geometries})
                if len(predictions) < 2 or unique != len(predictions) or any(g.is_empty or not g.is_valid or g.area <= 0 for g in geometries):
                    raise RehearsalError("Real inference did not produce multiple distinct valid building proposals.")
                if scan["inference"].get("state") != "completed" or scan["inference"].get("analysis_resampled") is not False:
                    raise RehearsalError("Real native-resolution inference did not complete.")
                write_json(directory / "predictions.geojson", {"type": "FeatureCollection", "features": predictions})
                write_json(directory / "inference-evidence.json", {"settings": settings, "inference": scan["inference"], "source": scan["source"],
                           "original_source": metadata, "model_sha256": report["model_sha256"], "derived_map_not_drone_capture": True})
                report["metrics"].update(building_proposals=len(predictions), distinct_valid_geometries=unique, scan_request_seconds=scan_seconds,
                                         model_pipeline_seconds=scan["inference"].get("elapsed_seconds"),
                                         native_windows=scan["inference"].get("tiles_processed"))
                checked("real_offline_multi_building_inference")
                saved = []
                for index, feature in enumerate(predictions[:3]):
                    payload = {"geometry": feature["geometry"], "properties": {**feature["properties"], "name": f"Rehearsal building {index + 1}",
                               "source_id": imported["id"], "status": "draft", "notes": "Offline rehearsal from derived map imagery; not a field survey."}}
                    result = expect(client.post(f"/api/projects/{project_id}/features", json=payload, headers={"Idempotency-Key": f"rehearsal-{index}"}), 201).json()
                    retry = expect(client.post(f"/api/projects/{project_id}/features", json=payload, headers={"Idempotency-Key": f"rehearsal-{index}"}), 201).json()
                    if retry["id"] != result["id"]:
                        raise RehearsalError("A retried save created a duplicate record.")
                    saved.append(result)
                checked("multiple_buildings_saved_and_retry_is_idempotent", {"saved": len(saved)})
                first = saved[0]
                original_geometry = shape(first["geometry"])
                edited_geometry = mapping(translate(original_geometry, xoff=(original_geometry.bounds[2] - original_geometry.bounds[0]) * 0.01))
                update = {"geometry": edited_geometry, "properties": {**first["properties"], "name": "Edited rehearsal geometry", "notes": "Geometry deliberately shifted by 1% of its width for an edit exercise; this is not independent accuracy review."}}
                edited = expect(client.put(f"/api/features/{first['id']}", json=update)).json()
                if edited["properties"]["version"] != 2 or shape(edited["geometry"]).equals(original_geometry):
                    raise RehearsalError("Geometry edit was not persisted or version did not advance.")
                expect(client.put(f"/api/features/{first['id']}", json=update), 409)
                history = expect(client.get(f"/api/features/{first['id']}/history")).json()["history"]
                if [item["version"] for item in history] != [2, 1]:
                    raise RehearsalError("Create/edit version history is incomplete.")
                checked("edit_history_and_stale_update_protection")
                reconcile = expect(client.post(f"/api/projects/{project_id}/proposals/reconcile", json={"features": [predictions[0], predictions[0]]})).json()
                if reconcile["summary"]["duplicates"] != 2 or reconcile["summary"]["kept"] != 0:
                    raise RehearsalError("Saved building was not reconciled against repeated proposals.")
                checked("duplicate_proposals_reconciled")
                pdf = expect(client.get(f"/api/features/{first['id']}/report.pdf"))
                report["metrics"]["pdf"] = pdf_structure(pdf.content)
                if pdf.headers.get("x-verification-url"):
                    raise RehearsalError("Temporary demo PDF unexpectedly exposes a permanent verification link.")
                (directory / "sample-survey-record.pdf").write_bytes(pdf.content)
                checked("pdf_export_structural_validation")
                collection = expect(client.get(f"/api/projects/{project_id}/export.geojson")).json()
                csv_response = expect(client.get(f"/api/projects/{project_id}/export.csv"))
                rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
                if len(collection["features"]) != len(saved) or len(rows) != len(saved):
                    raise RehearsalError("Export record counts disagree with saved buildings.")
                for row in rows:
                    if not shape(json.loads(row["geometry"])).is_valid:
                        raise RehearsalError("CSV geometry cannot be reopened.")
                write_json(directory / "saved-buildings.geojson", collection)
                (directory / "saved-buildings.csv").write_bytes(csv_response.content)
                checked("geojson_csv_exports_reopen", {"records": len(rows)})
                west, south, east, north = metadata["bounds"]
                # An explicit illustrative reference parcel, never presented as cadastral truth.
                parcel = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(box(west, south, west + (east-west)/4, south + (north-south)/4)),
                          "properties": {"name": "Illustrative reference parcel", "reference_id": "REHEARSAL-ONLY-001"}}]}
                parcel_project = expect(client.post("/api/projects", json={"name": "Illustrative parcel import", "description": "Synthetic reference geometry, not ownership evidence."}), 201).json()["id"]
                parcel_preview = expect(client.post(f"/api/projects/{parcel_project}/imports/preview", json={"collection": parcel, "feature_type": "parcel"})).json()
                if parcel_preview["saved"] is not False or len(parcel_preview["features"]) != 1:
                    raise RehearsalError("Parcel preview failed.")
                parcel_feature = parcel_preview["features"][0]
                expect(client.post(f"/api/projects/{parcel_project}/features", json={"geometry": parcel_feature["geometry"], "properties": parcel_feature["properties"]}), 201)
                checked("illustrative_parcel_import_preview_and_save")
                mission = expect(client.post(f"/api/projects/{project_id}/missions/preview", json={"geometry": mapping(box(west, south, east, north))})).json()
                plan = json.loads(json.dumps(mission["plan"], allow_nan=False))
                items = plan["mission"]["items"]
                if plan["fileType"] != "Plan" or plan["version"] != 1 or plan["mission"]["version"] != 2 or len(items) < 2:
                    raise RehearsalError("Mission preview does not produce a valid QGroundControl plan envelope.")
                if any(item["command"] != 16 or item["frame"] != 3 or len(item["params"]) != 7 for item in items):
                    raise RehearsalError("Mission contains unexpected commands or coordinates.")
                route = shape(mission["geojson"]["features"][0]["geometry"])
                if not box(west, south, east, north).buffer(1e-8).covers(route):
                    raise RehearsalError("Planned route escapes the chosen example survey area.")
                write_json(directory / "example-survey.plan", plan)
                report["metrics"]["planning_waypoints"] = len(items)
                checked("coverage_route_and_qgc_plan_export")
                drone = expect(client.get("/api/drone/status")).json()
                ports = expect(client.get("/api/drone/ports")).json()
                expect(client.post("/api/drone/connect", json={"transport": "udpin", "host": "127.0.0.1", "port": 14551}), 403)
                if drone.get("state") != "unavailable" or ports["ports"]:
                    raise RehearsalError("Public demo exposes physical drone access.")
                checked("physical_drone_access_disabled")
                cookies = dict(client.cookies)
            # Fresh application and HTTP client prove persistence beyond one in-memory app.
            reloaded = create_app(directory / "workspace", imagery_service=SurveyImagery(directory / "workspace", model_path), public_demo=True)
            with TestClient(reloaded) as again:
                again.cookies.update(cookies)
                restored = expect(again.get(f"/api/projects/{project_id}/features")).json()["features"]
                restored_first = next(f for f in restored if f["id"] == first["id"])
                if len(restored) != len(saved) or restored_first["properties"]["version"] != 2 or not shape(restored_first["geometry"]).equals(shape(edited["geometry"])):
                    raise RehearsalError("Saved records did not survive application recreation.")
                checked("saved_records_survive_app_restart")
            report["metrics"]["external_network_attempts"] = network_attempts["blocked"]
            checked("offline_external_network_attempts_zero")
            report["passed"] = True
    except Exception as exc:
        report["passed"] = False
        # Errors can include private absolute paths; preserve detail only in ignored run.
        (directory / "failure.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        report["failure"] = "A required workflow check failed. Inspect failure.txt in the private rehearsal directory."
    report["metrics"]["total_seconds"] = round(time.monotonic() - started, 3)
    write_json(directory / "rehearsal-summary.json", report)
    return report, directory


def markdown_report(report):
    lines = ["# SIH hardware-free workflow rehearsal", "", "Result: **" + ("PASSED" if report["passed"] else "FAILED") + "**", "",
             report["notice"], "", f"Recorded: {report['created_at']}", "", "## Checks", ""]
    lines.extend(f"- PASS — {check['name'].replace('_', ' ')}" for check in report["checks"])
    lines += ["", "## Observed results", "", "```json", json.dumps(report["metrics"], indent=2), "```", "", "## Limits", ""]
    lines.extend("- " + limitation for limitation in report["limitations"])
    if report.get("failure"):
        lines += ["", report["failure"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-metadata", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--model", type=Path, default=ROOT / "best.pt")
    parser.add_argument("--work-dir", type=Path, default=PRIVATE_RUNS / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]))
    parser.add_argument("--report-prefix", type=Path, default=ROOT / "outputs" / "SIH" / "rehearsal-latest")
    args = parser.parse_args(argv)
    try:
        prefix = args.report_prefix.resolve()
        public_reports = (ROOT / "outputs" / "SIH").resolve()
        if prefix.parent != public_reports or not prefix.name.startswith("rehearsal"):
            raise RehearsalError("Sanitized reports must use a rehearsal-prefixed filename directly under outputs/SIH.")
        report, directory = run_rehearsal(args.work_dir, args.source_metadata, args.model)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        write_json(prefix.with_suffix(".json"), report)
        prefix.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "metrics": report["metrics"], "private_run": str(directory)}, indent=2))
        return 0 if report["passed"] else 1
    except (RehearsalError, OSError, ValueError) as exc:
        print(f"Rehearsal could not start: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
