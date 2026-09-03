import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import DiscordMember, PlayerData, Ticket, TicketMessage
from services_ticket_management import (
    TicketStateError,
    apply_ticket_status_filter,
    purge_deleted_tickets,
    restore_ticket,
    search_ticket_members,
    soft_delete_ticket,
)
from discord_bot import bot as discord_bot


class _FakeOverwrite:
    def __init__(self, *, view_channel=None, send_messages=None, read_message_history=None):
        self.view_channel = view_channel
        self.send_messages = send_messages
        self.read_message_history = read_message_history


class _FakeMember:
    id = 200
    bot = False
    display_name = "Doctor Who"
    mention = "<@200>"


class _FakeMessage:
    id = 987


class _FakeChannel:
    id = 555

    def __init__(self, overwrite=None):
        self.overwrite = overwrite or _FakeOverwrite()
        self.set_permissions = AsyncMock()
        self.send = AsyncMock(return_value=_FakeMessage())

    def overwrites_for(self, member):
        return self.overwrite


class _FakeGuild:
    members = [_FakeMember()]

    def get_member(self, member_id):
        return _FakeMember() if member_id == 200 else None

    async def fetch_member(self, member_id):
        return _FakeMember()


class TicketManagementTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[
                PlayerData.__table__,
                DiscordMember.__table__,
                Ticket.__table__,
                TicketMessage.__table__,
            ],
        )
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _ticket(self, *, status="open", deleted_at=None):
        ticket = Ticket(
            ticket_type="help",
            title="Test ticket",
            status=status,
            creator_discord_id="100",
            deleted_at=deleted_at,
        )
        self.db.add(ticket)
        self.db.flush()
        return ticket

    def test_status_filter_keeps_deleted_tickets_out_of_all_views(self):
        open_ticket = self._ticket(status="open")
        closed_ticket = self._ticket(status="closed")
        deleted_ticket = self._ticket(
            status="closed",
            deleted_at=datetime(2026, 1, 1, 12),
        )
        self.db.commit()

        all_tickets = apply_ticket_status_filter(
            self.db.query(Ticket), "all"
        ).all()
        deleted_tickets = apply_ticket_status_filter(
            self.db.query(Ticket), "deleted"
        ).all()

        self.assertEqual({t.id for t in all_tickets}, {open_ticket.id, closed_ticket.id})
        self.assertEqual([t.id for t in deleted_tickets], [deleted_ticket.id])

    def test_soft_delete_requires_closed_ticket_and_records_actor(self):
        ticket = self._ticket(status="closed")
        self.db.commit()

        deleted = soft_delete_ticket(
            self.db, ticket.id, "admin@example.com", now=datetime(2026, 1, 2)
        )

        self.assertEqual(deleted.deleted_by, "admin@example.com")
        self.assertEqual(deleted.deleted_at, datetime(2026, 1, 2))

        open_ticket = self._ticket(status="open")
        self.db.commit()
        with self.assertRaises(TicketStateError):
            soft_delete_ticket(self.db, open_ticket.id, "admin@example.com")

    def test_restore_clears_soft_delete_fields_and_keeps_ticket_closed(self):
        ticket = self._ticket(status="closed", deleted_at=datetime(2026, 1, 2))
        ticket.deleted_by = "admin@example.com"
        self.db.commit()

        restored = restore_ticket(self.db, ticket.id)

        self.assertIsNone(restored.deleted_at)
        self.assertIsNone(restored.deleted_by)
        self.assertEqual(restored.status, "closed")

        with self.assertRaises(TicketStateError):
            restore_ticket(self.db, ticket.id)

    def test_purge_deletes_messages_before_ticket(self):
        deleted_at = datetime(2026, 1, 1, 12)
        ticket = self._ticket(status="closed", deleted_at=deleted_at)
        self.db.add(
            TicketMessage(
                ticket_id=ticket.id,
                author_discord_id="200",
                content="history",
            )
        )
        self.db.commit()

        result = purge_deleted_tickets(self.db, now=datetime(2026, 2, 2, 12))

        self.assertEqual(result["deleted_tickets"], 1)
        self.assertEqual(self.db.query(Ticket).filter(Ticket.id == ticket.id).count(), 0)
        self.assertEqual(
            self.db.query(TicketMessage)
            .filter(TicketMessage.ticket_id == ticket.id)
            .count(),
            0,
        )

    def test_member_search_matches_display_name_or_username(self):
        self.db.add_all(
            [
                DiscordMember(discord_id="100", display_name="Captain Nemo", username="nemo"),
                DiscordMember(discord_id="200", display_name="Doctor Who", username="doctor"),
            ]
        )
        self.db.commit()

        total, members = search_ticket_members(self.db, "nemo")

        self.assertEqual(total, 1)
        self.assertEqual([member.discord_id for member in members], ["100"])


class TicketDiscordOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_ticket_members_returns_only_members_with_channel_access(self):
        channel = _FakeChannel(_FakeOverwrite(view_channel=True, send_messages=True, read_message_history=True))
        with patch.object(discord_bot.bot, "get_guild", return_value=_FakeGuild()), \
             patch.object(discord_bot.bot, "get_channel", return_value=channel):
            result = await discord_bot.list_ticket_members("555")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["members"][0]["discord_id"], "200")

    async def test_add_member_does_not_duplicate_existing_access(self):
        channel = _FakeChannel(_FakeOverwrite(view_channel=True, send_messages=True, read_message_history=True))
        with patch.object(discord_bot.bot, "get_guild", return_value=_FakeGuild()), \
             patch.object(discord_bot.bot, "get_channel", return_value=channel):
            result = await discord_bot.add_ticket_member("555", "200", "admin", 1)

        self.assertEqual(result["status"], "already_present")
        channel.set_permissions.assert_not_awaited()
        channel.send.assert_not_awaited()

    async def test_add_member_sets_access_sends_announcement_and_saves_message(self):
        channel = _FakeChannel()
        with patch.object(discord_bot.bot, "get_guild", return_value=_FakeGuild()), \
             patch.object(discord_bot.bot, "get_channel", return_value=channel), \
             patch.object(discord_bot.bot_data, "add_ticket_message", return_value=True) as save_message:
            result = await discord_bot.add_ticket_member("555", "200", "admin", 1)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["message_id"], "987")
        channel.set_permissions.assert_awaited_once()
        channel.send.assert_awaited_once()
        save_message.assert_called_once()

    async def test_remove_member_clears_access_sends_announcement_and_saves_message(self):
        channel = _FakeChannel(_FakeOverwrite(view_channel=True, send_messages=True, read_message_history=True))
        with patch.object(discord_bot.bot, "get_guild", return_value=_FakeGuild()), \
             patch.object(discord_bot.bot, "get_channel", return_value=channel), \
             patch.object(discord_bot.bot_data, "add_ticket_message", return_value=True) as save_message:
            result = await discord_bot.remove_ticket_member("555", "200", "admin", 1)

        self.assertEqual(result["status"], "ok")
        channel.set_permissions.assert_awaited_once()
        self.assertIsNone(channel.set_permissions.await_args.kwargs["overwrite"])
        channel.send.assert_awaited_once()
        save_message.assert_called_once()

    async def test_remove_member_is_idempotent_when_access_is_absent(self):
        channel = _FakeChannel()
        with patch.object(discord_bot.bot, "get_guild", return_value=_FakeGuild()), \
             patch.object(discord_bot.bot, "get_channel", return_value=channel):
            result = await discord_bot.remove_ticket_member("555", "200", "admin", 1)

        self.assertEqual(result["status"], "already_absent")
        channel.set_permissions.assert_not_awaited()
        channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
