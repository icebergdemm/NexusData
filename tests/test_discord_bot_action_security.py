from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_db
from routes import discord_bot_api


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
