"""Run one authenticated server process behind an HTTPS reverse proxy."""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if os.environ.get("DRONEMAP_PUBLIC_DEMO", "").casefold() in {"1", "yes", "true"}:
        raise SystemExit("Unset DRONEMAP_PUBLIC_DEMO before starting the persistent server.")
    os.environ["DRONEMAP_HOSTED"] = "1"
    os.environ["YOLO_AUTOINSTALL"] = "false"
    try:
        port = int(os.environ.get("PORT", "8000"))
        if not 1024 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("PORT must be an integer between 1024 and 65535.")
    from main import app
    import uvicorn
    # One worker/replica keeps SQLite, AI admission and login throttling coherent.
    # Default trust is loopback only; change solely for a known private proxy.
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, proxy_headers=True,
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))


if __name__ == "__main__":
    main()
