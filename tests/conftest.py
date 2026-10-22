"""Isolated API fixtures: real storage/geometry, deliberately unavailable AI."""
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from imagery import SurveyImagery
from main import create_app


class NoInference:
    """Upload inspection is real; scan failures are deliberate, never inference."""

    def __init__(self, directory):
        self.real = SurveyImagery(directory, Path(directory) / "not-a-model.pt")

    def readiness(self):
        return {"available": False, "state": "missing_weights", "message": "AI disabled in tests"}

    def inspect_upload(self, contents, filename, bounds=None):
        return self.real.inspect_upload(contents, filename, bounds)

    def scan_map(self, bounds, confidence=0.35, min_area_sqm=4):
        raise RuntimeError("AI disabled in tests; no fabricated boundaries")

    def scan_source(self, source, confidence=0.35, min_area_sqm=4):
        raise RuntimeError("AI disabled in tests; no fabricated boundaries")


@pytest.fixture
def app_factory(tmp_path):
    directory = tmp_path / "workspace"

    def factory():
        return create_app(data_dir=directory, imagery_service=NoInference(directory))

    return factory


@pytest.fixture
def client(app_factory):
    with TestClient(app_factory()) as test_client:
        yield test_client


@pytest.fixture
def officer(client):
    response = client.post("/api/setup", json={
        "name": "Test Surveyor",
        "email": "surveyor@example.test",
        "password": "Correct-horse-Map!2026",
    })
    assert response.status_code in (200, 201), response.text
    return client


@pytest.fixture
def project(officer):
    response = officer.post("/api/projects", json={
        "name": "Synthetic survey",
        "location": "Test coordinates only",
        "description": "Not a field survey",
    })
    assert response.status_code in (200, 201), response.text
    return response.json()


def polygon(size=0.001):
    x, y = 87.7, 24.08
    return {"type": "Polygon", "coordinates": [[
        [x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y],
    ]]}


def feature_payload(size=0.001, **properties):
    return {"geometry": polygon(size), "properties": {
        "name": "Test footprint", "owner": "", "feature_type": "building",
        "status": "draft", "notes": "Synthetic fixture", **properties,
    }}


@pytest.fixture
def feature(officer, project):
    response = officer.post(f"/api/projects/{project['id']}/features", json=feature_payload())
    assert response.status_code in (200, 201), response.text
    return response.json()
