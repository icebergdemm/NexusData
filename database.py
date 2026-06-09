from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _apply_migrations():
    """Safely add missing columns to existing tables via ALTER TABLE."""
    migrations = [
        # anticheat_events
        ("anticheat_events", "player_steamid", "VARCHAR(50) NOT NULL DEFAULT ''"),
        ("anticheat_events", "player_name", "VARCHAR(255)"),
        ("anticheat_events", "player_id", "INT"),
        ("anticheat_events", "event_type", "VARCHAR(128) NOT NULL DEFAULT ''"),
        ("anticheat_events", "details", "JSON"),
        ("anticheat_events", "occurred_at", "DATETIME"),
        ("anticheat_events", "reviewed", "TINYINT DEFAULT 0"),
        # server_action_queue
        ("server_action_queue", "action_type", "VARCHAR(32) NOT NULL DEFAULT ''"),
        ("server_action_queue", "target_steamid", "VARCHAR(50) NOT NULL DEFAULT ''"),
        ("server_action_queue", "reason", "VARCHAR(500)"),
        ("server_action_queue", "duration_minutes", "INT"),
        ("server_action_queue", "message_text", "VARCHAR(1000)"),
        ("server_action_queue", "queued_by_admin_id", "INT"),
        ("server_action_queue", "queued_at", "DATETIME"),
        ("server_action_queue", "executed", "TINYINT DEFAULT 0"),
        ("server_action_queue", "executed_at", "DATETIME"),
        ("server_action_queue", "error_message", "VARCHAR(500)"),
        # players_data new columns
        ("players_data", "labels", "JSON"),
        # player_punishments columns (in case table pre-existed without them)
        ("player_punishments", "player_steamid", "VARCHAR(50)"),
        ("player_punishments", "player_ip", "VARCHAR(64)"),
        ("player_punishments", "notes", "VARCHAR(2000)"),
        ("player_punishments", "expires_at", "DATETIME"),
        ("player_punishments", "is_active", "TINYINT DEFAULT 1"),
        # server_player_privileges additions: groups, commands, allowed action ids
        ("server_player_privileges", "group_id", "VARCHAR(64)"),
        ("server_player_privileges", "commands", "JSON"),
        ("server_player_privileges", "allowed_action_ids", "JSON"),
        # server_action_queue: multi-server support
        ("server_action_queue", "server_name", "VARCHAR(100)"),
        # server_player_privileges: multi-server scope
        ("server_player_privileges", "server_scope", "VARCHAR(20)"),
        # server_action_queue: per-server ACK tracking
        ("server_action_queue", "ack_asper1", "TINYINT DEFAULT 0 NOT NULL"),
        ("server_action_queue", "ack_asper2", "TINYINT DEFAULT 0 NOT NULL"),
        # players_data: discord_id
        ("players_data", "discord_id", "VARCHAR(25)"),
        ("players_data", "discord_id_confirmed", "TINYINT DEFAULT 0"),
    ]
    db_name = settings.database_url.split("/")[-1].split("?")[0]
    with engine.connect() as conn:
        for table, column, col_def in migrations:
            exists = conn.execute(
                __import__('sqlalchemy').text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=:db AND TABLE_NAME=:tbl AND COLUMN_NAME=:col"
                ),
                {"db": db_name, "tbl": table, "col": column},
            ).scalar()
            if not exists:
                try:
                    conn.execute(
                        __import__('sqlalchemy').text(
                            f"ALTER TABLE `{table}` ADD COLUMN `{column}` {col_def}"
                        )
                    )
                    conn.commit()
                except Exception as e:
                    conn.rollback()

        # Widen kits.job_id if it's too narrow
        try:
            col_type = conn.execute(
                __import__('sqlalchemy').text(
                    "SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=:db AND TABLE_NAME='kits' AND COLUMN_NAME='job_id'"
                ),
                {"db": db_name},
            ).scalar()
            if col_type and int(col_type) < 50:
                conn.execute(__import__('sqlalchemy').text(
                    "ALTER TABLE `kits` MODIFY COLUMN `job_id` VARCHAR(50)"
                ))
                conn.commit()
        except Exception:
            pass


def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    _apply_migrations()
