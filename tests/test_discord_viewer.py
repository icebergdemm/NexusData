import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from discord_bot import bot as bot_module
from discord_bot import viewer_events


def _channel(channel_type, *, channel_id, position, can_view=True, can_send=True):
    channel_class = {
        "text": discord.TextChannel,
        "voice": discord.VoiceChannel,
        "stage": discord.StageChannel,
        "forum": discord.ForumChannel,
    }[channel_type]
    channel = Mock(spec=channel_class)
    channel.id = channel_id
    channel.name = f"{channel_type}-{channel_id}"
    channel.position = position
    channel.category_id = None
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=can_view,
        send_messages=can_send,
    )
    if channel_type == "text":
        async def no_archived_threads(**_kwargs):
            if False:
                yield None

        channel.archived_threads.side_effect = no_archived_threads
    if channel_type == "forum":
        async def no_archived_threads(*, limit=100, before=None):
            if False:
                yield None

        channel.archived_threads.side_effect = no_archived_threads
    if channel_type in {"voice", "stage"}:
        channel.members = []
    return channel


def test_viewer_tree_includes_visible_text_voice_and_stage_channels(monkeypatch):
    text = _channel("text", channel_id=10, position=2)
    voice = _channel("voice", channel_id=20, position=1)
    stage = _channel("stage", channel_id=30, position=3)
    hidden = _channel("voice", channel_id=40, position=4, can_view=False)
    guild = SimpleNamespace(
        me=object(),
        channels=[text, voice, stage, hidden],
        text_channels=[text],
        categories=[],
        id=123,
        name="Test guild",
        icon=None,
    )
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    result = asyncio.run(bot_module.viewer_get_tree())

    assert [(channel["id"], channel["type"], channel["can_send"]) for channel in result["channels"]] == [
        ("20", "voice", True),
        ("10", "text", True),
        ("30", "stage", True),
    ]


def test_viewer_tree_includes_forum_and_media_channels(monkeypatch):
    forum = _channel("forum", channel_id=50, position=1)
    forum.type = discord.ChannelType.forum
    media = _channel("forum", channel_id=60, position=2)
    media.type = discord.ChannelType.media
    guild = SimpleNamespace(
        me=object(), channels=[forum, media], threads=[], categories=[],
        id=123, name="Test guild", icon=None,
    )
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    result = asyncio.run(bot_module.viewer_get_tree())

    assert [(channel["id"], channel["type"]) for channel in result["channels"]] == [
        ("50", "forum"),
        ("60", "media"),
    ]


def test_viewer_tree_fetches_archived_forum_threads_with_supported_arguments(monkeypatch):
    forum = _channel("forum", channel_id=50, position=1)
    archived = Mock(spec=discord.Thread)
    archived.id = 99
    archived.name = "old-forum-post"
    archived.position = 0
    archived.parent_id = forum.id
    archived.parent = forum
    archived.category_id = None
    archived.archived = True
    archived.archive_timestamp = datetime(2026, 9, 28, tzinfo=timezone.utc)
    archived.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=False)

    async def archived_forum_threads(*, limit=100, before=None):
        assert limit == 101
        assert before is None
        yield archived

    forum.archived_threads.side_effect = archived_forum_threads
    guild = SimpleNamespace(me=object(), channels=[forum])
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    result = asyncio.run(bot_module.viewer_get_archived_threads(50, "public"))

    assert result["threads"] == [{
        "id": "99", "name": "old-forum-post", "position": 0, "can_send": False,
        "category_id": None, "parent_id": "50", "type": "thread", "archived": True,
        "voice_members": [],
    }]
    assert result["before"] == archived.archive_timestamp.isoformat()
    assert result["has_more"] is False


