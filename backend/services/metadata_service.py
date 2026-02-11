"""Game metadata fetching from Steam Store API, SteamGridDB, and IGDB.

Provides cover art, descriptions, developer/publisher info, genres, and release dates.
Falls through providers in priority order: Steam API -> SteamGridDB -> IGDB.
"""

import logging
from pathlib import Path

import httpx

from backend.config import settings
from backend.services.game_service import get_game, update_game, save_cover_image

logger = logging.getLogger(__name__)

STEAM_STORE_API = "https://store.steampowered.com/api"
STEAMGRIDDB_API = "https://www.steamgriddb.com/api/v2"
IGDB_API = "https://api.igdb.com/v4"
IGDB_AUTH_URL = "https://id.twitch.tv/oauth2/token"


# ── Steam Store API ──────────────────────────────────────────────────────────

async def fetch_steam_metadata(app_id: int) -> dict | None:
    """Fetch game metadata from Steam Store API (no key required)."""
    if not app_id:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{STEAM_STORE_API}/appdetails",
                params={"appids": str(app_id), "l": "english"},
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            app_data = data.get(str(app_id), {})
            if not app_data.get("success"):
                return None

            info = app_data.get("data", {})
            if not info:
                return None

            # Extract genres
            genres = ", ".join(g.get("description", "") for g in info.get("genres", []))

            # Extract developers/publishers
            developers = ", ".join(info.get("developers", []))
            publishers = ", ".join(info.get("publishers", []))

            # Release date
            release = info.get("release_date", {})
            release_date = release.get("date", "") if not release.get("coming_soon") else None

            return {
                "name": info.get("name"),
                "description": info.get("short_description", ""),
                "developer": developers or None,
                "publisher": publishers or None,
                "release_date": release_date or None,
                "genres": genres or None,
                "header_image_url": info.get("header_image"),
                "cover_image_url": info.get("header_image"),  # Steam header as cover
            }
    except Exception as e:
        logger.warning("Steam Store API error for app %d: %s", app_id, e)
        return None


# ── SteamGridDB API ──────────────────────────────────────────────────────────

