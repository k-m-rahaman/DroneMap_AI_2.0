# DroneMap AI · GeoVision

**SIH26012 · AI-Based Automated Urban Parcel Mapping and Cadastral Feature Extraction System using Drone Imagery**

<!-- LIVE_DEMO_LINK -->
**[Open the live SIH demo](https://motorcycles-badly-estates-usr.trycloudflare.com)** · [Project page](https://k-m-rahaman.github.io/DroneMap_AI_2.0/)

The live app is a temporary, free laptop-hosted demonstration. Keep the demo process and internet connection running; restarting creates a new address. Use sample data only. The downloadable source and project page remain available when the live session ends.

DroneMap AI is GeoVision's building-survey and parcel-review workspace. It detects individual roof footprints from satellite imagery or georeferenced orthomosaics, lets a surveyor correct and review boundaries, and produces versioned survey records and GIS exports. A dedicated drone section reads compatible MAVLink telemetry and prepares a coverage route for review in a ground station.

**Prototype scope:** detected roofs are building features, not automatically verified ownership parcels. The supplied model has not been retrained in this release, and no independently measured local accuracy is claimed. Physical drone capture still requires hardware integration and a field test. The team's problem title is supplied context; full official requirements must be checked separately.

| Start here | Purpose |
| --- | --- |
| [Five-minute SIH walkthrough](docs/SIH_DEMO.md) | Rehearsal, success criteria, truthful capability limits and offline fallback |
| [Installation and public demo](docs/DEPLOYMENT.md) | Portable setup, separate guest demo and temporary HTTPS link |
| [Accuracy evaluation](docs/ACCURACY.md) | Independent labels, geographic splits and reproducible metrics |

| Available workflow | Current boundary |
| --- | --- |
| Multiple-building segmentation with confidence, native image windows and spatial duplicate checks | Needs representative labeled imagery to establish accuracy |
| Manual boundary editing, parcel GeoJSON imports and overlap review | Does not infer legal ownership from roof appearance |
| Recoverable drafts, saved versions, PDF/CSV/GeoJSON and private backup/restore | Single-owner private workspaces; team invitations and independent approval roles are not implemented |
| MAVLink UDP/TCP/serial telemetry and QGroundControl route export | Read-only adapter; no arming, camera capture, mission upload or actual flight execution |
| Opt-in public guest demonstration with isolated disposable workspaces | Temporary laptop-hosted link; no permanent hosted-service or retention guarantee |

## Start the application

Version **4.2.0** adds an opt-in public demonstration to the native-image survey workspace. It includes recoverable drafts, spatial duplicate checks, parcel-reference imports, overlap review and a repeatable accuracy evaluator. Query `/api/status` for the running release version.

On Windows, install 64-bit Python 3.10–3.12, then double-click `Run_DroneMap_AI.bat` or `start-with-ai.bat` for the complete AI and telemetry installation. The launcher prepares this project's `.venv/` and reuses it on later starts. Open **http://127.0.0.1:8000/** and keep the terminal open. Stop an older DroneMap server before starting another on port 8000.

On first use, enter your email and choose a password of **at least 12 characters**. The sign-in flow asks for your name and creates the first workspace account. Later, use that email/password. There is no default password; the old `1234` demo PIN does not grant access. Existing accounts, projects and records continue to use the same local `data/` folder after this upgrade.

Choose a launcher for the capabilities you need:

| Launcher | Installed capabilities |
| --- | --- |
| `start-with-ai.bat` | Complete workspace, CPU building detection and MAVLink telemetry |
| `start-with-drone.bat` | Manual survey, imagery import and MAVLink telemetry |
| `start.bat` | Core workspace and manual survey |

The portable launchers create `.venv/` and install dependencies on first setup. Subsequent starts validate the installed dependencies and skip installation when requirements are unchanged, so restarting a prepared environment does not need pip/network access. To choose a Python installation explicitly, run `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Python C:\Path\To\python.exe -InstallAI -InstallDrone`. An unrelated existing `venv` may use Python 3.14; the pinned portable installation uses Python 3.10–3.12. Do not open `index.html` directly or through VS Code Live Server: the page and authenticated API must use the same origin.

For Linux/macOS or manual setup, see [DEPLOYMENT.md](docs/DEPLOYMENT.md). After installing requirements in an activated virtual environment, the normal launch command is `python -m uvicorn main:app --host 127.0.0.1 --port 8000`. Keep private data out of source control.

## Show a public SIH demo

On Windows, double-click **`Run_SIH_Demo.bat`**. It prepares the AI environment, starts an isolated demo on port 8003, and opens an accountless Cloudflare Quick Tunnel. The first download of the official `cloudflared` executable is checked against the release SHA256 digest. Share the printed HTTPS address; its current value is also stored locally in `.demo-runtime/public-url.txt`. Internet is needed for first setup and the tunnel. Use **Ctrl+C** to stop the demo and its owned processes.

GitHub contains the source; it does not run this Python/Torch backend. Guests receive isolated demonstration workspaces, and physical telemetry connection/port access is disabled. Public-demo PDFs omit public verification QR links. PDFs and exports still contain the visitor's supplied sample fields; they do not anonymize the input. Never tunnel the real private workspace or use confidential data in the demonstration.

Follow [the public-demo instructions](docs/DEPLOYMENT.md#temporary-public-demonstration). The URL works only while the demo server, tunnel, laptop and internet remain available. Restarting the tunnel changes the URL. The full private app stays available locally with existing accounts and surveys.

## Map and review a survey

1. Sign in and choose a project in the header. Create a project for a new ward, village or survey area, or edit the selected project's name, location and description.
2. Use **Map** to navigate to the area. Draw a rectangle to select the scan area, or scan the current visible neighborhood. Set the confidence and minimum building area before running detection.
3. The server retrieves real georeferenced Esri imagery and analyzes overlapping tiles. Each detected building has its own boundary, confidence and available edge-quality flags. The default confidence is 0.50 and minimum area is 4 m².
4. Review each outline against the image. Edit vertices, add a name and notes, then save the feature. Draw a polygon for a manually mapped parcel. Roof outlines are building features; they do not establish ownership boundaries.
5. Use **Registry** to search/filter saved records, reopen their geometry, inspect version history, download a survey PDF, or export GeoJSON/CSV with the visible export controls. Editing geometry recalculates WGS84 geodesic area and perimeter on the server.

Saved records survive restarts in SQLite. Updates retain the original geometry and the responsible user's name in version history. Retried initial saves use an idempotency key. AI suggestions remain drafts until a surveyor reviews them; the reviewed status records that action rather than government approval.

Unsaved boundaries and field edits are recovered per signed-in user/project from this browser for up to seven days (3 MiB total and 1,000 boundaries per snapshot). Recovery retains the original retry key/payload after an ambiguous save. When browser storage is denied/full, warnings explain that only the current page can preserve recovery; keep that page open and save to the server. Oversize work is suspended in memory across reauthentication and closing the page warns before discarding it. A browser recovery copy is not a server backup.

Map scans pass proposals through spatial reconciliation against saved and current edited/draft buildings. Strongly matching roofs from different scans are skipped; adjacent roofs and uncertain overlaps remain for review. In **Registry**, use **Check overlaps** for same-layer duplicate/overlap warnings on saved records. Building-within-parcel relationships are intentional and are not flagged as same-layer errors. Gap detection requires a known coverage boundary and is not inferred from empty space.

Use **Registry → Import parcels** to preview a WGS84 GeoJSON FeatureCollection (1–1,000 features, at most 8 MiB). The app recalculates measurements, retains a reference/parcel ID, and opens every imported boundary as an unsaved parcel draft. Legacy projected GeoJSON must be reprojected first. Imported labels/claimants are supplied evidence, not verified ownership; check the source before saving/reviewing.

## Import drone imagery

In **Sources**, choose or drop a georeferenced GeoTIFF orthomosaic, inspect its source metadata/preview, then run detection on that source. New GeoTIFF imports retain original bytes, native CRS, affine transform and dimensions. Detection reads original-resolution windows; a separate reduced Web Mercator preview serves the map. Declared RGB band ordering, grayscale, alpha/nodata and nonfinite pixels are checked. Masks touching invalid pixels are discarded for review rather than treated as valid roofs. PNG/JPEG imports retain original dimensions after EXIF orientation.

The upload limit remains 64 MiB and 100 million source pixels. A scan processes at most 256 native windows and returns at most 1,000 proposals; draw a smaller rectangle within an imported source when needed. Selected AOIs limit source analysis and their clipped outlines carry a review flag. No silent full-image downsampling occurs. Sources saved by earlier versions remain compatible at their historical analysis resolution; reimport the original to benefit from native detail. Local source inference can run without internet after installation; satellite fetching still needs internet, and map backgrounds may be unavailable offline.

For a north-up JPEG/PNG, enter its known WGS84 bounds in the displayed extent fields: west, south, east, north. The upload limit is 64 MB. A raw oblique photo or an unreferenced screenshot has no reliable ground transform. Process overlapping drone photographs into an orthorectified/georeferenced orthomosaic in your photogrammetry workflow before importing it here. This application does not stitch or orthorectify raw photographs.

## Connect a real drone

The **Drone** section supports **MAVLink telemetry** from a compatible vehicle/controller, using `pymavlink`. It reads received vehicle data; a connection is only marked connected after vehicle heartbeat data arrives. Battery, GPS, altitude and other fields stay empty when no such messages have been received. This is a real connection adapter, not simulated telemetry, and it does not provide a generic connection to every consumer drone. A proprietary controller that does not expose MAVLink needs its own supported SDK/bridge.

The connection is made by the **computer running the Python server**. USB/serial devices must be connected to that computer, and network telemetry must reach that computer. Browser access from a different computer does not expose that browser computer's USB devices to the server.

1. Install drone dependencies using `start-with-drone.bat` or the full `start-with-ai.bat` launcher. If adding them to an existing environment manually, install `requirements-drone.txt` with that environment's Python.
2. In **Drone**, choose a connection:
   - **UDP listener:** the default is `127.0.0.1:14551`. Configure your MAVLink source/bridge to forward telemetry to that host and port. A listener waits for incoming messages; entering a port does not cause the drone to start transmitting. Use a separate destination port from an existing ground-control listener.
   - **TCP endpoint:** enter the reachable loopback/private IPv4 address and port of your MAVLink TCP server. Network connections accept explicit IPv4 addresses and ports 1024–65535.
   - **Serial / USB radio:** refresh available ports, choose the actual COM device and set the baud rate to match the radio/controller configuration. Another program may already own the serial port; use telemetry forwarding if both applications need the same link.
3. Select **Connect vehicle**. Check the connection message and received telemetry. A waiting, unavailable or timed-out link is displayed as such, never as a successful scan.
4. Select **Disconnect** to release the link. Signing out also releases your connection. The backend restricts connection operations to the authenticated account that owns the link.

The adapter is read-only: the application does not arm, launch, upload a mission, trigger the camera or steer the vehicle. Hardware/controller selection and an actual field test are still required to verify the complete capture workflow. The connection model follows the [official MAVLink Python guide](https://mavlink.io/en/mavgen_python/) and [heartbeat connection protocol](https://mavlink.io/en/services/heartbeat.html).

## Prepare a drone survey route

1. Select or draw a rectangular/convex survey area on the map and open **Drone**.
2. Enter altitude, speed, front/side overlap and the **actual camera specifications**. The initial camera values are examples, not a detected camera profile. The planner currently accepts altitude 10–120 m and speed 0.5–15 m/s as application input limits.
3. Select **Preview coverage route**. The map shows the coverage path, with estimated distance, flight time, image count, nominal ground sampling distance and lane/camera spacing. The planner currently rejects non-convex polygons, holes and unsupported geometries rather than connecting survey legs through excluded areas.
4. Download the **QGroundControl `.plan`** file and open/review it in your ground-control workflow. The file follows the [QGroundControl Plan format](https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/file_formats/plan.html). Configure the vehicle, camera actions and complete flight sequence there before a pilot executes the survey. Previewing/downloading a plan does not transmit it to a vehicle or start a flight.
5. Process the captured imagery into a georeferenced orthomosaic, import it in **Sources**, then detect and review the buildings.

Route estimates assume a flat ground surface and the supplied camera settings. They do not model terrain, obstacles, turn dynamics, battery reserve or the full takeoff/landing flight. Nominal GSD is a planning estimate, not measured map accuracy. The route preview states these limitations alongside its results.

## Accuracy, reports and storage

`best.pt` is the existing Building-only YOLO segmentation checkpoint. Inference runs on CPU and returns confidence and edge-quality flags. Geometry placement, native image detail, duplicate suppression and failure handling are improved; this upgrade does not retrain the model or establish a measured accuracy. Follow [docs/ACCURACY.md](docs/ACCURACY.md) to build an independent benchmark and run `python tools/evaluate_buildings.py predictions.geojson reference.geojson --output metrics.json`. It reports one-to-one TP/FP/FN, precision, recall, F1, matched IoU and boundary distance without inventing scores for missing reference data. Keep geographically separate training/test surveys.

A scan with no detections remains empty. Missing weights, offline imagery and inference failures produce explicit errors. No artificial squares or simulated detections are substituted. PDFs are survey evidence with a signed snapshot-verification link, rather than title deeds or government Form 7 certificates. QR links share their report snapshot with anyone possessing the link.

Local records and imagery live in `data/`, excluded from source control. Back up that entire folder before replacing an installation; the database, source files and report signing key belong together. No records from the original in-memory server can be recovered after that server exits unless they were exported separately. Upgrading source files must not replace or remove your existing `data/` folder.

The default launcher binds to `127.0.0.1`. For a hosted team deployment, configure HTTPS, backups and `DRONEMAP_PUBLIC_BASE_URL` so QR links resolve on other devices. `DRONEMAP_DATA_DIR` can point to persistent storage. Credentials, private source images, model runtime output and survey records must not be committed.

## Private backup and restore

With Python available, run `python tools/backup_workspace.py backup --data-dir data --output PATH\survey-backup.zip`. Verify it with `python tools/backup_workspace.py verify --archive PATH\survey-backup.zip`. Restore to a new/empty folder with `python tools/backup_workspace.py restore --archive PATH\survey-backup.zip --data-dir restored-data`. Stop the app before changing its configured data folder. The tool makes a consistent SQLite snapshot, includes referenced imagery and report verification data, checks hashes, relocates image paths, and refuses to overwrite an existing populated folder. These archives contain private accounts/survey data and are not encrypted; keep them out of public source packages. Source ZIPs exclude `data/`.

## Verification

Install `requirements-dev.txt` and run `python -m pytest tests -q`, then `node --test tests/*.test.cjs` (Node 22 in CI). GitHub Actions runs these checks using core and MAVLink dependencies without downloading Torch. The suite checks authentication, project ownership, persistent edits/history, geometry calculations, exports, backup integrity, telemetry handling, mission validation and browser state/recovery. Test inference fixtures and loopback transports are explicitly isolated examples, not evidence of detection accuracy or physical drone connectivity.

Before an SIH presentation, run actual inference on the permitted demonstration imagery and complete the [rehearsal checklist](docs/SIH_DEMO.md#rehearsal-acceptance-checklist). Test counts and model confidence must not be presented as accuracy scores. Report a measured local score only alongside independent reference labels and the evaluation procedure.

## Project and contribution notes

Team **GeoVision · 171712**. Report reproducible software issues through GitHub using the bug-report template. Share only permitted, anonymized reproduction data; never attach the private `data/` folder or a backup archive.

This repository includes third-party dependencies and an existing model checkpoint. Their respective licenses and imagery-provider terms still apply. The repository does not establish permission to reuse a particular map image or to make legal cadastral claims.
