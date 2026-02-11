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

        Returns profile info or raises an error. Also resolves the
        Steam64 numeric ID if a vanity URL was provided.
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

        # Resolve Steam64 numeric ID (needed for API calls)
        steam64_id = self.user_id if self.is_numeric else None
        if not steam64_id:
            steam64_id = await self._resolve_steam64_id()

        return SteamProfile(
            user_id=steam64_id or self.user_id,
            profile_name=profile_name,
            avatar_url=avatar_url,
            is_numeric_id=True if steam64_id else self.is_numeric,
            profile_url=self.profile_url,
        )

    async def _resolve_steam64_id(self) -> str | None:
        """Resolve a vanity URL to a Steam64 numeric ID using the XML profile."""
        try:
            resp = await self._get(f"{self.profile_url}/?xml=1")
            if resp.status_code == 200:
                match = re.search(r"<steamID64>(\d+)</steamID64>", resp.text)
                if match:
                    return match.group(1)
        except Exception:
            pass
        return None

    # ── Game Discovery ───────────────────────────────────────────────────

    async def discover_games(self, fetch_counts: bool = False) -> list[SteamGameScreenshots]:
        """Discover all games that have screenshots on this profile.

        Scrapes the screenshot grid with appid=0 (all games view) and
        parses the game filter dropdown to get the list of games.

        Args:
            fetch_counts: If True, make an additional request per game to
                get the screenshot count. This is slow for large libraries.
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

        # Steam's current layout uses a custom dropdown for game filtering.
        # Each game is a div.option.ellipsis inside #sharedfiles_filterselect_app_filterable
        # with the app ID embedded in an onclick attribute like:
        #   onclick="javascript:SelectSharedFilesContentFilter({ 'appid': '292030' });"
        # and the game name as the element's text content.
        filterable = soup.find(id="sharedfiles_filterselect_app_filterable")
        if filterable:
            options = filterable.select("div.option")
            for opt in options:
                onclick = opt.get("onclick", "")
                match = re.search(r"'appid'\s*:\s*'(\d+)'", onclick)
                if not match:
                    continue

                app_id = int(match.group(1))
                if app_id == 0:
                    continue  # "All games" entry

                name = opt.get_text(strip=True) or f"App {app_id}"

                games.append(SteamGameScreenshots(
                    app_id=app_id,
                    name=name,
                    screenshot_count=0,  # Populated below
                ))

        # If the new dropdown wasn't found, try legacy sidebar selectors
        if not games:
            games = self._parse_legacy_sidebar(soup)

        # Optionally get screenshot counts by parsing the "Showing X of N" text
        # on each game's screenshot page. This is slow for large libraries.
        if games and fetch_counts:
            await self._populate_screenshot_counts(games)

        return games

    def _parse_legacy_sidebar(self, soup: BeautifulSoup) -> list[SteamGameScreenshots]:
        """Fallback: try the older sidebar-based game filter layout."""
        games = []
        filter_items = soup.select(".screenshot_filter_app")
        if not filter_items:
            filter_items = soup.select(".gameListRow, [data-appid]")

        for item in filter_items:
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

            name_elem = item.select_one(".screenshot_filter_app_name, .gameName, a")
            name = name_elem.get_text(strip=True) if name_elem else f"App {app_id}"

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

        return games

    async def _populate_screenshot_counts(self, games: list[SteamGameScreenshots]) -> None:
        """Fetch screenshot counts for each game by loading page 1 and reading
        the 'Showing X - Y of Z' text. Processes games sequentially with rate
        limiting to be respectful to Steam servers.
        """
        for game in games:
            try:
                url = (
                    f"{self.profile_url}/screenshots/"
                    f"?appid={game.app_id}&sort=newestfirst&browsefilter=myfiles"
                    f"&view=grid&privacy={PRIVACY_FILTER}"
                )
                resp = await self._get(url)
                if resp.status_code != 200:
                    continue

                # Look for "Showing X - Y of Z" in the page
                match = re.search(
                    r"Showing\s+\d+\s*-\s*\d+\s+of\s+([\d,]+)",
                    resp.text,
                )
                if match:
                    game.screenshot_count = int(match.group(1).replace(",", ""))
                else:
                    # If no paging text, count thumbnails on the page
                    soup = BeautifulSoup(resp.text, "lxml")
                    cards = soup.select("a[href*='filedetails']")
                    game.screenshot_count = len(cards)
            except Exception:
                # Don't fail the whole discovery if one game errors
                continue

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

        # Grid items are <a> links with class "profile_media_item" or similar
        items = soup.select(
            "a.profile_media_item, "
            "a.apphub_Card, "
            "a[href*='filedetails']"
        )

        for item in items:
            href = item.get("href", "")
            if not href or "filedetails" not in href:
                continue

            # Extract screenshot ID from URL or data attribute
            screenshot_id = item.get("data-publishedfileid", "")
            if not screenshot_id:
                id_match = re.search(r"id=(\d+)", href)
                if not id_match:
                    continue
                screenshot_id = id_match.group(1)

            # Current Steam layout (2024+): thumbnails are CSS background-image
            # on a div.imgWallItem child, not <img> tags.
            thumbnail_url = ""
            full_url = None

            # Try the new div.imgWallItem background-image approach
            wall_item = item.select_one(".imgWallItem, .imgWallHoverItem")
            if wall_item:
                style = wall_item.get("style", "")
                bg_match = re.search(r"background-image:\s*url\(['\"]?([^'\")\s]+)", style)
                if bg_match:
                    thumbnail_url = bg_match.group(1)
                    full_url = _extract_full_image_url(thumbnail_url)

            # Fallback: try <img> tag (older layout)
            if not thumbnail_url:
                img = item.select_one("img")
                if img:
                    thumbnail_url = img.get("src", "") or img.get("data-src", "")
                    if thumbnail_url:
                        full_url = _extract_full_image_url(thumbnail_url)

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
