# Run locally and share a temporary demonstration

The full application requires a running Python process, model runtime and
workspace storage. GitHub stores the source. GitHub Pages can host static pages;
it cannot execute this FastAPI/Torch application.

No permanent public hosting service is bundled. The zero-additional-service-fee
demonstration uses the team's existing computer and a temporary HTTPS tunnel.
Its availability depends on that computer, internet connection and tunnel.

## Public project page

`docs/index.html` is a static introduction with source, download, walkthrough
and accuracy-evaluation links. To serve it with GitHub Pages, choose **Deploy
from a branch**, **main** and **/docs** in the repository's Pages settings.
This publishes the project page; the Python backend still needs the separate
local or temporary-demo process described below.

After verifying a new temporary HTTPS session, update the `data-live-demo-url`
attribute on the page's `live-demo` link and the current demo link in README.md.
The button stays in its availability state while that attribute is empty.
Remove an expired session URL after the demonstration; a static page cannot
guarantee that a tunnel is still running. The source download remains useful
when no live session is available.

## Private installation

Use 64-bit Python 3.10–3.12 (3.12 recommended). On Windows, run
`start-with-ai.bat` for all capabilities, `start-with-drone.bat` for core plus
telemetry, or `start.bat` for the core workspace. The first setup installs
packages into `.venv/`; later starts reuse it.

For a manual installation in a terminal:

```sh
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements-ai.txt -r requirements-drone.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Keep the process running and visit `http://127.0.0.1:8000/`. Set up the first
account with a password of at least 12 characters. There is no shared default
password. Existing installations must keep their original `data/` folder.

For core/manual operation, install `requirements.txt` instead. Core tests
deliberately do not depend on inference, but actual detection requires the AI
requirements and the supplied `best.pt` checkpoint.

## Temporary public demonstration

### Windows: prepared launcher

Double-click **`Run_SIH_Demo.bat`**, or run
`powershell -NoProfile -ExecutionPolicy Bypass -File .\start-public-demo.ps1`.
The launcher:

1. Prepares core and AI dependencies in `.venv/` through `start.ps1 -PrepareOnly`.
2. Downloads the official Windows `cloudflared` executable if absent and checks
   its SHA256 against the official release digest before first use. The cached
   executable lives in the ignored `.tools/` folder.
3. Starts a dedicated backend on port 8003, using the ignored
   `.demo-runtime/visitor-data/` folder. It refuses to open a tunnel unless the
   backend explicitly reports `public_demo: true`.
4. Prints the public HTTPS address and writes its current value to the ignored
   `.demo-runtime/public-url.txt`. Keep the console open and the laptop awake.

The first setup needs internet access for Python packages and the tunnel
executable. A prepared environment reuses those dependencies. An existing
process on port 8003 causes an explicit error; stop the previous demo or choose
another port with `start-public-demo.ps1 -Port 8004`.

Use **Ctrl+C** to stop the launcher. Its normal shutdown terminates the owned
server/tunnel and removes `public-url.txt`. Do not share a saved old URL after
restarting. The disposable visitor directory is separate from private `data/`;
it must never be used for confidential or long-term records.

### Manual launch and other operating systems

Use a separate server on port **8003**, a dedicated temporary data directory,
and **`DRONEMAP_PUBLIC_DEMO=1`**. Never tunnel the private server on port 8000.
Public demo mode uses isolated guest workspaces; physical telemetry connection
and serial-port access are disabled. Do not enter private claimant names or
upload confidential imagery: public demonstrations are for disposable samples.

The public demo is an opt-in mode. Omitting its environment flag runs the normal
private authenticated product; a tunnel alone does not turn on guest isolation.
Starting the demo against an existing private data directory is unsupported.

In an already prepared Python environment, the same supervised launcher can be
run as `python tools/serve_demo.py --cloudflared PATH_TO_CLOUDFLARED`. Omit that
option for a local-only guest demo. On Windows, `--download-tunnel` resolves and
verifies the first official binary download. The examples below show the
underlying processes for manual control instead.

The following PowerShell example uses the project environment. Choose a **new**
temporary directory; the timestamp prevents accidental reuse of private `data/`:

```powershell
$env:DRONEMAP_PUBLIC_DEMO = '1'
$demoDirectory = Join-Path $env:TEMP ('dronemap-public-demo-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$env:DRONEMAP_DATA_DIR = $demoDirectory
$env:YOLO_AUTOINSTALL = 'false'
& '.\.venv\Scripts\python.exe' -m uvicorn main:app --host 127.0.0.1 --port 8003
```

