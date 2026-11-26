"""Captures are dated by the time recorded inside the file, not the upload day.

Special K and the Windows capture path write no EXIF. They record the moment in
an XMP packet (older Special K: a PNG ``Creation Time`` text chunk), which
GameVault did not read, so those uploads sorted by the day they were uploaded.
Steam's own screenshot JPGs carry no date at all; they take the time of the copy
they are grouped with, or the one GameVault Sync reports for them.
"""

import importlib
import zlib
from datetime import datetime

import aiosqlite
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from backend.migrations.runner import MIGRATIONS, run_migrations
from backend.services.image_processor import (
    describe_image,
    embedded_capture_time,
    parse_capture_time,
)
from backend.services.upload_service import capture_time_from_filename

# Made-up capture times; none come from a real library.
XMP_TIME = "2025-07-04T19:42:07"


def _xmp(tag: str = "exif:DateTimeOriginal", when: str = XMP_TIME) -> str:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        "<xmp:CreatorTool>Some Game</xmp:CreatorTool></rdf:Description>"
        '<rdf:Description rdf:about="" xmlns:exif="http://ns.adobe.com/exif/1.0/">'
        f"<{tag}>{when}</{tag}></rdf:Description></rdf:RDF></x:xmpmeta>"
    )


def _png(path, shade=90, *, xmp: str | None = None, creation_time: str | None = None):
    info = PngInfo()
    info.add_text("Software", "DirectXTex")
    if creation_time:
        info.add_text("Creation Time", creation_time)
    if xmp:
        info.add_itxt("XML:com.adobe.xmp", xmp)
    image = Image.new("RGB", (64, 36), (shade, 40, 90))
    for x in range(0, 64, 8):
        for y in range(0, 36, 6):
            image.putpixel((x, y), (255 - shade, 200, 10))
    image.save(path, "PNG", pnginfo=info)
    return path


def _jpeg(path, shade=90):
    image = Image.new("RGB", (64, 36), (shade, 40, 90))
    for x in range(0, 64, 8):
        for y in range(0, 36, 6):
            image.putpixel((x, y), (255 - shade, 200, 10))
    image.save(path, "JPEG", quality=95)
    return path


# ── Reading the time ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("tag", ["exif:DateTimeOriginal", "xmp:CreateDate"])
def test_png_xmp_capture_time_is_read(tmp_path, tag):
    path = _png(tmp_path / "shot.png", xmp=_xmp(tag))
    assert embedded_capture_time(path) == datetime(2025, 7, 4, 19, 42, 7)
    assert describe_image(path)["taken_at"] == datetime(2025, 7, 4, 19, 42, 7)


def test_older_special_k_png_creation_time_chunk_is_read(tmp_path):
    path = _png(tmp_path / "shot.png", creation_time="2024:11:20 07:57:04")
    assert describe_image(path)["taken_at"] == datetime(2024, 11, 20, 7, 57, 4)


def test_jxr_xmp_is_read_without_decoding(tmp_path):
    # A JPEG XR container keeps its XMP packet in the first IFD, ahead of the pixels.
    path = tmp_path / "shot.jxr"
    path.write_bytes(b"II\xbc\x01" + b"\x00" * 200 + _xmp().encode() + b"\x00" * 4096)
    assert embedded_capture_time(path) == datetime(2025, 7, 4, 19, 42, 7)


def test_files_without_a_recorded_time_stay_undated(tmp_path):
    assert embedded_capture_time(_jpeg(tmp_path / "steam.jpg")) is None
    assert describe_image(_png(tmp_path / "plain.png"))["taken_at"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025-07-04T19:42:07", datetime(2025, 7, 4, 19, 42, 7)),
        ("2025-07-04T19:42:07.123", datetime(2025, 7, 4, 19, 42, 7)),
        ("2025:07:04 19:42:07", datetime(2025, 7, 4, 19, 42, 7)),
        ("2025-07-04T19:42:07-07:00", datetime.fromisoformat("2025-07-04T19:42:07-07:00")),
        ("Fri, 04 Jul 2025 19:42:07 GMT", datetime.fromisoformat("2025-07-04T19:42:07+00:00")),
        ("1601-01-01T00:00:00", None),
        ("not a date", None),
    ],
)
def test_capture_time_forms(value, expected):
    assert parse_capture_time(value) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("20250704194207_1.jpg", datetime(2025, 7, 4, 19, 42, 7)),
        ("Some Game 2025-07-04 19-42-07.png", datetime(2025, 7, 4, 19, 42, 7)),
        # GameVault's own names carry minutes only and are made at upload time.
        ("Some Game 2026_04_28 06_45 (67).png", None),
        ("steam_3000000001.jpg", None),
    ],
)
def test_capture_time_from_an_uploaded_files_name(name, expected):
    assert capture_time_from_filename(name) == expected


