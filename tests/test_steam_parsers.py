"""Tests for Steam scraper HTML parsers using saved fixtures.

These tests validate that our parser correctly handles real Steam Community
HTML structures. When Steam changes their layout, failing tests here will
pinpoint exactly what broke.
"""

import pytest
from bs4 import BeautifulSoup

from backend.services.steam_scraper import (
    SteamScraper,
    SteamScreenshot,
    _build_cookies,
    _extract_full_image_url,
    _get_profile_url,
    _parse_steam_date,
)


# ── Date Parser Tests ────────────────────────────────────────────────────────


class TestDateParser:
    """Test Steam date string parsing with both known formats."""

    def test_format_dmy_at(self):
        """21 Jan, 2024 @ 3:44pm"""
        result = _parse_steam_date("21 Jan, 2024 @ 3:44pm")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 21
        assert result.hour == 15
        assert result.minute == 44

    def test_format_mdy_at(self):
        """Jan 21, 2024 @ 6:44am"""
        result = _parse_steam_date("Jan 21, 2024 @ 6:44am")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 21
        assert result.hour == 6
        assert result.minute == 44

    def test_format_mdy_comma(self):
        """Mar 15, 2023, 11:22am"""
        result = _parse_steam_date("Mar 15, 2023, 11:22am")
        assert result is not None
        assert result.year == 2023
        assert result.month == 3

    def test_format_dmy_comma(self):
        """15 Mar, 2023, 11:22am"""
        result = _parse_steam_date("15 Mar, 2023, 11:22am")
        assert result is not None
        assert result.year == 2023

    def test_format_with_space_before_ampm(self):
        """Jan 01, 2024 @ 12:00 pm"""
        result = _parse_steam_date("Jan 01, 2024 @ 12:00 pm")
        assert result is not None
        assert result.hour == 12

    def test_midnight(self):
        """12am should be hour 0."""
        result = _parse_steam_date("Jan 01, 2024 @ 12:00am")
        assert result is not None
        assert result.hour == 0

    def test_noon(self):
        """12pm should be hour 12."""
        result = _parse_steam_date("Jan 01, 2024 @ 12:00pm")
        assert result is not None
        assert result.hour == 12

    def test_empty_string(self):
        assert _parse_steam_date("") is None

    def test_none_input(self):
        assert _parse_steam_date(None) is None

    def test_invalid_format(self):
        assert _parse_steam_date("not a date at all") is None

    def test_extra_whitespace(self):
        """Should handle extra whitespace gracefully."""
        result = _parse_steam_date("  21 Jan, 2024   @   3:44pm  ")
        assert result is not None
        assert result.year == 2024


# ── URL Builder Tests ────────────────────────────────────────────────────────


class TestURLBuilder:
    def test_vanity_url(self):
        url = _get_profile_url("coolgamer")
        assert url == "https://steamcommunity.com/id/coolgamer"

    def test_numeric_id(self):
        url = _get_profile_url("76561198012345678")
        assert url == "https://steamcommunity.com/profiles/76561198012345678"

    def test_explicit_numeric(self):
        url = _get_profile_url("coolgamer", is_numeric=True)
        assert url == "https://steamcommunity.com/profiles/coolgamer"

    def test_explicit_not_numeric(self):
        url = _get_profile_url("76561198012345678", is_numeric=False)
        # isdigit() should still detect this
        assert url == "https://steamcommunity.com/profiles/76561198012345678"


# ── Image URL Tests ──────────────────────────────────────────────────────────


class TestImageURL:
    def test_strip_resize_params(self):
        thumb = "https://steamuserimages-a.akamaihd.net/ugc/abc123/hash/?imw=256&imh=144"
        full = _extract_full_image_url(thumb)
        assert full == "https://steamuserimages-a.akamaihd.net/ugc/abc123/hash/"
        assert "imw=" not in full

    def test_already_full(self):
        url = "https://steamuserimages-a.akamaihd.net/ugc/abc123/hash/"
        assert _extract_full_image_url(url) == url

    def test_empty(self):
        assert _extract_full_image_url("") == ""

    def test_none(self):
        assert _extract_full_image_url(None) is None


# ── Cookie Builder Tests ─────────────────────────────────────────────────────


