from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
from discord_bot import data as bot_data


def test_discord_command_uses_moderator_snapshot(monkeypatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[
        models.AdminActionLog.__table__,
        models.DiscordMember.__table__,
    ])
    db = Session(engine)
    db.add(models.DiscordMember(discord_id="555", display_name="Мичман Дмитрий Волков", username="volkov"))
    db.commit()
    monkeypatch.setattr(bot_data, "SessionLocal", lambda: db)
    try:
        bot_data.log_discord_command("close", {
            "moderator_id": "555",
            "moderator": "Мичман Дмитрий Волков",
            "channel_id": "77",
        })
        event = db.query(models.AdminActionLog).one()
        assert event.actor_type == "discord"
        assert event.actor_discord_id == "555"
        assert event.actor_discord_name == "Мичман Дмитрий Волков"
        assert event.action == "discord:close"
    finally:
        db.close()
        engine.dispose()
