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
    """
    # Считаем суммарное время сессий для каждого игрока одним запросом
    session_totals = (
        db.query(
            PlayerSession.player_id,
            func.sum(PlayerSession.session_duration).label("total"),
        )
        .filter(PlayerSession.session_duration.isnot(None))
        .group_by(PlayerSession.player_id)
        .all()
    )

    compensated = 0
    for player_id, total_seconds in session_totals:
        if not total_seconds:
            continue

        player: PlayerData | None = (
            db.query(PlayerData)
            .filter(PlayerData.player_id == player_id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if player is None:
            continue

        current = player.play_time or 0
        if current < total_seconds:
            diff = total_seconds - current
            player.play_time = total_seconds
            compensated += 1
            logger.debug(
                "Компенсация игрок #%d: play_time %d → %d (+%d сек)",
                player_id, current, total_seconds, diff,
            )

    if compensated:
        db.commit()
        logger.info("PlaytimeWorker: скомпенсировано %d игроков", compensated)
    else:
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
