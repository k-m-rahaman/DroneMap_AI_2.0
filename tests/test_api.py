"""Regression tests for authenticated storage, edits, exports, and honest errors."""
import csv
import io
import json
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from PIL import Image

from conftest import feature_payload


def test_setup_login_logout_and_http_only_cookie(client):
    status = client.get("/api/status").json()
    assert status["setup_required"] is True
    assert status["authenticated"] is False
    assert client.get("/api/projects").status_code == 401
    setup = client.post("/api/setup", json={
        "name": "Officer", "email": "officer@example.test", "password": "Correct-horse-Map!2026",
    })
    assert setup.status_code in (200, 201), setup.text
    assert "httponly" in setup.headers.get("set-cookie", "").lower()
    assert client.get("/api/status").json()["authenticated"] is True
    again = client.post("/api/setup", json={
        "name": "Intruder", "email": "intruder@example.test", "password": "Correct-horse-Map!2026",
    })
    assert again.status_code in (403, 409)
    assert client.post("/api/logout").status_code in (200, 204)
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/login", json={
        "email": "officer@example.test", "password": "wrong-password",
    }).status_code == 401
    assert client.post("/api/login", json={
        "email": "officer@example.test", "password": "Correct-horse-Map!2026",
    }).status_code == 200


def test_weak_first_account_password_is_rejected(client):
    response = client.post("/api/setup", json={
        "name": "Officer", "email": "officer@example.test", "password": "1234",
    })
    assert response.status_code == 422
    assert client.get("/api/status").json()["setup_required"] is True


def test_anonymous_workspace_mutations_are_rejected(client):
    assert client.post("/api/projects", json={"name": "Unauthorized"}).status_code == 401
    assert client.post("/api/projects/missing/features", json=feature_payload()).status_code == 401
    assert client.put("/api/features/missing", json=feature_payload(version=1)).status_code == 401
    assert client.delete("/api/features/missing").status_code == 401


def test_saved_geometry_has_server_area_and_version(officer, project, feature):
    properties = feature["properties"]
    assert 10000 < properties["area_sqm"] < 12000
    assert properties["perimeter_m"] > 400
    assert properties["version"] == 1
    collection = officer.get(f"/api/projects/{project['id']}/features").json()
    assert collection["type"] == "FeatureCollection"
    assert collection["features"][0]["geometry"] == feature["geometry"]


def test_client_measurements_are_not_authoritative(officer, project):
    payload = feature_payload(area_sqm=-12345, perimeter_m=-100)
    response = officer.post(f"/api/projects/{project['id']}/features", json=payload)
    assert response.status_code in (200, 201), response.text
    properties = response.json()["properties"]
    assert 10000 < properties["area_sqm"] < 12000
    assert properties["perimeter_m"] > 400


def test_edit_updates_geometry_and_rejects_stale_version(officer, feature):
    feature_id = feature["id"]
    version = feature["properties"]["version"]
    changed = feature_payload(0.002, version=version, name="Edited footprint")
    response = officer.put(f"/api/features/{feature_id}", json=changed)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["geometry"] != feature["geometry"]
    assert updated["properties"]["version"] == version + 1
    ratio = updated["properties"]["area_sqm"] / feature["properties"]["area_sqm"]
    assert 3.99 < ratio < 4.01
    assert officer.put(f"/api/features/{feature_id}", json=changed).status_code == 409


def test_delete_removes_persisted_feature(officer, project, feature):
    stale = officer.delete(f"/api/features/{feature['id']}?version={feature['properties']['version']+1}")
    assert stale.status_code == 409
    response = officer.delete(f"/api/features/{feature['id']}?version={feature['properties']['version']}")
    assert response.status_code in (200, 204), response.text
    assert officer.get(f"/api/projects/{project['id']}/features").json()["features"] == []


def test_restart_retains_accounts_projects_and_features(app_factory, officer, project, feature):
    with TestClient(app_factory()) as reopened:
        assert reopened.get("/api/status").json()["setup_required"] is False
        login = reopened.post("/api/login", json={
            "email": "surveyor@example.test", "password": "Correct-horse-Map!2026",
        })
        assert login.status_code == 200, login.text
        records = reopened.get(f"/api/projects/{project['id']}/features").json()["features"]
        assert [item["id"] for item in records] == [feature["id"]]


def test_invalid_polygon_is_rejected_without_poisoning_records(officer, project):
    payload = feature_payload()
    payload["geometry"]["coordinates"] = [[[87.7, 24.08], [87.702, 24.082],
        [87.7, 24.082], [87.702, 24.08], [87.7, 24.08]]]
    response = officer.post(f"/api/projects/{project['id']}/features", json=payload)
    assert response.status_code == 422, response.text
    assert officer.get(f"/api/projects/{project['id']}/features").json()["features"] == []


def test_geojson_and_csv_exports(officer, project, feature):
    geojson = officer.get(f"/api/projects/{project['id']}/export.geojson")
    assert geojson.status_code == 200, geojson.text
    assert geojson.json()["features"][0]["geometry"] == feature["geometry"]
    csv_response = officer.get(f"/api/projects/{project['id']}/export.csv")
    assert csv_response.status_code == 200, csv_response.text
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(rows) == 1
    assert any(str(feature["id"]) == value for value in rows[0].values())