class TestCookies:
    def test_with_all_cookies(self):
        cookies = _build_cookies("my_login_secure", "my_session_id")
        assert cookies["steamLoginSecure"] == "my_login_secure"
        assert cookies["sessionid"] == "my_session_id"
        assert cookies["birthtime"] == "0"
        assert cookies["mature_content"] == "1"

    def test_empty_cookies(self):
        cookies = _build_cookies()
        assert "steamLoginSecure" not in cookies
        assert "sessionid" not in cookies
        assert cookies["birthtime"] == "0"

    def test_partial_cookies(self):
        cookies = _build_cookies(steam_login_secure="just_login")
        assert cookies["steamLoginSecure"] == "just_login"
        assert "sessionid" not in cookies


# ── Grid Page Parser Tests (Fixtures) ────────────────────────────────────────


class TestGridParser:
    def test_parse_screenshot_cards(self, steam_grid_html):
        """Parse screenshot thumbnails from grid page."""
        soup = BeautifulSoup(steam_grid_html, "lxml")
        scraper = SteamScraper.__new__(SteamScraper)
        screenshots = scraper._parse_grid_page(soup, app_id=1245620)

        assert len(screenshots) == 3
        assert screenshots[0].screenshot_id == "111222333"
        assert screenshots[1].screenshot_id == "444555666"
        assert screenshots[2].screenshot_id == "777888999"

    def test_extracts_thumbnail_urls(self, steam_grid_html):
        soup = BeautifulSoup(steam_grid_html, "lxml")
        scraper = SteamScraper.__new__(SteamScraper)
        screenshots = scraper._parse_grid_page(soup, app_id=1245620)

        assert "akamaihd.net" in screenshots[0].thumbnail_url
        assert "thumb1" in screenshots[0].thumbnail_url

    def test_generates_full_image_urls(self, steam_grid_html):
        soup = BeautifulSoup(steam_grid_html, "lxml")
        scraper = SteamScraper.__new__(SteamScraper)
        screenshots = scraper._parse_grid_page(soup, app_id=1245620)

        # Full image URL should have no query parameters
        assert screenshots[0].full_image_url is not None
        assert "imw=" not in screenshots[0].full_image_url

    def test_ignores_non_screenshot_links(self, steam_grid_html):
        """The workshop link should not be picked up."""
        soup = BeautifulSoup(steam_grid_html, "lxml")
        scraper = SteamScraper.__new__(SteamScraper)
        screenshots = scraper._parse_grid_page(soup, app_id=1245620)

        ids = [s.screenshot_id for s in screenshots]
        assert all(sid.isdigit() for sid in ids)

    def test_parse_game_sidebar(self, steam_grid_html):
        """Parse game filter sidebar for game discovery."""
        soup = BeautifulSoup(steam_grid_html, "lxml")

        # Parse using the same selectors as discover_games
        filter_items = soup.select(".screenshot_filter_app")
        assert len(filter_items) == 3

        # First game
        item0 = filter_items[0]
        assert item0.get("data-appid") == "292030"
        name_elem = item0.select_one(".screenshot_filter_app_name")
        assert "Witcher" in name_elem.get_text(strip=True)

        count_elem = item0.select_one(".screenshot_filter_app_count")
        assert "42" in count_elem.get_text(strip=True)

    def test_empty_grid(self):
        """An empty grid page should return no screenshots."""
        html = '<html><body><div id="image_wall"></div></body></html>'
        soup = BeautifulSoup(html, "lxml")
        scraper = SteamScraper.__new__(SteamScraper)
        screenshots = scraper._parse_grid_page(soup, app_id=12345)
        assert screenshots == []


# ── Detail Page Parser Tests (Fixtures) ──────────────────────────────────────


