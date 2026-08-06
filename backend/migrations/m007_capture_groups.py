"""Add logical capture groups and perceptual fingerprints to screenshot assets."""

from pathlib import Path

import aiosqlite


def _is_hdr(filename: str, image_format: str | None) -> bool:
    return (image_format or "").lower() == "jxr" or Path(filename).suffix.lower() in {
        ".jxr",
        ".wdp",
    }


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute("ALTER TABLE screenshots ADD COLUMN capture_group_id INTEGER")
    await db.execute("ALTER TABLE screenshots ADD COLUMN perceptual_hash TEXT")
    await db.execute("ALTER TABLE screenshots ADD COLUMN source_key TEXT")
    await db.execute("UPDATE screenshots SET capture_group_id = id")

    cursor = await db.execute("SELECT id, game_id, filename, format FROM screenshots")
    groups: dict[tuple[int, str], list[tuple[int, bool]]] = {}
    for screenshot_id, game_id, filename, image_format in await cursor.fetchall():
        stem = Path(filename).stem.casefold()
        groups.setdefault((game_id, stem), []).append(
            (screenshot_id, _is_hdr(filename, image_format))
        )

    for assets in groups.values():
        if len(assets) < 2:
            continue
        kinds = {is_hdr for _, is_hdr in assets}
        if len(kinds) != 2:
            continue
        capture_group_id = min(screenshot_id for screenshot_id, _ in assets)
        await db.executemany(
            "UPDATE screenshots SET capture_group_id = ? WHERE id = ?",
            [(capture_group_id, screenshot_id) for screenshot_id, _ in assets],
        )

    await db.execute(
        """CREATE INDEX idx_screenshots_game_capture
           ON screenshots(game_id, capture_group_id)"""
    )
    await db.execute(
        """CREATE INDEX idx_screenshots_game_perceptual_hash
           ON screenshots(game_id, perceptual_hash)"""
    )
    await db.execute(
        """CREATE INDEX idx_screenshots_game_source_key
           ON screenshots(game_id, source_key)"""
    )
    await db.execute(
        """UPDATE games SET
               screenshot_count = (
                   SELECT COUNT(DISTINCT COALESCE(s.capture_group_id, s.id))
                   FROM screenshots s WHERE s.game_id = games.id
               )"""
    )
