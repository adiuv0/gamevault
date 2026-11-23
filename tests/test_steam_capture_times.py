"""Steam imports are dated by when a screenshot was taken, not when Steam posted it.

Steam posts a play session, or several days of them, as one batch a couple of
seconds apart. Dating captures by that put days of play on the upload day. The
real time is in the file name the Steam CDN returns with each file.
"""

import asyncio
import importlib
import io

import aiosqlite
import httpx
import pytest
from bs4 import BeautifulSoup
from PIL import Image

from backend.migrations.runner import MIGRATIONS, run_migrations
from backend.services.steam_scraper import (
    SteamDownload,
    SteamFetchError,
    SteamGameScreenshots,
    SteamProfile,
    SteamScraper,
    SteamScreenshot,
    capture_time_from_name,
    filename_from_content_disposition,
)

APP_ID = 620
# Three shots from two evenings, posted together the morning after the second.
POSTED = {
    "9003": ("2024-03-10T11:31:42+00:00", "20240310025516_1"),
    "9002": ("2024-03-10T11:31:40+00:00", "20240309210744_1"),
    "9001": ("2024-03-10T11:31:38+00:00", "20240305141921_1"),
}


def _name(stamp: str, thumbnail: bool = False) -> str:
    return f"{APP_ID}_screenshots_{'thumbnails_' if thumbnail else ''}{stamp}.jpg"


def _disposition(stamp: str, thumbnail: bool = False) -> str:
    return f"inline; filename*=UTF-8''{_name(stamp, thumbnail)};"


def _jpeg(shade: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), (shade, 40, 90)).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
async def library(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.database import close_db, init_db

    await close_db()
    for field, path in {"data_dir": tmp_path, "db_path": tmp_path / "state.db",
                        "library_dir": tmp_path / "library", "thumbnail_dir": tmp_path / "cache"}.items():
        monkeypatch.setattr(settings, field, path)
    await init_db()
    yield settings
    await close_db()


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    from backend.services import steam_scraper

    monkeypatch.setattr(steam_scraper, "_retry_delay", lambda resp, attempt: 0)


# ── Reading the capture time ─────────────────────────────────────────────────


def test_content_disposition_filename_forms():
    assert filename_from_content_disposition(_disposition("20240310025516_1")) == _name("20240310025516_1")
    assert filename_from_content_disposition('attachment; filename="a b.jpg"') == "a b.jpg"
    assert filename_from_content_disposition("attachment; filename=plain.png; size=3") == "plain.png"
    assert filename_from_content_disposition("inline; filename*=UTF-8''caf%C3%A9.jpg") == "café.jpg"
    assert filename_from_content_disposition("inline") is None
    assert filename_from_content_disposition(None) is None


def test_capture_time_comes_from_steams_file_name():
    posted = "2024-03-10T11:31:42+00:00"
    full = capture_time_from_name(_name("20240310025516_1"), posted)
    assert full.isoformat() == "2024-03-10T02:55:16"
    assert full.tzinfo is None
    # The thumbnail is a separate upload named after the same moment.
    assert capture_time_from_name(_name("20240310025516_1", thumbnail=True), posted) == full
    # The name as Steam writes it on the PC, and a later shot in the same second.
    assert capture_time_from_name("20240310025516_2.jpg", posted) == full


@pytest.mark.parametrize("name", [
    None,
    "",
    "steam_1234567890.jpg",
    "holiday.jpg",
    "620_screenshots_20241345025516_1.jpg",   # no month 13
    "620_screenshots_19990101000000_1.jpg",   # before Steam existed
    "620_screenshots_202403100255161_1.jpg",  # 15 digits is not a timestamp
])
def test_names_without_a_capture_time_are_ignored(name):
    assert capture_time_from_name(name, "2024-03-10T11:31:42+00:00") is None


def test_capture_time_after_the_post_is_not_believed():
    """A screenshot cannot be taken after it was posted: such a name is not Steam's."""
    posted = "2024-03-10T11:31:42+00:00"
    # Later on the local clock but still before the post, east of UTC.
    assert capture_time_from_name("620_screenshots_20240310233000_1.jpg", posted) is not None
    assert capture_time_from_name("620_screenshots_20240312120000_1.jpg", posted) is None
    # The HTML fallback's posted time has no zone; it bounds the same way.
    assert capture_time_from_name("620_screenshots_20240312120000_1.jpg", "2024-03-10T03:31:00") is None
    assert capture_time_from_name("620_screenshots_29990101000000_1.jpg") is None


# ── Talking to the CDN ───────────────────────────────────────────────────────


def _scraper(handler) -> SteamScraper:
    scraper = SteamScraper("76561198000000000", is_numeric_id=True)
    scraper.rate_limit_ms = 0
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return scraper


async def test_download_returns_the_cdn_file_name():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_jpeg(10),
                              headers={"content-disposition": _disposition("20240310025516_1")})

    scraper = _scraper(handler)
    try:
        download = await scraper.download_file("https://images.steamusercontent.com/ugc/1/A/")
        assert download.data == _jpeg(10)
        assert download.original_name == _name("20240310025516_1")
        assert await scraper.download_image("https://images.steamusercontent.com/ugc/1/A/") == _jpeg(10)
    finally:
        await scraper._client.aclose()


