"""Share link service: create, validate, and manage shareable screenshot links."""

import secrets
from datetime import datetime, timedelta

from backend.config import settings
from backend.database import get_db
from backend.services.screenshot_service import get_screenshot


async def create_share_link(
    screenshot_id: int,
    expires_in_days: int | None = None,
) -> dict:
    """Create a share link for a screenshot.

    Returns the share link dict with token and full URL.
    """
    screenshot = await get_screenshot(screenshot_id)
    if not screenshot:
        raise ValueError(f"Screenshot {screenshot_id} not found")

    # Check if an active link already exists
    existing = await get_active_share_link(screenshot_id)
    if existing:
        return existing

    db = await get_db()
    token = secrets.token_urlsafe(16)

    expires_at = None
    if expires_in_days:
        expires_at = (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat()

    cursor = await db.execute(
        """INSERT INTO share_links (screenshot_id, token, expires_at)
           VALUES (?, ?, ?)""",
        (screenshot_id, token, expires_at),
    )
    await db.commit()

    return await get_share_link(cursor.lastrowid)


async def get_share_link(link_id: int) -> dict | None:
    """Get a share link by ID."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM share_links WHERE id = ?", (link_id,)
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None
    link = dict(zip(columns, row))
    link["url"] = f"{settings.base_url}/share/{link['token']}"
    return link


async def get_share_link_by_token(token: str) -> dict | None:
    """Get a share link by token. Returns None if expired or inactive."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM share_links WHERE token = ? AND is_active = 1",
        (token,),
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None

    link = dict(zip(columns, row))

    # Check expiration
    if link.get("expires_at"):
        if datetime.fromisoformat(link["expires_at"]) < datetime.utcnow():
            # Mark as inactive
            await db.execute(
                "UPDATE share_links SET is_active = 0 WHERE id = ?",
                (link["id"],),
            )
            await db.commit()
            return None

    link["url"] = f"{settings.base_url}/share/{link['token']}"
    return link


async def get_active_share_link(screenshot_id: int) -> dict | None:
    """Get the active share link for a screenshot (if any)."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM share_links
           WHERE screenshot_id = ? AND is_active = 1
           ORDER BY created_at DESC LIMIT 1""",
        (screenshot_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if row is None:
        return None

    link = dict(zip(columns, row))

    # Check expiration
    if link.get("expires_at"):
        if datetime.fromisoformat(link["expires_at"]) < datetime.utcnow():
            await db.execute(
                "UPDATE share_links SET is_active = 0 WHERE id = ?",
                (link["id"],),
            )
            await db.commit()
            return None

    link["url"] = f"{settings.base_url}/share/{link['token']}"
    return link


async def deactivate_share_link(link_id: int) -> bool:
    """Deactivate a share link."""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE share_links SET is_active = 0 WHERE id = ?",
        (link_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def increment_view_count(link_id: int) -> None:
    """Increment the view count for a share link."""
    db = await get_db()
    await db.execute(
        "UPDATE share_links SET view_count = view_count + 1 WHERE id = ?",
        (link_id,),
    )
    await db.commit()


async def get_shared_screenshot_data(token: str) -> dict | None:
    """Get the full data needed to render a shared screenshot page.

    Returns screenshot + game info + annotation, or None if invalid.
    """
    link = await get_share_link_by_token(token)
    if not link:
        return None

    # Increment view count
    await increment_view_count(link["id"])

    # Get screenshot
    screenshot = await get_screenshot(link["screenshot_id"])
    if not screenshot:
        return None

    # Get game
    from backend.services.game_service import get_game
    game = await get_game(screenshot["game_id"])

    # Get annotation
    from backend.services.screenshot_service import get_annotation
    annotation = await get_annotation(screenshot["id"])

    return {
        "share_link": link,
        "screenshot": screenshot,
        "game": game,
        "annotation": annotation,
    }
