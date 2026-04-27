"""Special K filesystem importer.

Scans a user-supplied root directory containing per-game subfolders, finds
``.jxr`` and ``.png`` screenshots inside each, and imports them into the
GameVault library — generating SDR thumbnails for HDR sources via
``hdr_processor`` and preserving the originals on disk for download.

Layout assumed:

    <root>/
        GameA/
            ... any nested folders (HDR/, SDR/, etc.) ...
                screenshot1.jxr
                screenshot2.png
        GameB/
            ...

Top-level subfolder name = game name. Nested layout is irrelevant — we walk
recursively under each game folder for the supported extensions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend.database import get_db
from backend.services.filesystem import get_screenshots_dir
from backend.services.game_service import (
    get_or_create_game,
    update_screenshot_stats,
)
from backend.services.image_processor import (
    compute_sha256,
    extract_date_taken,
    extract_exif,
    generate_thumbnails,
    get_image_dimensions,
    get_image_format,
    validate_image,
)
from backend.services.metadata_service import fetch_and_apply_metadata
from backend.services.screenshot_service import (
    check_duplicate_hash,
    create_screenshot,
)

logger = logging.getLogger(__name__)


SUPPORTED_EXTENSIONS = {".jxr", ".png"}
HDR_FOLDER_HINTS = ("hdr", "jxr")
SDR_FOLDER_HINTS = ("sdr", "ldr")


# ── In-memory SSE queues & cancel flags ──────────────────────────────────────

_progress_queues: dict[int, asyncio.Queue] = {}
_cancel_flags: dict[int, bool] = {}


def get_progress_queue(session_id: int) -> asyncio.Queue:
    if session_id not in _progress_queues:
        _progress_queues[session_id] = asyncio.Queue()
    return _progress_queues[session_id]


def cleanup_session(session_id: int) -> None:
    _progress_queues.pop(session_id, None)
    _cancel_flags.pop(session_id, None)


def request_cancel(session_id: int) -> None:
    _cancel_flags[session_id] = True


def is_cancelled(session_id: int) -> bool:
    return _cancel_flags.get(session_id, False)


# ── Session DB helpers ───────────────────────────────────────────────────────


async def create_import_session(scan_path: str) -> int:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO specialk_import_sessions (scan_path, status)
           VALUES (?, 'pending')""",
        (scan_path,),
    )
    await db.commit()
    return cursor.lastrowid


async def get_import_session(session_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM specialk_import_sessions WHERE id = ?", (session_id,)
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


async def update_import_session(session_id: int, **fields) -> None:
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
        f"UPDATE specialk_import_sessions SET {', '.join(set_clauses)} WHERE id = ?",
        values,
    )
    await db.commit()


async def append_error_log(session_id: int, error_msg: str) -> None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT error_log FROM specialk_import_sessions WHERE id = ?",
        (session_id,),
    )
    row = await cursor.fetchone()
    existing = json.loads(row[0]) if row and row[0] else []
    existing.append(error_msg)
    await db.execute(
        "UPDATE specialk_import_sessions SET error_log = ? WHERE id = ?",
        (json.dumps(existing), session_id),
    )
    await db.commit()


# ── SSE event emitter ────────────────────────────────────────────────────────


async def emit_progress(session_id: int, event_type: str, data: dict) -> None:
    queue = get_progress_queue(session_id)
    await queue.put({"event": event_type, "data": data})


# ── Scanning ─────────────────────────────────────────────────────────────────


@dataclass
class ScannedGame:
    folder_name: str  # On-disk folder name as found
    suggested_name: str  # Cleaned-up name for display + matching
    files: list[Path]  # Absolute paths to all screenshot files
    has_hdr: bool
    has_sdr: bool


def _clean_game_name(folder_name: str) -> str:
    """Convert a folder name to a likely game name.

    Special K profiles often use the binary name (``Cyberpunk2077.exe`` or
    ``CP2077``); strip executable suffixes and split CamelCase / letter-digit
    boundaries so common patterns become readable.
    """
    name = folder_name.strip()
    # Drop a trailing .exe if present
    if name.lower().endswith(".exe"):
        name = name[:-4]
    # camelCase: lowercase → uppercase
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    # ACRONYM + Word: uppercase → uppercase+lowercase
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    # letter → digit ("Cyberpunk2077" → "Cyberpunk 2077")
    name = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name or folder_name


