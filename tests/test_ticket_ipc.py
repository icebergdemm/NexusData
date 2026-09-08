from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import discord
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from discord_bot import bot as ticket_bot
from discord_bot import data as ticket_data
from models import DiscordBotAction, Ticket


def test_discord_profile_asset_keeps_animated_avatar_format():
    asset = discord.Asset._from_avatar(None, 987047045498011658, "a_animatedhash")

    url = ticket_bot._discord_asset_url(asset, size=256)

    assert url.endswith("/a_animatedhash.gif?size=256")


def test_discord_profile_asset_keeps_animated_banner_format():
    asset = discord.Asset._from_user_banner(None, 987047045498011658, "a_bannerhash")

    url = ticket_bot._discord_asset_url(asset, size=1024)

    assert url.endswith("/a_bannerhash.gif?size=1024")


@pytest.fixture()
def ipc_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(ticket_data, "SessionLocal", sessions)
    try:
        yield sessions
    finally:
        engine.dispose()


def test_ticket_ipc_deduplicates_claims_and_ignores_unmarked_legacy_rows(ipc_db):
    db = ipc_db()
    db.add(DiscordBotAction(
        action_type="ticket_send_message",
        payload={"ticket_id": 1},
        executed=0,
    ))
    db.commit()
    db.close()

    first_id = ticket_data.enqueue_ticket_bot_action(
        "ticket_send_message",
        {"ticket_id": 1, "message_id": 9},
        dedupe_key="ticket-send:9",
    )
    assert ticket_data.enqueue_ticket_bot_action(
        "ticket_send_message",
        {"ticket_id": 1, "message_id": 9},
        dedupe_key="ticket-send:9",
    ) == first_id

    claimed = ticket_data.claim_next_ticket_bot_action()
    assert claimed["id"] == first_id
    assert claimed["payload"]["_execution_count"] == 1

    db = ipc_db()
    legacy = db.query(DiscordBotAction).filter(DiscordBotAction.id != first_id).one()
    assert legacy.executed == 2
    db.close()


def test_ticket_ipc_delayed_retry_and_crash_recovery(ipc_db):
    action_id = ticket_data.enqueue_ticket_bot_action(
        "ticket_close_channel",
        {"ticket_id": 7, "channel_id": "70"},
        dedupe_key="ticket-close:7",
    )
    assert ticket_data.claim_next_ticket_bot_action()["id"] == action_id
    ticket_data.requeue_ticket_bot_action(
        action_id,
        "retry later",
        delay_seconds=60,
        increment_attempt=True,
    )
    assert ticket_data.claim_next_ticket_bot_action() is None

    db = ipc_db()
    row = db.get(DiscordBotAction, action_id)
    row.executed = 3
    db.commit()
    db.close()
    assert ticket_data.recover_interrupted_ticket_bot_actions() == 1
    claimed = ticket_data.claim_next_ticket_bot_action()
    assert claimed["id"] == action_id
    assert claimed["payload"]["_attempt"] == 1
    assert claimed["payload"]["_execution_count"] == 2


def test_close_recovery_upgrades_existing_running_action_with_archive_proof(ipc_db):
    action_id = ticket_data.enqueue_ticket_bot_action(
        "ticket_close_channel",
        {"ticket_id": 7, "channel_id": "70", "closed_by": "Moderator"},
        dedupe_key="ticket-close:7",
    )
    claimed = ticket_data.claim_next_ticket_bot_action()
    assert claimed["id"] == action_id
    assert "evidence_archive_confirmed" not in claimed["payload"]

    same_id = ticket_data.enqueue_ticket_bot_action(
        "ticket_close_channel",
        {
            "ticket_id": 7,
            "channel_id": "70",
            "closed_by": "Moderator",
            "evidence_archive_confirmed": True,
        },
        dedupe_key="ticket-close:7",
    )

    assert same_id == action_id
    db = ipc_db()
    row = db.get(DiscordBotAction, action_id)
    assert row.payload["evidence_archive_confirmed"] is True
    assert row.executed == 3
    db.close()


