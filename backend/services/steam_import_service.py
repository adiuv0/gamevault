"""Steam import orchestrator: drives the scraper, saves screenshots, emits SSE progress."""

import asyncio
import json
import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path

from backend.config import settings
from backend.database import get_db
from backend.routers.settings import get_effective_key
from backend.services.steam_scraper import (
    SteamScraper,
    SteamScreenshot,
    SteamGameScreenshots,
)
from backend.services.game_service import get_or_create_game, update_screenshot_stats
from backend.services.screenshot_service import (
    create_screenshot,
    check_duplicate_steam_id,
    check_duplicate_hash,
)
from backend.services.image_processor import (
    compute_sha256_bytes,
    generate_thumbnails,
    get_image_dimensions,
    get_image_format,
)
from backend.services.filesystem import (
    sanitize_filename,
    get_screenshots_dir,
)
from backend.services.metadata_service import fetch_and_apply_metadata

logger = logging.getLogger(__name__)


# ── In-memory SSE queues & cancel flags ──────────────────────────────────────

_progress_queues: dict[int, asyncio.Queue] = {}
_cancel_flags: dict[int, bool] = {}


def get_progress_queue(session_id: int) -> asyncio.Queue:
    """Get or create an SSE progress queue for a session."""
    if session_id not in _progress_queues:
        _progress_queues[session_id] = asyncio.Queue()
    return _progress_queues[session_id]


def cleanup_session(session_id: int) -> None:
    """Remove progress queue and cancel flag for a completed session."""
    _progress_queues.pop(session_id, None)
    _cancel_flags.pop(session_id, None)


def request_cancel(session_id: int) -> None:
    """Set the cancel flag for a running import."""
    _cancel_flags[session_id] = True


def is_cancelled(session_id: int) -> bool:
    """Check if an import has been cancelled."""
    return _cancel_flags.get(session_id, False)


# ── Session DB helpers ───────────────────────────────────────────────────────

async def create_import_session(steam_user_id: str) -> int:
    """Create a new import session record. Returns the session ID."""
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO steam_import_sessions (steam_user_id, status)
           VALUES (?, 'pending')""",
        (steam_user_id,),
    )
    await db.commit()
    return cursor.lastrowid


async def get_import_session(session_id: int) -> dict | None:
    """Get an import session by ID."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM steam_import_sessions WHERE id = ?", (session_id,)
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


async def update_import_session(session_id: int, **fields) -> None:
    """Update import session fields."""
    db = await get_db()
    set_clauses = []
    values = []
    for key, value in fields.items():
        if value is not None:
            set_clauses.append(f"{key} = ?")
            values.append(value)
    if not set_clauses:
        return
    values.append(session_id)
    await db.execute(
        f"UPDATE steam_import_sessions SET {', '.join(set_clauses)} WHERE id = ?",
        values,
    )
    await db.commit()


async def append_error_log(session_id: int, error_msg: str) -> None:
    """Append an error message to the session's error_log JSON array."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT error_log FROM steam_import_sessions WHERE id = ?",
        (session_id,),
    )
    row = await cursor.fetchone()
    existing = json.loads(row[0]) if row and row[0] else []
    existing.append(error_msg)
    await db.execute(
        "UPDATE steam_import_sessions SET error_log = ? WHERE id = ?",
        (json.dumps(existing), session_id),
    )
    await db.commit()


# ── SSE event emitter ────────────────────────────────────────────────────────

async def emit_progress(session_id: int, event_type: str, data: dict) -> None:
    """Push an SSE event to the session's queue."""
    queue = get_progress_queue(session_id)
    await queue.put({"event": event_type, "data": data})


# ── Main import pipeline ─────────────────────────────────────────────────────

