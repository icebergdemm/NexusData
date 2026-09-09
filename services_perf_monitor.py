"""
Мониторинг производительности и процессов — три независимых, максимально
дешёвых детектора аномалий плюс лёгкий in-memory буфер для живого графика.

Специально НЕ строится на постоянной записи каждого сэмпла в БД (это само
по себе стало бы лишней нагрузкой, а задача явно требует "не тормозило
важные процессы") — рутинные измерения живут только в памяти (deque с
фиксированным размером), в БД (PerfAnomaly, models.py) попадают только
отклонения от нормы, да и то не чаще раза в COOLDOWN_SECONDS на источник.

Три детектора:
  1. event_loop_watchdog() — asyncio-задача, спит по 1 секунде и меряет,
     насколько РЕАЛЬНО дольше вышло. Это САМЫЙ прямой индикатор "сайт
     полностью завис": пока event loop процесса заблокирован СИНХРОННЫМ
     кодом где угодно в приложении (полное сканирование таблицы без
     run_in_threadpool, вложенные Python-циклы и т.п. — см. историю
     api_admin_administration в routes/admin.py, реальный источник таких
     зависаний), ни один запрос ни от кого не обслуживается, независимо от
     того, какой обработчик виноват. Запускается один раз при старте
     приложения (main.py's startup event).
  2. record_slow_request() — вызывается из middleware в main.py на каждый
     HTTP-запрос; сравнение с порогом стоит копейки, запись в БД — только
     если превышен порог (SLOW_REQUEST_THRESHOLD_S).
  3. sample_resources() — периодическая задача через task_scheduler
     (BACKGROUND-приоритет, как и остальные фоновые задачи в этом проекте —
     см. workers.py: никогда не конкурирует с обслуживанием запросов).
     Сэмплирует CPU/RAM процесса и системы через psutil, копит в ring
     buffer для графика, аномалии — в БД.
"""
import logging
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))

EVENT_LOOP_CHECK_INTERVAL_S = 1.0
EVENT_LOOP_LAG_THRESHOLD_S = 0.5     # лаг сверх интервала — событие "завис"
SLOW_REQUEST_THRESHOLD_S = 2.0       # HTTP-запрос дольше этого — аномалия
HIGH_CPU_THRESHOLD_PCT = 85.0        # системный CPU
HIGH_MEMORY_THRESHOLD_PCT = 90.0     # системная RAM

RETENTION_DAYS = 14
HISTORY_MAXLEN = 240                 # ~2 часа при сэмплировании раз в 30с
ANOMALY_QUEUE_MAXSIZE = 256

# Раз в COOLDOWN_SECONDS[kind] на уникальный ключ источника — не даёт одной
# и той же аномалии (например, устойчиво высокой нагрузке) плодить сотни
# одинаковых строк в БД.
_COOLDOWNS = {
    "event_loop_lag": 30,
    "slow_request": 30,
    "high_cpu": 300,
    "high_memory": 300,
}

_last_recorded: Dict[str, float] = {}
_last_recorded_lock = threading.Lock()

# Живой буфер для графика на странице мониторинга — ничего не пишет в БД,
# читается/пишется без явной блокировки: deque.append атомарен в CPython
# (GIL), а лёгкая гонка при чтении диагностического буфера не критична.
_history: Deque[Dict[str, Any]] = deque(maxlen=HISTORY_MAXLEN)
_latest_event_loop_lag_ms: float = 0.0


@dataclass(frozen=True)
class _PendingAnomaly:
    kind: str
    value: float
    path: Optional[str]
    method: Optional[str]
    detail: Optional[str]
    created_at: datetime


_anomaly_queue: "queue.Queue[_PendingAnomaly]" = queue.Queue(maxsize=ANOMALY_QUEUE_MAXSIZE)
_anomaly_worker_lock = threading.Lock()
_anomaly_worker_started = False


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


def _should_record(key: str, kind: str) -> bool:
    cooldown = _COOLDOWNS.get(kind, 30)
    now = time.monotonic()
    with _last_recorded_lock:
        last = _last_recorded.get(key, 0.0)
        if now - last < cooldown:
            return False
        _last_recorded[key] = now
    return True


def _persist_anomaly(payload: _PendingAnomaly) -> None:
    from database import SessionLocal
    from models import PerfAnomaly

    db = SessionLocal()
    try:
        db.add(PerfAnomaly(
            kind=payload.kind,
            value=payload.value,
            path=payload.path,
            method=payload.method,
            detail=payload.detail,
            created_at=payload.created_at,
        ))
        db.commit()
        import random
        if random.random() < 1 / 200:
            _prune_old(db)
    except Exception:
        db.rollback()
        # Diagnostics are fail-open. Logging here would feed the database
        # error handler and create a recursive error storm during an outage.
    finally:
        db.close()


def _anomaly_writer_loop() -> None:
    while True:
        payload = _anomaly_queue.get()
        try:
            _persist_anomaly(payload)
        except Exception:
            pass
        finally:
            _anomaly_queue.task_done()


def _ensure_anomaly_worker() -> None:
    global _anomaly_worker_started
    if _anomaly_worker_started:
        return
    with _anomaly_worker_lock:
        if _anomaly_worker_started:
            return
        threading.Thread(
            target=_anomaly_writer_loop,
            name="nexus-perf-anomaly-writer",
            daemon=True,
        ).start()
        _anomaly_worker_started = True


