"""Public visitors remain isolated from each other, hardware and private data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import threading
import time

import anyio
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from conftest import NoInference, feature_payload
from main import MapScanBody, create_app
from public_demo import DemoPolicy
from storage import Store, new_id, now


def demo_app(directory, imagery=None, **kwargs):
    return create_app(directory, imagery_service=imagery or NoInference(directory), public_demo=True, **kwargs)


def start(client):
    response = client.post("/api/demo/start")
    assert response.status_code == 200, response.text
    return response.json()


def png(width=40, height=30):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def upload(client, project_id):
    return client.post(f"/api/projects/{project_id}/sources", files={"file": ("sample.png", png(), "image/png")},
        data={"bounds": json.dumps([87.7,24.08,87.701,24.081])})


def test_demo_requires_explicit_separate_data_and_refuses_private_users(tmp_path, monkeypatch):
    monkeypatch.delenv("DRONEMAP_DATA_DIR", raising=False)
    with pytest.raises(RuntimeError, match="isolated"):
        create_app(public_demo=True)
    directory = tmp_path / "private"
    with TestClient(create_app(directory, imagery_service=NoInference(directory))) as private:
        assert private.post("/api/setup", json={"name":"Private owner", "email":"owner@example.test", "password":"Correct-horse-Map!2026"}).status_code == 201
    with pytest.raises(RuntimeError, match="private workspace"):
        demo_app(directory)
    # A failed attempt did not mark or damage the private installation.
    with TestClient(create_app(directory, imagery_service=NoInference(directory))) as private:
        assert private.get("/api/status").json()["setup_required"] is False


def test_demo_directory_cannot_be_opened_as_private(tmp_path):
    directory = tmp_path / "demo"
    demo_app(directory)
    with pytest.raises(RuntimeError, match="public demo"):
        create_app(directory, imagery_service=NoInference(directory), public_demo=False)


def test_public_demo_environment_flag_requires_an_isolated_directory(tmp_path, monkeypatch):
    directory = tmp_path / "environment-demo"
    monkeypatch.setenv("DRONEMAP_PUBLIC_DEMO", "1")
    monkeypatch.setenv("DRONEMAP_DATA_DIR", str(directory))
    app = create_app(imagery_service=NoInference(directory))
    assert app.state.public_demo is True


def test_guest_sessions_are_isolated_and_reused(tmp_path):
    app = demo_app(tmp_path / "demo")
    with TestClient(app) as first, TestClient(app) as second:
        status = first.get("/api/status").json()
        assert status["public_demo"] is True
        assert status["setup_required"] is False
        assert status["demo"]["expires_in_seconds"] == 7200
        one, two = start(first), start(second)
        assert one["user"]["role"] == "demo"
        assert one["user"]["id"] != two["user"]["id"]
        assert "dronemap_demo_session" in first.cookies
        assert "dronemap_session" not in first.cookies
        assert start(first)["project"]["id"] == one["project"]["id"]
        created = first.post(f"/api/projects/{one['project']['id']}/features", json=feature_payload()).json()
        assert second.get(f"/api/projects/{one['project']['id']}/features").status_code == 404
        assert second.get(f"/api/features/{created['id']}/history").status_code == 404
        assert [p["id"] for p in second.get("/api/projects").json()["projects"]] == [two["project"]["id"]]
        assert first.post("/api/logout").status_code == 200
        assert first.get("/api/projects").status_code == 401
        assert second.get("/api/projects").status_code == 200


def test_demo_blocks_password_accounts_and_all_physical_adapter_calls(tmp_path):
    class ForbiddenAdapter:
        def __getattr__(self, name):
            raise AssertionError(f"Public demo accessed physical adapter: {name}")
    app = demo_app(tmp_path / "demo", drone_service=ForbiddenAdapter())
    with TestClient(app) as client:
        start(client)
        assert client.post("/api/setup", json={"name":"Someone", "email":"someone@example.test", "password":"Correct-horse-Map!2026"}).status_code == 403
        assert client.post("/api/login", json={"email":"someone@example.test", "password":"anything"}).status_code == 403
        status = client.get("/api/drone/status").json()
        assert status["available"] is False and status["state"] == "unavailable"
        assert not any(status["controls"].values())
        assert client.get("/api/drone/ports").json() == {"ports":[]}
        for config in ({"transport":"tcp","host":"127.0.0.1","port":14551},
                       {"transport":"udpin","host":"0.0.0.0","port":14551},
                       {"transport":"serial","device":"COM3"}):
            assert client.post("/api/drone/connect", json=config).status_code == 403
        assert client.post("/api/drone/disconnect").json()["state"] == "unavailable"
        assert client.post("/api/logout").status_code == 200


def test_demo_guest_expiry_cleans_records_and_only_its_source_files(tmp_path):
    app = demo_app(tmp_path / "demo")
    outside = tmp_path / "private-survey.png"
    outside.write_bytes(b"never remove private files")
    with TestClient(app) as client:
        guest = start(client)
        project_id = guest["project"]["id"]
        source = upload(client, project_id)
        assert source.status_code == 201, source.text
        feature = client.post(f"/api/projects/{project_id}/features", json=feature_payload()).json()
        with app.state.store.connection() as db:
            metadata = json.loads(db.execute("SELECT metadata FROM sources").fetchone()[0])
            own_path = metadata["raster_path"]
            metadata["preview_path"] = str(outside)
            db.execute("UPDATE sources SET metadata=?", (json.dumps(metadata),))
            db.execute("INSERT INTO reports VALUES(?,?,?,?,?)", (new_id(), feature["id"], 1, "{}", now()))
            old = datetime.fromtimestamp(time.time()-7201, timezone.utc).isoformat(timespec="seconds")
            db.execute("UPDATE users SET created_at=? WHERE id=?", (old,guest["user"]["id"]))
        # The original creation time limits access even if a session expiry was extended.
        assert client.get("/api/projects").status_code == 401
        replacement = start(client)
        assert replacement["user"]["id"] != guest["user"]["id"]
        assert outside.read_bytes() == b"never remove private files"
        from pathlib import Path
        assert not Path(own_path).exists()
        with app.state.store.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
            for table in ("sources","features","feature_history","reports","feature_requests"):
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_demo_guest_and_project_caps_are_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr("public_demo.MAX_GUESTS", 1)
    app = demo_app(tmp_path / "demo")
    with TestClient(app) as one, TestClient(app) as two:
        start(one)
        assert two.post("/api/demo/start").status_code == 429
        for index in range(2):
            assert one.post("/api/projects", json={"name":f"Sample {index}"}).status_code == 201
        assert one.post("/api/projects", json={"name":"Fourth project"}).status_code == 429


def test_demo_saved_feature_cap_preserves_idempotent_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("public_demo.MAX_FEATURES", 1)
    app = demo_app(tmp_path / "demo")
    with TestClient(app) as client:
        project_id = start(client)["project"]["id"]
        url = f"/api/projects/{project_id}/features"
        headers = {"Idempotency-Key":"save-demo-1"}
        first = client.post(url, json=feature_payload(), headers=headers)
        assert first.status_code == 201
        assert client.post(url, json=feature_payload(), headers=headers).json()["id"] == first.json()["id"]
        assert client.post(url, json=feature_payload(name="Another")).status_code == 429


def test_demo_geometry_and_edit_history_are_bounded(tmp_path, monkeypatch):
    app = demo_app(tmp_path / "demo")
    with TestClient(app) as client:
        project_id = start(client)["project"]["id"]
        monkeypatch.setattr("public_demo.MAX_GEOMETRY_BYTES", 10)
        assert client.post(f"/api/projects/{project_id}/features", json=feature_payload()).status_code == 422
        monkeypatch.setattr("public_demo.MAX_GEOMETRY_BYTES", 64*1024)
        monkeypatch.setattr("public_demo.MAX_HISTORY_PER_VISITOR", 1)
        created = client.post(f"/api/projects/{project_id}/features", json=feature_payload()).json()
        response = client.put(f"/api/features/{created['id']}", json=feature_payload(version=1, name="Second edit"))
        assert response.status_code == 429
        saved = client.get(f"/api/projects/{project_id}/features").json()["features"][0]
        assert saved["properties"]["name"] == "Test footprint"
        assert saved["properties"]["version"] == 1


def test_demo_upload_and_scan_limits_do_not_persist_rejected_sources(tmp_path, monkeypatch):
    app = demo_app(tmp_path / "demo")
    with TestClient(app) as client:
        project_id = start(client)["project"]["id"]
        assert client.post(f"/api/projects/{project_id}/scans/map", json={"bounds":[87.7,24.08,87.71,24.09]}).status_code == 422
        monkeypatch.setattr("public_demo.MAX_PIXELS", 100)
        rejected = upload(client, project_id)
        assert rejected.status_code == 413
        assert client.get(f"/api/projects/{project_id}/sources").json()["sources"] == []
        monkeypatch.setattr("public_demo.MAX_PIXELS", 16_000_000)
        monkeypatch.setattr("public_demo.MAX_SOURCES", 1)
        assert upload(client, project_id).status_code == 201
        assert upload(client, project_id).status_code == 429
        assert len(client.get(f"/api/projects/{project_id}/sources").json()["sources"]) == 1


def test_demo_storage_cap_and_failed_job_cleanup(tmp_path, monkeypatch):
    class FailingImagery(NoInference):
        def inspect_upload(self, *args, **kwargs):
            self.real.sources_dir.mkdir(parents=True, exist_ok=True)
            (self.real.sources_dir / "failed-job.png").write_bytes(b"orphan")
            raise RuntimeError("Deliberate failure")
    directory = tmp_path / "demo"
    service = FailingImagery(directory)
    app = demo_app(directory, service)
    with TestClient(app) as client:
        project_id = start(client)["project"]["id"]
        assert upload(client, project_id).status_code == 503
        assert not (directory / "sources" / "failed-job.png").exists()
        assert not app.state.inference_lock.locked()
        monkeypatch.setattr("public_demo.MAX_STORAGE", 1)
        (directory / "sources" / "retained.png").write_bytes(b"existing")
        assert upload(client, project_id).status_code == 507
        assert (directory / "sources" / "retained.png").exists()


def test_new_upload_crossing_shared_storage_cap_is_removed(tmp_path, monkeypatch):
    app = demo_app(tmp_path / "demo")
    with TestClient(app) as client:
        project_id = start(client)["project"]["id"]
        monkeypatch.setattr("public_demo.MAX_STORAGE", 8)
        assert upload(client, project_id).status_code == 507
        assert app.state.demo_policy.files() == set()
        assert client.get(f"/api/projects/{project_id}/sources").json()["sources"] == []


def test_demo_native_scan_window_limit_and_cooldown(tmp_path):
    app = demo_app(tmp_path / "demo")
    policy = app.state.demo_policy
    with pytest.raises(HTTPException, match="16 native-resolution"):
        policy.source_windows({"width":8000,"height":8000}, None)
    policy.source_windows({"width":2048,"height":2048}, None)
    policy.scan_check("visitor")
    with pytest.raises(HTTPException) as failure:
        policy.scan_check("visitor")
    assert failure.value.status_code == 429
    assert 1 <= int(failure.value.headers["Retry-After"]) <= 15


def test_demo_pdf_has_no_public_snapshot_or_verification_link(tmp_path):
    app = demo_app(tmp_path / "demo")
    with TestClient(app) as client:
        project_id = start(client)["project"]["id"]
        feature = client.post(f"/api/projects/{project_id}/features", json=feature_payload()).json()
        response = client.get(f"/api/features/{feature['id']}/report.pdf")
        assert response.status_code == 200, response.text[:200]
        assert response.content.startswith(b"%PDF")
        assert "X-Verification-URL" not in response.headers
        assert client.get("/api/public/reports/anything").status_code == 404
        assert client.get("/report/anything").status_code == 404
        with app.state.store.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


def test_local_mode_has_no_guest_login_and_keeps_private_status(client):
    assert client.post("/api/demo/start").status_code == 404
    status = client.get("/api/status").json()
    assert status["public_demo"] is False and status["demo"] is None
    assert status["setup_required"] is True


class BlockingImagery(NoInference):
    def __init__(self, directory):
        super().__init__(directory)
        self.started = threading.Event()
        self.release = threading.Event()

    def scan_map(self, *args, **kwargs):
        self.started.set()
        assert self.release.wait(5), "Test did not release its worker"
        raise RuntimeError("Deliberate completion")


def test_busy_inference_rejects_without_queuing_worker_threads(tmp_path):
    directory = tmp_path / "demo"
    service = BlockingImagery(directory)
    app = demo_app(directory, service)
    with TestClient(app) as first, TestClient(app) as second:
        one, two = start(first), start(second)
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(first.post, f"/api/projects/{one['project']['id']}/scans/map", json={"bounds":[87.7,24.08,87.701,24.081]})
            try:
                assert service.started.wait(2)
                busy = upload(second, two["project"]["id"])
                assert busy.status_code == 429
                assert busy.headers["Retry-After"] == "5"
                assert second.get("/api/status").status_code == 200
            finally:
                service.release.set()
            assert running.result().status_code == 503
        assert not app.state.inference_lock.locked()


def test_cancelled_request_keeps_admission_until_native_worker_finishes(tmp_path):
    directory = tmp_path / "demo"
    service = BlockingImagery(directory)
    app = demo_app(directory, service)
    with TestClient(app) as client:
        guest = start(client)
    with app.state.store.connection() as db:
        user = db.execute("SELECT * FROM users").fetchone()
    endpoint = next(route.endpoint for route in app.routes if getattr(route,"path","") == "/api/projects/{project_id}/scans/map")
    async def run():
        async def scan():
            with pytest.raises(HTTPException) as failure:
                await endpoint(guest["project"]["id"], MapScanBody(bounds=[87.7,24.08,87.701,24.081]), user)
            assert failure.value.status_code == 503
        async with anyio.create_task_group() as group:
            group.start_soon(scan)
            assert await anyio.to_thread.run_sync(service.started.wait, 2)
            group.cancel_scope.cancel()
            with anyio.CancelScope(shield=True):
                await anyio.sleep(0.02)
                assert app.state.inference_lock.locked()
                with pytest.raises(HTTPException) as busy:
                    await endpoint(guest["project"]["id"], MapScanBody(bounds=[87.7,24.08,87.701,24.081]), user)
                assert busy.value.status_code == 429
                service.release.set()
        assert not app.state.inference_lock.locked()
    try:
        anyio.run(run)
    finally:
        service.release.set()
