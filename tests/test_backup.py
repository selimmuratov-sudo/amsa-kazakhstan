import sqlite3
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app import backup


def test_encrypted_backup_verify_and_staging_restore(tmp_path, monkeypatch):
    db_path = tmp_path / "platform.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE member_data (name TEXT)")
        db.execute("INSERT INTO member_data VALUES ('private-member-value')")
    media_dir = tmp_path / "news_media"
    media_dir.mkdir()
    (media_dir / "cover.webp").write_bytes(b"public-image-bytes")
    out_dir = tmp_path / "encrypted-backups"
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(backup, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(backup, "get_settings", lambda: SimpleNamespace(
        backup_encryption_key=key,
        backup_directory=str(out_dir),
        database_url=f"sqlite:///{db_path}",
    ))

    backup_path = backup.create_backup()
    assert backup_path.parent == out_dir
    assert "private-member-value" not in backup_path.read_bytes().decode("latin1")
    assert backup_path.stat().st_mode & 0o777 == 0o600
    assert out_dir.stat().st_mode & 0o777 == 0o700
    manifest = backup.verify_backup(backup_path)
    assert manifest["database"] == "sqlite"
    assert manifest["media_count"] == 1

    staging = tmp_path / "staging"
    backup.restore_staging(backup_path, staging)
    restored_db = staging / "database" / "platform.sqlite3"
    with sqlite3.connect(restored_db) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT name FROM member_data").fetchone()[0] == "private-member-value"
    assert (staging / "media" / "cover.webp").read_bytes() == b"public-image-bytes"


def test_restore_refuses_nonempty_destination_and_wrong_key(tmp_path, monkeypatch):
    db_path = tmp_path / "db.sqlite3"
    sqlite3.connect(db_path).close()
    key = Fernet.generate_key().decode()
    settings = SimpleNamespace(backup_encryption_key=key, backup_directory=str(tmp_path / "backups"), database_url=f"sqlite:///{db_path}")
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(backup, "MEDIA_DIR", tmp_path / "missing-media")
    monkeypatch.setattr(backup, "get_settings", lambda: settings)
    archive = backup.create_backup()

    destination = tmp_path / "not-empty"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep")
    with pytest.raises(backup.BackupError, match="new or empty"):
        backup.restore_staging(archive, destination)
    settings.backup_encryption_key = Fernet.generate_key().decode()
    with pytest.raises(backup.BackupError, match="authentication failed"):
        backup.verify_backup(archive)
