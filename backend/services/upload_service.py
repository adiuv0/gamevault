"""Upload processing service with progress tracking."""

import asyncio
import json as _json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from backend.services import hdr_processor
from backend.services.filesystem import get_screenshots_dir, sanitize_filename
from backend.services.game_service import get_game, update_screenshot_stats
from backend.services.image_processor import (
    compute_sha256,
    extract_date_taken,
    extract_exif,
    generate_thumbnails_with_hash,
    get_image_dimensions,
    get_image_format,
    validate_image,
)
from backend.services.performance_diagnostics import recorder
from backend.services.screenshot_service import (
    check_duplicate_hash,
    create_screenshot,
    get_screenshot_by_source_key,
)

logger = logging.getLogger(__name__)

# Whitelist of allowed image formats (Pillow format name → safe extension).
# Files whose detected format is not in this map are rejected.
ALLOWED_IMAGE_FORMATS: dict[str, str] = {
    "jpeg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "bmp": ".bmp",
    "tiff": ".tiff",
    "gif": ".gif",
    "jxr": ".jxr",
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
    temp_files: list[tuple[int, str, Path]],
    taken_at_override: str | None = None,
    *,
    durable: bool = False,
    emit_complete: bool = True,
) -> list[dict]:
    """Process uploaded files: validate, extract metadata, save, create DB records.

    Emits SSE progress events for each file.
    """
    game = await get_game(game_id)
    if not game:
        if durable:
            raise ValueError(f"Game ID {game_id} not found")
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

    async def report(event: dict) -> None:
        if durable:
            from backend.services.upload_job_service import record_upload_event

            await record_upload_event(task_id, event)
        await emit_progress(task_id, event)

    await report({
        "type": "start",
        "total_files": total,
        "game_name": game["name"],
    })

    for position, (file_index, original_name, temp_path) in enumerate(temp_files):
        file_started = perf_counter()
        file_failed = False
        file_terminal = False
        try:
            await report({
                "type": "file_start",
                "file_index": file_index,
                "filename": original_name,
                "total_files": total,
            })

            # Validate image
            if not await asyncio.to_thread(validate_image, temp_path):
                logger.warning("process_upload: INVALID IMAGE %s (size=%d)",
                               original_name, temp_path.stat().st_size if temp_path.exists() else -1)
                await report({
                    "type": "file_error",
                    "file_index": file_index,
                    "filename": original_name,
                    "error": "Not a valid image file",
                })
                file_terminal = True
                continue

            # Determine real format from magic bytes and enforce whitelist.
            # This prevents Stored XSS via malicious extensions (e.g. .html).
            detected_format = (await asyncio.to_thread(get_image_format, temp_path) or "").lower()
            if detected_format not in ALLOWED_IMAGE_FORMATS:
                await report({
                    "type": "file_error",
                    "file_index": file_index,
                    "filename": original_name,
                    "error": f"Unsupported image format: {detected_format or 'unknown'}",
                })
                file_terminal = True
                continue
            ext = ALLOWED_IMAGE_FORMATS[detected_format]

            img_dims = await asyncio.to_thread(get_image_dimensions, temp_path)
            img_format = detected_format
            file_size = temp_path.stat().st_size
            sha256 = await asyncio.to_thread(compute_sha256, temp_path)
            exif = await asyncio.to_thread(extract_exif, temp_path)
            date_taken_exif = await asyncio.to_thread(extract_date_taken, temp_path)

            img_data = {
                "width": img_dims[0] if img_dims else None,
                "height": img_dims[1] if img_dims else None,
                "format": img_format,
                "file_size": file_size,
                "sha256_hash": sha256,
                "exif_data": _json.dumps(exif) if exif else None,
                "taken_at": date_taken_exif.isoformat() if date_taken_exif else None,
                "is_hdr": await asyncio.to_thread(hdr_processor.is_hdr_source, temp_path),
            }

            source_key = f"upload:{task_id}:{file_index}"
            if durable:
                existing_source = await get_screenshot_by_source_key(game_id, source_key)
                if existing_source:
                    results.append(existing_source)
                    await report({
                        "type": "file_complete",
                        "file_index": file_index,
                        "filename": existing_source["filename"],
                        "screenshot_id": existing_source["id"],
                        "total_files": total,
                        "completed": position + 1,
                    })
                    file_terminal = True
                    continue

            # Check for duplicates by hash BEFORE generating thumbnails
            dup = await check_duplicate_hash(img_data["sha256_hash"])
            if dup:
                logger.info("process_upload: DUPLICATE %s (hash=%s, dup_of=%s)",
                            original_name, img_data["sha256_hash"][:12], dup['filename'])
                await report({
                    "type": "file_skipped",
                    "file_index": file_index,
                    "filename": original_name,
                    "reason": f"Duplicate of {dup['filename']}",
                })
                file_terminal = True
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
            if durable:
                from backend.services.upload_job_service import (
                    get_reserved_target,
                    reserve_target,
                )

                reserved = await get_reserved_target(task_id, file_index)
                if reserved is None:
                    filename = _ensure_unique_filename(screenshots_dir, filename)
                    filename = await reserve_target(task_id, file_index, filename)
                else:
                    filename = reserved
            else:
                filename = _ensure_unique_filename(screenshots_dir, filename)
            final_path = screenshots_dir / filename

            if final_path.exists():
                existing_hash = await asyncio.to_thread(compute_sha256, final_path)
                if not durable or existing_hash != sha256:
                    raise FileExistsError(f"Upload destination already exists: {filename}")
            else:
                await asyncio.to_thread(shutil.copy2, str(temp_path), str(final_path))
            logger.info("process_upload: SAVED %s -> %s (%d bytes)",
                        original_name, final_path, final_path.stat().st_size)

            # Generate thumbnails
            sm, md, preview, perceptual_hash = await asyncio.to_thread(
                generate_thumbnails_with_hash, final_path, folder_name, Path(filename).stem
            )

            # Create file_path relative to library
            rel_path = f"{folder_name}/screenshots/{filename}"

            # Create DB record
            screenshot = await create_screenshot(
                game_id=game_id,
                filename=filename,
                file_path=rel_path,
                source="upload",
                recalculate_stats=False,
                thumbnail_path_sm=sm,
                thumbnail_path_md=md,
                preview_path=preview,
                file_size=img_data.get("file_size"),
                width=img_data.get("width"),
                height=img_data.get("height"),
                format=img_data.get("format"),
                taken_at=taken_at.isoformat() if taken_at else None,
                sha256_hash=img_data.get("sha256_hash"),
                exif_data=img_data.get("exif_data"),
                perceptual_hash=perceptual_hash,
                source_key=source_key,
                is_hdr=img_data["is_hdr"],
            )

            results.append(screenshot)

            await report({
                "type": "file_complete",
                "file_index": file_index,
                "filename": filename,
                "screenshot_id": screenshot["id"],
                "total_files": total,
                "completed": position + 1,
            })
            file_terminal = True

        except Exception as e:
            file_failed = True
            logger.exception("process_upload: EXCEPTION for %s", original_name)
            await report({
                "type": "file_error",
                "file_index": file_index,
                "filename": original_name,
                "error": str(e),
            })
            file_terminal = True
        finally:
            recorder.record(
                "import",
                "browser_file",
                (perf_counter() - file_started) * 1000,
                threshold_ms=5000,
                error=file_failed,
            )
            if not durable or file_terminal:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    if results:
        await update_screenshot_stats(game_id)

    if emit_complete:
        await report({
            "type": "complete",
            "total_processed": len(results),
            "total_files": total,
        })

    return results
