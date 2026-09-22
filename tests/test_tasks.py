import asyncio
import sys
import types

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
from routes import tasks


class JsonRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


@pytest.fixture
def task_db():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _admin(admin_id=1, *, superuser=True):
    return models.AdminUser(
        id=admin_id,
        discord_id=f"admin-{admin_id}",
        discord_username=f"Admin {admin_id}",
        is_superuser=superuser,
    )


def _board(db, board_id, name, *, archived=False):
    board = models.TaskBoard(id=board_id, name=name, is_archived=archived)
    column = models.TaskColumn(id=board_id * 10, board=board, name="Новые", sort_order=0)
    db.add_all([board, column])
    return board, column


def test_take_task_claims_once_and_moves_to_in_progress(task_db, monkeypatch):
    db = task_db
    admin = _admin()
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    task = models.Task(board=board, title="Заявка", status="in_dev_queue")
    db.add_all([admin, task])
    db.commit()
    monkeypatch.setattr(tasks, "_schedule_task_dm", lambda *_args: None)

    response = asyncio.run(tasks.api_take_task(task.id, db, admin))
    assert response["task"]["status"] == "in_progress"
    assert response["task"]["assignee_admin_id"] == admin.id
    with pytest.raises(tasks.HTTPException) as error:
        asyncio.run(tasks.api_take_task(task.id, db, _admin(2)))
    assert error.value.status_code == 409
    assert "Admin 1" in error.value.detail


def test_take_task_claims_unassigned_in_progress_task(task_db, monkeypatch):
    db = task_db
    admin = _admin()
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    task = models.Task(board=board, title="Заявка", status="in_progress")
    db.add_all([admin, task])
    db.commit()
    monkeypatch.setattr(tasks, "_schedule_task_dm", lambda *_args: None)

    response = asyncio.run(tasks.api_take_task(task.id, db, admin))

    assert response["task"]["status"] == "in_progress"
    assert response["task"]["assignee_admin_id"] == admin.id
    with pytest.raises(tasks.HTTPException) as already_claimed:
        asyncio.run(tasks.api_take_task(task.id, db, _admin(2)))
    assert already_claimed.value.status_code == 409


def test_manual_assignment_moves_new_request_to_in_progress(task_db, monkeypatch):
    db = task_db
    admin = _admin()
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    task = models.Task(board=board, title="Заявка", status="in_dev_queue")
    db.add_all([admin, task])
    db.commit()
    monkeypatch.setattr(tasks, "_schedule_task_dm", lambda *_args: None)

    response = asyncio.run(tasks.api_update_task(
        task.id,
        JsonRequest({"assignee_admin_id": admin.id}),
        db,
        admin,
    ))

    assert response["task"]["status"] == "in_progress"
    assert response["task"]["assignee_admin_id"] == admin.id


def test_system_task_status_migration_normalizes_legacy_states_once(task_db):
    import database
    from datetime import datetime

    db = task_db
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    assigned = _admin(7)
    queue_task = models.Task(board=board, title="Назначенная новая", status="in_dev_queue", assignee_admin=assigned)
    blocked_task = models.Task(board=board, title="Заблокированная", status="blocked")
    archived_task = models.Task(board=board, title="В архиве", status="archived")
    db.add_all([assigned, queue_task, blocked_task, archived_task])
    db.flush()
    archive_log = models.TaskActivityLog(
        task_id=archived_task.id,
        action="status_change",
        detail="submitted -> archived",
        created_at=datetime(2026, 8, 1),
    )
    db.add(archive_log)
    db.commit()

    database._migrate_system_task_states(db.get_bind())
    database._migrate_system_task_states(db.get_bind())
    db.expire_all()

    assert db.get(models.Task, queue_task.id).status == "in_progress"
    assert db.get(models.Task, queue_task.id).assignee_admin_id == assigned.id
    assert db.get(models.Task, blocked_task.id).status == "in_progress"
    assert db.get(models.Task, archived_task.id).archived_at == datetime(2026, 8, 1)
    assert db.query(models.TaskActivityLog).filter_by(task_id=queue_task.id, action="status_change").count() == 1
    assert db.query(models.TaskActivityLog).filter_by(task_id=blocked_task.id, action="status_change").count() == 1


def test_system_request_is_created_before_existing_requests(task_db):
    db = task_db
    admin = _admin()
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    existing = models.Task(board=board, title="Старая заявка", status="submitted", sort_order=4)
    db.add_all([admin, existing])
    db.commit()

    response = asyncio.run(tasks.api_create_task(
        board.id,
        JsonRequest({"title": "Новая заявка", "product_tag": "game"}),
        db,
        admin,
    ))

    assert response["task"]["sort_order"] < existing.sort_order