async def test_name_lookup_asks_without_downloading():
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, headers={"content-disposition": _disposition("20240310025516_1")})

    scraper = _scraper(handler)
    try:
        name = await scraper.fetch_original_name("https://images.steamusercontent.com/ugc/1/A/")
    finally:
        await scraper._client.aclose()
    assert name == _name("20240310025516_1")
    assert methods == ["HEAD"]


@pytest.mark.parametrize("head_response", [
    httpx.Response(405),
    httpx.Response(200),  # an edge that leaves the name off HEAD answers
])
async def test_name_lookup_falls_back_to_get(head_response):
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(head_response.status_code)
        return httpx.Response(200, content=_jpeg(10),
                              headers={"content-disposition": _disposition("20240310025516_1")})

    scraper = _scraper(handler)
    try:
        name = await scraper.fetch_original_name("https://images.steamusercontent.com/ugc/1/A/")
    finally:
        await scraper._client.aclose()
    assert name == _name("20240310025516_1")
    assert methods == ["HEAD", "GET"]


async def test_name_lookup_stays_on_steams_cdn_and_reports_outages():
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.host)
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://internal.example/secret"})
        if request.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(503)

    scraper = _scraper(handler)
    try:
        assert await scraper.fetch_original_name("https://images.steamusercontent.com/redirect") is None
        assert await scraper.fetch_original_name("https://internal.example/secret") is None
        assert requested == ["images.steamusercontent.com"]
        # Deleted from Steam: a settled answer, nothing to retry.
        assert await scraper.fetch_original_name("https://images.steamusercontent.com/gone") is None
        with pytest.raises(SteamFetchError):
            await scraper.fetch_original_name("https://images.steamusercontent.com/down")
    finally:
        await scraper._client.aclose()


async def test_api_listing_keeps_the_file_name_when_steam_sends_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": {"total": 2, "publishedfiledetails": [
            {"publishedfileid": "9003", "time_created": 1710070302, "file_url": "https://images.steamusercontent.com/ugc/1/A/",
             "filename": "620/screenshots/20240310025516_1.jpg"},
            {"publishedfileid": "9002", "time_created": 1710070300, "file_url": "https://images.steamusercontent.com/ugc/1/B/"},
        ]}})

    scraper = _scraper(handler)
    scraper.api_key = "k"
    try:
        named, unnamed = await scraper.get_game_screenshots(APP_ID)
    finally:
        await scraper._client.aclose()
    assert named.date_taken == "2024-03-10T11:31:42+00:00"
    assert capture_time_from_name(named.original_name, named.date_taken).isoformat() == "2024-03-10T02:55:16"
    assert unnamed.original_name is None


def test_grid_thumbnail_without_resize_parameters_is_not_the_screenshot():
    """Steam's current grid shows a separate 200px file; importing it stored thumbnails."""
    html = """<div id="image_wall">
      <a href="https://steamcommunity.com/sharedfiles/filedetails/?id=9003"
         class="profile_media_item modalContentLink" data-publishedfileid="9003">
        <div style="background-image: url('https://images.steamusercontent.com/ugc/11/AA/');"
             class="imgWallItem  " id="imgWallItem_9003"></div></a>
      <a href="https://steamcommunity.com/sharedfiles/filedetails/?id=9002"
         class="profile_media_item modalContentLink" data-publishedfileid="9002">
        <div style="background-image: url('https://steamuserimages-a.akamaihd.net/ugc/22/BB/?imw=200&imh=112');"
             class="imgWallItem  " id="imgWallItem_9002"></div></a>
    </div>"""
    scraper = SteamScraper.__new__(SteamScraper)
    separate, resized = scraper._parse_grid_page(BeautifulSoup(html, "lxml"), app_id=APP_ID)

    assert separate.thumbnail_url == "https://images.steamusercontent.com/ugc/11/AA/"
    assert separate.full_image_url is None  # so the import reads the detail page
    assert resized.full_image_url == "https://steamuserimages-a.akamaihd.net/ugc/22/BB/"


