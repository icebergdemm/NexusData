"""
Background anomaly detection for Moderation → Аномалии.

Reuses services_sauron.py's existing, already-efficient pure-SQL detectors
(shared IP/subnet/name-prefix twink pairing; teamkill/damage/reactor-abuse/
rapid-reconnect deviant-behavior rules) rather than re-implementing that
logic — those queries are aggregated GROUP BYs with hard LIMITs, not
per-row Python loops, so they're already cheap. This module's own job is
just three things Sauron doesn't do:
  1. Persist every finding into PlayerAnomaly (Sauron only ever returned
     results on demand, cached 10 minutes, never stored).
  2. Ban-evasion correlation: cross-reference twink pairs against ACTIVE
     bans + who's online RIGHT NOW, and auto-ban a high-confidence evader.
  3. A wall-clock time budget per run, so a slow moment for the database
     cuts this run short (persisting whatever it already found) instead of
     grinding on — the next scheduled run 30 minutes later picks up the
     rest. The API and web frontend staying responsive matters more than
     this job finishing in one pass.

Runs ONLY from workers.task_scan_anomalies, every 30 minutes, in the
dedicated scheduler process (main.py's WORKER_ROLE gate / scheduler_process.py)
— never inside a request-serving worker.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_TIME_BUDGET_SECONDS = 15.0  # abort the rest of this run past this, persist what we have

_ND_AUTO_NAME = "ND. Автоматическая система"
_BAN_EVASION_REASON = "ND. Обратитесь к администратору"
_MAX_PLAYERS_PER_IP = 8  # shared VPN/campus/NAT addresses aren't a twink signal — skip the noise


def _upsert_anomaly(db: Session, category: str, subtype: str, player_id: int,
                     related_player_id: Optional[int], score: int, evidence: dict):
    from models import PlayerAnomaly
    row = (
        db.query(PlayerAnomaly)
        .filter(
            PlayerAnomaly.category == category, PlayerAnomaly.subtype == subtype,
            PlayerAnomaly.player_id == player_id, PlayerAnomaly.related_player_id == related_player_id,
        )
        .first()
    )
    now = datetime.utcnow()
    if row:
        row.score = max(row.score, score)
        row.evidence = evidence
        row.last_seen_at = now
    else:
        row = PlayerAnomaly(
            category=category, subtype=subtype, player_id=player_id,
            related_player_id=related_player_id, score=score, evidence=evidence,
            detected_at=now, last_seen_at=now,
        )
        db.add(row)
        db.flush()
    return row


def _persist_twink_pairs(db: Session, pairs: list) -> int:
    flagged = 0
    for pair in pairs:
        a_id, b_id = sorted((pair["pid_a"], pair["pid_b"]))
        _upsert_anomaly(
            db, "twink", "sauron_pair", a_id, b_id, pair["score"],
            {"risk": pair["risk"], "signals": pair["signals"], "name_a": pair["name_a"], "name_b": pair["name_b"]},
        )
        flagged += 1
    db.commit()
    return flagged


def _persist_deviant_players(db: Session, players: list) -> int:
    flagged = 0
    for entry in players:
        _upsert_anomaly(
            db, "behavior", "sauron_deviant", entry["player_id"], None, entry["total_score"],
            {"risk": entry["risk"], "incidents": entry["incidents"]},
        )
        flagged += 1
    db.commit()
    return flagged


def _auto_ban_evader(db: Session, candidate, ip: str, banned_player) -> bool:
    """Queues an actual ban action + punishment record for a suspected
    ban-evasion alt that's currently online. Best-effort: a failure to queue
    the server action just means no auto-ban this pass, never raises."""
    from models import PlayerPunishment

    already = (
        db.query(PlayerPunishment)
        .filter(PlayerPunishment.player_id == candidate.player_id, PlayerPunishment.type == "ban", PlayerPunishment.is_active == 1)
        .first()
    )
    if already:
        return False

    try:
        from services import ServerModerationService
        from schemas import QueueActionIn
        svc = ServerModerationService(db)
        svc.enqueue_action(
            QueueActionIn(action_type="ban", target_steamid=candidate.player_steamid, reason=_BAN_EVASION_REASON),
            admin_id=None,
        )
    except Exception as e:
        logger.error(f"Anomaly auto-ban: failed to queue ban for {candidate.player_steamid}: {e}")
        return False

    punishment = PlayerPunishment(
        player_id=candidate.player_id,
        player_steamid=candidate.player_steamid,
        player_ip=ip,
        type="ban",
        reason=_BAN_EVASION_REASON,
        issued_by_admin_id=None,
        issued_by_name=_ND_AUTO_NAME,
        issued_by_was_superuser=True,
        is_active=1,
    )
    db.add(punishment)
    db.flush()
    try:
        from routes.game_api import _queue_punishment_discord_notification
        _queue_punishment_discord_notification(db, punishment)
    except Exception as e:
        logger.warning(f"Anomaly auto-ban: Discord notification failed: {e}")

    logger.warning(
        "Auto-banned suspected ban-evasion alt: player_id=%s steamid=%s (matched banned player_id=%s via ip=%s)",
        candidate.player_id, candidate.player_steamid, banned_player.player_id, ip,
    )
    return True


def _scan_ban_evasion(db: Session, deadline: float) -> dict:
    """Scoped ONLY to currently-active bans (typically a small set) — for
    each, find every OTHER account that has ever shared an exact IP, upsert
    a high-confidence "ip_ban_evasion" finding, and auto-ban it if that
    account is online RIGHT NOW and not already banned itself."""
    from models import PlayerData, PlayerIP, PlayerPunishment, PlayerSession

    stats = {"pairs": 0, "flagged": 0, "auto_banned": 0}

    active_bans = (
        db.query(PlayerPunishment)
        .filter(PlayerPunishment.type == "ban", PlayerPunishment.is_active == 1)
        .all()
    )
    banned_player_ids = {b.player_id for b in active_bans if b.player_id}
    ban_by_player = {b.player_id: b for b in active_bans if b.player_id}
    if not banned_player_ids:
        return stats

    online_player_ids = {
        s.player_id for s in db.query(PlayerSession.player_id).filter(PlayerSession.logout_time.is_(None)).all()
    }

    banned_ips = db.query(PlayerIP).filter(PlayerIP.player_id.in_(banned_player_ids)).all()
    ip_to_banned: dict = {}
    for row in banned_ips:
        ip_to_banned.setdefault(row.ip, set()).add(row.player_id)

    for ip, banned_ids_here in ip_to_banned.items():
        if time.monotonic() > deadline:
            logger.warning("Anomaly scan: time budget exceeded mid ban-evasion pass, stopping early")
            break

        sharers = db.query(PlayerIP.player_id).filter(PlayerIP.ip == ip).distinct().all()
        candidate_ids = {r[0] for r in sharers} - banned_ids_here
        if not candidate_ids or len(candidate_ids) + len(banned_ids_here) > _MAX_PLAYERS_PER_IP:
            continue

        candidates = {p.player_id: p for p in db.query(PlayerData).filter(PlayerData.player_id.in_(candidate_ids)).all()}
        for banned_id in banned_ids_here:
            banned_player = db.query(PlayerData).filter(PlayerData.player_id == banned_id).first()
            if not banned_player:
                continue
            punishment = ban_by_player.get(banned_id)
            for cand_id, cand in candidates.items():
                stats["pairs"] += 1
                score = 80
                evidence = {"ip": ip, "signals": ["ip_exact_match_active_ban"]}
                if punishment and punishment.issued_at and cand.first_login:
                    delta = cand.first_login - punishment.issued_at
                    if timedelta(0) <= delta <= timedelta(hours=24):
                        score += 10
                        evidence["signals"].append("new_account_after_ban")

                lo, hi = sorted((banned_id, cand_id))
                row = _upsert_anomaly(db, "twink", "ip_ban_evasion", lo, hi, score, evidence)
                stats["flagged"] += 1

                if cand_id in online_player_ids and cand_id not in banned_player_ids:
                    if _auto_ban_evader(db, cand, ip, banned_player):
                        row.auto_banned = True
                        stats["auto_banned"] += 1
    db.commit()
    return stats


def run_anomaly_scan(db: Session) -> dict:
    """Entry point called by workers.task_scan_anomalies."""
    import services_sauron

    start = time.monotonic()
    deadline = start + _TIME_BUDGET_SECONDS
    result = {"status": "ok"}

    try:
        pairs = services_sauron.detect_all_twink_pairs(db, days=90)
        result["twink_pairs_found"] = len(pairs)
        result["twink_pairs_flagged"] = _persist_twink_pairs(db, pairs)
    except Exception as e:
        logger.error(f"Sauron twink scan failed: {e}")
        db.rollback()
        result["twinks_error"] = str(e)

    if time.monotonic() > deadline:
        logger.warning("Anomaly scan: time budget exceeded after twink pass, skipping the rest this run")
        result["cut_short"] = True
        return result

    try:
        deviants = services_sauron.detect_deviant_behavior(db, days=30, min_incidents=2)
        result["deviant_found"] = len(deviants)
        result["deviant_flagged"] = _persist_deviant_players(db, deviants)
    except Exception as e:
        logger.error(f"Sauron deviant-behavior scan failed: {e}")
        db.rollback()
        result["behavior_error"] = str(e)

    if time.monotonic() > deadline:
        logger.warning("Anomaly scan: time budget exceeded after deviant-behavior pass, skipping ban-evasion check this run")
        result["cut_short"] = True
        return result

    try:
        result["ban_evasion"] = _scan_ban_evasion(db, deadline)
    except Exception as e:
        logger.error(f"Ban-evasion scan failed: {e}")
        db.rollback()
        result["ban_evasion_error"] = str(e)

    result["elapsed_seconds"] = round(time.monotonic() - start, 2)
    return result
