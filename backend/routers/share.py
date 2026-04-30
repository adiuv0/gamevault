"""Share routes: create/manage share links (authenticated) + public view (unauthenticated)."""

import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from backend.auth import require_auth
from backend.config import settings
from backend.services.filesystem import safe_library_path
from backend.services.share_service import (
    create_share_link,
    get_active_share_link,
    deactivate_share_link,
    get_shared_screenshot_data,
)

# JXR isn't recognized by stdlib ``mimetypes`` — provide an explicit fallback.
_EXTRA_MIME_TYPES = {
    ".jxr": "image/vnd.ms-photo",
    ".wdp": "image/vnd.ms-photo",
}

router = APIRouter(tags=["share"])


# ── Authenticated share management ───────────────────────────────────────────

@router.post("/api/screenshots/{screenshot_id}/share", dependencies=[Depends(require_auth)])
async def create_share(screenshot_id: int, expires_in_days: int | None = None):
    """Create or get a share link for a screenshot."""
    try:
        link = await create_share_link(screenshot_id, expires_in_days)
        return link
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/screenshots/{screenshot_id}/share", dependencies=[Depends(require_auth)])
async def get_share(screenshot_id: int):
    """Get the active share link for a screenshot."""
    link = await get_active_share_link(screenshot_id)
    if not link:
        return None
    return link


@router.delete("/api/screenshots/{screenshot_id}/share", dependencies=[Depends(require_auth)])
async def delete_share(screenshot_id: int):
    """Deactivate the share link for a screenshot."""
    link = await get_active_share_link(screenshot_id)
    if not link:
        raise HTTPException(status_code=404, detail="No active share link")
    await deactivate_share_link(link["id"])
    return {"message": "Share link deactivated"}


# ── Public share page (no auth required) ─────────────────────────────────────

@router.get("/share/{token}")
async def public_share_page(token: str):
    """Public shared screenshot page with OpenGraph meta tags."""
    data = await get_shared_screenshot_data(token)
    if not data:
        return HTMLResponse(
            content=_render_error_page("Screenshot Not Found",
                                       "This share link is invalid or has expired."),
            status_code=404,
        )

    screenshot = data["screenshot"]
    game = data["game"]
    annotation = data.get("annotation")

    # Build the page
    title = f"{screenshot['filename']} - {game['name'] if game else 'GameVault'}"
    description = ""
    if annotation and annotation.get("content"):
        description = annotation["content"][:200]
    elif screenshot.get("steam_description"):
        description = screenshot["steam_description"][:200]
    else:
        description = f"Screenshot from {game['name']}" if game else "Shared screenshot"

    # Image URL points at the token-bound /share/{token}/image route, NOT
    # the auth-protected /api/screenshots/... endpoint. This is what makes
    # OpenGraph crawlers (Discord, Slack, Twitter) able to fetch the image
    # without needing a JWT, while still keeping the screenshot route
    # gated for everything else.
    image_url = f"{settings.base_url}/share/{token}/image"
    page_url = f"{settings.base_url}/share/{token}"

    return HTMLResponse(content=_render_share_page(
        title=title,
        description=description,
        image_url=image_url,
        page_url=page_url,
        screenshot=screenshot,
        game=game,
        annotation=annotation,
    ))


@router.get("/share/{token}/image")
async def share_image(token: str):
    """Serve the shared screenshot's full image bytes directly.

    The token is the only auth signal — anyone with the token gets the
    image. Importantly we do NOT redirect to the auth-protected
    /api/screenshots/... route; that flow used to break OpenGraph crawlers
    and tempted future "fixes" that would have made /api/screenshots
    public.
    """
    data = await get_shared_screenshot_data(token)
    if not data:
        raise HTTPException(status_code=404, detail="Share link not found")

    screenshot = data["screenshot"]
    file_path = safe_library_path(screenshot.get("file_path"))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    suffix = file_path.suffix.lower()
    media_type = (
        _EXTRA_MIME_TYPES.get(suffix)
        or mimetypes.guess_type(str(file_path))[0]
        or "image/jpeg"
    )
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
            # Cache for an hour so OpenGraph crawlers don't hammer the
            # backend, but short enough that revoking a share takes effect
            # quickly for re-views.
            "Cache-Control": "public, max-age=3600",
        },
    )


