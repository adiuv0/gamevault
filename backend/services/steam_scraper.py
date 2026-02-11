"""Steam screenshot fetcher using the Steam Web API.

Game discovery uses HTML scraping (the game filter dropdown on Steam Community).
Per-game screenshot fetching uses the Steam Web API (IPublishedFileService/GetUserFiles)
when an API key is available, with HTML scraping as fallback.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from backend.config import settings

logger = logging.getLogger(__name__)

# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SteamProfile:
    user_id: str
    profile_name: str | None = None
    avatar_url: str | None = None
    is_numeric_id: bool = False
    profile_url: str = ""
    steam64_id: str | None = None


@dataclass
class SteamScreenshot:
    screenshot_id: str
    app_id: int = 0
    thumbnail_url: str = ""
    full_image_url: str | None = None
    title: str = ""
    description: str = ""
    date_taken: str | None = None
    file_size: int | None = None
    width: int | None = None
    height: int | None = None

    # Legacy field kept for compatibility; not used by API path
    detail_url: str = ""


@dataclass
class SteamGameScreenshots:
    app_id: int
    name: str
    screenshot_count: int = 0
    screenshots: list[SteamScreenshot] = field(default_factory=list)


# ── Constants ────────────────────────────────────────────────────────────────

STEAM_COMMUNITY_URL = "https://steamcommunity.com"
STEAM_API_URL = "https://api.steampowered.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Privacy bitmask for HTML scraping: 14 = private + friends only + public
PRIVACY_FILTER = 14

# Steam date formats (used in HTML scraping fallback)
DATE_FORMATS = [
    "%b %d, %Y @ %I:%M%p",
    "%b %d, %Y, %I:%M%p",
    "%d %b, %Y @ %I:%M%p",
    "%d %b, %Y, %I:%M%p",
    "%b %d, %Y @ %I:%M %p",
    "%d %b, %Y @ %I:%M %p",
]

# How many screenshots to fetch per API page
API_PAGE_SIZE = 100

# Steam Screenshots creator app ID
STEAM_SCREENSHOTS_APP_ID = 760


# ── Helper Functions ─────────────────────────────────────────────────────────

def _build_cookies(steam_login_secure: str = "", session_id: str = "") -> dict:
    """Build Steam cookies dict."""
    cookies = {}
    if steam_login_secure:
        cookies["steamLoginSecure"] = steam_login_secure
    if session_id:
        cookies["sessionid"] = session_id
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
    cleaned = " ".join(date_str.strip().split())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _extract_full_image_url(thumbnail_url: str) -> str:
    """Convert a Steam thumbnail URL to the full-size image URL."""
    if not thumbnail_url:
        return thumbnail_url
    parsed = urlparse(thumbnail_url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


# ── Scraper Class ────────────────────────────────────────────────────────────

class SteamScraper:
    """Steam screenshot fetcher with API-first approach and HTML fallback.

    Game discovery always uses HTML scraping (parses the game filter dropdown).
    Per-game screenshot fetching uses the Steam Web API when an API key is
    available, falling back to HTML grid scraping otherwise.
    """

    def __init__(
        self,
        user_id: str,
        steam_login_secure: str = "",
        session_id: str = "",
        is_numeric_id: bool = False,
        api_key: str = "",
    ):
        self.user_id = user_id
        self.is_numeric = is_numeric_id or user_id.isdigit()
        self.profile_url = _get_profile_url(user_id, self.is_numeric)
        self.cookies = _build_cookies(steam_login_secure, session_id)
        self.api_key = api_key
        self.rate_limit_ms = settings.import_rate_limit_ms
        self._client: httpx.AsyncClient | None = None

        # Resolved Steam64 numeric ID (set during validate_profile)
        self._steam64_id: str | None = user_id if self.is_numeric else None

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

    async def _get(self, url: str, rate_limit: bool = True) -> httpx.Response:
        """Make a GET request, optionally rate-limited."""
        resp = await self._client.get(url)
        if rate_limit and self.rate_limit_ms > 0:
            await asyncio.sleep(self.rate_limit_ms / 1000)
        return resp

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    # ── Profile Validation ───────────────────────────────────────────────

    async def validate_profile(self) -> SteamProfile:
        """Validate that the Steam profile exists and is accessible.

        Also resolves the Steam64 numeric ID (needed for API calls).
        """
        url = self.profile_url
        logger.info("validate_profile: fetching %s", url)
        resp = await self._get(url)

        if resp.status_code != 200:
            logger.error("validate_profile: HTTP %d for %s", resp.status_code, url)
            raise ValueError(f"Could not access Steam profile (HTTP {resp.status_code})")

        soup = BeautifulSoup(resp.text, "lxml")

        error_elem = soup.select_one(".error_ctn")
        if error_elem:
            error_text = error_elem.get_text(strip=True)[:200]
            logger.error("validate_profile: error_ctn found: %s", error_text)
            raise ValueError("Steam profile not found or is private")

        name_elem = soup.select_one(".actual_persona_name")
        profile_name = name_elem.get_text(strip=True) if name_elem else None

        avatar_elem = soup.select_one(".playerAvatarAutoSizeInner img")
        avatar_url = avatar_elem.get("src") if avatar_elem else None

        # Resolve Steam64 numeric ID
        if not self._steam64_id:
            self._steam64_id = await self._resolve_steam64_id()

        return SteamProfile(
            user_id=self._steam64_id or self.user_id,
            profile_name=profile_name,
            avatar_url=avatar_url,
            is_numeric_id=True if self._steam64_id else self.is_numeric,
            profile_url=self.profile_url,
            steam64_id=self._steam64_id,
        )

    async def _resolve_steam64_id(self) -> str | None:
        """Resolve a vanity URL to a Steam64 numeric ID.

        Tries the ResolveVanityURL API first (if API key available),
        falls back to the XML profile endpoint.
        """
        if self.api_key:
            try:
                url = (
                    f"{STEAM_API_URL}/ISteamUser/ResolveVanityURL/v1/"
                    f"?key={self.api_key}&vanityurl={self.user_id}"
                )
                resp = await self._get(url, rate_limit=False)
                if resp.status_code == 200:
                    data = resp.json()
                    response = data.get("response", {})
                    if response.get("success") == 1:
                        return response.get("steamid")
            except Exception:
                pass

        # Fallback: XML profile
        try:
            resp = await self._get(f"{self.profile_url}/?xml=1")
            if resp.status_code == 200:
                match = re.search(r"<steamID64>(\d+)</steamID64>", resp.text)
                if match:
                    return match.group(1)
        except Exception:
            pass

        return None

    # ── Game Discovery (always HTML) ─────────────────────────────────────

    async def discover_games(self, fetch_counts: bool = False) -> list[SteamGameScreenshots]:
        """Discover all games that have screenshots on this profile.

        Always uses HTML scraping — the Steam Community game filter dropdown
        is the only reliable way to get the full list of games with screenshots.
        When an API key is available, we then fetch exact counts per game via
        the API (fast, one request per game vs scraping grid pages).
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

        # Current layout: custom dropdown for game filtering
        filterable = soup.find(id="sharedfiles_filterselect_app_filterable")
        logger.info("discover_games: filterable dropdown found = %s", bool(filterable))
        if filterable:
            options = filterable.select("div.option")
            for opt in options:
                onclick = opt.get("onclick", "")
                match = re.search(r"'appid'\s*:\s*'(\d+)'", onclick)
                if not match:
                    continue
                app_id = int(match.group(1))
                if app_id == 0:
                    continue
                name = opt.get_text(strip=True) or f"App {app_id}"
                games.append(SteamGameScreenshots(
                    app_id=app_id,
                    name=name,
                    screenshot_count=0,
                ))

        # Fallback: legacy sidebar
        if not games:
            games = self._parse_legacy_sidebar(soup)

        logger.info("discover_games: found %d games from HTML dropdown", len(games))

        # Get screenshot counts — via API (fast) or HTML scraping (slow)
        if games:
            if self.has_api_key and self._steam64_id:
                logger.info("discover_games: populating counts via API (steam64=%s)", self._steam64_id)
                await self._populate_screenshot_counts_api(games)
            elif fetch_counts:
                logger.info("discover_games: populating counts via HTML scraping")
                await self._populate_screenshot_counts_html(games)

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

    async def _populate_screenshot_counts_api(self, games: list[SteamGameScreenshots]) -> None:
        """Get screenshot counts using the Steam API (one request per game, totalonly)."""
        steam_id = self._steam64_id or self.user_id
        for game in games:
            try:
                url = (
                    f"{STEAM_API_URL}/IPublishedFileService/GetUserFiles/v1/"
                    f"?key={self.api_key}"
                    f"&steamid={steam_id}"
                    f"&appid={game.app_id}"
                    f"&filetype=4"
                    f"&totalonly=true"
                    f"&numperpage=1"
                    f"&page=1"
                )
                resp = await self._get(url, rate_limit=False)
                if resp.status_code == 200:
                    data = resp.json()
                    total = data.get("response", {}).get("total", 0)
                    game.screenshot_count = total
            except Exception:
                continue

    async def _populate_screenshot_counts_html(self, games: list[SteamGameScreenshots]) -> None:
        """Fetch screenshot counts for each game by loading page 1 (slow)."""
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

                match = re.search(
                    r"Showing\s+\d+\s*-\s*\d+\s+of\s+([\d,]+)",
                    resp.text,
                )
                if match:
                    game.screenshot_count = int(match.group(1).replace(",", ""))
                else:
                    soup = BeautifulSoup(resp.text, "lxml")
                    cards = soup.select("a[href*='filedetails']")
                    game.screenshot_count = len(cards)
            except Exception:
                continue

    # ── Per-Game Screenshot Fetching ─────────────────────────────────────

    async def get_game_screenshots(
        self,
        app_id: int,
        on_progress: callable = None,
    ) -> list[SteamScreenshot]:
        """Get all screenshots for a specific game.

        Uses the Steam Web API if an API key is available (returns direct
        CDN URLs with metadata). Falls back to HTML grid scraping otherwise.
        """
        if self.has_api_key and self._steam64_id:
            screenshots = await self._fetch_game_screenshots_api(app_id)
            if screenshots is not None:
                if on_progress:
                    on_progress(len(screenshots))
                return screenshots
            # API returned None (failed), fall through to HTML

        return await self._scrape_game_screenshots_html(app_id, on_progress)

    async def _fetch_game_screenshots_api(self, app_id: int) -> list[SteamScreenshot] | None:
        """Fetch all screenshots for a specific game via the Steam Web API.

        Uses IPublishedFileService/GetUserFiles with the game's appid.
        filetype=4 is the EPublishedFileInfoMatchingFileType value for screenshots
        (NOT 5, which is EWorkshopFileType — a different enum used in responses).
        Returns None if the API call fails (caller should fall back to HTML).
        """
        steam_id = self._steam64_id or self.user_id
        all_screenshots: list[SteamScreenshot] = []
        page = 1
        total_fetched = 0

        logger.info(
            "API: fetching screenshots for app %d, steam_id=%s",
            app_id, steam_id,
        )

        while True:
            url = (
                f"{STEAM_API_URL}/IPublishedFileService/GetUserFiles/v1/"
                f"?key={self.api_key}"
                f"&steamid={steam_id}"
                f"&appid={app_id}"
                f"&filetype=4"
                f"&numperpage={API_PAGE_SIZE}"
                f"&page={page}"
            )

            try:
                resp = await self._get(url, rate_limit=True)
            except Exception as e:
                logger.error("API request failed for app %d page %d: %s", app_id, page, e)
                return None if page == 1 else all_screenshots

            if resp.status_code != 200:
                logger.error("API returned HTTP %d for app %d page %d", resp.status_code, app_id, page)
                return None if page == 1 else all_screenshots

            try:
                data = resp.json()
            except Exception:
                logger.error("API returned invalid JSON for app %d page %d", app_id, page)
                return None if page == 1 else all_screenshots

            response = data.get("response", {})
            files = response.get("publishedfiledetails", [])
            total = response.get("total", 0)

            logger.info(
                "API app %d page %d: response has %d files, total=%d",
                app_id, page, len(files), total,
            )

            if not files:
                if page == 1:
                    logger.info("API app %d: no files returned on first page", app_id)
                break

            # Log first file's structure for debugging
            if page == 1 and files:
                first = files[0]
                logger.info(
                    "API app %d: first file sample — publishedfileid=%s, "
                    "file_type=%s, file_url=%s, preview_url=%s",
                    app_id,
                    first.get("publishedfileid"),
                    first.get("file_type"),
                    (first.get("file_url", "") or "")[:80],
                    (first.get("preview_url", "") or "")[:80],
                )

            for f in files:
                published_id = str(f.get("publishedfileid", ""))
                file_url = f.get("file_url", "")
                preview_url = f.get("preview_url", "")
                time_created = f.get("time_created", 0)
                file_size = f.get("file_size", 0)
                title = f.get("title", "")
                description = f.get("file_description", "")
                img_width = f.get("image_width", None)
                img_height = f.get("image_height", None)

                if not published_id:
                    continue

                # Convert Unix timestamp to ISO
                date_taken = None
                if time_created:
                    date_taken = datetime.fromtimestamp(
                        time_created, tz=timezone.utc
                    ).isoformat()

                # With filetype=4, the API returns file_url (direct CDN link).
                # If file_url is empty for some reason, set detail_url so the
                # import service can fall back to scraping the detail page.
                detail_url = (
                    f"{STEAM_COMMUNITY_URL}/sharedfiles/filedetails/?id={published_id}"
                )

                screenshot = SteamScreenshot(
                    screenshot_id=published_id,
                    app_id=app_id,
                    thumbnail_url=preview_url,
                    full_image_url=file_url if file_url else None,
                    detail_url=detail_url,
                    title=title,
                    description=description,
                    date_taken=date_taken,
                    file_size=int(file_size) if file_size else None,
                    width=int(img_width) if img_width else None,
                    height=int(img_height) if img_height else None,
                )

                all_screenshots.append(screenshot)

            total_fetched += len(files)
            logger.info(
                "API app %d page %d: parsed %d screenshots (%d/%d total fetched)",
                app_id, page, len(all_screenshots), total_fetched, total,
            )

            if total_fetched >= total or len(files) < API_PAGE_SIZE:
                break

            page += 1
            if page > 500:
                logger.warning("Hit API page safety limit for app %d", app_id)
                break

        logger.info(
            "API app %d: finished with %d screenshots total",
            app_id, len(all_screenshots),
        )
        return all_screenshots

    async def _scrape_game_screenshots_html(
        self,
        app_id: int,
        on_progress: callable = None,
    ) -> list[SteamScreenshot]:
        """Scrape all screenshots for a game via HTML grid pages."""
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
            if page > 200:
                break

        return all_screenshots

    def _parse_grid_page(self, soup: BeautifulSoup, app_id: int) -> list[SteamScreenshot]:
        """Parse a single grid page to extract screenshot entries."""
        screenshots = []

        items = soup.select(
            "a.profile_media_item, "
            "a.apphub_Card, "
            "a[href*='filedetails']"
        )

        for item in items:
            href = item.get("href", "")
            if not href or "filedetails" not in href:
                continue

            screenshot_id = item.get("data-publishedfileid", "")
            if not screenshot_id:
                id_match = re.search(r"id=(\d+)", href)
                if not id_match:
                    continue
                screenshot_id = id_match.group(1)

            thumbnail_url = ""
            full_url = None

            # Current layout (2024+): CSS background-image on div.imgWallItem
            wall_item = item.select_one(".imgWallItem, .imgWallHoverItem")
            if wall_item:
                style = wall_item.get("style", "")
                bg_match = re.search(r"background-image:\s*url\(['\"]?([^'\")\s]+)", style)
                if bg_match:
                    thumbnail_url = bg_match.group(1)
                    full_url = _extract_full_image_url(thumbnail_url)

            # Fallback: <img> tag
            if not thumbnail_url:
                img = item.select_one("img")
                if img:
                    thumbnail_url = img.get("src", "") or img.get("data-src", "")
                    if thumbnail_url:
                        full_url = _extract_full_image_url(thumbnail_url)

            screenshots.append(SteamScreenshot(
                screenshot_id=screenshot_id,
                app_id=app_id,
                detail_url=href,
                thumbnail_url=thumbnail_url,
                full_image_url=full_url,
            ))

        return screenshots

    # ── Screenshot Detail Page (HTML fallback only) ──────────────────────

    async def get_screenshot_details(self, screenshot: SteamScreenshot) -> SteamScreenshot:
        """Fetch detail page for a screenshot (HTML scraping fallback only).

        Not needed when using the API path — the API returns all metadata directly.
        """
        url = screenshot.detail_url
        if not url:
            return screenshot
        if not url.startswith("http"):
            url = f"{STEAM_COMMUNITY_URL}{url}"

        resp = await self._get(url)
        if resp.status_code != 200:
            return screenshot

        soup = BeautifulSoup(resp.text, "lxml")

        # Full-size image URL
        actual_img = soup.select_one(
            ".actualmediactn a img, "
            ".screenshotActualSize img, "
            "#ActualMedia img"
        )
        if actual_img:
            src = actual_img.get("src", "")
            if src:
                screenshot.full_image_url = _extract_full_image_url(src)

        if not screenshot.full_image_url:
            full_link = soup.select_one(".actualmediactn a, a[href*='ugc']")
            if full_link:
                href = full_link.get("href", "")
                if "akamaihd.net" in href or "ugc" in href:
                    screenshot.full_image_url = _extract_full_image_url(href)

        # Description
        desc_elem = soup.select_one(
            ".screenshotDescription, "
            ".nonSelectedScreenshotDescription, "
            ".detailsStatRight"
        )
        if desc_elem:
            screenshot.description = desc_elem.get_text(strip=True)

        # Date
        date_elem = soup.select_one(
            ".detailsStatsContainerRight .detailsStatRight, "
            ".screenshotDate"
        )
        if date_elem:
            date_str = date_elem.get_text(strip=True)
            parsed = _parse_steam_date(date_str)
            if parsed:
                screenshot.date_taken = parsed.isoformat()

        # File size
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
        """Download a screenshot image from Akamai / Steam CDN."""
        if not url:
            logger.warning("download_image: empty URL")
            return None

        try:
            resp = await self._client.get(url, timeout=60.0)
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                if "image" in content_type or len(resp.content) > 1000:
                    logger.info(
                        "download_image: success, %d bytes, type=%s",
                        len(resp.content), content_type,
                    )
                    return resp.content
                else:
                    logger.warning(
                        "download_image: unexpected content — type=%s, size=%d",
                        content_type, len(resp.content),
                    )
            else:
                logger.warning(
                    "download_image: HTTP %d for %s", resp.status_code, url[:100],
                )
        except Exception as e:
            logger.error("download_image: exception for %s: %s", url[:100], e)
        return None
