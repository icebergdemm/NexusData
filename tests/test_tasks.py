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


def test_private_system_requests_are_filtered_and_personalized(task_db, monkeypatch):
    from datetime import datetime

    db = task_db
    developer = _admin(1, superuser=False)
    regular_user = _admin(2, superuser=False)
    other_author = _admin(3, superuser=False)
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    private_old = models.Task(
        board=board, title="Приватная старая", status="submitted",
        created_by_admin_id=3, is_private=True, created_at=datetime(2026, 1, 1),
    )
    own_new = models.Task(
        board=board, title="Своя новая", status="submitted",
        created_by_admin_id=1, created_at=datetime(2026, 2, 1),
    )
    other_new = models.Task(
        board=board, title="Чужая новая", status="submitted",
        created_by_admin_id=3, created_at=datetime(2026, 3, 1),
    )
    other_old = models.Task(
        board=board, title="Чужая старая", status="submitted",
        created_by_admin_id=3, created_at=datetime(2026, 1, 2),
    )
    regular_own = models.Task(
        board=board, title="Заявка обычного пользователя", status="submitted",
        created_by_admin_id=2, created_at=datetime(2025, 12, 1),
    )
    dev_private_in_progress = models.Task(
        board=board, title="Приватная в работе", status="in_progress",
        created_by_admin_id=2, is_private=True,
    )
    dev_private_submitted = models.Task(
        board=board, title="Приватная на рассмотрении", status="submitted",
        created_by_admin_id=1, is_private=True, created_at=datetime(2026, 4, 1),
    )
    db.add_all([developer, regular_user, other_author, private_old, own_new, other_new, other_old, regular_own, dev_private_in_progress, dev_private_submitted])
    db.commit()
    monkeypatch.setattr(tasks, "_can_develop_tasks", lambda admin: admin.id == developer.id)
    monkeypatch.setattr(tasks, "_has_board_perm", lambda *_args: True)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    dev_requests = asyncio.run(tasks.api_get_task_board(board.id, "requests", False, db, developer))
    regular_requests = asyncio.run(tasks.api_get_task_board(board.id, "requests", False, db, regular_user))
    dev_development = asyncio.run(tasks.api_get_task_board(board.id, "development", False, db, developer))

    assert [row["title"] for row in dev_requests["tasks"]] == [
        "Приватная в работе", "Приватная на рассмотрении", "Приватная старая", "Своя новая", "Чужая новая", "Чужая старая", "Заявка обычного пользователя",
    ]
    assert [row["title"] for row in regular_requests["tasks"]] == [
        "Заявка обычного пользователя", "Чужая новая", "Своя новая", "Чужая старая",
    ]
    assert all(not row["is_private"] for row in regular_requests["tasks"])
    assert {row["title"] for row in dev_development["tasks"]} == {
        "Приватная в работе", "Приватная старая", "Приватная на рассмотрении",
    }
    assert any(column["id"] == "submitted" for column in dev_development["columns"])


