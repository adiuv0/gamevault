"""Timeline sessions and statistics count logical captures and stay relative to the viewer's day."""

import pytest


@pytest.fixture
async def library(monkeypatch, tmp_path):
    from backend.config import settings
    from backend.database import close_db, init_db
    await close_db()
    for field, path in {"data_dir": tmp_path, "db_path": tmp_path / "state.db",
                        "library_dir": tmp_path / "library", "thumbnail_dir": tmp_path / "cache"}.items():
        monkeypatch.setattr(settings, field, path)
    await init_db()
    yield settings
    await close_db()


async def _shot(game_id, name, taken_at, **extra):
    from backend.services import screenshot_service as shots
    ext = name.rsplit(".", 1)[1]
    return await shots.create_screenshot(
        game_id, name, name, taken_at=taken_at, format=ext, **extra
    )


async def _seed():
    """Two invented games over three days, including one grouped JPG + JXR capture."""
    from backend.services import game_service as games
    lantern = await games.create_game("Lantern Test")
    tide = await games.create_game("Tide Test")
    # 2025-03-10: an evening session of Lantern, a 2 h gap, then a second session.
    for clock in ("18:00", "18:10", "18:30", "19:10"):
        await _shot(lantern["id"], f"l{clock}.png", f"2025-03-10T{clock}:00")
    jxr = await _shot(lantern["id"], "late.jxr", "2025-03-10T21:30:00", is_hdr=True)
    await _shot(lantern["id"], "late.jpg", "2025-03-10T21:30:02",
                is_hdr=False, capture_group_id=jxr["id"])
    # Tide in the middle of the Lantern gap is its own session.
    await _shot(tide["id"], "t1.png", "2025-03-10T20:00:00")
    # The next two days make a three-day streak.
    await _shot(tide["id"], "t2.png", "2025-03-11T12:00:00")
    await _shot(tide["id"], "t3.png", "2025-03-12T12:00:00")
    # A capture a year later on the same calendar day, for "on this day".
    await _shot(tide["id"], "t4.png", "2026-03-10T09:00:00")
    return lantern, tide


async def test_timeline_days_split_into_sessions_and_count_variants_once(library):
    from backend.services.timeline_service import get_timeline
    await _seed()

    result = await get_timeline(start_date="2025-03-10", end_date="2025-03-10")
    (day,) = result["days"]
    # Seven files, six captures: the JPG and JXR of 21:30 are one.
    assert day["screenshot_count"] == 6
    sessions = day["sessions"]
    assert [(s["game_name"], s["capture_count"]) for s in sessions] == [
        ("Lantern Test", 1), ("Tide Test", 1), ("Lantern Test", 4),
    ]
    assert day["session_count"] == 3
    evening = sessions[2]
    assert evening["started_at"].startswith("2025-03-10T18:00")
    assert evening["ended_at"].startswith("2025-03-10T19:10")
    assert evening["duration_minutes"] == 70
    assert sessions[0]["hdr_count"] == 1
    # Previews are listed in session order and every one belongs to its session.
    by_id = {s["id"]: s for s in day["screenshots"]}
    assert [i for s in sessions for i in s["screenshot_ids"]] == [s["id"] for s in day["screenshots"]]
    assert all(by_id[i]["game_id"] == s["game_id"] for s in sessions for i in s["screenshot_ids"])


async def test_session_previews_spread_across_long_sessions(library):
    from backend.services import game_service as games
    from backend.services.timeline_service import SESSION_PREVIEWS, get_timeline
    game = await games.create_game("Burst Test")
    for i in range(40):
        await _shot(game["id"], f"b{i}.png", f"2025-05-01T20:{i:02d}:00")
    (day,) = (await get_timeline())["days"]
    (session,) = day["sessions"]
    assert session["capture_count"] == 40
    assert len(session["screenshot_ids"]) == SESSION_PREVIEWS
    shown = [s["taken_at"] for s in day["screenshots"]]
    assert shown[0].startswith("2025-05-01T20:00") and shown[-1].startswith("2025-05-01T20:39")


