"""
Background workers for Nexus Data.

PlaytimeCompensationWorker — каждые 30 минут сравнивает play_time игрока
с суммой session_duration из player_sessions. Если play_time < суммы сессий,
добавляет разницу (компенсирует). Если >= — не трогает.
"""

import logging
import threading
import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal
from models import PlayerData, PlayerSession

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 30 * 60  # 30 минут


def _compensate_playtime(db: Session) -> None:
    """
    Для каждого игрока:
      total_session = SUM(session_duration) по завершённым сессиям
      если play_time < total_session → play_time = total_session

    Реализовано одним UPDATE с коррелированным подзапросом — без N+1.
    """
    from sqlalchemy import text

    # Single atomic UPDATE: set play_time to session total where it lags behind.
    # Correlated subquery computes SUM(session_duration) per player.
    result = db.execute(text("""
        UPDATE players_data pd
        JOIN (
            SELECT player_id, SUM(session_duration) AS total
            FROM player_sessions
            WHERE session_duration IS NOT NULL
            GROUP BY player_id
        ) st ON st.player_id = pd.player_id
        SET pd.play_time = st.total
        WHERE pd.play_time < st.total
    """))
    compensated = result.rowcount or 0

    if compensated:
        db.commit()
        logger.info("PlaytimeWorker: скомпенсировано %d игроков", compensated)
    else:
        db.commit()
        logger.debug("PlaytimeWorker: компенсация не требуется")


def _worker_loop() -> None:
    """Основной цикл воркера. Запускается в отдельном daemon-потоке."""
    logger.info("PlaytimeWorker запущен (интервал %d сек)", INTERVAL_SECONDS)
    while True:
        try:
            db: Session = SessionLocal()
            try:
                _compensate_playtime(db)
            except Exception:
                logger.exception("PlaytimeWorker: ошибка при компенсации")
                db.rollback()
            finally:
                db.close()
                del db  # Help GC
        except Exception:
            logger.exception("PlaytimeWorker: не удалось открыть сессию БД")

        time.sleep(INTERVAL_SECONDS)


def start_playtime_worker() -> threading.Thread:
    """
    Создаёт и запускает daemon-поток с воркером.
    Daemon-поток автоматически завершается вместе с основным процессом.
    """
    t = threading.Thread(target=_worker_loop, name="PlaytimeWorker", daemon=True)
    t.start()
    logger.info("PlaytimeWorker поток запущен (id=%d)", t.ident)
    return t
