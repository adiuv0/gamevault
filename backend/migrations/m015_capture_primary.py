"""Store each capture's representative asset so logical views avoid window scans.

``logical_captures`` previously ranked every screenshot with a window function on each
evaluation, which made timeline and search cost a full-table sort per query. Triggers
now keep ``is_capture_primary`` current for every write path that changes grouping,
HDR classification, format, or game ownership.
"""

_GROUP_OF = "COALESCE({t}.capture_group_id, {t}.id)"
_PREFERENCE = "COALESCE(m.is_hdr, lower(COALESCE(m.format, '')) IN ('jxr', 'wdp')), m.id"


def _refresh_group(game: str, group: str) -> str:
    """SQL that recomputes the representative flag for one (game, group)."""
    return f"""UPDATE screenshots SET is_capture_primary = (
            id = (SELECT m.id FROM screenshots m
                  WHERE m.game_id = screenshots.game_id
                    AND COALESCE(m.capture_group_id, m.id)
                        = COALESCE(screenshots.capture_group_id, screenshots.id)
                  ORDER BY {_PREFERENCE} LIMIT 1)
        )
        WHERE game_id = {game} AND COALESCE(capture_group_id, id) = {group};"""


LOGICAL_CAPTURES_VIEW = """CREATE VIEW logical_captures AS
    SELECT s.*,
        EXISTS(
            SELECT 1 FROM screenshots f
            WHERE f.game_id = s.game_id
              AND COALESCE(f.capture_group_id, f.id) = COALESCE(s.capture_group_id, s.id)
              AND f.is_favorite = 1
        ) AS capture_favorite,
        (
            SELECT a.id FROM annotations a JOIN screenshots m ON m.id = a.screenshot_id
            WHERE m.game_id = s.game_id
              AND COALESCE(m.capture_group_id, m.id) = COALESCE(s.capture_group_id, s.id)
            ORDER BY a.updated_at DESC, a.id DESC LIMIT 1
        ) AS annotation_id
    FROM screenshots s
    WHERE s.is_capture_primary = 1"""


async def upgrade(db):
    # Every capture lookup filters on this exact expression; index it so lookups
    # stop scanning a whole game's rows.
    await db.execute(
        """CREATE INDEX idx_screenshots_game_capture_key
           ON screenshots(game_id, COALESCE(capture_group_id, id))"""
    )
    await db.execute(
        "ALTER TABLE screenshots ADD COLUMN is_capture_primary INTEGER NOT NULL DEFAULT 0"
    )
    await db.execute(
        f"""UPDATE screenshots SET is_capture_primary = 1 WHERE id IN (
            SELECT id FROM (
                SELECT m.id, ROW_NUMBER() OVER (
                    PARTITION BY m.game_id, {_GROUP_OF.format(t='m')}
                    ORDER BY {_PREFERENCE}
                ) AS capture_rank
                FROM screenshots m
            ) WHERE capture_rank = 1
        )"""
    )

    new_group = _GROUP_OF.format(t="NEW")
    old_group = _GROUP_OF.format(t="OLD")
    await db.execute(
        f"""CREATE TRIGGER screenshots_capture_primary_ai AFTER INSERT ON screenshots BEGIN
            {_refresh_group("NEW.game_id", new_group)}
        END"""
    )
    await db.execute(
        f"""CREATE TRIGGER screenshots_capture_primary_ad AFTER DELETE ON screenshots BEGIN
            {_refresh_group("OLD.game_id", old_group)}
        END"""
    )
    await db.execute(
        f"""CREATE TRIGGER screenshots_capture_primary_au
            AFTER UPDATE OF capture_group_id, game_id, is_hdr, format ON screenshots
        BEGIN
            {_refresh_group("OLD.game_id", old_group)}
            {_refresh_group("NEW.game_id", new_group)}
        END"""
    )

    await db.execute(
        """CREATE INDEX idx_screenshots_primary_day
           ON screenshots(date(COALESCE(taken_at, uploaded_at)), COALESCE(taken_at, uploaded_at), id)
           WHERE is_capture_primary = 1"""
    )
    await db.execute(
        """CREATE INDEX idx_screenshots_primary_moment
           ON screenshots(COALESCE(taken_at, uploaded_at), id)
           WHERE is_capture_primary = 1"""
    )
    await db.execute(
        """CREATE INDEX idx_screenshots_primary_game_moment
           ON screenshots(game_id, COALESCE(taken_at, uploaded_at), id)
           WHERE is_capture_primary = 1"""
    )
    await db.execute("DROP VIEW IF EXISTS logical_captures")
    await db.execute(LOGICAL_CAPTURES_VIEW)
