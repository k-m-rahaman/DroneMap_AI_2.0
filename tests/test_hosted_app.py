"""Hosted deployments retain accounts and records without exposing host hardware."""
import csv
from datetime import datetime, timedelta, timezone
import io

from fastapi.testclient import TestClient
import pytest

from conftest import NoInference, feature_payload, polygon
from main import create_app


ORIGIN = "https://survey.example.test"
EMAIL = "hosted-surveyor@example.test"
PASSWORD = "Hosted-correct-Map!2026"
BOOTSTRAP_KEYS = (
    "DRONEMAP_BOOTSTRAP_NAME", "DRONEMAP_BOOTSTRAP_EMAIL",
    "DRONEMAP_BOOTSTRAP_PASSWORD",
)


@pytest.fixture(autouse=True)
def deployment_environment(monkeypatch):
    """Each test gets explicit synthetic deployment settings, never local secrets."""
    for key in ("DRONEMAP_HOSTED", "DRONEMAP_PUBLIC_DEMO", "DRONEMAP_DATA_DIR",
                "DRONEMAP_PUBLIC_BASE_URL", *BOOTSTRAP_KEYS):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DRONEMAP_PUBLIC_BASE_URL", ORIGIN)
    monkeypatch.setenv("DRONEMAP_BOOTSTRAP_NAME", "Hosted Surveyor")
    monkeypatch.setenv("DRONEMAP_BOOTSTRAP_EMAIL", EMAIL)
    monkeypatch.setenv("DRONEMAP_BOOTSTRAP_PASSWORD", PASSWORD)


def hosted_app(directory, **kwargs):
    return create_app(directory, imagery_service=NoInference(directory),
                      hosted=True, **kwargs)


def login(client, password=PASSWORD):
    response = client.post("/api/login", json={"email": EMAIL, "password": password})
    assert response.status_code == 200, response.text
    return response


def test_hosted_requires_explicit_absolute_persistent_directory(tmp_path):
    # Invalid configurations must fail before opening the repository's data/.
    for directory in (None, "relative-hosted-workspace"):
        with pytest.raises(RuntimeError):
            create_app(directory, imagery_service=NoInference(tmp_path / "imagery"),
                       hosted=True)


@pytest.mark.parametrize("base", [
    "", "http://survey.example.test", "https://user:pass@survey.example.test",
    "https://survey.example.test/workspace", "https://survey.example.test?x=1",
    "https://survey.example.test#fragment", "https://*.example.test",
    "https://survey.example.test:wrong", "https://survey.example.test:65536",
])
def test_hosted_requires_canonical_https_origin(tmp_path, monkeypatch, base):
    monkeypatch.setenv("DRONEMAP_PUBLIC_BASE_URL", base)
    with pytest.raises(RuntimeError):
        hosted_app(tmp_path / "invalid-origin")


@pytest.mark.parametrize("missing", BOOTSTRAP_KEYS)
def test_empty_hosted_installation_requires_all_bootstrap_fields(tmp_path, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError):
        hosted_app(tmp_path / "unclaimed")


@pytest.mark.parametrize("field", BOOTSTRAP_KEYS)
def test_empty_bootstrap_fields_cannot_create_an_unclaimed_hosted_account(tmp_path, monkeypatch, field):
    monkeypatch.setenv(field, "")
    with pytest.raises(RuntimeError):
        hosted_app(tmp_path / "empty-bootstrap")


def test_hosted_and_temporary_demo_modes_cannot_be_combined(tmp_path):
    with pytest.raises(RuntimeError):
        hosted_app(tmp_path / "mixed-mode", public_demo=True)


def test_hosted_rejects_weak_bootstrap_password(tmp_path, monkeypatch):
    monkeypatch.setenv("DRONEMAP_BOOTSTRAP_PASSWORD", "1234")
    with pytest.raises(RuntimeError):
        hosted_app(tmp_path / "weak-password")


