# Use DroneMap AI for SIH

Open **[DroneMap AI](https://k-m-rahaman.github.io/DroneMap_AI_2.0/)** in a current
desktop browser and select **Open mapping workspace**. This edition runs the
existing Building segmentation model on your device using ONNX Runtime Web.
It needs no account, password, Python installation, Firebase project or laptop
tunnel. The public address opens the application itself.

## Map, detect and review

1. Create or select a project for the area being shown.
2. Navigate to a neighborhood on the map and draw a scan rectangle. Start with
   one small block. Map scans support at most 500 m per side and a 2,048-pixel
   export edge; a smaller selection may be needed at the source resolution.
3. Set confidence/minimum building area and run detection. The first scan
   downloads the actual model/runtime. Processing speed depends on the device.
4. Inspect the individual roof proposals against the image. Edit vertices,
   remove false positives, and manually draw missing buildings or parcels.
   Add a name, notes and any supplied reference information before saving.
5. Open **Registry**, inspect saved versions, use **Check overlaps**, and export
   GeoJSON/CSV. Open a saved feature and download its **Survey PDF**.
6. Reload and reopen the workspace once. Confirm the saved records remain in
   the same browser, then export copies before the presentation ends.

Proposals come from segmentation masks. A model confidence value is not an
accuracy measurement. Buildings and roof outlines do not establish legal land
ownership; manual parcel references also need their own supporting evidence.
Marking a record reviewed records a browser-workspace action, not approval by
a separate survey authority. Empty scans stay empty; acquisition/model errors
are shown rather than replaced by invented buildings.

## Save and recover work

Projects, original imported images, previews, saved geometries and version
history use IndexedDB **in this browser profile on this device**. Other devices
and browsers open their own workspace. There is no cloud login or sync. Closing
the workspace does not delete its saved data.

Use a normal browser profile with storage enabled. Private browsing, clearing
site data, storage eviction, a different origin or loss of the device can remove
access. A persistent-storage request helps only if the browser grants it; it
does not guarantee retention. If saving fails or storage is full, retain the
open page and export the saved records before changing browser data.

Keep GeoJSON/CSV exports, PDFs and original imagery in a separate folder. A GIS
export preserves feature records, not all project metadata, original rasters or
edit history. Reimporting GeoJSON opens reference drafts that need review; it
does not restore a complete browser database. Full browser-workspace
backup/restore and multi-user access are not implemented.

Browser PDFs contain measurements, a boundary diagram and available source and
version information. They are unsigned local survey records without a public
verification QR, a title deed or government certification.

## Import imagery without a drone

In **Sources**, select or drop a permitted image and inspect its preview and
metadata before scanning.

| Input | Requirements |
| --- | --- |
| GeoTIFF | Unsigned 8-bit RGB; EPSG:4326, EPSG:3857 or WGS84 UTM; valid georeferencing |
| PNG/JPEG | Known WGS84 west/south/east/north bounds for a rectified image |
| Size | At most 32 MiB, 16 million pixels and 16 native inference windows |

Unsupported CRS/radiometry and files needing more windows are rejected. Crop
or convert them with GIS software first; detection does not silently reduce
the original image resolution. The map preview may be resampled for display.
A GeoTIFF's extension does not prove it came from a drone or has verified
ground accuracy. User-supplied bounds are only as reliable as their source.

For future drone surveys, first process overlapping photos into a
georeferenced, orthorectified RGB orthomosaic, then import that result. This app
does not stitch raw photographs, correct an oblique photo or infer a reliable
ground transform from an unreferenced screenshot.

## Use the drone section honestly

Draw a rectangular or convex survey area and open **Drone**. Enter the intended
altitude, speed, overlap and actual camera specifications. Preview the coverage
route and download its QGroundControl `.plan` for review in a ground station.
The example camera values are not a detected camera profile.

The browser edition does not connect to aircraft telemetry, arm, launch, upload
missions or trigger a camera. The separate [Python installation](DEPLOYMENT.md)
has a read-only adapter for compatible MAVLink UDP/TCP/serial telemetry; it
still needs the selected hardware/controller and a field test. No generic
connection to every consumer drone is claimed.

The route is a planning preview with a PX4 multirotor template. A pilot must
select the actual vehicle/firmware, set home elevation, configure triggering
and complete the takeoff/return/landing workflow in a compatible ground
station. Estimates assume level ground and a nadir camera; they omit terrain,
obstacles, wind, turn dynamics and battery reserve. A route download performs
no flight or capture.

## Prepare the SIH presentation

- Use permitted, non-confidential imagery with several visible roofs. Retain
  the imagery source/date where available and label map-derived samples as
  map imagery.
- On the presenting computer, load the app and finish one actual scan before
  judges arrive. Confirm map access and allow the initial model download.
- Show detection, a visibly corrected boundary, saved measurements/history,
  an export, a page reload and the drone route preview.
- Keep exported records, original imagery and a recorded walkthrough as a
  fallback. Browser caching is not a guaranteed offline install; the app needs
  to load, and online imagery/search still require network access.
- Explain remaining work: independent local accuracy evaluation, authoritative
  parcel references and real drone field validation. Test counts, a successful
  scan and model confidence are not substitutes for that evidence.

See [ACCURACY.md](ACCURACY.md) and [BENCHMARK_WORKFLOW.md](BENCHMARK_WORKFLOW.md)
for independent labeling and evaluation. Keep geographic training/test areas
separate; do not score a model against its own predictions.
