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