def test_system_request_priority_sorts_inside_each_personalized_group(task_db, monkeypatch):
    from datetime import datetime, timedelta

    db = task_db
    developer = _admin(1, superuser=False)
    other_author = _admin(2, superuser=False)
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    priorities = ["ultra", "urgent", "high", "medium", "low", None]
    base_date = datetime(2026, 1, 1)
    tasks_to_add = [developer, other_author]

    for group in ("private", "own", "other"):
        for index, priority in enumerate(priorities):
            tasks_to_add.append(models.Task(
                board=board,
                title=f"{group}:{priority or 'none'}",
                status="submitted",
                priority=priority,
                is_private=group == "private",
                created_by_admin_id=developer.id if group == "own" else other_author.id,
                created_at=base_date + timedelta(days=index),
            ))

    tie_date = base_date + timedelta(days=10)
    tasks_to_add.extend([
        models.Task(
            board=board, title="other:high-newer-a", status="submitted", priority="high",
            created_by_admin_id=other_author.id, created_at=tie_date,
        ),
        models.Task(
            board=board, title="other:high-newer-b", status="submitted", priority="high",
            created_by_admin_id=other_author.id, created_at=tie_date,
        ),
    ])
    db.add_all(tasks_to_add)
    db.commit()
    monkeypatch.setattr(tasks, "_can_develop_tasks", lambda admin: admin.id == developer.id)
    monkeypatch.setattr(tasks, "_has_board_perm", lambda *_args: True)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    response = asyncio.run(tasks.api_get_task_board(board.id, "requests", False, db, developer))
    titles = [row["title"] for row in response["tasks"]]

    expected_priorities = ["ultra", "urgent", "high", "medium", "low", "none"]
    for group in ("private", "own", "other"):
        group_titles = [
            title for title in titles
            if title.startswith(f"{group}:") and title.split(":", 1)[1] in expected_priorities
        ]
        assert [title.split(":", 1)[1] for title in group_titles] == expected_priorities

    assert titles.index("private:ultra") < titles.index("own:ultra") < titles.index("other:ultra")
    tied_high_ids = [row["id"] for row in response["tasks"] if row["title"].startswith("other:high")]
    assert tied_high_ids == sorted(tied_high_ids, reverse=True)
    assert titles.index("other:high-newer-a") < titles.index("other:high")


def test_only_developers_can_create_private_system_requests(task_db, monkeypatch):
    db = task_db
    developer = _admin(1, superuser=False)
    regular_user = _admin(2, superuser=False)
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    db.add_all([developer, regular_user])
    db.commit()
    monkeypatch.setattr(tasks, "_has_board_perm", lambda *_args: True)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)
    monkeypatch.setattr(tasks, "_can_develop_tasks", lambda admin: admin.id == developer.id)

    created = asyncio.run(tasks.api_create_task(
        board.id,
        JsonRequest({"title": "Секретная заявка", "product_tag": "game", "is_private": True}),
        db,
        developer,
    ))

    assert created["task"]["is_private"] is True
    with pytest.raises(tasks.HTTPException) as error:
        asyncio.run(tasks.api_create_task(
            board.id,
            JsonRequest({"title": "Поддельная приватность", "product_tag": "site", "is_private": True}),
            db,
            regular_user,
        ))
    assert error.value.status_code == 403


def test_private_task_direct_read_endpoints_return_not_found(task_db, monkeypatch, tmp_path):
    db = task_db
    developer = _admin(1)
    regular_user = _admin(2, superuser=False)
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    task = models.Task(board=board, title="Секретная заявка", status="submitted", is_private=True)
    db.add_all([developer, regular_user, task])
    db.flush()
    attachment_path = tmp_path / "secret.txt"
    attachment_path.write_text("secret", encoding="utf-8")
    attachment = models.TaskAttachment(
        task_id=task.id, filename="secret.txt", stored_path=str(attachment_path),
    )
    comment = models.TaskComment(task_id=task.id, admin_id=regular_user.id, content="secret comment")
    db.add_all([attachment, comment])
    db.commit()
    monkeypatch.setattr(tasks, "_can_develop_tasks", lambda admin: admin.id == developer.id)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    endpoints = [
        lambda: asyncio.run(tasks.api_update_task(task.id, JsonRequest({"title": "Попытка правки"}), db, regular_user)),
        lambda: asyncio.run(tasks.api_list_task_comments(task.id, db, regular_user)),
        lambda: asyncio.run(tasks.api_get_task_activity(task.id, db, regular_user)),
        lambda: asyncio.run(tasks.api_list_task_attachments(task.id, db, regular_user)),
        lambda: asyncio.run(tasks.api_get_task_attachment_file(attachment.id, db, regular_user)),
        lambda: asyncio.run(tasks.api_delete_task_comment(task.id, comment.id, db, regular_user)),
    ]
    for endpoint in endpoints:
        with pytest.raises(tasks.HTTPException) as error:
            endpoint()
        assert error.value.status_code == 404