async def run_import(
    session_id: int,
    user_id: str,
    steam_login_secure: str = "",
    session_id_cookie: str = "",
    game_ids: list[int] | None = None,
    is_numeric_id: bool = False,
) -> None:
    """Run the full Steam import pipeline as a background task.

    1. Validate profile
    2. Discover games with screenshots (API if key available, HTML fallback)
    3. For each game: fetch screenshot list → download images → save to DB
    4. Emit SSE progress events throughout
    """
    try:
        await update_import_session(
            session_id,
            status="running",
            started_at=datetime.utcnow().isoformat(),
        )
        await emit_progress(session_id, "import_started", {
            "session_id": session_id,
            "status": "running",
        })

        # Retrieve Steam API key (DB override → env var)
        api_key = await get_effective_key("steam_api_key")

        async with SteamScraper(
            user_id=user_id,
            steam_login_secure=steam_login_secure,
            session_id=session_id_cookie,
            is_numeric_id=is_numeric_id,
            api_key=api_key,
        ) as scraper:

            # Step 1: Validate profile
            await emit_progress(session_id, "status", {"message": "Validating Steam profile..."})
            logger.info(
                "Import %d: validating profile for user_id=%s, is_numeric=%s, has_cookies=%s, has_api_key=%s",
                session_id, user_id, is_numeric_id,
                bool(steam_login_secure), bool(api_key),
            )
            try:
                profile = await scraper.validate_profile()
            except ValueError as e:
                logger.error("Import %d: profile validation failed: %s", session_id, e)
                await _fail_import(session_id, str(e))
                return

            await emit_progress(session_id, "profile_validated", {
                "profile_name": profile.profile_name,
                "avatar_url": profile.avatar_url,
            })

            # Step 2: Discover games
            await emit_progress(session_id, "status", {"message": "Discovering games..."})
            logger.info("Import %d: discovering games...", session_id)
            try:
                all_games = await scraper.discover_games()
            except ValueError as e:
                logger.error("Import %d: game discovery failed: %s", session_id, e)
                await _fail_import(session_id, f"Failed to discover games: {e}")
                return

            logger.info("Import %d: found %d games", session_id, len(all_games))

            if not all_games:
                await _fail_import(session_id, "No games with screenshots found on this profile.")
                return

            # Filter to requested games (if specified)
            if game_ids:
                all_games = [g for g in all_games if g.app_id in game_ids]
                if not all_games:
                    await _fail_import(session_id, "None of the selected games found on Steam profile.")
                    return

            # Calculate totals (counts may be 0 if not fetched during discovery)
            total_screenshots = sum(g.screenshot_count for g in all_games)
            await update_import_session(
                session_id,
                total_games=len(all_games),
                total_screenshots=total_screenshots if total_screenshots > 0 else None,
            )

            await emit_progress(session_id, "games_discovered", {
                "total_games": len(all_games),
                "total_screenshots": total_screenshots,
                "games": [
                    {"app_id": g.app_id, "name": g.name, "count": g.screenshot_count}
                    for g in all_games
                ],
            })

            # Step 3: Import each game
            overall_completed = 0
            overall_skipped = 0
            overall_failed = 0
            completed_games = 0

            for game_info in all_games:
                if is_cancelled(session_id):
                    await _cancel_import(session_id)
                    return

                await emit_progress(session_id, "game_start", {
                    "app_id": game_info.app_id,
                    "name": game_info.name,
                    "game_index": completed_games,
                    "total_games": len(all_games),
                })

                game_completed, game_skipped, game_failed = await _import_game(
                    session_id=session_id,
                    scraper=scraper,
                    game_info=game_info,
                    overall_completed=overall_completed,
                    total_screenshots=total_screenshots,
                )

                overall_completed += game_completed
                overall_skipped += game_skipped
                overall_failed += game_failed
                completed_games += 1

                await update_import_session(
                    session_id,
                    completed_games=completed_games,
                    completed_screenshots=overall_completed,
                    skipped_screenshots=overall_skipped,
                    failed_screenshots=overall_failed,
                )

                await emit_progress(session_id, "game_complete", {
                    "app_id": game_info.app_id,
                    "name": game_info.name,
                    "completed": game_completed,
                    "skipped": game_skipped,
                    "failed": game_failed,
                    "overall_completed": overall_completed,
                    "overall_skipped": overall_skipped,
                    "overall_failed": overall_failed,
                    "total_screenshots": total_screenshots,
                })

            # Done!
            await update_import_session(
                session_id,
                status="completed",
                completed_at=datetime.utcnow().isoformat(),
            )
            await emit_progress(session_id, "import_complete", {
                "session_id": session_id,
                "completed": overall_completed,
                "skipped": overall_skipped,
                "failed": overall_failed,
                "total_games": len(all_games),
            })

    except Exception as e:
        logger.exception("Import pipeline crashed")
        await _fail_import(session_id, f"Unexpected error: {e}")
    finally:
        # Send a terminal event so SSE listeners know to disconnect
        await emit_progress(session_id, "done", {})