def test_viewer_tree_includes_archived_threads_under_parent(monkeypatch):
    parent = _channel("text", channel_id=10, position=1)
    archived = Mock(spec=discord.Thread)
    archived.id = 99
    archived.name = "old-thread"
    archived.position = 0
    archived.parent_id = parent.id
    archived.parent = parent
    archived.category_id = None
    archived.archived = True
    archived.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=False)

    async def archived_threads(*, private=False, joined=False, limit=100, before=None):
        assert private is False
        assert joined is False
        assert limit == 101
        assert before is None
        yield archived

    parent.archived_threads.side_effect = archived_threads
    guild = SimpleNamespace(me=object(), channels=[parent])
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    result = asyncio.run(bot_module.viewer_get_archived_threads(10, "public"))

    assert result["threads"][0]["id"] == "99"
    assert result["threads"][0]["parent_id"] == "10"
    assert result["threads"][0]["type"] == "thread"
    assert result["threads"][0]["archived"] is True


def test_viewer_tree_fetches_archived_forum_threads_in_bounded_pages(monkeypatch):
    forum = _channel("forum", channel_id=50, position=1)
    archived = []
    for thread_id in range(1000, 1101):
        thread = Mock(spec=discord.Thread)
        thread.id = thread_id
        thread.name = f"old-{thread_id}"
        thread.position = 0
        thread.parent_id = forum.id
        thread.parent = forum
        thread.category_id = None
        thread.archived = True
        thread.archive_timestamp = datetime(2026, 9, 28, tzinfo=timezone.utc)
        thread.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=False)
        archived.append(thread)

    async def archived_forum_threads(*, limit=100, before=None):
        assert limit == 101
        assert before is None
        for thread in archived:
            yield thread

    forum.archived_threads.side_effect = archived_forum_threads
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: SimpleNamespace(me=object(), channels=[forum]))

    result = asyncio.run(bot_module.viewer_get_archived_threads(50, "public"))

    assert len(result["threads"]) == 100
    assert result["threads"][0]["id"] == "1000"
    assert result["threads"][-1]["id"] == "1099"
    assert result["before"] == archived[99].archive_timestamp.isoformat()
    assert result["has_more"] is True


def test_viewer_get_archived_private_page_uses_snowflake_cursor(monkeypatch):
    parent = _channel("text", channel_id=10, position=1)
    archived = Mock(spec=discord.Thread)
    archived.id = 99
    archived.name = "private-thread"
    archived.position = 0
    archived.parent_id = parent.id
    archived.parent = parent
    archived.category_id = None
    archived.archived = True
    archived.archive_timestamp = datetime(2026, 9, 28, tzinfo=timezone.utc)
    archived.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=False)

    async def private_archives(*, private=False, joined=False, limit=100, before=None):
        assert private is True and joined is True and limit == 101
        assert before.id == 42
        yield archived

    parent.archived_threads.side_effect = private_archives
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: SimpleNamespace(me=object(), channels=[parent]))

    result = asyncio.run(bot_module.viewer_get_archived_threads(10, "joined_private", "42"))

    assert result["threads"][0]["id"] == "99"
    assert result["before"] == "99"
    assert result["has_more"] is False


def test_viewer_tree_serializes_current_voice_members(monkeypatch):
    voice = _channel("voice", channel_id=20, position=1)
    member = SimpleNamespace(
        id=601, name="user", display_name="User", bot=False,
        display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
        voice=SimpleNamespace(self_mute=True, self_deaf=False, mute=False, deaf=False, self_video=False, self_stream=False),
    )
    voice.members = [member]
    guild = SimpleNamespace(
        me=object(), channels=[voice], threads=[], categories=[],
        id=123, name="Test guild", icon=None,
    )
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    result = asyncio.run(bot_module.viewer_get_tree())

    assert result["channels"][0]["voice_members"] == [{
        "id": "601", "display_name": "User", "avatar_url": "https://cdn.example/avatar.png",
        "bot": False, "self_mute": True, "self_deaf": False,
        "mute": False, "deaf": False, "video": False, "streaming": False,
    }]