def _classify_file(path: Path) -> tuple[bool, bool]:
    """Return (is_hdr, is_sdr) for a screenshot path.

    Considers extension first (.jxr is always HDR), then a folder-name hint.
    Falls back to (False, True) for plain PNGs without a hint.
    """
    ext = path.suffix.lower()
    if ext == ".jxr":
        return (True, False)
    parts_lower = [p.lower() for p in path.parts]
    in_hdr_folder = any(
        any(hint in part for hint in HDR_FOLDER_HINTS) for part in parts_lower
    )
    in_sdr_folder = any(
        any(hint in part for hint in SDR_FOLDER_HINTS) for part in parts_lower
    )
    if in_hdr_folder and not in_sdr_folder:
        return (True, False)
    return (False, True)


def scan_path(root: Path) -> list[ScannedGame]:
    """Scan ``root`` for per-game subfolders containing screenshots.

    Each top-level child directory is treated as a game. Screenshots are
    collected recursively under it.
    """
    if not root.exists() or not root.is_dir():
        return []

    games: list[ScannedGame] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        files: list[Path] = []
        has_hdr = False
        has_sdr = False
        for path in child.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            files.append(path)
            is_hdr, is_sdr = _classify_file(path)
            has_hdr = has_hdr or is_hdr
            has_sdr = has_sdr or is_sdr

        if not files:
            continue

        games.append(
            ScannedGame(
                folder_name=child.name,
                suggested_name=_clean_game_name(child.name),
                files=files,
                has_hdr=has_hdr,
                has_sdr=has_sdr,
            )
        )

    return games


# ── Import pipeline ──────────────────────────────────────────────────────────


async def run_import(
    session_id: int,
    scan_path_str: str,
    folder_names: list[str] | None = None,
) -> None:
    """Run the Special K import pipeline as a background task."""
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

        root = Path(scan_path_str)
        if not root.exists() or not root.is_dir():
            await _fail_import(session_id, f"Path does not exist: {scan_path_str}")
            return

        await emit_progress(session_id, "status", {"message": f"Scanning {root}..."})
        all_games = await asyncio.to_thread(scan_path, root)

        if folder_names:
            wanted = set(folder_names)
            all_games = [g for g in all_games if g.folder_name in wanted]

        if not all_games:
            await _fail_import(session_id, "No screenshots found in the specified path.")
            return

        total_screenshots = sum(len(g.files) for g in all_games)
        await update_import_session(
            session_id,
            total_games=len(all_games),
            total_screenshots=total_screenshots,
        )
        await emit_progress(session_id, "games_discovered", {
            "total_games": len(all_games),
            "total_screenshots": total_screenshots,
            "games": [
                {
                    "folder_name": g.folder_name,
                    "name": g.suggested_name,
                    "count": len(g.files),
                    "has_hdr": g.has_hdr,
                    "has_sdr": g.has_sdr,
                }
                for g in all_games
            ],
        })

        overall_completed = 0
        overall_skipped = 0
        overall_failed = 0
        completed_games = 0

        for scanned in all_games:
            if is_cancelled(session_id):
                await _cancel_import(session_id)
                return

            await emit_progress(session_id, "game_start", {
                "folder_name": scanned.folder_name,
                "name": scanned.suggested_name,
                "game_index": completed_games,
                "total_games": len(all_games),
            })

            game_completed, game_skipped, game_failed = await _import_game(
                session_id=session_id,
                scanned=scanned,
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
                "folder_name": scanned.folder_name,
                "name": scanned.suggested_name,
                "completed": game_completed,
                "skipped": game_skipped,
                "failed": game_failed,
                "overall_completed": overall_completed,
                "overall_skipped": overall_skipped,
                "overall_failed": overall_failed,
                "total_screenshots": total_screenshots,
            })

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
        logger.exception("Special K import crashed")
        await _fail_import(session_id, f"Unexpected error: {e}")
    finally:
        await emit_progress(session_id, "done", {})


async def _fail_import(session_id: int, error: str) -> None:
    logger.error("Special K import %d failed: %s", session_id, error)
    await update_import_session(
        session_id,
        status="failed",
        completed_at=datetime.utcnow().isoformat(),
    )
    await append_error_log(session_id, error)
    await emit_progress(session_id, "import_error", {"error": error})


async def _cancel_import(session_id: int) -> None:
    logger.info("Special K import %d cancelled by user", session_id)
    await update_import_session(
        session_id,
        status="cancelled",
        completed_at=datetime.utcnow().isoformat(),
    )
    await emit_progress(session_id, "import_cancelled", {"session_id": session_id})


# ── Per-game import ──────────────────────────────────────────────────────────


