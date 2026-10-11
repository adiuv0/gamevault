from datetime import datetime

from fakes import binary_vdf, make_specialk, make_steam
from gamevault_sync import sources
from gamevault_sync.vdf import vdf_parse


def test_text_vdf():
    data = vdf_parse('"A" { "b" "1" // comment\n "c" { "d" "x\\"y" } }')
    assert data == {"A": {"b": "1", "c": {"d": 'x"y'}}}


def test_binary_vdf_reads_shortcut_names():
    parsed = sources.parse_binary_vdf(binary_vdf({3228583970: "Dolphin", 2147483649: "RetroArch"}))
    entries = list(parsed["shortcuts"].values())
    assert [e["AppName"] for e in entries] == ["Dolphin", "RetroArch"]
    # appid is stored signed; the screenshot folder uses the unsigned value.
    assert entries[0]["appid"] & 0xFFFFFFFF == 3228583970


def test_steam_scan_names_games_and_filters_files(tmp_path):
    steam = make_steam(tmp_path)
    result = sources.scan(steam_path=str(steam), specialk_enabled=False)
    assert result.problems == []
    by_game = {}
    for item in result.files:
        by_game.setdefault(item.game.key, []).append(item.filename)
    assert sorted(by_game) == ["steam:1245620", "steam:3228583970", "steam:400"]
    # thumbnails, .txt and .tga (which the server rejects) are left out
    assert sorted(by_game["steam:400"]) == ["20110419101010_1.jpg"]
    assert len(by_game["steam:1245620"]) == 2

    games = result.games
    assert games["steam:1245620"].name == "ELDEN RING"  # from the second library's appmanifest
    assert games["steam:1245620"].steam_app_id == 1245620
    assert games["steam:400"].name == ""  # not installed: the server will name it
    assert games["steam:400"].display_name == "Steam app 400"
    # Non-Steam games are found by name, not sent to the Steam store.
    assert games["steam:3228583970"].name == "Dolphin"
    assert games["steam:3228583970"].steam_app_id is None


def test_known_names_fill_in_uninstalled_games(tmp_path):
    steam = make_steam(tmp_path)
    result = sources.scan(steam_path=str(steam), specialk_enabled=False,
                          known_names={"steam:400": "Portal"})
    assert result.games["steam:400"].name == "Portal"


def test_capture_dates(tmp_path):
    steam = make_steam(tmp_path)
    files = {f.filename: f for f in sources.scan(steam_path=str(steam), specialk_enabled=False).files}
    # screenshots.vdf creation time wins
    assert files["20110419101010_1.jpg"].taken_at_hint == datetime.fromtimestamp(1303200000).isoformat()
    # then the Steam filename
    assert files["20240301203512_1.jpg"].taken_at_hint == "2024-03-01T20:35:12"
    assert sources.date_from_filename("2024-05-01 21-15-03.png") == "2024-05-01T21:15:03"
    assert sources.date_from_filename("shot.png") is None


def test_specialk_scan(tmp_path):
    root = make_specialk(tmp_path)
    result = sources.scan(steam_enabled=False, specialk_path=str(root))
    names = {g.key: g.name for g in result.games.values()}
    assert names == {"specialk:Cyberpunk2077.exe": "Cyberpunk 2077", "specialk:HollowKnight": "Hollow Knight"}
    cyberpunk = [f for f in result.files if f.game.key == "specialk:Cyberpunk2077.exe"]
    assert sorted(f.filename for f in cyberpunk) == ["2024-05-01 21-15-03.jxr", "2024-05-01 21-15-03.png"]
    assert cyberpunk[0].taken_at_hint == "2024-05-01T21:15:03"
    hollow = next(f for f in result.files if f.filename == "shot.png")
    assert hollow.taken_at_hint  # falls back to the file's modified time


def test_missing_folders_are_reported_not_fatal(tmp_path):
    result = sources.scan(steam_path=str(tmp_path / "nope"), specialk_path=str(tmp_path / "nada"))
    assert result.files == []
    assert len(result.problems) == 2


def test_clean_specialk_folder_name():
    assert sources.clean_specialk_folder_name("EldenRing.exe") == "Elden Ring"
    assert sources.clean_specialk_folder_name("Witcher3") == "Witcher 3"
    assert sources.clean_specialk_folder_name("HZDGame") == "HZD Game"