def test_voice_state_event_moves_member_between_subscribers(monkeypatch):
    old_channel = SimpleNamespace(id=20, guild=SimpleNamespace(id=123))
    new_channel = SimpleNamespace(id=30, guild=SimpleNamespace(id=123))
    member = SimpleNamespace(
        id=601, name="user", display_name="User", bot=False, guild=SimpleNamespace(id=123),
        display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
    )
    before = SimpleNamespace(channel=old_channel)
    after = SimpleNamespace(
        channel=new_channel, self_mute=False, self_deaf=False, mute=False,
        deaf=False, self_video=True, self_stream=False,
    )
    published = []
    monkeypatch.setattr(bot_module, "GUILD_ID", 123)
    monkeypatch.setattr(viewer_events, "has_subscribers", lambda channel_id: channel_id in (20, 30))
    monkeypatch.setattr(viewer_events, "publish", lambda channel_id, event: published.append((channel_id, event)))

    asyncio.run(bot_module.on_voice_state_update(member, before, after))

    assert [(channel_id, event["voice"]["connected"]) for channel_id, event in published] == [
        (20, False),
        (30, True),
    ]


def test_voice_state_event_removes_disconnected_member(monkeypatch):
    channel = SimpleNamespace(id=20, guild=SimpleNamespace(id=123))
    member = SimpleNamespace(id=601, guild=SimpleNamespace(id=123))
    published = []
    monkeypatch.setattr(bot_module, "GUILD_ID", 123)
    monkeypatch.setattr(viewer_events, "has_subscribers", lambda channel_id: channel_id == 20)
    monkeypatch.setattr(viewer_events, "publish", lambda channel_id, event: published.append((channel_id, event)))

    asyncio.run(bot_module.on_voice_state_update(member, SimpleNamespace(channel=channel), SimpleNamespace(channel=None)))

    assert published == [(20, {
        "type": "voice_state", "channel_id": "20",
        "voice": {"member_id": "601", "connected": False},
    })]


def test_viewer_event_queue_overflow_emits_resync_and_drops_stale_deltas():
    async def check():
        subscription = viewer_events.ViewerSubscription(20, asyncio.get_running_loop())
        for index in range(viewer_events.MAX_QUEUED_EVENTS + 1):
            subscription.enqueue({"type": "message_create", "message_id": str(index)})
        event = await subscription.next_event()
        assert event == {"type": "resync", "channel_id": "20"}

    asyncio.run(check())


def test_viewer_audit_log_checks_permission_and_serializes_entries(monkeypatch):
    before = SimpleNamespace(name="old")
    after = SimpleNamespace(name="new")
    entry = SimpleNamespace(
        id=987, action=discord.AuditLogAction.channel_update,
        user=SimpleNamespace(id=601, name="moderator"),
        target=SimpleNamespace(id=10, name="general"),
        created_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        reason="cleanup", changes=SimpleNamespace(before=before, after=after),
    )
    async def audit_logs(**kwargs):
        assert kwargs["limit"] == 100
        assert kwargs["user"].id == 601
        assert kwargs["action"].value == discord.AuditLogAction.channel_update.value
        yield entry
    guild = SimpleNamespace(
        me=SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=True)), owner_id=1, audit_logs=audit_logs,
        get_member=lambda _id: None,
    )
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    result = asyncio.run(bot_module.viewer_get_audit_log(limit=999, user_id=601, action_type=discord.AuditLogAction.channel_update.value))

    assert result["entries"] == [{
        "id": "987", "action_type": discord.AuditLogAction.channel_update.value,
        "action": "channel_update", "actor": {"id": "601", "name": "moderator"},
        "target": {"id": "10", "name": "general"},
        "created_at": "2026-09-28T00:00:00+00:00", "reason": "cleanup",
        "changes": [{"key": "name", "old_value": "old", "new_value": "new"}],
    }]


def test_viewer_audit_log_paginates_before_and_clamps(monkeypatch):
    calls = []
    async def audit_logs(**kwargs):
        calls.append(kwargs)
        if False:
            yield None
    guild = SimpleNamespace(me=SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=True)), audit_logs=audit_logs)
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    result = asyncio.run(bot_module.viewer_get_audit_log(before=987, limit=0))

    assert result == {"entries": [], "before": "987", "has_more": False}
    assert calls[0]["limit"] == 1
    assert calls[0]["before"].id == 987