def test_private_tasks_do_not_change_public_board_count(task_db, monkeypatch):
    db = task_db
    developer = _admin(1)
    regular_user = _admin(2, superuser=False)
    board, _ = _board(db, 1, "Запросы функционала")
    board.board_type = "system"
    db.add_all([
        developer,
        regular_user,
        models.Task(board=board, title="Открытая", status="submitted"),
        models.Task(board=board, title="Секретная", status="submitted", is_private=True),
    ])
    db.commit()
    monkeypatch.setattr(tasks, "_can_develop_tasks", lambda admin: admin.id == developer.id)
    monkeypatch.setattr(tasks, "_has_board_perm", lambda *_args: True)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)
    monkeypatch.setattr(tasks, "check_permission", lambda *_args: False)

    public = asyncio.run(tasks.api_list_task_boards(db, regular_user))
    privileged = asyncio.run(tasks.api_list_task_boards(db, developer))

    assert public["boards"][0]["task_count"] == 1
    assert privileged["boards"][0]["task_count"] == 2


def test_custom_private_tasks_are_visible_to_board_editors_only(task_db, monkeypatch):
    db = task_db
    viewer = _admin(1, superuser=False)
    editor = _admin(2, superuser=False)
    board, column = _board(db, 1, "Кастомная")
    public = models.Task(board=board, column=column, title="Открытая", status="review")
    private = models.Task(board=board, column=column, title="Секретная", status="review", is_private=True)
    db.add_all([
        viewer,
        editor,
        public,
        private,
        models.TaskBoardPermission(
            board_id=board.id, grantee_type="user", grantee_key=str(viewer.id),
            can_view=True, can_edit=False,
        ),
        models.TaskBoardPermission(
            board_id=board.id, grantee_type="user", grantee_key=str(editor.id),
            can_view=True, can_edit=True,
        ),
    ])
    db.commit()
    monkeypatch.setattr(tasks, "check_permission", lambda *_args: False)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    viewer_board = asyncio.run(tasks.api_get_task_board(board.id, None, False, db, viewer))
    editor_board = asyncio.run(tasks.api_get_task_board(board.id, None, False, db, editor))

    assert [row["title"] for row in viewer_board["tasks"]] == ["Открытая"]
    assert [row["title"] for row in editor_board["tasks"]] == ["Секретная", "Открытая"]
    assert editor_board["can_manage_private_tasks"] is True
    assert viewer_board["can_manage_private_tasks"] is False

    viewer_count = asyncio.run(tasks.api_list_task_boards(db, viewer))["boards"][0]["task_count"]
    editor_count = asyncio.run(tasks.api_list_task_boards(db, editor))["boards"][0]["task_count"]
    assert viewer_count == 1
    assert editor_count == 2


def test_custom_private_task_direct_access_is_hidden_from_viewer(task_db, monkeypatch):
    db = task_db
    viewer = _admin(1, superuser=False)
    outsider = _admin(2, superuser=False)
    board, column = _board(db, 1, "Кастомная")
    board.is_private = True
    private = models.Task(board=board, column=column, title="Секретная", is_private=True)
    db.add_all([
        viewer,
        outsider,
        private,
        models.TaskBoardPermission(
            board_id=board.id, grantee_type="user", grantee_key=str(viewer.id),
            can_view=True, can_edit=False,
        ),
    ])
    db.commit()
    monkeypatch.setattr(tasks, "check_permission", lambda *_args: False)

    with pytest.raises(tasks.HTTPException) as error:
        tasks.get_task_or_404(db, private.id, viewer)
    assert error.value.status_code == 404

    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)
    with pytest.raises(tasks.HTTPException) as board_error:
        asyncio.run(tasks.api_get_task_board(board.id, None, False, db, outsider))
    assert board_error.value.status_code == 404


