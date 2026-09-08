"""
Background workers for Nexus Data.

All periodic tasks are now registered with the TaskScheduler instead of
spawning their own threads. This gives us:
  - Priority control (API requests always win)
  - Deduplication (no double-runs if a task takes longer than its interval)
  - Result caching (admin panel can poll completion status)
  - Unified logging and monitoring
"""

import ctypes
import ctypes.util
import gc
import logging
import os
import platform
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import GameSessionLocal, SessionLocal

logger = logging.getLogger(__name__)


# ── Memory maintenance helpers ─────────────────────────────────────────────
# Cached libc handle for malloc_trim (Linux/glibc only). `False` is a
# "tried and unavailable" sentinel so we don't re-probe every cycle.
_libc = None


def _get_libc():
    global _libc
    if _libc is None:
        if platform.system() != "Linux":
            _libc = False
        else:
            try:
                _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            except Exception:
                _libc = False
    return _libc or None


def _malloc_trim() -> bool:
    """Ask glibc to return free heap memory to the OS (no-op off Linux/glibc).

    Long-running Python services that periodically build large temporary
    structures (analytics row loads, log parsing, anomaly scans) otherwise
    show RSS that only ever grows and plateaus high — even fully idle —
    because glibc parks freed memory in per-thread arenas rather than handing
    it back. This is the direct fix for "RAM keeps growing on empty"."""
    libc = _get_libc()
    if libc is not None and hasattr(libc, "malloc_trim"):
        try:
            libc.malloc_trim(0)
            return True
        except Exception:
            return False
    return False


def _rss_mb():
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return None

# Try to import task_scheduler — if not present, fall back to simple thread model
try:
    from task_scheduler import scheduler, Priority
    _HAS_SCHEDULER = True
except ImportError:
    logger.warning("[Workers] task_scheduler not found, falling back to simple thread mode")
    _HAS_SCHEDULER = False
    scheduler = None
    Priority = None


# ═══════════════════════════════════════════════════════════════════════
# TASK IMPLEMENTATIONS (each opens its own DB session)
# ═══════════════════════════════════════════════════════════════════════

