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
    """Setup structured logging with file and console handlers"""
    
    root_logger = logging.getLogger()
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
    
    # Add handlers
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)
    
    return root_logger


# Initialize logging
logger = setup_logging()
