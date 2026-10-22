"""SQLite persistence for private surveys and immutable report snapshots."""
from __future__ import annotations
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def new_id():
    return secrets.token_hex(16)

class Store:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "workspace.sqlite3"
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,location TEXT NOT NULL,description TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,metadata TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS features(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,geometry TEXT NOT NULL,properties TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feature_history(id INTEGER PRIMARY KEY AUTOINCREMENT,feature_id TEXT NOT NULL,project_id TEXT NOT NULL,version INTEGER NOT NULL,action TEXT NOT NULL,snapshot TEXT NOT NULL,user_id TEXT NOT NULL,timestamp TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,feature_id TEXT NOT NULL,feature_version INTEGER NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(feature_id,feature_version));
            CREATE TABLE IF NOT EXISTS feature_requests(project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,request_key TEXT NOT NULL,payload_hash TEXT NOT NULL,feature_id TEXT NOT NULL,PRIMARY KEY(project_id,request_key));
            CREATE INDEX IF NOT EXISTS features_project ON features(project_id);
            CREATE INDEX IF NOT EXISTS sources_project ON sources(project_id);
            CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES('report_signing_key',?)", (secrets.token_hex(32),))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def setting(self, key):
        with self.connection() as db:
            return db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0]

def feature_json(row):
    props = json.loads(row["properties"])
    props.update(id=row["id"],project_id=row["project_id"],version=row["version"],created_at=row["created_at"],updated_at=row["updated_at"])
    return {"type":"Feature","id":row["id"],"geometry":json.loads(row["geometry"]),"properties":props}
