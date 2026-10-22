# SIH demonstration: one survey, end to end

**Problem:** SIH26012 — AI-Based Automated Urban Parcel Mapping and Cadastral
Feature Extraction System using Drone Imagery. **Team:** GeoVision, 171712.
The title above comes from the team's supplied brief. Check the full official
problem statement separately before asserting that every deliverable is covered.

This is a building-survey and parcel-review prototype. Roof segmentation is not
legal ownership determination. A reviewed record means a user reviewed it in
the application; it does not mean approval by a government authority.

## Prepare before the judges arrive

- Use the complete AI installation and confirm that model readiness is available.
  Run it once beforehand so first-time model loading is outside the presentation.
- Use a small area with several clearly visible roofs. Prefer an original
  georeferenced orthomosaic that you may display and redistribute. Record its
  source, date, CRS, pixel size and permission. Map imagery requires internet;
  lower-resolution imagery may not show small roofs clearly.
- Keep an authorized local image for the offline workflow. A screenshot without
  reliable ground bounds is not an accuracy reference or a drone capture.
- Keep the independent reference-label file and the resulting evaluation JSON
  if available. Without them, explicitly say local accuracy is unmeasured.
- Start from a new demonstration project with no personal or claimant data.
  Keep the private survey server separate from any public demo server.
- Confirm the local page works, downloads open, the temporary public URL works
  from another device, and the laptop will stay awake and connected.

## Five-minute walkthrough

Times are presentation allocations, not promises about inference speed. Rehearse
on the actual laptop; CPU detection and network image retrieval can take longer.

| Time | Action | Success criterion / explanation |
| --- | --- | --- |
| 0:00–0:40 | Explain the problem; open a new survey project and its image. | Source provenance is visible. Identify whether it is satellite imagery, a real orthomosaic or a synthetic fixture. |
| 0:40–1:50 | Draw a small scan rectangle and select **Detect buildings**. | Multiple independent outlines appear if the model finds roofs. Confidence is a model score, not measured accuracy. A legitimate empty result stays empty. |
| 1:50–2:50 | Inspect two roofs; edit one boundary; save a draft, then a reviewed record. | Area/perimeter update from geographic geometry. The registry shows saved status and history. A visible roof error is corrected openly. |
| 2:50–3:30 | Scan the same area again; open **Registry → Check overlaps**. | Strong matches are suppressed. Uncertain or partially overlapping roofs remain reviewable; suppression is not a guarantee of zero duplicates. |
| 3:30–4:10 | Show a parcel reference or manually draw a parcel. Download a PDF and GeoJSON/CSV. | Buildings and parcels are distinct layers. The PDF is a versioned survey record, not a title deed. Public-demo PDFs omit public verification links; supplied sample fields remain in PDFs and exports. Exported geometry reopens in a GIS. |
| 4:10–5:00 | Draw a rectangular AOI; open **Drone**, preview coverage and export a `.plan`. | The path and estimates render. Explain read-only MAVLink integration, missing physical field validation and the need for pilot review. |

In the public demo, use its guest entry and harmless sample information only.
Actual drone connection/port access is disabled there. On the private local app,
show a disconnected state unless real MAVLink telemetry is arriving. If showing
ArduPilot SITL or test packets, put **SIMULATED TELEMETRY** in the demonstration
description and say what the simulator verifies. Never present software packets
as evidence that the team flew a real drone.

## What to say about accuracy

Use a measured statement only when you can produce independent reference labels,
the held-out geography, imagery resolution, model hash and evaluation settings.
The evaluator is described in [ACCURACY.md](ACCURACY.md). It measures one-to-one
building precision/recall/F1, polygon IoU and matched boundary distances. Software
test counts are not accuracy scores. The supplied `best.pt` checkpoint has not
been retrained by this release.

New imports preserve native pixels for inference. A source imported by an old
release may retain its old reduced analysis resolution; reimport the original
file. Nominal route GSD is a planning estimate and does not measure the positional
accuracy of the resulting map.

## Honest capability boundaries

- Real workflows: georeferenced image import, CPU segmentation with the supplied
  model, manual corrections, spatial reconciliation, records/history, exports,
  PDF snapshots, read-only telemetry adapter and convex-AOI route calculation.
- Not completed: independently verified local model accuracy, automatic legal
  parcel/ownership reconstruction, actual drone capture, raw-photo stitching,
  field positioning checks and a tested production multi-user deployment.
- The planner rejects concave AOIs, holes and disconnected areas. It assumes flat
  ground and omits terrain, obstacles, exclusion-area routing, turn dynamics and
  a complete camera/takeoff/landing sequence. A downloaded route is not sent to a
  vehicle. A pilot must configure and review it in a compatible ground station.

## Rehearsal acceptance checklist

- [ ] No private account, owner, notes, imagery or backup appears in the demo.
- [ ] New project → image/AOI → actual inference completes on the chosen sample.
- [ ] At least two detected buildings are individually selectable; errors are reviewed.
- [ ] Saved edits survive refresh; version history and exports open correctly.
- [ ] An interrupted login/refresh recovers harmless unsaved test work.
- [ ] The repeated-scan and overlap-review behavior is demonstrated without invented counts.
- [ ] Drone state, route assumptions and simulated versus real evidence are explicit.
- [ ] Local fallback works if the public tunnel or map provider is unavailable.

## If something fails live

| Symptom | Recovery |
| --- | --- |
| AI unavailable | Check `best.pt` and the AI environment; use the complete launcher. Explain the unavailable state; do not replace inference with staged polygons labeled as AI. |
| No buildings found | Check image quality, scan extent, minimum area and confidence. Compare against visible roofs. An empty result is evidence about that input/settings, not a reason to invent geometry. |
| Image too large / scan-window limit | Select a smaller AOI in the imported source. The app avoids silently shrinking the complete image. |
| Background missing offline | Use the prepared local georeferenced source and its image overlay. Local inference works after dependencies are installed; online base tiles do not. |
| Drone waiting or timed out | Confirm source forwarding/port only in the private local installation. With no hardware, demonstrate route planning and explain the missing field test. |
| Public link fails | Verify the demo server and tunnel are both running. Use the local browser immediately. Restarting a Quick Tunnel generates a different URL. |

Keep a short screen recording of a genuine completed run for presentation
continuity, labeled as a recording. It is not a substitute for reporting the
current live result or for independent accuracy evidence.
