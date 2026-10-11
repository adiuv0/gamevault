import json

from gamevault_sync import app_paths, config
from gamevault_sync.config import Settings


def test_token_lives_in_the_keyring(isolated_home):
    Settings(server="http://tower:8080", token="gvd_secret", steam_path="D:/Steam").save()
    on_disk = json.loads(config.CONFIG_PATH.read_text())
    assert "token" not in on_disk
    assert on_disk[config._KEYRING_FLAG_KEY] is True
    loaded = Settings.load()
    assert loaded.token == "gvd_secret"
    assert loaded.steam_path == "D:/Steam"
    assert loaded.signed_in


def test_without_keyring_token_goes_to_json_with_warning(monkeypatch, capsys):
    monkeypatch.setattr(config, "_get_keyring_module", lambda: None)
    Settings(server="http://x", token="gvd_plain").save()
    assert json.loads(config.CONFIG_PATH.read_text())["token"] == "gvd_plain"
    assert "keyring" in capsys.readouterr().err.lower()
    assert Settings.load().token == "gvd_plain"


def test_signing_out_clears_the_keyring():
    Settings(server="http://x", token="gvd_secret").save()
    Settings(server="http://x", token="").save()
    assert config._get_keyring_module().store == {}
    assert not Settings.load().signed_in


def test_unknown_keys_and_bad_values_are_ignored():
    config.save_config({"server": "http://x", "something_new": 1, "interval_seconds": "soon"})
    settings = Settings.load()
    assert settings.server == "http://x"
    assert settings.interval_seconds == 60


def test_old_script_settings_are_migrated(tmp_path):
    keyring = config._get_keyring_module()
    keyring.set_password(config._KEYRING_SERVICE, config._KEYRING_USERNAME, "old-jwt")
    app_paths.LEGACY_CONFIG_PATH.write_text(json.dumps({
        "server": "http://tower:8080", "mode": "both", "specialk_path": "C:/SK",
        config._KEYRING_FLAG_KEY: True,
    }))
    settings = Settings.load()
    assert settings.server == "http://tower:8080"
    assert settings.token == "old-jwt"
    assert settings.steam_enabled and settings.specialk_enabled
    assert settings.specialk_path == "C:/SK"
    assert settings.first_sync_confirmed  # they already chose to upload everything
    assert config.CONFIG_PATH.exists()
    legacy = json.loads(app_paths.LEGACY_CONFIG_PATH.read_text())
    # Kept, minus the sign-in, which now belongs to the app alone.
    assert legacy == {"server": "http://tower:8080", "mode": "both", "specialk_path": "C:/SK"}
    assert Settings.load().token == "old-jwt"


def test_plaintext_token_in_the_old_file_is_removed(tmp_path):
    app_paths.LEGACY_CONFIG_PATH.write_text(json.dumps({"server": "http://tower:8080", "token": "old-jwt"}))
    assert Settings.load().token == "old-jwt"
    assert "token" not in json.loads(app_paths.LEGACY_CONFIG_PATH.read_text())


def test_server_key_names_the_memory_file():
    assert Settings(server="http://x", server_id="a1b2c3d4e5f60718").server_key == "a1b2c3d4e5f60718"
    # An odd id from the server can't reach outside the folder.
    assert Settings(server="http://x", server_id="../../evil").server_key == "evil"
    by_url = Settings(server="http://Tower:8080/").server_key
    assert by_url.startswith("url-") and by_url == Settings(server="http://tower:8080").server_key
