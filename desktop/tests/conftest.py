import os
import sys
from pathlib import Path

import pytest

# Keep every test away from the real settings folder, even at import time.
os.environ["GAMEVAULT_SYNC_HOME"] = str(Path(__file__).resolve().parent / ".unused-home")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, value):
        self.store[(service, username)] = value

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    from gamevault_sync import app_paths, config

    home = tmp_path / "home"
    monkeypatch.setenv("GAMEVAULT_SYNC_HOME", str(home))
    monkeypatch.setattr(config, "CONFIG_PATH", home / "config.json")
    monkeypatch.setattr(app_paths, "LEGACY_CONFIG_PATH", tmp_path / "legacy.json")
    keyring = FakeKeyring()
    monkeypatch.setattr(config, "_get_keyring_module", lambda: keyring)
    monkeypatch.setattr(config, "_keyring_warning_shown", False)
    return home
