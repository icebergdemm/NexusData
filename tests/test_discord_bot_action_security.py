from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_db
from routes import discord_bot_api, discord_viewer_api


def _client(monkeypatch):
    app = FastAPI()
    app.include_router(discord_bot_api.panel_router)

    def override_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        discord_bot_api,
        "_get_admin",
        lambda _request, _db: SimpleNamespace(id=7, is_superuser=False),
    )
    monkeypatch.setattr(discord_bot_api, "check_permission", lambda *_args: True)
    return TestClient(app)


def test_broadcast_api_rejects_internal_ticket_actions(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/v1/admin/discord-bot/action",
            json={
                "action_type": "ticket_close_channel",
                "payload": {"ticket_id": 1, "channel_id": "2"},
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported broadcast action_type"

def test_legacy_broadcast_form_rejects_internal_ticket_actions(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.post(
            "/adminold/discord-bot/action",
            data={
                "action_type": "ticket_send_message",
                "payload_json": '{"ticket_id": 1, "channel_id": "2"}',
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported broadcast action_type"


def _viewer_client(monkeypatch, *, allowed_actions, patch_bot_bridge=True):
    app = FastAPI()
    app.include_router(discord_viewer_api.router)

    def override_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(discord_viewer_api, "_get_admin", lambda _request, _db: SimpleNamespace(id=7))
    monkeypatch.setattr(
        discord_viewer_api,
        "check_permission",
        lambda _admin, _page, action: action in allowed_actions,
    )

    if patch_bot_bridge:
        async def call_bot(coro, **_kwargs):
            return await coro

        monkeypatch.setattr(discord_viewer_api, "_call_bot", call_bot)
    return TestClient(app)


def test_viewer_archived_threads_requires_view_permission(monkeypatch):
    with _viewer_client(monkeypatch, allowed_actions=set()) as client:
        response = client.get("/api/v1/admin/discord-bot/viewer/channels/10/archived-threads?source=public")

    assert response.status_code == 403


def test_viewer_archived_threads_forwards_cursor_and_uses_longer_timeout(monkeypatch):
    from discord_bot import bot as bot_module

    operation = Mock()

    async def archives(channel_id, source, before):
        operation(channel_id, source, before)
        return {"threads": [], "before": None, "has_more": False}

    monkeypatch.setattr(bot_module, "viewer_get_archived_threads", archives)
    bridge_kwargs = {}

    async def passthrough(coro, **kwargs):
        bridge_kwargs.update(kwargs)
        return await coro

    with _viewer_client(monkeypatch, allowed_actions={"view"}, patch_bot_bridge=False) as client:
        monkeypatch.setattr(discord_viewer_api, "_call_bot", passthrough)
        response = client.get(
            "/api/v1/admin/discord-bot/viewer/channels/10/archived-threads?source=joined_private&before=42",
        )

    assert response.status_code == 200
    operation.assert_called_once_with(10, "joined_private", "42")
    assert bridge_kwargs == {"timeout": 30.0}


@pytest.mark.parametrize("query", ["source=invalid", "source=joined_private&before=nope", "source=public&before=nope"])
def test_viewer_archived_threads_rejects_invalid_source_or_cursor(monkeypatch, query):
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        response = client.get(f"/api/v1/admin/discord-bot/viewer/channels/10/archived-threads?{query}")

    assert response.status_code == 400


def test_viewer_reaction_mutation_requires_manage_config(monkeypatch):
    from discord_bot import bot as bot_module

    toggle = Mock(return_value=None)
    monkeypatch.setattr(bot_module, "viewer_toggle_reaction", toggle)
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        response = client.post(
            "/api/v1/admin/discord-bot/viewer/channels/10/messages/20/reactions/toggle",
            json={"emoji": "✅"},
        )

    assert response.status_code == 403
    toggle.assert_not_called()


def test_viewer_reaction_users_requires_view_permission(monkeypatch):
    with _viewer_client(monkeypatch, allowed_actions=set()) as client:
        response = client.get(
            "/api/v1/admin/discord-bot/viewer/channels/10/messages/20/reactions/%E2%9C%85/users",
        )

    assert response.status_code == 403


def test_viewer_search_requires_view_permission(monkeypatch):
    with _viewer_client(monkeypatch, allowed_actions=set()) as client:
        response = client.get("/api/v1/admin/discord-bot/viewer/search?query=hello")

    assert response.status_code == 403


def test_viewer_search_maps_native_filters_and_page(monkeypatch):
    from discord_bot import bot as bot_module

    search = Mock()

    async def search_impl(query, *, offset, limit):
        search(query, offset=offset, limit=limit)
        return {"messages": [], "total_results": 0, "offset": offset, "has_more": False, "indexing": False, "retry_after": None}

    monkeypatch.setattr(bot_module, "viewer_search_messages", search_impl)
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        response = client.get(
            "/api/v1/admin/discord-bot/viewer/search?query=reactor&channel_id=10&channel_id=11&author_id=601&has=file&pinned=true&offset=25&limit=10",
        )

    assert response.status_code == 200
    search.assert_called_once_with({
        "content": "reactor", "channel_id": ["10", "11"], "author_id": ["601"],
        "has": ["file"], "pinned": True,
    }, offset=25, limit=10)


def test_viewer_search_accepts_native_filters_without_text(monkeypatch):
    from discord_bot import bot as bot_module

    search = Mock()

    async def search_impl(query, *, offset, limit):
        search(query, offset=offset, limit=limit)
        return {"messages": [], "total_results": 0, "offset": offset, "has_more": False, "indexing": False, "retry_after": None}

    monkeypatch.setattr(bot_module, "viewer_search_messages", search_impl)
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        response = client.get(
            "/api/v1/admin/discord-bot/viewer/search?mention_everyone=true&author_type=bot&embed_type=video&link_hostname=discord.com",
        )

    assert response.status_code == 200
    search.assert_called_once_with({
        "mention_everyone": True, "author_type": ["bot"], "embed_type": ["video"],
        "link_hostname": ["discord.com"],
    }, offset=0, limit=25)


def test_viewer_search_exposes_discord_retry_after(monkeypatch):
    from discord_bot import bot as bot_module

    future = Future()
    future.set_exception(discord.RateLimited(1.25))
    def schedule(coro):
        coro.close()
        return future

    monkeypatch.setattr(bot_module, "schedule", schedule)

    with _viewer_client(monkeypatch, allowed_actions={"view"}, patch_bot_bridge=False) as client:
        response = client.get("/api/v1/admin/discord-bot/viewer/search?query=hello")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1.25"


def test_viewer_channel_access_mutation_requires_manage_config(monkeypatch):
    from discord_bot import bot as bot_module

    operation = Mock()
    monkeypatch.setattr(bot_module, "viewer_set_channel_member_access", operation)
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        response = client.put(
            "/api/v1/admin/discord-bot/viewer/channels/10/members/99/access",
            json={"access": "allow"},
        )

    assert response.status_code == 403
    operation.assert_not_called()


def test_viewer_channel_delete_requires_manage_config(monkeypatch):
    from discord_bot import bot as bot_module

    operation = Mock()
    monkeypatch.setattr(bot_module, "viewer_delete_channel", operation)
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        response = client.request(
            "DELETE", "/api/v1/admin/discord-bot/viewer/channels/10", json={"reason": "remove"},
        )

    assert response.status_code == 403
    operation.assert_not_called()


def test_viewer_voice_move_requires_manage_config(monkeypatch):
    from discord_bot import bot as bot_module

    operation = Mock()
    monkeypatch.setattr(bot_module, "viewer_move_member", operation)
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        response = client.post(
            "/api/v1/admin/discord-bot/viewer/members/99/voice",
            json={"target_channel_id": "30"},
        )

    assert response.status_code == 403
    operation.assert_not_called()


def test_viewer_audit_log_requires_manage_config(monkeypatch):
    from discord_bot import bot as bot_module

    operation = Mock()
    monkeypatch.setattr(bot_module, "viewer_get_audit_log", operation)
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        response = client.get("/api/v1/admin/discord-bot/viewer/audit-log")

    assert response.status_code == 403
    operation.assert_not_called()


def test_viewer_audit_log_forwards_pagination_and_filters(monkeypatch):
    from discord_bot import bot as bot_module

    operation = Mock()
    async def audit_log_impl(**kwargs):
        operation(**kwargs)
        return {"entries": [], "before": "123", "has_more": False}
    monkeypatch.setattr(bot_module, "viewer_get_audit_log", audit_log_impl)
    with _viewer_client(monkeypatch, allowed_actions={"manage_config"}) as client:
        response = client.get("/api/v1/admin/discord-bot/viewer/audit-log?before=123&limit=45&user_id=601&action_type=11")

    assert response.status_code == 200
    operation.assert_called_once_with(before=123, limit=45, user_id=601, action_type=11)


@pytest.mark.parametrize("method,path,body", [
    ("put", "/api/v1/admin/discord-bot/viewer/channels/10/messages/20/pin", {"pinned": True}),
    ("request", "/api/v1/admin/discord-bot/viewer/channels/10/messages/20", {"reason": "cleanup"}),
    ("post", "/api/v1/admin/discord-bot/viewer/channels/10/messages/20/thread", {"name": "follow-up"}),
])
def test_viewer_message_mutations_require_manage_config(monkeypatch, method, path, body):
    from discord_bot import bot as bot_module

    operations = ["viewer_set_message_pinned", "viewer_delete_message", "viewer_create_message_thread"]
    for name in operations:
        monkeypatch.setattr(bot_module, name, Mock())
    with _viewer_client(monkeypatch, allowed_actions={"view"}) as client:
        if method == "request":
            response = client.request("DELETE", path, json=body)
        else:
            response = getattr(client, method)(path, json=body)

    assert response.status_code == 403
    for name in operations:
        getattr(bot_module, name).assert_not_called()
