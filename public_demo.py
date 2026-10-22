"""Opt-in, temporary visitor workspaces for a publicly reachable SIH demo.

This policy is deliberately separate from private installations. It never
opens a drone transport, and refuses to reuse a private workspace database.
"""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import time

from fastapi import HTTPException

SESSION_SECONDS = 7200
MAX_GUESTS = 20
MAX_PROJECTS = 3
MAX_FEATURES = 200
MAX_SOURCES = 4
MAX_UPLOAD = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_STORAGE = 256 * 1024 * 1024
MAX_WINDOWS = 16
MAX_GEOMETRY_BYTES = 64 * 1024
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_HISTORY_PER_VISITOR = 1000
SCAN_INTERVAL = 15
NOTICE = "Temporary SIH demonstration. Each visitor has a separate workspace that expires after two hours. Use sample data only; do not enter real personal or ownership information. Physical drone connections require the local installation."


def unavailable_drone():
    return {"available": False, "dependency_available": False, "connected": False,
        "state": "unavailable", "message": "Physical drone connections are disabled in the public demo. Use the local installation to connect real hardware.",
        "connection": None, "telemetry": {}, "telemetry_freshness": {}, "supported_transports": [],
        "controls": {"can_arm": False, "can_upload_mission": False, "can_capture": False}}


def initialize_mode(store, enabled):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        marker = db.execute("SELECT value FROM settings WHERE key='public_demo'").fetchone()
        if enabled:
            if db.execute("SELECT 1 FROM users WHERE role!='demo' LIMIT 1").fetchone():
                raise RuntimeError("Public demo cannot open a private workspace. Choose a new, isolated DRONEMAP_DATA_DIR.")
            if not marker and db.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
                raise RuntimeError("Public demo requires an empty or previously marked demo workspace.")
            db.execute("INSERT OR REPLACE INTO settings VALUES('public_demo','1')")
        elif marker:
            raise RuntimeError("This data directory belongs to a public demo. Choose your private workspace directory instead.")


class DemoPolicy:
    def __init__(self, store):
        self.store = store
        self.last_scan = {}

    def sources_root(self):
        root = (self.store.data_dir / "sources").resolve()
        if not root.is_relative_to(self.store.data_dir.resolve()):
            raise RuntimeError("Demo source storage must remain inside its isolated data directory.")
        return root

    def files(self):
        root = self.sources_root()
        if not root.is_dir():
            return set()
        return {path.resolve() for path in root.rglob("*")
            if path.is_file() and path.resolve().is_relative_to(root)}

    def remove_files(self, paths):
        root = self.sources_root()
        for path in paths:
            candidate = Path(path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                candidate.unlink(missing_ok=True)

    def storage_check(self):
        if sum(path.stat().st_size for path in self.files()) >= MAX_STORAGE:
            raise HTTPException(507, "Public demo imagery storage is full. Try again after expired visitor workspaces are cleaned up.")

    def source_check(self, project_id, *, new=True):
        if new:
            with self.store.connection() as db:
                count = db.execute("SELECT COUNT(*) FROM sources WHERE project_id=?", (project_id,)).fetchone()[0]
            if count >= MAX_SOURCES:
                raise HTTPException(429, "The public demo allows four imagery sources per project. Open another demo project or use the local installation.")
        self.storage_check()

    def scan_check(self, user_id):
        stamp = time.monotonic()
        remaining = SCAN_INTERVAL - (stamp - self.last_scan.get(user_id, float('-inf')))
        if remaining > 0:
            raise HTTPException(429, "Please wait 15 seconds between public demo scans.", headers={"Retry-After": str(max(1, math.ceil(remaining)))})
        self.last_scan[user_id] = stamp

    @staticmethod
    def history_check(db, user_id):
        # Bound editable geometry history as well as raster storage. Freed
        # SQLite pages are reusable after expired visitors are removed.
        used_pages = db.execute("PRAGMA page_count").fetchone()[0] - db.execute("PRAGMA freelist_count").fetchone()[0]
        if used_pages * db.execute("PRAGMA page_size").fetchone()[0] >= MAX_DATABASE_BYTES:
            raise HTTPException(507, "Public demo record storage is full. Export your work and try again after expired visitor workspaces are cleaned up.")
        if db.execute("SELECT COUNT(*) FROM feature_history WHERE user_id=?", (user_id,)).fetchone()[0] >= MAX_HISTORY_PER_VISITOR:
            raise HTTPException(429, "This demo visitor has reached the 1,000 saved-edit limit. Export your sample work or use the local installation.")

    @staticmethod
    def map_bounds(bounds):
        from geospatial import validate_bounds
        from pyproj import Geod
        west, south, east, north = validate_bounds(bounds, mercator=True)
        geod = Geod(ellps="WGS84")
        width = abs(geod.inv(west, (south+north)/2, east, (south+north)/2)[2])
        height = abs(geod.inv((west+east)/2, south, (west+east)/2, north)[2])
        if max(width, height) > 500:
            raise HTTPException(422, "Public demo scans must be no more than 500 m wide and 500 m high. Zoom in or draw a smaller AOI.")

    @staticmethod
    def upload_pixels(contents):
        from PIL import Image
        try:
            with Image.open(io.BytesIO(contents)) as image:
                width, height = image.size
        except Exception:
            try:
                from rasterio.io import MemoryFile
                with MemoryFile(contents) as memory, memory.open() as image:
                    width, height = image.width, image.height
            except Exception as exc:
                raise HTTPException(422, "Upload a readable PNG, JPEG, or georeferenced TIFF image.") from exc
        if width * height > MAX_PIXELS:
            raise HTTPException(413, "Public demo images are limited to 16 million pixels. Use a smaller image or the local installation.")

    @staticmethod
    def source_windows(source, bounds):
        from imagery import SurveyImagery, _window_positions
        try:
            (left, top, right, bottom), _ = SurveyImagery._scan_region(source, bounds)
            windows = len(_window_positions(right-left)) * len(_window_positions(bottom-top))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, "Choose a valid AOI within the imported imagery.") from exc
        if windows > MAX_WINDOWS:
            raise HTTPException(422, "The public demo allows 16 native-resolution inference windows per scan. Draw a smaller AOI within the image.")

    def cleanup_expired(self, db):
        """Called while inference admission is held and a DB transaction is open."""
        cutoff = datetime.fromtimestamp(time.time()-SESSION_SECONDS, timezone.utc).isoformat(timespec="seconds")
        guests = db.execute("SELECT id FROM users WHERE role='demo' AND created_at<=?", (cutoff,)).fetchall()
        files = []
        for guest in guests:
            user_id = guest["id"]
            project_ids = [row[0] for row in db.execute("SELECT id FROM projects WHERE user_id=?", (user_id,))]
            for project_id in project_ids:
                for source in db.execute("SELECT id,metadata FROM sources WHERE project_id=?", (project_id,)):
                    metadata = json.loads(source["metadata"])
                    files.extend(metadata.get(key) for key in ("raster_path", "preview_path") if metadata.get(key))
                    # Imagery keeps a small sidecar alongside the raster; it
                    # contains the same temporary source metadata and expires too.
                    files.append(self.sources_root() / (source["id"] + ".json"))
                db.execute("DELETE FROM reports WHERE feature_id IN (SELECT id FROM features WHERE project_id=?)", (project_id,))
                # Deleted features remain in history; their report snapshots also expire.
                db.execute("DELETE FROM reports WHERE feature_id IN (SELECT feature_id FROM feature_history WHERE project_id=?)", (project_id,))
                db.execute("DELETE FROM feature_history WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM projects WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
            self.last_scan.pop(user_id, None)
        return files
