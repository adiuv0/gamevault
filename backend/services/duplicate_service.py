"""Resumable perceptual-hash backfill and duplicate candidate review."""

import asyncio
from pathlib import Path

from backend.database import get_db
from backend.services.filesystem import safe_library_path
from backend.services.game_service import update_screenshot_stats
from backend.services.image_processor import compute_perceptual_hash

JOB_KIND = "duplicate_backfill"
SCAN_BATCH_SIZE = 25
MAX_HASH_DISTANCE = 8
LIKELY_HASH_DISTANCE = 4

_scan_lock = asyncio.Lock()


def _hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


async def get_latest_scan_job() -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM maintenance_jobs WHERE kind = ? ORDER BY id DESC LIMIT 1",
        (JOB_KIND,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(zip((d[0] for d in cursor.description), row))


async def start_scan_job() -> dict:
    """Create a scan job or return the unfinished job to be resumed."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM maintenance_jobs
           WHERE kind = ? AND status IN ('pending', 'running', 'failed')
           ORDER BY id DESC LIMIT 1""",
        (JOB_KIND,),
    )
    row = await cursor.fetchone()
    if row is not None:
        job = dict(zip((d[0] for d in cursor.description), row))
        await db.execute(
            """UPDATE maintenance_jobs SET status = 'pending', error_message = NULL,
                   updated_at = datetime('now') WHERE id = ?""",
            (job["id"],),
        )
        await db.commit()
        job["status"] = "pending"
        job["error_message"] = None
        return job

    cursor = await db.execute("SELECT COUNT(*) FROM screenshots")
    total = (await cursor.fetchone())[0]
    cursor = await db.execute(
        "INSERT INTO maintenance_jobs (kind, total) VALUES (?, ?)",
        (JOB_KIND, total),
    )
    await db.commit()
    return await _get_job(cursor.lastrowid)


async def _get_job(job_id: int) -> dict:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM maintenance_jobs WHERE id = ?", (job_id,))
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"Maintenance job {job_id} not found")
    return dict(zip((d[0] for d in cursor.description), row))


async def run_scan_job(job_id: int) -> None:
    """Resume a duplicate scan from its durable screenshot cursor."""
    if _scan_lock.locked():
        return
    async with _scan_lock:
        db = await get_db()
        await db.execute(
            """UPDATE maintenance_jobs SET status = 'running', updated_at = datetime('now')
               WHERE id = ?""",
            (job_id,),
        )
        await db.commit()

        try:
            while True:
                job = await _get_job(job_id)
                cursor = await db.execute(
                    """SELECT id, game_id, file_path, filename, width, height, taken_at,
                              uploaded_at, capture_group_id, perceptual_hash
                       FROM screenshots WHERE id > ? ORDER BY id LIMIT ?""",
                    (job["cursor_id"], SCAN_BATCH_SIZE),
                )
                columns = [d[0] for d in cursor.description]
                assets = [dict(zip(columns, row)) for row in await cursor.fetchall()]
                if not assets:
                    break

                batch_errors = 0
                for asset in assets:
                    try:
                        await _index_asset(asset)
                    except Exception:
                        batch_errors += 1

                last_id = assets[-1]["id"]
                await db.execute(
                    """UPDATE maintenance_jobs SET cursor_id = ?, processed = processed + ?,
                           errors = errors + ?, updated_at = datetime('now') WHERE id = ?""",
                    (last_id, len(assets), batch_errors, job_id),
                )
                await db.commit()

            await db.execute(
                """UPDATE maintenance_jobs SET status = 'completed',
                       completed_at = datetime('now'), updated_at = datetime('now')
                   WHERE id = ?""",
                (job_id,),
            )
            await db.commit()
        except Exception as e:
            await db.execute(
                """UPDATE maintenance_jobs SET status = 'failed', error_message = ?,
                       updated_at = datetime('now') WHERE id = ?""",
                (str(e), job_id),
            )
            await db.commit()


