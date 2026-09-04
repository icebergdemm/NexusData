from __future__ import annotations

import asyncio
import io
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import FormData, Headers, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import config
from database import Base, get_db
from models import AdminUser, DiscordMember, PlayerData, Ticket, TicketMessage
from routes import tickets_api
from routes.tickets_api import (
    _admin_avatar,
    _archive_summary,
    _close_uploads,
    _content_disposition,
    _read_send_form,
    _serialize_message,
    _serialize_player,
    _serialize_ticket,
    _ticket_mention_profiles,
    _ticket_participants,
    panel_router,
)
from services_ticket_media import storage_path


def _message(**overrides):
    values = {
        "id": 19,
        "ticket_id": 7,
        "discord_message_id": "1234567890",
        "author_discord_id": "42",
        "author_username": "reporter",
        "author_display_name": "Reporter",
        "author_avatar_url": None,
        "content": "Evidence",
        "attachments": [],
        "reference_snapshot": None,
        "is_bot": 0,
        "delivery_status": "received",
        "delivery_error": None,
        "created_at": datetime(2026, 9, 3, 12, 30),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_archive_summary_counts_direct_and_forwarded_media_and_bytes():
    messages = [
        _message(
            attachments=[
                {"storage_key": "direct.png", "archive_status": "stored", "file_size": 11},
            ],
            reference_snapshot={
                "kind": "forward",
                "attachments": [
                    {"storage_key": "forward.mp4", "archive_status": "stored", "size": "29"},
                ],
            },
        )
    ]

    assert _archive_summary(messages) == {
        "state": "complete",
        "attachment_count": 2,
        "stored_count": 2,
        "total_bytes": 40,
        "failed_count": 0,
        "legacy_count": 0,
    }


def test_archive_summary_pending_takes_precedence_and_bad_size_is_ignored():
    summary = _archive_summary(
        [
            _message(
                attachments=[
                    {"storage_key": "done.png", "archive_status": "stored", "file_size": 10},
                    {"archive_status": "failed", "file_size": "not-a-number"},
                    {"archive_status": "pending", "size": 5},
                ]
            )
        ]
    )

    assert summary["state"] == "pending"
    assert summary["attachment_count"] == 3
    assert summary["stored_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["legacy_count"] == 0
    assert summary["total_bytes"] == 15


def test_archive_summary_distinguishes_legacy_only_archive():
    summary = _archive_summary(
        [_message(attachments=[{"url": "https://cdn.discordapp.com/legacy.png", "size": 4}])]
    )

    assert summary["state"] == "legacy"
    assert summary["legacy_count"] == 1


def test_serialize_message_builds_stable_urls_for_direct_and_forwarded_attachments():
    serialized = _serialize_message(
        _message(
            attachments=[
                {
                    "storage_key": "direct.png",
                    "storage_namespace": "discord-123",
                    "filename": "direct.png",
                    "content_type": "image/png",
                    "archive_status": "stored",
                }
            ],
            reference_snapshot={
                "kind": "forward",
                "content": "Forwarded evidence",
                "attachments": [
                    {
                        "storage_key": "forwarded.mp4",
                        "storage_namespace": "forward-456",
                        "filename": "forwarded.mp4",
                        "content_type": "video/mp4",
                        "archive_status": "stored",
                    }
                ],
            },
        )
    )

    assert serialized["attachments"][0]["url"] == (
        "/api/v1/admin/tickets/7/messages/19/media/direct.png"
    )
    assert serialized["attachments"][0]["archived"] is True
    assert serialized["reference_snapshot"]["attachments"][0]["url"] == (
        "/api/v1/admin/tickets/7/messages/19/media/forwarded.mp4"
    )
    assert serialized["reference_snapshot"]["attachments"][0]["archived"] is True


def test_serialize_message_normalizes_discord_forward_snapshot_envelope():
    serialized = _serialize_message(
        _message(
            reference_snapshot={
                "type": "forward",
                "source": {
                    "guild_id": "1",
                    "channel_id": "2",
                    "message_id": "3",
                    "channel_name": "evidence",
                    "author": {"id": "44", "display_name": "Witness"},
                },
                "message_snapshots": [{
                    "message": {
                        "content": "Original immutable content",
                        "created_at": "2026-09-03T12:00:00+00:00",
                        "attachments": [{
                            "storage_key": "forward.png",
                            "storage_namespace": "msg-1-fwd-0",
                            "filename": "forward.png",
                            "archive_status": "stored",
                        }],
                        "embeds": [{
                            "title": "Incident",
                            "image": {"url": "https://example.invalid/frame.png"},
                        }],
                    },
                }],
            },
        )
    )

    reference = serialized["reference_snapshot"]
    assert reference["kind"] == "forward"
    assert reference["content"] == "Original immutable content"
    assert reference["author"]["display_name"] == "Witness"
    assert reference["source"]["jump_url"] == "https://discord.com/channels/1/2/3"
    assert reference["attachments"][0]["url"].endswith("/media/forward.png")
    assert reference["embeds"][0]["image_url"] == "https://example.invalid/frame.png"


def test_archive_summary_counts_nested_discord_forward_attachments():
    summary = _archive_summary([
        _message(reference_snapshot={
            "type": "forward",
            "message_snapshots": [{"message": {"attachments": [
                {"storage_key": "forward.mp4", "archive_status": "stored", "file_size": 99}
            ]}}],
        })
    ])

    assert summary["state"] == "complete"
    assert summary["attachment_count"] == 1
    assert summary["stored_count"] == 1
    assert summary["total_bytes"] == 99


def test_serialize_message_preserves_legacy_string_attachment_url():
    legacy_url = "https://cdn.discordapp.com/attachments/old/evidence.png"

    serialized = _serialize_message(_message(attachments=[legacy_url]))

    assert serialized["attachments"][0]["url"] == legacy_url
    assert serialized["attachments"][0]["archived"] is False


def test_secret_player_is_redacted_without_special_permission(monkeypatch):
    admin = SimpleNamespace(is_superuser=False)
    player = SimpleNamespace(is_secret=1)
    monkeypatch.setattr(tickets_api, "check_permission", lambda *_args: False)

    assert _serialize_player(admin, player) is None


def test_secret_player_is_visible_to_players_special_viewer(monkeypatch):
    admin = SimpleNamespace(is_superuser=False)
    player = SimpleNamespace(
        is_secret=1,
        player_id=31,
        player_last_time_name="Classified",
        forced_display_name=None,
        player_steamid="76561198000000031",
        steam_avatar_url=None,
        last_login_time=None,
    )
    monkeypatch.setattr(
        tickets_api,
        "check_permission",
        lambda _admin, page, action: page == "players_special" and action == "view",
    )

    serialized = _serialize_player(admin, player, online=True)

    assert serialized["player_id"] == 31
    assert serialized["player_steamid"] == "76561198000000031"
    assert serialized["online"] is True


def test_ticket_serializer_redacts_secret_player_foreign_key():
    ticket = Ticket(
        id=31,
        ticket_type="appeal",
        title="Classified",
        status="open",
        player_id=99,
    )

    serialized = _serialize_ticket(ticket, can_view_player=False)

    assert serialized["player_id"] is None


def test_ticket_mention_profiles_resolve_linked_player_and_nested_forward():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[AdminUser.__table__, PlayerData.__table__, DiscordMember.__table__],
    )
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        db.add_all([
            DiscordMember(
                discord_id="987047045498011658",
                username="volkov",
                display_name="Мичман Дмитрий Волков",
                avatar="https://cdn.discordapp.com/avatar.png",
            ),
            DiscordMember(
                discord_id="123456789012345678",
                username="witness",
                display_name="Свидетель",
            ),
            PlayerData(
                player_id=17,
                player_last_time_name="Дмитрий Волков",
                player_steamid="76561198000000017",
                discord_id="987047045498011658",
            ),
        ])
        db.commit()

        profiles = _ticket_mention_profiles(
            db,
            SimpleNamespace(is_superuser=True),
            [
                _message(
                    content="<@987047045498011658> проверьте доказательства",
                    reference_snapshot={
                        "message_snapshots": [{
                            "message": {"content": "Ответил <@!123456789012345678>"}
                        }],
                    },
                )
            ],
        )

    assert list(profiles) == ["987047045498011658", "123456789012345678"]
    assert profiles["987047045498011658"] == {
        "discord_id": "987047045498011658",
        "username": "volkov",
        "display_name": "Мичман Дмитрий Волков",
        "avatar_url": "https://cdn.discordapp.com/avatar.png",
        "player_id": 17,
        "profile_url": "/players/17",
        "is_staff": False,
    }
    assert profiles["123456789012345678"]["display_name"] == "Свидетель"
    assert profiles["123456789012345678"]["player_id"] is None


def test_ticket_mention_profiles_resolve_player_linked_through_nexus_account():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[AdminUser.__table__, PlayerData.__table__, DiscordMember.__table__],
    )
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        account = AdminUser(
            id=44,
            discord_id="987047045498011658",
            discord_username="Сергей Аверин",
            is_deleted=0,
            is_superuser=0,
        )
        db.add(account)
        db.flush()
        db.add(PlayerData(
            player_id=71,
            player_last_time_name="Сергей Аверин",
            player_steamid="76561198000000071",
            discord_id=None,
            linked_admin_id=account.id,
        ))
        db.commit()

        profiles = _ticket_mention_profiles(
            db,
            SimpleNamespace(is_superuser=True),
            [_message(content="Покажите <@987047045498011658> профиль")],
        )

    assert profiles["987047045498011658"]["player_id"] == 71
    assert profiles["987047045498011658"]["profile_url"] == "/players/71"
    assert profiles["987047045498011658"]["is_staff"] is True
    engine.dispose()


