"""
Tasks — a single global "Задачи" page: Notion/Trello-style Kanban boards
with named/colored columns, tasks with a fixed rich property set (deadline,
priority, tags, assignee admin/department, description, comments), and
drag-and-drop reordering both of columns and of tasks within/across columns.

A board's department_id is purely an organizational tag (filter/label) —
never an access-control boundary. Access is governed by per-board rules
(TaskBoardPermission, see _has_board_perm) when they match the user, with
site-wide "tasks" permissions as the fallback; edit/delete are additionally
own-task-scoped (creator or assignee) via "edit"/"delete" unless the admin
holds the "_all" variant. See permissions_registry.py's "tasks" entry for
the full action list.
"""

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import Optional, List
from datetime import datetime, timezone
import logging
import os
import uuid
import shutil

from database import get_db
from security import check_permission
from config import get_settings
from models import AdminUser, AdminSession, Department, TaskBoard, TaskColumn, Task, TaskComment, TaskBoardPermission, TaskAttachment, TaskActivityLog, TaskBoardMessage, TaskBoardMessageAttachment

logger = logging.getLogger(__name__)
router = APIRouter()

TASK_PRIORITIES = {"low", "medium", "high", "urgent"}


# ── Auth (self-contained, mirrors routes/departments.py's dependency) ───────

def _dept_level_matches(db: Session, admin: AdminUser, grantee_key: str) -> bool:
    """True if admin holds the department level encoded as "deptId:levelKey"."""
    try:
        dept_id_str, level_key = grantee_key.split(":", 1)
        dept_id = int(dept_id_str)
    except (ValueError, AttributeError):
        return False
    from models import DepartmentMember
    m = db.query(DepartmentMember).filter(
        DepartmentMember.department_id == dept_id,
        DepartmentMember.admin_id == admin.id,
    ).first()
    return bool(m and level_key in (m.held_level_keys or []))


def _perm_matches_admin(db: Session, admin: AdminUser, p) -> bool:
    if p.grantee_type == "user":
        return str(p.grantee_key) == str(admin.id)
    if p.grantee_type == "group_nd":
        return bool(admin.privilege_group_id) and str(p.grantee_key) == str(admin.privilege_group_id)
    if p.grantee_type == "dept_level":
        return _dept_level_matches(db, admin, p.grantee_key)
    return False


