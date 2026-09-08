from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from config import get_settings

settings = get_settings()

_CONNECT_ARGS = {"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    # Never let an async route stall the single event-loop thread for
    # SQLAlchemy's 30-second default merely waiting for a free connection.
    # Under overload it is safer to fail one request quickly than freeze the
    # website, Discord heartbeat and every game-server callback together.
    pool_timeout=3,
    # Размеры пулов подобраны под жёсткий лимит памяти контейнера (2 ГБ) и
    # ограниченный пул потоков (12, см. main.py): держать десятки открытых
    # соединений с их буферами незачем — параллелизм всё равно упирается в
    # число потоков. Суммарно оба пула дают максимум 28 соединений вместо 43.
    pool_size=5,
    max_overflow=10,
    # Without these, a stalled MySQL connection (network blip, server-side lock/
    # load spike) blocks the calling thread forever. Background worker tasks run
    # each on their own thread with only a soft timeout (task_scheduler can't
    # actually kill a stuck thread) — a query that never returns leaves its DB
    # session checked out of the pool permanently. Enough of those over time
    # exhaust the pool and freeze the whole app for every request. These
    # timeouts guarantee pymysql raises instead of blocking indefinitely, so
    # the thread unwinds and its connection is released back to the pool.
    connect_args=_CONNECT_ARGS,
)

# Separate engine/pool for the game-server-facing API (routes/game_api.py,
# routes/moderation.py's api_router, routes/dispatch.py's api_router,
# routes/world_state.py, routes/cache_sync.py) — a flood of slow admin-panel/
# analytics requests on `engine` above must never be able to exhaust the
# connections the actual Barotrauma game servers need to keep working. Same
# MySQL instance, same settings, just a physically separate connection pool
# so the two request classes can never compete for the same connection slot.
game_engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=5,
    max_overflow=8,
    pool_timeout=3,
    connect_args=_CONNECT_ARGS,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

GameSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=game_engine,
)

Base = declarative_base()

