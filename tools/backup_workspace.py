"""Private, portable survey backups using only Python's standard library.

Run ``python tools/backup_workspace.py --help`` for commands. Backups retain
account password hashes, sessions, report snapshots and the report signing key.
Treat the resulting ZIP as private. It is checksummed, not encrypted.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile

FORMAT = "dronemap-private-workspace"
VERSION = 1
DATABASE = "workspace.sqlite3"
MANIFEST = "backup-manifest.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 30_000
MAX_EXPANDED_BYTES = 200 * 1024**3
SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class BackupError(ValueError):
    """A backup or restore cannot safely complete."""


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _file_info(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"size": size, "sha256": digest.hexdigest()}


def _safe_member(name):
    # ZIP paths always use '/', independent of the system creating/restoring it.
    # Reject Windows drive/ADS syntax and backslashes even on POSIX machines.
    if not isinstance(name, str) or not name or any(character in name for character in '\\:<>"|?*\x00') or any(ord(character) < 32 for character in name):
        raise BackupError("Archive contains an unsafe path.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise BackupError("Archive contains an unsafe path.")
    if path.as_posix() != name:
        raise BackupError("Archive contains an ambiguous path.")
    # Windows device names and trailing spaces/dots can alias other files.
    for part in path.parts:
        if part.endswith((" ", ".")) or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I):
            raise BackupError("Archive contains an unsafe Windows filename.")
    return name


def _plain_file(path, root):
    """Do not follow source symlinks or include files outside the sources tree."""
    path = Path(path)
    if not path.is_absolute():
        path = root.parent / path
    try:
        lexical = path.absolute()
        lexical.relative_to(root)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise BackupError("A referenced source image is missing or outside the workspace sources folder.") from exc
    current = lexical
    while current != root:
        if current.is_symlink():
            raise BackupError("Referenced source images must not be symbolic links.")
        current = current.parent
    if not resolved.is_file():
        raise BackupError("A referenced source image is missing or is not a regular file.")
    return resolved


def _source_rows(database):
    try:
        rows = database.execute("SELECT id,metadata FROM sources ORDER BY id").fetchall()
    except sqlite3.DatabaseError as exc:
        raise BackupError("This is not a compatible DroneMap workspace database.") from exc
    sources = []
    for source_id, raw in rows:
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise BackupError("The database contains an invalid source identifier.")
        try:
            source = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BackupError("The database contains invalid source metadata.") from exc
        if not isinstance(source, dict) or source.get("id") != source_id:
            raise BackupError("Source metadata does not match its database identifier.")
        sources.append((source_id, source))
    return sources


def _check_database(path):
    try:
        with closing(sqlite3.connect(path)) as database:
            if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("The workspace database failed its integrity check.")
            required = {"settings", "users", "projects", "sources", "features", "feature_history", "reports"}
            actual = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required.issubset(actual):
                raise BackupError("This is not a compatible DroneMap workspace database.")
            key = database.execute("SELECT value FROM settings WHERE key='report_signing_key'").fetchone()
            if not key or not key[0]:
                raise BackupError("The report signing key is missing from the workspace.")
            return _source_rows(database)
    except sqlite3.DatabaseError as exc:
        raise BackupError("The workspace database could not be validated.") from exc


def create_backup(data_dir, output):
    """Snapshot SQLite safely while the app is running; retain referenced imagery.

    Source files are immutable in the application. An in-progress new import
    absent from the SQLite snapshot is intentionally excluded from this backup.
    """
    data_dir = Path(data_dir).resolve(strict=True)
    output = Path(output).absolute()
    original = data_dir / DATABASE
    if original.is_symlink() or not original.is_file():
        raise BackupError("The selected data folder has no regular workspace.sqlite3 file.")
    if output.exists() or output.is_symlink():
        raise BackupError("Backup output already exists; choose a new filename.")
    if not output.parent.is_dir():
        raise BackupError("The backup output folder does not exist.")
    sources_root = (data_dir / "sources").resolve()
    if (data_dir / "sources").is_symlink():
        raise BackupError("The workspace sources folder must not be a symbolic link.")
    wrote_output = False
    try:
        with tempfile.TemporaryDirectory(prefix="dronemap-backup-") as scratch:
            snapshot = Path(scratch) / DATABASE
            # sqlite3.backup includes committed WAL transactions without copying
            # a live database file at an inconsistent instant.
            uri = original.as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=20)) as live, closing(sqlite3.connect(snapshot)) as copy:
                live.backup(copy, pages=256, sleep=0.05)
                copy.execute("PRAGMA journal_mode=DELETE")
            sources = _check_database(snapshot)
            manifest = {"format": FORMAT, "version": VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "private": True, "files": {}, "sources": []}
            files = {DATABASE: snapshot}
            metadata = {}
            for source_id, source in sources:
                reference = {"id": source_id, "metadata": f"sources/{source_id}.json", "files": {}}
                for field in ("raster_path", "preview_path"):
                    value = source.get(field)
                    if not isinstance(value, str) or not value:
                        raise BackupError("A source is missing its raster or preview file reference.")
                    path = _plain_file(value, sources_root)
                    member = _safe_member(path.relative_to(data_dir).as_posix())
                    if member in metadata or member == reference["metadata"]:
                        raise BackupError("A source image conflicts with a source metadata filename.")
                    files[member] = path
                    reference["files"][field] = member
                if reference["metadata"] in files:
                    raise BackupError("Source metadata conflicts with an image filename.")
                metadata[reference["metadata"]] = _json_bytes(source)
                manifest["sources"].append(reference)
            with output.open("xb") as stream:
                wrote_output = True
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                    for member, path in files.items():
                        # Hash bytes placed in ZIP, rather than a prior read of a
                        # file that could theoretically change while copying.
                        digest = hashlib.sha256()
                        size = 0
                        with path.open("rb") as source_stream, archive.open(member, "w", force_zip64=True) as archived:
                            for block in iter(lambda: source_stream.read(1024 * 1024), b""):
                                archived.write(block)
                                digest.update(block)
                                size += len(block)
                        manifest["files"][member] = {"size": size, "sha256": digest.hexdigest()}
                    for member, contents in metadata.items():
                        archive.writestr(member, contents)
                        manifest["files"][member] = {"size": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}
                    archive.writestr(MANIFEST, _json_bytes(manifest))
            validate_archive(output)
        return {"archive": str(output.resolve()), "source_count": len(sources), "file_count": len(manifest["files"])}
    except Exception:
        if wrote_output:
            output.unlink(missing_ok=True)
        raise


def validate_archive(archive_path):
    """Validate member paths, types, inventory and all SHA256 checksums."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS or sum(item.file_size for item in infos) > MAX_EXPANDED_BYTES:
                raise BackupError("Archive exceeds the workspace backup size limits.")
            names = set()
            windows_names = set()
            for item in infos:
                # Python normalizes native separators and truncates NULs while
                # constructing ZipInfo; reject the original archive name too.
                _safe_member(item.orig_filename)
                name = _safe_member(item.filename)
                windows_name = name.casefold()
                mode = item.external_attr >> 16
                if item.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or item.flag_bits & 1:
                    raise BackupError("Archive contains a directory, link, encrypted member or special file.")
                if name in names or windows_name in windows_names:
                    raise BackupError("Archive contains duplicate or case-colliding filenames.")
                names.add(name)
                windows_names.add(windows_name)
            if MANIFEST not in names or archive.getinfo(MANIFEST).file_size > MAX_MANIFEST_BYTES:
                raise BackupError("A valid backup manifest is missing.")
            try:
                manifest = json.loads(archive.read(MANIFEST))
            except (ValueError, UnicodeError) as exc:
                raise BackupError("The backup manifest is invalid.") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
                raise BackupError("Unsupported workspace backup format.")
            files, sources = manifest.get("files"), manifest.get("sources")
            if not isinstance(files, dict) or not isinstance(sources, list) or set(files) != names - {MANIFEST} or DATABASE not in files:
                raise BackupError("Archive contents do not match the manifest.")
            referenced = {DATABASE}
            source_ids = set()
            for source in sources:
                if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not SOURCE_ID.fullmatch(source["id"]) or source["id"] in source_ids:
                    raise BackupError("Invalid or duplicate source in the backup manifest.")
                source_ids.add(source["id"])
                if source.get("metadata") != f"sources/{source['id']}.json" or not isinstance(source.get("files"), dict) or set(source["files"]) != {"raster_path", "preview_path"}:
                    raise BackupError("Invalid source references in the backup manifest.")
                for member in [source["metadata"], *source["files"].values()]:
                    if not isinstance(member, str) or _safe_member(member) not in files or not member.startswith("sources/"):
                        raise BackupError("A source file reference is missing from the archive.")
                    referenced.add(member)
            if referenced != set(files):
                raise BackupError("Archive contains files not referenced by the workspace.")
            for member, expected in files.items():
                if not isinstance(expected, dict) or not isinstance(expected.get("size"), int) or expected["size"] < 0 or not isinstance(expected.get("sha256"), str):
                    raise BackupError("Invalid file checksum in the manifest.")
                digest = hashlib.sha256()
                size = 0
                with archive.open(member) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                        size += len(block)
                if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
                    raise BackupError("A backup file failed its checksum check.")
            return manifest
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, RuntimeError) as exc:
        raise BackupError("The backup archive is corrupt or incomplete.") from exc