async def test_detail_page_gives_the_image_and_posted_date_whatever_the_stat_order():
    """Steam lists file size first, so the first stat is neither a date nor a caption."""
    page = """<div class="actualmediactn"><a href="https://images.steamusercontent.com/ugc/11/FULL/?imw=5000&imh=5000">
        <img src="https://images.steamusercontent.com/ugc/11/FULL/?imw=1024&imh=576"></a></div>
      <div class="detailsStatsContainerRight">
        <div class="detailsStatRight">1.25 MB</div>
        <div class="detailsStatRight">Mar 10, 2024 @ 3:30am</div>
        <div class="detailsStatRight">3840 x 2160</div>
      </div>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    scraper = _scraper(handler)
    try:
        shot = await scraper.get_screenshot_details(SteamScreenshot(
            screenshot_id="9003", app_id=APP_ID,
            detail_url="https://steamcommunity.com/sharedfiles/filedetails/?id=9003",
            thumbnail_url="https://images.steamusercontent.com/ugc/11/AA/",
        ))
    finally:
        await scraper._client.aclose()
    assert shot.full_image_url == "https://images.steamusercontent.com/ugc/11/FULL/"
    assert shot.date_taken == "2024-03-10T03:30:00"
    assert shot.description == ""
    assert shot.file_size == int(1.25 * 1024 * 1024)


# ── Importing ────────────────────────────────────────────────────────────────


class FakeSteam:
    """A Steam profile with one game, standing in for SteamScraper."""

    def __init__(self, shots=POSTED, *, names=True, down=False, other_app: int | None = None):
        self.shots = shots
        self.names = names
        self.down = down
        self.other_app = other_app
        self.lookups: list[str] = []
        self.downloads: list[str] = []
        self.listed: list[int] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def validate_profile(self):
        return SteamProfile(user_id="76561198000000000", profile_name="Tester", is_numeric_id=True)

    async def discover_games(self):
        games = [SteamGameScreenshots(app_id=APP_ID, name="Portal 2", screenshot_count=len(self.shots))]
        if self.other_app:
            games.append(SteamGameScreenshots(app_id=self.other_app, name="Elsewhere", screenshot_count=0))
        return games

    async def get_game_screenshots(self, app_id, on_progress=None):
        self.listed.append(app_id)
        if app_id != APP_ID:
            return []
        return [
            SteamScreenshot(
                screenshot_id=steam_id, app_id=APP_ID, date_taken=posted,
                thumbnail_url=f"https://images.steamusercontent.com/ugc/{steam_id}/THUMB/",
                full_image_url=f"https://images.steamusercontent.com/ugc/{steam_id}/FULL/",
            )
            for steam_id, (posted, _) in self.shots.items()
        ]

    async def get_screenshot_details(self, screenshot):
        return screenshot

    def _stamp(self, url: str) -> str | None:
        steam_id = url.split("/ugc/")[1].split("/")[0]
        return _name(self.shots[steam_id][1]) if self.names else None

    async def download_file(self, url):
        self.downloads.append(url)
        steam_id = url.split("/ugc/")[1].split("/")[0]
        return SteamDownload(_jpeg(int(steam_id) % 200), self._stamp(url))

    async def fetch_original_name(self, url):
        self.lookups.append(url)
        if self.down:
            raise SteamFetchError("The Steam CDN did not answer")
        return self._stamp(url)


async def _run_import(monkeypatch, steam: FakeSteam) -> dict:
    """Run a Steam import against the fake profile; returns its import_complete event."""
    from backend.services import steam_import_service as service

    monkeypatch.setattr(service, "SteamScraper", steam)

    async def no_metadata(*args, **kwargs):
        return {}

    monkeypatch.setattr(service, "fetch_and_apply_metadata", no_metadata)
    session_id = await service.create_import_session("tester")
    queue = service.get_progress_queue(session_id)
    await service.run_import(session_id=session_id, user_id="tester")
    events = {}
    while not queue.empty():
        event = queue.get_nowait()
        events[event["event"]] = event["data"]
    service.cleanup_session(session_id)
    assert "import_complete" in events, events
    return events["import_complete"]


async def _rows() -> dict[str, dict]:
    from backend.database import get_db

    db = await get_db()
    cursor = await db.execute(
        """SELECT steam_screenshot_id, taken_at, steam_posted_at, game_id
           FROM screenshots WHERE source = 'steam_import'"""
    )
    return {row[0]: {"taken_at": row[1], "steam_posted_at": row[2], "game_id": row[3]}
            for row in await cursor.fetchall()}


async def _backdate_to_posted_time() -> None:
    """Put the rows back the way versions before 1.2.3 stored them."""
    from backend.database import get_db

    db = await get_db()
    await db.execute(
        """UPDATE screenshots SET taken_at = steam_posted_at, steam_posted_at = NULL
           WHERE source = 'steam_import'"""
    )
    await db.commit()


async def test_import_dates_screenshots_by_when_they_were_taken(library, monkeypatch):
    done = await _run_import(monkeypatch, FakeSteam())
    assert (done["completed"], done["skipped"], done["failed"]) == (3, 0, 0)

    rows = await _rows()
    assert rows["9003"]["taken_at"] == "2024-03-10T02:55:16"
    assert rows["9002"]["taken_at"] == "2024-03-09T21:07:44"
    assert rows["9001"]["taken_at"] == "2024-03-05T14:19:21"
    # What Steam reported is kept beside it.
    assert rows["9003"]["steam_posted_at"] == "2024-03-10T11:31:42+00:00"

    # The point of it all: three days of play no longer sit on the upload day.
    from backend.services.timeline_service import get_timeline

    timeline = await get_timeline()
    assert [day["date"] for day in timeline["days"]] == ["2024-03-10", "2024-03-09", "2024-03-05"]


async def test_import_falls_back_to_the_posted_time(library, monkeypatch):
    done = await _run_import(monkeypatch, FakeSteam(names=False))
    assert done["completed"] == 3

    rows = await _rows()
    assert rows["9003"]["taken_at"] == "2024-03-10T11:31:42+00:00"
    # Marked as looked up, so later imports do not ask Steam again.
    assert rows["9003"]["steam_posted_at"] == "2024-03-10T11:31:42+00:00"


async def test_importing_again_fixes_screenshots_already_in_the_library(library, monkeypatch):
    from backend.services import game_service

    await _run_import(monkeypatch, FakeSteam())
    await _backdate_to_posted_time()
    game_id = (await _rows())["9003"]["game_id"]
    await game_service.update_screenshot_stats(game_id)
    assert (await game_service.get_game(game_id))["first_screenshot_date"].startswith("2024-03-10T11:31:38")

    steam = FakeSteam()
    done = await _run_import(monkeypatch, steam)
    assert (done["completed"], done["skipped"], done["dates_fixed"]) == (0, 3, 3)
    assert steam.downloads == []  # names only: nothing is downloaded again
    assert len(steam.lookups) == 3

    rows = await _rows()
    assert rows["9001"]["taken_at"] == "2024-03-05T14:19:21"
    assert rows["9001"]["steam_posted_at"] == "2024-03-10T11:31:38+00:00"
    game = await game_service.get_game(game_id)
    assert game["first_screenshot_date"] == "2024-03-05T14:19:21"
    assert game["last_screenshot_date"] == "2024-03-10T02:55:16"

    # A third import has nothing left to ask Steam about.
    again = FakeSteam()
    done = await _run_import(monkeypatch, again)
    assert (done["skipped"], done["dates_fixed"]) == (3, 0)
    assert again.lookups == []


async def test_an_unreachable_cdn_leaves_the_row_for_next_time(library, monkeypatch):
    await _run_import(monkeypatch, FakeSteam())
    await _backdate_to_posted_time()

    done = await _run_import(monkeypatch, FakeSteam(down=True))
    assert (done["skipped"], done["failed"], done["dates_fixed"]) == (3, 0, 0)
    rows = await _rows()
    assert rows["9003"]["taken_at"] == "2024-03-10T11:31:42+00:00"
    assert rows["9003"]["steam_posted_at"] is None

    done = await _run_import(monkeypatch, FakeSteam())
    assert done["dates_fixed"] == 3


async def test_a_listing_without_the_full_address_asks_about_the_thumbnail(library, monkeypatch):
    from backend.services import steam_capture_times

    steam = FakeSteam()
    shot = SteamScreenshot(
        screenshot_id="9003", app_id=APP_ID,
        thumbnail_url="https://images.steamusercontent.com/ugc/9003/THUMB/?imw=200",
    )
    captured = await steam_capture_times.lookup_capture_time(steam, shot, POSTED["9003"][0])
    assert captured.isoformat() == "2024-03-10T02:55:16"
    assert steam.lookups == ["https://images.steamusercontent.com/ugc/9003/THUMB/"]


# ── The repair queued by the migration ───────────────────────────────────────


async def _job() -> dict:
    from backend.database import get_db

    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM maintenance_jobs WHERE kind = 'steam_capture_times' ORDER BY id DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    return dict(zip((d[0] for d in cursor.description), row)) if row else {}


async def _queue_job() -> int:
    from backend.database import get_db

    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO maintenance_jobs (kind, total) VALUES ('steam_capture_times', 3)"
    )
    await db.commit()
    return cursor.lastrowid


async def test_migration_adds_the_column_and_queues_the_repair(tmp_path):
    async def upgraded(with_steam_rows: bool) -> tuple[list[str], int]:
        path = tmp_path / f"old-{with_steam_rows}.db"
        async with aiosqlite.connect(path) as db:
            await db.execute(
                """CREATE TABLE _migrations (id INTEGER PRIMARY KEY AUTOINCREMENT,
                       name TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"""
            )
            for name in MIGRATIONS[:17]:
                await importlib.import_module(name).upgrade(db)
                await db.execute("INSERT INTO _migrations (name) VALUES (?)", (name,))
            await db.execute("INSERT INTO games (name, folder_name) VALUES ('G', 'G')")
            if with_steam_rows:
                await db.execute(
                    """INSERT INTO screenshots (game_id, filename, file_path, source,
                           steam_screenshot_id, taken_at)
                       VALUES (1, 'steam_9003.jpg', 'G/screenshots/steam_9003.jpg',
                               'steam_import', '9003', '2024-03-10T11:31:42+00:00')"""
                )
            await db.execute(
                """INSERT INTO screenshots (game_id, filename, file_path, source, taken_at)
                   VALUES (1, 'a.png', 'G/screenshots/a.png', 'upload', '2026-01-05T20:15:00')"""
            )
            await db.commit()
            await run_migrations(db)
            columns = [row[1] for row in await (await db.execute("PRAGMA table_info(screenshots)")).fetchall()]
            jobs = await (await db.execute(
                "SELECT total, status FROM maintenance_jobs WHERE kind = 'steam_capture_times'"
            )).fetchall()
            # The capture view lists a table's columns itself; it must keep working.
            await db.execute("SELECT steam_posted_at, capture_favorite FROM logical_captures")
        return columns, jobs

    columns, jobs = await upgraded(with_steam_rows=True)
    assert "steam_posted_at" in columns
    assert jobs == [(1, "pending")]
    _, jobs = await upgraded(with_steam_rows=False)
    assert jobs == []


async def test_background_repair_fixes_the_library_without_a_click(library, monkeypatch):
    from backend.services import game_service, steam_capture_times

    await _run_import(monkeypatch, FakeSteam())
    await _backdate_to_posted_time()
    game_id = (await _rows())["9003"]["game_id"]
    await game_service.update_screenshot_stats(game_id)
    job_id = await _queue_job()

    steam = FakeSteam()
    monkeypatch.setattr(steam_capture_times, "SteamScraper", steam)
    await steam_capture_times.resume_repair_job()
    await steam_capture_times._startup_job

    rows = await _rows()
    assert rows["9002"]["taken_at"] == "2024-03-09T21:07:44"
    assert steam.downloads == []
    job = await _job()
    assert (job["id"], job["status"], job["processed"]) == (job_id, "completed", 3)
    assert (await game_service.get_game(game_id))["last_screenshot_date"] == "2024-03-10T02:55:16"

    # Finished jobs are not picked up again.
    monkeypatch.setattr(steam_capture_times, "_startup_job", None)
    await steam_capture_times.resume_repair_job()
    assert steam_capture_times._startup_job is None


async def test_background_repair_finds_screenshots_whose_game_was_merged(library, monkeypatch):
    """A merged game no longer names the Steam game its screenshots came from."""
    from backend.database import get_db
    from backend.services import steam_capture_times

    await _run_import(monkeypatch, FakeSteam())
    await _backdate_to_posted_time()
    db = await get_db()
    await db.execute("UPDATE games SET steam_app_id = NULL")
    await db.commit()
    job_id = await _queue_job()

    steam = FakeSteam(other_app=730)
    monkeypatch.setattr(steam_capture_times, "SteamScraper", steam)
    await steam_capture_times.run_repair_job(job_id)

    assert (await _rows())["9001"]["taken_at"] == "2024-03-05T14:19:21"
    assert steam.listed == [APP_ID]  # stops asking once nothing is left
    assert (await _job())["status"] == "completed"


async def test_background_repair_retries_a_few_starts_then_stops(library, monkeypatch):
    from backend.services import steam_capture_times

    await _run_import(monkeypatch, FakeSteam())
    await _backdate_to_posted_time()
    job_id = await _queue_job()

    monkeypatch.setattr(steam_capture_times, "SteamScraper", FakeSteam(down=True))
    for attempt in range(1, steam_capture_times.MAX_ATTEMPTS + 1):
        await steam_capture_times.run_repair_job(job_id)
        job = await _job()
        expected = "failed" if attempt == steam_capture_times.MAX_ATTEMPTS else "pending"
        assert (job["status"], job["errors"]) == (expected, attempt)
        assert "did not answer" in job["error_message"]
    assert (await _rows())["9003"]["steam_posted_at"] is None

    # A failed job is left alone on later starts.
    monkeypatch.setattr(steam_capture_times, "_startup_job", None)
    await steam_capture_times.resume_repair_job()
    assert steam_capture_times._startup_job is None


async def test_background_repair_finishes_once_steam_answers_again(library, monkeypatch):
    from backend.services import steam_capture_times

    await _run_import(monkeypatch, FakeSteam())
    await _backdate_to_posted_time()
    job_id = await _queue_job()

    monkeypatch.setattr(steam_capture_times, "SteamScraper", FakeSteam(down=True))
    await steam_capture_times.run_repair_job(job_id)
    assert (await _job())["status"] == "pending"

    monkeypatch.setattr(steam_capture_times, "SteamScraper", FakeSteam())
    await steam_capture_times.run_repair_job(job_id)
    job = await _job()
    assert (job["status"], job["processed"], job["error_message"]) == ("completed", 3, None)
    assert (await _rows())["9003"]["taken_at"] == "2024-03-10T02:55:16"


async def test_background_repair_waits_when_no_profile_is_known(library, monkeypatch):
    from backend.database import get_db
    from backend.services import game_service, screenshot_service, steam_capture_times

    game = await game_service.create_game("Portal 2")
    await screenshot_service.create_screenshot(
        game["id"], "steam_9003.jpg", "Portal 2/screenshots/steam_9003.jpg",
        source="steam_import", steam_screenshot_id="9003",
        taken_at="2024-03-10T11:31:42+00:00",
    )
    db = await get_db()
    await db.commit()
    job_id = await _queue_job()

    def never(*args, **kwargs):
        raise AssertionError("Steam must not be contacted without a known profile")

    monkeypatch.setattr(steam_capture_times, "SteamScraper", never)
    await steam_capture_times.run_repair_job(job_id)
    job = await _job()
    assert (job["status"], job["errors"]) == ("pending", 0)

    # With nothing left to repair the job simply ends.
    await db.execute("DELETE FROM screenshots")
    await db.commit()
    await steam_capture_times.run_repair_job(job_id)
    assert (await _job())["status"] == "completed"


async def test_server_shutdown_leaves_a_running_repair_queued(library, monkeypatch):
    from backend.services import steam_capture_times

    await _run_import(monkeypatch, FakeSteam())
    await _backdate_to_posted_time()
    await _queue_job()

    class Stalled(FakeSteam):
        async def fetch_original_name(self, url):
            await asyncio.sleep(3600)

    monkeypatch.setattr(steam_capture_times, "SteamScraper", Stalled())
    await steam_capture_times.resume_repair_job()
    for _ in range(200):
        if (await _job())["status"] == "running":
            break
        await asyncio.sleep(0.01)
    await steam_capture_times.stop_repair_job()

    assert (await _job())["status"] == "running"  # resume_repair_job picks it up again
    assert steam_capture_times._startup_job is None