For Linux/macOS, use a new temporary directory in a dedicated terminal:

```sh
export DRONEMAP_PUBLIC_DEMO=1
export DRONEMAP_DATA_DIR="$(mktemp -d -t dronemap-public-demo.XXXXXX)"
export YOLO_AUTOINSTALL=false
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8003
```

In another terminal, install/download `cloudflared` from its
[official distribution](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)
and run:

```sh
cloudflared tunnel --url http://127.0.0.1:8003
```

Use the printed `https://…trycloudflare.com` address. Check it from another device
before sharing. Quick Tunnels are intended for testing, use a random hostname
and have no uptime guarantee; see the
[official Quick Tunnel documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
No account/domain is required for this temporary workflow. If it is unavailable
on the current network, use the local installation for the presentation.

Keep **both** processes running and the laptop awake. When the tunnel exits,
the address stops serving. A new tunnel normally creates a new address. Do not
describe this URL as a permanent cloud deployment or a production endpoint.

Public demo PDFs omit public verification QR links; public report-snapshot
sharing is disabled. PDFs and exports retain visitor-supplied fields, including
sample owner claims and notes. They do not anonymize inputs. Normal private
installation reports retain their existing behavior. A public demo remains an
internet-facing prototype, not a confidential storage service.
Guests can export their demonstration work, but should not expect long-term
retention. Restart/cleanup and session expiry can remove access to demo records.

The guest session lasts two hours. The demo admits at most 20 active guests,
three projects per visitor, 200 features per project and four sources per
project. Uploads are limited to 8 MiB and 16 million input pixels, with a global
256 MiB source-storage cap and a 64 MiB cap on active SQLite record pages.
Individual boundaries are limited to 64 KiB of geometry, and each visitor is
limited to 1,000 saved edits/history entries. Map scans cover at most 500 m in either dimension;
native-image scans process at most 16 windows. Only one AI job is admitted at a
time, with a 15-second guest scan cooldown. If busy or limited, wait or use a
smaller scan instead of repeatedly submitting the same request. These limits
protect a laptop demonstration; they are not the private application's full
capacity or a production scaling claim.

Stop the tunnel and demo server after the session. Clear demo-only environment
variables before starting the private installation from the same terminal:

```powershell
Remove-Item Env:DRONEMAP_PUBLIC_DEMO -ErrorAction SilentlyContinue
Remove-Item Env:DRONEMAP_DATA_DIR -ErrorAction SilentlyContinue
```

On Linux/macOS, run `unset DRONEMAP_PUBLIC_DEMO DRONEMAP_DATA_DIR`.

## Persistent hosting later

Use a host that supports a Python web process, enough CPU/RAM for Torch,
persistent storage and HTTPS. Put `DRONEMAP_DATA_DIR` on that persistent volume
and set `DRONEMAP_PUBLIC_BASE_URL` to the externally reachable HTTPS origin so
PDF verification links work on other devices. Do not commit credentials,
session cookies, signing keys, private imagery, SQLite databases or backups.

Free-tier availability and quotas change. Do not assume a free plan can run the
full AI stack, preserve uploads across restarts or provide an uptime commitment.
There is no validated Docker build in this release. Test the application and
storage recovery on the chosen deployment platform before presenting it as a
supported hosted installation.

## Back up and restore the private workspace

```sh
python tools/backup_workspace.py backup --data-dir data --output /safe/private/survey-backup.zip
python tools/backup_workspace.py verify --archive /safe/private/survey-backup.zip
python tools/backup_workspace.py restore --archive /safe/private/survey-backup.zip --data-dir restored-data
```

Replace `/safe/private/` with an actual private path outside the repository.
Restore accepts a new/empty target and verifies archive hashes. Stop the server
before switching its configured data directory. The archive includes account
and survey data and is not encrypted. Treat it as private. A source-code ZIP or
a browser recovery copy does not replace this backup.

## Software checks

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node --test tests/*.test.cjs
```

Node 22 is used by CI. Python checks include core APIs, geospatial calculations,
imagery metadata/native-window behavior, backups, sensor freshness and actual
MAVLink packet parsing over loopback. AI is isolated using test fixtures in CI:
passing checks do not establish model accuracy or physical drone compatibility.
Run actual inference on the demonstration image and document a field test
separately. See [SIH_DEMO.md](SIH_DEMO.md) and [ACCURACY.md](ACCURACY.md).
