"""The unanswered-ticket deadline is off by default; the code stays in place."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import workers
from config import get_settings
from discord_bot import bot as ticket_bot


def test_deadline_is_disabled_by_default():
    settings = get_settings()
    assert settings.ticket_auto_close_enabled is False
    # The interval is still configurable, so turning the timer back on does
    # not require editing code.
    assert settings.ticket_auto_close_hours == 72


def test_disabled_task_never_touches_the_database():
    def must_not_open_a_session():
        raise AssertionError("a disabled deadline must not scan tickets")

    with (
        patch.object(workers, "SessionLocal", must_not_open_a_session),
        patch("config.get_settings", return_value=SimpleNamespace(
            ticket_auto_close_enabled=False, ticket_auto_close_hours=72,
        )),
    ):
        result = workers.task_auto_close_stale_tickets()

    assert result["status"] == "disabled"
    assert result["queued"] == 0
    assert result["closed"] == 0


def test_queued_stale_close_is_dropped_while_the_deadline_is_off(monkeypatch):
    """A job queued before the switch must not delete a channel afterwards."""
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "get_ticket_by_channel",
        lambda *_args, **_kwargs: {"id": 17},
    )
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "ticket_is_stale_unanswered",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("a disabled deadline must not be revalidated")
        ),
    )
    monkeypatch.setattr(
        ticket_bot.bot,
        "get_channel",
        lambda _channel_id: (_ for _ in ()).throw(
            AssertionError("a disabled deadline must not reach Discord")
        ),
    )
    monkeypatch.setattr(
        ticket_bot.settings, "ticket_auto_close_enabled", False, raising=False,
    )

    result = asyncio.run(ticket_bot.execute_bot_action(
        "ticket_close_channel",
        {
            "ticket_id": 17,
            "channel_id": 987654321,
            "closed_by": "Авто-закрытие (нет ответа администрации)",
            "auto_close_before": "2026-09-01T00:00:00",
        },
    ))

    assert result == (True, None)