# ── Repairing the library ────────────────────────────────────────────────────


@pytest.fixture
async def library(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.database import close_db, init_db

    await close_db()
    for field, path in {"data_dir": tmp_path, "db_path": tmp_path / "state.db",
                        "library_dir": tmp_path / "library", "thumbnail_dir": tmp_path / "cache"}.items():
        monkeypatch.setattr(settings, field, path)
    (tmp_path / "library" / "G" / "screenshots").mkdir(parents=True)
    await init_db()
    yield settings
    await close_db()


async def _add(db, name, *, taken_at=None, group=None, sha=None):
    cursor = await db.execute(
        """INSERT INTO screenshots (game_id, filename, file_path, source, taken_at,
               uploaded_at, sha256_hash, width, height, format)
           VALUES (1, ?, ?, 'upload', ?, '2026-04-28 11:45:56', ?, 64, 36, ?)""",
        (name, f"G/screenshots/{name}", taken_at, sha, name.rsplit(".", 1)[1].replace("jpg", "jpeg")),
    )
    row_id = cursor.lastrowid
    await db.execute(
        "UPDATE screenshots SET capture_group_id = ? WHERE id = ?", (group or row_id, row_id)
    )
    return row_id


async def _taken_at(db, row_id):
    return (await (await db.execute("SELECT taken_at FROM screenshots WHERE id = ?", (row_id,))).fetchone())[0]


async def test_repair_dates_stored_files_and_their_grouped_copies(library):
    from backend.database import get_db
    from backend.services import embedded_capture_times

    shots = library.library_dir / "G" / "screenshots"
    _png(shots / "Game 2026_04_28 06_45 (1).png", xmp=_xmp())
    _jpeg(shots / "Game 2026_02_11 15_59 (1).jpg")
    _jpeg(shots / "Game 2026_02_11 15_59 (2).jpg", shade=30)
    db = await get_db()
    await db.execute("INSERT INTO games (name, folder_name) VALUES ('G', 'G')")
    png = await _add(db, "Game 2026_04_28 06_45 (1).png")
    paired_jpg = await _add(db, "Game 2026_02_11 15_59 (1).jpg", group=png)
    lone_jpg = await _add(db, "Game 2026_02_11 15_59 (2).jpg")
    timed = await _add(db, "kept.png", taken_at="2020-01-01T10:00:00")
    cursor = await db.execute(
        "INSERT INTO maintenance_jobs (kind, total) VALUES ('embedded_capture_times', 3)"
    )
    job_id = cursor.lastrowid
    await db.commit()

    await embedded_capture_times.run_repair_job(job_id)

    assert await _taken_at(db, png) == XMP_TIME
    assert await _taken_at(db, paired_jpg) == XMP_TIME
    assert await _taken_at(db, lone_jpg) is None
    assert await _taken_at(db, timed) == "2020-01-01T10:00:00"
    status, processed = await (await db.execute(
        "SELECT status, processed FROM maintenance_jobs WHERE id = ?", (job_id,)
    )).fetchone()
    assert (status, processed) == ("completed", 1)
    first, last = await (await db.execute(
        "SELECT first_screenshot_date, last_screenshot_date FROM games WHERE id = 1"
    )).fetchone()
    assert (first, last) == ("2020-01-01T10:00:00", XMP_TIME)


async def test_duplicate_scan_waits_for_the_repair(library):
    from backend.database import get_db
    from backend.services import embedded_capture_times

    db = await get_db()
    await db.execute("INSERT INTO maintenance_jobs (kind, total) VALUES ('embedded_capture_times', 0)")
    await db.commit()
    order = []

    async def scan():
        order.append("scan")

    original = embedded_capture_times.run_repair_job

    async def repair(job_id):
        order.append("repair")
        await original(job_id)

    embedded_capture_times.run_repair_job = repair
    try:
        await embedded_capture_times.resume_repair_job(then=scan)
        await embedded_capture_times._startup_job
    finally:
        embedded_capture_times.run_repair_job = original
    assert order == ["repair", "scan"]

    # Nothing queued: the scan starts straight away.
    order.clear()
    await embedded_capture_times.resume_repair_job(then=scan)
    assert order == ["scan"]


async def test_sync_reported_times_date_only_undated_copies(library):
    from backend.database import get_db
    from backend.services.screenshot_service import apply_capture_time_hints

    db = await get_db()
    await db.execute("INSERT INTO games (name, folder_name) VALUES ('G', 'G')")
    jpg = await _add(db, "a.jpg", sha="a" * 64)
    jxr = await _add(db, "a.jxr", sha="b" * 64, group=jpg)
    timed = await _add(db, "c.jpg", sha="c" * 64, taken_at="2020-01-01T10:00:00")
    await db.commit()

    dated = await apply_capture_time_hints({
        "a" * 64: "2025-07-04T19:42:07",
        "c" * 64: "2025-07-04T19:42:07",
        "d" * 64: "garbage",
    })

    assert dated == 1
    assert await _taken_at(db, jpg) == XMP_TIME
    assert await _taken_at(db, jxr) == XMP_TIME
    assert await _taken_at(db, timed) == "2020-01-01T10:00:00"


async def test_check_hashes_accepts_capture_times(library):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    db = await get_db()
    await db.execute("INSERT INTO games (name, folder_name) VALUES ('G', 'G')")
    row = await _add(db, "a.jpg", sha="a" * 64)
    await db.commit()
    with TestClient(app) as client:
        response = client.post("/api/screenshots/check-hashes", json={
            "hashes": ["a" * 64, "e" * 64],
            "capture_times": {"a" * 64: XMP_TIME, "e" * 64: XMP_TIME},
        })
        stored = client.get(f"/api/screenshots/{row}").json()
    assert response.status_code == 200
    body = response.json()
    assert body["existing"] == ["a" * 64]
    assert body["capture_times_applied"] == 1
    assert stored["taken_at"].startswith(XMP_TIME)


async def test_migration_queues_the_repair_ahead_of_a_fresh_scan(tmp_path):
    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """CREATE TABLE _migrations (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"""
        )
        index = MIGRATIONS.index("backend.migrations.m022_embedded_capture_times")
        for name in MIGRATIONS[:index]:
            await importlib.import_module(name).upgrade(db)
            await db.execute("INSERT INTO _migrations (name) VALUES (?)", (name,))
        await db.execute("INSERT INTO games (name, folder_name) VALUES ('G', 'G')")
        await db.execute(
            """INSERT INTO screenshots (game_id, filename, file_path, source)
               VALUES (1, 'a.png', 'G/screenshots/a.png', 'upload')"""
        )
        await db.execute(
            """INSERT INTO screenshots (game_id, filename, file_path, source, taken_at)
               VALUES (1, 'b.png', 'G/screenshots/b.png', 'upload', '2026-01-05T20:15:00')"""
        )
        await db.execute("INSERT INTO maintenance_jobs (kind, total) VALUES ('duplicate_backfill', 2)")
        await db.commit()
        await run_migrations(db)
        jobs = await (await db.execute(
            "SELECT kind, total, status FROM maintenance_jobs ORDER BY id"
        )).fetchall()
    assert jobs == [
        ("duplicate_backfill", 2, "completed"),
        ("embedded_capture_times", 1, "pending"),
        ("duplicate_backfill", 2, "pending"),
    ]


def test_png_chunk_reader_ignores_a_damaged_length():
    head = b"\x89PNG" + b"\xff\xff\xff\xff" + b"tEXtCreation Time\x00" + b"x" * 10
    assert embedded_capture_time(None, head=head) is None
    good = b"Creation Time\x002024:11:20 07:57:04"
    chunk = len(good).to_bytes(4, "big") + b"tEXt" + good + zlib.crc32(b"tEXt" + good).to_bytes(4, "big")
    assert embedded_capture_time(None, head=b"\x89PNG" + chunk) == datetime(2024, 11, 20, 7, 57, 4)