def test_report_has_public_verification_and_immutable_snapshot(officer, feature):
    report = officer.get(f"/api/features/{feature['id']}/report.pdf")
    assert report.status_code == 200, report.text[:400]
    assert report.content.startswith(b"%PDF-")
    verification = report.headers.get("X-Verification-URL")
    assert verification, "PDF must expose its server verification URL"
    token = urlparse(verification).path.rstrip("/").split("/")[-1]
    before = officer.get(f"/api/public/reports/{token}")
    assert before.status_code == 200, before.text
    original = before.json()
    assert "raster_path" not in json.dumps(original)
    assert "preview_path" not in json.dumps(original)
    edited = officer.put(f"/api/features/{feature['id']}", json=feature_payload(
        0.002, version=feature["properties"]["version"], name="New revision"))
    assert edited.status_code == 200, edited.text
    assert officer.post("/api/logout").status_code in (200, 204)
    public = officer.get(f"/api/public/reports/{token}")
    assert public.status_code == 200
    assert public.json()["feature"] == original["feature"]
    assert public.json()["report"]["changed_since_report"] is True
    # Modifying a signed report identifier/signature must not expose a snapshot.
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert officer.get(f"/api/public/reports/{tampered}").status_code in (403, 404)


def test_private_rows_cannot_be_accessed_by_another_account(officer, project, feature):
    """Seed a second user to exercise authorization, not account creation UI."""
    from main import password_hash
    from storage import new_id, now

    with officer.app.state.store.connection() as database:
        database.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (
            new_id(), "Other Officer", "other@example.test",
            password_hash("Other-correct-Map!2026"), "officer", now(),
        ))
    officer.post("/api/logout")
    assert officer.post("/api/login", json={
        "email": "other@example.test", "password": "Other-correct-Map!2026",
    }).status_code == 200
    assert officer.get(f"/api/projects/{project['id']}/features").status_code == 404
    assert officer.post(f"/api/projects/{project['id']}/features", json=feature_payload()).status_code == 404
    assert officer.put(f"/api/features/{feature['id']}", json=feature_payload(version=1)).status_code == 404
    assert officer.delete(f"/api/features/{feature['id']}").status_code == 404
    assert officer.get(f"/api/features/{feature['id']}/report.pdf").status_code == 404
    assert officer.get(f"/api/projects/{project['id']}/export.geojson").status_code == 404
    assert officer.get("/api/projects").json()["projects"] == []


def test_multipolygon_report_is_supported(officer, project):
    import copy

    payload = feature_payload()
    first = payload["geometry"]["coordinates"]
    second = copy.deepcopy(first)
    for point in second[0]:
        point[0] += 0.002
    payload["geometry"] = {"type": "MultiPolygon", "coordinates": [first, second]}
    response = officer.post(f"/api/projects/{project['id']}/features", json=payload)
    assert response.status_code in (200, 201), response.text
    report = officer.get(f"/api/features/{response.json()['id']}/report.pdf")
    assert report.status_code == 200, report.text[:400]
    assert report.content.startswith(b"%PDF-")


def test_missing_model_is_an_error_without_generated_features(officer, project):
    assert officer.get("/api/status").json()["model"]["available"] is False
    response = officer.post(f"/api/projects/{project['id']}/scans/map", json={
        "bounds": [87.7, 24.08, 87.701, 24.081], "confidence": 0.35,
    })
    assert response.status_code == 503, response.text
    assert "geojson" not in response.json()
    assert officer.get(f"/api/projects/{project['id']}/features").json()["features"] == []


def test_cross_origin_write_with_session_is_rejected(officer):
    response = officer.post("/api/projects", json={"name": "Cross-origin attempt"},
        headers={"Origin": "https://unrelated.example"})
    assert response.status_code == 403
    assert officer.get("/api/projects").json()["projects"] == []


def test_bengali_report_text_does_not_crash_pdf_generation(officer, project):
    payload = feature_payload(name="জরিপ ভবন", owner="রহিম")
    response = officer.post(f"/api/projects/{project['id']}/features", json=payload)
    assert response.status_code in (200, 201), response.text
    report = officer.get(f"/api/features/{response.json()['id']}/report.pdf")
    assert report.status_code == 200, report.text[:400]
    assert report.content.startswith(b"%PDF-")


def test_plain_image_needs_explicit_extent(officer, project):
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(image, format="PNG")
    endpoint = f"/api/projects/{project['id']}/sources"
    files = {"file": ("synthetic.png", image.getvalue(), "image/png")}
    assert officer.post(endpoint, files=files).status_code == 422
    response = officer.post(endpoint, files=files, data={
        "bounds": json.dumps([87.7, 24.08, 87.701, 24.081]),
    })
    assert response.status_code in (200, 201), response.text
    source = response.json()
    assert source["width"] > 0 and source["height"] > 0
    assert officer.get(f"/api/sources/{source['id']}/preview").status_code == 200
    # The authenticated source DTO must not expose server filesystem paths.
    assert "raster_path" not in source and "preview_path" not in source
    saved = officer.post(f"/api/projects/{project['id']}/features", json=feature_payload(source_id=source["id"]))
    assert saved.status_code in (200, 201), saved.text
    report = officer.get(f"/api/features/{saved.json()['id']}/report.pdf")
    assert report.status_code == 200, report.text[:400]
    token = urlparse(report.headers["X-Verification-URL"]).path.rstrip("/").split("/")[-1]
    shared = officer.get(f"/api/public/reports/{token}").json()
    assert shared["source"]["name"] == source["name"]
    assert "raster_path" not in json.dumps(shared)
    assert "preview_path" not in json.dumps(shared)
    assert "surveyor@example.test" not in json.dumps(shared)
