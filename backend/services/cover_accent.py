"""Accent colour for a game, taken from its cover art.

Worked out once on the server and stored on the game row, so the browser never
has to decode a cover to theme a page.
"""

import asyncio
import colorsys
import logging
import math
from pathlib import Path

from PIL import Image

from backend.database import get_db
from backend.services.filesystem import safe_library_path

logger = logging.getLogger(__name__)

_startup_task: asyncio.Task | None = None


def accent_from_image(path: Path) -> str:
    """Return a ``#rrggbb`` accent for the image, or ``""`` when it has none.

    Picks the most common vivid hue, then fixes its lightness and saturation so
    it reads as a highlight on the app's dark background whatever the cover.
    """
    try:
        with Image.open(path) as image:
            image.draft("RGB", (64, 64))
            small = image.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
    except Exception:
        return ""

    # 12 hue buckets, weighted towards saturated, mid-bright pixels so a dark
    # or washed-out background doesn't outvote the art's actual colour.
    weights = [0.0] * 12
    hue_x = [0.0] * 12
    hue_y = [0.0] * 12
    pixels = small.tobytes()
    for offset in range(0, len(pixels), 3):
        red, green, blue = pixels[offset:offset + 3]
        hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
        if saturation < 0.25 or lightness < 0.15 or lightness > 0.9:
            continue
        weight = saturation * (1 - abs(lightness - 0.5))
        bucket = int(hue * 12) % 12
        weights[bucket] += weight
        # Average the hue as an angle, so reds either side of 0 don't cancel out.
        hue_x[bucket] += math.cos(hue * math.tau) * weight
        hue_y[bucket] += math.sin(hue * math.tau) * weight

    best = max(range(12), key=weights.__getitem__)
    # Under ~3% of the cover: effectively greyscale.
    if weights[best] < 0.03 * 32 * 32 * 0.5:
        return ""
    hue = (math.atan2(hue_y[best], hue_x[best]) / math.tau) % 1
    # Blues and purples are dark at equal lightness; lift them to stay legible.
    lightness = 0.68 if 0.55 <= hue <= 0.85 else 0.6
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, 0.78)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


async def accent_for_cover(cover_image_path: str) -> str:
    try:
        path = safe_library_path(cover_image_path)
    except Exception:
        return ""
    return await asyncio.to_thread(accent_from_image, path)


async def backfill_missing_accents() -> int:
    """Fill in the accent of every game that has a cover and no accent yet."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, cover_image_path FROM games
           WHERE cover_image_path IS NOT NULL AND accent_color IS NULL"""
    )
    rows = await cursor.fetchall()
    for game_id, cover_image_path in rows:
        accent = await accent_for_cover(cover_image_path)
        # updated_at is left alone: it versions the cover URL, and the cover
        # itself has not changed.
        await db.execute(
            "UPDATE games SET accent_color = ? WHERE id = ? AND accent_color IS NULL",
            (accent, game_id),
        )
        await db.commit()
    if rows:
        logger.info("Worked out accent colours for %d game covers", len(rows))
    return len(rows)


async def start_accent_backfill() -> None:
    global _startup_task

    async def run() -> None:
        try:
            await backfill_missing_accents()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Accent colour backfill failed; it will retry on the next start")

    _startup_task = asyncio.create_task(run(), name="gamevault-cover-accents")


async def stop_accent_backfill() -> None:
    global _startup_task
    task, _startup_task = _startup_task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
