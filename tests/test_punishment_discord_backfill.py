from datetime import datetime, timezone, timedelta

from discord_bot.bot import _apply_punishment_record_to_embed, _find_punishment_record_for_message


def _embed(player_steamid="765", title="⚠️ Предупреждение", reason="Причина", issuer="ND. Старое"):
    return {
        "title": title,
        "footer": {"text": "1984"},
        "fields": [
            {"name": "Игрок", "value": f"`{player_steamid}` (Игрок)", "inline": True},
            {"name": "Выдал", "value": issuer, "inline": True},
            {"name": "Причина", "value": reason, "inline": False},
        ],
    }


def test_backfill_matches_punishment_embed_by_type_player_reason_and_time():
    issued_at = datetime(2026, 9, 8, 12, 0)
    record = {
        "id": 30,
        "type": "warning",
        "player_steamid": "765",
        "reason": "Причина",
        "issued_at": issued_at.isoformat(),
        "issued_by_name": "ND. Серверный Ник",
    }

    matched = _find_punishment_record_for_message(
        _embed(),
        issued_at.replace(tzinfo=timezone(timedelta(hours=3))).astimezone(timezone.utc),
        [record],
        set(),
    )

    assert matched == record


def test_backfill_ignores_non_punishment_embeds():
    issued_at = datetime(2026, 9, 8, 12, 0)
    record = {
        "id": 30,
        "type": "warning",
        "player_steamid": "765",
        "reason": "Причина",
        "issued_at": issued_at.isoformat(),
        "issued_by_name": "ND. Серверный Ник",
    }

    assert _find_punishment_record_for_message(
        {"title": "Не наказание", "fields": []},
        issued_at.replace(tzinfo=timezone.utc),
        [record],
        set(),
    ) is None


def test_backfill_marks_void_punishment_without_removing_embed_fields():
    embed = _embed()
    record = {"issued_by_name": "ND. Серверный Ник", "is_void": True}

    assert _apply_punishment_record_to_embed(embed, record) is True
    assert {field["name"]: field["value"] for field in embed["fields"]}["Выдал"] == "ND. Серверный Ник"
    assert {field["name"]: field["value"] for field in embed["fields"]}["Статус"] == "Аннулировано"
    assert embed["title"] == "⚠️ Предупреждение"
