"""Upload processing service with progress tracking."""

import asyncio
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import json as _json

from backend.config import settings

logger = logging.getLogger(__name__)

from backend.services.filesystem import get_screenshots_dir, sanitize_filename
from backend.services.game_service import get_game
from backend.services.image_processor import (
    compute_sha256,
    extract_date_taken,
    extract_exif,
    generate_thumbnails,
    get_image_dimensions,
    get_image_format,
    validate_image,
)
from backend.services.screenshot_service import (
    check_duplicate_hash,
    create_screenshot,
)

# Whitelist of allowed image formats (Pillow format name → safe extension).
# Files whose detected format is not in this map are rejected.
ALLOWED_IMAGE_FORMATS: dict[str, str] = {
    "jpeg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "bmp": ".bmp",
    "tiff": ".tiff",
    "gif": ".gif",
}


# In-memory progress tracking for SSE
_progress_queues: dict[str, asyncio.Queue] = {}


def create_task_id() -> str:
    """Generate a unique task ID for upload tracking."""
    return str(uuid4())


def get_progress_queue(task_id: str) -> asyncio.Queue:
    """Get or create a progress queue for a task."""
    if task_id not in _progress_queues:
        _progress_queues[task_id] = asyncio.Queue()
    return _progress_queues[task_id]


def cleanup_progress(task_id: str) -> None:
    """Remove a progress queue when done."""
    _progress_queues.pop(task_id, None)


async def emit_progress(task_id: str, event: dict) -> None:
    """Emit a progress event to the SSE stream."""
    queue = _progress_queues.get(task_id)
    if queue:
        await queue.put(event)


def _generate_filename(game_name: str, taken_at: datetime | None, original_ext: str) -> str:
    """Generate a screenshot filename.

    Format: GameName YYYY_MM_DD HH_MM counter.ext
    """
    if taken_at:
        date_part = taken_at.strftime("%Y_%m_%d %H_%M")
    else:
        date_part = datetime.now().strftime("%Y_%m_%d %H_%M")

    safe_name = sanitize_filename(game_name)
    # Truncate game name if too long
    if len(safe_name) > 60:
        safe_name = safe_name[:60].rstrip()

    base = f"{safe_name} {date_part}"
    ext = original_ext.lower()
    if not ext.startswith("."):
        ext = f".{ext}"

    return f"{base}{ext}"


def _ensure_unique_filename(directory: Path, filename: str) -> str:
    """Ensure a filename is unique within a directory by appending a counter."""
    path = directory / filename
    if not path.exists():
        return filename

    stem = path.stem
    ext = path.suffix
    counter = 1
    while True:
        new_name = f"{stem} ({counter}){ext}"
        if not (directory / new_name).exists():
            return new_name
        counter += 1


