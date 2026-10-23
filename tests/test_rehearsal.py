"""Rehearsal safety and evidence checks, independent of any model accuracy claim."""
import copy
import json
import os
import socket
import sys
from urllib.request import urlopen

import httpx
import numpy as np
from PIL import Image
import pytest
import rasterio
try:
    import requests
except ImportError:
    requests = None

from tools import rehearse_sih as rehearsal


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(rehearsal, "ROOT", tmp_path)
    monkeypatch.setattr(rehearsal, "PRIVATE_RUNS", tmp_path / ".demo-runtime" / "rehearsals")
    sources = tmp_path / ".demo-runtime" / "visitor-data" / "sources"
    sources.mkdir(parents=True)
    image = sources / "sample.png"
    pixels = np.arange(20 * 30 * 3, dtype=np.uint8).reshape(20, 30, 3)
    Image.fromarray(pixels).save(image)
    metadata = {"kind": "map", "resampled": False, "width": 30, "height": 20,
                "raster_path": str(image), "crs": "EPSG:3857", "transform": [0.3, 0, 100, 0, -0.3, 200]}
    sidecar = sources / "sample.json"
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata, image, sidecar, pixels


def test_only_fresh_ignored_descendants_can_be_reserved(tmp_path, monkeypatch):
    sandbox = tmp_path / ".demo-runtime" / "rehearsals"
    monkeypatch.setattr(rehearsal, "PRIVATE_RUNS", sandbox)
    for target in (sandbox, tmp_path / "data", sandbox / ".." / "elsewhere"):
        with pytest.raises(rehearsal.RehearsalError, match="subdirectory"):
            rehearsal.reserve_run(target)
    chosen = rehearsal.reserve_run(sandbox / "first")
    (chosen / "keep.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="already exists"):
        rehearsal.reserve_run(chosen)
    assert (chosen / "keep.txt").read_text() == "preserve"


def test_reject_private_source_and_external_raster(sample, tmp_path):
    metadata, _, sidecar, _ = sample
    private_sidecar = tmp_path / "data" / "source.json"
    private_sidecar.parent.mkdir()
    private_sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="explicit public-demo"):
        rehearsal.read_sample(private_sidecar)
    metadata["raster_path"] = str(private_sidecar)
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="beside its public-demo"):
        rehearsal.read_sample(sidecar)


@pytest.mark.parametrize("change", [
    {"kind": "upload"}, {"resampled": True}, {"resampled": None},
    {"transform": [True, 0, 100, 0, -0.3, 200]},
    {"transform": [0, 0, 100, 0, 0, 200]}, {"transform": [float("inf"), 0, 100, 0, -0.3, 200]},
])
def test_reject_mislabeled_or_invalid_sample(sample, change):
    metadata, _, sidecar, _ = sample
    metadata.update(change)
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError):
        rehearsal.read_sample(sidecar)


def test_derived_map_geotiff_retains_pixels_crs_and_affine(sample, tmp_path):
    metadata, image, _, pixels = sample
    target = tmp_path / "derived.tif"
    assert rehearsal.create_derived_geotiff(metadata, image, target) == (30, 20)
    with rasterio.open(target) as dataset:
        assert np.array_equal(dataset.read(), pixels.transpose(2, 0, 1))
        assert dataset.crs.to_string() == metadata["crs"]
        assert list(dataset.transform)[:6] == metadata["transform"]
        assert "not drone capture" in dataset.tags()["REHEARSAL_DERIVATION"]
    metadata["width"] += 1
    with pytest.raises(rehearsal.RehearsalError, match="dimensions"):
        rehearsal.create_derived_geotiff(metadata, image, tmp_path / "bad.tif")


def test_offline_guard_blocks_http_dns_socket_and_restores_transport():
    original = socket.getaddrinfo
    with pytest.raises(rehearsal.RehearsalError, match="external network request was attempted"), rehearsal.offline_only():
        operations = [lambda: socket.getaddrinfo("example.test", 443),
                      lambda: urlopen("https://example.test"),
                      lambda: httpx.get("https://example.test")]
        if requests is not None:
            operations.append(lambda: requests.get("https://example.test"))
        for operation in operations:
            with pytest.raises(rehearsal.RehearsalError, match="Network access is disabled"):
                operation()
        with socket.socket() as connection:
            with pytest.raises(rehearsal.RehearsalError):
                connection.connect(("127.0.0.1", 12345))
    assert socket.getaddrinfo is original


def test_offline_guard_does_not_require_optional_requests(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)
    with rehearsal.offline_only() as attempts:
        assert attempts["blocked"] == 0


def test_offline_guard_still_allows_in_process_application_calls():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()

    @app.get("/check")
    def check():
        return {"mode": "in-process"}

    with rehearsal.offline_only(), TestClient(app) as client:
        assert client.get("/check").json() == {"mode": "in-process"}


def test_pdf_structure_detects_valid_objects_and_corrupted_offsets():
    from fpdf import FPDF
    document = FPDF()
    document.add_page()
    document.set_font("Helvetica", size=10)
    document.cell(text="Synthetic rehearsal PDF")
    content = bytes(document.output())
    result = rehearsal.pdf_structure(content)
    assert result["pages"] == 1
    assert result["visual_layout_verified"] is False
    with pytest.raises(rehearsal.RehearsalError, match="cross-reference"):
        rehearsal.pdf_structure(content.replace(b"startxref\n", b"startxref\n1", 1))
    with pytest.raises(rehearsal.RehearsalError, match="header"):
        rehearsal.pdf_structure(content[:-10])


def test_unsafe_report_target_is_rejected_before_work(monkeypatch, tmp_path):
    def forbidden(*args):
        raise AssertionError("No rehearsal should start for an invalid report target")
    monkeypatch.setattr(rehearsal, "run_rehearsal", forbidden)
    assert rehearsal.main(["--report-prefix", str(tmp_path / "outside")]) == 2


def test_failed_workflow_preserves_evidence_and_restores_environment(sample, tmp_path, monkeypatch):
    _, _, sidecar, _ = sample
    model = tmp_path / "local-model.pt"
    model.write_bytes(b"Fixture path only; never loaded")
    monkeypatch.setenv("DRONEMAP_DATA_DIR", "original-application-setting")
    monkeypatch.delenv("YOLO_OFFLINE", raising=False)

    def fail(*args):
        raise rehearsal.RehearsalError("Deliberate fixture failure containing a private local path")

    monkeypatch.setattr(rehearsal, "create_derived_geotiff", fail)
    report, directory = rehearsal.run_rehearsal(rehearsal.PRIVATE_RUNS / "failure-evidence", sidecar, model)
    assert report["passed"] is False
    assert "failure" in report
    assert "private local path" not in json.dumps(report)
    assert "private local path" in (directory / "failure.txt").read_text()
    assert (directory / "workspace" / "workspace.sqlite3").is_file()
    assert os.environ["DRONEMAP_DATA_DIR"] == "original-application-setting"
    assert "YOLO_OFFLINE" not in os.environ


def test_sanitized_markdown_never_claims_accuracy():
    report = {"passed": True, "notice": rehearsal.NOTICE, "created_at": "2026-10-23",
              "checks": [{"name": "real_offline_inference"}], "metrics": {"building_proposals": 72}, "limitations": []}
    markdown = rehearsal.markdown_report(copy.deepcopy(report))
    assert "no measured detection accuracy" in markdown
    assert "real offline inference" in markdown
