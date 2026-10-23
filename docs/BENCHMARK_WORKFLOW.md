# Independent building reference benchmark

The application can propose many building outlines. This workflow measures how
those outlines agree with **independently drawn and reviewed references** in one
fixed area. It does not train the model, invent reference labels, certify land
ownership, or establish field survey accuracy.

`tools/benchmark_workspace.py prepare` freezes existing predictions, acquisition
metadata, inference settings and an explicit WGS84 area of interest (AOI). It
creates **empty** references and incomplete review declarations. `evaluate`
refuses to score that unfinished package.

## 1. Choose and retain evidence

Use several separately reported areas with a documented selection process:
for example, detached roofs, dense connected roofs and an area with vegetation
or shadows. Do not select only attractive results. Keep any area used to tune
settings separate from the final evaluation areas. Record whether training
overlap is known; do not assume it is absent.

For each area, retain:

- Raw, unedited inference proposals as a WGS84 `FeatureCollection` with one
  Polygon/MultiPolygon per predicted building. Do not use only the proposals a
  user accepted or saved; that would discard false positives.
- The exact source imagery, its attribution and acquisition date when known.
  A preview is not a substitute for original pixels. Do not copy imagery unless
  its terms permit that use.
- Source metadata in `source.json` (the required example fields are below), the
  actual settings retained from inference and the exact model weights used.
- Explicit AOI bounds in longitude/latitude order: west, south, east, north.

Required source metadata example; replace every value with your actual source:

```json
{
  "name": "Actual source identifier",
  "kind": "geotiff",
  "crs": "EPSG:32645",
  "bounds": [88.4148, 22.5762, 88.4172, 22.5778],
  "width": 896,
  "height": 647,
  "attribution": "Actual imagery provider and permitted usage",
  "acquired_at": null,
  "native_resolution_m": null,
  "acquisition_notes": "Unknown fields remain unknown"
}
```

`bounds` is always WGS84, even if the raster's `crs` is a projected native CRS.
Bounds must contain the entire AOI. The tool records metadata as supplied; it
does not inspect the raster to verify those acquisition claims or bounds.

Required inference settings example:

```json
{
  "confidence_threshold": null,
  "minimum_area_sqm": null,
  "tile_size_px": null,
  "tile_overlap": null,
  "settings_notes": "Fill actual retained settings; null means unknown"
}
```

Use actual numeric values if retained. `confidence_threshold` must be between
0 and 1, and `minimum_area_sqm` must be nonnegative. Both keys must exist; use
`null` for an unknown value. Other keys are preserved. Never fill in a guessed
default, model training history or acquisition date to make a report complete.

## 2. Prepare a new workspace

Run from the project directory with the Python environment used for the app:

```powershell
python tools/benchmark_workspace.py prepare `
  --predictions data/benchmark-inputs/raw-predictions.geojson `
  --source data/benchmark-inputs/source.json `
  --settings data/benchmark-inputs/settings.json `
  --model best.pt `
  --aoi 88.4148 22.5762 88.4172 22.5778 `
  --area-name "Area 01 - independent review pending" `
  --output data/benchmark-area-01
```

The coordinates above are an example, not a completed benchmark or recommended
sample selection. The output directory must **not exist**; its parent must
exist. Optionally add `--imagery path/to/exact-source.tif` to copy and hash an
unchanged PNG, JPEG or GeoTIFF up to 64 MiB. The tool does not reproject,
reencode, validate or display that optional raster. It hashes the supplied model
file (up to 2 GiB), without loading or copying it.

JSON/GeoJSON and optional imagery inputs are limited to 64 MiB per file. The
evaluator accepts local areas below 100 km extent and outside polar regions,
up to 10,000 features and 500,000 vertices per set, and 250,000 intersecting
candidate pairs. Keep real benchmarks much smaller for practical manual review.
Antimeridian-spanning and invalid/unclosed geometry are rejected.

## 3. Draw labels without seeing the predictions

Give a labeler the source imagery, `source.json`, `aoi.geojson` and the generated
`LABELING_INSTRUCTIONS.md`. **Hide all model predictions and overlays.** Do not
copy, accept, trace or edit model proposals into the reference layer.

Use QGIS or another GIS editor to draw one independent polygon for each visible
building instance. Keep separate buildings separate, including those the model
missed. Export WGS84 longitude/latitude GeoJSON to `references.geojson`.

Use one boundary policy for both layers: clip buildings crossing the AOI border
to that border. The tool rejects features outside the AOI rather than clipping
them silently. If raw proposals cross the border, establish the fixed boundary
policy and produce an explicitly clipped prediction snapshot before preparation;
retain the unmodified raw proposals too. Do not remove difficult buildings.
When any part of the area cannot be interpreted exhaustively, choose a smaller
fully interpretable AOI and prepare a new workspace instead of omitting regions.

A different reviewer must inspect all reference buildings and the complete
imagery area, searching for omitted buildings and boundary errors independently
of the predictions. Keep label creation history and review evidence.

Fill in `review.json` with the actual labeler and reviewer, timezone-qualified
ISO 8601 timestamps, manual labeling method and exact imagery observed. Set the
four declarations to literal JSON `true` only after the declared work is done.
The reviewer must differ from the labeler, and review must follow label completion.
The tool cannot prove that identities, independence or complete coverage are true.

## 4. Score into a separate new report directory

```powershell
python tools/benchmark_workspace.py evaluate data/benchmark-area-01 `
  --output data/benchmark-report-01
```

Frozen predictions, settings, source, AOI and optional imagery must still match
their manifest SHA256 and byte counts. The AOI file must also match the explicit
manifest bounds. Keep the original workspace and its manifest: these are
consistency fingerprints, not cryptographic signatures or tamper-proof evidence.
The manifest and inputs can be rewritten together by a person with write access.
To change settings or inputs, prepare a new workspace instead of rewriting evidence.

Missing/empty labels, incomplete review, byte-identical copied prediction files,
bad JSON, path traversal, symlink/junction ancestors and inconsistent fingerprints
are rejected. A syntactically different copy of predictions cannot be detected
reliably, which is why independent labeling/review evidence is still essential.
Existing output directories are never intentionally replaced. All report files
are staged before the completed directory is published; validation/write failures
leave no partly valid report directory during normal operation.

The output contains:

- `report.html`: readable summary with source, review, model/input fingerprints
  and explicit interpretation limits. Open in a browser or print to PDF.
- `report.json`: full metrics, settings, one-to-one matches, matched boundary
  distances and unmatched indices at **IoU 0.50 and IoU 0.75**.
- `evaluated-references.geojson`, `review.json` and `manifest.json`: the exact
  label/review snapshot and original manifest consumed for that report.

## 5. Explain the result honestly

Precision asks how many proposals matched references. Recall asks how many
reference buildings were found. F1 combines those two values. IoU measures
footprint overlap. Each prediction/reference can match at most once, so duplicate
proposals count as false positives. Matching maximizes eligible pair count using
IoU-prioritized adjacency; it does not maximize total IoU. Mean matched IoU and
boundary distance exclude unmatched buildings and must be read alongside counts,
precision and recall. Undefined values are `null`, not perfect scores.

Report each area's selection reason, counts, settings and both thresholds.
Keep any tuning results separate from untouched evaluation areas. Several
convenience samples are still not a representative geographic benchmark. A roof
outline is not necessarily a legal parcel boundary; this workflow establishes
neither ownership nor positional accuracy from ground checkpoints.

The tests in `tests/test_benchmark_workspace.py` use invented geometry and mock
review declarations. Passing those tests checks the workflow only. **They are
not model accuracy results and must never be presented as SIH field validation.**
