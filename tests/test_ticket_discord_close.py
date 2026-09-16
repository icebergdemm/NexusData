import asyncio
from types import SimpleNamespace

import discord
import pytest

from discord_bot import bot as ticket_bot


class _FakeChannel:
    def __init__(self):
        self.id = 987654321
        self.default_role = object()
        self.participant = object()
        self.bot_member = object()
        self.guild = SimpleNamespace(
            default_role=self.default_role,
            me=self.bot_member,
        )
        self.overwrites = {
            self.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
            ),
            self.participant: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
            ),
        }
        self.edits = []
        self.deleted = False

    async def edit(self, *, overwrites, reason):
        self.edits.append((overwrites, reason))
        self.overwrites = overwrites

    async def delete(self, *, reason):
        self.deleted = True


def test_pending_close_reservation_cancels_setup_phase_atomically():
    channel_id = "close-race-before-task"
    ticket_bot._pending_close.pop(channel_id, None)

    first = ticket_bot._reserve_pending_ticket_close(channel_id)
    assert first is not None
    assert first.task is None

    # A competing command reaches the synchronous reservation while the first
    # one is awaiting its Discord response/history write. It must toggle the
    # existing request off, not create a second owner.
    second = ticket_bot._reserve_pending_ticket_close(channel_id)

    assert second is None
    assert first.cancelled is True
    assert not ticket_bot._pending_ticket_close_is_active(channel_id, first)
    assert channel_id not in ticket_bot._pending_close


def test_pending_close_reservation_cancels_created_task():
    async def exercise():
        channel_id = "close-race-with-task"
        ticket_bot._pending_close.pop(channel_id, None)
        first = ticket_bot._reserve_pending_ticket_close(channel_id)
        assert first is not None
        first.task = asyncio.create_task(asyncio.sleep(60))

        assert ticket_bot._reserve_pending_ticket_close(channel_id) is None
        await asyncio.sleep(0)

        assert first.cancelled is True
        assert first.task.cancelled()
        assert channel_id not in ticket_bot._pending_close

    asyncio.run(exercise())


def test_ticket_close_freezes_participants_before_final_sweep(monkeypatch):
    channel = _FakeChannel()

    async def reconcile(ticket_id, reconciled_channel, **_kwargs):
        assert ticket_id == 17
        assert reconciled_channel is channel
        assert channel.overwrites[channel.default_role].send_messages is False
        assert channel.overwrites[channel.participant].send_messages is False
        assert channel.overwrites[channel.bot_member].send_messages is True
        return 4

    monkeypatch.setattr(ticket_bot, "_reconcile_ticket_channel_history", reconcile)
    async def close_ticket(_ticket_id, _closed_by, **_kwargs):
        return True
    monkeypatch.setattr(ticket_bot, "_close_ticket_with_retry", close_ticket)

    count = asyncio.run(
        ticket_bot._freeze_reconcile_and_delete_ticket_channel(
            17,
            channel,
            reason="test close",
            closed_by="Moderator",
        )
    )

    assert count == 4
    assert channel.deleted is True
    assert len(channel.edits) == 1


def test_ticket_close_restores_permissions_when_reconcile_fails(monkeypatch):
    channel = _FakeChannel()

    async def fail_reconcile(_ticket_id, _channel, **_kwargs):
        raise RuntimeError("archive unavailable")

    monkeypatch.setattr(ticket_bot, "_reconcile_ticket_channel_history", fail_reconcile)

    with pytest.raises(RuntimeError, match="archive unavailable"):
        asyncio.run(
            ticket_bot._freeze_reconcile_and_delete_ticket_channel(
                17,
                channel,
                reason="test close",
                closed_by="Moderator",
            )
        )

    assert channel.deleted is False
    assert len(channel.edits) == 2
    restored = channel.edits[-1][0]
    assert restored[channel.default_role].send_messages is True
    assert restored[channel.participant].send_messages is True


def test_ticket_close_restores_permissions_when_cancelled(monkeypatch):
    channel = _FakeChannel()

    async def cancelled_reconcile(_ticket_id, _channel, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(ticket_bot, "_reconcile_ticket_channel_history", cancelled_reconcile)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ticket_bot._freeze_reconcile_and_delete_ticket_channel(
                17,
                channel,
                reason="test close",
                closed_by="Moderator",
            )
        )

    assert channel.deleted is False
    assert len(channel.edits) == 2
    restored = channel.edits[-1][0]
    assert restored[channel.default_role].send_messages is True
    assert restored[channel.participant].send_messages is True
    assert channel.id not in ticket_bot._ticket_channels_closing


