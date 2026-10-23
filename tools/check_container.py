"""Exercise a built server image with synthetic data and disposable Docker storage.

Run only on a machine with Docker: python tools/check_container.py IMAGE_TAG.
Nothing is uploaded. The actual supplied model is loaded and run once on a blank
image; that is a runtime smoke test, not a building-accuracy measurement.
"""
from __future__ import annotations

import json
from http.cookies import SimpleCookie
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid


def docker(*arguments, capture=True):
    return subprocess.run(["docker", *arguments], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def run(image):
    run_id = uuid.uuid4().hex[:12]
    volume = "dronemap-check-" + run_id
    name = "dronemap-check-" + run_id
    created_volume = created_container = False
    base = ""
    token = ""
    password = secrets.token_urlsafe(24)

    def request(path, payload=None, *, origin="https://survey.example.test"):
        headers = {"Host": "survey.example.test", "Origin": origin}
        if token:
            headers["Cookie"] = "dronemap_session=" + token
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        with urlopen(Request(base + path, data=body, headers=headers), timeout=30) as response:
            content = response.read()
            return response.status, response.headers, content

    def start(env_file):
        nonlocal created_container, base
        docker("run", "--detach", "--name", name, "--publish", "127.0.0.1::8000",
               "--mount", f"type=volume,source={volume},target=/var/lib/dronemap",
               "--env-file", str(env_file), image)
        created_container = True
        mapping = docker("port", name, "8000/tcp").strip().splitlines()[0]
        base = "http://" + mapping
        deadline = time.monotonic() + 90
        while True:
            try:
                _, _, body = request("/healthz")
                assert json.loads(body) == {"status": "ok", "mode": "hosted"}
                break
            except (URLError, TimeoutError, ConnectionError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Container did not become healthy within 90 seconds.")
                time.sleep(1)

    try:
        docker("run", "--rm", "--entrypoint", "python", image, "-c",
               "import numpy as np; from ultralytics import YOLO; "
               "m=YOLO('/app/best.pt'); r=m.predict(np.zeros((256,256,3),dtype=np.uint8),device='cpu',verbose=False); "
               "assert len(r)==1; print('Actual checkpoint CPU inference passed on a synthetic blank image; no accuracy claim.')",
               capture=False)
        docker("volume", "create", volume)
        created_volume = True
        with tempfile.TemporaryDirectory(prefix="dronemap-container-check-") as temporary:
            env_file = Path(temporary) / "server.env"
            env_file.write_text("\n".join([
                "DRONEMAP_PUBLIC_BASE_URL=https://survey.example.test",
                "DRONEMAP_BOOTSTRAP_NAME=Synthetic operator",
                "DRONEMAP_BOOTSTRAP_EMAIL=operator@example.test",
                "DRONEMAP_BOOTSTRAP_PASSWORD=" + password,
            ]) + "\n", encoding="utf-8")
            env_file.chmod(0o600)
            start(env_file)
            _, _, body = request("/api/status")
            status = json.loads(body)
            assert not status["setup_required"] and not status["public_demo"]
            _, headers, _ = request("/api/login", {"email": "operator@example.test", "password": password})
            assert "Secure" in headers["Set-Cookie"]
            cookie = SimpleCookie()
            cookie.load(headers["Set-Cookie"])
            token = cookie["dronemap_session"].value
            _, _, body = request("/api/projects", {"name": "Synthetic persistence check"})
            project = json.loads(body)
            geometry = {"type": "Polygon", "coordinates": [[[88, 22], [88.0001, 22], [88.0001, 22.0001], [88, 22.0001], [88, 22]]]}
            _, _, body = request(f"/api/projects/{project['id']}/features", {
                "geometry": geometry, "properties": {"name": "Synthetic roof", "feature_type": "building", "status": "draft"}})
            feature = json.loads(body)
            _, _, body = request("/api/drone/status")
            assert json.loads(body)["available"] is False
            try:
                request("/api/projects", {"name": "Blocked"}, origin="https://attacker.example.test")
                raise AssertionError("Cross-origin write was accepted")
            except HTTPError as exc:
                assert exc.code == 403
            docker("stop", "--time", "10", name)
            docker("rm", name)
            created_container = False
            # Start a new container with no bootstrap credentials. Both account
            # and session/project/geometry must come from the persistent volume.
            env_file.write_text("DRONEMAP_PUBLIC_BASE_URL=https://survey.example.test\n", encoding="utf-8")
            start(env_file)
            _, _, body = request(f"/api/projects/{project['id']}/features")
            assert any(item["id"] == feature["id"] and item["geometry"] == geometry
                       for item in json.loads(body)["features"])
            _, headers, body = request(f"/api/features/{feature['id']}/report.pdf")
            assert body.startswith(b"%PDF-")
            assert headers["X-Verification-URL"].startswith("https://survey.example.test/report/")
            print("Hosted image passed: real CPU model execution, HTTPS-cookie policy, normal login, saved geometry across container replacement, canonical PDF URL, origin protection and hosted hardware isolation.")
    except Exception:
        if created_container:
            subprocess.run(["docker", "logs", "--tail", "60", name], check=False)
        raise
    finally:
        if created_container:
            subprocess.run(["docker", "stop", "--time", "10", name], check=False, stdout=subprocess.DEVNULL)
            subprocess.run(["docker", "rm", name], check=False, stdout=subprocess.DEVNULL)
        if created_volume:
            subprocess.run(["docker", "volume", "rm", volume], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tools/check_container.py IMAGE_TAG")
    run(sys.argv[1])
