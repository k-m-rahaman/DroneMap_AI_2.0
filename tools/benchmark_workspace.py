"""Prepare independent building labels, then evaluate immutable local inputs.

This workflow produces reference-agreement evidence, not a field-accuracy claim.
It never obtains labels from model predictions or changes the application data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import _geometries, evaluate_buildings
from shapely.geometry import box

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_MODEL_BYTES = 2 * 1024 * 1024 * 1024
SCHEMA_VERSION = 1
BASE_FILES = {"aoi.geojson", "predictions.geojson", "source.json", "settings.json"}
IMAGE_NAMES = {"imagery.png", "imagery.jpg", "imagery.jpeg", "imagery.tif", "imagery.tiff"}
BOUNDARY_POLICY = (
    "Label every visible building instance inside the AOI, including missed buildings. "
    "Clip footprints crossing the AOI boundary to that boundary; apply the same policy "
    "to predictions and references. Keep separate building instances separate. "
    "If any part of the AOI cannot be exhaustively interpreted, choose a smaller AOI "
    "and prepare a new workspace instead of omitting difficult regions."
)
LIMITATIONS = [
    "Metrics measure agreement with the supplied reference footprints, not independently verified field accuracy.",
    "Identity, blind-labeling and coverage declarations are attestations; this tool cannot prove label independence or completeness.",
    "No training/test independence, ownership, cadastral boundary, survey checkpoint accuracy or generalization is established.",
    "Matched IoU averages exclude unmatched buildings; read them alongside TP, FP, FN, precision and recall.",
    "This single-area result is not a representative multi-location benchmark; retain separate areas and report selection criteria.",
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(contents, label):
    try:
        return json.loads(contents.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Non-finite JSON number: {value}")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON.") from exc


def _plain_path(path):
    """Reject traversal and link/junction ancestors before resolving any path."""
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("Parent-directory traversal is not allowed; use a direct path.")
    path = path.absolute()
    for part in [*reversed(path.parents), path]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError(f"Symbolic links and junctions are not allowed: {part.name}")
    return path


def _read_file(path):
    path = _plain_path(path)
    if not path.is_file():
        raise ValueError(f"Expected a regular input file: {path.name}")
    before = path.stat()
    if before.st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{path.name} exceeds the 64 MiB input limit; use a smaller area.")
    with path.open("rb") as stream:
        contents = stream.read(MAX_INPUT_BYTES + 1)
    after = path.stat()
    if len(contents) > MAX_INPUT_BYTES or len(contents) != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"{path.name} changed while being read; try again with a stable input.")
    return contents


def _digest(contents):
    return {"sha256": hashlib.sha256(contents).hexdigest(), "bytes": len(contents)}


def _model_digest(path):
    path = _plain_path(path)
    if not path.is_file():
        raise ValueError("Model must be a regular file.")
    before = path.stat()
    if not 0 < before.st_size <= MAX_MODEL_BYTES:
        raise ValueError("Model must contain between 1 byte and 2 GiB.")
    checksum, consumed = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
            consumed += len(chunk)
            if consumed > MAX_MODEL_BYTES:
                raise ValueError("Model exceeds 2 GiB.")
    after = path.stat()
    if consumed != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Model changed while being hashed.")
    return {"filename": path.name, "sha256": checksum.hexdigest(), "bytes": consumed}


def _bounds(values, label):
    if not isinstance(values, (list, tuple)) or len(values) != 4 or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values
    ):
        raise ValueError(f"{label} must be four finite WGS84 west south east north coordinates.")
    west, south, east, north = values
    if not -180 <= west < east <= 180 or not -85 < south < north < 85 or east - west > 180:
        raise ValueError(f"{label} requires ordered local WGS84 bounds outside polar regions.")
    return [float(value) for value in values]


def _aoi_collection(bounds):
    west, south, east, north = bounds
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"role": "evaluation_aoi"},
        "geometry": {"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]]}}]}


def _validate_aoi(bounds):
    bounds = _bounds(bounds, "AOI")
    # Reuse the existing evaluator's local-extent/projection restrictions.
    evaluate_buildings(_aoi_collection(bounds), {"type": "FeatureCollection", "features": []})
    return bounds


def _validate_source(source, bounds):
    if not isinstance(source, dict):
        raise ValueError("Source metadata must be a JSON object.")
    for field in ("name", "kind", "crs", "attribution"):
        if not isinstance(source.get(field), str) or not source[field].strip():
            raise ValueError(f"Source metadata requires a nonempty {field}.")
    for field in ("width", "height"):
        if isinstance(source.get(field), bool) or not isinstance(source.get(field), int) or source[field] <= 0:
            raise ValueError(f"Source metadata requires a positive integer {field}.")
    source_bounds = _bounds(source.get("bounds"), "Source bounds")
    if not box(*source_bounds).covers(box(*bounds)):
        raise ValueError("The explicit AOI must lie entirely within the source bounds.")


def _validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("Inference settings must be a JSON object.")
    for field, minimum, maximum in (("confidence_threshold", 0, 1), ("minimum_area_sqm", 0, float("inf"))):
        if field not in settings:
            raise ValueError(f"Inference settings require {field}; use null when the actual value is unknown.")
        value = settings.get(field)
        # An older inference snapshot may not retain every setting. Record the
        # uncertainty explicitly instead of inventing a default configuration.
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"Inference settings require a valid {field}, or null for an unknown value.")


def _inside_aoi(collection, bounds, label, *, nonempty=False):
    geometries, _ = _geometries(collection, label)
    if nonempty and not geometries:
        raise ValueError("References are empty. Draw independent labels and complete review before evaluating.")
    aoi = box(*bounds)
    for index, geometry in enumerate(geometries):
        if not aoi.covers(geometry):
            raise ValueError(f"{label} feature {index} extends outside the common explicit AOI; no automatic clipping is performed.")
    return geometries


def _new_output(path):
    path = _plain_path(path)
    if path.exists():
        raise ValueError("Output already exists; choose a new directory to avoid overwriting inputs or prior evidence.")
    if not path.parent.is_dir():
        raise ValueError("Output parent directory must already exist.")
    return path


def _publish(output, files):
    """Validate before calling; publish a completed sibling directory in one rename."""
    output = _new_output(output)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for name, contents in files.items():
            # All callers use fixed flat names; never interpret supplied JSON paths.
            if Path(name).name != name or name in {".", ".."} or "/" in name or "\\" in name:
                raise ValueError("Output contains an unsafe file name.")
            (temporary / name).write_bytes(contents)
        # Windows scanners can briefly hold a just-written file open. Retry a
        # denied directory rename, but never replace a newly occupied output.
        for attempt in range(6):
            _new_output(output)
            try:
                os.rename(temporary, output)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.05 * 2 ** attempt)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return output


def prepare(predictions_path, source_path, settings_path, model_path, *, aoi, area_name, output, imagery_path=None):
    """Freeze supplied inference inputs and create deliberately empty references."""
    _new_output(output)
    if not isinstance(area_name, str) or not area_name.strip() or len(area_name) > 180:
        raise ValueError("Area name requires 1 to 180 characters.")
    bounds = _validate_aoi(aoi)
    files = {"predictions.geojson": _read_file(predictions_path), "source.json": _read_file(source_path),
             "settings.json": _read_file(settings_path), "aoi.geojson": _json_bytes(_aoi_collection(bounds))}
    predictions = _parse_json(files["predictions.geojson"], "Predictions")
    _inside_aoi(predictions, bounds, "Predictions")
    _validate_source(_parse_json(files["source.json"], "Source"), bounds)
    _validate_settings(_parse_json(files["settings.json"], "Settings"))
    imagery_name = None
    if imagery_path is not None:
        imagery_name = "imagery" + Path(imagery_path).suffix.lower()
        if imagery_name not in IMAGE_NAMES:
            raise ValueError("Optional source imagery must be PNG, JPEG or GeoTIFF.")
        files[imagery_name] = _read_file(imagery_path)
        if not files[imagery_name]:
            raise ValueError("Optional source imagery is empty.")
    manifest = {"schema_version": SCHEMA_VERSION, "kind": "building_reference_agreement", "area_name": area_name.strip(),
        "created_at": _now(), "aoi": {"crs": "EPSG:4326", "bounds": bounds}, "boundary_policy": BOUNDARY_POLICY,
        "model": _model_digest(model_path), "imagery_file": imagery_name,
        "frozen_files": {name: _digest(contents) for name, contents in files.items()},
        "notes": ["Predictions are immutable inference snapshots, not reference labels.",
                  "Hashes identify supplied files; they do not certify acquisition or model training provenance.",
                  "Null inference settings are unknown; no defaults or acquisition provenance are inferred.",
                  "No source imagery was snapshotted; labels must document the exact imagery used." if imagery_name is None else
                  "The optional imagery file is copied unchanged and hashed, not interpreted or reprojected by this tool."]}
    files["manifest.json"] = _json_bytes(manifest)
    files["references.geojson"] = _json_bytes({"type": "FeatureCollection", "features": []})
    files["review.json"] = _json_bytes({"labeler": "", "reviewer": "", "labeled_at": "", "reviewed_at": "",
        "labeling_method": "", "imagery_observed": "", "independent_of_predictions": False,
        "coverage_complete": False, "reviewer_checked_all_buildings": False,
        "boundary_policy_acknowledged": False, "notes": ""})
    files["LABELING_INSTRUCTIONS.md"] = _labeling_instructions(manifest).encode("utf-8")
    return _publish(output, files)


def _labeling_instructions(manifest):
    return f"""# Independent labels: {manifest['area_name']}