def test_archive_cleanup_deletes_only_expired_archived_system_requests(task_db):
    from datetime import datetime, timedelta

    db = task_db
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    old = models.Task(board=board, title="Старая", status="rejected", archived_at=datetime(2026, 1, 1))
    recent = models.Task(board=board, title="Свежая", status="archived", archived_at=datetime(2026, 9, 1))
    active = models.Task(board=board, title="Активная", status="submitted", archived_at=None)
    custom, _ = _board(db, 2, "Кастомная")
    custom_old = models.Task(board=custom, title="Старая кастомная", status="archived", archived_at=datetime(2026, 1, 1))
    db.add_all([old, recent, active, custom_old])
    db.commit()

    deleted = tasks.cleanup_expired_system_tasks(db, now=datetime(2026, 9, 23), retention_days=30)

    assert deleted == 1
    assert db.get(models.Task, old.id) is None
    assert db.get(models.Task, recent.id) is not None
    assert db.get(models.Task, active.id) is not None
    assert db.get(models.Task, custom_old.id) is not None


def test_archive_cleanup_is_armed_and_runs_at_most_monthly(task_db):
    from datetime import datetime

    db = task_db
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    old = models.Task(board=board, title="Старая заявка", status="archived", archived_at=datetime(2026, 1, 1))
    db.add(old)
    db.commit()

    assert tasks.run_system_task_archive_cleanup(db, now=datetime(2026, 9, 1)) == {"status": "armed", "deleted": 0}
    assert db.get(models.Task, old.id) is not None
    assert tasks.run_system_task_archive_cleanup(db, now=datetime(2026, 9, 30)) == {"status": "not_due", "deleted": 0}
    assert tasks.run_system_task_archive_cleanup(db, now=datetime(2026, 10, 2)) == {"status": "done", "deleted": 1}
    assert db.get(models.Task, old.id) is None


def test_reject_task_requires_reason_and_sets_separate_status(task_db, monkeypatch):
    db = task_db
    admin = _admin()
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    task = models.Task(board=board, title="Заявка", status="submitted")
    db.add_all([admin, task])
    db.commit()
    monkeypatch.setattr(tasks, "_schedule_task_dm", lambda *_args: None)

    with pytest.raises(tasks.HTTPException) as missing_reason:
        asyncio.run(tasks.api_reject_task(task.id, JsonRequest({"reason": " "}), db, admin))
    assert missing_reason.value.status_code == 400
    response = asyncio.run(tasks.api_reject_task(task.id, JsonRequest({"reason": "Не подходит"}), db, admin))
    assert response["task"]["status"] == "rejected"
    assert response["task"]["rejection_reason"] == "Не подходит"
    assert response["task"]["priority"] is None


def test_system_board_settings_save_archive_cleanup_options(task_db):
    db = task_db
    admin = _admin()
    db.add(admin)
    db.commit()

    response = asyncio.run(tasks.api_update_system_board_settings(
        JsonRequest({
            "request_limit": {"enabled": True, "max_active": 3},
            "archive_cleanup": {"enabled": False, "retention_days": 30},
        }),
        db,
        admin,
    ))

    assert response["settings"]["archive_cleanup"] == {"enabled": False, "retention_days": 30}
    assert tasks._get_system_board_settings(db)["archive_cleanup"] == response["settings"]["archive_cleanup"]


@pytest.mark.parametrize("status", ["submitted", "in_dev_queue", "in_progress", "blocked", "testing", "done"])
def test_system_task_can_only_be_deleted_from_archive(task_db, status):
    db = task_db
    admin = _admin()
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    task = models.Task(board=board, title="Заявка", status=status)
    db.add_all([admin, task])
    db.commit()

    with pytest.raises(tasks.HTTPException) as error:
        asyncio.run(tasks.api_delete_task(task.id, db, admin))
    assert error.value.status_code == 409


def test_task_dm_uses_server_nickname(task_db, monkeypatch):
    import discord_bot.bot as discord_bot

    db = task_db
    author = _admin(1)
    actor = _admin(2)
    board, _ = _board(db, 1, "Запросы функционала")
    task = models.Task(board=board, title="Заявка", created_by=author)
    profile = models.DiscordMember(discord_id=actor.discord_id, username="public-name", display_name="Ник на сервере")
    db.add_all([author, actor, task, profile])
    db.commit()
    queued = []
    monkeypatch.setattr(discord_bot, "execute_bot_action", lambda _action, payload: payload)
    monkeypatch.setattr(discord_bot, "schedule", lambda payload: queued.append(payload))

    tasks._schedule_task_dm(db, task, actor, "done")

    assert "Ник на сервере" in queued[0]["content"]
    assert "public-name" not in queued[0]["content"]