def _has_any_board_view(db: Session, admin: AdminUser) -> bool:
    if admin.is_superuser:
        return True
    from models import TaskBoardPermission
    perms = db.query(TaskBoardPermission).filter(TaskBoardPermission.can_view.is_(True)).all()
    for p in perms:
        if _perm_matches_admin(db, admin, p):
            return True
    return False


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
                try:
                    admin._has_task_board_view = _has_any_board_view(db, admin)
                except Exception:
                    admin._has_task_board_view = False
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def require_tasks_page(admin: AdminUser, action: str = "view"):
    # п. “доступ к вкладке Задачи если есть право на доску”
    if action == "view" and getattr(admin, "_has_task_board_view", False):
        return
    if not check_permission(admin, "tasks", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _task_is_owner(admin: AdminUser, task: Task) -> bool:
    return task.created_by_admin_id == admin.id or task.assignee_admin_id == admin.id


def _can_edit_task(db: Session, admin: AdminUser, task: Task) -> bool:
    """Любую задачу доски — по правилу доски (can_edit) или tasks:edit_all;
    свою (созданную/назначенную) — ещё и по tasks:edit."""
    if _has_board_perm(db, admin, task.board_id, "edit"):
        return True
    return _task_is_owner(admin, task) and check_permission(admin, "tasks", "edit")


def _can_delete_task(db: Session, admin: AdminUser, task: Task) -> bool:
    if _has_board_perm(db, admin, task.board_id, "delete"):
        return True
    return _task_is_owner(admin, task) and check_permission(admin, "tasks", "delete")


def _log_task_activity(db: Session, task_id: int, admin_id: Optional[int], action: str, detail: Optional[str] = None):
    try:
        db.add(TaskActivityLog(task_id=task_id, admin_id=admin_id, action=action, detail=(detail or "")[:2000]))
        db.flush()
    except Exception:
        pass


def _has_board_perm(db: Session, admin: AdminUser, board_id: int, need: str) -> bool:
    """Права по доске. Правила доски (TaskBoardPermission) имеют приоритет над
    глобальными tasks-правами: если для доски есть правила и хотя бы одно
    подходит пользователю, действует самый конкретный подходивший уровень
    (user → group_nd → dept_level, внутри уровня — union) — в т.ч. когда он
    запрещает действие. Глобальные права применяются только как fallback,
    когда правил нет или ни одно не касается этого пользователя:
      view   ← tasks:view, create ← tasks:create,
      edit   ← tasks:edit_all, delete ← tasks:delete_all
    (правка/удаление СВОИХ задач — отдельно, см. _can_edit_task/_can_delete_task)."""
    if admin.is_superuser:
        return True
    if check_permission(admin, "tasks", "manage_boards"):
        return True
    col = {"view": "can_view", "create": "can_create", "edit": "can_edit", "delete": "can_delete"}.get(need, need)
    perms = db.query(TaskBoardPermission).filter(TaskBoardPermission.board_id == board_id).all()
    matched_by_level: dict = {}
    for p in perms:
        if _perm_matches_admin(db, admin, p):
            matched_by_level.setdefault(p.grantee_type, []).append(p)
    for level in ("user", "group_nd", "dept_level"):
        rows = matched_by_level.get(level)
        if rows:
            return any(getattr(p, col, False) for p in rows)
    # fallback на глобальные tasks права (правил нет / не про этого пользователя)
    if need == "view":
        return check_permission(admin, "tasks", "view")
    if need == "create":
        return check_permission(admin, "tasks", "create")
    if need == "edit":
        return check_permission(admin, "tasks", "edit_all")
    if need == "delete":
        return check_permission(admin, "tasks", "delete_all")
    return False


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


def serialize_task(task: Task, comment_count: int = 0, attachment_count: int = 0) -> dict:
    preview_url = None
    if task.preview_attachment_id:
        preview_url = f"/api/v1/admin/tasks/attachments/{task.preview_attachment_id}/file"
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
        "status": getattr(task, "status", "review"),
        "is_hidden": bool(getattr(task, "is_hidden", False)),
        "rejection_reason": getattr(task, "rejection_reason", None),
        "preview_attachment_id": getattr(task, "preview_attachment_id", None),
        "preview_url": preview_url,
        "attachment_count": attachment_count,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "comment_count": comment_count,
    }


def serialize_attachment(a: TaskAttachment) -> dict:
    return {
        "id": a.id, "task_id": a.task_id, "filename": a.filename, "mime_type": a.mime_type,
        "file_size": a.file_size, "is_preview": bool(a.is_preview),
        "url": f"/api/v1/admin/tasks/attachments/{a.id}/file",
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "uploader_name": a.uploader.discord_username if a.uploader else None,
    }