def record_anomaly(kind: str, value: float, path: Optional[str] = None,
                    method: Optional[str] = None, detail: Optional[str] = None) -> None:
    """Queue one anomaly without ever waiting for a database connection.

    This function is called from the event-loop watchdog and HTTP middleware;
    a synchronous ``Session.commit`` here used to freeze every request for the
    SQLAlchemy pool's 30-second checkout timeout precisely when the database
    was already overloaded.
    """
    payload = _PendingAnomaly(
        kind=kind,
        value=value,
        path=path,
        method=method,
        detail=detail,
        created_at=_now_msk(),
    )
    try:
        _anomaly_queue.put_nowait(payload)
    except queue.Full:
        return
    _ensure_anomaly_worker()


def _prune_old(db) -> None:
    from models import PerfAnomaly
    cutoff = _now_msk() - timedelta(days=RETENTION_DAYS)
    db.query(PerfAnomaly).filter(PerfAnomaly.created_at < cutoff).delete()
    db.commit()


# ── 1. Event-loop watchdog ──────────────────────────────────────────────────

async def event_loop_watchdog() -> None:
    """Живёт всё время работы процесса (создаётся один раз в main.py's
    startup event через asyncio.create_task). 99% времени просто спит —
    расход ресурсов на сам детектор пренебрежимо мал."""
    import asyncio
    logger.info("[PerfMonitor] Event-loop watchdog started")
    loop = asyncio.get_event_loop()
    while True:
        t0 = loop.time()
        await asyncio.sleep(EVENT_LOOP_CHECK_INTERVAL_S)
        elapsed = loop.time() - t0
        lag = elapsed - EVENT_LOOP_CHECK_INTERVAL_S

        global _latest_event_loop_lag_ms
        _latest_event_loop_lag_ms = max(0.0, lag) * 1000

        if lag >= EVENT_LOOP_LAG_THRESHOLD_S:
            logger.warning(
                "[PerfMonitor] Event loop lag: %.2fs (порог %.2fs) — весь сайт "
                "не отвечал никому это время", lag, EVENT_LOOP_LAG_THRESHOLD_S,
            )
            if _should_record("event_loop_lag", "event_loop_lag"):
                record_anomaly("event_loop_lag", round(lag, 3))


# ── 2. Slow HTTP requests ────────────────────────────────────────────────────

def record_slow_request(method: str, path: str, duration_s: float) -> None:
    """Вызывается из main.py's middleware на каждый запрос — сравнение с
    порогом дешёвое, работа (запись в БД) только если он превышен."""
    if duration_s < SLOW_REQUEST_THRESHOLD_S:
        return
    key = f"slow_request:{method}:{path}"
    if _should_record(key, "slow_request"):
        record_anomaly("slow_request", round(duration_s, 3), path=path, method=method)


# ── 3. Periodic resource sampling ───────────────────────────────────────────

def sample_resources() -> dict:
    """Вызывается task_scheduler'ом на BACKGROUND-приоритете (см.
    workers.py: task_sample_perf_resources) — никогда не конкурирует с
    обслуживанием запросов. Один вызов psutil ~миллисекунды."""
    try:
        import psutil
    except ImportError:
        return {"status": "skipped", "reason": "psutil not installed"}

    try:
        proc = psutil.Process(os.getpid())
        # interval=None — неблокирующий вызов, использует дельту с прошлого
        # вызова психутила для ЭТОГО процесса; занимает микросекунды, в
        # отличие от interval=0.1, который бы полсекунды удерживал этот
        # background-поток (не критично для event loop, но незачем).
        proc_cpu = proc.cpu_percent(interval=None)
        rss_mb = proc.memory_info().rss / 1024 / 1024
        sys_cpu = psutil.cpu_percent(interval=None)
        sys_ram_pct = psutil.virtual_memory().percent
        threads = proc.num_threads()
    except Exception as e:
        logger.warning("perf_monitor.sample_resources: psutil error: %s", e)
        return {"status": "error", "error": str(e)}

    sample = {
        "ts": _now_msk().isoformat(),
        "proc_cpu_pct": round(proc_cpu, 1),
        "sys_cpu_pct": round(sys_cpu, 1),
        "rss_mb": round(rss_mb, 1),
        "sys_ram_pct": round(sys_ram_pct, 1),
        "threads": threads,
        "event_loop_lag_ms": round(_latest_event_loop_lag_ms, 1),
    }
    _history.append(sample)

    if sys_cpu >= HIGH_CPU_THRESHOLD_PCT and _should_record("high_cpu", "high_cpu"):
        record_anomaly("high_cpu", round(sys_cpu, 1), detail=f"process={round(proc_cpu, 1)}%")
    if sys_ram_pct >= HIGH_MEMORY_THRESHOLD_PCT and _should_record("high_memory", "high_memory"):
        record_anomaly("high_memory", round(sys_ram_pct, 1), detail=f"process_rss_mb={round(rss_mb, 1)}")

    return {"status": "ok", **sample}


def get_history() -> list:
    """Живой буфер для графика — последние ~2 часа, ничего не читает из БД."""
    return list(_history)


# ── Query API (для routes/perf_monitor.py) ──────────────────────────────────

def list_anomalies(db, kind: str = "", limit: int = 100, offset: int = 0):
    from models import PerfAnomaly
    q = db.query(PerfAnomaly)
    if kind:
        q = q.filter(PerfAnomaly.kind == kind)
    total = q.count()
    rows = q.order_by(PerfAnomaly.created_at.desc()).offset(offset).limit(limit).all()
    return rows, total
