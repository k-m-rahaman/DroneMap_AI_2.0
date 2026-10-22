"""Private backups must be consistent, portable, and unable to overwrite data."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import zipfile

import pytest

from storage import Store
from tools.backup_workspace import (
    BackupError, DATABASE, MANIFEST, create_backup, restore_backup, validate_archive,
)


@pytest.fixture
def workspace(tmp_path):
    directory = tmp_path / "original"
    store = Store(directory)
    sources = directory / "sources"
    sources.mkdir()
    raster = sources / "source-one_native.tif"
    preview = sources / "source-one_preview.png"
    raster.write_bytes(b"test fixture raster bytes\x00\x01")
    preview.write_bytes(b"test fixture preview bytes")
    source = {"id": "source-one", "name": "স্থানীয় জরিপ", "width": 8000,
              "raster_path": str(raster), "preview_path": str(preview),
              "crs": "EPSG:32645", "transform": [0.1, 0, 500000, 0, -0.1, 2500000]}
    # The DB is canonical: a stale JSON sidecar must never override it.
    (sources / "source-one.json").write_text('{"stale":true}', encoding="utf-8")
    (sources / "unused.png").write_bytes(b"unfinished import, not referenced")
    with store.connection() as database:
        database.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", ("officer", "Test", "test@example.test", "retained-password-hash", "surveyor", "2026"))
        database.execute("INSERT INTO sessions VALUES(?,?,?)", ("retained-token-hash", "officer", 9999999999))
        database.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", ("project", "officer", "Survey", "Test", "Fixture", "2026", "2026"))
        database.execute("INSERT INTO sources VALUES(?,?,?,?)", ("source-one", "project", json.dumps(source, ensure_ascii=False), "2026"))
        database.execute("INSERT INTO features VALUES(?,?,?,?,?,?,?)", ("building", "project", '{"type":"Polygon"}', '{"name":"Test"}', 2, "2026", "2026"))
        database.execute("INSERT INTO feature_history(feature_id,project_id,version,action,snapshot,user_id,timestamp) VALUES(?,?,?,?,?,?,?)", ("building", "project", 1, "created", '{"old":"geometry"}', "officer", "2026"))
        database.execute("INSERT INTO reports VALUES(?,?,?,?,?)", ("report", "building", 2, '{"immutable":"signed snapshot"}', "2026"))
    return store, source


def table_rows(path, table):
    with closing(sqlite3.connect(path)) as database:
        return database.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def rewrite_archive(original, output, *, mutate=None, extra=None):
    with zipfile.ZipFile(original) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    if mutate:
        mutate(members)
    with zipfile.ZipFile(output, "w") as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)
        if extra:
            name, contents = extra
            if isinstance(name, str):
                # ZipInfo normalizes os.sep on Windows. Set the raw archive
                # name afterwards to exercise a deliberately malformed ZIP.
                entry = zipfile.ZipInfo("placeholder")
                entry.filename = name
            else:
                entry = name
            archive.writestr(entry, contents)
    return output


def test_backup_restores_committed_wal_and_canonical_metadata(workspace, tmp_path):
    store, source = workspace
    original_key = store.setting("report_signing_key")
    output = tmp_path / "private.zip"
    # Keep the live WAL connection open, including a pending transaction. The
    # snapshot must include committed writes without copying the pending update.
    with closing(sqlite3.connect(store.path)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO settings VALUES('committed-in-wal','yes')")
        live.commit()
        live.execute("UPDATE settings SET value='pending' WHERE key='committed-in-wal'")
        result = create_backup(store.data_dir, output)
        assert result["source_count"] == 1
        manifest = validate_archive(output)
        assert manifest["private"] is True
        with zipfile.ZipFile(output) as archive:
            assert json.loads(archive.read("sources/source-one.json")) == source
            assert "sources/unused.png" not in archive.namelist()
            snapshot = tmp_path / "snapshot.sqlite3"
            snapshot.write_bytes(archive.read(DATABASE))
        assert ("committed-in-wal", "yes") in table_rows(snapshot, "settings")
        live.rollback()
    target = tmp_path / "restored"
    restored = restore_backup(output, target)
    assert restored["source_count"] == 1
    restored_store = Store(target)
    assert restored_store.setting("report_signing_key") == original_key
    for table in ("users", "sessions", "projects", "features", "feature_history", "reports", "feature_requests", "settings"):
        assert table_rows(target / DATABASE, table) == table_rows(store.path, table)
    with closing(sqlite3.connect(target / DATABASE)) as database:
        restored_source = json.loads(database.execute("SELECT metadata FROM sources").fetchone()[0])
    assert restored_source["name"] == source["name"]
    for field in ("raster_path", "preview_path"):
        assert Path(restored_source[field]).is_relative_to(target)
        assert Path(restored_source[field]).read_bytes() == Path(source[field]).read_bytes()
    assert json.loads((target / "sources/source-one.json").read_text(encoding="utf-8")) == restored_source
    # Images and database handles are closed: the whole restored workspace can
    # be moved immediately, including on Windows.
    moved = tmp_path / "moved-after-restore"
    target.rename(moved)
    assert (moved / DATABASE).is_file()


def test_restore_allows_existing_empty_folder_and_backup_never_overwrites(workspace, tmp_path):
    store, _ = workspace
    output = tmp_path / "private.zip"
    create_backup(store.data_dir, output)
    previous = output.read_bytes()
    with pytest.raises(BackupError, match="already exists"):
        create_backup(store.data_dir, output)
    assert output.read_bytes() == previous
    target = tmp_path / "empty"
    target.mkdir()
    restore_backup(output, target)
    assert (target / DATABASE).is_file()
    before = (target / DATABASE).read_bytes()
    with pytest.raises(BackupError, match="new or empty"):
        restore_backup(output, target)
    assert (target / DATABASE).read_bytes() == before


def test_backup_with_missing_referenced_image_fails_without_partial_zip(workspace, tmp_path):
    store, source = workspace
    Path(source["preview_path"]).unlink()
    output = tmp_path / "failed.zip"
    with pytest.raises(BackupError, match="missing"):
        create_backup(store.data_dir, output)
    assert not output.exists()


def test_backup_rejects_reference_outside_sources(workspace, tmp_path):
    store, source = workspace
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"must stay outside")
    source["raster_path"] = str(outside)
    with store.connection() as database:
        database.execute("UPDATE sources SET metadata=?", (json.dumps(source),))
    with pytest.raises(BackupError, match="outside"):
        create_backup(store.data_dir, tmp_path / "failed.zip")


@pytest.mark.parametrize("member", ["../escape", "/absolute", "C:/escape", "sources/../escape", "sources\\escape", "sources/CON.txt", "sources/image.", "sources/image?.png"])
def test_restore_rejects_unsafe_archive_paths_before_creating_target(workspace, tmp_path, member):
    store, _ = workspace
    original = tmp_path / "private.zip"
    create_backup(store.data_dir, original)
    bad = rewrite_archive(original, tmp_path / "bad.zip", extra=(member, b"bad"))
    target = tmp_path / "must-not-exist"
    with pytest.raises(BackupError, match="unsafe"):
        restore_backup(bad, target)
    assert not target.exists()
    assert not (tmp_path / "escape").exists()


def test_restore_rejects_checksum_tampering(workspace, tmp_path):
    store, _ = workspace
    original = tmp_path / "private.zip"
    create_backup(store.data_dir, original)
    def tamper(members):
        members["sources/source-one_preview.png"] = b"changed"
    bad = rewrite_archive(original, tmp_path / "bad.zip", mutate=tamper)
    with pytest.raises(BackupError, match="checksum"):
        restore_backup(bad, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_rejects_metadata_disagreeing_with_database(workspace, tmp_path):
    store, _ = workspace
    original = tmp_path / "private.zip"
    create_backup(store.data_dir, original)
    def tamper(members):
        member = "sources/source-one.json"
        contents = json.loads(members[member])
        contents["name"] = "Different from canonical DB"
        members[member] = json.dumps(contents).encode()
        manifest = json.loads(members[MANIFEST])
        manifest["files"][member] = {"size": len(members[member]), "sha256": hashlib.sha256(members[member]).hexdigest()}
        members[MANIFEST] = json.dumps(manifest).encode()
    bad = rewrite_archive(original, tmp_path / "bad.zip", mutate=tamper)
    with pytest.raises(BackupError, match="does not match"):
        restore_backup(bad, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_rejects_link_member(workspace, tmp_path):
    store, _ = workspace
    original = tmp_path / "private.zip"
    create_backup(store.data_dir, original)
    link = zipfile.ZipInfo("sources/symlink")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    bad = rewrite_archive(original, tmp_path / "bad.zip", extra=(link, b"../../other"))
    with pytest.raises(BackupError, match="link"):
        restore_backup(bad, tmp_path / "restored")


def test_backup_empty_workspace_still_retains_report_signing_key(tmp_path):
    store = Store(tmp_path / "empty")
    key = store.setting("report_signing_key")
    output = tmp_path / "empty.zip"
    assert create_backup(store.data_dir, output)["source_count"] == 0
    target = tmp_path / "restored"
    assert restore_backup(output, target)["source_count"] == 0
    assert Store(target).setting("report_signing_key") == key
