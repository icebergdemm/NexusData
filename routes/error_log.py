"""
Журнал серверных ошибок (ErrorLogEntry, заполняется services_error_log.
DatabaseErrorHandler) — только для суперпользователей, поэтому здесь нет
обвязки под permissions_registry: это не про роли/разрешения, а про то, что
трасса исключений и внутренние сообщения об ошибках могут содержать
чувствительные детали (пути на диске, куски запросов и т.п.), которым не
место даже у полноправного, но не супер- админа.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models import AdminUser, AdminSession
import services_error_log as error_log_service

router = APIRouter()


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


def _require_superuser(admin: AdminUser):
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Доступно только суперпользователям")


@router.get("/api/v1/admin/error-log")
async def api_error_log_list(
    level: str = Query(""),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    per_page = 50
    rows, total = error_log_service.list_entries(
        db, level=level, search=search, limit=per_page, offset=(page - 1) * per_page,
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "entries": [
            {
                "id": e.id,
                "level": e.level,
                "logger_name": e.logger_name,
                "message": e.message,
                "traceback": e.traceback,
                "path": e.path,
                "method": e.method,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ],
        "total": total,
        "page": page,
        "pages": pages,
        "retention_days": error_log_service.RETENTION_DAYS,
    }


@router.delete("/api/v1/admin/error-log")
async def api_error_log_clear(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Полная очистка журнала — например, после разбора и фикса волны
    однотипных ошибок, чтобы новые не терялись в старом шуме."""
    _require_superuser(current_admin)
    from models import ErrorLogEntry
    deleted = db.query(ErrorLogEntry).delete()
    db.commit()
    return {"status": "ok", "deleted": deleted}
