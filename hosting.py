"""Explicit configuration for the persistent, authenticated server installation."""
from __future__ import annotations

import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from storage import new_id, now


def public_origin(value):
    """Require one HTTPS origin, without credentials or a path prefix."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise RuntimeError("DRONEMAP_PUBLIC_BASE_URL must be a valid HTTPS origin.") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment or "*" in parsed.netloc
            or any(character.isspace() for character in value)):
        raise RuntimeError("DRONEMAP_PUBLIC_BASE_URL must be one HTTPS origin without credentials, query or path.")
    hostname = parsed.hostname.lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname):
        raise RuntimeError("DRONEMAP_PUBLIC_BASE_URL requires a valid DNS hostname or IPv4 address.")
    authority = hostname + (f":{port}" if port and port != 443 else "")
    return "https://" + authority, hostname


def hosted_configuration(directory):
    if not directory or not Path(directory).is_absolute():
        raise RuntimeError("Hosted mode requires an explicit absolute DRONEMAP_DATA_DIR on persistent storage.")
    origin, hostname = public_origin(os.environ.get("DRONEMAP_PUBLIC_BASE_URL", ""))
    return origin, hostname


def provision_owner(store, encode_password):
    """Create the initial account privately, and never reset an existing one."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
        name = os.environ.get("DRONEMAP_BOOTSTRAP_NAME", "").strip()
        email = os.environ.get("DRONEMAP_BOOTSTRAP_EMAIL", "").strip().casefold()
        password = os.environ.get("DRONEMAP_BOOTSTRAP_PASSWORD", "")
        if (not 1 <= len(name) <= 120 or not 3 <= len(email) <= 254
                or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
                or not 12 <= len(password) <= 256):
            raise RuntimeError("First hosted launch requires DRONEMAP_BOOTSTRAP_NAME, a valid DRONEMAP_BOOTSTRAP_EMAIL and a 12–256 character DRONEMAP_BOOTSTRAP_PASSWORD. Set these as server secrets.")
        db.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (
            new_id(), name, email, encode_password(password), "surveyor", now()))


def unavailable_hosted_drone():
    return {"available": False, "dependency_available": False, "connected": False,
        "state": "unavailable", "message": "This server cannot connect directly to your local aircraft. Import georeferenced imagery here; use the local installation for compatible drone telemetry.",
        "connection": None, "telemetry": {}, "telemetry_freshness": {}, "supported_transports": [],
        "controls": {"can_arm": False, "can_upload_mission": False, "can_capture": False}}