def test_deleted_channel_sql_failure_persists_recovery_action(monkeypatch):
    channel = _FakeChannel()
    queued = []

    async def reconcile(_ticket_id, _channel, **_kwargs):
        return 2

    async def fail_close(_ticket_id, _closed_by, **_kwargs):
        return False

    def enqueue(action_type, payload, **kwargs):
        queued.append((action_type, payload, kwargs))
        return 55

    monkeypatch.setattr(ticket_bot, "_reconcile_ticket_channel_history", reconcile)
    monkeypatch.setattr(ticket_bot, "_close_ticket_with_retry", fail_close)
    monkeypatch.setattr(ticket_bot.bot_data, "enqueue_ticket_bot_action", enqueue)

    with pytest.raises(RuntimeError, match="SQL close failed"):
        asyncio.run(
            ticket_bot._freeze_reconcile_and_delete_ticket_channel(
                17,
                channel,
                reason="test close",
                closed_by="Moderator",
            )
        )

    assert channel.deleted is True
    assert queued[0][0] == "ticket_close_channel"
    assert queued[0][1]["ticket_id"] == 17
    assert queued[0][2]["dedupe_key"] == "ticket-close:17"


def test_ticket_close_response_ignores_deleted_channel(monkeypatch):
    interaction = SimpleNamespace()
    response = SimpleNamespace(status=404, reason="Not Found")
    interaction.followup = SimpleNamespace(
        send=_raise_unknown_channel(response),
    )

    asyncio.run(
        ticket_bot._send_ticket_followup(interaction, "closed")
    )


def _raise_unknown_channel(response):
    async def send(*_args, **_kwargs):
        raise discord.HTTPException(response, {"message": "Unknown Channel", "code": 10003})

    return send


def test_stale_close_rechecks_after_history_and_restores(monkeypatch):
    channel = _FakeChannel()

    async def reconcile(_ticket_id, _channel, **_kwargs):
        return 3

    monkeypatch.setattr(ticket_bot, "_reconcile_ticket_channel_history", reconcile)
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "ticket_is_stale_unanswered",
        lambda *_args: False,
    )

    with pytest.raises(ticket_bot._TicketAutoCloseNoLongerEligible):
        asyncio.run(
            ticket_bot._freeze_reconcile_and_delete_ticket_channel(
                17,
                channel,
                reason="stale close",
                closed_by="Auto close",
                auto_close_before="2026-09-01T00:00:00",
            )
        )

    assert channel.deleted is False
    assert len(channel.edits) == 2


def test_manual_channel_delete_marks_archive_incomplete_without_closing(monkeypatch):
    """A Gateway delete cannot prove the final Discord evidence was archived."""
    channel = SimpleNamespace(id=123456789, name="ticket-evidence")
    marked = []
    refreshed = []

    monkeypatch.setattr(
        ticket_bot.bot_data,
        "get_open_tickets",
        lambda: [{"id": 42, "discord_channel_id": str(channel.id)}],
    )
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "mark_ticket_archive_incomplete",
        lambda ticket_id: marked.append(ticket_id) or True,
    )

    async def refresh():
        refreshed.append(True)

    monkeypatch.setattr(ticket_bot, "refresh_ticket_status", refresh)

    asyncio.run(ticket_bot.on_guild_channel_delete(channel))

    assert marked == [42]
    assert refreshed == [True]


def _discord_not_found():
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(response, {"message": "Unknown Channel", "code": 10003})


def test_missing_channel_without_archive_proof_is_marked_incomplete(monkeypatch):
    marked = []

    monkeypatch.setattr(ticket_bot.bot, "get_channel", lambda _channel_id: None)

    async def fetch_channel(_channel_id):
        raise _discord_not_found()

    monkeypatch.setattr(ticket_bot.bot, "fetch_channel", fetch_channel)
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "get_ticket_by_channel",
        lambda *_args, **_kwargs: {"id": 17, "archive_state": None},
    )
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "mark_ticket_archive_incomplete",
        lambda ticket_id: marked.append(ticket_id) or True,
    )

    async def close_must_not_run(*_args, **_kwargs):
        raise AssertionError("unproven missing channel must not be closed")

    monkeypatch.setattr(ticket_bot, "_close_ticket_with_retry", close_must_not_run)

    result = asyncio.run(
        ticket_bot.execute_bot_action(
            "ticket_close_channel",
            {"ticket_id": 17, "channel_id": 987654321, "closed_by": "Moderator"},
        )
    )

    assert result == (True, None)
    assert marked == [17]


def test_missing_channel_with_post_delete_archive_proof_finishes_sql_close(monkeypatch):
    closed = []

    monkeypatch.setattr(ticket_bot.bot, "get_channel", lambda _channel_id: None)

    async def fetch_channel(_channel_id):
        raise _discord_not_found()

    monkeypatch.setattr(ticket_bot.bot, "fetch_channel", fetch_channel)
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "get_ticket_by_channel",
        lambda *_args, **_kwargs: {"id": 17, "archive_state": None},
    )

    async def close_ticket(ticket_id, closed_by, **_kwargs):
        closed.append((ticket_id, closed_by))
        return True

    monkeypatch.setattr(ticket_bot, "_close_ticket_with_retry", close_ticket)
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "mark_ticket_archive_incomplete",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("proven recovery must finish closure")
        ),
    )

    result = asyncio.run(
        ticket_bot.execute_bot_action(
            "ticket_close_channel",
            {
                "ticket_id": 17,
                "channel_id": 987654321,
                "closed_by": "Moderator",
                "evidence_archive_confirmed": True,
            },
        )
    )

    assert result == (True, None)
    assert closed == [(17, "Moderator")]