def serialize_activity(a: TaskActivityLog) -> dict:
    return {
        "id": a.id, "task_id": a.task_id, "admin_id": a.admin_id,
        "admin_name": a.admin.discord_username if a.admin else None,
        "action": a.action, "detail": a.detail,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def serialize_board_message(m: TaskBoardMessage, attachments=None) -> dict:
    return {
        "id": m.id, "board_id": m.board_id, "author_admin_id": m.author_admin_id,
        "author_name": m.author.discord_username if m.author else None,
        "author_avatar": m.author.discord_avatar if m.author else None,
        "content": m.content, "reply_to_id": m.reply_to_id,
        "is_pinned": bool(m.is_pinned),
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "attachments": attachments or [],
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
    # показываем только доски, которые пользователю реально видны
    # (правила доски приоритетнее глобального tasks:view)
    if not current_admin.is_superuser:
        boards = [b for b in boards if _has_board_perm(db, current_admin, b.id, "view")]
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
async def api_get_task_board(board_id: int, show_hidden: bool = False, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    if not _has_board_perm(db, current_admin, board_id, "view"):
        raise HTTPException(status_code=403, detail="Нет доступа к доске")
    board = get_board_or_404(db, board_id)
    columns = db.query(TaskColumn).filter(TaskColumn.board_id == board.id).order_by(TaskColumn.sort_order).all()
    q = db.query(Task).options(joinedload(Task.assignee_admin), joinedload(Task.assignee_department), joinedload(Task.created_by)).filter(Task.board_id == board.id)
    if not show_hidden:
        q = q.filter(Task.is_hidden.is_(False))
    tasks = q.order_by(Task.sort_order).all()
    comment_counts = dict(
        db.query(TaskComment.task_id, func.count(TaskComment.id))
        .filter(TaskComment.task_id.in_([t.id for t in tasks]))
        .group_by(TaskComment.task_id)
        .all()
    ) if tasks else {}
    attachment_counts = dict(
        db.query(TaskAttachment.task_id, func.count(TaskAttachment.id))
        .filter(TaskAttachment.task_id.in_([t.id for t in tasks]))
        .group_by(TaskAttachment.task_id)
        .all()
    ) if tasks else {}

    return {
        "board": serialize_board(board),
        "columns": [serialize_column(c) for c in columns],
        "tasks": [serialize_task(t, comment_counts.get(t.id, 0), attachment_counts.get(t.id, 0)) for t in tasks],
        "can_manage_boards": check_permission(current_admin, "tasks", "manage_boards"),
        "can_create": _has_board_perm(db, current_admin, board_id, "create"),
        "can_edit_all": _has_board_perm(db, current_admin, board_id, "edit"),
        "can_delete_all": _has_board_perm(db, current_admin, board_id, "delete"),
        "my_board_perms": {
            "view": _has_board_perm(db, current_admin, board_id, "view"),
            "create": _has_board_perm(db, current_admin, board_id, "create"),
            "edit": _has_board_perm(db, current_admin, board_id, "edit"),
            "delete": _has_board_perm(db, current_admin, board_id, "delete"),
        },
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
    require_tasks_page(current_admin, "view")
    if not _has_board_perm(db, current_admin, board_id, "create"):
        raise HTTPException(status_code=403, detail="Нет прав на создание задач на этой доске")
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
    if not _can_edit_task(db, current_admin, task):
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
    if "status" in body:
        st = body.get("status")
        if st not in ("review", "in_progress", "done", "rejected"):
            raise HTTPException(status_code=400, detail="Некорректный статус")
        old_status = task.status
        task.status = st
        _log_task_activity(db, task.id, current_admin.id, "status_change", f"{old_status} -> {st}")
    if "is_hidden" in body:
        task.is_hidden = bool(body.get("is_hidden"))
        _log_task_activity(db, task.id, current_admin.id, "hide" if task.is_hidden else "unhide", None)
    if "rejection_reason" in body:
        task.rejection_reason = (body.get("rejection_reason") or "").strip()[:1000] or None
        if task.rejection_reason:
            _log_task_activity(db, task.id, current_admin.id, "reject", task.rejection_reason)
    if "preview_attachment_id" in body:
        pid = body.get("preview_attachment_id")
        if pid is not None:
            att = db.query(TaskAttachment).filter(TaskAttachment.id == pid, TaskAttachment.task_id == task.id).first()
            if not att:
                raise HTTPException(status_code=404, detail="Вложение не найдено")
        task.preview_attachment_id = pid

    db.commit()
    _log_task_activity(db, task.id, current_admin.id, "update", None)
    comment_count = db.query(TaskComment).filter(TaskComment.task_id == task.id).count()
    attachment_count = db.query(TaskAttachment).filter(TaskAttachment.task_id == task.id).count()
    return {"status": "ok", "task": serialize_task(task, comment_count, attachment_count)}


@router.delete("/api/v1/admin/tasks/tasks/{task_id}")
async def api_delete_task(task_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    task = get_task_or_404(db, task_id)
    if not _can_delete_task(db, current_admin, task):
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
            if not t or not _can_edit_task(db, current_admin, t):
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
    if comment.admin_id != current_admin.id and not _can_edit_task(db, current_admin, get_task_or_404(db, task_id)):
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


# ══════════════════════════════════════════════════════════════════════════════
# Гибкие права доски (п.1)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/tasks/boards/{board_id}/permissions")
async def api_get_board_permissions(board_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    get_board_or_404(db, board_id)
    perms = db.query(TaskBoardPermission).filter(TaskBoardPermission.board_id == board_id).all()
    return {"permissions": [{"id": p.id, "grantee_type": p.grantee_type, "grantee_key": p.grantee_key, "can_view": p.can_view, "can_create": p.can_create, "can_edit": p.can_edit, "can_delete": p.can_delete} for p in perms]}


@router.put("/api/v1/admin/tasks/boards/{board_id}/permissions")
async def api_set_board_permissions(board_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "manage_boards")
    get_board_or_404(db, board_id)
    body = await request.json()
    items = body.get("permissions") or []
    db.query(TaskBoardPermission).filter(TaskBoardPermission.board_id == board_id).delete()
    for it in items:
        gt = it.get("grantee_type")
        gk = str(it.get("grantee_key") or "").strip()
        if gt not in ("group_nd", "user", "dept_level") or not gk:
            continue
        db.add(TaskBoardPermission(board_id=board_id, grantee_type=gt, grantee_key=gk, can_view=bool(it.get("can_view")), can_create=bool(it.get("can_create")), can_edit=bool(it.get("can_edit")), can_delete=bool(it.get("can_delete"))))
    db.commit()
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# Вложения задач — скриншоты + превью (п.2)
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_task_upload_dir(task_id: int) -> str:
    settings = get_settings()
    base = getattr(settings, "uploads_dir", "uploads")
    path = os.path.join(base, "task_attachments", str(task_id))
    os.makedirs(path, exist_ok=True)
    return path


@router.get("/api/v1/admin/tasks/tasks/{task_id}/attachments")
async def api_list_task_attachments(task_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    get_task_or_404(db, task_id)
    atts = db.query(TaskAttachment).filter(TaskAttachment.task_id == task_id).order_by(TaskAttachment.created_at).all()
    return {"attachments": [serialize_attachment(a) for a in atts]}


@router.post("/api/v1/admin/tasks/tasks/{task_id}/attachments")
async def api_upload_task_attachment(task_id: int, file: UploadFile = File(...), is_preview: bool = Form(False), db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    task = get_task_or_404(db, task_id)
    if not _can_edit_task(db, current_admin, task):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    if file.size and file.size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Файл слишком большой (10 MB)")
    ext = os.path.splitext(file.filename or "file")[1][:10]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dir_path = _ensure_task_upload_dir(task_id)
    stored_path = os.path.join(dir_path, stored_name)
    with open(stored_path, "wb") as out:
        shutil.copyfileobj(file.file, out)
    size = os.path.getsize(stored_path) if os.path.exists(stored_path) else None
    att = TaskAttachment(task_id=task_id, uploader_admin_id=current_admin.id, filename=file.filename or stored_name, stored_path=stored_path, mime_type=file.content_type, file_size=size, is_preview=bool(is_preview))
    db.add(att)
    db.flush()
    if is_preview:
        task.preview_attachment_id = att.id
    db.commit()
    _log_task_activity(db, task_id, current_admin.id, "attach", file.filename or stored_name)
    db.commit()
    return {"status": "ok", "attachment": serialize_attachment(att)}


@router.delete("/api/v1/admin/tasks/tasks/{task_id}/attachments/{attachment_id}")
async def api_delete_task_attachment(task_id: int, attachment_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    att = db.query(TaskAttachment).filter(TaskAttachment.id == attachment_id, TaskAttachment.task_id == task_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    task = get_task_or_404(db, task_id)
    if not _can_edit_task(db, current_admin, task):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    try:
        if att.stored_path and os.path.exists(att.stored_path):
            os.remove(att.stored_path)
    except Exception:
        pass
    if task.preview_attachment_id == att.id:
        task.preview_attachment_id = None
    db.delete(att)
    db.commit()
    _log_task_activity(db, task_id, current_admin.id, "detach", att.filename)
    db.commit()
    return {"status": "ok"}


@router.get("/api/v1/admin/tasks/attachments/{attachment_id}/file")
async def api_get_task_attachment_file(attachment_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    att = db.query(TaskAttachment).filter(TaskAttachment.id == attachment_id).first()
    if not att or not att.stored_path or not os.path.exists(att.stored_path):
        raise HTTPException(status_code=404, detail="Файл не найден")
    from fastapi.responses import FileResponse
    return FileResponse(att.stored_path, filename=att.filename, media_type=att.mime_type or "application/octet-stream")


# ══════════════════════════════════════════════════════════════════════════════
# Активность + статусы + скрытые (п.3)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/tasks/tasks/{task_id}/activity")
async def api_get_task_activity(task_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    get_task_or_404(db, task_id)
    logs = db.query(TaskActivityLog).filter(TaskActivityLog.task_id == task_id).order_by(TaskActivityLog.created_at.desc()).limit(100).all()
    for l in logs:
        if l.admin_id:
            db.refresh(l)
    # joinedload for admin
    logs = db.query(TaskActivityLog).options(joinedload(TaskActivityLog.admin)).filter(TaskActivityLog.task_id == task_id).order_by(TaskActivityLog.created_at.desc()).limit(100).all()
    return {"activity": [serialize_activity(a) for a in logs]}


@router.post("/api/v1/admin/tasks/tasks/{task_id}/reject")
async def api_reject_task(task_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    task = get_task_or_404(db, task_id)
    if not _can_edit_task(db, current_admin, task):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    body = await request.json()
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Укажите причину отклонения")
    # п.5: отклонено — состояние (is_hidden + rejection_reason), а не статус
    task.is_hidden = True
    task.rejection_reason = reason[:1000]
    db.commit()
    _log_task_activity(db, task_id, current_admin.id, "reject", reason)
    db.commit()
    return {"status": "ok", "task": serialize_task(task)}


@router.post("/api/v1/admin/tasks/tasks/{task_id}/toggle-hidden")
async def api_toggle_task_hidden(task_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    task = get_task_or_404(db, task_id)
    if not _can_edit_task(db, current_admin, task):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    task.is_hidden = not task.is_hidden
    db.commit()
    _log_task_activity(db, task_id, current_admin.id, "unhide" if not task.is_hidden else "hide", None)
    db.commit()
    return {"status": "ok", "is_hidden": task.is_hidden}


# ══════════════════════════════════════════════════════════════════════════════
# Чат доски — общий чат + файлы + закрепления (п.5)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/tasks/boards/{board_id}/chat")
async def api_get_board_chat(board_id: int, limit: int = 50, before_id: Optional[int] = None, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    get_board_or_404(db, board_id)
    if not _has_board_perm(db, current_admin, board_id, "view"):
        raise HTTPException(status_code=403, detail="Нет доступа к чату доски")
    q = db.query(TaskBoardMessage).options(joinedload(TaskBoardMessage.author)).filter(TaskBoardMessage.board_id == board_id)
    if before_id:
        q = q.filter(TaskBoardMessage.id < before_id)
    msgs = q.order_by(TaskBoardMessage.created_at.desc()).limit(limit).all()
    msgs = list(reversed(msgs))
    result = []
    for m in msgs:
        atts = db.query(TaskBoardMessageAttachment).filter(TaskBoardMessageAttachment.message_id == m.id).all()
        result.append(serialize_board_message(m, [{"id": a.id, "filename": a.filename, "url": f"/api/v1/admin/tasks/board-chat/attachments/{a.id}/file", "mime_type": a.mime_type} for a in atts]))
    pinned = db.query(TaskBoardMessage).filter(TaskBoardMessage.board_id == board_id, TaskBoardMessage.is_pinned.is_(True)).order_by(TaskBoardMessage.created_at.desc()).limit(10).all()
    return {"messages": result, "pinned": [serialize_board_message(m) for m in pinned]}


@router.post("/api/v1/admin/tasks/boards/{board_id}/chat")
async def api_post_board_chat(board_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    get_board_or_404(db, board_id)
    if not _has_board_perm(db, current_admin, board_id, "view"):
        raise HTTPException(status_code=403, detail="Нет доступа")
    # support both JSON and multipart
    content_type = request.headers.get("content-type", "")
    files = []
    content = ""
    reply_to_id = None
    if "multipart/form-data" in content_type:
        form = await request.form()
        content = (form.get("content") or "").strip()
        reply_to_id = form.get("reply_to_id")
        files = form.getlist("files") if hasattr(form, "getlist") else []
        if not files and "file" in form:
            files = [form["file"]]
    else:
        body = await request.json()
        content = (body.get("content") or "").strip()
        reply_to_id = body.get("reply_to_id")
    if not content and not files:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
    if len(content) > 4000:
        content = content[:4000]
    msg = TaskBoardMessage(board_id=board_id, author_admin_id=current_admin.id, content=content or "(файл)", reply_to_id=int(reply_to_id) if reply_to_id else None)
    db.add(msg)
    db.flush()
    # save files
    for f in files:
        if not hasattr(f, "filename"):
            continue
        ext = os.path.splitext(f.filename or "file")[1][:10]
        stored_name = f"{uuid.uuid4().hex}{ext}"
        base = getattr(get_settings(), "uploads_dir", "uploads")
        dir_path = os.path.join(base, "task_board_chat", str(board_id))
        os.makedirs(dir_path, exist_ok=True)
        stored_path = os.path.join(dir_path, stored_name)
        try:
            data = await f.read()
            with open(stored_path, "wb") as out:
                out.write(data)
            db.add(TaskBoardMessageAttachment(message_id=msg.id, filename=f.filename or stored_name, stored_path=stored_path, mime_type=getattr(f, "content_type", None), file_size=len(data)))
        except Exception:
            continue
    db.commit()
    db.refresh(msg)
    atts = db.query(TaskBoardMessageAttachment).filter(TaskBoardMessageAttachment.message_id == msg.id).all()
    return {"status": "ok", "message": serialize_board_message(msg, [{"id": a.id, "filename": a.filename, "url": f"/api/v1/admin/tasks/board-chat/attachments/{a.id}/file"} for a in atts])}


@router.post("/api/v1/admin/tasks/boards/{board_id}/chat/{message_id}/pin")
async def api_toggle_pin_board_chat(board_id: int, message_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    msg = db.query(TaskBoardMessage).filter(TaskBoardMessage.id == message_id, TaskBoardMessage.board_id == board_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    msg.is_pinned = not msg.is_pinned
    db.commit()
    return {"status": "ok", "is_pinned": msg.is_pinned}


@router.get("/api/v1/admin/tasks/board-chat/attachments/{attachment_id}/file")
async def api_get_board_chat_attachment(attachment_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_tasks_page(current_admin, "view")
    att = db.query(TaskBoardMessageAttachment).filter(TaskBoardMessageAttachment.id == attachment_id).first()
    if not att or not os.path.exists(att.stored_path):
        raise HTTPException(status_code=404, detail="Файл не найден")
    from fastapi.responses import FileResponse
    return FileResponse(att.stored_path, filename=att.filename, media_type=att.mime_type or "application/octet-stream")