# Names of the seeded blank document templates (see _seed_blank_templates) —
# also used by routes/documents.py to find the default file for a new
# document that isn't created from an explicit template_id.
BLANK_DOCX_TEMPLATE_NAME = "Пустой документ"
BLANK_XLSX_TEMPLATE_NAME = "Пустая таблица"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_game_db():
    """Same shape as get_db(), bound to the separate game_engine pool — use
    this for game-server-facing routes so admin-panel/analytics DB load can
    never starve them (see the game_engine comment above)."""
    db = GameSessionLocal()
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
        # admin_users: soft-delete
        ("admin_users", "is_deleted", "TINYINT NOT NULL DEFAULT 0"),
        # player_punishments: snapshot whether the issuing admin was a superuser
        ("player_punishments", "issued_by_was_superuser", "TINYINT NOT NULL DEFAULT 0"),
        # players_data: superuser-managed link to an ND admin account + a display
        # name override shown everywhere in ratings/punishments
        ("players_data", "linked_admin_id", "INT"),
        ("players_data", "forced_display_name", "VARCHAR(255)"),
        ("players_data", "linked_admin_auto", "TINYINT NOT NULL DEFAULT 0"),
        # players_data: cached Steam avatar
        ("players_data", "steam_avatar_url", "VARCHAR(500)"),
        ("players_data", "steam_avatar_fetched_at", "DATETIME"),
        # round_events: link an "end" row back to the "start" row that opened it
        ("round_events", "paired_start_id", "INT"),
        ("round_events", "server_session_id", "VARCHAR(64)"),
        ("round_events", "round_token", "VARCHAR(64)"),
        ("player_communication_recipients", "is_dead", "TINYINT NOT NULL DEFAULT 0"),
        # tickets: retain closed ticket history in a soft-deleted archive
        ("tickets", "deleted_at", "DATETIME"),
        ("tickets", "deleted_by", "VARCHAR(255)"),
        # world_rounds: durable once-per-round claim for the automatic AI briefing
        ("world_rounds", "ai_briefing_queued_at", "DATETIME"),
        # AI dossier diagnostics: bounded raw model content, exposed only to
        # superusers by the AI-center API.
        ("ai_player_dossier_runs", "raw_response", "TEXT"),
        # ai_creature_alerts: retryable outbox state. A detected contact must
        # not become permanently suppressed merely because an AI request or
        # game-server delivery failed once.
        ("ai_creature_alerts", "queued_at", "DATETIME"),
        ("ai_creature_alerts", "delivered_at", "DATETIME"),
        ("ai_creature_alerts", "last_seen_at", "DATETIME"),
        ("ai_creature_alerts", "yasa_perimeter", "TINYINT NOT NULL DEFAULT 0"),
        ("ai_creature_alerts", "reported_distance_from_yasa_m_approx", "INT"),
        # admin_users: flags accounts auto-provisioned by task_sync_department_roles
        ("admin_users", "created_by_worker", "TINYINT NOT NULL DEFAULT 0"),
        # discord_members: role IDs currently held, for department role sync
        ("discord_members", "role_ids", "JSON"),
        # documents: department-scoped documentation + nested sub-pages
        ("documents", "department_id", "INT"),
        ("documents", "parent_document_id", "INT"),
        # rating v2: raw input is retained separately from any calculated delta
        ("rating_event_ledger", "raw_value", "DOUBLE NOT NULL DEFAULT 0"),
        ("rating_event_ledger", "mapping_status", "VARCHAR(16) NOT NULL DEFAULT 'mapped'"),
        ("rating_event_ledger", "metadata", "JSON"),
        ("rating_event_ledger", "formula_version", "VARCHAR(64) NOT NULL DEFAULT 'rating-v2-ledger-v1'"),
        ("rating_event_ledger", "config_version", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
        ("rating_event_ledger", "dedup_key", "VARCHAR(64)"),
        ("rating_projection_states", "action_counts", "JSON"),
        ("rating_projection_states", "round_tokens", "JSON"),
        ("rating_projection_states", "qualified_rounds", "INT NOT NULL DEFAULT 0"),
        ("rating_projection_states", "unmapped_events", "INT NOT NULL DEFAULT 0"),
        ("rating_delivery_log", "mode", "VARCHAR(16)"),
        ("rating_delivery_log", "role", "VARCHAR(32)"),
        # orders (item 8): department Discord channel + order-doc/-template flags
        ("departments", "orders_channel_id", "VARCHAR(50)"),
        ("documents", "is_order", "TINYINT NOT NULL DEFAULT 0"),
        ("document_templates", "is_order_template", "TINYINT NOT NULL DEFAULT 0"),
        # Hybrid permissions (item 3): superuser-only site-wide grants per department access level
        ("department_access_levels", "site_permissions", "JSON"),
        # Department document folders (item 5) + mandatory news module (item 6)
        ("documents", "department_folder_id", "INT"),
        ("documents", "is_news", "TINYINT NOT NULL DEFAULT 0"),
        ("departments", "news_channel_id", "VARCHAR(50)"),
        ("department_members", "is_deputy_head", "TINYINT NOT NULL DEFAULT 0"),
        # Manual replay<->round link, escape hatch for the header-parse-based
        # auto matcher (item 9)
        ("round_replays", "manual_round_event_id", "INT"),
        # Kit variations + special kits (item 15) — additive; `items` (variation 1)
        # unchanged, so old game-server clients that only read job_id/kit_level/items
        # keep working untouched.
        ("kits", "variation_2_items", "JSON"),
        ("kits", "variation_3_items", "JSON"),
        ("kits", "is_special", "TINYINT NOT NULL DEFAULT 0"),
        ("kits", "special_variation", "INT"),
        # Documents move to ONLYOFFICE-edited real files instead of Tiptap JSON —
        # `content`/`content_text` (documents), `content` (document_revisions/
        # document_templates), and is_wiki + the database/view tables are DROPPED
        # manually via migrations/038_onlyoffice_documents.sql (this app never
        # auto-drops columns/tables, only adds them).
        ("documents", "file_type", "VARCHAR(10) NOT NULL DEFAULT 'word'"),
        ("documents", "storage_filename", "VARCHAR(255)"),
        ("document_templates", "file_type", "VARCHAR(10) NOT NULL DEFAULT 'word'"),
        ("document_templates", "storage_filename", "VARCHAR(255)"),
        ("document_revisions", "storage_filename", "VARCHAR(255)"),
        # Sub-projects split into normal zone + department-scoped "Тестовый
        # полигон ASPER" zone — NULL department_id = normal zone.
        ("sub_projects", "department_id", "INT"),
        # Superuser-configurable links from department access levels/head role
        # to a server privilege group, auto-applied by
        # task_sync_auto_server_privileges.
        ("department_access_levels", "server_privilege_group_id", "INT"),
        ("departments", "head_server_privilege_group_id", "INT"),
        # Recency signal for the auto-grant tie-break ("most recent
        # assignment wins" across departments).
        ("department_members", "role_state_updated_at", "DATETIME"),
        # Marks a server_group_id as worker-managed vs a human's manual
        # assignment — the worker never touches a row once is_auto is False.
        ("server_player_privileges", "is_auto", "TINYINT NOT NULL DEFAULT 0"),
        ("server_player_privileges", "auto_source_department_id", "INT"),
        # sub_project_categories: default category auto-assigned to new
        # normal-zone projects.
        ("sub_project_categories", "is_default", "TINYINT NOT NULL DEFAULT 0"),
        # document_permissions: share with "everyone holding this department
        # access level" (department-scoped documents only).
        ("document_permissions", "grantee_access_level_key", "VARCHAR(64)"),
        # departments: Discord channel for the hourly department-rating
        # leaderboard embed (bot-published).
        ("departments", "rating_channel_id", "VARCHAR(50)"),
        # discord_bot_actions: structured response for request/response-style
        # actions (channel-category verification).
        ("discord_bot_actions", "result", "JSON"),
        # documents: Discord publish tracking (generalized publish/sync,
        # not just orders/news).
        ("documents", "discord_channel_id", "VARCHAR(50)"),
        ("documents", "discord_message_id", "VARCHAR(64)"),
        # department_document_folders: default publish channel + whether this
        # folder's permission grants cascade to nested subfolders' documents.
        ("department_document_folders", "discord_channel_id", "VARCHAR(50)"),
        ("department_document_folders", "auto_apply_permissions", "TINYINT NOT NULL DEFAULT 0"),
        # departments: Discord category ID, for validating a manually-entered
        # publish-channel ID actually belongs to this department.
        ("departments", "discord_category_id", "VARCHAR(50)"),
        # sub_projects: manual drag-and-drop card ordering.
        ("sub_projects", "sort_order", "INT NOT NULL DEFAULT 0"),
        # server_actions: structured data for action types that don't fit the
        # original kick/ban/message columns — torpedo strike coordinates,
        # cargo item list, etc. (see routes/dispatch.py's new action types).
        ("server_actions", "payload", "JSON"),
        # tasks — flexible board permissions, attachments, activity log, hidden/status (items 1-3)
        ("tasks", "status", 'VARCHAR(20) NOT NULL DEFAULT "review"'),
        ("tasks", "is_hidden", "TINYINT NOT NULL DEFAULT 0"),
        ("tasks", "rejection_reason", "VARCHAR(1000)"),
        ("tasks", "preview_attachment_id", "INT"),
        # tickets — durable evidence/forward snapshots and a real assignee.
        ("tickets", "assigned_admin_id", "INT"),
        # tickets — manual Discord deletion is an explicit incomplete-archive
        # exception and must not be treated as evidence-complete closure.
        ("tickets", "archive_state", "VARCHAR(20)"),
        ("ticket_messages", "reference_snapshot", "JSON"),
        ("ticket_messages", "delivery_status", 'VARCHAR(20) NOT NULL DEFAULT "received"'),
        ("ticket_messages", "delivery_error", "VARCHAR(1000)"),
        # label folders/sorting + department access level sorting/description + superuser bind flag
        ("label_categories", "folder_id", "INT"),
        ("label_categories", "sort_order", "INT NOT NULL DEFAULT 0"),
        ("label_categories", "description", "VARCHAR(500)"),
        ("label_folders", "sort_order", "INT NOT NULL DEFAULT 0"),
        ("department_access_levels", "sort_order", "INT NOT NULL DEFAULT 0"),
        ("department_access_levels", "description", "VARCHAR(500)"),
        ("department_access_levels", "can_bind_server_privilege", "TINYINT NOT NULL DEFAULT 0"),
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
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "Migration failed for %s.%s: %s", table, column, e
                    )

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

        # AI system prompts are intentionally editable by superusers and the
        # Git default is already longer than the old generic setting limit.
        # Widen the existing production column before the first prompt save.
        try:
            value_type = conn.execute(__import__('sqlalchemy').text(
                "SELECT DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=:db AND TABLE_NAME='app_settings' AND COLUMN_NAME='value'"
            ), {"db": db_name}).scalar()
            if value_type and str(value_type).lower() not in {"text", "mediumtext", "longtext"}:
                conn.execute(__import__('sqlalchemy').text(
                    "ALTER TABLE `app_settings` MODIFY COLUMN `value` TEXT NULL"
                ))
                conn.commit()
        except Exception:
            pass