def test_move_task_preserves_data_appends_and_logs(task_db):
    db = task_db
    admin = _admin()
    source, source_column = _board(db, 1, "Источник")
    target, target_column = _board(db, 2, "Цель")
    existing = models.Task(
        board=target,
        column=target_column,
        title="Уже в цели",
        sort_order=4,
        created_by_admin_id=admin.id,
    )
    task = models.Task(
        board=source,
        column=source_column,
        title="Переносимая задача",
        description="Не потерять",
        deadline=None,
        priority="high",
        tags=["важно"],
        status="in_progress",
        sort_order=7,
        created_by_admin_id=admin.id,
    )
    comment = models.TaskComment(task=task, admin_id=admin.id, content="История")
    attachment = models.TaskAttachment(
        task=task,
        uploader_admin_id=admin.id,
        filename="proof.txt",
        stored_path="/tmp/proof.txt",
    )
    db.add_all([admin, existing, task, comment, attachment])
    db.commit()

    response = asyncio.run(tasks.api_move_task(
        task.id,
        JsonRequest({"target_board_id": target.id, "target_column_id": target_column.id}),
        db,
        admin,
    ))

    db.refresh(task)
    assert response["status"] == "ok"
    assert response["task"]["board_id"] == target.id
    assert response["task"]["column_id"] == target_column.id
    assert task.title == "Переносимая задача"
    assert task.description == "Не потерять"
    assert task.priority == "high"
    assert task.tags == ["важно"]
    assert task.status == "in_progress"
    assert task.sort_order == 5
    assert db.query(models.TaskComment).filter_by(task_id=task.id).count() == 1
    assert db.query(models.TaskAttachment).filter_by(task_id=task.id).count() == 1
    activity = db.query(models.TaskActivityLog).filter_by(task_id=task.id, action="move").one()
    assert "Источник" in activity.detail
    assert "Цель" in activity.detail
    assert "Новые" in activity.detail


def test_move_task_requires_manage_boards(task_db, monkeypatch):
    db = task_db
    admin = _admin(superuser=False)
    source, source_column = _board(db, 1, "Источник")
    target, target_column = _board(db, 2, "Цель")
    task = models.Task(board=source, column=source_column, title="Задача", created_by_admin_id=admin.id)
    db.add_all([admin, task])
    db.commit()
    monkeypatch.setattr(tasks, "check_permission", lambda *_args: False)

    with pytest.raises(tasks.HTTPException) as error:
        asyncio.run(tasks.api_move_task(
            task.id,
            JsonRequest({"target_board_id": target.id, "target_column_id": target_column.id}),
            db,
            admin,
        ))

    assert error.value.status_code == 403
    db.refresh(task)
    assert task.board_id == source.id


def test_move_task_uses_manage_boards_without_tasks_view(task_db, monkeypatch):
    db = task_db
    admin = _admin(superuser=False)
    source, source_column = _board(db, 1, "Источник")
    target, target_column = _board(db, 2, "Цель")
    task = models.Task(board=source, column=source_column, title="Задача", created_by_admin_id=admin.id)
    db.add_all([admin, task])
    db.commit()
    monkeypatch.setattr(tasks, "check_permission", lambda _admin, _page, action: action == "manage_boards")

    response = asyncio.run(tasks.api_move_task(
        task.id,
        JsonRequest({"target_board_id": target.id, "target_column_id": target_column.id}),
        db,
        admin,
    ))

    assert response["status"] == "ok"
    db.refresh(task)
    assert task.board_id == target.id


@pytest.mark.parametrize(
    ("target_board_id", "target_column_id", "expected_status"),
    [(1, 20, 400), (999, 20, 404), (2, 999, 404)],
)
def test_move_task_rejects_invalid_destination(task_db, target_board_id, target_column_id, expected_status):
    db = task_db
    admin = _admin()
    source, source_column = _board(db, 1, "Источник")
    target, target_column = _board(db, 2, "Цель")
    db.add_all([admin, models.Task(board=source, column=source_column, title="Задача", created_by_admin_id=admin.id)])
    db.commit()
    task = db.query(models.Task).one()

    with pytest.raises(tasks.HTTPException) as error:
        asyncio.run(tasks.api_move_task(
            task.id,
            JsonRequest({"target_board_id": target_board_id, "target_column_id": target_column_id}),
            db,
            admin,
        ))

    assert error.value.status_code == expected_status
    db.refresh(task)
    assert task.board_id == source.id


def test_ultra_priority_is_accepted_and_serialized(task_db):
    db = task_db
    admin = _admin()
    board, column = _board(db, 1, "Задачи")
    db.add(admin)
    db.commit()

    response = asyncio.run(tasks.api_create_task(
        board.id,
        JsonRequest({"title": "Ультра", "column_id": column.id, "priority": "ultra"}),
        db,
        admin,
    ))

    assert "ultra" in tasks.TASK_PRIORITIES
    assert response["task"]["priority"] == "ultra"


def test_move_task_rejects_archived_destination(task_db):
    db = task_db
    admin = _admin()
    source, source_column = _board(db, 1, "Источник")
    target, target_column = _board(db, 2, "Архив", archived=True)
    task = models.Task(board=source, column=source_column, title="Задача", created_by_admin_id=admin.id)
    db.add_all([admin, task])
    db.commit()

    with pytest.raises(tasks.HTTPException) as error:
        asyncio.run(tasks.api_move_task(
            task.id,
            JsonRequest({"target_board_id": target.id, "target_column_id": target_column.id}),
            db,
            admin,
        ))

    assert error.value.status_code == 404