def test_custom_private_task_can_be_created_and_toggled_by_board_editor(task_db, monkeypatch):
    db = task_db
    editor = _admin(1, superuser=False)
    viewer = _admin(2, superuser=False)
    board, column = _board(db, 1, "Кастомная")
    db.add_all([
        editor,
        viewer,
        models.TaskBoardPermission(
            board_id=board.id, grantee_type="user", grantee_key=str(editor.id),
            can_view=True, can_create=True, can_edit=True,
        ),
        models.TaskBoardPermission(
            board_id=board.id, grantee_type="user", grantee_key=str(viewer.id),
            can_view=True, can_create=True, can_edit=False,
        ),
    ])
    db.commit()
    monkeypatch.setattr(tasks, "check_permission", lambda *_args: False)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    created = asyncio.run(tasks.api_create_task(
        board.id, JsonRequest({"title": "Приватная", "is_private": True}), db, editor,
    ))
    assert created["task"]["is_private"] is True

    asyncio.run(tasks.api_update_task(
        created["task"]["id"], JsonRequest({"is_private": False}), db, editor,
    ))
    assert db.get(models.Task, created["task"]["id"]).is_private is False

    with pytest.raises(tasks.HTTPException) as error:
        asyncio.run(tasks.api_create_task(
            board.id, JsonRequest({"title": "Нельзя сделать приватной", "is_private": True}), db, viewer,
        ))
    assert error.value.status_code == 403


def test_private_board_requires_explicit_view_grant(task_db, monkeypatch):
    db = task_db
    ordinary = _admin(1, superuser=False)
    granted = _admin(2, superuser=False)
    private_board, _ = _board(db, 1, "Закрытая")
    private_board.is_private = True
    db.add_all([
        ordinary,
        granted,
        models.TaskBoardPermission(
            board_id=private_board.id, grantee_type="user", grantee_key=str(granted.id),
            can_view=True,
        ),
    ])
    db.commit()
    monkeypatch.setattr(tasks, "check_permission", lambda _admin, _page, action: action == "view")

    assert tasks._has_board_perm(db, ordinary, private_board.id, "view") is False
    assert tasks._has_board_perm(db, granted, private_board.id, "view") is True
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)
    visible_to_ordinary = asyncio.run(tasks.api_list_task_boards(db, ordinary))["boards"]
    visible_to_granted = asyncio.run(tasks.api_list_task_boards(db, granted))["boards"]
    assert visible_to_ordinary == []
    assert [row["id"] for row in visible_to_granted] == [private_board.id]


def test_board_list_pins_system_board_and_hides_inaccessible_private_counts(task_db, monkeypatch):
    from datetime import datetime

    db = task_db
    viewer = _admin(1, superuser=False)
    public_board, public_column = _board(db, 1, "Позже")
    private_board, private_column = _board(db, 2, "Закрытая")
    private_board.is_private = True
    system_board, system_column = _board(db, 3, "Запросы функционала")
    system_board.board_type = "system"
    system_board.created_at = datetime(2020, 1, 1)
    public_board.created_at = datetime(2026, 1, 1)
    private_board.created_at = datetime(2025, 1, 1)
    db.add_all([
        viewer,
        models.Task(board=public_board, column=public_column, title="Видимая", is_private=False),
        models.Task(board=public_board, column=public_column, title="Секретная", is_private=True),
        models.Task(board=private_board, column=private_column, title="Скрытая доска"),
        models.Task(board=system_board, column=system_column, title="Заявка"),
        models.TaskBoardPermission(
            board_id=private_board.id, grantee_type="user", grantee_key=str(viewer.id),
            can_view=False,
        ),
    ])
    db.commit()
    monkeypatch.setattr(tasks, "check_permission", lambda _admin, _page, action: action == "view")
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    response = asyncio.run(tasks.api_list_task_boards(db, viewer))

    assert [row["name"] for row in response["boards"]] == ["Запросы функционала", "Позже"]
    assert response["boards"][1]["task_count"] == 1