async def _import_game(
    session_id: int,
    scanned: ScannedGame,
    overall_completed: int,
    total_screenshots: int,
) -> tuple[int, int, int]:
    """Import all screenshots for a single scanned game folder.

    Returns (completed, skipped, failed).
    """
    completed = 0
    skipped = 0
    failed = 0

    game = await get_or_create_game(name=scanned.suggested_name)
    game_id = game["id"]
    game_folder = game["folder_name"]

    for index, src_path in enumerate(scanned.files):
        if is_cancelled(session_id):
            return completed, skipped, failed

        try:
            result = await _import_single_file(
                session_id=session_id,
                src_path=src_path,
                game_id=game_id,
                game_folder=game_folder,
                game_name=scanned.suggested_name,
                index=index,
                total_for_game=len(scanned.files),
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
            error_msg = f"{scanned.suggested_name} / {src_path.name}: {e}"
            logger.exception(error_msg)
            await append_error_log(session_id, error_msg)
            await emit_progress(session_id, "screenshot_failed", {
                "filename": src_path.name,
                "game_name": scanned.suggested_name,
                "error": str(e),
            })

    await update_screenshot_stats(game_id)

    # Auto-fetch metadata for new games (best effort)
    try:
        await fetch_and_apply_metadata(game_id)
    except Exception as e:
        logger.warning("Special K auto-metadata fetch failed for %s: %s",
                       scanned.suggested_name, e)

    return completed, skipped, failed


async def _import_single_file(
    session_id: int,
    src_path: Path,
    game_id: int,
    game_folder: str,
    game_name: str,
    index: int,
    total_for_game: int,
    overall_completed: int,
    total_screenshots: int,
) -> str:
    """Import a single Special K screenshot file. Returns 'completed' | 'skipped' | 'failed'."""

    if not validate_image(src_path):
        await emit_progress(session_id, "screenshot_failed", {
            "filename": src_path.name,
            "game_name": game_name,
            "error": "Not a valid image",
        })
        return "failed"

    sha256 = await asyncio.to_thread(compute_sha256, src_path)
    duplicate = await check_duplicate_hash(sha256)
    if duplicate:
        await emit_progress(session_id, "screenshot_skipped", {
            "filename": src_path.name,
            "game_name": game_name,
            "reason": "duplicate_hash",
            "index": index,
            "total_for_game": total_for_game,
            "overall_progress": overall_completed + 1,
            "total_screenshots": total_screenshots,
        })
        return "skipped"

    # Determine destination filename — keep extension, prefix with game name
    # and source mtime so it sorts naturally with other library entries.
    mtime = datetime.fromtimestamp(src_path.stat().st_mtime)
    dest_dir = get_screenshots_dir(game_folder)
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext = src_path.suffix.lower()
    base_stem = f"specialk_{mtime.strftime('%Y%m%d_%H%M%S')}_{src_path.stem}"
    filename = f"{base_stem}{ext}"
    dest_path = dest_dir / filename
    counter = 1
    while dest_path.exists():
        filename = f"{base_stem} ({counter}){ext}"
        dest_path = dest_dir / filename
        counter += 1

    await asyncio.to_thread(shutil.copy2, str(src_path), str(dest_path))

    # Image metadata + thumbnails (HDR-aware via image_processor)
    dims = await asyncio.to_thread(get_image_dimensions, dest_path)
    width, height = dims if dims else (None, None)
    fmt = await asyncio.to_thread(get_image_format, dest_path)
    file_size = dest_path.stat().st_size
    exif = await asyncio.to_thread(extract_exif, dest_path)
    date_taken = await asyncio.to_thread(extract_date_taken, dest_path)
    taken_at = (date_taken or mtime).isoformat()

    filename_stem = Path(filename).stem
    sm, md = await asyncio.to_thread(
        generate_thumbnails, dest_path, game_folder, filename_stem
    )

    rel_path = f"{game_folder}/screenshots/{filename}"

    await create_screenshot(
        game_id=game_id,
        filename=filename,
        file_path=rel_path,
        source="specialk_import",
        sha256_hash=sha256,
        width=width,
        height=height,
        format=fmt,
        file_size=file_size,
        taken_at=taken_at,
        exif_data=json.dumps(exif) if exif else None,
        thumbnail_path_sm=sm,
        thumbnail_path_md=md,
    )

    await emit_progress(session_id, "screenshot_complete", {
        "filename": filename,
        "game_name": game_name,
        "index": index,
        "total_for_game": total_for_game,
        "overall_progress": overall_completed + 1,
        "total_screenshots": total_screenshots,
    })

    return "completed"
