"""Synthetic workflow checks only: these fixtures measure no model/field accuracy."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import benchmark_workspace as benchmark


def polygon(x=88.3602, y=22.5702, identifier="synthetic-p"):
    return {"type": "Feature", "id": identifier, "properties": {"synthetic": True}, "geometry": {
        "type": "Polygon", "coordinates": [[[x, y], [x + .0001, y],
            [x + .0001, y + .0001], [x, y + .0001], [x, y]]]}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return path


@pytest.fixture
def inputs(tmp_path):
    return {
        "predictions_path": write_json(tmp_path / "predictions.json", collection(polygon())),
        "source_path": write_json(tmp_path / "source.json", {"name": "SYNTHETIC ONLY", "kind": "synthetic_fixture",
            "crs": "EPSG:4326", "attribution": "Invented test geometry; no imagery acquisition",
            "width": 100, "height": 100, "bounds": [88.36, 22.57, 88.361, 22.571]}),
        "settings_path": write_json(tmp_path / "settings.json", {"confidence_threshold": .5, "minimum_area_sqm": 4,
            "synthetic_test": True, "other_setting": None}),
        "model_path": tmp_path / "synthetic-not-a-model.bin",
        "aoi": [88.36, 22.57, 88.361, 22.571], "area_name": "Synthetic test; not accuracy evidence",
        "output": tmp_path / "benchmark",
    }


@pytest.fixture
def workspace(inputs):
    inputs["model_path"].write_bytes(b"SYNTHETIC: never loaded as a model")
    return benchmark.prepare(**inputs)


def reviewed(workspace, references=None, **changes):
    write_json(workspace / "references.geojson", references or collection(polygon(x=88.36022, identifier="synthetic-r")))
    review = {"labeler": "Synthetic fixture labeler", "reviewer": "Synthetic fixture reviewer",
        "labeled_at": "2026-10-20T10:00:00+05:30", "reviewed_at": "2026-10-20T11:00:00+05:30",
        "labeling_method": "SYNTHETIC test fixture only", "imagery_observed": "No imagery; invented test geometry",
        "independent_of_predictions": True, "coverage_complete": True,
        "reviewer_checked_all_buildings": True, "boundary_policy_acknowledged": True,
        "notes": "Declarations are synthetic test input and not completed human review."}
    review.update(changes)
    write_json(workspace / "review.json", review)
    return review


def test_prepare_freezes_inputs_and_never_creates_reference_labels(workspace, inputs):
    manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    assert json.loads((workspace / "references.geojson").read_text())["features"] == []
    review = json.loads((workspace / "review.json").read_text())
    assert review["independent_of_predictions"] is False
    assert review["labeler"] == review["reviewer"] == ""
    assert manifest["model"]["sha256"] == hashlib.sha256(inputs["model_path"].read_bytes()).hexdigest()
    assert not (workspace / inputs["model_path"].name).exists()
    for name, fingerprint in manifest["frozen_files"].items():
        data = (workspace / name).read_bytes()
        assert fingerprint == {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    instructions = (workspace / "LABELING_INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "Hide `predictions.geojson`" in instructions
    assert "NOT EVALUATED" in instructions


def test_empty_references_fail_without_any_partial_report(workspace, tmp_path):
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="References are empty"):
        benchmark.evaluate(workspace, output=output)
    assert not output.exists()


def test_reviewed_synthetic_inputs_report_both_thresholds(workspace, tmp_path):
    reviewed(workspace)
    output = benchmark.evaluate(workspace, output=tmp_path / "report")
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert set(report["thresholds"]) == {"0.50", "0.75"}
    assert report["thresholds"]["0.50"]["true_positives"] == 1
    assert report["thresholds"]["0.75"]["true_positives"] == 0
    assert report["thresholds"]["0.75"]["false_negatives"] == 1
    assert report["thresholds"]["0.75"]["mean_matched_iou"] is None
    assert report["settings"]["synthetic_test"] is True
    assert (output / "evaluated-references.geojson").read_bytes() == (workspace / "references.geojson").read_bytes()
    assert set(report["input_hashes"]) >= {"references.geojson", "review.json", "manifest.json", "aoi.geojson"}
    page = (output / "report.html").read_text(encoding="utf-8")
    assert "Undefined" in page and "not a field-accuracy certification" in page
    assert "accuracy" not in report


@pytest.mark.parametrize("field", ["independent_of_predictions", "coverage_complete", "reviewer_checked_all_buildings", "boundary_policy_acknowledged"])
@pytest.mark.parametrize("value", [False, "true", 1])
def test_every_review_attestation_must_be_literal_true(workspace, tmp_path, field, value):
    reviewed(workspace, **{field: value})
    with pytest.raises(ValueError, match=field):
        benchmark.evaluate(workspace, output=tmp_path / "report")
    assert not (tmp_path / "report").exists()


@pytest.mark.parametrize("changes,message", [
    ({"reviewer": "  SYNTHETIC FIXTURE LABELER "}, "different reviewer"),
    ({"labeling_method": ""}, "labeling_method"),
    ({"imagery_observed": ""}, "imagery_observed"),
    ({"labeled_at": "2026-10-20T10:00:00"}, "timezone"),
    ({"reviewed_at": "not a timestamp"}, "timestamp"),
    ({"reviewed_at": "2026-10-20T09:59:00+05:30"}, "must not precede"),
])
def test_incomplete_or_inconsistent_review_is_rejected(workspace, tmp_path, changes, message):
    reviewed(workspace, **changes)
    with pytest.raises(ValueError, match=message):
        benchmark.evaluate(workspace, output=tmp_path / "report")


def test_byte_identical_copied_predictions_cannot_be_references(workspace, tmp_path):
    reviewed(workspace)
    (workspace / "references.geojson").write_bytes((workspace / "predictions.geojson").read_bytes())
    with pytest.raises(ValueError, match="byte-identical"):
        benchmark.evaluate(workspace, output=tmp_path / "report")


@pytest.mark.parametrize("name", ["predictions.geojson", "source.json", "settings.json", "aoi.geojson"])
def test_changed_frozen_file_fails_before_output(workspace, tmp_path, name):
    reviewed(workspace)
    with (workspace / name).open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="Frozen input.*changed"):
        benchmark.evaluate(workspace, output=tmp_path / "report")
    assert not (tmp_path / "report").exists()


def test_aoi_manifest_disagreement_is_rejected_even_with_valid_file_hash(workspace, tmp_path):
    reviewed(workspace)
    manifest = json.loads((workspace / "manifest.json").read_text())
    manifest["aoi"]["bounds"][0] += .00001
    write_json(workspace / "manifest.json", manifest)
    with pytest.raises(ValueError, match="AOI file.*disagree"):
        benchmark.evaluate(workspace, output=tmp_path / "report")


@pytest.mark.parametrize("name", ["../outside.geojson", "sub/file.json", "C:\\outside.json"])
def test_manifest_cannot_read_supplied_paths(workspace, tmp_path, name):
    manifest = json.loads((workspace / "manifest.json").read_text())
    manifest["frozen_files"][name] = manifest["frozen_files"].pop("source.json")
    write_json(workspace / "manifest.json", manifest)
    with pytest.raises(ValueError, match="exact supported filenames"):
        benchmark.evaluate(workspace, output=tmp_path / "report")


def test_reference_outside_common_aoi_is_rejected(workspace, tmp_path):
    reviewed(workspace, collection(polygon(x=88.362)))
    with pytest.raises(ValueError, match="outside.*AOI"):
        benchmark.evaluate(workspace, output=tmp_path / "report")


def test_prepare_rejects_prediction_outside_aoi_before_writing(inputs):
    inputs["model_path"].write_bytes(b"synthetic model fixture")
    write_json(inputs["predictions_path"], collection(polygon(x=88.362)))
    with pytest.raises(ValueError, match="outside.*AOI"):
        benchmark.prepare(**inputs)
    assert not inputs["output"].exists()


def test_settings_preserve_explicit_unknown_values_without_inventing_defaults(inputs, tmp_path):
    inputs["model_path"].write_bytes(b"synthetic model fixture")
    settings = {"confidence_threshold": None, "minimum_area_sqm": None, "tiling": "Unknown; not retained"}
    write_json(inputs["settings_path"], settings)
    workspace = benchmark.prepare(**inputs)
    reviewed(workspace)
    output = benchmark.evaluate(workspace, output=tmp_path / "report")
    assert json.loads((output / "report.json").read_text())["settings"] == settings
    assert "Unknown (not retained)" in (output / "report.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("change", [{"confidence_threshold": True}, {"confidence_threshold": 1.1}, {"minimum_area_sqm": -1}])
def test_invalid_settings_are_rejected(inputs, change):
    inputs["model_path"].write_bytes(b"synthetic model fixture")
    write_json(inputs["settings_path"], {"confidence_threshold": .5, "minimum_area_sqm": 4, **change})
    with pytest.raises(ValueError, match="valid"):
        benchmark.prepare(**inputs)
    assert not inputs["output"].exists()


def test_optional_imagery_is_copied_and_fingerprinted_without_reencoding(inputs):
    inputs["model_path"].write_bytes(b"synthetic model fixture")
    imagery = inputs["output"].parent / "synthetic.png"
    imagery.write_bytes(b"synthetic byte payload; not a real raster")
    workspace = benchmark.prepare(**inputs, imagery_path=imagery)
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["imagery_file"] == "imagery.png"
    assert (workspace / "imagery.png").read_bytes() == imagery.read_bytes()


def test_existing_outputs_are_never_overwritten(workspace, inputs, tmp_path):
    original = (workspace / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        benchmark.prepare(**inputs)
    assert (workspace / "manifest.json").read_bytes() == original
    reviewed(workspace)
    output = tmp_path / "report"
    output.mkdir()
    (output / "keep.txt").write_text("existing evidence")
    with pytest.raises(ValueError, match="already exists"):
        benchmark.evaluate(workspace, output=output)
    assert (output / "keep.txt").read_text() == "existing evidence"


def test_publish_failure_cleans_staging_and_does_not_leave_invalid_output(tmp_path, monkeypatch):
    original = Path.write_bytes
    def interrupted_write(path, contents):
        if path.name == "second.json":
            raise OSError("simulated disk failure")
        return original(path, contents)
    monkeypatch.setattr(Path, "write_bytes", interrupted_write)
    with pytest.raises(OSError, match="disk failure"):
        benchmark._publish(tmp_path / "report", {"first.json": b"{}", "second.json": b"{}"})
    assert list(tmp_path.iterdir()) == []


def test_publish_retries_transient_directory_lock_without_replacing_existing_output(tmp_path, monkeypatch):
    original = benchmark.os.rename
    attempts = []
    def transient_lock(source, target):
        attempts.append((source, target))
        if len(attempts) == 1:
            raise PermissionError("synthetic transient directory lock")
        return original(source, target)
    monkeypatch.setattr(benchmark.os, "rename", transient_lock)
    monkeypatch.setattr(benchmark.time, "sleep", lambda delay: None)
    output = benchmark._publish(tmp_path / "report", {"report.json": b"{}"})
    assert (output / "report.json").read_bytes() == b"{}"
    assert len(attempts) == 2


def test_publish_retry_preserves_output_created_during_lock(tmp_path, monkeypatch):
    output = tmp_path / "report"
    def occupied_during_lock(source, target):
        Path(target).mkdir()
        (Path(target) / "keep.txt").write_text("other completed evidence")
        raise PermissionError("synthetic transient directory lock")
    monkeypatch.setattr(benchmark.os, "rename", occupied_during_lock)
    monkeypatch.setattr(benchmark.time, "sleep", lambda delay: None)
    with pytest.raises(ValueError, match="already exists"):
        benchmark._publish(output, {"report.json": b"{}"})
    assert (output / "keep.txt").read_text() == "other completed evidence"
    assert not (output / "report.json").exists()
    assert list(tmp_path.iterdir()) == [output]


def test_traversal_and_link_ancestors_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="traversal"):
        benchmark._plain_path(tmp_path / "child" / ".." / "report")
    target, link = tmp_path / "target", tmp_path / "link"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a symlink requires privileges on this Windows installation")
    with pytest.raises(ValueError, match="links and junctions"):
        benchmark._plain_path(link / "report")


@pytest.mark.parametrize("payload", [b'{"name":1,"name":2}', b'{"value":NaN}', b'\xff'])
def test_ambiguous_or_non_finite_json_is_rejected(payload):
    with pytest.raises(ValueError):
        benchmark._parse_json(payload, "Synthetic input")


def test_report_escapes_supplied_text(workspace, tmp_path):
    reviewed(workspace, labeling_method='<script>alert("test")</script>')
    output = benchmark.evaluate(workspace, output=tmp_path / "report")
    page = (output / "report.html").read_text(encoding="utf-8")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_cli_error_is_readable_and_creates_no_report(workspace, tmp_path):
    command = Path(benchmark.__file__)
    output = tmp_path / "report"
    result = subprocess.run([sys.executable, str(command), "evaluate", str(workspace), "--output", str(output)],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert "References are empty" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()
