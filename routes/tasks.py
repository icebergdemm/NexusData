"""
Tasks — a single global "Задачи" page: Notion/Trello-style Kanban boards
with named/colored columns, tasks with a fixed rich property set (deadline,
priority, tags, assignee admin/department, description, comments), and
drag-and-drop reordering both of columns and of tasks within/across columns.

A board's department_id is purely an organizational tag (filter/label) —
never an access-control boundary. Visibility is all-or-nothing via the
site-wide "tasks" permission page; edit/delete are own-task-scoped (creator
or assignee) unless the admin holds the "_all" variant. See
permissions_registry.py's "tasks" entry for the full action list.
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional
from datetime import datetime, timezone
import logging

from database import get_db
from security import check_permission
from models import AdminUser, AdminSession, Department, TaskBoard, TaskColumn, Task, TaskComment

logger = logging.getLogger(__name__)
router = APIRouter()

TASK_PRIORITIES = {"low", "medium", "high", "urgent"}


# ── Auth (self-contained, mirrors routes/departments.py's dependency) ───────

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
        if session and (session.expires_at is None or session.expires_at > now):
            admin = (
                db.query(AdminUser)
                .options(joinedload(AdminUser.privilege_group))
                .filter(AdminUser.id == session.admin_id)
                .first()
            )
            if admin:
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def require_tasks_page(admin: AdminUser, action: str = "view"):
    if not check_permission(admin, "tasks", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _task_is_owner(admin: AdminUser, task: Task) -> bool:
    return task.created_by_admin_id == admin.id or task.assignee_admin_id == admin.id


def _has_task_action(admin: AdminUser, own_action: str, all_action: str, task: Task) -> bool:
    if check_permission(admin, "tasks", all_action):
        return True
    return _task_is_owner(admin, task) and check_permission(admin, "tasks", own_action)


def get_board_or_404(db: Session, board_id: int) -> TaskBoard:
    board = db.query(TaskBoard).filter(TaskBoard.id == board_id, TaskBoard.is_archived.is_(False)).first()
    if not board:
        raise HTTPException(status_code=404, detail="Доска не найдена")
    return board


def get_column_or_404(db: Session, board_id: int, column_id: int) -> TaskColumn:
    col = db.query(TaskColumn).filter(TaskColumn.id == column_id, TaskColumn.board_id == board_id).first()
    if not col:
        raise HTTPException(status_code=404, detail="Колонка не найдена")
    return col


def get_task_or_404(db: Session, task_id: int) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


def _parse_deadline(raw) -> Optional[datetime]:
    """Accepts either a bare "YYYY-MM-DDTHH:MM[:SS]" (the frontend's
    <input type="datetime-local"> value, stored/redisplayed as-is with no
    timezone math — this app's whole audience is one timezone, so a UTC
    round-trip would just make the value redisplay wrong) or a tz-aware ISO
    string, converted to naive UTC."""
    if not raw:
        return None
    text = str(raw)
    if len(text) == 16:  # "YYYY-MM-DDTHH:MM" — fromisoformat needs seconds pre-3.11
        text += ":00"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Некорректный формат дедлайна")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ── Serializers ──────────────────────────────────────────────────────────────

def serialize_board(board: TaskBoard, task_count: Optional[int] = None) -> dict:
    return {
        "id": board.id,
        "name": board.name,
        "description": board.description,
        "icon": board.icon,
        "color": board.color,
        "department_id": board.department_id,
        "department_name": board.department.name if board.department else None,
        "created_by_name": board.created_by.discord_username if board.created_by else None,
        "created_at": board.created_at.isoformat() if board.created_at else None,
        "task_count": task_count,
    }


def serialize_column(col: TaskColumn) -> dict:
    return {
        "id": col.id, "board_id": col.board_id, "name": col.name,
        "color": col.color, "sort_order": col.sort_order,
    }


def serialize_task(task: Task, comment_count: int = 0) -> dict:
    return {
        "id": task.id,
        "board_id": task.board_id,
        "column_id": task.column_id,
        "title": task.title,
        "description": task.description,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "priority": task.priority,
        "tags": task.tags or [],
        "assignee_admin_id": task.assignee_admin_id,
        "assignee_admin_name": task.assignee_admin.discord_username if task.assignee_admin else None,
        "assignee_department_id": task.assignee_department_id,
        "assignee_department_name": task.assignee_department.name if task.assignee_department else None,
        "created_by_admin_id": task.created_by_admin_id,
        "created_by_name": task.created_by.discord_username if task.created_by else None,
        "sort_order": task.sort_order,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "comment_count": comment_count,
    }


def serialize_comment(c: TaskComment) -> dict:
    return {
        "id": c.id,
        "task_id": c.task_id,
        "admin_id": c.admin_id,
        "admin_name": c.admin.discord_username if c.admin else None,
        "admin_avatar": c.admin.discord_avatar if c.admin else None,
        "content": c.content,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


# ── Boards ───────────────────────────────────────────────────────────────────

@router.get("/api/v1/admin/tasks/boards")
async def api_list_task_boards(db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    boards = (
        db.query(TaskBoard)
        .options(joinedload(TaskBoard.department), joinedload(TaskBoard.created_by))
        .filter(TaskBoard.is_archived.is_(False))
        .order_by(TaskBoard.created_at.desc())
        .all()
    )
    counts = dict(
        db.query(Task.board_id, func.count(Task.id))
        .filter(Task.board_id.in_([b.id for b in boards]))
        .group_by(Task.board_id)
        .all()
    ) if boards else {}
    return {
        "boards": [serialize_board(b, counts.get(b.id, 0)) for b in boards],
        "can_manage_boards": check_permission(current_admin, "tasks", "manage_boards"),
        "can_create": check_permission(current_admin, "tasks", "create"),
    }


@router.post("/api/v1/admin/tasks/boards")
async def api_create_task_board(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "manage_boards")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название доски")

    department_id = body.get("department_id")
    if department_id is not None:
        dept = db.query(Department).filter(Department.id == department_id, Department.is_archived.is_(False)).first()
        if not dept:
            raise HTTPException(status_code=404, detail="Отдел не найден")

    board = TaskBoard(
        name=name,
        description=(body.get("description") or "").strip() or None,
        icon=body.get("icon") or "📋",
        color=body.get("color") or "#1a68de",
        department_id=department_id,
        created_by_admin_id=current_admin.id,
    )
    db.add(board)
    db.flush()
    # Seed a default column so a brand-new board isn't an empty dead end.
    db.add(TaskColumn(board_id=board.id, name="Новые", color="#5A646E", sort_order=0))
    db.commit()
    db.refresh(board)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "task_board_create", {"board_id": board.id, "name": name})
    return {"status": "ok", "board": serialize_board(board, 0)}


@router.get("/api/v1/admin/tasks/boards/{board_id}")
async def api_get_task_board(board_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    board = get_board_or_404(db, board_id)
    columns = db.query(TaskColumn).filter(TaskColumn.board_id == board.id).order_by(TaskColumn.sort_order).all()
    tasks = (
        db.query(Task)
        .options(joinedload(Task.assignee_admin), joinedload(Task.assignee_department), joinedload(Task.created_by))
        .filter(Task.board_id == board.id)
        .order_by(Task.sort_order)
        .all()
    )
    comment_counts = dict(
        db.query(TaskComment.task_id, func.count(TaskComment.id))
        .filter(TaskComment.task_id.in_([t.id for t in tasks]))
        .group_by(TaskComment.task_id)
        .all()
    ) if tasks else {}

    return {
        "board": serialize_board(board),
        "columns": [serialize_column(c) for c in columns],
        "tasks": [serialize_task(t, comment_counts.get(t.id, 0)) for t in tasks],
        "can_manage_boards": check_permission(current_admin, "tasks", "manage_boards"),
        "can_create": check_permission(current_admin, "tasks", "create"),
        "can_edit_all": check_permission(current_admin, "tasks", "edit_all"),
        "can_delete_all": check_permission(current_admin, "tasks", "delete_all"),
    }


@router.patch("/api/v1/admin/tasks/boards/{board_id}")
async def api_update_task_board(board_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "manage_boards")
    board = get_board_or_404(db, board_id)
    body = await request.json()

    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Название не может быть пустым")
        board.name = name
    if "description" in body:
        board.description = (body.get("description") or "").strip() or None
    if "icon" in body:
        board.icon = body.get("icon") or "📋"
    if "color" in body:
        board.color = body.get("color") or "#1a68de"
    if "department_id" in body:
        department_id = body.get("department_id")
        if department_id is not None:
            dept = db.query(Department).filter(Department.id == department_id, Department.is_archived.is_(False)).first()
            if not dept:
                raise HTTPException(status_code=404, detail="Отдел не найден")
        board.department_id = department_id

    db.commit()
    return {"status": "ok", "board": serialize_board(board)}


@router.delete("/api/v1/admin/tasks/boards/{board_id}")
async def api_archive_task_board(board_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "manage_boards")
    board = get_board_or_404(db, board_id)
    board.is_archived = True
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "task_board_archive", {"board_id": board_id})
    return {"status": "ok"}


# ── Columns ──────────────────────────────────────────────────────────────────

@router.post("/api/v1/admin/tasks/boards/{board_id}/columns")
async def api_create_task_column(board_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "manage_boards")
    board = get_board_or_404(db, board_id)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название колонки")

    max_order = db.query(func.max(TaskColumn.sort_order)).filter(TaskColumn.board_id == board.id).scalar() or 0
    col = TaskColumn(board_id=board.id, name=name, color=body.get("color") or "#5A646E", sort_order=max_order + 1)
    db.add(col)
    db.commit()
    db.refresh(col)
    return {"status": "ok", "column": serialize_column(col)}


@router.patch("/api/v1/admin/tasks/boards/{board_id}/columns/{column_id}")
async def api_update_task_column(board_id: int, column_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "manage_boards")
    col = get_column_or_404(db, board_id, column_id)
    body = await request.json()

    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Название не может быть пустым")
        col.name = name
    if "color" in body:
        col.color = body.get("color") or "#5A646E"

    db.commit()
    return {"status": "ok", "column": serialize_column(col)}


@router.post("/api/v1/admin/tasks/boards/{board_id}/columns/reorder")
async def api_reorder_task_columns(board_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "manage_boards")
    board = get_board_or_404(db, board_id)
    body = await request.json()
    order = body.get("column_ids") or []

    cols = {c.id: c for c in db.query(TaskColumn).filter(TaskColumn.board_id == board.id).all()}
    for idx, cid in enumerate(order):
        col = cols.get(int(cid))
        if col:
            col.sort_order = idx
    db.commit()
    return {"status": "ok"}


@router.delete("/api/v1/admin/tasks/boards/{board_id}/columns/{column_id}")
async def api_delete_task_column(board_id: int, column_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "manage_boards")
    col = get_column_or_404(db, board_id, column_id)

    remaining = db.query(TaskColumn).filter(TaskColumn.board_id == board_id).count()
    if remaining <= 1:
        raise HTTPException(status_code=400, detail="Нельзя удалить последнюю колонку доски")
    task_count = db.query(Task).filter(Task.column_id == column_id).count()
    if task_count:
        raise HTTPException(status_code=400, detail="В колонке есть задачи — перенесите или удалите их перед удалением колонки")

    db.delete(col)
    db.commit()
    return {"status": "ok"}


# ── Tasks ────────────────────────────────────────────────────────────────────

@router.post("/api/v1/admin/tasks/boards/{board_id}/tasks")
async def api_create_task(board_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "create")
    board = get_board_or_404(db, board_id)
    body = await request.json()

    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Укажите название задачи")

    column = None
    if body.get("column_id"):
        column = db.query(TaskColumn).filter(TaskColumn.id == body.get("column_id"), TaskColumn.board_id == board.id).first()
    if not column:
        column = db.query(TaskColumn).filter(TaskColumn.board_id == board.id).order_by(TaskColumn.sort_order).first()
    if not column:
        raise HTTPException(status_code=400, detail="На доске нет колонок")

    priority = body.get("priority") or None
    if priority and priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=400, detail="Некорректный приоритет")

    assignee_admin_id = body.get("assignee_admin_id")
    if assignee_admin_id is not None and not db.query(AdminUser).filter(AdminUser.id == assignee_admin_id).first():
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    assignee_department_id = body.get("assignee_department_id")
    if assignee_department_id is not None and not db.query(Department).filter(Department.id == assignee_department_id, Department.is_archived.is_(False)).first():
        raise HTTPException(status_code=404, detail="Отдел не найден")

    deadline = _parse_deadline(body.get("deadline"))
    max_order = db.query(func.max(Task.sort_order)).filter(Task.column_id == column.id).scalar() or 0

    task = Task(
        board_id=board.id, column_id=column.id, title=title,
        description=(body.get("description") or "").strip() or None,
        deadline=deadline, priority=priority,
        tags=[str(t).strip() for t in (body.get("tags") or []) if str(t).strip()][:20],
        assignee_admin_id=assignee_admin_id, assignee_department_id=assignee_department_id,
        created_by_admin_id=current_admin.id, sort_order=max_order + 1,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "task_create", {"task_id": task.id, "board_id": board.id, "title": title})
    return {"status": "ok", "task": serialize_task(task, 0)}


@router.patch("/api/v1/admin/tasks/tasks/{task_id}")
async def api_update_task(task_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    task = get_task_or_404(db, task_id)
    if not _has_task_action(current_admin, "edit", "edit_all", task):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    body = await request.json()

    if "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Название не может быть пустым")
        task.title = title
    if "description" in body:
        task.description = (body.get("description") or "").strip() or None
    if "priority" in body:
        priority = body.get("priority") or None
        if priority and priority not in TASK_PRIORITIES:
            raise HTTPException(status_code=400, detail="Некорректный приоритет")
        task.priority = priority
    if "tags" in body:
        task.tags = [str(t).strip() for t in (body.get("tags") or []) if str(t).strip()][:20]
    if "deadline" in body:
        task.deadline = _parse_deadline(body.get("deadline"))
    if "assignee_admin_id" in body:
        assignee_admin_id = body.get("assignee_admin_id")
        if assignee_admin_id is not None and not db.query(AdminUser).filter(AdminUser.id == assignee_admin_id).first():
            raise HTTPException(status_code=404, detail="Сотрудник не найден")
        task.assignee_admin_id = assignee_admin_id
    if "assignee_department_id" in body:
        assignee_department_id = body.get("assignee_department_id")
        if assignee_department_id is not None and not db.query(Department).filter(Department.id == assignee_department_id, Department.is_archived.is_(False)).first():
            raise HTTPException(status_code=404, detail="Отдел не найден")
        task.assignee_department_id = assignee_department_id
    if "column_id" in body:
        column = db.query(TaskColumn).filter(TaskColumn.id == body.get("column_id"), TaskColumn.board_id == task.board_id).first()
        if not column:
            raise HTTPException(status_code=404, detail="Колонка не найдена")
        task.column_id = column.id
    if "sort_order" in body:
        task.sort_order = int(body.get("sort_order") or 0)

    db.commit()
    comment_count = db.query(TaskComment).filter(TaskComment.task_id == task.id).count()
    return {"status": "ok", "task": serialize_task(task, comment_count)}


@router.delete("/api/v1/admin/tasks/tasks/{task_id}")
async def api_delete_task(task_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    task = get_task_or_404(db, task_id)
    if not _has_task_action(current_admin, "delete", "delete_all", task):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    db.delete(task)
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "task_delete", {"task_id": task_id})
    return {"status": "ok"}


@router.post("/api/v1/admin/tasks/boards/{board_id}/tasks/reorder")
async def api_reorder_tasks(board_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    """Bulk reposition after a drag-drop. Body: {"columns": {column_id: [task_id, ...]}}
    — normally the moved-from and moved-to columns' full new task-id order.
    Tasks the caller can't edit are silently skipped rather than erroring the
    whole batch, so one restricted card doesn't block reordering the rest."""
    require_tasks_page(current_admin, "view")
    board = get_board_or_404(db, board_id)
    body = await request.json()
    columns_payload = body.get("columns") or {}

    valid_column_ids = {c.id for c in db.query(TaskColumn).filter(TaskColumn.board_id == board.id).all()}
    for column_id_raw, task_ids in columns_payload.items():
        try:
            column_id = int(column_id_raw)
        except (TypeError, ValueError):
            continue
        if column_id not in valid_column_ids:
            continue
        tasks = {
            t.id: t for t in db.query(Task).filter(Task.id.in_(task_ids), Task.board_id == board.id).all()
        }
        for idx, tid in enumerate(task_ids):
            t = tasks.get(int(tid))
            if not t or not _has_task_action(current_admin, "edit", "edit_all", t):
                continue
            t.column_id = column_id
            t.sort_order = idx

    db.commit()
    return {"status": "ok"}