def test_viewer_audit_log_rejects_missing_permission(monkeypatch):
    guild = SimpleNamespace(me=SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=False)), audit_logs=AsyncMock())
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)

    with pytest.raises(PermissionError, match="VIEW_AUDIT_LOG"):
        asyncio.run(bot_module.viewer_get_audit_log())
    guild.audit_logs.assert_not_called()


def test_viewer_messages_reject_channel_from_another_guild(monkeypatch):
    foreign = Mock(spec=discord.TextChannel)
    foreign.id = 700
    foreign.guild = SimpleNamespace(id=999)

    async def history(**_kwargs):
        if False:
            yield None

    foreign.history.side_effect = history
    guild = SimpleNamespace(id=123, me=object(), channels=[], threads=[], categories=[])
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda _channel_id: foreign)

    with pytest.raises(ValueError, match="configured guild"):
        asyncio.run(bot_module.viewer_get_messages(700, None, 50))

    foreign.history.assert_not_called()


def test_viewer_messages_support_around_for_search_result_navigation(monkeypatch):
    channel = Mock(spec=discord.TextChannel)
    channel.id = 10
    async def history(**_kwargs):
        if False:
            yield None
    channel.history.side_effect = history
    monkeypatch.setattr(bot_module, "_viewer_channel", AsyncMock(return_value=channel))

    result = asyncio.run(bot_module.viewer_get_messages(10, None, 50, around=9003))

    assert result == []
    channel.history.assert_called_once()
    assert channel.history.call_args.kwargs["around"].id == 9003


def _install_viewer_message(monkeypatch, channel, message, *, manage_messages=True, member=None):
    guild = SimpleNamespace(id=123, me=object(), channels=[channel], threads=[], categories=[])
    channel.id = 10
    guild.get_channel_or_thread = lambda channel_id: channel if int(channel_id) == channel.id else None
    guild.get_member = lambda member_id: member if member and int(member_id) == member.id else None
    channel.guild = guild
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, manage_messages=manage_messages,
    )
    channel.fetch_message = AsyncMock(return_value=message)
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)
    return guild


def test_serialize_message_includes_reaction_count_and_bot_reaction():
    reaction = SimpleNamespace(emoji="✅", count=3, me=True)
    message = SimpleNamespace(
        id=501, content="hello", author=SimpleNamespace(
            id=601, name="User", display_name="User", bot=False,
            display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
        ), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), edited_at=None,
        attachments=[], embeds=[], reference=None, reactions=[reaction],
    )

    serialized = bot_module._serialize_message(message)

    assert serialized["reactions"] == [{"emoji": "✅", "count": 3, "me": True}]


def test_viewer_toggle_reaction_removes_existing_bot_reaction(monkeypatch):
    message = SimpleNamespace(reactions=[SimpleNamespace(emoji="✅", me=True)])
    message.remove_reaction = AsyncMock()
    channel = Mock(spec=discord.TextChannel)
    _install_viewer_message(monkeypatch, channel, message)
    monkeypatch.setattr(bot_module.bot._connection, "user", SimpleNamespace(id=1))

    result = asyncio.run(bot_module.viewer_toggle_reaction(10, 501, "✅"))

    assert result == {"emoji": "✅", "me": False}
    message.remove_reaction.assert_awaited_once_with(discord.PartialEmoji.from_str("✅"), bot_module.bot.user)


def test_viewer_toggle_reaction_adds_missing_bot_reaction(monkeypatch):
    message = SimpleNamespace(reactions=[])
    message.add_reaction = AsyncMock()
    channel = Mock(spec=discord.TextChannel)
    _install_viewer_message(monkeypatch, channel, message)

    result = asyncio.run(bot_module.viewer_toggle_reaction(10, 501, "✅"))

    assert result == {"emoji": "✅", "me": True}
    message.add_reaction.assert_awaited_once_with(discord.PartialEmoji.from_str("✅"))