async def _fail_import(session_id: int, error: str) -> None:
    """Mark an import as failed and emit the error event."""
    logger.error("Import %d failed: %s", session_id, error)
    await update_import_session(
        session_id,
        status="failed",
        completed_at=datetime.utcnow().isoformat(),
    )
    await append_error_log(session_id, error)
    await emit_progress(session_id, "import_error", {"error": error})


async def _cancel_import(session_id: int) -> None:
    """Mark an import as cancelled."""
    logger.info("Import %d cancelled by user", session_id)
    await update_import_session(
        session_id,
        status="cancelled",
        completed_at=datetime.utcnow().isoformat(),
    )
    await emit_progress(session_id, "import_cancelled", {"session_id": session_id})


# ── Per-game import ──────────────────────────────────────────────────────────

async def _import_game(
    session_id: int,
    scraper: SteamScraper,
    game_info: SteamGameScreenshots,
    overall_completed: int,
    total_screenshots: int,
) -> tuple[int, int, int]:
    """Import all screenshots for a single game.

    Returns (completed_count, skipped_count, failed_count).
    """
    completed = 0
    skipped = 0
    failed = 0

    # Get or create the game in our DB
    game = await get_or_create_game(
        name=game_info.name,
        steam_app_id=game_info.app_id,
    )
    game_id = game["id"]
    game_folder = game["folder_name"]

    # Get all screenshot entries (API cache or HTML scraping fallback)
    try:
        screenshots = await scraper.get_game_screenshots(game_info.app_id)
    except Exception as e:
        error_msg = f"Failed to fetch screenshots for {game_info.name}: {e}"
        logger.error(error_msg)
        await append_error_log(session_id, error_msg)
        await emit_progress(session_id, "game_error", {
            "app_id": game_info.app_id,
            "name": game_info.name,
            "error": error_msg,
        })
        return 0, 0, game_info.screenshot_count

    for i, screenshot in enumerate(screenshots):
        if is_cancelled(session_id):
            return completed, skipped, failed

        try:
            result = await _import_single_screenshot(
                session_id=session_id,
                scraper=scraper,
                screenshot=screenshot,
                game_id=game_id,
                game_folder=game_folder,
                game_name=game_info.name,
                index=i,
                total_for_game=len(screenshots),
                overall_completed=overall_completed + completed + skipped,
                total_screenshots=total_screenshots,
            )
            if result == "completed":
                completed += 1
            elif result == "skipped":
                skipped += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            error_msg = f"Screenshot {screenshot.screenshot_id}: {e}"
            logger.error(error_msg)
            await append_error_log(session_id, error_msg)

    # Update game stats after all screenshots imported
    await update_screenshot_stats(game_id)

    # Auto-fetch metadata (cover art, description, etc.) for new games
    try:
        meta = await fetch_and_apply_metadata(game_id)
        if meta.get("fields_updated") or meta.get("cover_downloaded"):
            logger.info(
                "Auto-fetched metadata for %s: fields=%s, cover=%s",
                game_info.name, meta.get("fields_updated"), meta.get("cover_downloaded"),
            )
    except Exception as e:
        logger.warning("Auto-metadata fetch failed for %s: %s", game_info.name, e)

    return completed, skipped, failed