def test_only_archive_confirmed_close_can_recover_incomplete_ticket(ipc_db):
    db = ipc_db()
    db.add(Ticket(
        id=7,
        ticket_type="appeal",
        title="Evidence",
        status="open",
        archive_state="incomplete",
        creator_discord_id="42",
    ))
    db.commit()
    db.close()

    assert ticket_data.close_ticket(7, "Unproven") is False
    assert ticket_data.close_ticket(
        7,
        "Recovered",
        evidence_archive_confirmed=True,
    ) is True

    db = ipc_db()
    ticket = db.get(Ticket, 7)
    assert ticket.status == "closed"
    assert ticket.archive_state == "complete"
    assert ticket.closed_by == "Recovered"
    db.close()


def test_dispatch_uses_durable_queue_and_timeout_does_not_cancel(monkeypatch):
    enqueued = []

    def enqueue(*args, **kwargs):
        enqueued.append((args, kwargs))
        return 41

    monkeypatch.setattr(ticket_bot.bot_data, "enqueue_ticket_bot_action", enqueue)
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "get_ticket_bot_action_status",
        lambda _action_id: {"state": "queued"},
    )

    result = asyncio.run(ticket_bot.dispatch_ticket_bot_action(
        "ticket_close_channel",
        {"ticket_id": 3, "channel_id": "30"},
        timeout=0,
        dedupe_key="ticket-close:3",
    ))

    assert result[0] is None
    assert result[2] == 41
    assert len(enqueued) == 1


def test_duplicate_send_action_skips_discord_when_delivery_already_confirmed(monkeypatch):
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "get_ticket_by_channel",
        lambda *_args: {"id": 5, "status": "open"},
    )
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "get_ticket_message_delivery",
        lambda *_args: {"status": "sent", "discord_message_id": "999"},
    )

    def must_not_resolve_channel(_channel_id):
        raise AssertionError("a confirmed send must not call Discord again")

    monkeypatch.setattr(ticket_bot.bot, "get_channel", must_not_resolve_channel)
    success, error = asyncio.run(ticket_bot.execute_bot_action(
        "ticket_send_message",
        {"ticket_id": 5, "channel_id": "50", "message_id": 12, "content": "hello"},
    ))
    assert success is True
    assert error is None


def test_own_bot_echo_links_site_row_without_duplicate_archive(monkeypatch):
    monkeypatch.setattr(ticket_bot.bot._connection, "user", SimpleNamespace(id=101))
    calls = []
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "link_site_ticket_message",
        lambda *args: calls.append(args) or 77,
    )

    async def must_not_archive(*_args, **_kwargs):
        raise AssertionError("linked site echo must reuse its local media")

    monkeypatch.setattr(ticket_bot, "_archive_ticket_attachments", must_not_archive)
    message = SimpleNamespace(
        id=999,
        content="from site",
        attachments=[],
        created_at=datetime.now(timezone.utc),
        author=SimpleNamespace(id=101, bot=True),
    )

    assert asyncio.run(ticket_bot._capture_ticket_message(4, message)) == 77
    assert calls[0][0] == 4
    assert calls[0][1] == "999"


def test_third_party_bot_message_and_embed_are_archived(monkeypatch):
    monkeypatch.setattr(ticket_bot.bot._connection, "user", SimpleNamespace(id=101))
    monkeypatch.setattr(
        ticket_bot.bot_data,
        "get_ticket_message_by_discord_id",
        lambda *_args: None,
    )
    captured = {}

    async def archive(*_args, **_kwargs):
        return []

    async def reference(*_args, **_kwargs):
        return None

    def add_message(**kwargs):
        captured.update(kwargs)
        return 88

    monkeypatch.setattr(ticket_bot, "_archive_ticket_attachments", archive)
    monkeypatch.setattr(ticket_bot, "_build_ticket_reference_snapshot", reference)
    monkeypatch.setattr(ticket_bot.bot_data, "add_ticket_message", add_message)
    message = SimpleNamespace(
        id=1000,
        content="automation evidence",
        attachments=[],
        embeds=[SimpleNamespace(to_dict=lambda: {"title": "Audit event"})],
        stickers=[],
        reference=None,
        message_snapshots=[],
        created_at=datetime.now(timezone.utc),
        author=SimpleNamespace(
            id=202,
            bot=True,
            display_name="Webhook",
            display_avatar=None,
        ),
    )

    assert asyncio.run(ticket_bot._capture_ticket_message(4, message)) == 88
    assert captured["is_bot"] is True
    assert captured["reference_snapshot"]["kind"] == "message_content"
    assert captured["reference_snapshot"]["outer_embeds"][0]["title"] == "Audit event"


