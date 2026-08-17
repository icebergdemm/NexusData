"""
Мониторинг производительности и процессов (services_perf_monitor.py) —
только для суперпользователей, по той же причине, что и «Журнал ошибок»:
это внутренние детали сервера (пути медленных запросов, потребление
ресурсов), не для всех админов с полными правами.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models import AdminUser, AdminSession
import services_perf_monitor as perf

router = APIRouter()


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    from datetime import datetime
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
        if session and (session.expires_at is None or session.expires_at > now):
            admin = db.query(AdminUser).filter(AdminUser.id == session.admin_id).first()
            if admin:
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require_superuser(admin: AdminUser):
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Доступно только суперпользователям")


@router.get("/api/v1/admin/perf/history")
async def api_perf_history(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Живой буфер сэмплов CPU/RAM/event-loop-lag (последние ~2 часа) —
    из памяти процесса, ничего не читает из БД."""
    _require_superuser(current_admin)
    return {"samples": perf.get_history()}


@router.get("/api/v1/admin/perf/anomalies")
async def api_perf_anomalies(
    kind: str = Query(""),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    per_page = 50
    rows, total = perf.list_anomalies(db, kind=kind, limit=per_page, offset=(page - 1) * per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "anomalies": [
            {
                "id": a.id,
                "kind": a.kind,
                "value": a.value,
                "path": a.path,
                "method": a.method,
                "detail": a.detail,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ],
        "total": total,
        "page": page,
        "pages": pages,
        "retention_days": perf.RETENTION_DAYS,
        "thresholds": {
            "event_loop_lag_s": perf.EVENT_LOOP_LAG_THRESHOLD_S,
            "slow_request_s": perf.SLOW_REQUEST_THRESHOLD_S,
            "high_cpu_pct": perf.HIGH_CPU_THRESHOLD_PCT,
            "high_memory_pct": perf.HIGH_MEMORY_THRESHOLD_PCT,
        },
    }


@router.delete("/api/v1/admin/perf/anomalies")
async def api_perf_anomalies_clear(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    from models import PerfAnomaly
    deleted = db.query(PerfAnomaly).delete()
    db.commit()
    return {"status": "ok", "deleted": deleted}
