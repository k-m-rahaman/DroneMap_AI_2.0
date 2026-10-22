"""Professional workspace regressions; all coordinates and users are synthetic.

These exercise actual authorization, persistence and planning. No test pretends
that a MAVLink connection, imagery capture or physical flight has occurred.
"""
from fastapi.testclient import TestClient

from conftest import feature_payload, polygon


def sign_in_as_other(officer):
    """Provision a second account directly to test private project ownership."""
    from main import password_hash
    from storage import new_id, now

    with officer.app.state.store.connection() as database:
        database.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (
            new_id(), "Other Surveyor", "other-workspace@example.test",
            password_hash("Other-workspace-map!2026"), "officer", now(),
        ))
    assert officer.post("/api/logout").status_code == 200
    response = officer.post("/api/login", json={
        "email": "other-workspace@example.test", "password": "Other-workspace-map!2026",
    })
    assert response.status_code == 200, response.text


def test_project_edit_retains_records_counts_and_restart(officer, project, feature, app_factory):
    update = {
        "name": "Ward 12 survey", "location": "Synthetic ward",
        "description": "Buildings reviewed against supplied orthomosaic",
    }
    response = officer.put(f"/api/projects/{project['id']}", json=update)
    assert response.status_code == 200, response.text
    updated = response.json()
    for key, value in update.items():
        assert updated[key] == value
    assert updated["id"] == project["id"]
    assert updated["feature_count"] == 1
    assert updated["source_count"] == 0
    assert officer.get(f"/api/projects/{project['id']}/features").json()["features"][0]["id"] == feature["id"]

    with TestClient(app_factory()) as reopened:
        assert reopened.post("/api/login", json={
            "email": "surveyor@example.test", "password": "Correct-horse-Map!2026",
        }).status_code == 200
        saved = reopened.get("/api/projects").json()["projects"][0]
        assert saved["name"] == update["name"]
        assert saved["feature_count"] == 1


def test_project_empty_name_cannot_erase_saved_details(officer, project):
    for name in ("", "   "):
        response = officer.put(f"/api/projects/{project['id']}", json={"name": name})
        assert response.status_code == 422, response.text
    assert officer.get("/api/projects").json()["projects"][0]["name"] == project["name"]


def test_feature_history_retains_original_geometry_and_actor(officer, feature):
    feature_id = feature["id"]
    revised = feature_payload(0.002, version=1, name="Reviewed building", status="reviewed")
    changed = officer.put(f"/api/features/{feature_id}", json=revised)
    assert changed.status_code == 200, changed.text

    response = officer.get(f"/api/features/{feature_id}/history")
    assert response.status_code == 200, response.text
    entries = response.json()["history"]
    assert len(entries) == 2
    by_version = {entry["version"]: entry for entry in entries}
    assert set(by_version) == {1, 2}
    assert by_version[1]["snapshot"]["geometry"] == feature["geometry"]
    assert by_version[2]["snapshot"]["geometry"] == revised["geometry"]
    assert by_version[2]["snapshot"]["properties"]["status"] == "reviewed"
    for entry in entries:
        assert entry["action"]
        assert entry["timestamp"]
        assert entry["user_name"] == "Test Surveyor"
        assert "password_hash" not in entry and "email" not in entry


def test_project_edit_and_history_are_private(officer, project, feature):
    sign_in_as_other(officer)
    assert officer.put(f"/api/projects/{project['id']}", json={"name": "Hijacked"}).status_code == 404
    assert officer.get(f"/api/features/{feature['id']}/history").status_code == 404


def test_anonymous_professional_workspace_routes_require_sign_in(client):
    assert client.put("/api/projects/missing", json={"name": "Unauthorized"}).status_code == 401
    assert client.get("/api/features/missing/history").status_code == 401
    assert client.get("/api/drone/status").status_code == 401
    assert client.get("/api/drone/ports").status_code == 401
    assert client.post("/api/drone/connect", json={"transport": "udpin", "port": 14551}).status_code == 401
    assert client.post("/api/drone/disconnect").status_code == 401
    assert client.post("/api/projects/missing/missions/preview", json={"geometry": polygon()}).status_code == 401


def test_another_account_cannot_plan_mission_for_private_project(officer, project):
    sign_in_as_other(officer)
    response = officer.post(f"/api/projects/{project['id']}/missions/preview", json={"geometry": polygon()})
    assert response.status_code == 404, response.text


def test_drone_status_is_honestly_disconnected_without_hardware(officer):
    response = officer.get("/api/drone/status")
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["connected"] is False
    assert status["state"] in ("idle", "dependency_missing", "disconnected", "unavailable")
    assert status["connection"] is None
    assert status["telemetry"] == {}
    assert set(status["supported_transports"]) == {"udpin", "tcp", "serial"}
    assert status["controls"] == {
        "can_arm": False, "can_upload_mission": False, "can_capture": False,
    }
    assert status["message"]
    ports = officer.get("/api/drone/ports")
    assert ports.status_code == 200, ports.text
    assert isinstance(ports.json()["ports"], list)
    assert officer.post("/api/drone/disconnect").status_code == 200
    assert officer.get("/api/drone/status").json()["connected"] is False


