"""Steam Community screenshot scraper.

Re-implementation of steamscrd logic in async Python using httpx + BeautifulSoup.
Scrapes steamcommunity.com grid pages to discover and download screenshots.
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

from backend.config import settings


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SteamProfile:
    user_id: str
    profile_name: str | None = None
    avatar_url: str | None = None
    is_numeric_id: bool = False
    profile_url: str = ""


@dataclass
class SteamScreenshot:
    screenshot_id: str
    detail_url: str
    thumbnail_url: str
    full_image_url: str | None = None
    title: str = ""
    description: str = ""
    date_taken: str | None = None
    file_size: int | None = None


@dataclass
class SteamGameScreenshots:
    app_id: int
    name: str
    screenshot_count: int = 0
    screenshots: list[SteamScreenshot] = field(default_factory=list)


# ── Constants ────────────────────────────────────────────────────────────────

STEAM_COMMUNITY_URL = "https://steamcommunity.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Privacy bitmask: 14 = private + friends only + public
PRIVACY_FILTER = 14

# Steam date formats
DATE_FORMATS = [
    "%b %d, %Y @ %I:%M%p",  # "Jan 01, 2024 @ 12:00pm"
    "%b %d, %Y, %I:%M%p",   # "Jan 01, 2024, 12:00pm"
    "%d %b, %Y @ %I:%M%p",  # "01 Jan, 2024 @ 12:00pm"
    "%d %b, %Y, %I:%M%p",   # "01 Jan, 2024, 12:00pm"
    "%b %d, %Y @ %I:%M %p", # "Jan 01, 2024 @ 12:00 pm"
    "%d %b, %Y @ %I:%M %p", # "01 Jan, 2024 @ 12:00 pm"
]


# ── Helper Functions ─────────────────────────────────────────────────────────

def _build_cookies(steam_login_secure: str = "", session_id: str = "") -> dict:
    """Build Steam cookies dict."""
    cookies = {}
    if steam_login_secure:
        cookies["steamLoginSecure"] = steam_login_secure
    if session_id:
        cookies["sessionid"] = session_id
    # Mature content cookie for games with age gates
    cookies["birthtime"] = "0"
    cookies["mature_content"] = "1"
    cookies["lastagecheckage"] = "1-0-1990"
    return cookies


def _get_profile_url(user_id: str, is_numeric: bool = False) -> str:
    """Build the Steam profile URL."""
    if is_numeric or user_id.isdigit():
        return f"{STEAM_COMMUNITY_URL}/profiles/{user_id}"
    return f"{STEAM_COMMUNITY_URL}/id/{user_id}"


def _parse_steam_date(date_str: str) -> datetime | None:
    """Try to parse a Steam date string using known formats."""
    if not date_str:
        return None
    # Clean up whitespace
    cleaned = " ".join(date_str.strip().split())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _extract_full_image_url(thumbnail_url: str) -> str:
    """Convert a Steam thumbnail URL to the full-size image URL.

    Steam thumbnails use URLs like:
    https://steamuserimages-a.akamaihd.net/ugc/ID/HASH/?imw=NNN
    The full image is accessed without the resize parameters.
    """
    if not thumbnail_url:
        return thumbnail_url
    # Remove query parameters to get full-size image
    parsed = urlparse(thumbnail_url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


# ── Scraper Class ────────────────────────────────────────────────────────────

class SteamScraper:
    """Async Steam Community screenshot scraper."""

    def __init__(
        self,
        user_id: str,
        steam_login_secure: str = "",
        session_id: str = "",
        is_numeric_id: bool = False,
    ):
        self.user_id = user_id
        self.is_numeric = is_numeric_id or user_id.isdigit()
        self.profile_url = _get_profile_url(user_id, self.is_numeric)
        self.cookies = _build_cookies(steam_login_secure, session_id)
        self.rate_limit_ms = settings.import_rate_limit_ms
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            cookies=self.cookies,
            follow_redirects=True,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def _get(self, url: str) -> httpx.Response:
        """Make a rate-limited GET request."""
        resp = await self._client.get(url)
        if self.rate_limit_ms > 0:
            await asyncio.sleep(self.rate_limit_ms / 1000)
        return resp

    # ── Profile Validation ───────────────────────────────────────────────

    async def validate_profile(self) -> SteamProfile:
        """Validate that the Steam profile exists and is accessible.

        Returns profile info or raises an error.
        """
        url = self.profile_url
        resp = await self._get(url)

        if resp.status_code != 200:
            raise ValueError(f"Could not access Steam profile (HTTP {resp.status_code})")

        soup = BeautifulSoup(resp.text, "lxml")

        # Check for error page
        error_elem = soup.select_one(".error_ctn")
        if error_elem:
            raise ValueError("Steam profile not found or is private")

        # Extract profile name
        name_elem = soup.select_one(".actual_persona_name")
        profile_name = name_elem.get_text(strip=True) if name_elem else None

        # Extract avatar
        avatar_elem = soup.select_one(".playerAvatarAutoSizeInner img")
        avatar_url = avatar_elem.get("src") if avatar_elem else None

        return SteamProfile(
            user_id=self.user_id,
            profile_name=profile_name,
            avatar_url=avatar_url,
            is_numeric_id=self.is_numeric,
            profile_url=self.profile_url,
        )

    # ── Game Discovery ───────────────────────────────────────────────────

    async def discover_games(self) -> list[SteamGameScreenshots]:
        """Discover all games that have screenshots on this profile.

        Scrapes the screenshot grid with appid=0 (all games view) and
        extracts the game filter sidebar to get per-game counts.
        """
        url = (
            f"{self.profile_url}/screenshots/"
            f"?appid=0&sort=newestfirst&browsefilter=myfiles"
            f"&view=grid&privacy={PRIVACY_FILTER}"
        )
        resp = await self._get(url)
        if resp.status_code != 200:
            raise ValueError(f"Could not access screenshots page (HTTP {resp.status_code})")

        soup = BeautifulSoup(resp.text, "lxml")
        games = []

        # Parse the game filter sidebar
        # Each game appears as a link with the app ID and screenshot count
        filter_items = soup.select(".screenshot_filter_app")
        if not filter_items:
            # Try alternative selector
            filter_items = soup.select(".gameListRow, [data-appid]")

        for item in filter_items:
            # Extract app ID from the link/data attribute
            app_id = None
            link = item.select_one("a")

            if item.get("data-appid"):
                app_id = int(item["data-appid"])
            elif link and "appid=" in (link.get("href") or ""):
                match = re.search(r"appid=(\d+)", link.get("href", ""))
                if match:
                    app_id = int(match.group(1))

            if not app_id or app_id == 0:
                continue

            # Extract game name
            name_elem = item.select_one(".screenshot_filter_app_name, .gameName, a")
            name = name_elem.get_text(strip=True) if name_elem else f"App {app_id}"

            # Extract count
            count_elem = item.select_one(".screenshot_filter_app_count, .gameCount")
            count = 0
            if count_elem:
                count_text = count_elem.get_text(strip=True)
                count_match = re.search(r"(\d+)", count_text.replace(",", ""))
                if count_match:
                    count = int(count_match.group(1))

            games.append(SteamGameScreenshots(
                app_id=app_id,
                name=name,
                screenshot_count=count,
            ))

        # If sidebar parsing didn't work, try to parse from the grid directly
        if not games:
            games = await self._discover_games_from_grid(soup)

        return games

    async def _discover_games_from_grid(self, soup: BeautifulSoup) -> list[SteamGameScreenshots]:
        """Fallback: discover games by parsing screenshot thumbnails in the grid."""
        game_map: dict[int, SteamGameScreenshots] = {}

        cards = soup.select(".apphub_Card, .profile_media_item")
        for card in cards:
            link = card.get("href") or card.select_one("a")
            if isinstance(link, str):
                href = link
            elif link:
                href = link.get("href", "")
            else:
                continue

            # Extract app_id from screenshot detail URL
            match = re.search(r"/(\d+)/", href)
            if not match:
                continue

            # This is the screenshot ID, not app ID — need detail page for app ID
            # For now, count unique apps from grid page context
            pass

        return list(game_map.values())

    # ── Screenshot Grid Scraping ─────────────────────────────────────────

    async def scrape_game_screenshots(
        self,
        app_id: int,
        on_progress: callable = None,
    ) -> list[SteamScreenshot]:
        """Scrape all screenshots for a specific game.

        Paginates through the grid pages until no more screenshots are found.
        """
        all_screenshots = []
        page = 1

        while True:
            url = (
                f"{self.profile_url}/screenshots/"
                f"?appid={app_id}&sort=newestfirst&browsefilter=myfiles"
                f"&view=grid&privacy={PRIVACY_FILTER}&p={page}"
            )

            resp = await self._get(url)
            if resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.text, "lxml")
            screenshots = self._parse_grid_page(soup, app_id)

            if not screenshots:
                break

            all_screenshots.extend(screenshots)

            if on_progress:
                on_progress(len(all_screenshots))

            page += 1

            # Safety limit to prevent infinite loops
            if page > 200:
                break

        return all_screenshots

    def _parse_grid_page(self, soup: BeautifulSoup, app_id: int) -> list[SteamScreenshot]:
        """Parse a single grid page to extract screenshot entries."""
        screenshots = []

        # Grid items are typically links with thumbnail images
        items = soup.select(
            ".apphub_Card, "
            ".profile_media_item, "
            "a[href*='filedetails']"
        )

        for item in items:
            # Get the detail page URL
            if item.name == "a":
                href = item.get("href", "")
            else:
                link = item.select_one("a")
                href = link.get("href", "") if link else ""

            if not href or "filedetails" not in href:
                continue

            # Extract screenshot ID from URL
            id_match = re.search(r"id=(\d+)", href)
            if not id_match:
                continue

            screenshot_id = id_match.group(1)

            # Get thumbnail URL
            img = item.select_one("img")
            thumbnail_url = ""
            if img:
                thumbnail_url = img.get("src", "") or img.get("data-src", "")

            # Derive full image URL from thumbnail
            full_url = _extract_full_image_url(thumbnail_url) if thumbnail_url else None

            screenshots.append(SteamScreenshot(
                screenshot_id=screenshot_id,
                detail_url=href,
                thumbnail_url=thumbnail_url,
                full_image_url=full_url,
            ))

        return screenshots

    # ── Screenshot Detail Page ───────────────────────────────────────────

    async def get_screenshot_details(self, screenshot: SteamScreenshot) -> SteamScreenshot:
        """Fetch the detail page for a screenshot to get the full-size image URL,
        description, and date.
        """
        url = screenshot.detail_url
        if not url.startswith("http"):
            url = f"{STEAM_COMMUNITY_URL}{url}"

        resp = await self._get(url)
        if resp.status_code != 200:
            return screenshot

        soup = BeautifulSoup(resp.text, "lxml")

        # Get full-size image URL from the detail page
        actual_img = soup.select_one(
            ".actualmediactn a img, "
            ".screenshotActualSize img, "
            "#ActualMedia img"
        )
        if actual_img:
            src = actual_img.get("src", "")
            if src:
                screenshot.full_image_url = _extract_full_image_url(src)

        # Also check for a direct link to full size
        full_link = soup.select_one(
            ".actualmediactn a, "
            "a[href*='ugc']"
        )
        if full_link and not screenshot.full_image_url:
            href = full_link.get("href", "")
            if "akamaihd.net" in href or "ugc" in href:
                screenshot.full_image_url = _extract_full_image_url(href)

        # Get description/caption
        desc_elem = soup.select_one(
            ".screenshotDescription, "
            ".nonSelectedScreenshotDescription, "
            ".detailsStatRight"
        )
        if desc_elem:
            screenshot.description = desc_elem.get_text(strip=True)

        # Get date taken
        date_elem = soup.select_one(
            ".detailsStatsContainerRight .detailsStatRight, "
            ".screenshotDate"
        )
        if date_elem:
            date_str = date_elem.get_text(strip=True)
            parsed = _parse_steam_date(date_str)
            if parsed:
                screenshot.date_taken = parsed.isoformat()

        # Get file size from details
        stats = soup.select(".detailsStatRight")
        for stat in stats:
            text = stat.get_text(strip=True)
            if "MB" in text or "KB" in text:
                try:
                    size_match = re.search(r"([\d.]+)\s*(MB|KB)", text)
                    if size_match:
                        val = float(size_match.group(1))
                        unit = size_match.group(2)
                        screenshot.file_size = int(val * (1024 * 1024 if unit == "MB" else 1024))
                except (ValueError, AttributeError):
                    pass

        return screenshot

    # ── Image Download ───────────────────────────────────────────────────

    async def download_image(self, url: str) -> bytes | None:
        """Download a screenshot image from Akamai CDN."""
        if not url:
            return None

        try:
            resp = await self._client.get(url, timeout=60.0)
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                if "image" in content_type or len(resp.content) > 1000:
                    return resp.content
        except Exception:
            pass
        return None