def restore_backup(archive_path, data_dir):
    """Restore only into a new or empty directory, never a running workspace."""
    archive_path = Path(archive_path).resolve(strict=True)
    target = Path(data_dir).absolute()
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise BackupError("Restore requires a new or empty data folder; existing data will never be overwritten.")
    if not target.parent.is_dir():
        raise BackupError("The restore destination's parent folder does not exist.")
    # Also reject a symlinked ancestor: a user-selected destination should resolve
    # where its displayed path says, and extraction must not escape via links.
    if any(parent.is_symlink() for parent in target.parents):
        raise BackupError("Restore destinations must not have symbolic-link parents.")
    manifest = validate_archive(archive_path)
    with tempfile.TemporaryDirectory(prefix=".dronemap-restore-", dir=target.parent) as scratch:
        staged = Path(scratch)
        # All archive paths/types have already been checked; use explicit copies
        # instead of extractall so ZIP attributes cannot create links or modes.
        with zipfile.ZipFile(archive_path) as archive:
            for member in manifest["files"]:
                destination = staged.joinpath(*PurePosixPath(member).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        rows = dict(_check_database(staged / DATABASE))
        if set(rows) != {source["id"] for source in manifest["sources"]}:
            raise BackupError("Database sources do not match the backup manifest.")
        # Rewrite only storage paths. Coordinates, reports, history and signing
        # key are preserved exactly, including immutable old report snapshots.
        with closing(sqlite3.connect(staged / DATABASE)) as database, database:
            for reference in manifest["sources"]:
                source = rows[reference["id"]]
                try:
                    archived_source = json.loads(staged.joinpath(*PurePosixPath(reference["metadata"]).parts).read_bytes())
                except (ValueError, UnicodeError) as exc:
                    raise BackupError("A source metadata file is invalid.") from exc
                if archived_source != source:
                    raise BackupError("Source metadata does not match the database snapshot.")
                for field, member in reference["files"].items():
                    source[field] = str(target.resolve().joinpath(*PurePosixPath(member).parts))
                contents = json.dumps(source, ensure_ascii=False)
                database.execute("UPDATE sources SET metadata=? WHERE id=?", (contents, reference["id"]))
                staged.joinpath(*PurePosixPath(reference["metadata"]).parts).write_bytes(_json_bytes(source))
        _check_database(staged / DATABASE)
        # Claim the empty target only after complete validation. Copy files using
        # exclusive creation, including if another process races this restore.
        created = []
        new_target = False
        try:
            if not target.exists():
                target.mkdir()
                new_target = True
            if any(target.iterdir()):
                raise BackupError("Restore destination became nonempty; no files were overwritten.")
            for path in sorted(staged.rglob("*")):
                if path.is_file():
                    destination = target / path.relative_to(staged)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("rb") as source, destination.open("xb") as output:
                        created.append(destination)
                        shutil.copyfileobj(source, output, 1024 * 1024)
        except Exception:
            # Remove only files this restore created; never delete concurrent
            # unrelated files or use a recursive delete on a user path.
            for path in reversed(created):
                path.unlink(missing_ok=True)
            for directory in sorted({p.parent for p in created if p.parent != target}, key=lambda p: len(p.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            if new_target:
                try:
                    target.rmdir()
                except OSError:
                    pass
            raise
    return {"data_dir": str(target.resolve()), "source_count": len(rows)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up or restore private DroneMap data. ZIPs contain account credentials and report keys; store them privately.")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="Create a consistent database and referenced-imagery ZIP.")
    backup.add_argument("--data-dir", required=True, type=Path)
    backup.add_argument("--output", required=True, type=Path)
    restore = commands.add_parser("restore", help="Restore into a new or empty folder only.")
    restore.add_argument("--archive", required=True, type=Path)
    restore.add_argument("--data-dir", required=True, type=Path)
    verify = commands.add_parser("verify", help="Check archive paths, inventory and every checksum.")
    verify.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.data_dir, args.output)
            print(f"Private backup created and verified: {result['archive']} ({result['source_count']} sources).")
        elif args.command == "restore":
            result = restore_backup(args.archive, args.data_dir)
            print(f"Workspace restored: {result['data_dir']} ({result['source_count']} sources). Existing accounts and report signatures are retained.")
        else:
            manifest = validate_archive(args.archive)
            print(f"Backup checksums and archive paths verified ({len(manifest['sources'])} sources).")
    except (BackupError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Backup operation failed: {exc}\n")


if __name__ == "__main__":
    main()