# ── HTML Templates ───────────────────────────────────────────────────────────

def _render_share_page(
    title: str,
    description: str,
    image_url: str,
    page_url: str,
    screenshot: dict,
    game: dict | None,
    annotation: dict | None,
) -> str:
    """Render a minimal, self-contained HTML page for the shared screenshot."""
    game_name = game["name"] if game else "Unknown Game"
    dims = ""
    if screenshot.get("width") and screenshot.get("height"):
        dims = f"{screenshot['width']} x {screenshot['height']}"

    annotation_html = ""
    if annotation and annotation.get("content_html"):
        annotation_html = f'<div class="annotation">{annotation["content_html"]}</div>'
    elif annotation and annotation.get("content"):
        import html
        annotation_html = f'<div class="annotation"><p>{html.escape(annotation["content"])}</p></div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_esc(title)}</title>

    <!-- OpenGraph -->
    <meta property="og:title" content="{_esc(title)}">
    <meta property="og:description" content="{_esc(description)}">
    <meta property="og:image" content="{image_url}">
    <meta property="og:image:width" content="{screenshot.get('width', '')}">
    <meta property="og:image:height" content="{screenshot.get('height', '')}">
    <meta property="og:url" content="{page_url}">
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="GameVault">

    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{_esc(title)}">
    <meta name="twitter:description" content="{_esc(description)}">
    <meta name="twitter:image" content="{image_url}">

    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; min-height: 100vh; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px; }}
        .header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }}
        .header h1 {{ font-size: 14px; color: #8b949e; font-weight: 400; }}
        .header a {{ color: #58a6ff; text-decoration: none; }}
        .image-container {{ background: #161b22; border-radius: 8px; overflow: hidden; border: 1px solid #30363d; }}
        .image-container img {{ width: 100%; height: auto; display: block; }}
        .info {{ padding: 16px 0; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }}
        .info .game {{ font-size: 18px; font-weight: 600; color: #c9d1d9; }}
        .info .meta {{ font-size: 13px; color: #8b949e; }}
        .annotation {{ margin-top: 16px; padding: 16px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; font-size: 14px; line-height: 1.6; }}
        .annotation p {{ margin-bottom: 8px; }}
        .footer {{ margin-top: 32px; text-align: center; font-size: 12px; color: #484f58; }}
        .footer a {{ color: #58a6ff; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Shared from <a href="{settings.base_url}">GameVault</a></h1>
        </div>

        <div class="image-container">
            <img src="{image_url}" alt="{_esc(screenshot.get('filename', ''))}">
        </div>

        <div class="info">
            <span class="game">{_esc(game_name)}</span>
            <span class="meta">{_esc(dims)}</span>
        </div>

        {annotation_html}

        <div class="footer">
            <p>Powered by <a href="https://github.com/gamevault">GameVault</a></p>
        </div>
    </div>
</body>
</html>"""


def _render_error_page(title: str, message: str) -> str:
    """Render a simple error page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_esc(title)} - GameVault</title>
    <style>
        body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
        .error {{ text-align: center; }}
        h1 {{ color: #f85149; margin-bottom: 8px; }}
        p {{ color: #8b949e; }}
    </style>
</head>
<body>
    <div class="error">
        <h1>{_esc(title)}</h1>
        <p>{_esc(message)}</p>
    </div>
</body>
</html>"""


def _esc(s: str) -> str:
    """HTML-escape a string."""
    import html
    return html.escape(str(s)) if s else ""
