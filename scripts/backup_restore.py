#!/usr/bin/env python3
"""Verified PostgreSQL/media archives. Restore stages a new database; never replaces one."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
FILES = ("database.dump", "media.tar")
TABLES = ("commerce_productvariant", "commerce_inventorybatch", "commerce_order", "commerce_customercashcollection", "commerce_courierremittance", "commerce_returnrecord")


class RecoveryError(Exception):
    pass


def load_environment(root=ROOT):
    """Read only literal key=value assignments; never execute a shell or interpolate values."""
    values = {}
    path = Path(root) / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                values[key] = value.strip().strip("\"").strip("'")
    values.update(os.environ)
    return values


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def publish_directory(stage, destination):
    """Claim the destination exclusively; a concurrent empty directory is never replaced."""
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise RecoveryError("Destination was created by another operation; refusing to replace it.") from exc
    try:
        # Put the manifest last: an interrupted backup remains detectably incomplete.
        for item in sorted(stage.iterdir(), key=lambda p: (p.name == "manifest.json", p.name)):
            item.rename(destination / item.name)
        stage.rmdir()
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def validate_media(path):
    """Fully inspect member paths/types before any extraction or database creation."""
    names = set()
    with tarfile.open(path, "r:") as tar:
        for member in tar:
            p = PurePosixPath(member.name)
            if p.is_absolute() or ".." in p.parts or "\\" in member.name or not member.name or not (member.isdir() or member.isfile()):
                raise RecoveryError("Media archive contains an unsafe path, link or special file.")
            name = p.as_posix().rstrip("/")
            if name in names:
                raise RecoveryError("Media archive contains duplicate paths.")
            names.add(name)
    return names


def inspect_archive(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise RecoveryError("Backup must be a real directory.")
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RecoveryError("Missing regular manifest.json file.")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (ValueError, OSError) as exc:
        raise RecoveryError("Backup manifest is unreadable.") from exc
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1 or not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(FILES):
        raise RecoveryError("Unsupported or incomplete backup manifest.")
    for name in FILES:
        p = directory / name
        info = manifest["files"][name]
        if p.is_symlink() or not p.is_file() or not isinstance(info, dict):
            raise RecoveryError("Backup payload is missing or is a symbolic link.")
        if p.stat().st_size != info.get("size_bytes") or digest(p) != info.get("sha256"):
            raise RecoveryError(f"Checksum or size mismatch: {name}.")
    validate_media(directory / "media.tar")
    return manifest


class Transport:
    def __init__(self, *, direct=False, env=None, pg_bin=None, root=ROOT):
        self.env = dict(env or load_environment(root))
        self.direct = direct
        self.pg_bin = Path(pg_bin) if pg_bin else None
        self.root = Path(root)
        self.database = self.env.get("POSTGRES_DB", "skin_care")
        self.user = self.env.get("POSTGRES_USER", "skin_care_user")

    def run(self, tool, args=(), *, stdin=None, stdout=subprocess.PIPE):
        env = dict(self.env)
        if self.direct:
            env["PGPASSWORD"] = env.get("POSTGRES_PASSWORD", "")
            env["PGHOST"] = env.get("POSTGRES_HOST", "127.0.0.1")
            env["PGPORT"] = env.get("POSTGRES_PORT", "5432")
            executable = str(self.pg_bin / tool) if self.pg_bin else tool
            cmd = [executable, "-U", self.user, *args]
        else:
            cmd = ["docker", "compose", "exec", "-T", "db", tool, "-U", self.user, *args]
        result = subprocess.run(cmd, cwd=self.root, env=env, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE)
        if result.returncode:
            # Do not echo database URLs, environment variables or full command lines.
            raise RecoveryError(f"{tool} failed (exit {result.returncode}). Check the database service and permissions.")
        return result.stdout or b""

    def query(self, database, query):
        return self.run("psql", ["-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-c", query]).decode().strip()

    def archive_media(self, output, media_dir):
        if self.direct:
            media = Path(media_dir)
            with tarfile.open(output, "w:") as tar:
                if media.exists():
                    for p in sorted(media.rglob("*")):
                        if p.is_symlink() or not (p.is_file() or p.is_dir()):
                            raise RecoveryError("Media source contains a link or special file.")
                        tar.add(p, arcname=p.relative_to(media).as_posix(), recursive=False)
        else:
            code = "import pathlib,sys,tarfile; p=pathlib.Path('/app/media'); items=sorted(p.rglob('*')); assert all(not x.is_symlink() and (x.is_file() or x.is_dir()) for x in items), 'Unsafe media link'; t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|'); [t.add(x,arcname=x.relative_to(p).as_posix(),recursive=False) for x in items]; t.close()"
            with Path(output).open("wb") as stream:
                result = subprocess.run(["docker", "compose", "exec", "-T", "backend", "python", "-c", code], cwd=self.root, env=self.env, stdout=stream, stderr=subprocess.PIPE)
            if result.returncode:
                raise RecoveryError("Media backup failed. Check the backend service.")


def backup(directory, transport, *, media_dir=ROOT / "backend/media"):
    directory = Path(directory).absolute()
    if directory.exists() or directory.is_symlink():
        raise RecoveryError("Backup destination already exists; choose a new directory.")
    directory.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".skincare-backup-", dir=directory.parent))
    try:
        with (stage / "database.dump").open("wb") as stream:
            transport.run("pg_dump", ["-d", transport.database, "--format=custom", "--no-owner", "--no-acl"], stdout=stream)
        transport.archive_media(stage / "media.tar", media_dir)
        manifest = {"format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "source_database": transport.database, "files": {n: {"sha256": digest(stage / n), "size_bytes": (stage / n).stat().st_size} for n in FILES}}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        inspect_archive(stage)
        publish_directory(stage, directory)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def extract_media(archive, destination):
    """Destination must be an empty fresh directory owned by this operation."""
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("xb") as out:
                    shutil.copyfileobj(source, out)


def restore(directory, target_database, media_destination, transport):
    directory = Path(directory).absolute()
    inspect_archive(directory)  # Must precede database calls or creating a staging directory.
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", target_database):
        raise RecoveryError("Target database must use lower-case letters, numbers and underscores, starting with a letter.")
    if target_database == transport.database:
        raise RecoveryError("Refusing to restore into the configured active database.")
    destination = Path(media_destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise RecoveryError("Media destination already exists; choose a new staging directory.")
    exists = transport.query("postgres", f"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='{target_database}')")
    if exists != "f":
        raise RecoveryError("Target database already exists or could not be safely checked.")
    # Check archive readability before database creation (pg_restore -l doesn't accept -U).
    if transport.direct:
        executable = str(transport.pg_bin / "pg_restore") if transport.pg_bin else "pg_restore"
        cmd = [executable, "--list", str(directory / "database.dump")]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    else:
        with (directory / "database.dump").open("rb") as stream:
            result = subprocess.run(["docker", "compose", "exec", "-T", "db", "pg_restore", "--list"], cwd=transport.root, env=transport.env, stdin=stream, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode:
        raise RecoveryError("Database archive is not readable by pg_restore.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".skincare-media-", dir=destination.parent))
    created = False
    try:
        extract_media(directory / "media.tar", stage)
        transport.run("createdb", ["--maintenance-db=postgres", target_database])
        created = True
        with (directory / "database.dump").open("rb") as stream:
            transport.run("pg_restore", ["-d", target_database, "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction"], stdin=stream)
        for table in TABLES:
            if transport.query(target_database, f"SELECT to_regclass('public.{table}') IS NOT NULL") != "t":
                raise RecoveryError(f"Restored database is missing {table}.")
        invalid = transport.query(target_database, "SELECT count(*) FROM commerce_inventorybatch WHERE qty_on_hand < 0 OR qty_reserved < 0 OR qty_reserved > qty_on_hand")
        if invalid != "0":
            raise RecoveryError("Restored stock validation failed.")
        bad_cash = transport.query(target_database, "SELECT count(*) FROM commerce_courierremittance WHERE gross_collected_syp < 0 OR courier_deductions_syp < 0 OR net_remitted_syp < 0 OR net_remitted_syp <> gross_collected_syp - courier_deductions_syp")
        if bad_cash != "0":
            raise RecoveryError("Restored cash validation failed.")
        counts = {table: int(transport.query(target_database, f"SELECT count(*) FROM {table}")) for table in TABLES}
        publish_directory(stage, destination)
        return counts
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        # Only remove the new database created by this operation. Never touch configured DB.
        if created:
            try:
                transport.run("dropdb", ["--maintenance-db=postgres", target_database])
            except RecoveryError:
                print("Restore failed; its NEW staging database could not be removed. Active database was not changed.", file=sys.stderr)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", action="store_true", help="Use locally installed PostgreSQL tools instead of Docker Compose")
    parser.add_argument("--pg-bin", help="PostgreSQL binary directory for direct mode")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("backup"); p.add_argument("directory"); p.add_argument("--media-dir", default=str(ROOT / "backend/media"))
    p = sub.add_parser("inspect"); p.add_argument("directory")
    p = sub.add_parser("restore"); p.add_argument("directory"); p.add_argument("--target-database", required=True); p.add_argument("--media-destination", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "inspect":
            manifest = inspect_archive(args.directory)
            print(f"Verified backup format {manifest['format_version']}: database dump and media checksums are valid.")
        else:
            transport = Transport(direct=args.direct, pg_bin=args.pg_bin)
            if args.action == "backup":
                backup(args.directory, transport, media_dir=args.media_dir)
                print(f"Verified backup created: {args.directory}")
            else:
                counts = restore(args.directory, args.target_database, args.media_destination, transport)
                print("Restored NEW staging database and staged media. Active database was not changed.")
                print(json.dumps(counts, indent=2))
                print("Inspect the staged application first. Activation is a separate manual change to POSTGRES_DB and the media volume; see docs/RECOVERY.md.")
    except (RecoveryError, OSError, tarfile.TarError, ValueError) as exc:
        print(f"Recovery stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