async def fetch_steamgriddb_cover(game_name: str, steam_app_id: int | None = None) -> dict | None:
    """Fetch cover art from SteamGridDB. Requires API key."""
    if not settings.steamgriddb_api_key:
        return None

    try:
        headers = {"Authorization": f"Bearer {settings.steamgriddb_api_key}"}

        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            # Find the game on SteamGridDB
            game_id = None

            if steam_app_id:
                # Search by Steam app ID first
                resp = await client.get(f"{STEAMGRIDDB_API}/games/steam/{steam_app_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success") and data.get("data"):
                        game_id = data["data"]["id"]

            if not game_id:
                # Search by name
                resp = await client.get(
                    f"{STEAMGRIDDB_API}/search/autocomplete/{game_name}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success") and data.get("data"):
                        game_id = data["data"][0]["id"]

            if not game_id:
                return None

            # Fetch grid images (cover art) — prefer 600x900 (2:3 ratio)
            resp = await client.get(
                f"{STEAMGRIDDB_API}/grids/game/{game_id}",
                params={"dimensions": "600x900", "types": "static", "limit": "1"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("data"):
                    return {
                        "steamgriddb_id": game_id,
                        "cover_image_url": data["data"][0]["url"],
                    }

            # Fallback: any grid image
            resp = await client.get(
                f"{STEAMGRIDDB_API}/grids/game/{game_id}",
                params={"types": "static", "limit": "1"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("data"):
                    return {
                        "steamgriddb_id": game_id,
                        "cover_image_url": data["data"][0]["url"],
                    }

            return {"steamgriddb_id": game_id}

    except Exception as e:
        logger.warning("SteamGridDB error for %s: %s", game_name, e)
        return None


# ── IGDB API ─────────────────────────────────────────────────────────────────

async def _get_igdb_token() -> str | None:
    """Get an IGDB access token via Twitch OAuth2."""
    if not settings.igdb_client_id or not settings.igdb_client_secret:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                IGDB_AUTH_URL,
                params={
                    "client_id": settings.igdb_client_id,
                    "client_secret": settings.igdb_client_secret,
                    "grant_type": "client_credentials",
                },
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
    except Exception as e:
        logger.warning("IGDB auth error: %s", e)
    return None


async def fetch_igdb_metadata(game_name: str) -> dict | None:
    """Fetch game metadata from IGDB. Requires Twitch client credentials."""
    token = await _get_igdb_token()
    if not token:
        return None

    try:
        headers = {
            "Client-ID": settings.igdb_client_id,
            "Authorization": f"Bearer {token}",
        }

        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            # Search for the game
            resp = await client.post(
                f"{IGDB_API}/games",
                data=(
                    f'search "{game_name}"; '
                    f'fields name,summary,genres.name,involved_companies.company.name,'
                    f'involved_companies.developer,involved_companies.publisher,'
                    f'first_release_date,cover.image_id; '
                    f'limit 1;'
                ),
            )

            if resp.status_code != 200:
                return None

            results = resp.json()
            if not results:
                return None

            game = results[0]

            # Extract genres
            genres = ", ".join(g.get("name", "") for g in game.get("genres", []))

            # Extract developer/publisher from involved_companies
            developers = []
            publishers = []
            for ic in game.get("involved_companies", []):
                company_name = ic.get("company", {}).get("name", "")
                if ic.get("developer"):
                    developers.append(company_name)
                if ic.get("publisher"):
                    publishers.append(company_name)

            # Release date (UNIX timestamp)
            release_ts = game.get("first_release_date")
            release_date = None
            if release_ts:
                from datetime import datetime, timezone
                release_date = datetime.fromtimestamp(release_ts, tz=timezone.utc).strftime("%Y-%m-%d")

            # Cover image
            cover_id = game.get("cover", {}).get("image_id")
            cover_url = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover_id}.jpg" if cover_id else None

            return {
                "igdb_id": game.get("id"),
                "name": game.get("name"),
                "description": game.get("summary", ""),
                "developer": ", ".join(developers) or None,
                "publisher": ", ".join(publishers) or None,
                "release_date": release_date,
                "genres": genres or None,
                "cover_image_url": cover_url,
            }

    except Exception as e:
        logger.warning("IGDB error for %s: %s", game_name, e)
        return None


# ── Unified metadata fetcher ─────────────────────────────────────────────────

async def fetch_and_apply_metadata(game_id: int) -> dict:
    """Fetch metadata from all available sources and apply to a game.

    Priority: Steam Store API > SteamGridDB (covers) > IGDB (fallback).
    Returns a dict with what was updated.
    """
    game = await get_game(game_id)
    if not game:
        raise ValueError(f"Game {game_id} not found")

    updates = {}
    cover_url = None
    sources_tried = []
    sources_succeeded = []

    # 1. Steam Store API (if we have a steam_app_id)
    if game.get("steam_app_id"):
        sources_tried.append("steam")
        steam_data = await fetch_steam_metadata(game["steam_app_id"])
        if steam_data:
            sources_succeeded.append("steam")
            # Apply non-None fields that aren't already set
            for field in ("description", "developer", "publisher", "release_date", "genres", "header_image_url"):
                if steam_data.get(field) and not game.get(field):
                    updates[field] = steam_data[field]
            if steam_data.get("cover_image_url"):
                cover_url = steam_data["cover_image_url"]

    # 2. SteamGridDB (better cover art)
    sources_tried.append("steamgriddb")
    sgdb_data = await fetch_steamgriddb_cover(game["name"], game.get("steam_app_id"))
    if sgdb_data:
        sources_succeeded.append("steamgriddb")
        if sgdb_data.get("steamgriddb_id"):
            updates["steamgriddb_id"] = sgdb_data["steamgriddb_id"]
        if sgdb_data.get("cover_image_url"):
            cover_url = sgdb_data["cover_image_url"]  # Prefer SteamGridDB covers

    # 3. IGDB (fallback for non-Steam games)
    if not game.get("steam_app_id") or not updates.get("description"):
        sources_tried.append("igdb")
        igdb_data = await fetch_igdb_metadata(game["name"])
        if igdb_data:
            sources_succeeded.append("igdb")
            if igdb_data.get("igdb_id"):
                updates["igdb_id"] = igdb_data["igdb_id"]
            for field in ("description", "developer", "publisher", "release_date", "genres"):
                if igdb_data.get(field) and field not in updates and not game.get(field):
                    updates[field] = igdb_data[field]
            if igdb_data.get("cover_image_url") and not cover_url:
                cover_url = igdb_data["cover_image_url"]

    # Download and save cover image
    if cover_url and not game.get("cover_image_path"):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(cover_url)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    ext = "jpg"
                    ct = resp.headers.get("content-type", "")
                    if "png" in ct:
                        ext = "png"
                    elif "webp" in ct:
                        ext = "webp"
                    await save_cover_image(game_id, resp.content, f"cover.{ext}")
        except Exception as e:
            logger.warning("Failed to download cover for game %d: %s", game_id, e)

    # Apply metadata updates to DB
    if updates:
        await update_game(game_id, **updates)

    return {
        "game_id": game_id,
        "sources_tried": sources_tried,
        "sources_succeeded": sources_succeeded,
        "fields_updated": list(updates.keys()),
        "cover_downloaded": cover_url is not None and not game.get("cover_image_path"),
    }


async def search_external_games(query: str) -> list[dict]:
    """Search for games across external metadata sources.

    Returns a list of matches with name, app_id, cover_url, source.
    """
    results = []

    # Search Steam Store
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://store.steampowered.com/api/storesearch/",
                params={"term": query, "l": "english", "cc": "US"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", [])[:10]:
                    results.append({
                        "name": item.get("name", ""),
                        "steam_app_id": item.get("id"),
                        "cover_url": item.get("tiny_image"),
                        "source": "steam",
                    })
    except Exception as e:
        logger.warning("Steam store search error: %s", e)

    return results