def test_viewer_get_reaction_users_returns_snowflake_cursor(monkeypatch):
    reaction = SimpleNamespace(emoji="✅")
    users = [
        SimpleNamespace(id=601, name="a", display_name="A", bot=False, display_avatar=None),
        SimpleNamespace(id=602, name="b", display_name="B", bot=False, display_avatar=None),
    ]

    async def iter_users(*, limit, after):
        assert limit == 2
        assert after.id == 500
        for user in users:
            yield user

    reaction.users = iter_users
    message = SimpleNamespace(reactions=[reaction])
    channel = Mock(spec=discord.TextChannel)
    _install_viewer_message(monkeypatch, channel, message)

    result = asyncio.run(bot_module.viewer_get_reaction_users(10, 501, "✅", after=500, limit=2))

    assert [user["id"] for user in result["users"]] == ["601", "602"]
    assert result["next_after"] == "602"


def test_viewer_remove_other_reaction_requires_manage_messages(monkeypatch):
    message = SimpleNamespace()
    message.remove_reaction = AsyncMock()
    member = SimpleNamespace(id=602)
    channel = Mock(spec=discord.TextChannel)
    _install_viewer_message(monkeypatch, channel, message, manage_messages=False, member=member)

    with pytest.raises(PermissionError, match="MANAGE_MESSAGES"):
        asyncio.run(bot_module.viewer_remove_reaction(10, 501, "✅", 602))

    message.remove_reaction.assert_not_awaited()


def test_viewer_clear_reactions_requires_manage_messages(monkeypatch):
    message = SimpleNamespace()
    message.clear_reactions = AsyncMock()
    channel = Mock(spec=discord.TextChannel)
    _install_viewer_message(monkeypatch, channel, message, manage_messages=False)

    with pytest.raises(PermissionError, match="MANAGE_MESSAGES"):
        asyncio.run(bot_module.viewer_clear_reactions(10, 501))

    message.clear_reactions.assert_not_awaited()


def test_raw_reaction_events_notify_only_open_viewer_channel(monkeypatch):
    payload = SimpleNamespace(guild_id=123, channel_id=10, message_id=501)
    published = []
    monkeypatch.setattr(bot_module, "GUILD_ID", 123)
    monkeypatch.setattr(viewer_events, "has_subscribers", lambda channel_id: channel_id == 10)
    monkeypatch.setattr(viewer_events, "publish", lambda channel_id, event: published.append((channel_id, event)))

    asyncio.run(bot_module.on_raw_reaction_add(payload))
    asyncio.run(bot_module.on_raw_reaction_remove(payload))

    assert published == [(10, {
        "type": "reaction_update", "channel_id": "10", "message_id": "501",
    })] * 2


def _search_message(message_id, content):
    return SimpleNamespace(
        id=message_id, content=content, author=SimpleNamespace(
            id=601, name="User", display_name="User", bot=False,
            display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
        ), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), edited_at=None,
        attachments=[], embeds=[], reference=None, reactions=[], channel=SimpleNamespace(id=10),
    )


def test_viewer_search_builds_discord_search_params_and_caps_page(monkeypatch):
    from discord.http import Route

    guild = SimpleNamespace(id=123)
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)
    response = {"messages": [], "total_results": 0}
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(bot_module.bot.http, "request", request)
    query = {
        "content": "reactor warning", "channel_id": ["10", "11"],
        "author_id": ["601"], "mentions_role_id": ["701"],
        "has": ["file"], "pinned": True, "sort_by": "timestamp", "sort_order": "desc",
    }

    result = asyncio.run(bot_module.viewer_search_messages(query, offset=10000, limit=50))

    route = request.await_args.args[0]
    assert isinstance(route, Route)
    assert route.method == "GET"
    assert route.url.endswith("/guilds/123/messages/search")
    assert request.await_args.kwargs["params"] == [
        ("content", "reactor warning"), ("channel_id", "10"), ("channel_id", "11"),
        ("author_id", "601"), ("mentions_role_id", "701"), ("has", "file"),
        ("pinned", "true"), ("sort_by", "timestamp"), ("sort_order", "desc"),
        ("limit", "25"), ("offset", "9975"),
    ]
    assert result["messages"] == []