def test_forced_close_records_an_incomplete_archive(ipc_db):
    db = ipc_db()
    db.add(Ticket(
        id=8,
        ticket_type="appeal",
        title="Channel deleted in Discord",
        status="open",
        archive_state="incomplete",
        creator_discord_id="42",
    ))
    db.commit()
    db.close()

    # Without an explicit decision the ticket still stays open for review.
    assert ticket_data.close_ticket(8, "Unproven") is False
    assert ticket_data.close_ticket(8, "Moderator", force=True) is True

    db = ipc_db()
    ticket = db.get(Ticket, 8)
    assert ticket.status == "closed"
    # The exception survives the closure instead of being laundered into a
    # complete archive nobody can audit.
    assert ticket.archive_state == "incomplete"
    assert ticket.closed_by == "Moderator"
    db.close()


def test_partial_archive_state_is_persisted_on_close(ipc_db):
    db = ipc_db()
    db.add(Ticket(
        id=9,
        ticket_type="appeal",
        title="Oversized evidence",
        status="open",
        creator_discord_id="42",
    ))
    db.commit()
    db.close()

    assert ticket_data.close_ticket(
        9,
        "Moderator",
        evidence_archive_confirmed=True,
        archive_state="partial",
    ) is True

    db = ipc_db()
    assert db.get(Ticket, 9).archive_state == "partial"
    db.close()


def test_message_is_visible_before_its_attachments_are_archived(monkeypatch):
    monkeypatch.setattr(ticket_bot.bot._connection, "user", SimpleNamespace(id=101))
    captured = {}
    scheduled = []

    def add_message(**kwargs):
        captured.update(kwargs)
        return 91

    async def must_not_archive(*_args, **_kwargs):
        raise AssertionError("the row must be written before any download")

    monkeypatch.setattr(ticket_bot.bot_data, "add_ticket_message", add_message)
    monkeypatch.setattr(ticket_bot, "_archive_ticket_attachments", must_not_archive)
    monkeypatch.setattr(
        ticket_bot,
        "_schedule_ticket_media_archive",
        lambda ticket_id, message: scheduled.append((ticket_id, message.id)),
    )
    message = SimpleNamespace(
        id=1001,
        content="screenshot attached",
        attachments=[SimpleNamespace(
            id=555,
            filename="proof.png",
            content_type="image/png",
            size=4096,
            width=800,
            height=600,
            url="https://cdn.discordapp.com/proof.png",
            proxy_url="https://media.discordapp.net/proof.png",
        )],
        embeds=[],
        stickers=[],
        reference=None,
        message_snapshots=[],
        created_at=datetime.now(timezone.utc),
        author=SimpleNamespace(
            id=202,
            bot=False,
            display_name="Player",
            display_avatar=None,
        ),
    )

    assert asyncio.run(ticket_bot._capture_ticket_message_now(4, message)) == 91
    assert captured["content"] == "screenshot attached"
    assert captured["attachments"][0]["archive_status"] == "pending"
    assert captured["attachments"][0]["filename"] == "proof.png"
    assert scheduled == [(4, 1001)]


def test_refreshed_cdn_link_upgrades_the_legacy_row_instead_of_duplicating_it():
    """A re-signed Discord link is the same file, not a second one."""
    legacy = (
        "https://cdn.discordapp.com/attachments/70/555/proof.png"
        "?ex=old&is=old&hm=old"
    )
    stored = {
        "id": "555",
        "filename": "proof.png",
        "source_url": (
            "https://cdn.discordapp.com/attachments/70/555/proof.png"
            "?ex=new&is=new&hm=new"
        ),
        "proxy_url": "https://media.discordapp.net/attachments/70/555/proof.png",
        "storage_key": "abc.png",
        "storage_namespace": "msg-1001",
        "archive_status": "stored",
    }

    merged = ticket_data._merge_ticket_attachments([legacy], [stored])

    assert len(merged) == 1
    assert merged[0]["archive_status"] == "stored"


def test_unrelated_cdn_objects_are_not_merged():
    first = "https://cdn.discordapp.com/attachments/70/555/proof.png?ex=a"
    second = {
        "id": "556",
        "filename": "other.png",
        "source_url": "https://cdn.discordapp.com/attachments/70/556/other.png?ex=b",
        "storage_key": "def.png",
        "archive_status": "stored",
    }

    assert len(ticket_data._merge_ticket_attachments([first], [second])) == 2
