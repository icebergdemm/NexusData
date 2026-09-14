from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models import (
    AdminActionLog,
    AdminUser,
    DiscordMember,
    PlayerData,
    PlayerSession,
    Ticket,
    TicketMessage,
)
from routes import tickets_api
from routes.tickets_api import panel_router


@pytest.fixture
def player_export_api(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            AdminActionLog.__table__,
            AdminUser.__table__,
            DiscordMember.__table__,
            PlayerData.__table__,
            PlayerSession.__table__,
            Ticket.__table__,
            TicketMessage.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    current_admin = SimpleNamespace(id=1, is_superuser=True)

    with session_factory() as db:
        db.add_all([
            AdminUser(id=1, discord_id="admin-1", discord_username="Super", is_superuser=True),
            PlayerData(
                player_id=1195,
                player_last_time_name="Гаммас Паллоройз",
                player_steamid="76561198000000001",
                discord_id="player-1195",
            ),
            PlayerData(
                player_id=2000,
                player_last_time_name="Другой игрок",
                player_steamid="76561198000000002",
                discord_id="player-2000",
            ),
            DiscordMember(
                discord_id="42",
                username="reporter",
                display_name="Reporter",
            ),
            Ticket(
                id=10,
                ticket_type="appeal",
                title="Удалённое обжалование",
                status="closed",
                creator_discord_id="42",
                creator_username="reporter",
                player_id=1195,
                deleted_at=datetime(2026, 9, 1, 10),
                deleted_by="Moderator",
                created_at=datetime(2026, 9, 1, 9),
            ),
            Ticket(
                id=11,
                ticket_type="player_report",
                title="Не тот игрок",
                status="open",
                creator_discord_id="99",
                player_id=2000,
                created_at=datetime(2026, 9, 2, 9),
            ),
            TicketMessage(
                id=100,
                ticket_id=10,
                author_discord_id="42",
                author_username="reporter",
                author_display_name="Reporter",
                content="Полный текст обращения",
                attachments=[{
                    "url": "https://cdn.discordapp.com/evidence.png",
                    "filename": "evidence.png",
                    "content_type": "image/png",
                    "file_size": 12,
                }],
                reference_snapshot={
                    "kind": "reply",
                    "content": "Цитата",
                    "embeds": [{"title": "Evidence"}],
                },
                created_at=datetime(2026, 9, 1, 9, 1),
            ),
            TicketMessage(
                id=101,
                ticket_id=10,
                author_discord_id="42",
                content="Второе сообщение",
                created_at=datetime(2026, 9, 1, 9, 2),
            ),
        ])
        db.commit()

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(panel_router)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        tickets_api,
        "_get_admin",
        lambda _request, _db: current_admin,
    )

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            current_admin=current_admin,
            session_factory=session_factory,
        )
    engine.dispose()


def test_player_export_requires_superuser(player_export_api):
    player_export_api.current_admin.is_superuser = False

    response = player_export_api.client.get("/api/v1/admin/players/1195/export-data")

    assert response.status_code == 403


def test_player_export_includes_full_matching_archive_and_audit(player_export_api):
    response = player_export_api.client.get("/api/v1/admin/players/1195/export-data")

    assert response.status_code == 200
    payload = response.json()
    assert payload["player_id"] == 1195
    assert payload["ticket_count"] == 1
    assert [row["ticket"]["id"] for row in payload["tickets"]] == [10]
    assert payload["tickets"][0]["ticket"]["deleted_at"] == "2026-09-01T10:00:00"
    assert [message["id"] for message in payload["tickets"][0]["messages"]] == [100, 101]
    assert payload["tickets"][0]["messages"][0]["attachments"][0]["filename"] == "evidence.png"
    assert payload["tickets"][0]["messages"][0]["reference_snapshot"]["content"] == "Цитата"
    assert payload["tickets"][0]["archive"]["attachment_count"] == 1
    assert payload["tickets"][0]["participants"][0]["discord_id"] == "42"

    with player_export_api.session_factory() as db:
        audit = db.query(AdminActionLog).one()
        assert audit.action == "export_player_personal_file"
        assert audit.details == {
            "player_id": 1195,
            "ticket_count": 1,
            "includes_deleted": True,
        }


def test_player_export_returns_not_found_without_touching_discord(player_export_api, monkeypatch):
    def fail(*_args, **_kwargs):
        pytest.fail("player export must use only the stored NexusData archive")

    monkeypatch.setattr(tickets_api, "_ticket_participants", fail)

    response = player_export_api.client.get("/api/v1/admin/players/9999/export-data")

    assert response.status_code == 404


def test_player_export_does_not_include_unrelated_tickets(player_export_api):
    response = player_export_api.client.get("/api/v1/admin/players/1195/export-data")

    assert all(row["ticket"]["player_id"] == 1195 for row in response.json()["tickets"])
