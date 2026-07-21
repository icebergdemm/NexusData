"""
Background workers for Nexus Data.

All periodic tasks are now registered with the TaskScheduler instead of
spawning their own threads. This gives us:
  - Priority control (API requests always win)
  - Deduplication (no double-runs if a task takes longer than its interval)
  - Result caching (admin panel can poll completion status)
  - Unified logging and monitoring
"""

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal

logger = logging.getLogger(__name__)

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

            admin = db.query(AdminUser).filter(AdminUser.discord_id == dm.discord_id).first()
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
            existing = {
                m.department_id: m
                for m in db.query(DepartmentMember).filter(DepartmentMember.admin_id == admin.id).all()
            }
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
        from models import DepartmentMember, Department, DiscordMember, DiscordBotAction

        memberships = (
            db.query(DepartmentMember)
            .join(Department, Department.id == DepartmentMember.department_id)
            .filter(Department.is_archived.is_(False))
            .all()
        )
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

            snapshot = db.query(DiscordMember).filter(DiscordMember.discord_id == admin.discord_id).first()
            current_roles = set(snapshot.role_ids or []) if snapshot else set()
            for role_id in expected_roles - current_roles:
                db.add(DiscordBotAction(
                    action_type="sync_department_role",
                    payload={"discord_id": admin.discord_id, "add_role_id": role_id},
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
            holding_player_ids = set()
            for m in members:
                if level.key not in (m.held_level_keys or []):
                    continue
                player = db.query(PlayerData).filter(PlayerData.linked_admin_id == m.admin_id).first()
                if player:
                    holding_player_ids.add(player.player_id)

            existing_grants = db.query(DepartmentKitGrant).filter(DepartmentKitGrant.binding_id == binding.id).all()
            existing_by_player = {g.player_id: g for g in existing_grants}

            for player_id in holding_player_ids - set(existing_by_player.keys()):
                player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
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
                player = db.query(PlayerData).filter(PlayerData.player_id == player_id).first()
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

    Never touches a ServerPlayerPrivilege row a human manually assigned
    (is_auto=False) — routes/admin.py's api_assign_server_privilege always
    flips is_auto back to False on any manual assign, same never-clobber
    contract as DepartmentMember.is_auto/PlayerData.linked_admin_auto.

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

        assigned = 0
        cleared = 0

        for admin_id, members in by_admin.items():
            player = db.query(PlayerData).filter(PlayerData.linked_admin_id == admin_id).first()
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

            existing = db.query(ServerPlayerPrivilege).filter(ServerPlayerPrivilege.steamid == player.player_steamid).first()
            if existing and not existing.is_auto:
                continue  # a human manually assigned this — never touch it

            if best is None:
                if existing and existing.is_auto and (existing.server_group_id or existing.auto_source_department_id):
                    existing.server_group_id = None
                    existing.auto_source_department_id = None
                    cleared += 1
                continue

            _, group_id, dept_id = best
            if existing:
                if existing.server_group_id != group_id or existing.auto_source_department_id != dept_id or not existing.is_auto:
                    existing.server_group_id = group_id
                    existing.auto_source_department_id = dept_id
                    existing.is_auto = True
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
    """Auto-close tickets that have been waiting on an admin reply for too
    long (item 13) — mirrors the site panel's own close flow exactly
    (routes/tickets_api.py's api_ticket_close: mark closed + queue
    "ticket_close_channel", inheriting that action's existing tolerance for
    a channel that's already gone). Only tickets still flagged
    awaiting_admin=1 (genuinely unanswered, never engaged) are eligible —
    a ticket an admin already replied to is left alone even if quiet since,
    since it may just be waiting on the reporter. Threshold is a fixed 72h
    of no reply; tune STALE_HOURS below if that's too aggressive/lax. Runs
    every 30 min.
    """
    STALE_HOURS = 72
    db = SessionLocal()
    try:
        from models import Ticket, DiscordBotAction

        cutoff = datetime.utcnow() - timedelta(hours=STALE_HOURS)
        candidates = db.query(Ticket).filter(Ticket.status != "closed", Ticket.awaiting_admin == 1).all()

        closed = 0
        for ticket in candidates:
            reference = ticket.last_message_at or ticket.created_at
            if not reference or reference > cutoff:
                continue
            ticket.status = "closed"
            ticket.closed_by = "Авто-закрытие (нет ответа администрации)"
            ticket.closed_at = datetime.utcnow()
            if ticket.discord_channel_id:
                db.add(DiscordBotAction(
                    action_type="ticket_close_channel",
                    payload={"ticket_id": ticket.id, "channel_id": ticket.discord_channel_id},
                ))
            closed += 1

        db.commit()
        return {"status": "ok", "closed": closed}
    except Exception as e:
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


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

    for fn, interval in [
        (task_compensate_playtime, 30 * 60),
        (task_recalculate_ratings, 24 * 60 * 60),
        (task_cleanup_replays, 6 * 60 * 60),
        (task_auto_link_admins, 10 * 60),
        (task_sync_department_roles, 10 * 60),
        (task_sync_department_kits, 60),
        (task_fetch_steam_avatars, 15 * 60),
        (task_auto_close_stale_tickets, 30 * 60),
        (task_sync_auto_server_privileges, 5 * 60),
        (task_reconcile_department_discord_roles, 30 * 60),
    ]:
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

    # Replay cleanup: BACKGROUND priority, every 6 hours
    scheduler.register_periodic(
        task_cleanup_replays,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="replay_cleanup",
    )

    # "Обзор мира" stale round cleanup: BACKGROUND priority, every 6 hours
    scheduler.register_periodic(
        task_cleanup_world_rounds,
        interval_secs=6 * 60 * 60,
        priority=Priority.BACKGROUND,
        task_id="world_rounds_cleanup",
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

    # Auto server-privilege-group sync from department roles: BACKGROUND
    # priority, every 5 min
    scheduler.register_periodic(
        task_sync_auto_server_privileges,
        interval_secs=5 * 60,
        priority=Priority.BACKGROUND,
        task_id="auto_server_privileges_sync",
    )

    # Soft, additive-only safety net for site -> Discord department role
    # push: BACKGROUND priority, every 30 min
    scheduler.register_periodic(
        task_reconcile_department_discord_roles,
        interval_secs=30 * 60,
        priority=Priority.BACKGROUND,
        task_id="department_discord_role_reconcile",
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