def test_mission_preview_returns_real_geometry_and_downloadable_plan(officer, project):
    body = {
        "geometry": polygon(), "altitude_m": 60, "speed_m_s": 5,
        "front_overlap": 0.8, "side_overlap": 0.7,
        "sensor_width_mm": 13.2, "sensor_height_mm": 8.8,
        "focal_length_mm": 8.8, "image_width_px": 5472, "image_height_px": 3648,
        "angle_deg": 0,
    }
    response = officer.post(f"/api/projects/{project['id']}/missions/preview", json=body)
    assert response.status_code == 200, response.text
    mission = response.json()
    summary = mission["summary"]
    assert 10000 < summary["area_sqm"] < 12000
    assert summary["route_distance_m"] > 100
    assert summary["estimated_flight_seconds"] > 0
    assert summary["estimated_photos"] > 1
    assert summary["gsd_cm"] > 0
    assert summary["line_spacing_m"] > 0
    assert summary["trigger_spacing_m"] > 0
    assert summary["waypoint_count"] > 2
    assert mission["geojson"]["type"] == "FeatureCollection"
    assert any(item["geometry"]["type"] == "LineString" for item in mission["geojson"]["features"])
    assert mission["plan"]["fileType"] == "Plan"
    assert mission["plan"]["mission"]["items"]
    assert mission["limitations"]
    # Planning is a preview, never a claim that flight or capture has occurred.
    assert officer.get("/api/drone/status").json()["connected"] is False
    assert officer.get(f"/api/projects/{project['id']}/features").json()["features"] == []


def test_mission_camera_and_altitude_validation(officer, project):
    endpoint = f"/api/projects/{project['id']}/missions/preview"
    for fields in ({"altitude_m": 0}, {"altitude_m": 121}, {"speed_m_s": 0},
                   {"front_overlap": 1}, {"side_overlap": -0.1},
                   {"focal_length_mm": 0}, {"image_width_px": 0}):
        response = officer.post(endpoint, json={"geometry": polygon(), **fields})
        assert response.status_code == 422, response.text


def test_mission_rejects_holes_and_nonconvex_survey_area(officer, project):
    with_hole = polygon(0.002)
    x, y = 87.7, 24.08
    with_hole["coordinates"].append([
        [x + 0.0005, y + 0.0005], [x + 0.0005, y + 0.001],
        [x + 0.001, y + 0.001], [x + 0.001, y + 0.0005],
        [x + 0.0005, y + 0.0005],
    ])
    nonconvex = {"type": "Polygon", "coordinates": [[
        [x, y], [x + 0.002, y], [x + 0.002, y + 0.001],
        [x + 0.001, y + 0.001], [x + 0.001, y + 0.002],
        [x, y + 0.002], [x, y],
    ]]}
    for geometry in (with_hole, nonconvex):
        response = officer.post(f"/api/projects/{project['id']}/missions/preview", json={"geometry": geometry})
        assert response.status_code == 422, response.text
        assert response.json()["detail"]


def test_drone_routes_keep_accounts_separate_and_logout_releases_link(tmp_path):
    """An explicit fake adapter checks auth routing, without a socket or drone."""
    from conftest import NoInference
    from main import create_app, password_hash
    from storage import new_id, now

    class FakeTelemetryAdapter:
        def __init__(self):
            self.links = {}

        def status(self, user_id):
            return {"connected": user_id in self.links,
                    "connection": self.links.get(user_id), "telemetry": {}}

        def ports(self):
            return []

        def connect(self, user_id, configuration):
            self.links[user_id] = configuration
            return self.status(user_id)

        def disconnect(self, user_id):
            self.links.pop(user_id, None)
            return self.status(user_id)

        def close(self):
            self.links.clear()

    directory = tmp_path / "isolated-routing-check"
    adapter = FakeTelemetryAdapter()
    application = create_app(directory, NoInference(directory), drone_service=adapter)
    with TestClient(application) as owner:
        assert owner.post("/api/setup", json={
            "name": "Link Owner", "email": "owner-link@example.test",
            "password": "Owner-telemetry-map!2026",
        }).status_code == 201
        with application.state.store.connection() as database:
            database.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (
                new_id(), "Other Surveyor", "other-link@example.test",
                password_hash("Other-telemetry-map!2026"), "officer", now(),
            ))
        other = TestClient(application)
        try:
            assert other.post("/api/login", json={
                "email": "other-link@example.test", "password": "Other-telemetry-map!2026",
            }).status_code == 200
            response = owner.post("/api/drone/connect", json={
                "transport": "udpin", "host": "127.0.0.1", "port": 14551,
            })
            assert response.status_code == 202, response.text
            assert owner.get("/api/drone/status").json()["connected"] is True
            private_status = other.get("/api/drone/status").json()
            assert private_status["connected"] is False
            assert private_status["connection"] is None
            assert other.post("/api/drone/disconnect").status_code == 200
            assert owner.get("/api/drone/status").json()["connected"] is True
            assert owner.post("/api/logout").status_code == 200
            assert adapter.links == {}
        finally:
            other.close()
