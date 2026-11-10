"""Encrypted, integrity-checked backups with non-destructive staging restore."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine import make_url

from app.config import get_settings

ROOT = Path.cwd()
MEDIA_DIR = ROOT / "data" / "news_media"
PRIVATE_DOCS_DIR = ROOT / "data" / "exchange_private_documents"
MANIFEST = "manifest.json"


class BackupError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().backup_encryption_key.strip()
    if not key:
        raise BackupError("Set AMSA_BACKUP_ENCRYPTION_KEY before using backup commands")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise BackupError("AMSA_BACKUP_ENCRYPTION_KEY must be a valid Fernet key") from exc


def _database_snapshot(destination: Path) -> str:
    url = make_url(get_settings().database_url)
    if url.get_backend_name() == "sqlite":
        source_path = Path(unquote(url.database or ""))
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        if not source_path.is_file():
            raise BackupError(f"SQLite database does not exist: {source_path}")
        source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupError("SQLite snapshot failed its integrity check")
        finally:
            target.close()
            source.close()
        return "sqlite"
    if url.get_backend_name() != "postgresql":
        raise BackupError("Only SQLite and PostgreSQL databases are supported")
    executable = shutil.which("pg_dump")
    if not executable:
        raise BackupError("pg_dump is required to back up PostgreSQL")
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = unquote(url.password)
    query = dict(url.query)
    for option in ("sslmode", "sslrootcert", "sslcert", "sslkey"):
        if option in query:
            env[{"sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY"}[option]] = str(query[option])
    args = [executable, "--no-password", "--format=custom", "--no-owner", "--no-privileges", "--file", str(destination)]
    if url.host: args += ["--host", url.host]
    if url.port: args += ["--port", str(url.port)]
    if url.username: args += ["--username", unquote(url.username)]
    args += [unquote(url.database or "")]
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    if result.returncode:
        raise BackupError("pg_dump failed; check database connectivity and permissions")
    return "postgresql"


def _safe_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    if directory.is_symlink():
        raise BackupError("Backup data directories must not be symlinks")
    files = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise BackupError("Backup refuses symlinks in data directories")
        if path.is_file():
            files.append(path)
    return files


def create_backup() -> Path:
    settings = get_settings()
    cipher = _fernet()
    out_dir = Path(settings.backup_directory).expanduser()
    if not out_dir.is_absolute(): out_dir = ROOT / out_dir
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    final = out_dir / f"amsa-{stamp}.tar.gz.fernet"
    with tempfile.TemporaryDirectory(prefix="amsa-backup-") as temp_name:
        temp = Path(temp_name)
        database = temp / ("platform.sqlite3" if make_url(settings.database_url).get_backend_name() == "sqlite" else "platform.dump")
        engine = _database_snapshot(database)
        media = _safe_files(MEDIA_DIR)
        private_docs = _safe_files(PRIVATE_DOCS_DIR)
        manifest = {"format": 1, "created_at": now.isoformat(), "database": engine,
                    "database_file": f"database/{database.name}",
                    "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                    "media_count": len(media), "private_documents_count": len(private_docs)}
        plain_archive = temp / "backup.tar.gz"
        with tarfile.open(plain_archive, "w:gz") as archive:
            archive.add(database, arcname=manifest["database_file"], recursive=False)
            for path in media:
                archive.add(path, arcname=PurePosixPath("media") / path.relative_to(MEDIA_DIR), recursive=False)
            for path in private_docs:
                archive.add(path, arcname=PurePosixPath("private-docs") / path.relative_to(PRIVATE_DOCS_DIR), recursive=False)
            info = tarfile.TarInfo(MANIFEST)
            contents = json.dumps(manifest, separators=(",", ":")).encode()
            info.size = len(contents); info.mode = 0o600
            import io
            archive.addfile(info, io.BytesIO(contents))
        encrypted = temp / "backup.encrypted"
        encrypted.write_bytes(cipher.encrypt(plain_archive.read_bytes()))
        partial = out_dir / f".{final.name}.tmp"
        with partial.open("wb") as handle:
            os.chmod(partial, 0o600)
            with encrypted.open("rb") as source:
                shutil.copyfileobj(source, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, final)
    return final


def _read_archive(path: Path, work: Path) -> tuple[tarfile.TarFile, dict]:
    try:
        plain = _fernet().decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise BackupError("Backup authentication failed; key is wrong or file is damaged") from exc
    except OSError as exc:
        raise BackupError("Backup file could not be read") from exc
    archive_path = work / "decrypted.tar.gz"
    archive_path.write_bytes(plain)
    archive = tarfile.open(archive_path, "r:gz")
    members = archive.getmembers()
    allowed = {MANIFEST}
    for member in members:
        p = PurePosixPath(member.name)
        if p.is_absolute() or ".." in p.parts or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            archive.close(); raise BackupError("Backup contains an unsafe archive entry")
        if member.name != MANIFEST and not (member.name.startswith("database/") or member.name.startswith("media/") or member.name.startswith("private-docs/")):
            archive.close(); raise BackupError("Backup contains an unexpected archive entry")
        allowed.add(member.name)
    f = archive.extractfile(MANIFEST)
    if f is None:
        archive.close(); raise BackupError("Backup manifest is missing")
    try: manifest = json.load(f)
    except (ValueError, UnicodeDecodeError) as exc:
        archive.close(); raise BackupError("Backup manifest is invalid") from exc
    expected = manifest.get("database_file")
    if manifest.get("format") != 1 or expected not in allowed or manifest.get("database") not in {"sqlite", "postgresql"}:
        archive.close(); raise BackupError("Backup manifest is unsupported or incomplete")
    media_entries = [name for name in allowed if name.startswith("media/") and name != "media/"]
    if manifest.get("media_count") != len(media_entries):
        archive.close(); raise BackupError("Backup media count does not match the manifest")
    private_doc_entries = [name for name in allowed if name.startswith("private-docs/") and name != "private-docs/"]
    if manifest.get("private_documents_count") != len(private_doc_entries):
        archive.close(); raise BackupError("Backup private-document count does not match the manifest")
    db_file = archive.extractfile(expected)
    if db_file is None or hashlib.sha256(db_file.read()).hexdigest() != manifest.get("database_sha256"):
        archive.close(); raise BackupError("Database checksum does not match the manifest")
    return archive, manifest


def verify_backup(path: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="amsa-verify-") as temp:
        archive, manifest = _read_archive(path, Path(temp))
        try:
            if manifest["database"] == "sqlite":
                stream = archive.extractfile(manifest["database_file"])
                snapshot = Path(temp) / "snapshot.sqlite3"
                snapshot.write_bytes(stream.read())
                db = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
                try:
                    result = db.execute("PRAGMA integrity_check").fetchone()
                    if not result or result[0] != "ok": raise BackupError("SQLite integrity check failed")
                finally: db.close()
            else:
                pg_restore = shutil.which("pg_restore")
                if not pg_restore: raise BackupError("pg_restore is required to verify PostgreSQL backups")
                dump = Path(temp) / "platform.dump"
                dump.write_bytes(archive.extractfile(manifest["database_file"]).read())
                result = subprocess.run([pg_restore, "--list", str(dump)], capture_output=True, text=True)
                if result.returncode: raise BackupError("PostgreSQL dump failed validation")
        finally: archive.close()
        return manifest


def _extract_safe(archive: tarfile.TarFile, destination: Path) -> None:
    for member in archive.getmembers():
        if member.isdir(): continue
        relative = PurePosixPath(member.name)
        if relative.is_absolute() or ".." in relative.parts:
            raise BackupError("Unsafe restore path")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None: raise BackupError("Unreadable archive entry")
        with target.open("xb") as handle:
            os.chmod(target, 0o600)
            shutil.copyfileobj(source, handle)


def restore_staging(path: Path, destination: Path) -> Path:
    dest = destination.expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        raise BackupError("Staging destination must be a new or empty directory")
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    try:
        with tempfile.TemporaryDirectory(prefix="amsa-restore-check-") as temp:
            archive, manifest = _read_archive(path, Path(temp))
            try: _extract_safe(archive, dest)
            finally: archive.close()
        database = dest / manifest["database_file"]
        if manifest["database"] == "sqlite":
            db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise BackupError("Restored SQLite database is corrupt")
            finally: db.close()
        else:
            pg_restore = shutil.which("pg_restore")
            if not pg_restore: raise BackupError("pg_restore is required to validate PostgreSQL staging dumps")
            result = subprocess.run([pg_restore, "--list", str(database)], capture_output=True, text=True)
            if result.returncode: raise BackupError("Restored PostgreSQL dump failed validation")
        return dest
    except Exception:
        # Keep evidence if an operator wants to inspect a failed restore; never touch live data.
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Encrypted AMSA database and news-media backups")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup", help="Create an encrypted backup")
    verify = commands.add_parser("verify", help="Decrypt and validate a backup")
    verify.add_argument("archive", type=Path)
    restore = commands.add_parser("restore-staging", help="Extract a backup into a new staging directory")
    restore.add_argument("archive", type=Path); restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "backup":
            print(create_backup())
        elif args.command == "verify":
            manifest = verify_backup(args.archive)
            print(f"Verified {manifest['database']} backup from {manifest['created_at']} ({manifest['media_count']} media files, {manifest['private_documents_count']} private documents)")
        else:
            dest = restore_staging(args.archive, args.destination)
            print(f"Restored to staging directory: {dest}")
    except BackupError as exc:
        parser.exit(2, f"Backup error: {exc}\n")


if __name__ == "__main__":
    main()
