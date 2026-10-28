import time

import pytest
from fakes import FakeServer, make_specialk, make_steam
from gamevault_sync import engine as engine_module
from gamevault_sync.config import Settings
from gamevault_sync.service import SyncService, ago, describe
from gamevault_sync.state import SyncState


@pytest.fixture(autouse=True)
def no_steam_delay(monkeypatch):
    monkeypatch.setattr(engine_module, "STEAM_LOOKUP_DELAY", 0)


@pytest.fixture
def setup(tmp_path):
    settings = Settings(server="http://x", token="gvd_t", server_id="fakeserver01",
                        steam_path=str(make_steam(tmp_path)), specialk_path=str(make_specialk(tmp_path)))
    server = FakeServer()
    notes = []
    service = SyncService(settings, open_state=lambda s: SyncState(tmp_path / f"{s.server_key}.db"),
                          client_factory=server.client,
                          notify=lambda title, message: notes.append((title, message)))
    yield service, server, notes
    assert service.stop()
    service.close()


def wait_for(service, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = service.snapshot()
        if predicate(status):
            return status
        time.sleep(0.02)
    raise AssertionError(f"timed out; last status {service.snapshot()}")


def test_first_run_waits_for_a_click_then_syncs(setup):
    service, server, notes = setup
    service.start()
    status = wait_for(service, lambda s: s.state == "ready")
    assert status.last_report.new == 7 and server.uploads == []
    assert describe(status)[0] == "Ready to upload 7 screenshots"

    service.start_first_sync()
    status = wait_for(service, lambda s: s.state == "idle")
    assert len(server.uploads) == 7
    assert status.synced_total == 7
    assert describe(status)[0] == "Up to date"
    assert notes == [("Uploaded to GameVault", "7 screenshots from 5 games")]


def test_offline_server_backs_off_and_recovers(setup):
    service, server, _notes = setup
    service.update_settings(first_sync_confirmed=True)
    server.offline = True
    service.start()
    status = wait_for(service, lambda s: s.state == "offline")
    assert status.next_check_at > time.time() + 20
    assert describe(status)[0] == "Can't reach GameVault"
    server.offline = False
    service.sync_now()
    wait_for(service, lambda s: s.state == "idle")
    assert len(server.uploads) == 7


def test_signed_out_stops_and_says_so(setup):
    service, server, notes = setup
    service.update_settings(first_sync_confirmed=True)
    server.signed_out = True
    service.start()
    status = wait_for(service, lambda s: s.state == "signed_out")
    assert status.next_check_at is None
    assert notes and "signed out" in notes[0][0].lower()


def test_pause_and_resume(setup):
    service, server, _notes = setup
    service.update_settings(first_sync_confirmed=True)
    service.pause(None)
    service.start()
    time.sleep(0.2)
    assert service.snapshot().state == "paused" and server.uploads == []
    service.resume()
    wait_for(service, lambda s: s.state == "idle")
    assert len(server.uploads) == 7


def test_old_session_token_is_swapped_for_a_device_token(setup):
    service, server, _notes = setup
    service.update_settings(token="eyJhbGciOi.old.jwt", first_sync_confirmed=True)
    service.start()
    wait_for(service, lambda s: s.state == "idle")
    assert service.settings.token == "gvd_upgraded"
    assert "device" in server.calls


def test_old_token_on_an_old_server_says_the_server_needs_updating(setup):
    service, server, _notes = setup
    server.too_old = True
    service.update_settings(token="eyJhbGciOi.old.jwt", first_sync_confirmed=True)
    service.start()
    status = wait_for(service, lambda s: s.state == "error")
    assert "too old" in status.message
    assert server.uploads == []


def test_sign_out_tells_the_server(setup):
    service, server, _notes = setup
    service.sign_out()
    assert "sign_out" in server.calls
    assert not service.settings.signed_in
    assert service.snapshot().state == "setup"


def test_ago():
    now = time.time()
    assert ago(None) == "never"
    assert ago(now - 10, now) == "just now"
    assert ago(now - 600, now) == "10 min ago"
    assert ago(now - 7200, now) == "2 h ago"


def test_settings_changes_while_signed_out_keep_saying_so(setup):
    service, server, _notes = setup
    service.update_settings(first_sync_confirmed=True)
    server.signed_out = True
    service.start()
    wait_for(service, lambda s: s.state == "signed_out")
    service.update_settings(notify=False)
    assert service.snapshot().state == "signed_out"


def test_another_server_gets_its_own_memory_and_asks_again(setup):
    service, _server, _notes = setup
    service.update_settings(first_sync_confirmed=True)
    service.start()
    wait_for(service, lambda s: s.state == "idle")
    first_memory = service.state
    assert first_memory.counts() == {"synced": 7}

    # The same server under another address: nothing is forgotten.
    service.use_sign_in(server="http://192.168.1.5:8080", token="gvd_2", device_name="PC",
                        server_id="fakeserver01")
    assert service.state is first_memory and service.settings.first_sync_confirmed

    # A different server: fresh memory, and the first sync waits for a click.
    other = FakeServer()
    other.instance = "otherserver02"
    service._client_factory = other.client
    service.use_sign_in(server="http://other:8080", token="gvd_3", device_name="PC", server_id="otherserver02")
    status = wait_for(service, lambda s: s.state == "ready")
    assert service.state is not first_memory
    assert status.last_report.new == 7 and other.uploads == []
    assert first_memory.counts() == {"synced": 7}  # untouched


def test_settings_carried_over_without_a_server_id_learn_it(tmp_path):
    settings = Settings(server="http://x", token="gvd_t", first_sync_confirmed=True,
                        steam_path=str(make_steam(tmp_path)), specialk_path=str(make_specialk(tmp_path)))
    server = FakeServer()
    service = SyncService(settings, open_state=lambda s: SyncState(tmp_path / f"{s.server_key}.db"),
                          client_factory=server.client)
    service.start()
    try:
        wait_for(service, lambda s: s.state == "idle")
        assert service.settings.server_id == "fakeserver01"
        assert service.state.path.name == "fakeserver01.db"
        assert len(server.uploads) == 7
    finally:
        service.stop()
        service.close()