async def test_insights_for_a_year(library):
    from backend.services.timeline_service import get_timeline_insights
    lantern, _tide = await _seed()

    stats = await get_timeline_insights(year=2025, today="2025-03-12")
    assert stats["total_captures"] == 8
    assert stats["active_days"] == 3
    assert stats["games"] == 2
    assert stats["hdr_captures"] == 1
    assert stats["busiest_day"] == {
        "date": "2025-03-10", "captures": 6, "game_id": lantern["id"], "game_name": "Lantern Test",
    }
    assert stats["best_streak"] == {"days": 3, "start": "2025-03-10", "end": "2025-03-12"}
    assert stats["current_streak"]["days"] == 3
    # Lantern x2 and Tide x1 on the 10th, then one Tide session on each later day.
    assert stats["session_count"] == 5
    assert stats["longest_session"]["duration_minutes"] == 70
    assert [(g["game_name"], g["captures"]) for g in stats["top_games"]] == [
        ("Lantern Test", 5), ("Tide Test", 3),
    ]
    assert stats["years"] == [2026, 2025]
    assert sum(stats["hours"]) == 8
    assert stats["hours"][18] == 3 and stats["hours"][21] == 1 and stats["hours"][12] == 2
    assert stats["heatmap"]["days"] == [["2025-03-10", 6], ["2025-03-11", 1], ["2025-03-12", 1]]


async def test_insights_streak_on_this_day_and_game_filter(library):
    from backend.services.timeline_service import get_timeline_insights
    lantern, _tide = await _seed()

    # Viewed on 2026-03-11 with no capture yet that day: yesterday's capture keeps a
    # one-day streak alive, and the 2025-03-11 capture shows as "on this day".
    stats = await get_timeline_insights(today="2026-03-11")
    assert stats["current_streak"] == {"days": 1, "start": "2026-03-10", "end": "2026-03-10"}
    assert [c["taken_at"][:10] for c in stats["on_this_day"]["captures"]] == ["2025-03-11"]
    assert stats["total_captures"] == 9

    later = await get_timeline_insights(today="2026-03-20")
    assert later["current_streak"]["days"] == 0

    only_lantern = await get_timeline_insights(game_id=lantern["id"], today="2026-03-10")
    assert only_lantern["total_captures"] == 5
    assert only_lantern["on_this_day"]["captures"] and all(
        c["game_id"] == lantern["id"] for c in only_lantern["on_this_day"]["captures"]
    )


async def test_insights_empty_library_and_bad_today(library):
    from backend.services.timeline_service import get_timeline_insights
    stats = await get_timeline_insights(today="2026-02-30")
    assert stats["total_captures"] == 0
    assert stats["busiest_day"] is None and stats["longest_session"] is None
    assert stats["on_this_day"]["captures"] == []
    assert stats["years"] == []


async def test_insights_cache_follows_library_changes(library):
    from backend.services import game_service as games
    from backend.services import screenshot_service as shots
    from backend.services.timeline_service import get_timeline_insights
    lantern, tide = await _seed()

    first = await get_timeline_insights(year=2025, today="2025-03-12")
    assert await get_timeline_insights(year=2025, today="2025-03-12") is first

    extra = await _shot(tide["id"], "t5.png", "2025-03-12T13:00:00")
    added = await get_timeline_insights(year=2025, today="2025-03-12")
    assert added["total_captures"] == first["total_captures"] + 1

    await games.update_game(lantern["id"], name="Lantern Renamed")
    renamed = await get_timeline_insights(year=2025, today="2025-03-12")
    assert renamed["top_games"][0]["game_name"] == "Lantern Renamed"

    await shots.delete_screenshot(extra["id"])
    removed = await get_timeline_insights(year=2025, today="2025-03-12")
    assert removed["total_captures"] == first["total_captures"]