def test_admin_avatar_preserves_animated_discord_hash():
    admin = SimpleNamespace(
        discord_id="987047045498011658",
        discord_avatar="a_animatedhash",
    )

    assert _admin_avatar(admin) == (
        "https://cdn.discordapp.com/avatars/987047045498011658/"
        "a_animatedhash.gif?size=128"
    )


def test_admin_avatar_uses_png_for_static_discord_hash():
    admin = SimpleNamespace(
        discord_id="987047045498011658",
        discord_avatar="statichash",
    )

    assert _admin_avatar(admin).endswith("/statichash.png?size=128")


def test_ticket_participants_use_synced_discord_avatar_as_fallback():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            AdminUser.__table__,
            PlayerData.__table__,
            DiscordMember.__table__,
            Ticket.__table__,
            TicketMessage.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    ticket = Ticket(
        id=7,
        ticket_type="appeal",
        title="Appeal",
        status="open",
        creator_discord_id="987047045498011658",
    )
    message = TicketMessage(
        id=19,
        ticket_id=7,
        author_discord_id="987047045498011658",
        content="Evidence",
    )

    with session_factory() as db:
        db.add(
            DiscordMember(
                discord_id="987047045498011658",
                username="volkov",
                display_name="Мичман Дмитрий Волков",
                avatar="https://cdn.discordapp.com/avatar.png",
            )
        )
        db.commit()
        participants = _ticket_participants(
            db,
            SimpleNamespace(is_superuser=True),
            ticket,
            [message],
        )

    assert participants[0]["display_name"] == "Мичман Дмитрий Волков"
    assert participants[0]["avatar_url"] == "https://cdn.discordapp.com/avatar.png"


