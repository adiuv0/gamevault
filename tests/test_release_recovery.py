"""Release, migration, and preservation-first recovery tests."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import shutil
import sqlite3
import tomllib
from contextlib import closing
from pathlib import Path

import aiosqlite
import pytest

from backend.migrations.runner import MIGRATIONS, run_migrations

# Each shipped schema, pinned by count: a slice from the end would shift every
# time a migration is added and quietly stop testing the real upgrade.
SUPPORTED_RELEASE_SCHEMAS = {
    "0.1.0": MIGRATIONS[:2],
    "0.2.0": MIGRATIONS[:3],
    "0.3.0": MIGRATIONS[:5],
    "0.3.1": MIGRATIONS[:5],
    "1.0.0": MIGRATIONS[:11],
    "1.0.1": MIGRATIONS[:11],
    "1.0.2": MIGRATIONS[:11],
    "1.0.3": MIGRATIONS[:12],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _open_current_database(path: Path) -> aiosqlite.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA wal_autocheckpoint=0")
    await run_migrations(db)
    return db


@pytest.mark.asyncio
async def test_online_backup_captures_active_wal_and_restores_without_touching_source(
    tmp_path: Path,
):
    from backend.services.backup_service import (
        DATABASE_FILENAME,
        MANIFEST_FILENAME,
        SECRET_FILENAME,
        create_state_backup,
        verify_state_backup,
    )

    source_root = tmp_path / "source"
    database_path = source_root / DATABASE_FILENAME
    library_file = source_root / "library" / "Recovery Game" / "screenshots" / "original.png"
    library_file.parent.mkdir(parents=True)
    library_file.write_bytes(b"irreplaceable-original")

    db = await _open_current_database(database_path)
    try:
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cursor = await db.execute(
            "INSERT INTO games (name, folder_name) VALUES (?, ?)",
            ("Recovery Game", "Recovery Game"),
        )
        game_id = cursor.lastrowid
        await db.execute(
            """INSERT INTO screenshots (
                   game_id, filename, file_path, sha256_hash, capture_group_id
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                game_id,
                "original.png",
                "Recovery Game/screenshots/original.png",
                "recovery-hash",
                1,
            ),
        )
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            ("steam_api_key", "state-secret"),
        )
        await db.commit()

        wal_path = database_path.with_name(f"{database_path.name}-wal")
        assert wal_path.stat().st_size > 0
        with closing(
            sqlite3.connect(f"{database_path.resolve().as_uri()}?immutable=1", uri=True)
        ) as main_file_only:
            assert main_file_only.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0

        source_database_hash = _sha256(database_path)
        source_wal_hash = _sha256(wal_path)
        bundle = create_state_backup(
            tmp_path / "backups",
            source_db=database_path,
            secret_key="effective-recovery-key",
            bundle_name="gamevault-state-active-wal-test",
        )

        assert _sha256(database_path) == source_database_hash
        assert _sha256(wal_path) == source_wal_hash
        assert library_file.read_bytes() == b"irreplaceable-original"
    finally:
        await db.close()

    verification = verify_state_backup(bundle)
    assert verification.applied_migrations == tuple(MIGRATIONS)
    assert not (bundle / f"{DATABASE_FILENAME}-wal").exists()
    assert not (bundle / f"{DATABASE_FILENAME}-shm").exists()
    assert (bundle / SECRET_FILENAME).read_text(encoding="utf-8") == "effective-recovery-key"
    manifest = json.loads((bundle / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert "library_originals" in manifest["scope"]["excluded"]

    restore_root = tmp_path / "restored"
    restore_root.mkdir()
    restored_database = restore_root / DATABASE_FILENAME
    shutil.copy2(bundle / DATABASE_FILENAME, restored_database)
    shutil.copy2(bundle / SECRET_FILENAME, restore_root / SECRET_FILENAME)
    restored = await aiosqlite.connect(restored_database)
    try:
        await restored.execute("PRAGMA foreign_keys=ON")
        await run_migrations(restored)
        cursor = await restored.execute(
            """SELECT g.name, s.filename, s.sha256_hash
               FROM screenshots s JOIN games g ON g.id = s.game_id"""
        )
        assert await cursor.fetchone() == (
            "Recovery Game",
            "original.png",
            "recovery-hash",
        )
        cursor = await restored.execute(
            "SELECT value FROM app_settings WHERE key = 'steam_api_key'"
        )
        assert await cursor.fetchone() == ("state-secret",)
        assert await (await restored.execute("PRAGMA integrity_check")).fetchone() == ("ok",)
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_backup_refuses_active_browser_upload_and_cleans_partial_bundle(tmp_path: Path):
    from backend.services.backup_service import BackupError, create_state_backup

    database_path = tmp_path / "source" / "gamevault.db"
    db = await _open_current_database(database_path)
    try:
        cursor = await db.execute(
            "INSERT INTO games (name, folder_name) VALUES (?, ?)",
            ("Active Upload", "Active Upload"),
        )
        await db.execute(
            """INSERT INTO upload_jobs (task_id, game_id, total_files)
               VALUES (?, ?, ?)""",
            ("01234567-89ab-4def-8123-456789abcdef", cursor.lastrowid, 1),
        )
        await db.commit()

        destination = tmp_path / "backups"
        with pytest.raises(BackupError, match="active browser uploads"):
            create_state_backup(
                destination,
                source_db=database_path,
                secret_key="backup-test-key",
                bundle_name="gamevault-state-must-not-publish",
            )
        assert list(destination.iterdir()) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_backup_verification_rejects_tampered_database(tmp_path: Path):
    from backend.services.backup_service import (
        BackupError,
        create_state_backup,
        verify_state_backup,
    )

    database_path = tmp_path / "source" / "gamevault.db"
    db = await _open_current_database(database_path)
    try:
        bundle = create_state_backup(
            tmp_path / "backups",
            source_db=database_path,
            secret_key="backup-test-key",
            bundle_name="gamevault-state-tamper-test",
        )
    finally:
        await db.close()

    with (bundle / "gamevault.db").open("ab") as backup_database:
        backup_database.write(b"tampered")
    with pytest.raises(BackupError, match="checksum"):
        verify_state_backup(bundle)


@pytest.mark.asyncio
async def test_backup_cli_creates_and_verifies_bundle(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    from backend.backup import main
    from backend.config import settings

    data_root = tmp_path / "data"
    database_path = data_root / "gamevault.db"
    db = await _open_current_database(database_path)
    await db.close()
    monkeypatch.setattr(settings, "data_dir", data_root)
    monkeypatch.setattr(settings, "db_path", database_path)
    monkeypatch.setattr(settings, "secret_key", "cli-effective-secret")

    destination = tmp_path / "backups"
    assert main(["create", str(destination)]) == 0
    create_output = json.loads(capsys.readouterr().out)
    assert create_output["status"] == "ok"
    bundle = Path(create_output["bundle"])
    assert bundle.parent == destination

    assert main(["verify", str(bundle)]) == 0
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["database_sha256"] == create_output["database_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("release", "release_migrations"),
    SUPPORTED_RELEASE_SCHEMAS.items(),
)
async def test_supported_release_database_upgrades_to_current_without_data_loss(
    tmp_path: Path,
    release: str,
    release_migrations: list[str],
):
    database_path = tmp_path / f"gamevault-{release}.db"
    db = await aiosqlite.connect(database_path)
    try:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """CREATE TABLE _migrations (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT NOT NULL UNIQUE,
                   applied_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        for migration_name in release_migrations:
            migration = importlib.import_module(migration_name)
            await migration.upgrade(db)
            await db.execute("INSERT INTO _migrations (name) VALUES (?)", (migration_name,))
            await db.commit()

        cursor = await db.execute(
            "INSERT INTO games (name, folder_name, is_public) VALUES (?, ?, ?)",
            ("Versioned Library", "Versioned Library", 0),
        )
        game_id = cursor.lastrowid
        cursor = await db.execute(
            """INSERT INTO screenshots (
                   game_id, filename, file_path, sha256_hash, width, height
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                game_id,
                "legacy.png",
                "Versioned Library/screenshots/legacy.png",
                "legacy-hash",
                1920,
                1080,
            ),
        )
        screenshot_id = cursor.lastrowid
        if "backend.migrations.m007_capture_groups" in release_migrations:
            await db.execute(
                "UPDATE screenshots SET capture_group_id = ? WHERE id = ?",
                (screenshot_id, screenshot_id),
            )
            await db.execute(
                "UPDATE games SET screenshot_count = 1 WHERE id = ?",
                (game_id,),
            )
        annotation_html = (
            "<p>kept</p>"
            if "backend.migrations.m004_resanitize_annotations" in release_migrations
            else "<script>alert(1)</script>"
        )
        await db.execute(
            """INSERT INTO annotations (screenshot_id, content, content_html)
               VALUES (?, ?, ?)""",
            (screenshot_id, "<script>alert(1)</script>kept", annotation_html),
        )
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            ("igdb_client_id", "legacy-setting"),
        )
        await db.commit()

        await run_migrations(db)

        cursor = await db.execute("SELECT name FROM _migrations ORDER BY id")
        assert [row[0] for row in await cursor.fetchall()] == MIGRATIONS
        cursor = await db.execute(
            """SELECT filename, sha256_hash, capture_group_id, preview_path, is_hdr
               FROM screenshots WHERE id = ?""",
            (screenshot_id,),
        )
        assert await cursor.fetchone() == (
            "legacy.png",
            "legacy-hash",
            screenshot_id,
            None,
            None,
        )
        assert await (
            await db.execute("SELECT is_public, screenshot_count FROM games WHERE id = ?", (game_id,))
        ).fetchone() == (0, 1)
        annotation_html = (
            await (
                await db.execute(
                    "SELECT content_html FROM annotations WHERE screenshot_id = ?",
                    (screenshot_id,),
                )
            ).fetchone()
        )[0]
        assert "<script" not in annotation_html
        assert "kept" in annotation_html
        assert await (
            await db.execute("SELECT value FROM app_settings WHERE key = 'igdb_client_id'")
        ).fetchone() == ("legacy-setting",)
        tables = {
            row[0]
            for row in await (
                await db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            ).fetchall()
        }
        assert {"import_jobs", "upload_jobs", "upload_job_files"} <= tables
        assert "tags" not in tables
        assert await (await db.execute("PRAGMA foreign_key_check")).fetchall() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_release_version_surfaces_stay_synchronized():
    from backend.main import app, health
    from backend.version import APP_VERSION

    root = Path(__file__).parents[1]
    package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    frontend_package = json.loads(
        (root / "frontend/package.json").read_text(encoding="utf-8")
    )
    frontend_lock = json.loads(
        (root / "frontend/package-lock.json").read_text(encoding="utf-8")
    )
    sidebar = (root / "frontend/src/components/layout/Sidebar.tsx").read_text(
        encoding="utf-8"
    )
    api_reference = (root / "docs/API_REFERENCE.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    stable_releases = set(re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE))

    assert package["project"]["version"] == APP_VERSION
    assert frontend_package["version"] == APP_VERSION
    assert frontend_lock["version"] == APP_VERSION
    assert frontend_lock["packages"][""]["version"] == APP_VERSION
    assert set(SUPPORTED_RELEASE_SCHEMAS) == stable_releases - {APP_VERSION}
    assert app.version == APP_VERSION
    body = await health()
    assert body["status"] == "ok" and body["version"] == APP_VERSION
    assert f"GameVault v{APP_VERSION}" in sidebar
    assert f'"version": "{APP_VERSION}"' in api_reference