async def _import_single_screenshot(
    session_id: int,
    scraper: SteamScraper,
    screenshot: SteamScreenshot,
    game_id: int,
    game_folder: str,
    game_name: str,
    index: int,
    total_for_game: int,
    overall_completed: int,
    total_screenshots: int,
) -> str:
    """Import a single screenshot. Returns 'completed', 'skipped', or 'failed'."""

    steam_id = screenshot.screenshot_id
    logger.info(
        "Importing screenshot %s (%d/%d for %s): full_url=%s, detail_url=%s",
        steam_id, index + 1, total_for_game, game_name,
        (screenshot.full_image_url or "")[:80],
        (screenshot.detail_url or "")[:80],
    )

    # Check if already imported (by Steam ID)
    if await check_duplicate_steam_id(steam_id):
        await emit_progress(session_id, "screenshot_skipped", {
            "steam_id": steam_id,
            "reason": "already_imported",
            "game_name": game_name,
            "index": index,
            "total_for_game": total_for_game,
            "overall_progress": overall_completed + 1,
            "total_screenshots": total_screenshots,
        })
        return "skipped"

    # If we don't have a full image URL yet, fetch the detail page
    # to get the full-res URL. This applies to both the API path
    # (where file_url is typically empty) and the HTML scraping path.
    if not screenshot.full_image_url and screenshot.detail_url:
        try:
            screenshot = await scraper.get_screenshot_details(screenshot)
        except Exception as e:
            logger.warning("Could not fetch details for %s: %s", steam_id, e)

    # Determine which URL to download
    image_url = screenshot.full_image_url or screenshot.thumbnail_url
    logger.info("Screenshot %s: download URL = %s", steam_id, (image_url or "NONE")[:100])
    if not image_url:
        await emit_progress(session_id, "screenshot_failed", {
            "steam_id": steam_id,
            "error": "No image URL found",
            "game_name": game_name,
        })
        return "failed"

    # Download the image
    image_data = await scraper.download_image(image_url)
    if not image_data:
        await emit_progress(session_id, "screenshot_failed", {
            "steam_id": steam_id,
            "error": "Download failed",
            "game_name": game_name,
        })
        return "failed"

    # Check duplicate by hash
    sha256 = compute_sha256_bytes(image_data)
    existing = await check_duplicate_hash(sha256)
    if existing:
        await emit_progress(session_id, "screenshot_skipped", {
            "steam_id": steam_id,
            "reason": "duplicate_hash",
            "game_name": game_name,
            "index": index,
            "total_for_game": total_for_game,
            "overall_progress": overall_completed + 1,
            "total_screenshots": total_screenshots,
        })
        return "skipped"

    # Save to disk
    # Generate filename: steam_<id>.<ext>
    ext = _guess_extension(image_url, image_data)
    filename = f"steam_{steam_id}.{ext}"
    screenshots_dir = get_screenshots_dir(game_folder)
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    file_path = screenshots_dir / filename
    file_path.write_bytes(image_data)

    # Get image metadata
    dims = get_image_dimensions(file_path)
    width, height = dims if dims else (None, None)
    fmt = get_image_format(file_path)
    file_size = len(image_data)

    # Generate thumbnails
    filename_stem = Path(filename).stem
    thumb_sm, thumb_md = generate_thumbnails(file_path, game_folder, filename_stem)

    # Parse date from scraper
    taken_at = screenshot.date_taken  # already ISO string or None

    # Relative path for DB
    rel_path = f"{game_folder}/screenshots/{filename}"

    # Create DB record
    await create_screenshot(
        game_id=game_id,
        filename=filename,
        file_path=rel_path,
        source="steam_import",
        steam_screenshot_id=steam_id,
        steam_description=screenshot.description or None,
        sha256_hash=sha256,
        width=width,
        height=height,
        format=fmt,
        file_size=file_size,
        taken_at=taken_at,
        thumbnail_path_sm=thumb_sm,
        thumbnail_path_md=thumb_md,
    )

    await emit_progress(session_id, "screenshot_complete", {
        "steam_id": steam_id,
        "filename": filename,
        "game_name": game_name,
        "index": index,
        "total_for_game": total_for_game,
        "overall_progress": overall_completed + 1,
        "total_screenshots": total_screenshots,
    })

    return "completed"


def _guess_extension(url: str, data: bytes) -> str:
    """Guess file extension from URL or magic bytes."""
    # Check URL
    url_lower = url.lower().split("?")[0]
    if url_lower.endswith(".png"):
        return "png"
    elif url_lower.endswith(".jpg") or url_lower.endswith(".jpeg"):
        return "jpg"
    elif url_lower.endswith(".webp"):
        return "webp"
    elif url_lower.endswith(".bmp"):
        return "bmp"

    # Check magic bytes
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    elif data[:2] == b"\xff\xd8":
        return "jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"

    # Default to jpg (most Steam screenshots are JPEG)
    return "jpg"
