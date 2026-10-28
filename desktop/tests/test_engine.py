import threading
import time

import pytest
from fakes import FakeServer, make_specialk, make_steam, sha, write_file
from gamevault_sync import engine as engine_module
from gamevault_sync.api import ServerUnreachableError, SignedOutError
from gamevault_sync.config import Settings
from gamevault_sync.engine import SyncEngine
from gamevault_sync.state import REJECTED, SyncState


@pytest.fixture
def world(tmp_path):
    steam = make_steam(tmp_path)
    specialk = make_specialk(tmp_path)
    settings = Settings(server="http://x", token="gvd_t", steam_path=str(steam),
                        specialk_path=str(specialk), first_sync_confirmed=True)
    state = SyncState(tmp_path / "state.db")
    server = FakeServer()
    yield settings, state, server, steam, specialk
    state.close()



def sync(world, dry_run=False, **kwargs):
    settings, state, server, *_ = world
    return SyncEngine(server.client(), state, settings, steam_lookup_delay=0, **kwargs).run(dry_run=dry_run)


def test_first_sync_uploads_everything_once(world):
    _settings, state, server, *_ = world
    report = sync(world)
    assert report.found == 7
    assert report.uploaded == 7 and report.failed == 0
    names = {name for name in server.games.values()}
    assert names == {"ELDEN RING", "Portal", "Dolphin", "Cyberpunk 2077", "Hollow Knight"}
    # Elden Ring went by app id, Dolphin by name.
    assert 1245620 in server.steam_ids and 3228583970 not in server.steam_ids
    hints = {name: hint for _, name, hint in server.uploads}
    assert hints["20240301203512_1.jpg"] == "2024-03-01T20:35:12"
    assert state.counts() == {"synced": 7}
    assert len(state.recent()) == 7


def test_second_sync_reads_nothing(world, monkeypatch):
    sync(world)
    _settings, _state, server, *_ = world
    calls_before = list(server.calls)
    reads = []
    monkeypatch.setattr(engine_module, "sha256_file", lambda path: reads.append(path))
    report = sync(world)
    assert report.already == 7 and report.new == 0
    assert reads == []
    assert server.calls == calls_before  # not even a hash check


def test_deleted_on_server_stays_deleted(world):
    sync(world)
    _settings, state, server, *_ = world
    server.hashes.clear()  # everything deleted in the web UI
    assert sync(world).uploaded == 0
    state.forget_sync_status()  # "Check every file again"
    assert sync(world).uploaded == 7


def test_new_and_changed_files_are_picked_up(world):
    sync(world)
    _settings, _state, server, steam, specialk = world
    shot = steam / "userdata" / "11111" / "760" / "remote" / "400" / "screenshots" / "20240101000000_1.jpg"
    write_file(shot, b"portal-2")
    write_file(specialk / "HollowKnight" / "shot.png", b"hk-1-edited", mtime=time.time() - 60)
    report = sync(world)
    assert report.uploaded == 2
    assert {name for _, name, _ in server.uploads[-2:]} == {"20240101000000_1.jpg", "shot.png"}


def test_files_still_being_written_wait(world):
    _settings, _state, _server, _steam, specialk = world
    write_file(specialk / "HollowKnight" / "fresh.png", b"fresh", mtime=time.time())
    report = sync(world)
    assert report.waiting == 1 and report.uploaded == 7


def test_already_on_server_is_not_uploaded(world):
    _settings, _state, server, *_ = world
    server.hashes.add(sha(b"elden-1"))
    report = sync(world)
    assert report.already == 1 and report.uploaded == 6


def test_same_picture_in_two_places_goes_up_once(world):
    _settings, state, _server, _steam, specialk = world
    write_file(specialk / "HollowKnight" / "copy.png", b"hk-1")
    report = sync(world)
    assert report.found == 8 and report.new == 7 and report.uploaded == 7
    assert state.counts() == {"synced": 8}


def test_excluded_games_are_skipped(world):
    settings, _state, server, *_ = world
    settings.excluded_games = ["steam:1245620", "specialk:Cyberpunk2077.exe"]
    report = sync(world)
    assert report.uploaded == 3
    assert "ELDEN RING" not in server.games.values()
    summary = {g.key: g for g in report.games}
    assert summary["steam:1245620"].excluded and summary["steam:1245620"].found == 2