def test_content_disposition_supports_inline_unicode_without_header_injection():
    header = _content_disposition('доказ "кадр"\r\n.png', download=False)

    assert header.startswith('inline; filename="')
    assert "\r" not in header and "\n" not in header
    assert "filename*=UTF-8''" in header
    assert "%D0%B4%D0%BE%D0%BA%D0%B0%D0%B7" in header
    assert "%0D%0A" in header


def test_content_disposition_can_force_download():
    assert _content_disposition("evidence.svg", download=True).startswith("attachment;")


def test_ticket_multipart_rejects_unexpected_file_field_and_closes_spool(
    monkeypatch,
):
    upload = UploadFile(filename="proof.png", file=io.BytesIO(b"evidence"))
    form = FormData([("content", "proof"), ("unexpected", upload)])

    async def fake_parser(*_args, **_kwargs):
        return form

    class FakeRequest:
        headers = Headers({"content-type": "multipart/form-data; boundary=x"})

        async def stream(self):
            if False:
                yield b""

    monkeypatch.setattr(tickets_api, "parse_bounded_multipart", fake_parser)
    monkeypatch.setattr(
        tickets_api,
        "settings",
        SimpleNamespace(
            ticket_attachment_message_max_bytes=100,
            ticket_web_upload_max_bytes=100,
            ticket_web_upload_max_files=2,
        ),
    )

    async def run():
        with pytest.raises(HTTPException) as captured:
            await _read_send_form(FakeRequest())
        assert captured.value.status_code == 400

    asyncio.run(run())
    assert upload.file.closed


