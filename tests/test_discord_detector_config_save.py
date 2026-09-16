from types import SimpleNamespace
import asyncio

from routes import discord_bot_api


class _Query:
    def all(self):
        return []

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return None


class _Db:
    def query(self, *_args, **_kwargs):
        return _Query()

    def add(self, _record):
        pass

    def commit(self):
        pass


class _Request:
    async def json(self):
        return {"key": "monitored_channel_ids", "value": "[]", "source": "advanced"}


class _DetectorConfig:
    monitored_channel_ids = {1428721204482539551}
    excluded_channel_ids = set()
    signals_channel_id = ""
    moderator_role_id = ""

    def public_status(self):
        return {}


def test_detector_config_save_does_not_shadow_logging(monkeypatch):
    monkeypatch.setattr(discord_bot_api, "_get_admin", lambda *_args: SimpleNamespace(id=7))
    monkeypatch.setattr(discord_bot_api, "check_permission", lambda *_args: True)
    async def valid_picker_value(*_args):
        return "[]"

    monkeypatch.setattr(discord_bot_api, "_validate_picker_whitelist", valid_picker_value)
    monkeypatch.setattr("config.get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "discord_bot.address_detector.config.load_detector_config",
        lambda *_args: _DetectorConfig(),
    )
    monkeypatch.setattr("routes.admin.log_admin_action", lambda *_args, **_kwargs: None)

    response = asyncio.run(discord_bot_api.api_admin_discord_bot_set_config(_Request(), _Db()))

    assert response["status"] == "ok"
    assert response["effective"]["monitored_channel_ids"] == ["1428721204482539551"]
