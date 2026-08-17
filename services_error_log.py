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
import random
import traceback as tb_module
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

MSK = timezone(timedelta(hours=3))

RETENTION_DAYS = 30

_request_context: contextvars.ContextVar[Optional[Tuple[str, str]]] = contextvars.ContextVar(
    "error_log_request_context", default=None
)


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


def set_request_context(method: str, path: str) -> None:
    _request_context.set((method, path))


class DatabaseErrorHandler(logging.Handler):
    """Персистирует ERROR/CRITICAL записи в error_log_entries.

    КРИТИЧНО: emit() глушит абсолютно любое собственное исключение молча —
    никогда logger.error/exception отсюда. Если этого не сделать, сбой самой
    записи в БД (недоступна, таблицы ещё нет при первом старте до init_db()
    и т.п.) уйдёт в тот же логгер, на который подписан этот же handler —
    прямая бесконечная рекурсия/лавина логов вместо одной тихо пропущенной
    записи."""

    def __init__(self):
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from database import SessionLocal
            from models import ErrorLogEntry

            method, path = _request_context.get() or (None, None)
            entry = ErrorLogEntry(
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage()[:8000],
                traceback=self._format_traceback(record),
                path=path,
                method=method,
                created_at=_now_msk(),
            )
            db = SessionLocal()
            try:
                db.add(entry)
                db.commit()
                # Лёгкая ротация без отдельного воркера: на каждую ~500-ю
                # вставку заодно подчищаем то, что старше RETENTION_DAYS.
                # Вероятностно, а не "каждый раз count()" — не добавлять
                # лишний SELECT на горячий путь логирования ошибок.
                if random.random() < 1 / 500:
                    prune_old(db)
            finally:
                db.close()
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