def _relax_server_player_privileges_unique_steamid():
    """server_player_privileges used to have UNIQUE(steamid), allowing exactly
    one privilege row per player — which forced a manual grant and a
    department-derived (is_auto) grant to fight over the same row, and made
    per-server-scoped grants (ASPER1 vs ASPER2) impossible. Drops that unique
    index so multiple rows can coexist per player (one manual per
    server_scope + one auto), summed together at read time by
    privilege_resolution.py. Idempotent: finds whatever the unique index is
    actually named (this table was originally created via raw SQL, not
    SQLAlchemy's create_all, so the name isn't guaranteed) and re-adds a
    plain (non-unique) index on steamid for lookup performance. Once the
    unique index no longer exists this is a no-op on every later startup."""
    text = __import__('sqlalchemy').text
    db_name = settings.database_url.split("/")[-1].split("?")[0]
    with engine.connect() as conn:
        try:
            idx_rows = conn.execute(text(
                "SELECT s.INDEX_NAME FROM information_schema.STATISTICS s "
                "WHERE s.TABLE_SCHEMA=:db AND s.TABLE_NAME='server_player_privileges' "
                "AND s.NON_UNIQUE=0 AND s.INDEX_NAME != 'PRIMARY' AND s.COLUMN_NAME='steamid' "
                "AND (SELECT COUNT(*) FROM information_schema.STATISTICS s2 "
                "     WHERE s2.TABLE_SCHEMA=s.TABLE_SCHEMA AND s2.TABLE_NAME=s.TABLE_NAME "
                "     AND s2.INDEX_NAME=s.INDEX_NAME) = 1"
            ), {"db": db_name}).fetchall()

            if not idx_rows:
                return

            for (index_name,) in idx_rows:
                conn.execute(text(f"ALTER TABLE `server_player_privileges` DROP INDEX `{index_name}`"))
                conn.commit()

            has_any_index = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA=:db AND TABLE_NAME='server_player_privileges' AND COLUMN_NAME='steamid'"
            ), {"db": db_name}).scalar()
            if not has_any_index:
                conn.execute(text(
                    "ALTER TABLE `server_player_privileges` ADD INDEX `ix_server_player_privileges_steamid` (`steamid`)"
                ))
                conn.commit()
        except Exception as e:
            conn.rollback()
            import logging as _logging
            _logging.getLogger(__name__).warning("server_player_privileges unique-steamid relax failed: %s", e)


