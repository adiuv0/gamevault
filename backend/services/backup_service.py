"""Verified online state backups for GameVault's SQLite database."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.config import DEFAULT_SECRET_SENTINEL, settings
from backend.version import APP_VERSION

BACKUP_FORMAT_VERSION = 1
DATABASE_FILENAME = "gamevault.db"
MANIFEST_FILENAME = "manifest.json"
SECRET_FILENAME = ".secret_key"
_BUNDLE_NAME_PATTERN = re.compile(r"^gamevault-state-[A-Za-z0-9._-]+$")
_REQUIRED_TABLES = {"_migrations", "app_settings", "games", "screenshots"}


class BackupError(RuntimeError):
    """Raised when a state bundle cannot be safely created or verified."""


@dataclass(frozen=True)
class BackupVerification:
    bundle_path: Path
    database_sha256: str
    database_size_bytes: int
    applied_migrations: tuple[str, ...]


def create_state_backup(
    destination_root: Path,
    *,
    source_db: Path | None = None,
    secret_key: str | None = None,
    bundle_name: str | None = None,
    created_at: datetime | None = None,
) -> Path:
    """Create and atomically publish a verified database-and-key bundle.

    The SQLite online backup API captures committed WAL contents without
    checkpointing or modifying the source database. Original screenshots,
    generated previews, and active upload bodies are intentionally excluded.
    """
    source = (source_db or settings.db_path).resolve()
    if not source.is_file():
        raise BackupError(f"GameVault database not found: {source}")

    destination_root = destination_root.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    timestamp = (created_at or datetime.now(UTC)).astimezone(UTC)
    name = bundle_name or f"gamevault-state-{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    if not _BUNDLE_NAME_PATTERN.fullmatch(name):
        raise BackupError("Invalid backup bundle name")

    final_path = destination_root / name
    if final_path.exists():
        raise BackupError(f"Backup destination already exists: {final_path}")

    partial_path = destination_root / f".{name}.partial-{uuid4().hex}"
    partial_path.mkdir(mode=0o700)
    try:
        database_path = partial_path / DATABASE_FILENAME
        _online_database_backup(source, database_path)
        database_info = _inspect_database(database_path)
        if database_info["active_upload_jobs"]:
            raise BackupError(
                "The database snapshot contains active browser uploads. "
                "Wait for them to finish and retry so staged bodies are not omitted."
            )

        effective_secret = secret_key if secret_key is not None else _effective_secret_key()
        secret_path = partial_path / SECRET_FILENAME
        secret_path.write_text(effective_secret, encoding="utf-8")
        _restrict_file(secret_path)

        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "application": "GameVault",
            "application_version": APP_VERSION,
            "created_at": timestamp.isoformat().replace("+00:00", "Z"),
            "scope": {
                "included": ["database", "effective_secret_key"],
                "excluded": [
                    "library_originals",
                    "thumbnail_cache",
                    "upload_staging",
                ],
            },
            "database": {
                "filename": DATABASE_FILENAME,
                "sha256": _sha256_file(database_path),
                "size_bytes": database_path.stat().st_size,
                "applied_migrations": database_info["applied_migrations"],
            },
            "secret_key": {
                "filename": SECRET_FILENAME,
                "sha256": _sha256_file(secret_path),
            },
        }
        (partial_path / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        verify_state_backup(partial_path)
        partial_path.replace(final_path)
        return final_path
    except BackupError:
        shutil.rmtree(partial_path, ignore_errors=True)
        raise
    except Exception as error:
        shutil.rmtree(partial_path, ignore_errors=True)
        raise BackupError(f"Could not create GameVault state backup: {error}") from error


def verify_state_backup(bundle_path: Path) -> BackupVerification:
    """Verify checksums, SQLite integrity, foreign keys, and migration metadata."""
    bundle = bundle_path.resolve()
    if not bundle.is_dir():
        raise BackupError(f"Backup bundle not found: {bundle}")

    manifest_path = bundle / MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackupError("Backup manifest is missing or invalid") from error

    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise BackupError("Unsupported backup format version")
    if manifest.get("application") != "GameVault":
        raise BackupError("Backup manifest is not for GameVault")

    database = manifest.get("database")
    secret = manifest.get("secret_key")
    if not isinstance(database, dict) or database.get("filename") != DATABASE_FILENAME:
        raise BackupError("Backup manifest has an invalid database entry")
    if not isinstance(secret, dict) or secret.get("filename") != SECRET_FILENAME:
        raise BackupError("Backup manifest has an invalid secret-key entry")

    database_path = bundle / DATABASE_FILENAME
    secret_path = bundle / SECRET_FILENAME
    database_hash = _sha256_file_checked(database_path, "database")
    secret_hash = _sha256_file_checked(secret_path, "secret key")
    if not hmac.compare_digest(database_hash, str(database.get("sha256", ""))):
        raise BackupError("Backup database checksum does not match the manifest")
    if not hmac.compare_digest(secret_hash, str(secret.get("sha256", ""))):
        raise BackupError("Backup secret-key checksum does not match the manifest")

    database_info = _inspect_database(database_path)
    if database_info["active_upload_jobs"]:
        raise BackupError("Backup contains active browser upload jobs without staged bodies")
    manifest_migrations = database.get("applied_migrations")
    if manifest_migrations != database_info["applied_migrations"]:
        raise BackupError("Backup migration metadata does not match the database")

    return BackupVerification(
        bundle_path=bundle,
        database_sha256=database_hash,
        database_size_bytes=database_path.stat().st_size,
        applied_migrations=tuple(database_info["applied_migrations"]),
    )


def _online_database_backup(source_path: Path, destination_path: Path) -> None:
    source_uri = f"{source_path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=5)) as source:
        source.execute("PRAGMA busy_timeout=5000")
        with closing(sqlite3.connect(destination_path)) as destination:
            source.backup(destination, pages=256, sleep=0.05)
            destination.execute("PRAGMA journal_mode=DELETE")
            destination.commit()
    _restrict_file(destination_path)


def _inspect_database(database_path: Path) -> dict[str, Any]:
    if not database_path.is_file():
        raise BackupError(f"Backup database is missing: {database_path}")
    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(database_uri, uri=True, timeout=5)) as db:
            db.execute("PRAGMA query_only=ON")
            integrity_rows = [row[0] for row in db.execute("PRAGMA integrity_check")]
            if integrity_rows != ["ok"]:
                raise BackupError("Backup database failed SQLite integrity_check")
            if list(db.execute("PRAGMA foreign_key_check")):
                raise BackupError("Backup database contains foreign-key violations")

            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing = sorted(_REQUIRED_TABLES - tables)
            if missing:
                raise BackupError(
                    f"Backup database is missing required tables: {', '.join(missing)}"
                )
            migrations = [
                row[0] for row in db.execute("SELECT name FROM _migrations ORDER BY id")
            ]
            active_upload_jobs = 0
            if "upload_jobs" in tables:
                active_upload_jobs = db.execute(
                    "SELECT COUNT(*) FROM upload_jobs WHERE status IN ('pending', 'running')"
                ).fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise BackupError(f"Backup database cannot be read: {error}") from error

    return {
        "applied_migrations": migrations,
        "active_upload_jobs": active_upload_jobs,
    }


def _effective_secret_key() -> str:
    if settings.secret_key != DEFAULT_SECRET_SENTINEL:
        return settings.secret_key
    key_path = settings.data_dir / SECRET_FILENAME
    try:
        secret = key_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise BackupError(
            "The effective secret key is unavailable. Start GameVault once or set "
            "GAMEVAULT_SECRET_KEY before creating a state backup."
        ) from error
    if not secret:
        raise BackupError("The persisted GameVault secret key is empty")
    return secret


def _sha256_file_checked(path: Path, label: str) -> str:
    if not path.is_file():
        raise BackupError(f"Backup {label} is missing")
    return _sha256_file(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
