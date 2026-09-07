from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import LabelCategory, PlayerData
from security import check_moderation_action_permission, csrf_token_from_request
from services import ModerationActionTemplateService, PlayerLabelService, prepare_admin_message


def _database() -> tuple[object, Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_player_label_updates_and_removal_are_scoped_to_player():
    engine, db = _database()
    try:
        db.add_all([
            PlayerData(player_id=1, player_last_time_name="Первый", player_steamid="steam-1"),
            PlayerData(player_id=2, player_last_time_name="Второй", player_steamid="steam-2"),
        ])
        category = LabelCategory(name="Наблюдение", color="#888888", icon="🏷")
        db.add(category)
        db.commit()

        label = PlayerLabelService(db).assign(1, category.id, "старый комментарий", 7)
        service = PlayerLabelService(db)

        assert service.update_note(label.id, 1, "новый комментарий").note == "новый комментарий"
        assert service.update_note(label.id, 2, "чужая запись") is None
        assert service.remove(label.id, player_id=2) is False
        assert service.remove(label.id, player_id=1) is True
    finally:
        db.close()
        engine.dispose()


def test_moderation_action_templates_support_crud_and_type_filtering():
    engine, db = _database()
    try:
        service = ModerationActionTemplateService(db)
        message = service.create("message", "Короткое сообщение", "Пожалуйста, прекратите", 7)
        warning = service.create("warning", "Предупреждение", "Нарушение правил", 7)

        assert [row.id for row in service.list(allowed_types={"message"})] == [message.id]
        assert service.get(message.id).content == "Пожалуйста, прекратите"

        service.update(message.id, name="Сообщение игроку", content="Новая формулировка")
        assert service.get(message.id).name == "Сообщение игроку"
        assert service.get(message.id).content == "Новая формулировка"

        assert service.delete(warning.id) is True
        assert service.get(warning.id) is None
    finally:
        db.close()
        engine.dispose()


def test_legacy_moderation_edit_keeps_message_action_available():
    admin = SimpleNamespace(
        is_superuser=False,
        individual_permissions={"pages": {"moderation": {"edit": True}}},
        privilege_group=None,
    )

    assert check_moderation_action_permission(admin, "message") is True


def test_admin_message_limit_includes_administration_prefix():
    assert len(prepare_admin_message("x" * 1980)) <= 2000
    with pytest.raises(ValueError):
        prepare_admin_message("x" * 1984)


def test_csrf_header_is_used_by_json_requests():
    request = SimpleNamespace(headers={"X-CSRF-Token": "header-token"})

    assert csrf_token_from_request(request, None) == "header-token"