def task_compensate_playtime() -> dict:
    """Sync play_time from session durations. Runs every 30 min."""
    db = SessionLocal()
    try:
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
        db.commit()
        return {"compensated_players": compensated}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def task_sync_punishment_discord() -> dict:
    """Reconcile linked web punishment names and Discord embeds."""
    from models import AdminUser, DiscordMember, PlayerPunishment, PlayerPunishmentDiscordMessage
    from services import (
        format_admin_issuer_name_from_member,
        punishment_discord_state,
        punishment_discord_state_hash,
    )

    db = SessionLocal()
    try:
        punishments = db.query(PlayerPunishment).filter(
            PlayerPunishment.issued_by_admin_id.isnot(None),
        ).order_by(PlayerPunishment.id).all()
        if not punishments:
            return {"issuer_updated": 0, "discord_synced": 0, "discord_missing": 0, "discord_errors": 0}

        admin_ids = {punishment.issued_by_admin_id for punishment in punishments}
        admins = db.query(AdminUser).filter(AdminUser.id.in_(admin_ids)).all()
        discord_ids = {str(admin.discord_id) for admin in admins if admin.discord_id}
        members = db.query(DiscordMember).filter(DiscordMember.discord_id.in_(discord_ids)).all() if discord_ids else []
        members_by_discord_id = {str(member.discord_id): member for member in members}
        issuer_by_admin_id = {
            admin.id: format_admin_issuer_name_from_member(
                admin, members_by_discord_id.get(str(admin.discord_id)),
            )
            for admin in admins
        }

        issuer_updated = 0
        for punishment in punishments:
            current_name = issuer_by_admin_id.get(punishment.issued_by_admin_id)
            if current_name and punishment.issued_by_name != current_name:
                punishment.issued_by_name = current_name
                issuer_updated += 1
        db.commit()

        link_rows = (
            db.query(PlayerPunishmentDiscordMessage, PlayerPunishment)
            .join(PlayerPunishment, PlayerPunishment.id == PlayerPunishmentDiscordMessage.punishment_id)
            .filter(PlayerPunishment.issued_by_admin_id.isnot(None))
            .all()
        )
        changes = []
        for link, punishment in link_rows:
            if link.sync_status == "missing":
                continue
            issuer_name = issuer_by_admin_id.get(punishment.issued_by_admin_id)
            if not issuer_name:
                continue
            state = punishment_discord_state(punishment, issuer_name)
            state_hash = punishment_discord_state_hash(state)
            if link.sync_status == "synced" and link.state_hash == state_hash:
                continue
            changes.append({
                "link_id": link.id,
                "channel_id": link.channel_id,
                "message_id": link.message_id,
                "state": state,
                "state_hash": state_hash,
            })

        if not changes:
            return {"issuer_updated": issuer_updated, "discord_synced": 0, "discord_missing": 0, "discord_errors": 0}

        from discord_bot import bot as discord_bot
        sync_coro = discord_bot.sync_punishment_discord_messages(changes)
        try:
            future = discord_bot.schedule(
                sync_coro,
                description="Синхронизация наказаний с Discord",
            )
        except RuntimeError as exc:
            sync_coro.close()
            logger.warning("Punishment Discord sync deferred: %s", type(exc).__name__)
            return {"issuer_updated": issuer_updated, "discord_synced": 0, "discord_missing": 0, "discord_errors": len(changes)}
        try:
            outcomes = future.result(timeout=90)
        except TimeoutError as exc:
            logger.warning("Punishment Discord sync deferred: %s", type(exc).__name__)
            return {"issuer_updated": issuer_updated, "discord_synced": 0, "discord_missing": 0, "discord_errors": len(changes)}

        links_by_id = {link.id: link for link, _ in link_rows}
        changes_by_link_id = {change["link_id"]: change for change in changes}
        now = datetime.utcnow()
        discord_synced = discord_missing = discord_errors = 0
        for outcome in outcomes:
            link = links_by_id.get(outcome.get("link_id"))
            if not link:
                continue
            link.last_attempt_at = now
            status = outcome.get("status")
            if status == "synced":
                change = changes_by_link_id.get(link.id)
                if not change:
                    continue
                link.state_hash = change["state_hash"]
                link.sync_status = "synced"
                link.synced_at = now
                link.last_error = None
                discord_synced += 1
            elif status == "missing":
                link.sync_status = "missing"
                link.last_error = (outcome.get("error") or "Discord message not found")[:500]
                discord_missing += 1
            else:
                link.sync_status = "error"
                link.last_error = (outcome.get("error") or "Discord message sync failed")[:500]
                discord_errors += 1
        db.commit()
        return {
            "issuer_updated": issuer_updated,
            "discord_synced": discord_synced,
            "discord_missing": discord_missing,
            "discord_errors": discord_errors,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# 8 часов — порог, выше которого сессия считается битой (зависший logout,
# сбой часов, незакрытая сессия). Такие сессии искажают play_time.
_BROKEN_SESSION_THRESHOLD_SECS = 8 * 3600  # 28800
_BROKEN_SESSION_BATCH = 500


def task_cleanup_broken_sessions() -> dict:
    """Находит битые сессии (>8ч), удаляет их батчами и пересчитывает play_time.

    Эффективность:
      - один SELECT с LIMIT батча, один DELETE по PK, один UPDATE-JOIN для
        пересчёта play_time только затронутых игроков — без full table scan;
      - работает на BACKGROUND приоритете, не конкурирует с API/важными воркерами;
      - идемпотентен и безопасен при конкурентной записи новых сессий.
    Запускается каждый час.
    """
    db = SessionLocal()
    try:
        # 1. Найти батч битых сессий
        rows = db.execute(text("""
            SELECT id, player_id FROM player_sessions
            WHERE session_duration IS NOT NULL
              AND session_duration > :thr
            LIMIT :lim
        """), {"thr": _BROKEN_SESSION_THRESHOLD_SECS, "lim": _BROKEN_SESSION_BATCH}).fetchall()

        if not rows:
            return {"deleted": 0, "recalculated_players": 0, "status": "ok"}

        ids = [r[0] for r in rows]
        player_ids = list({r[1] for r in rows})

        # 2. Удалить битые сессии одним запросом по PK (эффективно, использует primary index)
        # MySQL не позволяет bind список напрямую — строим placeholders.
        # Батч 500 — безопасно для длины запроса.
        placeholders = ",".join(str(int(x)) for x in ids)
        db.execute(text(f"DELETE FROM player_sessions WHERE id IN ({placeholders})"))

        # 3. Пересчитать play_time для затронутых игроков.
        # SUM по оставшимся сессиям; COALESCE -> 0 если сессий не осталось.
        # UPDATE-JOIN — один запрос на всех игроков батча.
        placeholders_p = ",".join(str(int(x)) for x in player_ids)
        db.execute(text(f"""
            UPDATE players_data pd
            LEFT JOIN (
                SELECT player_id, COALESCE(SUM(session_duration), 0) AS total
                FROM player_sessions
                WHERE player_id IN ({placeholders_p})
                  AND session_duration IS NOT NULL
                GROUP BY player_id
            ) st ON st.player_id = pd.player_id
            SET pd.play_time = COALESCE(st.total, 0)
            WHERE pd.player_id IN ({placeholders_p})
        """))
        db.commit()
        logger.info("[BrokenSessions] deleted=%s players_recalculated=%s", len(ids), len(player_ids))
        return {"deleted": len(ids), "recalculated_players": len(player_ids), "status": "ok"}
    except Exception:
        db.rollback()
        logger.exception("[BrokenSessions] failed")
        raise
    finally:
        db.close()


def task_recalculate_ratings() -> dict:
    """Full rating recalculation. Runs every 24h."""
    from rating_engine import calculate_all_ratings, sync_ratings_to_db, apply_daily_erosion
    db = SessionLocal()
    try:
        ratings = calculate_all_ratings(db)
        if ratings:
            sync_ratings_to_db(db, ratings)
        apply_daily_erosion(db)
        return {"players_recalculated": len(ratings)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def task_server_restarts() -> dict:
    """Advance the durable daily restart state for ASPER1 and ASPER2."""
    from services_server_restart import (
        PterodactylClient,
        PterodactylError,
        _condition_state,
        _claim_schedule,
        _next_schedule,
        _utc_aware,
        build_msg_box_command,
        ensure_restart_schedules,
        evaluate_schedule_tick,
        server_state_allows_restart,
        suspend_schedule_for_offline,
    )
    from models import AdminActionLog

    db = SessionLocal()
    current = datetime.now(timezone.utc)
    result = {"checked": 0, "warnings": 0, "restarts": 0, "errors": 0}
    try:
        rows = ensure_restart_schedules(db)
        client = PterodactylClient()
        for row in rows:
            if not row.enabled:
                continue
            result["checked"] += 1
            if row.processing_until and row.processing_until > current.replace(tzinfo=None):
                continue

            try:
                server_state = client.get_state(row.server_name)
            except PterodactylError as exc:
                row.last_status = "error"
                row.last_error = str(exc)
                db.commit()
                result["errors"] += 1
                continue
            if not server_state_allows_restart(server_state):
                suspend_schedule_for_offline(row, current)
                db.commit()
                continue

            online_count, round_active = _condition_state(db, row.server_name)
            decision = evaluate_schedule_tick(
                enabled=bool(row.enabled),
                scheduled_for=_utc_aware(row.scheduled_for),
                execution_due_at=_utc_aware(row.execution_due_at),
                warning_sent_at=_utc_aware(row.warning_sent_at),
                no_online=bool(row.require_no_online),
                round_finished=bool(row.require_round_finished),
                online_count=online_count,
                round_active=round_active,
                now=current,
            )

            if decision.action == "wait_conditions":
                row.last_status = "waiting_conditions"
                db.commit()
                continue
            if decision.action == "noop":
                if row.execution_due_at is None:
                    row.last_status = "scheduled"
                    db.commit()
                continue
            if not _claim_schedule(db, row, current):
                continue

            if decision.action == "send_warning":
                warning_error = None
                try:
                    client.send_console_command(row.server_name, build_msg_box_command(row.message_text))
                except (PterodactylError, ValueError) as exc:
                    # A warning failure must not prevent the actual restart.
                    warning_error = str(exc)
                row.warning_sent_at = current.replace(tzinfo=None)
                row.execution_due_at = decision.execution_due_at.replace(tzinfo=None) if decision.execution_due_at else None
                row.last_status = "warning_sent"
                row.last_error = warning_error
                row.processing_until = None
                row.processing_token = None
                db.add(AdminActionLog(
                    admin_id=None,
                    action="server_restart_automatic_warning",
                    details={
                        "server": row.server_name,
                        "warning_error": warning_error,
                    },
                ))
                db.commit()
                result["warnings"] += 1
                continue

            if decision.action == "restart":
                try:
                    client.restart(row.server_name)
                except PterodactylError as exc:
                    row.last_status = "error"
                    row.last_error = str(exc)
                    row.last_action = "automatic_restart_failed"
                    row.last_action_at = current.replace(tzinfo=None)
                    row.execution_due_at = (current + timedelta(minutes=1)).replace(tzinfo=None)
                    row.processing_until = None
                    row.processing_token = None
                    db.add(AdminActionLog(
                        admin_id=None,
                        action="server_restart_automatic_failed",
                        details={
                            "server": row.server_name,
                            "error": str(exc),
                        },
                    ))
                    db.commit()
                    result["errors"] += 1
                    continue

                row.scheduled_for = _next_schedule(current, row.daily_time)
                row.warning_sent_at = None
                row.execution_due_at = None
                row.last_status = "scheduled"
                row.last_error = None
                row.last_action = "automatic_restart"
                row.last_action_at = current.replace(tzinfo=None)
                row.processing_until = None
                row.processing_token = None
                db.add(AdminActionLog(
                    admin_id=None,
                    action="server_restart_automatic",
                    details={"server": row.server_name},
                ))
                db.commit()
                result["restarts"] += 1
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def task_project_rating_events() -> dict:
    """Retry accepted but not-yet-projected v2 events after transient DB errors."""
    from models import RatingEventLedger
    from routes.rating_events import _project_rating_events

    db = GameSessionLocal()
    try:
        ids = [row.id for row in db.query(RatingEventLedger.id).filter(
            RatingEventLedger.projected_at.is_(None),
        ).order_by(RatingEventLedger.id).limit(500).all()]
    finally:
        db.close()
    if ids:
        _project_rating_events(ids)
    return {"pending_events": len(ids)}


def task_migrate_legacy_rating() -> dict:
    """Import the frozen legacy role-action aggregate once on startup."""
    from rating_legacy_migration import import_legacy_rating_once

    source_db = SessionLocal()
    target_db = GameSessionLocal()
    try:
        return import_legacy_rating_once(source_db, target_db)
    finally:
        source_db.close()
        target_db.close()


def task_check_rating_reward_schedules() -> dict:
    """Проверяет, не наступило ли назначенное время (день недели + время МСК)
    у какого-нибудь правила награды за место в рейтинге, и если да —
    реконсилирует держателей (выдаёт новому, забирает у прежнего, если место
    сменилось). Каждое правило само решает по своему расписанию — задача
    просто дёргает проверку часто (раз в 5 мин), а не спит до полуночи, как
    было раньше (см. services_playtime_stats.reconcile_rating_rewards)."""
    import services_playtime_stats
    return services_playtime_stats.reconcile_rating_rewards()


def task_sauron_twinks() -> dict:
    """Twink detection analysis. Triggered on demand, cached 10 min."""
    import services_sauron
    db = SessionLocal()
    try:
        results = services_sauron.detect_all_twink_pairs(db, days=90)
        return {"results": results, "count": len(results)}
    finally:
        db.close()


def task_sauron_deviant() -> dict:
    """Deviant behavior detection. Triggered on demand, cached 10 min."""
    import services_sauron
    db = SessionLocal()
    try:
        results = services_sauron.detect_deviant_behavior(db, days=30, min_incidents=2)
        return {"results": results, "count": len(results)}
    finally:
        db.close()


def task_sauron_summary() -> dict:
    """Lightweight Sauron summary counts."""
    import services_sauron
    db = SessionLocal()
    try:
        return services_sauron.get_sauron_summary(db)
    finally:
        db.close()


def task_scan_anomalies() -> dict:
    """Twink/alt-account + behavior-outlier detection (Moderation → Аномалии).
    Runs every 30 min — see services_anomaly.py for the actual scans and
    their self-throttling; this just owns the DB session lifecycle like
    every other scheduled task here."""
    import services_anomaly
    db = SessionLocal()
    try:
        return services_anomaly.run_anomaly_scan(db)
    finally:
        db.close()


def task_cleanup_world_rounds() -> dict:
    """Delete stale "Обзор мира" round rows (see routes/world_state.py) —
    this is live-round scratch data, not history worth keeping once a round
    is long over. Runs every 6 hours."""
    from models import WorldRound
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=6)
        deleted = db.query(WorldRound).filter(WorldRound.last_update_at < cutoff).delete(synchronize_session=False)
        db.commit()
        return {"deleted_world_rounds": deleted}
    finally:
        db.close()


def task_cleanup_replays() -> dict:
    """Delete expired replay files. Runs every 6 hours."""
    import services_replays
    db = SessionLocal()
    try:
        deleted = services_replays.ReplayService(db).cleanup_expired()
        return {"deleted_replays": deleted}
    finally:
        db.close()


def task_cleanup_communications() -> dict:
    """Delete expired chat/voice metadata using the admin-configured retention."""
    from routes.communications import purge_expired_communications
    db = SessionLocal()
    try:
        return {"deleted_communications": purge_expired_communications(db)}
    except Exception:
        db.rollback()
        logger.exception("Communication retention cleanup failed")
        raise
    finally:
        db.close()


def task_refresh_logs() -> dict:
    """Refresh server log cache from SFTP. Runs every 30 min."""
    try:
        import services_logs
        services_logs.refresh_all_logs()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def task_auto_link_admins() -> dict:
    """Auto-link players to ND admin accounts when the player has a
    superuser-confirmed Discord ID matching an admin's Discord ID. Never
    overwrites an existing link (manual or previously auto-created honors
    the `linked_admin_auto` flag so a manual override always wins). Runs
    every 10 min.
    """
    db = SessionLocal()
    try:
        from models import PlayerData, AdminUser

        admins_by_discord = {
            a.discord_id: a.id
            for a in db.query(AdminUser).filter(AdminUser.discord_id.isnot(None), AdminUser.is_deleted == 0).all()
        }
        if not admins_by_discord:
            return {"status": "ok", "linked": 0}

        candidates = (
            db.query(PlayerData)
            .filter(
                PlayerData.discord_id.isnot(None),
                PlayerData.discord_id_confirmed == 1,
                PlayerData.linked_admin_id.is_(None),
            )
            .all()
        )

        linked = 0
        for player in candidates:
            admin_id = admins_by_discord.get(player.discord_id)
            if admin_id:
                player.linked_admin_id = admin_id
                player.linked_admin_auto = True
                linked += 1
        if linked:
            db.commit()
        return {"status": "ok", "linked": linked, "checked": len(candidates)}
    except Exception as e:
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def task_sync_department_roles() -> dict:
    """Discord -> site half of department membership sync (background,
    every 1 min). The site -> Discord half is routes/departments.py's
    member add/update/remove endpoints, which queue "sync_department_role"
    bot actions directly (see _queue_role_sync there) — this worker never
    calls that queuing logic itself.

    Full two-way reconciliation, not just additive: an is_auto=True
    DepartmentMember row's held_level_keys/is_head/is_deputy_head are set to
    EXACTLY what the member's current Discord roles grant — gaining a role
    adds the corresponding level, losing one removes it, and losing every
    role-mapped level for a department removes the membership entirely.
    Any synced DiscordMember holding a role that matches a
    DepartmentAccessLevel.discord_role_id for the first time gets an
    AdminUser (created if missing, flagged created_by_worker=True) and a new
    DepartmentMember row (is_auto=True).

    Loop safety: this worker only ever touches is_auto=True rows and never
    queues a Discord-side change itself. The moment a human edits a row
    through the site (add/update/remove), that endpoint flips it to
    is_auto=False in the same request — so this worker immediately stops
    touching it ("a human has taken ownership of this row — leave it
    alone" below), and the endpoint's own Discord push is a one-shot action,
    not something this worker reacts to. Neither side ever re-triggers the
    other, so the two directions can't oscillate — each converges to a
    fixed point (the current Discord role state) instead of looping.
    """
    db = SessionLocal()
    try:
        from models import DiscordMember, DepartmentAccessLevel, DepartmentMember, AdminUser, Department

        levels = db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.discord_role_id.isnot(None)).all()
        departments_with_base = db.query(Department).filter(
            Department.base_discord_role_id.isnot(None), Department.is_archived.is_(False)
        ).all()
        if not levels and not departments_with_base:
            return {"status": "ok", "provisioned": 0, "updated": 0, "removed": 0}

        # discord_role_id -> [(department_id, level_key, kind), ...]
        role_map: dict = {}
        # department_id -> set of every level key that's currently role-mapped
        # in that department — scopes reconciliation to departments where
        # Discord-role sync is actually configured, so a row in a department
        # with no role-mapped levels at all is left alone rather than wiped.
        dept_role_mapped_keys: dict = {}
        # Read the base role straight off Department.base_discord_role_id —
        # NOT only from a "base"-kind DepartmentAccessLevel row. A department
        # only gets that row auto-created at creation time (see
        # api_create_department); setting/changing the base role later
        # through department settings never touches it, so relying solely on
        # DepartmentAccessLevel here silently broke base-role auto-add for
        # any department configured that way.
        for dept in departments_with_base:
            role_map.setdefault(dept.base_discord_role_id, []).append((dept.id, "base", "base"))
            dept_role_mapped_keys.setdefault(dept.id, set()).add("base")
        for lvl in levels:
            role_map.setdefault(lvl.discord_role_id, []).append((lvl.department_id, lvl.key, lvl.kind))
            dept_role_mapped_keys.setdefault(lvl.department_id, set()).add(lvl.key)

        members = db.query(DiscordMember).all()
        provisioned = 0
        updated = 0
        removed = 0

        # Batch-load AdminUser (by discord_id) and every existing
        # DepartmentMember row (by admin_id) up front instead of one query per
        # member inside the loop below. Admins created mid-loop (newly
        # provisioned) are added to admins_by_discord_id as they're created;
        # they have no pre-existing DepartmentMember rows to look up.
        discord_ids = [dm.discord_id for dm in members]
        admins_by_discord_id = {
            a.discord_id: a
            for a in (db.query(AdminUser).filter(AdminUser.discord_id.in_(discord_ids)).all() if discord_ids else [])
        }
        existing_by_admin_id: dict = {}
        if admins_by_discord_id:
            admin_ids = [a.id for a in admins_by_discord_id.values()]
            for row in db.query(DepartmentMember).filter(DepartmentMember.admin_id.in_(admin_ids)).all():
                existing_by_admin_id.setdefault(row.admin_id, {})[row.department_id] = row

        for dm in members:
            role_ids = dm.role_ids or []

            # Which departments (and which level keys within each) this member's CURRENT roles grant.
            grants: dict = {}  # department_id -> {"keys": set(), "is_head": bool, "is_deputy": bool}
            for rid in role_ids:
                for department_id, key, kind in role_map.get(rid, []):
                    grant = grants.setdefault(department_id, {"keys": set(), "is_head": False, "is_deputy": False})
                    grant["keys"].add(key)
                    if kind == "head":
                        grant["is_head"] = True
                    elif kind == "deputy_head":
                        grant["is_deputy"] = True

            admin = admins_by_discord_id.get(dm.discord_id)
            if not admin:
                if not grants:
                    continue  # nothing to provision, nothing to reconcile
                admin = AdminUser(
                    discord_id=dm.discord_id,
                    discord_username=dm.display_name or dm.username or f"User_{dm.discord_id}",
                    discord_avatar=dm.avatar,
                    role="admin",
                    created_by_worker=True,
                )
                db.add(admin)
                db.flush()
                admins_by_discord_id[dm.discord_id] = admin
                provisioned += 1
            elif admin.is_deleted and grants:
                # Regained a department-granting role after being
                # soft-deleted — restore login access. Privileges stay
                # wiped (delete_admin's design); only login + membership
                # are restored, same contract as the manual add-member path.
                admin.is_deleted = 0
                provisioned += 1

            # Must include is_auto=False rows here too — otherwise a manually
            # curated membership that happens to share a (department_id,
            # admin_id) pair with a live role grant looks like "no row
            # exists" below and triggers a duplicate INSERT, which violates
            # DepartmentMember's ux_dept_member unique constraint and fails
            # the whole run (this previously broke the worker outright).
            existing = existing_by_admin_id.setdefault(admin.id, {})
            # Reconcile every department currently granted by role, plus every
            # department with an existing AUTO row that's still role-mapped
            # (so a fully-revoked grant gets cleaned up, not just skipped).
            touched_depts = set(grants.keys()) | {
                dept_id for dept_id, m in existing.items()
                if m.is_auto and dept_id in dept_role_mapped_keys
            }

            for department_id in touched_depts:
                grant = grants.get(department_id)
                member = existing.get(department_id)

                if member is not None and not member.is_auto:
                    continue  # a human has taken ownership of this row — leave it alone

                if grant is None:
                    # Held none of this department's role-mapped levels anymore.
                    if member is not None:
                        db.delete(member)
                        removed += 1
                    continue

                new_keys = sorted(grant["keys"])
                if member is None:
                    db.add(DepartmentMember(
                        department_id=department_id, admin_id=admin.id,
                        held_level_keys=new_keys,
                        is_head=grant["is_head"],
                        is_deputy_head=grant["is_deputy"],
                        is_auto=True,
                        role_state_updated_at=datetime.utcnow(),
                    ))
                    updated += 1
                else:
                    changed = False
                    if sorted(member.held_level_keys or []) != new_keys:
                        member.held_level_keys = new_keys
                        changed = True
                    if member.is_head != grant["is_head"]:
                        member.is_head = grant["is_head"]
                        changed = True
                    if member.is_deputy_head != grant["is_deputy"]:
                        member.is_deputy_head = grant["is_deputy"]
                        changed = True
                    if changed:
                        # Recency signal for task_sync_auto_server_privileges's
                        # "most recent assignment wins" cross-department tie-break.
                        member.role_state_updated_at = datetime.utcnow()
                        updated += 1

        db.commit()

        # Newly-provisioned accounts should also get PlayerData-linked, same
        # as any other admin — reuse the existing safe/idempotent worker.
        if provisioned:
            task_auto_link_admins()

        return {"status": "ok", "provisioned": provisioned, "updated": updated, "removed": removed}
    except Exception as e:
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def task_reconcile_department_discord_roles() -> dict:
    """Soft, additive-only safety net for the site -> Discord half of
    department role sync. routes/departments.py's member add/update/remove
    endpoints already push a one-shot "sync_department_role" DiscordBotAction
    in real time (_queue_role_sync) — this exists only to catch cases where
    that one-shot push never landed (the admin had no discord_id yet at the
    time, the bot was down when the action was queued and somehow lost it,
    etc.). Runs every 30 min.

    Deliberately soft: only ever ADDS a role the site currently says someone
    should hold but their last-known Discord roles (DiscordMember.role_ids)
    don't show yet — never removes one. Removal stays the real-time
    endpoints' job; a background sweep silently stripping a role would be a
    much more surprising failure mode than occasionally re-queuing an add
    that already landed (harmless — the bot skips a role the member already
    has, see discord_bot/bot.py's sync_department_role handler).
    """
    db = SessionLocal()
    try:
        from sqlalchemy.orm import joinedload
        from models import DepartmentMember, Department, DiscordMember
        from discord_bot import bot as discord_bot

        # joinedload admin/department/access_levels so the loop below never
        # lazy-loads a relationship per row (each would otherwise be its own
        # query per membership).
        memberships = (
            db.query(DepartmentMember)
            .join(Department, Department.id == DepartmentMember.department_id)
            .filter(Department.is_archived.is_(False))
            .options(
                joinedload(DepartmentMember.admin),
                joinedload(DepartmentMember.department).joinedload(Department.access_levels),
            )
            .all()
        )

        discord_ids = [m.admin.discord_id for m in memberships if m.admin and m.admin.discord_id]
        snapshots_by_discord_id = {
            s.discord_id: s
            for s in (db.query(DiscordMember).filter(DiscordMember.discord_id.in_(discord_ids)).all() if discord_ids else [])
        }

        queued = 0
        for m in memberships:
            admin = m.admin
            if not admin or not admin.discord_id:
                continue
            dept = m.department
            expected_roles = set()
            if dept.base_discord_role_id:
                expected_roles.add(dept.base_discord_role_id)
            held = m.held_level_keys or []
            for level in dept.access_levels:
                if level.key in held and level.discord_role_id:
                    expected_roles.add(level.discord_role_id)
            if not expected_roles:
                continue

            snapshot = snapshots_by_discord_id.get(admin.discord_id)
            current_roles = set(snapshot.role_ids or []) if snapshot else set()
            for role_id in expected_roles - current_roles:
                discord_bot.schedule(discord_bot.execute_bot_action(
                    "sync_department_role",
                    {"discord_id": admin.discord_id, "add_role_id": role_id},
                ))
                queued += 1

        db.commit()
        return {"status": "ok", "queued": queued}
    except Exception as e:
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def task_sync_department_kits() -> dict:
    """Keep PlayerData.achievements.kval_kits in sync with superuser-defined
    DepartmentKitBinding rows (department access level -> qual-kit). Runs
    every 1 min, same cadence as task_sync_department_roles.

    For each binding, resolves which players are currently eligible (their
    linked admin holds the bound access level in that department) and grants
    the kit; for players who lost eligibility, revokes it — but ONLY if a
    DepartmentKitGrant row proves the worker itself made that exact grant,
    and only if the stored kval_kits value still matches what was granted
    (never clobber a kit a player earned some other way or that an admin
    manually changed since). Same never-clobber contract as
    task_sync_department_roles's is_auto handling.
    """
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        from models import DepartmentKitBinding, DepartmentKitGrant, DepartmentMember, DepartmentAccessLevel, PlayerData

        bindings = db.query(DepartmentKitBinding).all()
        if not bindings:
            return {"status": "ok", "granted": 0, "revoked": 0}

        granted = 0
        revoked = 0

        def _achievements_dict(player) -> dict:
            raw = player.achievements or {}
            return dict(raw) if not isinstance(raw, list) else {}

        for binding in bindings:
            level = db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.id == binding.access_level_id).first()
            if not level:
                continue

            members = db.query(DepartmentMember).filter(DepartmentMember.department_id == level.department_id).all()
            eligible_admin_ids = {m.admin_id for m in members if level.key in (m.held_level_keys or [])}

            existing_grants = db.query(DepartmentKitGrant).filter(DepartmentKitGrant.binding_id == binding.id).all()
            existing_by_player = {g.player_id: g for g in existing_grants}

            # Batch-load every PlayerData row this binding could touch — players
            # linked to an eligible admin (to grant) plus players already holding
            # this binding's grant (to check for revocation) — one query instead
            # of one per member/grant.
            needed_player_ids = set(existing_by_player.keys())
            players_by_admin_id: dict = {}
            players_by_id: dict = {}
            if eligible_admin_ids or needed_player_ids:
                filters = []
                if eligible_admin_ids:
                    filters.append(PlayerData.linked_admin_id.in_(eligible_admin_ids))
                if needed_player_ids:
                    filters.append(PlayerData.player_id.in_(needed_player_ids))
                for p in db.query(PlayerData).filter(or_(*filters)).all():
                    players_by_id[p.player_id] = p
                    if p.linked_admin_id is not None:
                        players_by_admin_id.setdefault(p.linked_admin_id, p)

            holding_player_ids = set()
            for admin_id in eligible_admin_ids:
                player = players_by_admin_id.get(admin_id)
                if player:
                    holding_player_ids.add(player.player_id)

            for player_id in holding_player_ids - set(existing_by_player.keys()):
                player = players_by_id.get(player_id)
                if not player:
                    continue
                ach = _achievements_dict(player)
                kval_kits = dict(ach.get("kval_kits", {}))
                kval_kits[binding.job_id] = binding.kit_level
                ach["kval_kits"] = kval_kits
                player.achievements = ach
                db.add(DepartmentKitGrant(binding_id=binding.id, player_id=player_id))
                granted += 1

            for player_id, grant in existing_by_player.items():
                if player_id in holding_player_ids:
                    continue
                player = players_by_id.get(player_id)
                if player:
                    ach = _achievements_dict(player)
                    kval_kits = dict(ach.get("kval_kits", {}))
                    if kval_kits.get(binding.job_id) == binding.kit_level:
                        del kval_kits[binding.job_id]
                        ach["kval_kits"] = kval_kits
                        player.achievements = ach
                db.delete(grant)
                revoked += 1

        db.commit()
        return {"status": "ok", "granted": granted, "revoked": revoked}
    except Exception as e:
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def task_sync_auto_server_privileges() -> dict:
    """Auto-assigns a ServerPrivilegeGroup to a player based on their linked
    admin's department roles: DepartmentAccessLevel.server_privilege_group_id
    (held via held_level_keys) or Department.head_server_privilege_group_id
    (is_head/is_deputy_head) — both superuser-configured. Runs every 5 min.

    Owns exactly one row per player: the one with is_auto=True. A human's
    manual grant(s) (routes/admin.py's assign endpoints, is_auto=False,
    one per server_scope) live in SEPARATE rows this worker never queries
    for and therefore can never touch — manual and auto sum together at
    read time (privilege_resolution.py) instead of one clobbering the other.

    Tie-break when one admin simultaneously qualifies via multiple sources:
    within one department, by kind (head/deputy_head > qualification > base),
    access-level id desc for ties; ACROSS departments, by
    DepartmentMember.role_state_updated_at — "the most recently assigned
    role wins", per explicit product decision.
    """
    db = SessionLocal()
    try:
        from models import DepartmentMember, Department, PlayerData, ServerPlayerPrivilege

        KIND_RANK = {"head": 3, "deputy_head": 3, "qualification": 2, "base": 1}

        memberships = (
            db.query(DepartmentMember)
            .join(Department, Department.id == DepartmentMember.department_id)
            .filter(Department.is_archived.is_(False))
            .all()
        )
        by_admin: dict = {}
        for m in memberships:
            by_admin.setdefault(m.admin_id, []).append(m)

        # Batch-load one PlayerData per candidate admin instead of a query
        # per admin inside the loop below.
        players_by_admin_id = {
            p.linked_admin_id: p
            for p in db.query(PlayerData).filter(PlayerData.linked_admin_id.in_(by_admin.keys())).all()
        }

        assigned = 0
        cleared = 0

        for admin_id, members in by_admin.items():
            player = players_by_admin_id.get(admin_id)
            if not player or not player.player_steamid:
                continue

            # Best (kind_rank, level_id, group_id) candidate PER department,
            # then pick whichever department's membership was most recently
            # touched — role_state_updated_at is the "most recent
            # assignment" signal for the cross-department tie-break.
            best = None  # (timestamp, group_id, department_id)
            for m in members:
                dept = m.department
                candidates = []
                if (m.is_head or m.is_deputy_head) and dept.head_server_privilege_group_id:
                    candidates.append((3, 0, dept.head_server_privilege_group_id))
                if m.held_level_keys:
                    for level in dept.access_levels:
                        if level.key in m.held_level_keys and level.server_privilege_group_id:
                            candidates.append((KIND_RANK.get(level.kind, 2), level.id, level.server_privilege_group_id))
                if not candidates:
                    continue
                candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
                _, _, group_id = candidates[0]
                ts = m.role_state_updated_at or m.added_at or datetime.min
                if best is None or ts > best[0]:
                    best = (ts, group_id, dept.id)

            # This worker's own row only — a manual grant (is_auto=False)
            # for the same steamid, if any, is a different row entirely and
            # is never matched by this filter, so there's nothing to skip.
            existing = db.query(ServerPlayerPrivilege).filter(
                ServerPlayerPrivilege.steamid == player.player_steamid,
                ServerPlayerPrivilege.is_auto == True,
            ).first()

            if best is None:
                if existing and (existing.server_group_id or existing.auto_source_department_id):
                    existing.server_group_id = None
                    existing.auto_source_department_id = None
                    cleared += 1
                continue

            _, group_id, dept_id = best
            if existing:
                if existing.server_group_id != group_id or existing.auto_source_department_id != dept_id:
                    existing.server_group_id = group_id
                    existing.auto_source_department_id = dept_id
                    assigned += 1
            else:
                db.add(ServerPlayerPrivilege(
                    steamid=player.player_steamid,
                    player_id=player.player_id,
                    server_group_id=group_id,
                    is_auto=True,
                    auto_source_department_id=dept_id,
                ))
                assigned += 1

        db.commit()
        return {"status": "ok", "assigned": assigned, "cleared": cleared}
    except Exception as e:
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def task_auto_close_stale_tickets() -> dict:
    """Queue evidence-first closure for unanswered tickets older than 72h.

    A ticket with a Discord channel stays open in SQL until the bot freezes the
    channel, archives its final history and confirms deletion. Failed actions
    remain retryable, and the bot revalidates the cutoff immediately before
    closing so a reply received while it was offline cancels the stale close.
    """
    STALE_HOURS = 72
    db = SessionLocal()
    try:
        from models import Ticket
        from discord_bot import data as bot_data

        cutoff = datetime.utcnow() - timedelta(hours=STALE_HOURS)
        candidates = db.query(Ticket).filter(Ticket.status != "closed", Ticket.awaiting_admin == 1).all()

        eligible = []
        for ticket in candidates:
            # A manually deleted Discord channel is an evidence exception,
            # not an unanswered ticket eligible for destructive auto-close.
            if getattr(ticket, "archive_state", None) == "incomplete":
                continue
            reference = ticket.last_message_at or ticket.created_at
            if not reference or reference > cutoff:
                continue
            eligible.append((int(ticket.id), str(ticket.discord_channel_id or "")))

        # Release this task's connection before the queue helpers open their
        # short independent transactions.
        db.rollback()
        closed = queued = failed = 0
        closed_by = "Авто-закрытие (нет ответа администрации)"
        cutoff_iso = cutoff.isoformat()
        for ticket_id, channel_id in eligible:
            try:
                if not channel_id:
                    if bot_data.close_ticket(ticket_id, closed_by):
                        closed += 1
                    else:
                        failed += 1
                    continue
                bot_data.enqueue_ticket_bot_action(
                    "ticket_close_channel",
                    {
                        "ticket_id": ticket_id,
                        "channel_id": channel_id,
                        "closed_by": closed_by,
                        "auto_close_before": cutoff_iso,
                    },
                    dedupe_key=f"ticket-close:{ticket_id}",
                )
                queued += 1
            except Exception:
                failed += 1
                logger.exception("Не удалось поставить авто-закрытие тикета #%s в очередь", ticket_id)

        return {"status": "ok", "closed": closed, "queued": queued, "failed": failed}
    except Exception as e:
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def task_cleanup_deleted_tickets() -> dict:
    """Permanently remove ticket history after the 30-day soft-delete window."""
    from services_ticket_management import purge_deleted_tickets

    db = SessionLocal()
    try:
        return purge_deleted_tickets(db)
    except Exception as exc:
        logger.exception("Deleted ticket cleanup failed")
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