def test_environment_can_enable_hosted_mode_with_explicit_storage(tmp_path, monkeypatch):
    directory = tmp_path / "environment-hosted"
    monkeypatch.setenv("DRONEMAP_HOSTED", "1")
    monkeypatch.setenv("DRONEMAP_DATA_DIR", str(directory))
    app = create_app(imagery_service=NoInference(directory))
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/healthz").json() == {"status": "ok", "mode": "hosted"}
        assert client.get("/api/status").json()["setup_required"] is False
        login(client)


def test_hosted_provisions_normal_account_and_blocks_claim_setup_and_guest_mode(tmp_path):
    app = hosted_app(tmp_path / "hosted")
    with TestClient(app, base_url=ORIGIN) as client:
        status = client.get("/api/status").json()
        assert status["setup_required"] is False
        assert status["public_demo"] is False
        assert status["demo"] is None
        assert status["authenticated"] is False
        assert client.get("/api/projects").status_code == 401
        assert client.post("/api/setup", json={
            "name": "First visitor", "email": "visitor@example.test",
            "password": "Visitor-correct-Map!2026",
        }).status_code == 403
        assert client.post("/api/demo/start").status_code == 404
        logged_in = login(client)
        assert logged_in.json()["user"]["role"] == "surveyor"
        assert "dronemap_session" in client.cookies
        assert "dronemap_demo_session" not in client.cookies
        assert client.get("/api/status").json()["authenticated"] is True
        assert client.post("/api/setup", json={
            "name": "Extra visitor", "email": "other@example.test",
            "password": "Other-correct-Map!2026",
        }).status_code == 403
    with app.state.store.connection() as database:
        assert database.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_hosted_uses_canonical_origin_and_secure_cookie_behind_http_proxy(tmp_path):
    app = hosted_app(tmp_path / "proxy-hosted")
    # The reverse proxy terminates HTTPS. The application still sees HTTP.
    with TestClient(app, base_url="http://survey.example.test") as client:
        response = client.post("/api/login", headers={"Origin": ORIGIN},
                               json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 200, response.text
        cookie = response.headers.get("set-cookie", "").casefold()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        assert "dronemap_session=" in cookie
        blocked = client.post("/api/login", headers={"Origin": "http://survey.example.test"},
                              json={"email": EMAIL, "password": PASSWORD})
        assert blocked.status_code == 403
        assert client.post("/api/login", headers={"Origin": "https://attacker.example.test"},
                           json={"email": EMAIL, "password": PASSWORD}).status_code == 403


def test_hosted_rejects_untrusted_host_before_exposing_status_or_setup(tmp_path):
    app = hosted_app(tmp_path / "host-guard")
    with TestClient(app, base_url="https://attacker.example.test", follow_redirects=False) as client:
        assert client.get("/api/status").status_code == 400
        assert client.get("/healthz").status_code == 400
        assert client.post("/api/setup", json={
            "name": "Visitor", "email": "visitor@example.test",
            "password": "Visitor-correct-Map!2026",
        }).status_code == 400


def test_hosted_rejects_cross_site_mutation_even_with_canonical_origin(tmp_path):
    app = hosted_app(tmp_path / "cross-site")
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        response = client.post("/api/projects", json={"name": "Must not exist"},
                               headers={"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403
        assert client.get("/api/projects").json()["projects"] == []


def test_hosted_ignores_physical_adapter_for_all_routes_and_shutdown(tmp_path):
    class ForbiddenAdapter:
        def __getattr__(self, name):
            raise AssertionError(f"Hosted application accessed physical adapter: {name}")

    app = hosted_app(tmp_path / "no-hardware", drone_service=ForbiddenAdapter())
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        status = client.get("/api/drone/status").json()
        assert status["available"] is False and status["state"] == "unavailable"
        assert not any(status["controls"].values())
        assert any(word in status["message"].casefold() for word in ("host", "server"))
        assert "public demo" not in status["message"].casefold()
        assert client.get("/api/drone/ports").json() == {"ports": []}
        for config in ({"transport": "tcp", "host": "127.0.0.1", "port": 14551},
                       {"transport": "udpin", "host": "0.0.0.0", "port": 14551},
                       {"transport": "serial", "device": "COM3"}):
            assert client.post("/api/drone/connect", json=config).status_code == 403
        assert client.post("/api/drone/disconnect").json()["state"] == "unavailable"
        project = client.post("/api/projects", json={"name": "Hosted survey plan"}).json()
        plan = client.post(f"/api/projects/{project['id']}/missions/preview",
                           json={"geometry": polygon()})
        assert plan.status_code == 200, plan.text
        assert plan.json()["plan"]["fileType"] == "Plan"
        assert plan.json()["summary"]["waypoint_count"] >= 4
        assert client.post("/api/logout").status_code == 200
        assert client.get("/api/projects").status_code == 401


def test_hosted_records_exports_and_signing_key_persist_without_bootstrap_secrets(tmp_path, monkeypatch):
    directory = tmp_path / "persisted-hosted"
    app = hosted_app(directory)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        project_response = client.post("/api/projects", json={"name": "Persistent synthetic survey"})
        assert project_response.status_code == 201, project_response.text
        project = project_response.json()
        saved = client.post(f"/api/projects/{project['id']}/features", json=feature_payload())
        assert saved.status_code == 201, saved.text
        feature = saved.json()
        signing_key = app.state.store.setting("report_signing_key")
        # Creation age must not impose the two-hour guest expiry on normal accounts.
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with app.state.store.connection() as database:
            database.execute("UPDATE users SET created_at=?", (old,))
        assert client.get("/api/status").json()["authenticated"] is True
        assert client.post("/api/logout").status_code == 200

    for key in BOOTSTRAP_KEYS:
        monkeypatch.delenv(key)
    reopened = hosted_app(directory)
    with TestClient(reopened, base_url=ORIGIN) as client:
        assert client.get("/api/status").json()["setup_required"] is False
        login(client)
        assert reopened.state.store.setting("report_signing_key") == signing_key
        projects = client.get("/api/projects").json()["projects"]
        assert [item["id"] for item in projects] == [project["id"]]
        records = client.get(f"/api/projects/{project['id']}/features").json()["features"]
        assert records[0]["id"] == feature["id"]
        assert records[0]["geometry"] == feature["geometry"]
        geojson = client.get(f"/api/projects/{project['id']}/export.geojson")
        assert geojson.status_code == 200
        assert geojson.json()["features"][0]["geometry"] == feature["geometry"]
        csv_response = client.get(f"/api/projects/{project['id']}/export.csv")
        assert csv_response.status_code == 200
        rows = list(csv.DictReader(io.StringIO(csv_response.text)))
        assert len(rows) == 1
        assert str(feature["id"]) in rows[0].values()
        assert client.post("/api/demo/start").status_code == 404


def test_restart_never_overwrites_existing_password_from_bootstrap_environment(tmp_path, monkeypatch):
    directory = tmp_path / "no-password-reset"
    hosted_app(directory)
    monkeypatch.setenv("DRONEMAP_BOOTSTRAP_NAME", "Replacement owner")
    monkeypatch.setenv("DRONEMAP_BOOTSTRAP_EMAIL", "replacement@example.test")
    monkeypatch.setenv("DRONEMAP_BOOTSTRAP_PASSWORD", "Replacement-correct-Map!2026")
    app = hosted_app(directory)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/api/login", json={
            "email": EMAIL, "password": "Replacement-correct-Map!2026",
        }).status_code == 401
        original = login(client).json()["user"]
        assert original["name"] == "Hosted Surveyor"
        with app.state.store.connection() as database:
            assert database.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
            assert database.execute("SELECT email FROM users").fetchone()[0] == EMAIL


@pytest.mark.parametrize("mode", ["local", "demo", "hosted"])
def test_health_is_lightweight_and_discloses_no_accounts(tmp_path, mode):
    directory = tmp_path / mode
    app = create_app(directory, imagery_service=NoInference(directory),
                     hosted=mode == "hosted", public_demo=mode == "demo")
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "mode": mode}
        assert EMAIL not in response.text
        assert "password" not in response.text.casefold()
