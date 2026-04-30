"""Re-render and sanitize every existing annotation.

Closes GV-001 for installs that ran the unsanitized renderer (markdown-it
with default ``html=True``). Walks every row in ``annotations``,
re-renders ``content`` through the new safe pipeline, and overwrites
``content_html``. Idempotent — running it on already-sanitized data
produces the same output, so it's safe to re-run.
"""

import aiosqlite

from backend.services.annotation_renderer import render_and_sanitize


async def upgrade(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT id, content FROM annotations")
    rows = await cursor.fetchall()
    for ann_id, content in rows:
        if content is None:
            continue
        new_html = render_and_sanitize(content)
        await db.execute(
            "UPDATE annotations SET content_html = ? WHERE id = ?",
            (new_html, ann_id),
        )
    await db.commit()