def test_dry_run_uploads_nothing_but_remembers_hashes(world, monkeypatch):
    _settings, _state, server, *_ = world
    report = sync(world, dry_run=True)
    assert report.new == 7 and server.uploads == [] and server.games == {}
    reads = []
    real = engine_module.sha256_file
    monkeypatch.setattr(engine_module, "sha256_file", lambda p: reads.append(p) or real(p))
    assert sync(world).uploaded == 7
    assert reads == []  # the real sync reused the dry run's hashes


def test_rejected_files_are_not_retried_until_they_change(world):
    _settings, state, server, *_ = world
    server.reject.add("shot.png")
    report = sync(world)
    assert report.rejected == 1 and report.uploaded == 6
    assert state.counts() == {"synced": 6, REJECTED: 1}
    server.reject.clear()
    assert sync(world).uploaded == 0


def test_game_deleted_on_server_is_found_again(world):
    sync(world)
    _settings, _state, server, steam, _ = world
    elden_id = server.steam_ids[1245620]
    del server.games[elden_id]
    shot = steam / "userdata" / "11111" / "760" / "remote" / "1245620" / "screenshots" / "20240302000000_1.jpg"
    write_file(shot, b"elden-3")
    report = sync(world)
    assert report.uploaded == 1 and report.failed == 0
    assert server.steam_ids[1245620] != elden_id


def test_signed_out_and_offline_stop_the_pass(world):
    _settings, _state, server, *_ = world
    server.signed_out = True
    with pytest.raises(SignedOutError):
        sync(world)
    server.signed_out = False
    server.offline = True
    with pytest.raises(ServerUnreachableError):
        sync(world)
    server.offline = False
    assert sync(world).uploaded == 7


def test_cancel(world):
    cancel = threading.Event()
    cancel.set()
    report = sync(world, cancel=cancel)
    assert report.cancelled and report.uploaded == 0


def test_newest_screenshots_go_first(world):
    _settings, _state, server, _steam, specialk = world
    write_file(specialk / "HollowKnight" / "newest.png", b"newest", mtime=time.time() - 10)
    sync(world, workers=1)
    assert server.uploads[0][1] == "newest.png"


def test_failed_uploads_are_tried_again_later_not_every_pass(world, monkeypatch):
    _settings, state, server, *_ = world
    server.fail.add("shot.png")
    report = sync(world)
    assert report.failed == 1 and report.uploaded == 6
    report = sync(world)  # straight away: not due yet, so not sent again
    assert report.failed == 0 and report.deferred == 1
    assert [name for _, name, _ in server.uploads].count("shot.png") == 0

    later = time.time() + 120
    monkeypatch.setattr(engine_module.time, "time", lambda: later)
    server.fail.clear()
    report = sync(world)
    assert report.uploaded == 1 and report.deferred == 0
    assert state.counts() == {"synced": 7}


def test_retry_gaps_grow(tmp_path):
    state = SyncState(tmp_path / "s.db")
    state.remember_hashes([("a.png", 1, 1, "aa")])
    gaps = []
    for _ in range(7):
        before = time.time()
        state.mark_failed(["a.png"], "database is locked")
        gaps.append(round(state.lookup(["a.png"])["a.png"].retry_at - before, -1))
    assert gaps == [60, 240, 960, 3840, 15360, 21600, 21600]
    state.mark(["a.png"], "synced")
    assert state.lookup(["a.png"])["a.png"].retry_at is None
    state.close()


def test_a_timestamp_in_the_future_does_not_wait_forever(world):
    _settings, _state, server, _steam, specialk = world
    write_file(specialk / "HollowKnight" / "future.png", b"from the future", mtime=time.time() + 86400)
    report = sync(world)
    assert report.waiting == 0
    assert "future.png" in [name for _, name, _ in server.uploads]


def test_a_damaged_memory_is_set_aside_and_started_fresh(tmp_path):
    path = tmp_path / "sync-state-x.db"
    path.write_bytes(b"this is not a database" * 100)
    state = SyncState.open(path)
    assert state.counts() == {}
    state.close()
    assert [p.name.split(".corrupt-")[0] for p in tmp_path.glob("*.corrupt-*")] == ["sync-state-x.db"]
