from datetime import datetime, timedelta
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from discord_bot.data import record_punishment_discord_message
from database import Base
from models import AdminUser, DiscordMember, PlayerData, PlayerPunishment, PlayerPunishmentDiscordMessage
from services import apply_punishment_discord_state, build_punishment_discord_embed, punishment_discord_state


def _punishment(**overrides):
    values = {
        "type": "warning",
        "reason": "Причина",
        "expires_at": None,
        "is_active": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _embed():
    return {
        "title": "⚠️ Предупреждение",
        "color": 0xffaa00,
        "fields": [
            {"name": "Игрок", "value": "`765` (Игрок)", "inline": True},
            {"name": "Выдал", "value": "ND. Старое имя", "inline": True},
            {"name": "Причина", "value": "Старая причина", "inline": False},
            {"name": "Истекает", "value": "01.01.2020 00:00", "inline": True},
            {"name": "Статус", "value": "Аннулировано", "inline": False},
        ],
    }


def test_active_state_has_no_status_and_keeps_current_issuer_and_reason():
    state = punishment_discord_state(_punishment(), "ND. Серверный Ник")

    assert state == {
        "issuer": "ND. Серверный Ник",
        "reason": "Причина",
        "expires_at": None,
        "status": None,
    }


def test_expired_state_is_explicit():
    punishment = _punishment(expires_at=datetime(2020, 1, 1))

    assert punishment_discord_state(punishment, "ND. Ник")["status"] == "Истекло"


def test_apply_state_updates_reason_expiry_and_removes_old_status():
    embed = _embed()
    changed = apply_punishment_discord_state(embed, {
        "issuer": "ND. Ник",
        "reason": "Новая причина",
        "expires_at": None,
        "status": None,
    })

    assert changed is True
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Выдал"] == "ND. Ник"
    assert fields["Причина"] == "Новая причина"
    assert "Истекает" not in fields
    assert "Статус" not in fields
    assert embed["title"] == "⚠️ Предупреждение"


def test_apply_state_adds_void_status_without_touching_unmanaged_fields():
    embed = _embed()
    embed["description"] = "Неуправляемое поле"
    apply_punishment_discord_state(embed, {
        "issuer": "ND. Ник",
        "reason": "Причина",
        "expires_at": None,
        "status": "Аннулировано",
    })

    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Статус"] == "Аннулировано"
    assert "Истекает" not in fields
    assert embed["description"] == "Неуправляемое поле"


def test_renderer_uses_server_nickname_and_marks_expired_web_punishment():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        player = PlayerData(player_steamid="765", player_last_time_name="Игрок")
        admin = AdminUser(discord_id="42", discord_username="Global Name")
        db.add_all([player, admin])
        db.flush()
        db.add(DiscordMember(discord_id="42", display_name="Server Nick"))
        punishment = PlayerPunishment(
            player_id=player.player_id,
            player_steamid="765",
            type="warning",
            reason="Причина",
            issued_by_admin_id=admin.id,
            issued_by_name="ND. Старое имя",
            expires_at=datetime(2020, 1, 1),
            is_active=1,
        )
        db.add(punishment)
        db.commit()

        embed, state_hash = build_punishment_discord_embed(db, punishment)
        fields = {field["name"]: field["value"] for field in embed["fields"]}

        assert fields["Выдал"] == "ND. Server Nick"
        assert fields["Статус"] == "Истекло"
        assert state_hash
    finally:
        db.close()
        engine.dispose()


def test_renderer_keeps_game_server_punishment_legacy_fields():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        punishment = PlayerPunishment(
            player_id=0,
            player_steamid="765",
            type="ban",
            reason="Причина",
            issued_by_admin_id=None,
            issued_by_name="GameServer",
            expires_at=datetime(2020, 1, 1),
            is_active=1,
        )
        db.add(punishment)
        db.commit()

        embed, _ = build_punishment_discord_embed(db, punishment)
        fields = {field["name"]: field["value"] for field in embed["fields"]}

        assert fields["Выдал"] == "GameServer"
        assert "Статус" not in fields
    finally:
        db.close()
        engine.dispose()


def test_record_punishment_discord_message_is_idempotent_per_channel():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[PlayerPunishment.__table__, PlayerPunishmentDiscordMessage.__table__])
    db = Session(engine)
    try:
        punishment = PlayerPunishment(player_id=1, type="warning", issued_by_admin_id=7)
        db.add(punishment)
        db.commit()
        punishment_id = punishment.id

        with patch("discord_bot.data.SessionLocal", return_value=db):
            record_punishment_discord_message(punishment_id, "channel", "message-1", "hash-1")
            record_punishment_discord_message(punishment_id, "channel", "message-2", "hash-2")

        rows = db.query(PlayerPunishmentDiscordMessage).all()
        assert len(rows) == 1
        assert rows[0].message_id == "message-2"
        assert rows[0].state_hash == "hash-2"
    finally:
        db.close()
        engine.dispose()
