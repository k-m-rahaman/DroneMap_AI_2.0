import copy

import pytest

from conftest import feature_payload
from spatial_quality import project_quality, reconcile_proposals


def building(offset=0, *, kind="building", ident="b"):
    payload = feature_payload(0.0001, feature_type=kind)
    for point in payload["geometry"]["coordinates"][0]:
        point[0] += offset
    return {"type":"Feature", "id":ident, **payload}


def test_reconcile_matches_spatially_across_sources_and_keeps_neighbors():
    saved = building(ident="saved")
    duplicate = building(0.000001, ident="new")
    duplicate["properties"]["source_id"] = "different-map-source"
    adjacent = building(0.0001, ident="next-door")
    answer = reconcile_proposals([duplicate, adjacent], [saved])
    assert [f["id"] for f in answer["features"]] == ["next-door"]
    assert answer["duplicates"][0]["existing_id"] == "saved"
    assert answer["summary"] == {"received":2,"duplicates":1,"kept":1}


def test_reconcile_does_not_suppress_small_nested_buildings_or_parcels():
    nested = building()
    for p in nested["geometry"]["coordinates"][0]:
        p[0] = 87.7 + (p[0]-87.7)/3
        p[1] = 24.08 + (p[1]-24.08)/3
    assert len(reconcile_proposals([nested], [building()])["features"]) == 1
    assert len(reconcile_proposals([building(kind="parcel")], [building()])["features"]) == 1


def test_quality_checks_each_layer_without_flagging_building_inside_parcel():
    result = project_quality([building(ident="a"), building(0.00005,ident="b"), building(kind="parcel",ident="p")])
    assert len(result["issues"]) == 1
    assert result["issues"][0]["kind"] == "overlap"
    assert result["summary"]["layers"]["building"]["overcount_sqm"] > 0
    assert result["summary"]["layers"]["parcel"]["overcount_sqm"] == 0
    assert project_quality([building(),building(0.0001)])["issues"] == []


def test_spatial_checks_do_not_mutate_inputs_and_reject_invalid_geometry():
    original = building()
    expected = copy.deepcopy(original)
    reconcile_proposals([original], [])
    assert original == expected
    bad = building()
    bad["geometry"]["coordinates"][0][1][0] = float("nan")
    with pytest.raises(ValueError):
        reconcile_proposals([bad], [])


def test_reconcile_api_saved_records_remain_unchanged(officer,project):
    saved = officer.post(f"/api/projects/{project['id']}/features",json=feature_payload(0.0001)).json()
    response = officer.post(f"/api/projects/{project['id']}/proposals/reconcile",json={"features":[building()]})
    assert response.status_code == 200, response.text
    assert response.json()["features"] == []
    assert response.json()["duplicates"][0]["existing_id"] == saved["id"]
    assert len(officer.get(f"/api/projects/{project['id']}/features").json()["features"]) == 1


def test_import_preview_is_draft_measured_and_never_auto_saved(officer,project):
    imported = building(kind="parcel")
    imported["properties"].update(status="reviewed",parcel_id="P-42",area_sqm=999)
    endpoint = f"/api/projects/{project['id']}/imports/preview"
    result = officer.post(endpoint,json={"collection":{"type":"FeatureCollection","features":[imported]}})
    assert result.status_code == 200, result.text
    feature = result.json()["features"][0]
    assert feature["properties"]["status"] == "draft"
    assert feature["properties"]["reference_id"] == "P-42"
    assert feature["properties"]["area_sqm"] != 999
    assert officer.get(f"/api/projects/{project['id']}/features").json()["features"] == []
    assert officer.post(endpoint,json={"collection":{"type":"FeatureCollection","crs":{"name":"EPSG:3857"},"features":[imported]}}).status_code == 422


def test_quality_api_reports_overlap_and_keeps_claims_private(officer,project):
    for b in (building(),building(0.00005)):
        assert officer.post(f"/api/projects/{project['id']}/features",json=b).status_code == 201
    result = officer.get(f"/api/projects/{project['id']}/quality")
    assert result.status_code == 200, result.text
    assert len(result.json()["issues"]) == 1
    officer.post('/api/logout')
    assert officer.get(f"/api/projects/{project['id']}/quality").status_code == 401
    assert officer.post(f"/api/projects/{project['id']}/proposals/reconcile",json={"features":[]}).status_code == 401