class _HistoryChannel(_FakeChannel):
    """A ticket channel whose final history sweep can be scripted."""

    def __init__(self, message_ids):
        super().__init__()
        self.message_ids = list(message_ids)
        self.fetched = []

    def history(self, *, limit=None, oldest_first=False):
        async def iterator():
            for message_id in self.message_ids:
                yield SimpleNamespace(id=message_id)
        return iterator()

    async def fetch_message(self, message_id):
        self.fetched.append(int(message_id))
        return SimpleNamespace(id=int(message_id))


def _archive_rows(monkeypatch, rows):
    monkeypatch.setattr(
        ticket_bot.bot_data, "get_ticket_message_archives", lambda _ticket_id: rows,
    )


def _stub_capture(monkeypatch, calls=None):
    async def capture(ticket_id, message):
        if calls is not None:
            calls.append((ticket_id, int(message.id)))
        return 1

    monkeypatch.setattr(ticket_bot, "_capture_ticket_message", capture)


def test_reconcile_closes_when_missing_media_can_never_be_recovered(monkeypatch):
    channel = _HistoryChannel([10])
    _stub_capture(monkeypatch)
    _archive_rows(monkeypatch, [
        # Above the per-file archive limit: no retry can ever store it.
        {
            "id": 1,
            "discord_message_id": "10",
            "attachments": [{"filename": "evidence.mp4", "archive_status": "too_large"}],
            "reference_snapshot": None,
        },
        # A bare CDN link from a message that is no longer in Discord.
        {
            "id": 2,
            "discord_message_id": "99",
            "attachments": ["https://cdn.discordapp.com/expired.png"],
            "reference_snapshot": None,
        },
    ])

    defects = []
    count = asyncio.run(
        ticket_bot._reconcile_ticket_channel_history(17, channel, defects=defects)
    )

    assert count == 1
    assert [item["permanent"] for item in defects] == [True, True]
    # Nothing recoverable was found, so no retry download was attempted.
    assert channel.fetched == []


def test_reconcile_retries_recoverable_media_then_refuses_to_delete(monkeypatch):
    channel = _HistoryChannel([10])
    captured = []
    _stub_capture(monkeypatch, captured)
    _archive_rows(monkeypatch, [{
        "id": 1,
        "discord_message_id": "10",
        "attachments": [{"filename": "proof.png", "archive_status": "failed"}],
        "reference_snapshot": None,
    }])

    with pytest.raises(ticket_bot.TicketArchiveIncomplete):
        asyncio.run(ticket_bot._reconcile_ticket_channel_history(17, channel))

    # The message is still in Discord, so the close re-downloads it before
    # giving up and leaving the channel in place.
    assert channel.fetched == [10, 10]
    assert captured == [(17, 10), (17, 10), (17, 10)]


def test_forced_reconcile_reports_recoverable_media_instead_of_blocking(monkeypatch):
    channel = _HistoryChannel([10])
    _stub_capture(monkeypatch)
    _archive_rows(monkeypatch, [{
        "id": 1,
        "discord_message_id": "10",
        "attachments": [{"filename": "proof.png", "archive_status": "failed"}],
        "reference_snapshot": None,
    }])

    defects = []
    count = asyncio.run(
        ticket_bot._reconcile_ticket_channel_history(
            17, channel, force=True, defects=defects,
        )
    )

    assert count == 1
    assert [item["permanent"] for item in defects] == [False]


def test_incomplete_archive_is_recorded_on_the_closed_ticket(monkeypatch):
    channel = _HistoryChannel([10])
    closed = []

    async def reconcile(ticket_id, reconciled_channel, *, force=False, defects=None):
        if defects is not None:
            defects.append({
                "message_id": 1,
                "discord_message_id": "10",
                "filename": "evidence.mp4",
                "status": "too_large",
                "permanent": True,
            })
        return 1

    async def close_ticket(ticket_id, closed_by, *, archive_state="complete"):
        closed.append((ticket_id, closed_by, archive_state))
        return True

    monkeypatch.setattr(ticket_bot, "_reconcile_ticket_channel_history", reconcile)
    monkeypatch.setattr(ticket_bot, "_close_ticket_with_retry", close_ticket)

    asyncio.run(
        ticket_bot._freeze_reconcile_and_delete_ticket_channel(
            17, channel, reason="test close", closed_by="Moderator",
        )
    )

    assert channel.deleted is True
    assert closed == [(17, "Moderator", "partial")]