def _collect_ticket_media_references(ticket_id: int, value, output: set) -> None:
    """Collect storage identities from direct and nested reply/forward JSON."""
    if isinstance(value, dict):
        namespace = value.get("storage_namespace")
        storage_key = value.get("storage_key")
        if namespace and storage_key:
            output.add((int(ticket_id), str(namespace), str(storage_key)))
        for nested in value.values():
            _collect_ticket_media_references(ticket_id, nested, output)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _collect_ticket_media_references(ticket_id, nested, output)


def task_cleanup_ticket_media_orphans() -> dict:
    """Reclaim crash leftovers without touching live or referenced evidence."""
    from config import get_settings
    from models import TicketMessage
    from services_ticket_media import cleanup_orphaned_ticket_media

    settings = get_settings()
    db = SessionLocal()
    references: set[tuple[int, str, str]] = set()
    try:
        rows = db.query(
            TicketMessage.ticket_id,
            TicketMessage.attachments,
            TicketMessage.reference_snapshot,
        ).yield_per(500)
        for ticket_id, attachments, reference_snapshot in rows:
            _collect_ticket_media_references(ticket_id, attachments, references)
            _collect_ticket_media_references(ticket_id, reference_snapshot, references)
    finally:
        db.close()

    result = cleanup_orphaned_ticket_media(
        references,
        root=settings.ticket_attachments_dir,
        min_age_seconds=settings.ticket_attachment_orphan_grace_seconds,
    )
    if result.get("deleted_files") or result.get("deleted_partials"):
        logger.info(
            "Ticket media cleanup: files=%s partials=%s reclaimed=%s bytes",
            result.get("deleted_files", 0),
            result.get("deleted_partials", 0),
            result.get("reclaimed_bytes", 0),
        )
    return result