def test_viewer_search_flattens_context_arrays_and_returns_message_channel(monkeypatch):
    guild = SimpleNamespace(id=123)
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)
    response = {
        "messages": [[_search_message(501, "one")], [_search_message(502, "two")]],
        "total_results": 2,
    }
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(bot_module.bot.http, "request", request)

    result = asyncio.run(bot_module.viewer_search_messages({"content": "one"}, offset=0, limit=25))

    assert [message["id"] for message in result["messages"]] == ["501", "502"]
    assert [message["channel_id"] for message in result["messages"]] == ["10", "10"]
    assert [message["content"] for message in result["messages"]] == ["one", "two"]
    assert result["total_results"] == 2


def test_viewer_search_returns_indexing_delay_without_unbounded_retry(monkeypatch):
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: SimpleNamespace(id=123))
    request = AsyncMock(return_value={"code": 110000, "retry_after": 2, "messages": []})
    monkeypatch.setattr(bot_module.bot.http, "request", request)

    result = asyncio.run(bot_module.viewer_search_messages({"content": "new text"}, offset=0, limit=25))

    assert result["indexing"] is True
    assert result["retry_after"] == 2
    assert request.await_count == 1


def test_viewer_search_deserializes_discord_http_message_payload(monkeypatch):
    channel = SimpleNamespace(id=10, guild=SimpleNamespace(id=123))
    guild = SimpleNamespace(id=123, get_channel_or_thread=lambda _channel_id: channel)
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)
    raw_message = {
        "id": "501", "channel_id": "10", "content": "matched content",
        "timestamp": "2026-01-01T00:00:00+00:00", "edited_timestamp": None,
        "type": 0, "author": {"id": "601", "username": "User", "avatar": None, "discriminator": "0"},
        "attachments": [], "embeds": [],
    }
    request = AsyncMock(return_value={"messages": [[raw_message]], "total_results": 1})
    monkeypatch.setattr(bot_module.bot.http, "request", request)

    result = asyncio.run(bot_module.viewer_search_messages({"content": "matched"}, offset=0, limit=25))

    assert result["messages"][0]["id"] == "501"
    assert result["messages"][0]["channel_id"] == "10"
    assert result["messages"][0]["author"]["id"] == "601"


def _install_viewer_channels(monkeypatch, channels, *, members=()):
    guild = SimpleNamespace(id=123, me=SimpleNamespace(top_role=SimpleNamespace(position=10)), channels=channels, threads=[], categories=[])
    channel_by_id = {channel.id: channel for channel in channels}
    member_by_id = {member.id: member for member in members}
    guild.get_channel_or_thread = lambda channel_id: channel_by_id.get(int(channel_id))
    guild.get_member = lambda member_id: member_by_id.get(int(member_id))
    for channel in channels:
        channel.guild = guild
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda channel_id: channel_by_id.get(int(channel_id)))
    return guild


def test_viewer_set_channel_access_changes_only_view_bit(monkeypatch):
    channel = Mock(spec=discord.TextChannel)
    channel.id = 10
    member = SimpleNamespace(id=99, top_role=SimpleNamespace(position=5))
    channel.permissions_for.return_value = SimpleNamespace(view_channel=True, manage_roles=True)
    overwrite = discord.PermissionOverwrite(send_messages=True)
    channel.overwrites_for.return_value = overwrite
    channel.set_permissions = AsyncMock()
    _install_viewer_channels(monkeypatch, [channel], members=[member])

    result = asyncio.run(bot_module.viewer_set_channel_member_access(10, 99, "deny"))

    assert result["access"] == "deny"
    assert overwrite.view_channel is False
    assert overwrite.send_messages is True
    channel.set_permissions.assert_awaited_once()