_MISSING_INDEX_COLUMNS = [
    # (table, column) — added as index=True in models.py but the live MySQL
    # table (created before these fields existed / before this pass) may not
    # actually have the index yet. High priority: game-server hot paths
    # polled on every request cycle. Medium: background-task/admin-panel
    # filter columns previously full-scanned.
    ("server_actions", "status_asper1"),
    ("server_actions", "status_asper2"),
    ("player_punishments", "is_active"),
    ("players_data", "linked_admin_id"),
    ("players_data", "steam_avatar_fetched_at"),
    ("players_data", "last_login_time"),
    ("tickets", "status"),
    ("tickets", "awaiting_admin"),
    ("tickets", "player_id"),
    ("tickets", "deleted_at"),
    ("player_anomalies", "status"),
    ("player_anomalies", "score"),
    ("player_anomalies", "last_seen_at"),
    ("anticheat_events", "reviewed"),
    # player_sessions.login_time had NO index at all despite being the
    # WHERE-clause filter of every analytics query (services_analytics.py)
    # and the old Administration stats endpoint — a full table scan on a
    # table that grows with every player login/logout is exactly why the
    # Аналитика/Администрирование pages were "invaluably slow" for any
    # range wider than what the 5-minute background snapshot cache covers.
    ("player_sessions", "login_time"),
    # round_events.event_type is filtered alongside server_name/created_at
    # (both already indexed) in compute_submarine_popularity/
    # compute_server_comparison — an index here lets MySQL pick the most
    # selective single-column index instead of guessing.
    ("round_events", "event_type"),
    ("round_events", "server_session_id"),
    ("round_events", "round_token"),
    ("rating_event_ledger", "dedup_key"),
]