async def _index_asset(asset: dict) -> None:
    db = await get_db()
    perceptual_hash = asset.get("perceptual_hash")
    if not perceptual_hash:
        file_path = safe_library_path(asset.get("file_path"))
        if not file_path.exists():
            raise FileNotFoundError(file_path)
        perceptual_hash = await asyncio.to_thread(compute_perceptual_hash, file_path)
        if not perceptual_hash:
            raise ValueError(f"Could not fingerprint {file_path}")
        await db.execute(
            "UPDATE screenshots SET perceptual_hash = ? WHERE id = ?",
            (perceptual_hash, asset["id"]),
        )

    if not asset.get("width") or not asset.get("height"):
        return

    cursor = await db.execute(
        """SELECT id, capture_group_id, filename, perceptual_hash, taken_at, uploaded_at
           FROM screenshots
           WHERE game_id = ? AND id < ? AND width = ? AND height = ?
             AND perceptual_hash IS NOT NULL
             AND COALESCE(capture_group_id, id) != ?
             AND (
                 (? IS NOT NULL AND taken_at IS NOT NULL
                  AND ABS(strftime('%s', taken_at) - strftime('%s', ?)) <= 300)
                 OR ABS(strftime('%s', uploaded_at) - strftime('%s', ?)) <= 600
             )
           ORDER BY id DESC LIMIT 200""",
        (
            asset["game_id"],
            asset["id"],
            asset["width"],
            asset["height"],
            asset.get("capture_group_id") or asset["id"],
            asset.get("taken_at"),
            asset.get("taken_at"),
            asset["uploaded_at"],
        ),
    )
    for candidate_id, _, candidate_filename, candidate_hash, _, _ in await cursor.fetchall():
        distance = _hash_distance(perceptual_hash, candidate_hash)
        if distance > MAX_HASH_DISTANCE:
            continue
        same_stem = Path(candidate_filename).stem.casefold() == Path(asset["filename"]).stem.casefold()
        confidence = "likely" if distance <= LIKELY_HASH_DISTANCE else "possible"
        reason = "same_name_and_visual" if same_stem else "visual_similarity"
        await db.execute(
            """INSERT OR IGNORE INTO duplicate_candidates
               (game_id, left_screenshot_id, right_screenshot_id,
                hash_distance, confidence, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (asset["game_id"], candidate_id, asset["id"], distance, confidence, reason),
        )


async def list_candidates(
    status: str = "pending", page: int = 1, limit: int = 25
) -> tuple[list[dict], int]:
    if status not in {"pending", "grouped", "dismissed"}:
        status = "pending"
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM duplicate_candidates WHERE status = ?", (status,)
    )
    total = (await cursor.fetchone())[0]
    cursor = await db.execute(
        """SELECT dc.*, g.name AS game_name,
                  l.filename AS left_filename, l.format AS left_format,
                  l.width AS left_width, l.height AS left_height,
                  l.taken_at AS left_taken_at,
                  r.filename AS right_filename, r.format AS right_format,
                  r.width AS right_width, r.height AS right_height,
                  r.taken_at AS right_taken_at
           FROM duplicate_candidates dc
           JOIN games g ON g.id = dc.game_id
           JOIN screenshots l ON l.id = dc.left_screenshot_id
           JOIN screenshots r ON r.id = dc.right_screenshot_id
           WHERE dc.status = ? ORDER BY dc.id DESC LIMIT ? OFFSET ?""",
        (status, limit, (page - 1) * limit),
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in await cursor.fetchall()], total


async def group_candidate(candidate_id: int) -> dict:
    db = await get_db()
    candidate = await _get_candidate(candidate_id)
    left_group = candidate["left_group"] or candidate["left_screenshot_id"]
    right_group = candidate["right_group"] or candidate["right_screenshot_id"]

    if left_group != right_group:
        target_group = min(left_group, right_group)
        source_group = max(left_group, right_group)
        await db.execute(
            """UPDATE screenshots SET capture_group_id = ?, updated_at = datetime('now')
               WHERE game_id = ? AND COALESCE(capture_group_id, id) = ?""",
            (target_group, candidate["game_id"], source_group),
        )
    await db.execute(
        """UPDATE duplicate_candidates SET status = 'grouped', updated_at = datetime('now')
           WHERE id = ?""",
        (candidate_id,),
    )
    await db.execute(
        """UPDATE duplicate_candidates AS dc
           SET status = 'grouped', updated_at = datetime('now')
           WHERE dc.game_id = ? AND dc.status = 'pending'
             AND EXISTS (
                 SELECT 1
                 FROM screenshots l
                 JOIN screenshots r
                   ON r.id = dc.right_screenshot_id
                 WHERE l.id = dc.left_screenshot_id
                   AND COALESCE(l.capture_group_id, l.id)
                       = COALESCE(r.capture_group_id, r.id)
             )""",
        (candidate["game_id"],),
    )
    await update_screenshot_stats(candidate["game_id"], commit=False)
    await db.commit()
    return await _get_candidate(candidate_id)


async def dismiss_candidate(candidate_id: int) -> dict:
    db = await get_db()
    await _get_candidate(candidate_id)
    await db.execute(
        """UPDATE duplicate_candidates SET status = 'dismissed', updated_at = datetime('now')
           WHERE id = ?""",
        (candidate_id,),
    )
    await db.commit()
    return await _get_candidate(candidate_id)


async def _get_candidate(candidate_id: int) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """SELECT dc.*, l.capture_group_id AS left_group,
                  r.capture_group_id AS right_group
           FROM duplicate_candidates dc
           JOIN screenshots l ON l.id = dc.left_screenshot_id
           JOIN screenshots r ON r.id = dc.right_screenshot_id
           WHERE dc.id = ?""",
        (candidate_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"Duplicate candidate {candidate_id} not found")
    return dict(zip((d[0] for d in cursor.description), row))