Status: NOT EVALUATED. `references.geojson` is intentionally empty.

1. Give a labeler the exact source imagery, `source.json` and `aoi.geojson`.
   Hide `predictions.geojson` and all model overlays until independent labels are frozen.
   Do not trace, accept, copy or edit predictions into the reference layer.
2. In QGIS or another GIS editor, draw a separate polygon for every visible building
   inside the AOI. Export the reference layer as WGS84 longitude/latitude GeoJSON.
   Use closed valid Polygon/MultiPolygon rings and distinct building identifiers.
3. Boundary and coverage policy: {BOUNDARY_POLICY}
4. Have a different reviewer check the imagery and every reference building, search
   for omitted buildings, verify the AOI, and correct labels independently of predictions.
5. Save those labels as `references.geojson`. Fill in `review.json` with both identities,
   ISO 8601 dates, the manual labeling method and exact imagery observed. Set each
   declaration to true only when it accurately describes the work performed.
6. Evaluate into a NEW output directory. Never change frozen predictions, settings,
   source, AOI or manifest. To change inference settings, prepare a new workspace.

These declarations cannot prove independence. Keep your label creation history,
review evidence and source licensing/acquisition details for examination.
No field-accuracy percentage can be claimed before real independent labels exist.
"""


def _validate_descriptor(value, label, *, model=False):
    if not isinstance(value, dict) or not isinstance(value.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise ValueError(f"Manifest has an invalid {label} SHA256.")
    if isinstance(value.get("bytes"), bool) or not isinstance(value.get("bytes"), int) or value["bytes"] < (1 if model else 0):
        raise ValueError(f"Manifest has an invalid {label} byte count.")
    if model and (not isinstance(value.get("filename"), str) or not value["filename"] or Path(value["filename"]).name != value["filename"] or any(char in value["filename"] for char in "/\\:")):
        raise ValueError("Manifest model filename must be a plain filename.")


def _review(review):
    if not isinstance(review, dict):
        raise ValueError("Review metadata must be a JSON object.")
    for field in ("labeler", "reviewer", "labeling_method", "imagery_observed"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            raise ValueError(f"Review metadata requires {field}.")
    if review["labeler"].strip().casefold() == review["reviewer"].strip().casefold():
        raise ValueError("A different reviewer must check the independently drawn labels.")
    dates = []
    for field in ("labeled_at", "reviewed_at"):
        try:
            date = datetime.fromisoformat(review[field].replace("Z", "+00:00"))
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError(f"Review metadata requires an ISO 8601 {field} timestamp with timezone.") from exc
        if date.tzinfo is None:
            raise ValueError(f"Review metadata requires a timezone in {field}.")
        dates.append(date)
    if dates[1] < dates[0]:
        raise ValueError("Review timestamp must not precede label completion.")
    for field in ("independent_of_predictions", "coverage_complete", "reviewer_checked_all_buildings", "boundary_policy_acknowledged"):
        if review.get(field) is not True:
            raise ValueError(f"Review declaration {field} must be true after the work is actually completed.")


def evaluate(workspace, *, output):
    """Validate the complete area before writing either threshold's report."""
    _new_output(output)
    workspace = _plain_path(workspace)
    if not workspace.is_dir():
        raise ValueError("Benchmark workspace must be a directory.")
    manifest_bytes = _read_file(workspace / "manifest.json")
    manifest = _parse_json(manifest_bytes, "Manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("kind") != "building_reference_agreement":
        raise ValueError("Unsupported benchmark manifest.")
    if not isinstance(manifest.get("area_name"), str) or not manifest["area_name"].strip():
        raise ValueError("Manifest requires an area name.")
    if manifest.get("boundary_policy") != BOUNDARY_POLICY:
        raise ValueError("Manifest boundary policy is missing or changed; prepare a new workspace.")
    aoi = manifest.get("aoi")
    if not isinstance(aoi, dict) or aoi.get("crs") != "EPSG:4326":
        raise ValueError("Manifest requires explicit WGS84 AOI bounds.")
    bounds = _validate_aoi(aoi.get("bounds"))
    _validate_descriptor(manifest.get("model"), "model", model=True)
    imagery_file = manifest.get("imagery_file")
    if imagery_file is not None and (not isinstance(imagery_file, str) or imagery_file not in IMAGE_NAMES):
        raise ValueError("Manifest imagery filename is unsafe or unsupported.")
    expected_files = BASE_FILES | ({imagery_file} if imagery_file is not None else set())
    frozen = manifest.get("frozen_files")
    if not isinstance(frozen, dict) or set(frozen) != expected_files:
        raise ValueError("Manifest frozen files must use the exact supported filenames; paths are not accepted.")
    files = {}
    for name in sorted(expected_files):
        _validate_descriptor(frozen[name], name)
        files[name] = _read_file(workspace / name)
        if _digest(files[name]) != frozen[name]:
            raise ValueError(f"Frozen input {name} has changed; prepare a new workspace instead.")
    if _parse_json(files["aoi.geojson"], "AOI") != _aoi_collection(bounds):
        raise ValueError("AOI file and explicit manifest bounds disagree.")
    source, settings = _parse_json(files["source.json"], "Source"), _parse_json(files["settings.json"], "Settings")
    _validate_source(source, bounds)
    _validate_settings(settings)
    predictions = _parse_json(files["predictions.geojson"], "Predictions")
    references_bytes, review_bytes = _read_file(workspace / "references.geojson"), _read_file(workspace / "review.json")
    references, review = _parse_json(references_bytes, "References"), _parse_json(review_bytes, "Review")
    _inside_aoi(predictions, bounds, "Predictions")
    _inside_aoi(references, bounds, "References", nonempty=True)
    _review(review)
    if _digest(references_bytes)["sha256"] == _digest(files["predictions.geojson"])["sha256"]:
        raise ValueError("References are byte-identical to predictions; supply independently created labels, never a copied prediction file.")
    results = {f"{threshold:.2f}": evaluate_buildings(predictions, references, iou_threshold=threshold) for threshold in (0.5, 0.75)}
    report = {"schema_version": SCHEMA_VERSION, "kind": "building_reference_agreement", "area_name": manifest["area_name"],
        "evaluated_at": _now(), "aoi": aoi, "boundary_policy": BOUNDARY_POLICY, "model": manifest["model"],
        "source": source, "settings": settings, "review": review,
        "input_hashes": {**{name: _digest(contents) for name, contents in files.items()},
                         "manifest.json": _digest(manifest_bytes), "references.geojson": _digest(references_bytes), "review.json": _digest(review_bytes)},
        "thresholds": results, "limitations": LIMITATIONS}
    return _publish(output, {"report.json": _json_bytes(report), "report.html": _html_report(report).encode("utf-8"),
        "evaluated-references.geojson": references_bytes, "review.json": review_bytes, "manifest.json": manifest_bytes})


def _html_report(report):
    esc = lambda value: html.escape(str(value), quote=True)
    percent = lambda value: "Undefined" if value is None else f"{100 * value:.1f}%"
    setting = lambda key: "Unknown (not retained)" if report["settings"][key] is None else esc(report["settings"][key])
    rows = []
    for threshold, result in report["thresholds"].items():
        values = [threshold, result["prediction_count"], result["reference_count"], result["true_positives"],
                  result["false_positives"], result["false_negatives"], percent(result["precision"]), percent(result["recall"]),
                  percent(result["f1"]), percent(result["mean_matched_iou"])]
        rows.append("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in values) + "</tr>")
    source, review = report["source"], report["review"]
    hashes = "".join(f"<tr><td>{esc(name)}</td><td><code>{esc(value['sha256'])}</code></td></tr>" for name, value in report["input_hashes"].items())
    limits = "".join(f"<li>{esc(item)}</li>" for item in report["limitations"])
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reference agreement · {esc(report['area_name'])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1150px;margin:40px auto;padding:0 24px;color:#183b3a;background:#f6f8f5}}h1{{line-height:1.2}}.note{{background:#fff0cb;border-left:5px solid #946900;padding:18px}}section{{background:white;border:1px solid #dce4df;border-radius:12px;padding:22px;margin:22px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #dce4df}}.scroll{{overflow-x:auto}}code{{overflow-wrap:anywhere;font-size:12px}}small{{color:#47625e}}@media print{{body{{margin:0;background:white}}section{{break-inside:avoid}}}}</style>
<header><small>DRONEMAP AI · GEOVISION</small><h1>Reference agreement: {esc(report['area_name'])}</h1>
<p>Evaluated {esc(report['evaluated_at'])}</p></header>
<p class="note"><strong>Agreement with supplied labels.</strong> This is not a field-accuracy certification.
The labeler and reviewer supplied declarations of independence and complete coverage; the tool cannot prove those declarations.</p>
<section><h2>Building detection and footprint overlap</h2><div class="scroll"><table><thead><tr><th>IoU threshold</th><th>Predictions</th><th>References</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>F1</th><th>Matched mean IoU</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p>TP = matched buildings, FP = unmatched proposals, FN = missed reference buildings. Each building matches at most once.
Matching maximizes the number of eligible pairs, using IoU-prioritized adjacency; it does not maximize total IoU.
Matched mean IoU describes matched buildings only. Detailed matches and unmatched indices are retained in <a href="report.json">report.json</a>.</p></section>
<section><h2>Source and review record</h2><p>Source: {esc(source['name'])} · {esc(source['kind'])} · {esc(source['width'])} × {esc(source['height'])} pixels · {esc(source['crs'])}<br>
Attribution: {esc(source['attribution'])}<br>AOI [west, south, east, north]: {esc(report['aoi']['bounds'])}<br>
Labeler: {esc(review['labeler'])} · Reviewer: {esc(review['reviewer'])}<br>
Labeling method: {esc(review['labeling_method'])}<br>Imagery observed: {esc(review['imagery_observed'])}</p>
<p>{esc(BOUNDARY_POLICY)}</p><p>Model: {esc(report['model']['filename'])}<br><code>SHA256 {esc(report['model']['sha256'])}</code></p>
<p>Confidence threshold: {setting('confidence_threshold')} · Minimum area (m²): {setting('minimum_area_sqm')}.</p>
<p>Null values mean the actual inference setting was not retained. No default value has been substituted. Other supplied settings are preserved in <a href="report.json">report.json</a>.</p></section>
<section><h2>Interpretation limits</h2><ul>{limits}</ul></section>
<section><h2>Input fingerprints</h2><p>Keep the original benchmark workspace. References and declarations as consumed are also copied into this report directory.</p><div class="scroll"><table><thead><tr><th>Input</th><th>SHA256</th></tr></thead><tbody>{hashes}</tbody></table></div></section>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare honest independent labels and evaluate one fixed WGS84 building area.")
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare", help="Freeze inference inputs and create EMPTY reference/review templates.")
    preparation.add_argument("--predictions", required=True, type=Path)
    preparation.add_argument("--source", required=True, type=Path, help="Source metadata JSON, including name/kind/CRS/bounds/width/height/attribution.")
    preparation.add_argument("--settings", required=True, type=Path, help="Inference JSON including confidence_threshold and minimum_area_sqm; use null for unknown values.")
    preparation.add_argument("--model", required=True, type=Path, help="Model weights to hash; never loaded or copied.")
    preparation.add_argument("--imagery", type=Path, help="Optional exact source PNG/JPEG/GeoTIFF snapshot, up to 64 MiB.")
    preparation.add_argument("--aoi", required=True, nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    preparation.add_argument("--area-name", required=True)
    preparation.add_argument("--output", required=True, type=Path, help="New directory; its parent must exist.")
    scoring = commands.add_parser("evaluate", help="Require independent reviewed labels and report IoU0.50/0.75 agreement.")
    scoring.add_argument("workspace", type=Path)
    scoring.add_argument("--output", required=True, type=Path, help="New report directory; existing inputs/reports are never overwritten.")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.predictions, args.source, args.settings, args.model, aoi=args.aoi, area_name=args.area_name,
                             output=args.output, imagery_path=args.imagery)
            print(f"Prepared {result}\nNOT EVALUATED: references are empty. Follow LABELING_INSTRUCTIONS.md before scoring.")
        else:
            result = evaluate(args.workspace, output=args.output)
            print(f"Reference-agreement report: {result / 'report.html'}\nDeclarations cannot establish label independence or field accuracy.")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Benchmark workflow failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