def task_fetch_steam_avatars() -> dict:
    """Fetch/refresh Steam avatars for players missing one or with a stale
    (>7 day old) one, via Steam Web API GetPlayerSummaries (up to 100
    SteamIDs per call). No-ops if steam_api_key isn't configured. Runs
    every 15 min, prioritizing recently-active players.
    """
    from config import get_settings
    settings = get_settings()
    if not settings.steam_api_key:
        return {"status": "skipped", "reason": "steam_api_key not configured"}

    db = SessionLocal()
    try:
        from models import PlayerData
        from sqlalchemy import or_

        cutoff = datetime.utcnow() - timedelta(days=7)
        players = (
            db.query(PlayerData)
            .filter(
                PlayerData.player_steamid.isnot(None),
                or_(PlayerData.steam_avatar_fetched_at.is_(None), PlayerData.steam_avatar_fetched_at < cutoff),
            )
            .order_by(PlayerData.last_login_time.desc())
            .limit(100)
            .all()
        )
        steamids = [p.player_steamid for p in players if p.player_steamid and p.player_steamid.isdigit()]
        if not steamids:
            return {"status": "ok", "updated": 0}

        import httpx
        resp = httpx.get(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
            params={"key": settings.steam_api_key, "steamids": ",".join(steamids)},
            timeout=10.0,
        )
        resp.raise_for_status()
        players_json = resp.json().get("response", {}).get("players", [])
        avatar_map = {p["steamid"]: (p.get("avatarfull") or p.get("avatarmedium") or p.get("avatar")) for p in players_json}

        now = datetime.utcnow()
        updated = 0
        for p in players:
            # Mark as attempted regardless of a hit, so private/unresolvable
            # profiles don't get retried every cycle.
            p.steam_avatar_fetched_at = now
            avatar_url = avatar_map.get(p.player_steamid)
            if avatar_url:
                p.steam_avatar_url = avatar_url
                updated += 1
        db.commit()
        return {"status": "ok", "updated": updated, "checked": len(players)}
    except Exception as e:
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def task_cleanup_online_snapshots() -> dict:
    """Delete OnlineSnapshot rows older than 30 days — this table gets a new
    row on every /server/players report with no existing retention, so it
    grows without bound. Runs every 6 hours, same cadence as the other
    cleanup tasks."""
    from models import OnlineSnapshot
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=30)
        deleted = db.query(OnlineSnapshot).filter(OnlineSnapshot.recorded_at < cutoff).delete(synchronize_session=False)
        db.commit()
        return {"deleted_online_snapshots": deleted}
    finally:
        db.close()


