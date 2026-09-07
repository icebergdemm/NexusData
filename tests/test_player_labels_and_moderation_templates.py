from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import LabelCategory, PlayerData
from services import ModerationActionTemplateService, PlayerLabelService


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
