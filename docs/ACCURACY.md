# Measure accuracy before claiming it

No local field benchmark or retraining result is bundled with this application.
Automated tests verify software behavior using synthetic examples; they do not
establish detection accuracy on a neighborhood, drone, season, or camera.

## Free, repeatable building benchmark

1. Obtain imagery that permits your intended use. Record source, acquisition
   date, pixel size, CRS, positioning method, license and area of interest.
   Retain original pixels and georeferencing. Do not mix imagery acquired at
   different dates without documenting changed or demolished buildings.
2. Choose geographically separate training, validation and test neighborhoods.
   Split the parent survey before creating overlapping tiles. Adjacent or
   overlapping imagery of the same roofs must never appear on both sides of a
   split. Keep the test set untouched while selecting models and thresholds.
3. Label every visible building consistently in each evaluation area. Agree on
   roof versus ground footprint, attached houses, courtyards, occlusions and
   minimum visible size. Have a second person review labels. Export independent
   reference polygons as WGS84 longitude/latitude GeoJSON FeatureCollections.
   Do not use the model's unchanged predictions as reference labels.
4. Run inference with recorded model hash/version and fixed settings. Corrected
   predictions used for training must remain outside the held-out test area.
   Export raw predictions and define the same geographic extent for both files.
5. Run with the core Python dependencies installed:

   ```powershell
   python tools/evaluate_buildings.py predictions.geojson reference.geojson --iou-threshold 0.5 --output metrics.json
   ```

6. Inspect unmatched reference indices (missed buildings), unmatched prediction
   indices (false detections/duplicates), and weak matched boundaries. Evaluate
   several thresholds, for example 0.5 and 0.75, and publish all chosen results
   with the number of labeled buildings, area, image resolution and limitations.
   Compare the same held-out data before and after changing the model.

## What the metrics mean

- IoU is polygon intersection area divided by union area in a local metric
  projection. A prediction is a true positive only if it reaches the configured
  threshold and is assigned to a unique reference building.
- Matching maximizes the number of qualifying one-to-one pairs with an indexed
  Hopcroft-Karp algorithm. Candidate adjacency is ordered by decreasing IoU;
  it does not globally maximize summed IoU. One prediction covering two houses
  can match only one; duplicate predictions cannot both match one reference.
- Precision = TP / (TP + FP), recall = TP / (TP + FN), and F1 =
  2 TP / (2 TP + FP + FN). Undefined values are JSON `null`; an empty test set
  never receives an invented perfect score.
- Mean matched IoU describes only matched buildings. Always read it alongside
  recall: missed buildings are not in that mean.
- Boundary Hausdorff distance is reported in metres for matched outlines. It
  describes the supplied polygons; it is sensitive to outliers and vertex
  sampling and does not measure absolute georeferencing accuracy.
- Feature indices in reports are zero-based source-file indices. IDs are
  included where available but are not assumed unique.

Inputs must be valid, nonempty Polygon/MultiPolygon geometries with closed
two-dimensional WGS84 rings. Empty collections are supported. Invalid polygons
are rejected, not silently repaired. One run accepts up to 10,000 features and
500,000 vertices per file, 250,000 intersecting candidate pairs, 64 MiB per CLI
input, and a local extent below 100 km outside the polar regions. Split larger
surveys geographically and sum TP/FP/FN before computing overall metrics.

## Position, parcels and field evidence

Roof detection is not legal parcel reconstruction or ownership verification.
Keep buildings separate from land parcels; reconcile parcels against authorized
records and human review. Report claimant information as unverified unless it
has been checked through the appropriate process.

Assess absolute position using independent surveyed checkpoints and a documented
coordinate reference system. Report position errors separately from outline IoU.
Do not substitute phone coordinates or map-tile alignment for survey evidence.
When hardware becomes available, record the actual aircraft, camera, altitude,
overlap, processing settings and control/checkpoint method. Repeat the same
workflow with real captures; simulator packets only verify integration behavior.

## Telemetry freshness

An aircraft heartbeat keeps the connection alive but does not refresh GPS,
position, altitude, heading, speed or battery measurements. The status API
returns per-field age/UTC observation time in `telemetry_freshness` and nulls
measurements older than five seconds (battery: fifteen seconds). GPS fix loss
immediately clears the displayed position. A valid `GLOBAL_POSITION_INT` is
preferred; fresh valid `GPS_RAW_INT` supplies fallback position after the global
position expires. Unknown battery reports clear the prior percentage immediately.
These are display freshness thresholds, not an aircraft health assessment.
