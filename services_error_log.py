"""
Персистентный журнал серверных ошибок (модель ErrorLogEntry в models.py).

Состоит из трёх частей:
  1. DatabaseErrorHandler — logging.Handler, подключаемый к корневому
     логгеру в logging_config.setup_logging(). Ловит ЛЮБОЙ ERROR/CRITICAL
     лог из любого модуля сайта (не только необработанные исключения из
     main.py's unhandled_exception_handler) — это и есть "аудит всех ошибок".
  2. set_request_context() — контекст текущего HTTP-запроса (метод + путь),
     выставляется middleware в main.py, читается хендлером для колонок
     path/method. В фоновых потоках (threading.Thread) остаётся пуст —
     contextvars туда не наследуются, это ожидаемо и не баг.
  3. list_entries()/prune_old() — чтение для API страницы (routes/error_log.py,
     только суперпользователи) и лёгкая ротация старых записей.
"""
import contextvars
import logging
import queue
import random
import threading
import traceback as tb_module
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

MSK = timezone(timedelta(hours=3))

RETENTION_DAYS = 30
ERROR_QUEUE_MAXSIZE = 512

_request_context: contextvars.ContextVar[Optional[Tuple[str, str]]] = contextvars.ContextVar(
    "error_log_request_context", default=None
)


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


def set_request_context(method: str, path: str) -> None:
    _request_context.set((method, path))


@dataclass(frozen=True)
class _PendingError:
    level: str
    logger_name: str
    message: str
    traceback: Optional[str]
    path: Optional[str]
    method: Optional[str]
    created_at: datetime


_error_queue: "queue.Queue[_PendingError]" = queue.Queue(maxsize=ERROR_QUEUE_MAXSIZE)
_worker_lock = threading.Lock()
_worker_started = False


def _persist_error(payload: _PendingError) -> None:
    """Write one queued record outside the request/event-loop thread."""
    from database import SessionLocal
    from models import ErrorLogEntry

    db = SessionLocal()
    try:
        db.add(ErrorLogEntry(
            level=payload.level,
            logger_name=payload.logger_name,
            message=payload.message,
            traceback=payload.traceback,
            path=payload.path,
            method=payload.method,
            created_at=payload.created_at,
        ))
        db.commit()
        if random.random() < 1 / 500:
            prune_old(db)
    except Exception:
        db.rollback()
        # Never log from the logging sink itself: that would recurse back into
        # DatabaseErrorHandler while the database is already unavailable.
    finally:
        db.close()


def _error_writer_loop() -> None:
    while True:
        payload = _error_queue.get()
        try:
            _persist_error(payload)
        except Exception:
            # Imports/startup failures must not terminate the only consumer.
            pass
        finally:
            _error_queue.task_done()


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(
            target=_error_writer_loop,
            name="nexus-error-log-writer",
            daemon=True,
        ).start()
        _worker_started = True


def _safe_record_message(record: logging.LogRecord) -> str:
    try:
        return record.getMessage()
    except Exception:
        # Some third-party records embed percent signs from a captured stack
        # in their format string. Preserve the evidence without mutating the
        # shared LogRecord seen by console/file handlers.
        return f"{record.msg!s} | args={record.args!r}"


class DatabaseErrorHandler(logging.Handler):
    """Персистирует ERROR/CRITICAL записи в error_log_entries.

    ``emit`` никогда не обращается к БД: он только складывает неизменяемый
    снимок записи в ограниченную очередь. Иначе исчерпание SQLAlchemy pool
    блокирует event loop на ``pool_timeout`` прямо во время обработки исходной
    ошибки и превращает временный сбой БД в полное зависание сайта.

    Очередь fail-open: при переполнении диагностическая строка пропускается,
    но запросы, Discord heartbeat и игровой API продолжают работать."""

    def __init__(self):
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            method, path = _request_context.get() or (None, None)
            payload = _PendingError(
                level=record.levelname,
                logger_name=record.name,
                message=_safe_record_message(record)[:8000],
                traceback=self._format_traceback(record),
                path=path,
                method=method,
                created_at=_now_msk(),
            )
            try:
                _error_queue.put_nowait(payload)
            except queue.Full:
                return
            _ensure_worker()
        except Exception:
            pass  # см. докстринг класса

    @staticmethod
    def _format_traceback(record: logging.LogRecord) -> Optional[str]:
        if not record.exc_info:
            return None
        try:
            return "".join(tb_module.format_exception(*record.exc_info))[:20000]
        except Exception:
            return None


def prune_old(db) -> int:
    """Удаляет записи старше RETENTION_DAYS. Вызывается лениво из emit() —
    таблица растёт с каждой ошибкой в проде, без ротации это неограниченный
    рост. Возвращает число удалённых строк (для ручного вызова из консоли)."""
    from models import ErrorLogEntry
    cutoff = _now_msk() - timedelta(days=RETENTION_DAYS)
    deleted = db.query(ErrorLogEntry).filter(ErrorLogEntry.created_at < cutoff).delete()
    db.commit()
    return deleted


def list_entries(db, level: str = "", search: str = "", limit: int = 100, offset: int = 0):
    """Постранично, новые сверху. level="" — все уровни (ERROR и CRITICAL,
    другие сюда handler не пишет); search ищет по message/traceback/
    logger_name/path одновременно (та же простая ILIKE-модель, что и у
    PunishmentService.list_by_type)."""
    from models import ErrorLogEntry
    q = db.query(ErrorLogEntry)
    if level:
        q = q.filter(ErrorLogEntry.level == level.upper())
    if search:
        like = f"%{search}%"
        q = q.filter(
            ErrorLogEntry.message.ilike(like) |
            ErrorLogEntry.traceback.ilike(like) |
            ErrorLogEntry.logger_name.ilike(like) |
            ErrorLogEntry.path.ilike(like)
        )
    total = q.count()
    rows = q.order_by(ErrorLogEntry.created_at.desc()).offset(offset).limit(limit).all()
    return rows, total