class TestDetailParser:
    def test_extract_full_image(self, steam_detail_html):
        """Extract the full-size image URL from detail page."""
        soup = BeautifulSoup(steam_detail_html, "lxml")

        actual_img = soup.select_one(
            ".actualmediactn a img, "
            ".screenshotActualSize img, "
            "#ActualMedia img"
        )
        assert actual_img is not None
        src = actual_img.get("src", "")
        full_url = _extract_full_image_url(src)
        assert "akamaihd.net" in full_url
        assert "imw=" not in full_url

    def test_extract_description(self, steam_detail_html):
        """Extract screenshot description."""
        soup = BeautifulSoup(steam_detail_html, "lxml")
        desc_elem = soup.select_one(".screenshotDescription")
        assert desc_elem is not None
        assert "sunset" in desc_elem.get_text(strip=True).lower()

    def test_extract_date(self, steam_detail_html):
        """Extract and parse the date from detail page."""
        soup = BeautifulSoup(steam_detail_html, "lxml")
        stats = soup.select(".detailsStatRight")
        # Date should be the second stat
        assert len(stats) >= 2
        date_str = stats[1].get_text(strip=True)
        parsed = _parse_steam_date(date_str)
        assert parsed is not None
        assert parsed.year == 2024
        assert parsed.month == 1

    def test_extract_file_size(self, steam_detail_html):
        """Extract file size from detail page."""
        import re
        soup = BeautifulSoup(steam_detail_html, "lxml")
        stats = soup.select(".detailsStatRight")

        found_size = None
        for stat in stats:
            text = stat.get_text(strip=True)
            if "MB" in text or "KB" in text:
                size_match = re.search(r"([\d.]+)\s*(MB|KB)", text)
                if size_match:
                    val = float(size_match.group(1))
                    unit = size_match.group(2)
                    found_size = int(val * (1024 * 1024 if unit == "MB" else 1024))

        assert found_size is not None
        assert found_size > 2_000_000  # Should be ~2.4 MB

    def test_extract_game_name(self, steam_detail_html):
        """Extract game name from detail page."""
        soup = BeautifulSoup(steam_detail_html, "lxml")
        game_elem = soup.select_one(".screenshotAppName a")
        assert game_elem is not None
        assert "ELDEN RING" in game_elem.get_text(strip=True)

    def test_extract_app_id_from_link(self, steam_detail_html):
        """Extract Steam app ID from the store link."""
        import re
        soup = BeautifulSoup(steam_detail_html, "lxml")
        game_link = soup.select_one(".screenshotAppName a")
        href = game_link.get("href", "")
        match = re.search(r"/app/(\d+)/", href)
        assert match is not None
        assert match.group(1) == "1245620"

    def test_mature_content_page(self, steam_detail_mature_html):
        """Mature content page should not have the main image."""
        soup = BeautifulSoup(steam_detail_mature_html, "lxml")
        actual_img = soup.select_one(
            ".actualmediactn a img, "
            ".screenshotActualSize img, "
            "#ActualMedia img"
        )
        assert actual_img is None
        # But should still have the age gate
        age_gate = soup.select_one(".agegate_container")
        assert age_gate is not None


# ── Profile Page Parser Tests (Fixtures) ─────────────────────────────────────


class TestProfileParser:
    def test_extract_profile_name(self, steam_profile_html):
        soup = BeautifulSoup(steam_profile_html, "lxml")
        name_elem = soup.select_one(".actual_persona_name")
        assert name_elem is not None
        assert name_elem.get_text(strip=True) == "TestGamer42"

    def test_extract_avatar(self, steam_profile_html):
        soup = BeautifulSoup(steam_profile_html, "lxml")
        avatar_elem = soup.select_one(".playerAvatarAutoSizeInner img")
        assert avatar_elem is not None
        assert "akamai.steamstatic.com" in avatar_elem.get("src", "")

    def test_detect_error_page(self, steam_profile_error_html):
        soup = BeautifulSoup(steam_profile_error_html, "lxml")
        error_elem = soup.select_one(".error_ctn")
        assert error_elem is not None

    def test_no_error_on_valid_profile(self, steam_profile_html):
        soup = BeautifulSoup(steam_profile_html, "lxml")
        error_elem = soup.select_one(".error_ctn")
        assert error_elem is None


# ── SteamScraper Unit Tests ──────────────────────────────────────────────────


class TestSteamScraperInit:
    def test_vanity_url_init(self):
        scraper = SteamScraper.__new__(SteamScraper)
        scraper.user_id = "coolgamer"
        scraper.is_numeric = False
        scraper.profile_url = _get_profile_url("coolgamer")
        assert "/id/coolgamer" in scraper.profile_url

    def test_numeric_url_init(self):
        scraper = SteamScraper.__new__(SteamScraper)
        scraper.user_id = "76561198012345678"
        scraper.is_numeric = True
        scraper.profile_url = _get_profile_url("76561198012345678", is_numeric=True)
        assert "/profiles/76561198012345678" in scraper.profile_url