def test_close_uploads_releases_all_spooled_files():
    uploads = [
        UploadFile(filename="one.bin", file=io.BytesIO(b"one")),
        UploadFile(filename="two.bin", file=io.BytesIO(b"two")),
    ]

    asyncio.run(_close_uploads(uploads))

    assert all(upload.file.closed for upload in uploads)


def test_ticket_json_body_limit_rejects_declared_oversize_before_streaming():
    class FakeRequest:
        headers = Headers({
            "content-type": "application/json",
            "content-length": str(32 * 1024 + 1),
        })
        consumed = False

        async def stream(self):
            self.consumed = True
            yield b'{}'

    request = FakeRequest()

    async def run():
        with pytest.raises(HTTPException) as captured:
            await _read_send_form(request)
        assert captured.value.status_code == 413

    asyncio.run(run())
    assert request.consumed is False


def test_ticket_json_body_limit_rejects_chunked_oversize():
    class FakeRequest:
        headers = Headers({"content-type": "application/json"})

        async def stream(self):
            yield b'{"content":"'
            yield b"x" * (32 * 1024)
            yield b'"}'

    async def run():
        with pytest.raises(HTTPException) as captured:
            await _read_send_form(FakeRequest())
        assert captured.value.status_code == 413

    asyncio.run(run())


@pytest.fixture
def ticket_media_api(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[Ticket.__table__, TicketMessage.__table__],
    )
    session_factory = sessionmaker(bind=engine)

    storage_root = tmp_path / "ticket-media"
    namespace = "discord-123"
    storage_key = "proof.mp4"
    payload = bytes(range(32))
    archived_path = storage_path(7, namespace, storage_key, root=storage_root)
    archived_path.parent.mkdir(parents=True)
    archived_path.write_bytes(payload)

    with session_factory() as db:
        db.add(
            Ticket(
                id=7,
                ticket_type="appeal",
                title="Appeal",
                status="open",
                creator_discord_id="42",
            )
        )
        db.add(
            TicketMessage(
                id=19,
                ticket_id=7,
                author_discord_id="42",
                content="Evidence",
                attachments=[
                    {
                        "storage_key": storage_key,
                        "storage_namespace": namespace,
                        "filename": "proof.mp4",
                        "content_type": "video/mp4",
                        "file_size": len(payload),
                        "archive_status": "stored",
                    }
                ],
            )
        )
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
        config,
        "get_settings",
        lambda: SimpleNamespace(ticket_attachments_dir=str(storage_root)),
    )
    monkeypatch.setattr(
        tickets_api,
        "_get_admin",
        lambda _request, _db: SimpleNamespace(is_superuser=True),
    )
    monkeypatch.setattr(tickets_api, "check_permission", lambda *_args: True)

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            payload=payload,
            url=f"/api/v1/admin/tickets/7/messages/19/media/{storage_key}",
            session_factory=session_factory,
        )
    engine.dispose()


def test_api_ticket_media_serves_full_private_inline_file(ticket_media_api):
    response = ticket_media_api.client.get(ticket_media_api.url)

    assert response.status_code == 200
    assert response.content == ticket_media_api.payload
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-length"] == str(len(ticket_media_api.payload))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")


@pytest.mark.parametrize(
    ("range_header", "expected_range", "expected"),
    [
        ("bytes=2-5", "bytes 2-5/32", bytes(range(2, 6))),
        ("bytes=28-", "bytes 28-31/32", bytes(range(28, 32))),
        ("bytes=-3", "bytes 29-31/32", bytes(range(29, 32))),
        ("bytes=30-100", "bytes 30-31/32", bytes(range(30, 32))),
    ],
)
def test_api_ticket_media_serves_single_byte_ranges(
    ticket_media_api, range_header, expected_range, expected
):
    response = ticket_media_api.client.get(
        ticket_media_api.url,
        headers={"Range": range_header},
    )

    assert response.status_code == 206
    assert response.content == expected
    assert response.headers["content-range"] == expected_range
    assert response.headers["content-length"] == str(len(expected))
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize(
    "range_header",
    ["bytes=32-", "bytes=8-3", "items=0-2", "bytes=0-1,4-5", "bytes=-0", "nonsense"],
)
def test_api_ticket_media_returns_416_for_invalid_or_unsatisfiable_range(
    ticket_media_api, range_header
):
    response = ticket_media_api.client.get(
        ticket_media_api.url,
        headers={"Range": range_header},
    )

    assert response.status_code == 416
    assert response.content == b""
    assert response.headers["content-range"] == "bytes */32"


