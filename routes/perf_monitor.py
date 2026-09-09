"""
Мониторинг производительности и процессов (services_perf_monitor.py) —
только для суперпользователей, по той же причине, что и «Журнал ошибок»:
это внутренние детали сервера (пути медленных запросов, потребление
ресурсов), не для всех админов с полными правами.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from database import get_db
from models import AdminUser, AdminSession
import services_perf_monitor as perf

router = APIRouter()


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


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


@router.get("/api/v1/admin/perf/task-manager")
def api_perf_task_manager(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Аналог Диспетчера задач Windows для контейнера (services_task_manager.py).

    Синхронный def (а не async): сборка снимка — миллисекунды работы psutil
    частого опрашивания страницы лучше держать в threadpool, а не в event
    loop. Дельты CPU живут в module cache — первый запрос после старта
    возвращает 0% (нет предыдущего окна)."""
    _require_superuser(current_admin)
    import services_task_manager as tm
    return tm.build_snapshot()


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


@router.post("/api/v1/admin/perf/task-manager/control")
async def api_perf_task_manager_control(
    request: Request,
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Управление процессами/службами из Диспетчера задач.

    target: "pid:<pid>" (только API и его дочерние) | "scheduler" |
    "log_worker" | "replay_worker" | "playtime_worker".
    action: suspend | resume | kill | terminate | restart | stop | start.
    Синхронные блокировки (scheduler.stop джойнит до 8с и psutil.wait)
    выполняются в threadpool."""
    _require_superuser(current_admin)
    body = await request.json()
    target = str(body.get("target") or "").strip()
    action = str(body.get("action") or "").strip()
    import services_task_manager as tm
    try:
        result = await run_in_threadpool(tm.execute_control, target, action)
    except tm.ControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tm.invalidate_snapshot_cache()
    return {"status": "ok", **result}


@router.post("/api/v1/admin/perf/task-manager/cpu-capture")
async def api_perf_task_manager_cpu_capture(
    request: Request,
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Сэмплирующий CPU-профайлер: N секунд агрегирует стеки всех потоков
    по верхней функции. request.json(): {"seconds": 6} (1-15)."""
    _require_superuser(current_admin)
    import services_task_manager as tm
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    seconds = body.get("seconds") or 6
    try:
        return await run_in_threadpool(tm.capture_cpu_profile, seconds)
    except tm.ControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/admin/perf/task-manager/mem-profile")
async def api_perf_task_manager_mem_profile(
    request: Request,
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Профайлер памяти (tracemalloc): {"action": "start"|"snapshot"|"stop"}.
    snapshot возвращает текущие лидеры аллокаций и прирост с прошлого
    снимка по (файл:строка)."""
    _require_superuser(current_admin)
    import services_task_manager as tm
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    action = str(body.get("action") or "").strip()
    try:
        return await run_in_threadpool(tm.mem_profile, action)
    except tm.ControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
