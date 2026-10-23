# SIH hardware-free workflow rehearsal

Result: **PASSED**

Workflow rehearsal only; no measured detection accuracy, drone capture, flight or legal boundary validation.

Recorded: 2026-10-23T11:14:36.120005+00:00

## Checks

- PASS — derived geotiff retains native pixels
- PASS — isolated guest entry
- PASS — geotiff import native geometry
- PASS — real offline multi building inference
- PASS — multiple buildings saved and retry is idempotent
- PASS — edit history and stale update protection
- PASS — duplicate proposals reconciled
- PASS — pdf export structural validation
- PASS — geojson csv exports reopen
- PASS — illustrative parcel import preview and save
- PASS — coverage route and qgc plan export
- PASS — physical drone access disabled
- PASS — saved records survive app restart
- PASS — offline external network attempts zero

## Observed results

```json
{
  "building_proposals": 76,
  "distinct_valid_geometries": 76,
  "scan_request_seconds": 11.078,
  "model_pipeline_seconds": 7.08,
  "native_windows": 2,
  "pdf": {
    "pages": 2,
    "bytes": 35791,
    "xref_objects": 37,
    "visual_layout_verified": false
  },
  "planning_waypoints": 20,
  "external_network_attempts": 0,
  "total_seconds": 14.843
}
```

## Limits

- A saved map image was wrapped as a GeoTIFF; no actual drone or new imagery acquisition was used.
- Model proposal counts and confidence are not accuracy measurements; independent reference labels are still required.
- This exercises the backend through an in-process HTTP client; browser usability and PDF appearance require separate checks.
- Raw imagery, polygons, temporary sessions and workspace database remain private and are not redistributed.