_MISSING_COMPOSITE_INDEXES = [
    # Ticket archive list: equality filters first, newest activity next. This
    # removes the full scan/filesort that dominates status=closed as history
    # grows.
    (
        "tickets",
        "ix_tickets_status_deleted_activity",
        ("status", "deleted_at", "last_message_at", "created_at"),
    ),
    # Latest-message lookup ranks only messages belonging to the 100 visible
    # tickets and orders within each ticket by creation time/id.
    (
        "ticket_messages",
        "ix_ticket_messages_ticket_created_id",
        ("ticket_id", "created_at", "id"),
    ),
]


def _add_missing_indexes():
    """Idempotent one-off: adds a plain index for each (table, column) pair in
    _MISSING_INDEX_COLUMNS if the live table doesn't already have one on that
    column — mirrors the information_schema.STATISTICS check pattern from
    _relax_server_player_privileges_unique_steamid. These columns are all
    declared index=True in models.py, but Base.metadata.create_all only
    creates indexes for brand-new tables — it never alters existing ones, so
    tables created before a given column got its index=True need this to
    actually pick it up on the live DB."""
    text = __import__('sqlalchemy').text
    db_name = settings.database_url.split("/")[-1].split("?")[0]
    with engine.connect() as conn:
        for table, column in _MISSING_INDEX_COLUMNS:
            try:
                has_index = conn.execute(text(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA=:db AND TABLE_NAME=:table AND COLUMN_NAME=:column"
                ), {"db": db_name, "table": table, "column": column}).scalar()
                if has_index:
                    continue
                conn.execute(text(
                    f"ALTER TABLE `{table}` ADD INDEX `ix_{table}_{column}` (`{column}`)"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "Adding missing index on %s.%s failed: %s", table, column, e
                )

        for table, index_name, columns in _MISSING_COMPOSITE_INDEXES:
            try:
                has_index = conn.execute(text(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA=:db AND TABLE_NAME=:table AND INDEX_NAME=:index"
                ), {"db": db_name, "table": table, "index": index_name}).scalar()
                if has_index:
                    continue
                quoted_columns = ", ".join(f"`{column}`" for column in columns)
                conn.execute(text(
                    f"ALTER TABLE `{table}` ADD INDEX `{index_name}` ({quoted_columns})"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "Adding composite index %s on %s failed: %s",
                    index_name,
                    table,
                    e,
                )


def _backfill_round_pairing():
    """One-time, idempotent pairing of round_events rows that predate the
    paired_start_id column, so historical rounds also show up in "Прошедшие
    раунды". Only scans anything when there's actually unpaired work."""
    text = __import__('sqlalchemy').text
    with engine.connect() as conn:
        try:
            unpaired = conn.execute(text(
                "SELECT COUNT(*) FROM round_events WHERE event_type='end' AND paired_start_id IS NULL"
            )).scalar()
            if not unpaired:
                return

            rows = conn.execute(text(
                "SELECT id, event_type, server_name, created_at FROM round_events ORDER BY server_name, created_at"
            )).fetchall()

            pending_start_by_server = {}
            for row in rows:
                sn = row.server_name
                if row.event_type == "start":
                    pending_start_by_server[sn] = row.id
                elif row.event_type == "end":
                    start_id = pending_start_by_server.pop(sn, None)
                    if start_id:
                        conn.execute(
                            text("UPDATE round_events SET paired_start_id=:sid WHERE id=:eid AND paired_start_id IS NULL"),
                            {"sid": start_id, "eid": row.id},
                        )
            conn.commit()
        except Exception as e:
            conn.rollback()
            import logging as _logging
            _logging.getLogger(__name__).warning("Round pairing backfill failed: %s", e)


def _backfill_permission_splits():
    """When a page's generic "edit" action gets split into new named actions
    (permissions_registry.PERMISSION_ACTION_SPLITS/PERMISSION_CROSS_PAGE_SPLITS),
    any group/individual that already had the OLD action granted gets the NEW
    actions granted too — purely additive (OR), so nobody silently loses a
    capability they had before this rework. Safe/cheap to re-run every startup."""
    import json
    from permissions_registry import PERMISSION_ACTION_SPLITS, PERMISSION_CROSS_PAGE_SPLITS
    text = __import__('sqlalchemy').text

    def apply_splits(perms):
        if not isinstance(perms, dict):
            return perms, False
        pages = perms.get("pages", perms)
        if not isinstance(pages, dict):
            return perms, False
        changed = False
        for page, split_map in PERMISSION_ACTION_SPLITS.items():
            page_perms = pages.get(page)
            if not isinstance(page_perms, dict):
                continue
            for old_action, new_actions in split_map.items():
                if page_perms.get(old_action):
                    for na in new_actions:
                        if not page_perms.get(na):
                            page_perms[na] = True
                            changed = True
        for from_page, from_action, to_page, to_action in PERMISSION_CROSS_PAGE_SPLITS:
            from_perms = pages.get(from_page)
            if isinstance(from_perms, dict) and from_perms.get(from_action):
                to_perms = pages.setdefault(to_page, {})
                if not to_perms.get(to_action):
                    to_perms[to_action] = True
                    changed = True
        if "pages" in perms:
            perms["pages"] = pages
        else:
            perms = pages
        return perms, changed

    with engine.connect() as conn:
        try:
            groups = conn.execute(text("SELECT id, permissions FROM admin_privilege_groups")).fetchall()
            for row in groups:
                raw = row.permissions
                if not raw:
                    continue
                perms = raw if isinstance(raw, dict) else json.loads(raw)
                updated, changed = apply_splits(perms)
                if changed:
                    conn.execute(
                        text("UPDATE admin_privilege_groups SET permissions=:p WHERE id=:id"),
                        {"p": json.dumps(updated), "id": row.id},
                    )

            admins = conn.execute(text(
                "SELECT id, individual_permissions FROM admin_users WHERE individual_permissions IS NOT NULL"
            )).fetchall()
            for row in admins:
                raw = row.individual_permissions
                if not raw:
                    continue
                perms = raw if isinstance(raw, dict) else json.loads(raw)
                updated, changed = apply_splits(perms)
                if changed:
                    conn.execute(
                        text("UPDATE admin_users SET individual_permissions=:p WHERE id=:id"),
                        {"p": json.dumps(updated), "id": row.id},
                    )
            conn.commit()
        except Exception as e:
            conn.rollback()
            import logging as _logging
            _logging.getLogger(__name__).warning("Permission-split backfill failed: %s", e)


def _seed_blank_templates():
    """One-time seed of the 2 starter templates for the ONLYOFFICE-based
    document library — a blank Word doc and a blank Excel sheet, generated
    once as real files under {documents_upload_dir}/templates/. Idempotent:
    skips a given template if its name already exists, so deleting/editing
    the seeded ones sticks. Replaces the old Tiptap-JSON template seeding
    (charter/order templates) — those had no ONLYOFFICE equivalent."""
    text = __import__('sqlalchemy').text
    import os

    templates_dir = os.path.join(settings.documents_upload_dir, "templates")
    os.makedirs(templates_dir, exist_ok=True)

    def ensure_docx(filename):
        # python-docx's built-in default template hardcodes the document's
        # default run language to en-US, which makes ONLYOFFICE spell-check
        # in English regardless of editorConfig.lang — always re-patch it to
        # ru-RU (not just on first creation), so an already-seeded file from
        # before this fix existed also gets corrected on the next restart.
        import docx
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        path = os.path.join(templates_dir, filename)
        doc = docx.Document(path) if os.path.isfile(path) else docx.Document()

        def set_lang(rpr):
            lang_el = rpr.find(qn('w:lang'))
            if lang_el is None:
                lang_el = OxmlElement('w:lang')
                rpr.append(lang_el)
            for attr in ('w:val', 'w:eastAsia', 'w:bidi'):
                lang_el.set(qn(attr), 'ru-RU')

        doc_defaults = doc.styles.element.find(qn('w:docDefaults'))
        if doc_defaults is not None:
            rpr_default = doc_defaults.find(qn('w:rPrDefault'))
            if rpr_default is not None:
                rpr = rpr_default.find(qn('w:rPr'))
                if rpr is None:
                    rpr = OxmlElement('w:rPr')
                    rpr_default.append(rpr)
                set_lang(rpr)

        normal_el = doc.styles['Normal'].element
        rpr = normal_el.find(qn('w:rPr'))
        if rpr is None:
            rpr = OxmlElement('w:rPr')
            normal_el.append(rpr)
        set_lang(rpr)

        doc.save(path)
        return filename

    def ensure_xlsx(filename):
        path = os.path.join(templates_dir, filename)
        if not os.path.isfile(path):
            from openpyxl import Workbook
            Workbook().save(path)
        return filename

    seeds = [
        {"name": BLANK_DOCX_TEMPLATE_NAME, "icon": "📄", "description": "Чистый документ Word", "file_type": "word", "filename": ensure_docx("blank.docx")},
        {"name": BLANK_XLSX_TEMPLATE_NAME, "icon": "📊", "description": "Чистая таблица Excel", "file_type": "cell", "filename": ensure_xlsx("blank.xlsx")},
    ]

    with engine.connect() as conn:
        try:
            for t in seeds:
                existing = conn.execute(
                    text("SELECT COUNT(*) FROM document_templates WHERE name=:name"),
                    {"name": t["name"]},
                ).scalar()
                if existing:
                    continue
                conn.execute(
                    text(
                        "INSERT INTO document_templates (name, description, icon, file_type, storage_filename, is_order_template, created_at, updated_at) "
                        "VALUES (:name, :description, :icon, :file_type, :filename, 0, NOW(), NOW())"
                    ),
                    {"name": t["name"], "description": t["description"], "icon": t["icon"], "file_type": t["file_type"], "filename": t["filename"]},
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            import logging as _logging
            _logging.getLogger(__name__).warning("Blank template seeding failed: %s", e)


def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    _apply_migrations()
    _relax_server_player_privileges_unique_steamid()
    _add_missing_indexes()
    _backfill_round_pairing()
    _seed_blank_templates()
    _backfill_permission_splits()