def test_viewer_set_channel_access_uses_manage_roles_without_member_hierarchy_gate(monkeypatch):
    channel = Mock(spec=discord.TextChannel)
    channel.id = 10
    member = SimpleNamespace(id=99, top_role=SimpleNamespace(position=20))
    channel.permissions_for.return_value = SimpleNamespace(view_channel=True, manage_roles=True)
    channel.set_permissions = AsyncMock()
    _install_viewer_channels(monkeypatch, [channel], members=[member])

    result = asyncio.run(bot_module.viewer_set_channel_member_access(10, 99, "allow"))

    assert result["access"] == "allow"
    channel.set_permissions.assert_awaited_once()


def test_viewer_delete_channel_requires_manage_channels_and_reason(monkeypatch):
    channel = Mock(spec=discord.TextChannel)
    channel.id = 10
    channel.name = "text-10"
    channel.permissions_for.return_value = SimpleNamespace(view_channel=True, manage_channels=True)
    channel.delete = AsyncMock()
    _install_viewer_channels(monkeypatch, [channel])

    result = asyncio.run(bot_module.viewer_delete_channel(10, "Requested by admin"))

    assert result == {"deleted": True, "channel_id": "10", "channel_name": "text-10"}
    channel.delete.assert_awaited_once_with(reason="Requested by admin")


def test_viewer_create_invite_requires_native_permission(monkeypatch):
    channel = Mock(spec=discord.TextChannel)
    channel.id = 10
    channel.permissions_for.return_value = SimpleNamespace(view_channel=True, create_instant_invite=False)
    channel.create_invite = AsyncMock()
    _install_viewer_channels(monkeypatch, [channel])

    with pytest.raises(PermissionError, match="CREATE_INSTANT_INVITE"):
        asyncio.run(bot_module.viewer_create_channel_invite(10))

    channel.create_invite.assert_not_awaited()


def test_viewer_move_member_checks_source_and_target_voice_permissions(monkeypatch):
    source = Mock(spec=discord.VoiceChannel)
    source.id = 20
    source.permissions_for.return_value = SimpleNamespace(view_channel=True, move_members=True)
    target = Mock(spec=discord.VoiceChannel)
    target.id = 30
    target.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True)
    member = SimpleNamespace(id=99, voice=SimpleNamespace(channel=source), move_to=AsyncMock())
    source.guild = target.guild = SimpleNamespace(id=123)
    guild = _install_viewer_channels(monkeypatch, [source, target], members=[member])
    guild.me.guild_permissions = SimpleNamespace(move_members=True)

    result = asyncio.run(bot_module.viewer_move_member(99, 30))

    assert result == {"member_id": "99", "channel_id": "30"}
    member.move_to.assert_awaited_once_with(target, reason="Перенос участника через Discord-вьюер")


def test_viewer_message_operations_support_vocal_channels(monkeypatch):
    voice = Mock(spec=discord.VoiceChannel)
    voice.guild = SimpleNamespace(id=123)
    voice.permissions_for.return_value = SimpleNamespace(view_channel=True)
    message = SimpleNamespace(
        id=501,
        content="voice chat message",
        author=SimpleNamespace(
            id=601,
            name="User",
            display_name="User",
            bot=False,
            display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
        ),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        edited_at=None,
        attachments=[],
        embeds=[],
        reference=None,
    )

    async def history(*_args, **_kwargs):
        yield message

    async def send(**_kwargs):
        return message

    voice.history.side_effect = history
    voice.send.side_effect = send
    guild = SimpleNamespace(id=123, me=object(), channels=[voice], threads=[], categories=[])
    monkeypatch.setattr(bot_module, "_viewer_guild", lambda: guild)
    monkeypatch.setattr(bot_module.bot, "get_channel", lambda _channel_id: voice)

    messages = asyncio.run(bot_module.viewer_get_messages(20, None, 50))
    sent = asyncio.run(bot_module.viewer_send_message(20, "hello", "hello.txt", b"file"))
    exported = asyncio.run(bot_module.viewer_export_messages(20, 50))

    assert messages[0]["id"] == "501"
    assert sent["content"] == "voice chat message"
    assert exported[0]["author"]["display_name"] == "User"
    voice.send.assert_awaited_once()
