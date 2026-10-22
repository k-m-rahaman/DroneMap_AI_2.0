"""Geometry regressions use known synthetic rings, not model predictions."""
import copy
import math

import pytest

from geospatial import validate_geometry
from conftest import polygon


def test_hole_is_preserved_and_subtracted_from_geodesic_area():
    outer = polygon()
    full = validate_geometry(outer)
    hole = [[87.7002, 24.0802], [87.7008, 24.0802], [87.7008, 24.0808],
        [87.7002, 24.0808], [87.7002, 24.0802]]
    with_hole = copy.deepcopy(outer)
    with_hole["coordinates"].append(hole)
    result = validate_geometry(with_hole)
    assert len(result["geometry"]["coordinates"]) == 2
    assert 0.63 < result["area_sqm"] / full["area_sqm"] < 0.65
    assert result["perimeter_m"] > full["perimeter_m"]
    assert isinstance(result["area_sqm"], float)


@pytest.mark.parametrize("geometry", [
    {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]]},
    {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [2, 0], [0, 0]]]},
    {"type": "Polygon", "coordinates": [[[181, 0], [182, 0], [182, 1], [181, 0]]]},
    {"type": "Polygon", "coordinates": [[[0, 91], [1, 91], [1, 92], [0, 91]]]},
    {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [math.nan, 1], [0, 0]]]},
    {"type": "Point", "coordinates": [87.7, 24.08]},
])
def test_invalid_or_nonpolygon_geometry_is_rejected(geometry):
    with pytest.raises(ValueError):
        validate_geometry(geometry)


def test_multipolygon_sums_separate_components():
    first = polygon()
    second = copy.deepcopy(first)
    for point in second["coordinates"][0]:
        point[0] += 0.002
    result = validate_geometry({"type": "MultiPolygon", "coordinates": [
        first["coordinates"], second["coordinates"],
    ]})
    one = validate_geometry(first)
    assert result["area_sqm"] == pytest.approx(one["area_sqm"] * 2, rel=1e-5)
