import asyncio
from types import SimpleNamespace


def test_viewer_roles_include_discord_hoist_flag(monkeypatch):
    from discord_bot import bot as bot_module

    role = SimpleNamespace(
        id=123, name="Crew", color=SimpleNamespace(value=0x123456), position=5,
        managed=False, mentionable=True, hoist=True,
    )
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: SimpleNamespace(roles=[role]))

    assert asyncio.run(bot_module.viewer_get_roles()) == [{
        "id": "123", "name": "Crew", "color": "#123456", "position": 5,
        "managed": False, "mentionable": True, "hoist": True,
    }]
