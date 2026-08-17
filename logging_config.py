import logging
import logging.handlers
import os
from pathlib import Path
from config import get_settings

settings = get_settings()

# Создай папку logs если её нет
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

# Форматирование логов
DETAILED_FORMAT = logging.Formatter(
    fmt='%(asctime)s | %(name)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

SIMPLE_FORMAT = logging.Formatter(
    fmt='%(levelname)-8s | %(message)s'
)


def setup_logging():
    """Setup structured logging with file and console handlers.

    Idempotent: guarded so calling this more than once in the same process
    (e.g. main.py re-imported) doesn't stack duplicate handlers and doubly
    log/persist every message."""
    root_logger = logging.getLogger()
    if any(isinstance(h, logging.Handler) and getattr(h, "_nexusdata_setup", False) for h in root_logger.handlers):
        return root_logger
    root_logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # Console handler (simple format)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO if not settings.debug else logging.DEBUG)
    console_handler.setFormatter(SIMPLE_FORMAT)
    
    # File handler (detailed format)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=logs_dir / "api.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(DETAILED_FORMAT)
    
    # Error file handler
    error_handler = logging.handlers.RotatingFileHandler(
        filename=logs_dir / "error.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(DETAILED_FORMAT)

    # DB-backed error journal — powers the "Журнал ошибок" admin page
    # (superusers only, see routes/error_log.py). Complements error.log
    # above rather than replacing it: this one is searchable/filterable
    # from the panel, error.log is the raw fallback if the DB itself is down.
    from services_error_log import DatabaseErrorHandler
    db_handler = DatabaseErrorHandler()

    for h in (console_handler, file_handler, error_handler, db_handler):
        h._nexusdata_setup = True  # marks this process's handlers for the idempotency guard above

    # Add handlers
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)
    root_logger.addHandler(db_handler)

    return root_logger


# NOTE: setup_logging() is called explicitly from main.py at import time —
# not here at module import, so it runs exactly once per process regardless
# of how many places end up importing logging_config.
logger = logging.getLogger(__name__)