def test_custom_board_tasks_sort_by_groups_priority_then_newest(task_db, monkeypatch):
    from datetime import datetime, timedelta

    db = task_db
    viewer = _admin(1, superuser=False)
    other = _admin(2, superuser=False)
    board, column = _board(db, 1, "Кастомная")
    base = datetime(2026, 1, 1)
    tasks_to_add = [viewer, other]
    priorities = ["ultra", "urgent", "high", "medium", "low", None]
    for group in ("private", "own", "other"):
        for index, priority in enumerate(priorities):
            tasks_to_add.append(models.Task(
                board=board,
                column=column,
                title=f"{group}:{priority or 'none'}",
                status="review",
                is_private=group == "private",
                priority=priority,
                created_by_admin_id=viewer.id if group == "own" else other.id,
                created_at=base + timedelta(days=index),
                sort_order=100 - index,
            ))
    db.add_all(tasks_to_add)
    db.commit()
    monkeypatch.setattr(tasks, "_has_board_perm", lambda *_args: True)
    monkeypatch.setattr(tasks, "check_permission", lambda *_args: False)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    response = asyncio.run(tasks.api_get_task_board(board.id, None, False, db, viewer))
    titles = [row["title"] for row in response["tasks"]]
    expected_priorities = ["ultra", "urgent", "high", "medium", "low", "none"]

    for group in ("private", "own", "other"):
        group_priorities = [
            title.split(":", 1)[1]
            for title in titles
            if title.startswith(f"{group}:")
        ]
        assert group_priorities == expected_priorities
    assert titles.index("private:ultra") < titles.index("own:ultra") < titles.index("other:ultra")

    tie_time = base + timedelta(days=20)
    tied = [
        models.Task(
            board=board, column=column, title=f"other:tie-{index}", status="review",
            priority="high", created_by_admin_id=other.id, created_at=tie_time,
        )
        for index in range(2)
    ]
    db.add_all(tied)
    db.commit()
    response = asyncio.run(tasks.api_get_task_board(board.id, None, False, db, viewer))
    tied_ids = [row["id"] for row in response["tasks"] if row["title"].startswith("other:tie-")]
    assert tied_ids == sorted(tied_ids, reverse=True)


def test_custom_drag_move_changes_column_without_manual_sort_order(task_db, monkeypatch):
    db = task_db
    admin = _admin()
    board, first = _board(db, 1, "Кастомная")
    second = models.TaskColumn(board=board, name="Готово", sort_order=1)
    task = models.Task(board=board, column=first, title="Задача", sort_order=7)
    db.add_all([admin, second, task])
    db.commit()
    monkeypatch.setattr(tasks, "check_permission", lambda *_args: True)
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    asyncio.run(tasks.api_reorder_tasks(
        board.id,
        JsonRequest({"columns": {str(first.id): [task.id]}}),
        db,
        admin,
    ))
    assert task.column_id == first.id
    assert task.sort_order == 7

    asyncio.run(tasks.api_reorder_tasks(
        board.id,
        JsonRequest({"columns": {str(second.id): [task.id]}}),
        db,
        admin,
    ))

    assert task.column_id == second.id
    assert task.sort_order == 7


def test_private_board_can_be_enabled_and_disabled_in_settings(task_db, monkeypatch):
    db = task_db
    manager = _admin(1)
    board, _ = _board(db, 1, "Кастомная")
    db.add(manager)
    db.commit()
    monkeypatch.setattr(tasks, "require_tasks_page", lambda *_args: None)

    asyncio.run(tasks.api_update_task_board(board.id, JsonRequest({"is_private": True}), db, manager))
    assert db.get(models.TaskBoard, board.id).is_private is True
    assert tasks.serialize_board(db.get(models.TaskBoard, board.id))["is_private"] is True

    asyncio.run(tasks.api_update_task_board(board.id, JsonRequest({"is_private": False}), db, manager))
    assert db.get(models.TaskBoard, board.id).is_private is False


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