async def process_upload(
    task_id: str,
    game_id: int,
    temp_files: list[tuple[str, Path]],  # [(original_filename, temp_path), ...]
    taken_at_override: str | None = None,
) -> list[dict]:
    """Process uploaded files: validate, extract metadata, save, create DB records.

    Emits SSE progress events for each file.
    """
    game = await get_game(game_id)
    if not game:
        logger.error("process_upload: game_id=%d not found", game_id)
        await emit_progress(task_id, {
            "type": "error",
            "message": f"Game ID {game_id} not found",
        })
        await emit_progress(task_id, {"type": "complete"})
        return []

    folder_name = game["folder_name"]
    screenshots_dir = get_screenshots_dir(folder_name)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    total = len(temp_files)
    results = []
    logger.info("process_upload: game=%s (%d), %d files, dir=%s",
                game["name"], game_id, total, screenshots_dir)

    await emit_progress(task_id, {
        "type": "start",
        "total_files": total,
        "game_name": game["name"],
    })

    for i, (original_name, temp_path) in enumerate(temp_files):
        try:
            await emit_progress(task_id, {
                "type": "file_start",
                "file_index": i,
                "filename": original_name,
                "total_files": total,
            })

            # Validate image
            if not validate_image(temp_path):
                logger.warning("process_upload: INVALID IMAGE %s (size=%d)",
                               original_name, temp_path.stat().st_size if temp_path.exists() else -1)
                await emit_progress(task_id, {
                    "type": "file_error",
                    "file_index": i,
                    "filename": original_name,
                    "error": "Not a valid image file",
                })
                continue

            # Determine real format from magic bytes and enforce whitelist.
            # This prevents Stored XSS via malicious extensions (e.g. .html).
            detected_format = (get_image_format(temp_path) or "").lower()
            if detected_format not in ALLOWED_IMAGE_FORMATS:
                await emit_progress(task_id, {
                    "type": "file_error",
                    "file_index": i,
                    "filename": original_name,
                    "error": f"Unsupported image format: {detected_format or 'unknown'}",
                })
                continue
            ext = ALLOWED_IMAGE_FORMATS[detected_format]

            img_dims = get_image_dimensions(temp_path)
            img_format = get_image_format(temp_path)
            file_size = temp_path.stat().st_size
            sha256 = compute_sha256(temp_path)
            exif = extract_exif(temp_path)
            date_taken_exif = extract_date_taken(temp_path)

            img_data = {
                "width": img_dims[0] if img_dims else None,
                "height": img_dims[1] if img_dims else None,
                "format": img_format,
                "file_size": file_size,
                "sha256_hash": sha256,
                "exif_data": _json.dumps(exif) if exif else None,
                "taken_at": date_taken_exif.isoformat() if date_taken_exif else None,
            }

            # Check for duplicates by hash BEFORE generating thumbnails
            dup = await check_duplicate_hash(img_data["sha256_hash"])
            if dup:
                logger.info("process_upload: DUPLICATE %s (hash=%s, dup_of=%s)",
                            original_name, img_data["sha256_hash"][:12], dup['filename'])
                await emit_progress(task_id, {
                    "type": "file_skipped",
                    "file_index": i,
                    "filename": original_name,
                    "reason": f"Duplicate of {dup['filename']}",
                })
                continue

            # Determine taken_at
            taken_at = None
            if taken_at_override:
                try:
                    taken_at = datetime.fromisoformat(taken_at_override)
                except ValueError:
                    pass
            if not taken_at and img_data.get("taken_at"):
                taken_at = datetime.fromisoformat(img_data["taken_at"])

            # Generate final filename and save
            filename = _generate_filename(game["name"], taken_at, ext)
            filename = _ensure_unique_filename(screenshots_dir, filename)
            final_path = screenshots_dir / filename

            shutil.copy2(str(temp_path), str(final_path))
            logger.info("process_upload: SAVED %s -> %s (%d bytes)",
                        original_name, final_path, final_path.stat().st_size)

            # Generate thumbnails
            sm, md = generate_thumbnails(final_path, folder_name, Path(filename).stem)

            # Create file_path relative to library
            rel_path = f"{folder_name}/screenshots/{filename}"

            # Create DB record
            screenshot = await create_screenshot(
                game_id=game_id,
                filename=filename,
                file_path=rel_path,
                source="upload",
                thumbnail_path_sm=sm,
                thumbnail_path_md=md,
                file_size=img_data.get("file_size"),
                width=img_data.get("width"),
                height=img_data.get("height"),
                format=img_data.get("format"),
                taken_at=taken_at.isoformat() if taken_at else None,
                sha256_hash=img_data.get("sha256_hash"),
                exif_data=img_data.get("exif_data"),
            )

            results.append(screenshot)

            await emit_progress(task_id, {
                "type": "file_complete",
                "file_index": i,
                "filename": filename,
                "screenshot_id": screenshot["id"],
                "total_files": total,
                "completed": i + 1,
            })

        except Exception as e:
            logger.exception("process_upload: EXCEPTION for %s", original_name)
            await emit_progress(task_id, {
                "type": "file_error",
                "file_index": i,
                "filename": original_name,
                "error": str(e),
            })
        finally:
            # Clean up temp file
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    await emit_progress(task_id, {
        "type": "complete",
        "total_processed": len(results),
        "total_files": total,
    })

    return results
