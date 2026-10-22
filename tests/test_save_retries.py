"""A dropped response must not create another house on retry."""
from conftest import feature_payload


def test_creation_retry_is_persistent_and_unique(officer, project, app_factory):
    from fastapi.testclient import TestClient
    endpoint = f"/api/projects/{project['id']}/features"
    headers = {"Idempotency-Key": "draft-house-001"}
    first = officer.post(endpoint, json=feature_payload(), headers=headers)
    assert first.status_code == 201
    with TestClient(app_factory()) as reopened:
        reopened.cookies.update(officer.cookies)
        second = reopened.post(endpoint, json=feature_payload(), headers=headers)
        assert second.status_code == 201
        assert second.json()['id'] == first.json()['id']
        assert len(reopened.get(endpoint).json()['features']) == 1


def test_creation_key_cannot_silently_save_different_geometry(officer, project):
    endpoint = f"/api/projects/{project['id']}/features"
    headers = {"Idempotency-Key": "draft-house-002"}
    assert officer.post(endpoint, json=feature_payload(), headers=headers).status_code == 201
    assert officer.post(endpoint, json=feature_payload(size=0.002), headers=headers).status_code == 409
    assert len(officer.get(endpoint).json()['features']) == 1


def test_deleted_house_is_not_recreated_by_retry(officer, project):
    endpoint = f"/api/projects/{project['id']}/features"
    headers = {"Idempotency-Key": "draft-house-003"}
    first = officer.post(endpoint, json=feature_payload(), headers=headers).json()
    assert officer.delete(f"/api/features/{first['id']}?version=1").status_code == 200
    assert officer.post(endpoint, json=feature_payload(), headers=headers).status_code == 409
    assert officer.get(endpoint).json()['features'] == []


def test_delete_requires_current_version(officer, feature):
    endpoint = f"/api/features/{feature['id']}"
    assert officer.delete(endpoint).status_code == 422
    assert officer.delete(endpoint + '?version=2').status_code == 409
    assert officer.delete(endpoint + '?version=1').status_code == 200


def test_saved_edge_warnings_survive_reload(officer, project):
    endpoint = f"/api/projects/{project['id']}/features"
    payload = feature_payload(quality_flags=['source_image_edge'],touches_source_edge=True,mask_area_px=380.25)
    response = officer.post(endpoint, json=payload)
    assert response.status_code == 201
    properties = officer.get(endpoint).json()['features'][0]['properties']
    assert properties['quality_flags'] == ['source_image_edge']
    assert properties['touches_source_edge'] is True
    assert properties['mask_area_px'] == 380.25