def task_cleanup_server_actions() -> dict:
    """Delete ServerAction rows older than 14 days that are fully resolved on
    every server they targeted (status != 0 = pending for both
    status_asper1/status_asper2 — see routes/dispatch.py, which immediately
    marks the non-targeted server's status as done/not-applicable at creation
    time, so this condition is correct even for single-server-targeted
    actions). Never deletes a still-pending action regardless of age. Runs
    every 6 hours."""
    from models import ServerAction
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=14)
        deleted = (
            db.query(ServerAction)
            .filter(
                ServerAction.created_at < cutoff,
                ServerAction.status_asper1 != 0,
                ServerAction.status_asper2 != 0,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"deleted_server_actions": deleted}
    finally:
        db.close()


def task_memory_maintenance() -> dict:
    """Periodic memory hygiene (every 5 min) — the "фоновая очистка памяти":
      1. gc.collect() — promptly reclaim cyclic garbage. SQLAlchemy ORM
         instances, closures and the like form reference cycles the
         generational collector would otherwise sit on for a while.
      2. malloc_trim(0) on Linux/glibc — hand freed heap back to the OS so
         RSS actually goes down after the heavy periodic tasks release their
         temporary allocations (see _malloc_trim's docstring — this is the
         core fix for idle RAM growth).
      3. Evict stale in-process TTL cache entries (dept-site-permissions),
         so memory isn't held for admins who stopped making requests.
    Cheap and safe; logs the RSS delta so the effect is observable."""
    import security

    rss_before = _rss_mb()
    collected = gc.collect()
    trimmed = _malloc_trim()
    try:
        evicted = security.evict_stale_dept_site_perms()
    except Exception:
        evicted = 0
    rss_after = _rss_mb()

    freed = round(rss_before - rss_after, 1) if (rss_before is not None and rss_after is not None) else None
    logger.info(
        "[MemMaint] gc_collected=%s malloc_trim=%s cache_evicted=%s rss_mb=%s->%s (freed=%s)",
        collected, trimmed, evicted, rss_before, rss_after, freed,
    )
    return {
        "status": "ok",
        "gc_collected": collected,
        "malloc_trimmed": trimmed,
        "cache_evicted": evicted,
        "rss_mb_before": rss_before,
        "rss_mb_after": rss_after,
        "rss_mb_freed": freed,
    }


def task_sample_perf_resources() -> dict:
    """CPU/RAM-сэмпл процесса+системы для страницы «Мониторинг
    производительности» — см. services_perf_monitor.py про то, почему это
    не пишет в БД на каждый вызов (только редкие аномалии) и почему это
    BACKGROUND-приоритет (никогда не конкурирует с обслуживанием запросов).
    Runs every 30s."""
    import services_perf_monitor
    return services_perf_monitor.sample_resources()


def task_check_db_health() -> dict:
    """Lightweight periodic `SELECT 1` liveness probe, feeding
    runtime_control.set_db_healthy() automatically instead of relying solely
    on someone running the console's manual `db check`. Runs every 30s."""
    import runtime_control
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        runtime_control.set_db_healthy(True)
        return {"status": "ok", "db_healthy": True}
    except Exception as e:
        runtime_control.set_db_healthy(False)
        return {"status": "error", "db_healthy": False, "error": str(e)}
    finally:
        db.close()


def task_llm_monster_auto_cycle() -> dict:
    """LLM-assistant auto-cycle for MonsterSpawnController: for every server with
    the assistant enabled and its per-server interval elapsed, runs one
    validated apply-cycle. The scheduler calls this every 5 min at BACKGROUND
    priority; the service itself decides per server whether a run is due and
    never raises (all failures are captured per server)."""
    import services_llm_monster
    try:
        results = services_llm_monster.run_auto_cycle_for_all_servers()
        return {"status": "ok", "servers": results}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def task_refresh_analytics() -> dict:
    """Recompute the analytics dashboard's default-view snapshot. Runs every 5 min.

    Serves /adminold/analytics so the page doesn't recompute all charts on every visit.
    """
    import services_analytics
    db = SessionLocal()
    try:
        return services_analytics.compute_default_snapshot(db)
    finally:
        db.close()


def task_process_ai_dispatcher_reminders() -> dict:
    """Enqueue due durable AI notes without importing routes at startup."""

    from routes.dispatch import _queue_due_ai_dispatcher_reminders

    return _queue_due_ai_dispatcher_reminders()


def task_process_ai_dossier() -> dict:
    """Process at most one durable dossier request; KoboldCpp is serial by design."""
    from services_ai_dossier import process_one_dossier
    return process_one_dossier()


def task_enqueue_daily_ai_dossiers() -> dict:
    """Queue active profiles once during the configured quiet hour."""
    from zoneinfo import ZoneInfo
    from config import get_settings
    from services_ai_dossier import get_dossier_config, run_daily_dossier_batch
    db = SessionLocal()
    try:
        cfg = get_dossier_config(db)
        local_now = datetime.now(ZoneInfo(get_settings().logs_default_timezone))
        if cfg["test_mode"] or not cfg["enabled"] or not cfg["daily_enabled"] or local_now.hour != cfg["schedule_hour"]:
            return {"status": "outside_schedule"}
        return run_daily_dossier_batch()
    finally:
        db.close()


def task_create_encrypted_backup() -> dict:
    """Create and upload one encrypted off-site backup.

    The implementation has its own non-blocking lock, so even a slow SFTP
    destination cannot create overlapping backup runs.
    """

    from services_backup import create_and_upload_backup

    return create_and_upload_backup()


# ═══════════════════════════════════════════════════════════════════════
# STARTUP: register all periodic tasks and start scheduler
# ═══════════════════════════════════════════════════════════════════════

def start_playtime_worker():
    """Legacy entrypoint — now starts the full scheduler with all tasks."""
    if _HAS_SCHEDULER:
        _start_scheduler()
    else:
        _start_simple_workers()


def _start_simple_workers():
    """Fallback: simple daemon threads without scheduler (when task_scheduler.py is missing)."""
    import time

    def _loop(fn, interval):
        while True:
            try:
                fn()
            except Exception as e:
                logger.error(f"Worker {fn.__name__} error: {e}")
            time.sleep(interval)

    periodic_tasks = [
        (task_compensate_playtime, 30 * 60),
        (task_recalculate_ratings, 24 * 60 * 60),
        (task_migrate_legacy_rating, 30),
        (task_project_rating_events, 10),
        (task_check_rating_reward_schedules, 5 * 60),
        (task_cleanup_replays, 6 * 60 * 60),
        (task_cleanup_communications, 6 * 60 * 60),
        (task_auto_link_admins, 10 * 60),
        (task_sync_department_roles, 10 * 60),
        (task_sync_department_kits, 60),
        (task_fetch_steam_avatars, 15 * 60),
        (task_auto_close_stale_tickets, 30 * 60),
        (task_cleanup_deleted_tickets, 30 * 60),
        (task_cleanup_ticket_media_orphans, 6 * 60 * 60),
        (task_sync_auto_server_privileges, 5 * 60),
        (task_sync_punishment_discord, 10 * 60),
        (task_reconcile_department_discord_roles, 30 * 60),
        (task_cleanup_online_snapshots, 6 * 60 * 60),
        (task_cleanup_server_actions, 6 * 60 * 60),
        (task_check_db_health, 30),
        (task_sample_perf_resources, 30),
        (task_memory_maintenance, 5 * 60),
        (task_process_ai_dispatcher_reminders, 5),
        (task_process_ai_dossier, 5),
        (task_enqueue_daily_ai_dossiers, 15 * 60),
        (task_cleanup_broken_sessions, 60 * 60),
    ]
    from config import get_settings
    backup_settings = get_settings()
    if backup_settings.backup_enabled:
        periodic_tasks.append(
            (task_create_encrypted_backup, backup_settings.backup_interval_hours * 60 * 60)
        )

    for fn, interval in periodic_tasks:
        t = threading.Thread(target=_loop, args=(fn, interval), daemon=True, name=f"Worker-{fn.__name__}")
        t.start()
        logger.info(f"[Workers] Started fallback worker: {fn.__name__} every {interval}s")


def _start_scheduler():
    """Register periodic tasks and start the scheduler."""
    # Playtime compensation: LOW priority, every 30 min
    scheduler.register_periodic(
        task_compensate_playtime,
        interval_secs=30 * 60,
        priority=Priority.LOW,
        task_id="playtime_compensation",
    )

    # Rating recalculation: LOW priority, every 24h
    scheduler.register_periodic(
        task_recalculate_ratings,
        interval_secs=24 * 60 * 60,
        priority=Priority.LOW,
        task_id="rating_recalc",
    )

    # The old role-action aggregate has no round/mode context. Import it once
    # as explicitly tagged Mission history before new v2 events accumulate.
    scheduler.register_periodic(
        task_migrate_legacy_rating,
        interval_secs=30,
        priority=Priority.NORMAL,
        task_id="rating_legacy_migration",
    )

    # Accepted v2 events are durable before projection. This short retry loop
    # makes a transient DB lock or worker exception observable and recoverable.
    scheduler.register_periodic(
        task_project_rating_events,
        interval_secs=10,
        priority=Priority.NORMAL,
        task_id="rating_v2_projection",
    )

    # Награды за место в рейтинге: правило само решает по своему расписанию
    # (день недели + время МСК), проверка — раз в 5 мин, LOW priority.
    scheduler.register_periodic(
        task_check_rating_reward_schedules,
        interval_secs=5 * 60,
        priority=Priority.LOW,
        task_id="rating_reward_schedules",
    )

    # Replay cleanup: BACKGROUND priority, every 6 hours
    scheduler.register_periodic(
        task_cleanup_replays,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="replay_cleanup",
    )

    scheduler.register_periodic(
        task_cleanup_communications,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="communications_cleanup",
    )

    # "Обзор мира" stale round cleanup: BACKGROUND priority, every 6 hours
    scheduler.register_periodic(
        task_cleanup_world_rounds,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="world_rounds_cleanup",
    )

    # Daily production-server restart controller. It is deliberately a short
    # poll: the warning window is 15 seconds, and the durable lease prevents
    # duplicate commands if a tick overlaps or the process has multiple loops.
    scheduler.register_periodic(
        task_server_restarts,
        interval_secs=5,
        priority=Priority.NORMAL,
        task_id="server_restarts",
        timeout=30,
    )

    # Anomaly scan (twinks + behavior outliers): BACKGROUND priority, every
    # 30 min — the heaviest new task here, deliberately lowest priority and
    # internally self-throttled (services_anomaly.py) so it never competes
    # with request-serving work for the database.
    scheduler.register_periodic(
        task_scan_anomalies,
        interval_secs=30 * 60,
        priority=Priority.BACKGROUND,
        task_id="anomaly_scan",
    )

    # Log refresh: handled by services_logs built-in worker (interval set to 60s there)
    # No need to duplicate in scheduler

    # Analytics dashboard snapshot: LOW priority, every 5 min
    scheduler.register_periodic(
        task_refresh_analytics,
        interval_secs=5 * 60,
        priority=Priority.LOW,
        task_id="analytics_refresh",
    )

    # Auto-link ND admins to players by confirmed Discord ID: BACKGROUND priority, every 10 min
    scheduler.register_periodic(
        task_auto_link_admins,
        interval_secs=10 * 60,
        priority=Priority.BACKGROUND,
        task_id="auto_link_admins",
    )

    # Department role sync: two-way department <-> Discord role reconciliation
    # — BACKGROUND priority, every 1 min
    scheduler.register_periodic(
        task_sync_department_roles,
        interval_secs=60,
        priority=Priority.BACKGROUND,
        task_id="department_role_sync",
    )

    # Department kit sync: reconcile qual-kit grants against DepartmentKitBinding
    # — BACKGROUND priority, every 1 min
    scheduler.register_periodic(
        task_sync_department_kits,
        interval_secs=60,
        priority=Priority.BACKGROUND,
        task_id="department_kit_sync",
    )

    # Steam avatar refresh: BACKGROUND priority, every 15 min
    scheduler.register_periodic(
        task_fetch_steam_avatars,
        interval_secs=15 * 60,
        priority=Priority.BACKGROUND,
        task_id="steam_avatars",
    )

    # Auto-close stale tickets: BACKGROUND priority, every 30 min
    scheduler.register_periodic(
        task_auto_close_stale_tickets,
        interval_secs=30 * 60,
        priority=Priority.BACKGROUND,
        task_id="ticket_auto_close",
    )

    # Deleted-ticket retention: BACKGROUND priority, every 30 min. The
    # service deletes TicketMessage rows before Ticket because the historical
    # foreign key has no database-level cascade.
    scheduler.register_periodic(
        task_cleanup_deleted_tickets,
        interval_secs=30 * 60,
        priority=Priority.BACKGROUND,
        task_id="ticket_deleted_cleanup",
    )

    # Reclaim interrupted `.part` files and files atomically written just
    # before a process crash prevented the TicketMessage commit. A 24-hour
    # grace (configurable) prevents races with live requests.
    scheduler.register_periodic(
        task_cleanup_ticket_media_orphans,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="ticket_media_cleanup",
    )

    # Auto server-privilege-group sync from department roles: BACKGROUND
    # priority, every 5 min
    scheduler.register_periodic(
        task_sync_auto_server_privileges,
        interval_secs=5 * 60,
        priority=Priority.BACKGROUND,
        task_id="auto_server_privileges_sync",
    )

    scheduler.register_periodic(
        task_sync_punishment_discord,
        interval_secs=10 * 60,
        priority=Priority.BACKGROUND,
        task_id="punishment_discord_sync",
        timeout=120,
    )

    # Soft, additive-only safety net for site -> Discord department role
    # push: BACKGROUND priority, every 30 min
    scheduler.register_periodic(
        task_reconcile_department_discord_roles,
        interval_secs=30 * 60,
        priority=Priority.BACKGROUND,
        task_id="department_discord_role_reconcile",
    )

    # Online-snapshot retention (30 days): BACKGROUND priority, every 6 hours
    scheduler.register_periodic(
        task_cleanup_online_snapshots,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="online_snapshots_cleanup",
    )

    # Fully-resolved ServerAction retention (14 days): BACKGROUND priority, every 6 hours
    scheduler.register_periodic(
        task_cleanup_server_actions,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="server_actions_cleanup",
    )

    # DB liveness probe: CRITICAL priority (must never be starved by
    # everything else queued), every 30s
    scheduler.register_periodic(
        task_check_db_health,
        interval_secs=30,
        priority=Priority.CRITICAL,
        task_id="db_health_check",
    )

    # LLM-assistant auto-cycle for MonsterSpawnController: BACKGROUND priority,
    # every 5 min; the service self-gates per server on its configured
    # interval (0 = manual runs only), so this stays a cheap no-op until the
    # operator enables the assistant.
    scheduler.register_periodic(
        task_llm_monster_auto_cycle,
        interval_secs=5 * 60,
        priority=Priority.BACKGROUND,
        task_id="llm_monster_auto_cycle",
    )

    # Мониторинг производительности: BACKGROUND priority — сам по себе не
    # должен ни на миг задержать обслуживание запросов (см. services_perf_
    # monitor.py). Every 30s — тот же порядок, что и у liveness-проверки
    # выше, достаточно часто для графика, не создаёт заметной нагрузки.
    scheduler.register_periodic(
        task_sample_perf_resources,
        interval_secs=30,
        priority=Priority.BACKGROUND,
        task_id="perf_resource_sample",
    )

    # Memory maintenance (gc + malloc_trim + cache eviction): LOW priority so
    # it runs promptly rather than sitting behind the BACKGROUND cleanups,
    # every 5 min. See task_memory_maintenance / _malloc_trim.
    scheduler.register_periodic(
        task_memory_maintenance,
        interval_secs=5 * 60,
        priority=Priority.LOW,
        task_id="memory_maintenance",
    )

    scheduler.register_periodic(
        task_process_ai_dispatcher_reminders,
        interval_secs=5,
        priority=Priority.NORMAL,
        task_id="ai_dispatcher_reminders",
    )

    # One task id plus a single durable claim keeps KoboldCpp from receiving
    # concurrent prompts even when a previous generation is slow.
    scheduler.register_periodic(
        task_process_ai_dossier,
        interval_secs=5,
        priority=Priority.NORMAL,
        task_id="ai_dossier_worker",
        timeout=None,
    )
    scheduler.register_periodic(
        task_enqueue_daily_ai_dossiers,
        interval_secs=15 * 60,
        priority=Priority.BACKGROUND,
        task_id="ai_dossier_daily_batch",
        timeout=120,
    )

    # Broken sessions (>8h) cleanup + play_time recalc: BACKGROUND priority
    # (не мешает важным процессам), раз в час, батчами по 500.
    scheduler.register_periodic(
        task_cleanup_broken_sessions,
        interval_secs=60 * 60,
        priority=Priority.BACKGROUND,
        task_id="broken_sessions_cleanup",
    )

    # Encrypted off-site backup.  It starts only when explicitly enabled and
    # has no scheduler timeout: database dumps/uploads can legitimately take
    # longer than the generic 120-second task limit.  One of the two worker
    # threads remains free for normal maintenance while this BACKGROUND task
    # runs, and services_backup prevents overlap itself.
    from config import get_settings
    backup_settings = get_settings()
    if backup_settings.backup_enabled:
        scheduler.register_periodic(
            task_create_encrypted_backup,
            interval_secs=backup_settings.backup_interval_hours * 60 * 60,
            priority=Priority.BACKGROUND,
            task_id="encrypted_sftp_backup",
            timeout=None,
        )

    scheduler.start()
    logger.info("[Workers] All periodic tasks registered and scheduler started")


# ═══════════════════════════════════════════════════════════════════════
# RUNTIME CONTROL — lets the "workers on/off/restart" console command (see
# console.py) stop/restart every background subsystem here without killing
# the process they run in. _start_scheduler() above only ever registers
# periodic tasks ONCE at process startup; stop_all_workers()/
# start_all_workers() below intentionally never call it again — restarting
# just resumes the SAME already-registered periodic list via
# scheduler.start() (see task_scheduler.py's now-restartable stop()),
# instead of duplicating every registration and doubling job frequency.
# ═══════════════════════════════════════════════════════════════════════

def stop_all_workers() -> None:
    """Stop every background subsystem (task scheduler + log/playtime-stats/
    replay workers) without killing the process. Safe to call even if
    task_scheduler isn't available (_HAS_SCHEDULER=False) or workers were
    never started."""
    if _HAS_SCHEDULER and scheduler is not None:
        scheduler.stop()
    try:
        from services_logs import stop_log_worker
        stop_log_worker()
    except Exception:
        logger.exception("[Workers] Failed to stop log worker")
    try:
        from services_playtime_stats import stop_playtime_stats_worker
        stop_playtime_stats_worker()
    except Exception:
        logger.exception("[Workers] Failed to stop playtime stats worker")
    try:
        from services_replays import stop_replay_worker
        stop_replay_worker()
    except Exception:
        logger.exception("[Workers] Failed to stop replay worker")
    logger.info("[Workers] All background workers stopped")


def start_all_workers() -> None:
    """Restart every background subsystem after stop_all_workers() — resumes
    the scheduler's existing periodic-task list (registered once at process
    startup) rather than re-registering it."""
    if _HAS_SCHEDULER and scheduler is not None:
        scheduler.start()
    from services_logs import start_log_worker
    start_log_worker()
    from services_playtime_stats import start_playtime_stats_worker
    start_playtime_stats_worker()
    from services_replays import start_replay_worker
    start_replay_worker()
    logger.info("[Workers] All background workers started")


def watch_runtime_state(poll_secs: float = 5.0) -> None:
    """Daemon thread: reacts to the "workers on/off" flag in
    runtime_control's shared state file. The console (console.py) only ever
    writes that file — this is what actually stops/starts the workers,
    wherever they happen to live (in-process, or the dedicated
    scheduler_process.py in multi-worker mode). Call once, right after the
    workers you want it to manage have been started."""
    import time
    import runtime_control

    def _loop():
        enabled = True  # workers start enabled — see _start_scheduler()/start_playtime_worker()
        while True:
            time.sleep(poll_secs)
            try:
                state = runtime_control.read_state(force=True)
            except Exception:
                continue
            if state.workers_enabled and not enabled:
                logger.info("[Workers] runtime_state: workers_enabled=True — restarting")
                start_all_workers()
                enabled = True
            elif not state.workers_enabled and enabled:
                logger.info("[Workers] runtime_state: workers_enabled=False — stopping")
                stop_all_workers()
                enabled = False

    threading.Thread(target=_loop, daemon=True, name="RuntimeStateWatcher").start()
