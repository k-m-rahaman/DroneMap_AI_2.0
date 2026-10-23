# Repeatable SIH workflow rehearsal

This rehearsal checks the hardware-free backend workflow with the actual local
building model. It is separate from the detection accuracy benchmark. Proposal
counts and confidence are not precision, recall or cadastral boundary accuracy.

Run from the project folder in a Python environment with the installed application,
AI and testing dependencies:

```powershell
python tools/rehearse_sih.py --source-metadata ".demo-runtime/visitor-data/sources/<source-id>.json"
```

The input must be an existing, original-resolution map sample saved by the isolated
public demo. The tool accepts only a sidecar and raster beneath
`.demo-runtime/visitor-data/sources`. It refuses private workspace sources, stale
paths, resampled images and singular georeferences. It never downloads a model,
fetches map tiles or opens the private workspace database. Keep a permitted sample
locally before the presentation; the repository does not redistribute map imagery.

Each run uses a fresh folder inside `.demo-runtime/rehearsals`. Existing runs are
never overwritten or deleted. The sample pixels are packaged as an explicitly
satellite-derived GeoTIFF, retaining their native CRS and affine transform. This
tests the GeoTIFF import pipeline; it is not an actual drone capture or an
orthomosaic reconstruction test. Attribution, original metadata, model checksum,
derived imagery, raw predictions, settings and exports remain in this ignored
folder for inspection. Do not upload that folder or private account data.

The workflow checks:

- Isolated guest entry and GeoTIFF import without changing native pixels.
- Actual CPU inference producing multiple distinct valid building proposals.
- Saving multiple buildings, idempotent retries, geometry edits, version history
  and rejection of stale edits.
- Reconciliation of repeated proposals and illustrative parcel import.
- PDF structure and reopening CSV and GeoJSON exports.
- Coverage planning, QGroundControl plan structure and route containment.
- Blocking physical drone access in a public demo.
- Saved geometry and versions surviving application recreation.

External HTTP, DNS and socket connection attempts fail immediately during the run.
The in-process HTTP client still exercises real application routes, storage,
geospatial processing and inference. The scan request time includes the first
model load. The model pipeline time starts after loading and includes native-window
inference and geometry processing. Total workflow time additionally includes
import, database, exports and all checks. These timings are CPU observations on
this computer, not pure model latency or GPU performance claims.

Only aggregate reports are written to `outputs/SIH/rehearsal-latest.json` and
`outputs/SIH/rehearsal-latest.md`. Their timestamps and model/source hashes support
repeatability, and their limits are explicit. A failed run preserves private
`failure.txt` details and returns a nonzero exit status instead of fabricating a
successful result.

The rehearsal does not assess browser usability, PDF visual layout, field accuracy,
physical drone compatibility, image acquisition or flight safety. Rehearse the
actual browser separately and use independently reviewed labels for accuracy
claims. QGroundControl output is an illustrative plan; no commands are sent to an
aircraft.

Safety checks for the rehearsal utility can be repeated with:

```powershell
python -m pytest tests/test_rehearsal.py -q
```
