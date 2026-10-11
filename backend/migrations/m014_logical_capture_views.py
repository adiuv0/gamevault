"""Give search and timeline the same logical-capture semantics as the gallery."""

async def upgrade(db):
    await db.execute("""CREATE VIEW logical_captures AS
        WITH ranked AS (
            SELECT s.*,
                ROW_NUMBER() OVER (
                    PARTITION BY game_id, COALESCE(capture_group_id, id)
                    ORDER BY COALESCE(is_hdr, lower(COALESCE(format, '')) IN ('jxr', 'wdp')), id
                ) AS capture_rank,
                MAX(is_favorite) OVER (
                    PARTITION BY game_id, COALESCE(capture_group_id, id)
                ) AS capture_favorite
            FROM screenshots s
        )
        SELECT ranked.*, (
            SELECT a.id FROM annotations a JOIN screenshots m ON m.id = a.screenshot_id
            WHERE m.game_id = ranked.game_id
              AND COALESCE(m.capture_group_id, m.id) = COALESCE(ranked.capture_group_id, ranked.id)
            ORDER BY a.updated_at DESC, a.id DESC LIMIT 1
        ) AS annotation_id
        FROM ranked WHERE capture_rank = 1""")