def test_api_ticket_media_can_force_download(ticket_media_api):
    response = ticket_media_api.client.get(f"{ticket_media_api.url}?download=true")

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")


def test_api_ticket_media_requires_authenticated_admin(ticket_media_api, monkeypatch):
    def reject(_request, _db):
        raise HTTPException(status_code=401, detail="Not authenticated")

    monkeypatch.setattr(tickets_api, "_get_admin", reject)

    response = ticket_media_api.client.get(ticket_media_api.url)

    assert response.status_code == 401


def test_api_ticket_media_requires_ticket_view_permission(ticket_media_api, monkeypatch):
    monkeypatch.setattr(tickets_api, "check_permission", lambda *_args: False)

    response = ticket_media_api.client.get(ticket_media_api.url)

    assert response.status_code == 403


def test_ticket_message_updates_are_incremental_and_use_stable_media_urls(
    ticket_media_api,
):
    response = ticket_media_api.client.get(
        "/api/v1/admin/tickets/7/messages?after_id=18"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_more"] is False
    assert [message["id"] for message in payload["messages"]] == [19]
    assert payload["delivery_updates"] == [{
        "id": 19,
        "delivery_status": "received",
        "delivery_error": None,
    }]
    assert payload["ticket_update"] == {
        "status": "open",
        "archive_state": None,
        "closed_by": None,
        "closed_at": None,
        "last_message_at": None,
        "awaiting_admin": True,
    }
    assert payload["messages"][0]["attachments"][0]["url"] == ticket_media_api.url

    caught_up = ticket_media_api.client.get(
        "/api/v1/admin/tickets/7/messages?after_id=19"
    )
    assert caught_up.status_code == 200
    assert caught_up.json() == {
        "messages": [],
        "delivery_updates": [{
            "id": 19,
            "delivery_status": "received",
            "delivery_error": None,
        }],
        "ticket_update": {
            "status": "open",
            "archive_state": None,
            "closed_by": None,
            "closed_at": None,
            "last_message_at": None,
            "awaiting_admin": True,
        },
        "has_more": False,
    }


def test_legacy_ticket_poll_uses_protected_archived_media_url(ticket_media_api):
    response = ticket_media_api.client.get(
        "/adminold/tickets/7/messages-json?after_id=18"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == 19
    assert payload[0]["attachments"][0]["url"] == ticket_media_api.url
    assert "storage_namespace" not in payload[0]["attachments"][0]


@pytest.mark.parametrize("endpoint", ["send", "close"])
def test_incomplete_archive_is_read_only_and_cannot_claim_complete_close(
    ticket_media_api, endpoint
):
    with ticket_media_api.session_factory() as db:
        ticket = db.get(Ticket, 7)
        ticket.archive_state = "incomplete"
        db.commit()

    response = ticket_media_api.client.post(
        f"/api/v1/admin/tickets/7/{endpoint}",
        json={"content": "must not reach Discord"} if endpoint == "send" else {},
    )

    assert response.status_code == 409


@pytest.mark.parametrize("endpoint", ["send", "close"])
def test_ticket_mutations_require_view_in_addition_to_action_permission(
    ticket_media_api, monkeypatch, endpoint
):
    monkeypatch.setattr(
        tickets_api,
        "check_permission",
        lambda _admin, _page, action: action in {"reply", "close"},
    )

    response = ticket_media_api.client.post(
        f"/api/v1/admin/tickets/7/{endpoint}",
        json={"content": "not visible"} if endpoint == "send" else {},
    )

    assert response.status_code == 403


def test_ticket_assign_uses_dedicated_permission_not_edit(
    ticket_media_api, monkeypatch
):
    monkeypatch.setattr(
        tickets_api,
        "check_permission",
        lambda _admin, _page, action: action in {"view", "edit"},
    )

    response = ticket_media_api.client.post("/api/v1/admin/tickets/7/assign")

    assert response.status_code == 403


def test_api_ticket_media_message_lookup_is_scoped_to_ticket(ticket_media_api):
    response = ticket_media_api.client.get(
        "/api/v1/admin/tickets/8/messages/19/media/proof.mp4"
    )

    assert response.status_code == 404