# ── Comments ─────────────────────────────────────────────────────────────────

@router.get("/api/v1/admin/tasks/tasks/{task_id}/comments")
async def api_list_task_comments(task_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    task = get_task_or_404(db, task_id)
    comments = (
        db.query(TaskComment)
        .options(joinedload(TaskComment.admin))
        .filter(TaskComment.task_id == task.id)
        .order_by(TaskComment.created_at)
        .all()
    )
    return {"comments": [serialize_comment(c) for c in comments]}


@router.post("/api/v1/admin/tasks/tasks/{task_id}/comments")
async def api_add_task_comment(task_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    task = get_task_or_404(db, task_id)
    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Комментарий не может быть пустым")

    comment = TaskComment(task_id=task.id, admin_id=current_admin.id, content=content[:2000])
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return {"status": "ok", "comment": serialize_comment(comment)}


@router.delete("/api/v1/admin/tasks/tasks/{task_id}/comments/{comment_id}")
async def api_delete_task_comment(task_id: int, comment_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    comment = db.query(TaskComment).filter(TaskComment.id == comment_id, TaskComment.task_id == task_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    if comment.admin_id != current_admin.id and not check_permission(current_admin, "tasks", "edit_all"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    db.delete(comment)
    db.commit()
    return {"status": "ok"}


# ── Assignee candidates ──────────────────────────────────────────────────────

@router.get("/api/v1/admin/tasks/assignee-candidates")
async def api_task_assignee_candidates(db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    admins = db.query(AdminUser).filter(AdminUser.is_deleted == 0).order_by(AdminUser.discord_username).all()
    departments = db.query(Department).filter(Department.is_archived.is_(False)).order_by(Department.name).all()
    return {
        "admins": [{"id": a.id, "name": a.discord_username} for a in admins],
        "departments": [{"id": d.id, "name": d.name, "icon": d.icon} for d in departments],
    }
