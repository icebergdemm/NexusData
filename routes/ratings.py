"""
Admin panel route for Rating leaderboard page + admin actions + snapshots.
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional
import secrets
import logging
import json
import os
from datetime import datetime, timedelta

from database import get_db
from security import check_permission
from config import get_settings
from models import AdminUser, AdminSession, PlayerRating, PlayerData, RatingHistory


router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(check_permission=check_permission)
settings = get_settings()
logger = logging.getLogger(__name__)

SNAPSHOTS_DIR = os.path.join("uploads", "rating_snapshots")
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)


def get_snapshots_list() -> list:
    """Get list of saved rating snapshots sorted by date desc."""
    snapshots = []
    if not os.path.exists(SNAPSHOTS_DIR):
        return snapshots
    for fname in sorted(os.listdir(SNAPSHOTS_DIR), reverse=True):
        if fname.endswith(".json"):
            fpath = os.path.join(SNAPSHOTS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    meta = json.load(f).get("meta", {})
                snapshots.append({
                    "filename": fname,
                    "title": meta.get("title", fname),
                    "created_at": meta.get("created_at", ""),
                    "date_from": meta.get("date_from"),
                    "date_to": meta.get("date_to"),
                    "total_players": meta.get("total_players", 0),
                })
            except Exception:
                snapshots.append({"filename": fname, "title": fname, "created_at": "", "date_from": None, "date_to": None, "total_players": 0})
    return snapshots


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


def push_flash(request: Request, level: str, message: str):
    flashes = request.session.get("_flashes", [])
    flashes.append({"level": level, "message": message})
    request.session["_flashes"] = flashes


def pop_flashes(request: Request):
    flashes = request.session.get("_flashes", [])
    if "_flashes" in request.session:
        request.session.pop("_flashes", None)
    return flashes


@router.get("/adminold/ratings", response_class=HTMLResponse)
async def admin_ratings_page(
    request: Request,
    role: str = Query("captain"),
    search: Optional[str] = Query(None),
    tab: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Rating leaderboard page with admin tools."""
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    from rating_constants import ROLES, K, S, DECAY_THRESHOLD_DAYS, DECAY_RATE

    if role not in ROLES:
        role = "captain"

    # Search by steamid or name
    search_result = None
    if search and search.strip():
        q = search.strip()
        # Prefer exact SteamID match; fall back to name substring only if no SteamID hit.
        player = db.query(PlayerData).filter(PlayerData.player_steamid == q).first()
        if not player:
            player = db.query(PlayerData).filter(
                PlayerData.player_last_time_name.ilike(f"%{q}%")
            ).first()
        if player:
            # Get all ratings for this player
            records = db.query(PlayerRating).filter(PlayerRating.steam_id == player.player_steamid).all()
            search_result = {
                "player": player,
                "ratings": {r.role: {"rating": round(r.current_rating, 2), "points": round(r.cumulative_points, 1), "last_activity": r.last_activity_date} for r in records},
            }

    # Get top players for selected role
    top_records = (
        db.query(PlayerRating)
        .filter(PlayerRating.role == role, PlayerRating.current_rating > 0)
        .order_by(desc(PlayerRating.current_rating))
        .limit(100)
        .all()
    )

    # Enrich with player names
    steam_ids = [r.steam_id for r in top_records]
    players_map = {}
    if steam_ids:
        players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steam_ids)).all()
        players_map = {p.player_steamid: p for p in players}

    leaderboard = []
    for i, record in enumerate(top_records, 1):
        player = players_map.get(record.steam_id)
        leaderboard.append({
            "rank": i,
            "steam_id": record.steam_id,
            "player_name": (player.forced_display_name or player.player_last_time_name) if player else record.steam_id,
            "player_id": player.player_id if player else None,
            "rating": round(record.current_rating, 2),
            "cumulative_points": round(record.cumulative_points, 1),
            "last_activity": record.last_activity_date,
        })

    # Rating params (for superuser config panel)
    rating_params = {
        "K": K,
        "S": S,
        "decay_threshold_days": DECAY_THRESHOLD_DAYS,
        "decay_rate": DECAY_RATE,
    }

    return templates.TemplateResponse("admin/ratings.html", {
        "request": request,
        "admin": current_admin,
        "leaderboard": leaderboard,
        "selected_role": role,
        "roles": ROLES,
        "search_query": search,
        "search_result": search_result,
        "rating_params": rating_params,
        "snapshots": get_snapshots_list(),
        "tab": tab or "",
        "flashes": pop_flashes(request),
    })


@router.get("/adminold/ratings/rewards", response_class=HTMLResponse)
async def admin_rewards_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Rewards configuration page (groups, rating conditions, playtime conditions)."""
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    from rating_constants import ROLES

    return templates.TemplateResponse("admin/rewards.html", {
        "request": request,
        "admin": current_admin,
        "roles": ROLES,
        "can_edit": check_permission(current_admin, "ratings", "edit"),
        "flashes": pop_flashes(request),
    })


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN ACTIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/ratings/reset")
async def admin_rating_reset(
    request: Request,
    steam_id: str = Form(...),
    role: str = Form("all"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Reset (zero out) a player's rating. role='all' resets all roles."""
    if not check_permission(current_admin, "ratings", "edit"):
        raise HTTPException(status_code=403)

    from rating_constants import ROLES
    from rating_engine import compute_rating, insert_history

    steam_id = steam_id.strip()
    roles_to_reset = ROLES if role == "all" else [role]

    count = 0
    for r in roles_to_reset:
        record = db.query(PlayerRating).filter(
            PlayerRating.steam_id == steam_id, PlayerRating.role == r
        ).first()
        if record and record.cumulative_points > 0:
            old_points = record.cumulative_points
            old_rating = record.current_rating
            record.cumulative_points = 0.0
            record.current_rating = 0.0
            insert_history(db, steam_id, r, "admin_reset", -old_points,
                           old_points, 0.0, old_rating, 0.0,
                           None, f"reset_by_{current_admin.discord_username}")
            count += 1

    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "rating_reset", {"steam_id": steam_id, "role": role, "count": count})
    push_flash(request, "success", f"Рейтинг обнулён ({count} ролей) для {steam_id}")
    return RedirectResponse(url=f"/adminold/ratings?role={role}&search={steam_id}", status_code=302)


@router.post("/adminold/ratings/adjust")
async def admin_rating_adjust(
    request: Request,
    steam_id: str = Form(...),
    role: str = Form(...),
    points_delta: float = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Manually adjust a player's cumulative points (+/-)."""
    if not check_permission(current_admin, "ratings", "edit"):
        raise HTTPException(status_code=403)

    from rating_constants import ROLES
    from rating_engine import compute_rating, get_or_create_rating, insert_history

    steam_id = steam_id.strip()
    if role not in ROLES:
        push_flash(request, "error", "Некорректная роль")
        return RedirectResponse(url="/adminold/ratings", status_code=302)

    record = get_or_create_rating(db, steam_id, role)
    old_points = record.cumulative_points
    old_rating = record.current_rating

    record.cumulative_points = max(0.0, record.cumulative_points + points_delta)
    record.current_rating = compute_rating(record.cumulative_points)

    insert_history(db, steam_id, role, "admin_adjust", points_delta,
                   old_points, record.cumulative_points,
                   old_rating, record.current_rating,
                   None, f"adjust_by_{current_admin.discord_username}: {reason}")

    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "rating_adjust", {
        "steam_id": steam_id, "role": role, "delta": points_delta, "reason": reason
    })
    direction = "+" if points_delta > 0 else ""
    push_flash(request, "success", f"Рейтинг {role}: {direction}{points_delta} очков для {steam_id}")
    return RedirectResponse(url=f"/adminold/ratings?role={role}&search={steam_id}", status_code=302)


@router.post("/adminold/ratings/recalculate")
async def admin_rating_recalculate(
    request: Request,
    steam_id: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Recalculate rating from current cumulative_points (useful after param changes)."""
    if not check_permission(current_admin, "ratings", "recalculate"):
        raise HTTPException(status_code=403)

    from rating_constants import ROLES
    from rating_engine import compute_rating

    steam_id = steam_id.strip()
    count = 0
    for r in ROLES:
        record = db.query(PlayerRating).filter(
            PlayerRating.steam_id == steam_id, PlayerRating.role == r
        ).first()
        if record:
            record.current_rating = compute_rating(record.cumulative_points)
            count += 1

    db.commit()
    push_flash(request, "success", f"Рейтинг пересчитан для {steam_id} ({count} ролей)")
    return RedirectResponse(url=f"/adminold/ratings?search={steam_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# RATINGS JSON API (for the React SPA)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/ratings")
async def api_ratings_page(
    role: str = Query("captain"),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    from rating_constants import ROLES, K, S, DECAY_THRESHOLD_DAYS, DECAY_RATE
    if role not in ROLES:
        role = "captain"

    search_result = None
    if search and search.strip():
        q = search.strip()
        player = db.query(PlayerData).filter(PlayerData.player_steamid == q).first()
        if not player:
            player = db.query(PlayerData).filter(PlayerData.player_last_time_name.ilike(f"%{q}%")).first()
        if player:
            records = db.query(PlayerRating).filter(PlayerRating.steam_id == player.player_steamid).all()
            search_result = {
                "player_id": player.player_id,
                "player_name": player.forced_display_name or player.player_last_time_name,
                "player_steamid": player.player_steamid,
                "ratings": {
                    r.role: {
                        "rating": round(r.current_rating, 2),
                        "points": round(r.cumulative_points, 1),
                        "last_activity": r.last_activity_date.isoformat() if r.last_activity_date else None,
                    }
                    for r in records
                },
            }

    top_records = (
        db.query(PlayerRating)
        .filter(PlayerRating.role == role, PlayerRating.current_rating > 0)
        .order_by(desc(PlayerRating.current_rating))
        .limit(100)
        .all()
    )
    steam_ids = [r.steam_id for r in top_records]
    players_map = {}
    if steam_ids:
        players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steam_ids)).all()
        players_map = {p.player_steamid: p for p in players}

    leaderboard = []
    for i, record in enumerate(top_records, 1):
        player = players_map.get(record.steam_id)
        leaderboard.append({
            "rank": i,
            "steam_id": record.steam_id,
            "player_name": (player.forced_display_name or player.player_last_time_name) if player else record.steam_id,
            "player_id": player.player_id if player else None,
            "rating": round(record.current_rating, 2),
            "cumulative_points": round(record.cumulative_points, 1),
            "last_activity": record.last_activity_date.isoformat() if record.last_activity_date else None,
        })

    return {
        "leaderboard": leaderboard,
        "selected_role": role,
        "roles": ROLES,
        "search_query": search,
        "search_result": search_result,
        "rating_params": {"K": K, "S": S, "decay_threshold_days": DECAY_THRESHOLD_DAYS, "decay_rate": DECAY_RATE},
        "snapshots": get_snapshots_list(),
        "can_edit": check_permission(current_admin, "ratings", "edit"),
        "is_superuser": bool(current_admin.is_superuser),
    }


@router.post("/api/v1/admin/ratings/snapshot/generate")
async def api_rating_snapshot_generate(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "ratings", "edit"):
        raise HTTPException(status_code=403)

    body = await request.json()
    title = body.get("title") or ""
    date_from = body.get("date_from")
    date_to = body.get("date_to")

    from rating_constants import ROLES, K, S
    from rating_engine import calculate_all_ratings

    now = datetime.utcnow()
    ratings_data = calculate_all_ratings(db)

    steam_ids = list(ratings_data.keys())
    players_map = {}
    if steam_ids:
        players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steam_ids)).all()
        players_map = {p.player_steamid: (p.forced_display_name or p.player_last_time_name) for p in players}

    leaderboards = {}
    for role in ROLES:
        entries = []
        for steam_id, roles_dict in ratings_data.items():
            if role in roles_dict:
                entries.append({
                    "steam_id": steam_id, "player_name": players_map.get(steam_id, steam_id),
                    "cumulative_points": roles_dict[role]["points"], "rating": roles_dict[role]["rating"],
                })
        entries.sort(key=lambda x: x.get("rating", 0), reverse=True)
        leaderboards[role] = entries[:100]

    snapshot_title = title.strip() or f"Рейтинг {now.strftime('%d.%m.%Y %H:%M')}"
    snapshot = {
        "meta": {
            "title": snapshot_title, "created_at": now.isoformat(), "created_by": current_admin.discord_username,
            "date_from": date_from or None, "date_to": date_to or None,
            "total_players": len(ratings_data), "params": {"K": K, "S": S},
        },
        "leaderboards": leaderboards,
    }
    safe_title = now.strftime("%Y%m%d_%H%M%S")
    filename = f"rating_snapshot_{safe_title}.json"
    filepath = os.path.join(SNAPSHOTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "rating_snapshot_generate", {
        "filename": filename, "title": snapshot_title, "players": len(ratings_data)
    })
    return {"status": "ok", "filename": filename, "title": snapshot_title, "players": len(ratings_data)}


@router.get("/api/v1/admin/ratings/snapshot/{filename}/view")
async def api_rating_snapshot_view(
    filename: str,
    role: str = Query("captain"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403)

    from rating_constants import ROLES
    safe_name = os.path.basename(filename)
    filepath = os.path.join(SNAPSHOTS_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Snapshot not found")

    with open(filepath, "r", encoding="utf-8") as f:
        snapshot = json.load(f)
    if role not in ROLES:
        role = "captain"

    return {
        "meta": snapshot.get("meta", {}),
        "filename": safe_name,
        "leaderboard": snapshot.get("leaderboards", {}).get(role, []),
        "selected_role": role,
        "roles": ROLES,
    }


@router.delete("/api/v1/admin/ratings/snapshot/{filename}")
async def api_rating_snapshot_delete(
    filename: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "ratings", "edit"):
        raise HTTPException(status_code=403)
    safe_name = os.path.basename(filename)
    filepath = os.path.join(SNAPSHOTS_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Файл не найден")
    os.remove(filepath)
    return {"status": "ok"}


@router.post("/api/v1/admin/ratings/snapshot/{filename}/restore")
async def api_rating_snapshot_restore(
    filename: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "ratings", "edit"):
        raise HTTPException(status_code=403)

    from rating_constants import ROLES
    safe_name = os.path.basename(filename)
    filepath = os.path.join(SNAPSHOTS_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Файл снапшота не найден")

    with open(filepath, "r", encoding="utf-8") as f:
        snapshot = json.load(f)
    leaderboards = snapshot.get("leaderboards", {})
    now = datetime.utcnow()

    db.query(PlayerRating).delete()
    restored = 0
    for role, entries in leaderboards.items():
        if role not in ROLES:
            continue
        for entry in entries:
            steam_id = entry.get("steam_id", "")
            if not steam_id:
                continue
            rating = entry.get("rating", 0.0)
            points = entry.get("cumulative_points", 0.0)
            if rating > 0:
                db.add(PlayerRating(
                    steam_id=steam_id, role=role, cumulative_points=float(points),
                    current_rating=float(rating), last_activity_date=now, last_decay_applied_at=now,
                ))
                restored += 1
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "rating_snapshot_restore", {"filename": safe_name, "restored_records": restored})
    return {"status": "ok", "restored": restored, "title": snapshot.get("meta", {}).get("title", safe_name)}


# ══════════════════════════════════════════════════════════════════════════════
# SUPERUSER-ONLY: formula config + manual rating adjustment (React SPA)
# ══════════════════════════════════════════════════════════════════════════════

def require_superuser(admin: AdminUser):
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Доступно только суперпользователям")


@router.get("/api/v1/admin/ratings/config")
async def api_rating_config_get(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Full rating-formula configuration (weights, penalties, decay/erosion
    params) — superuser-only, shown on the Рейтинг page's config panel."""
    require_superuser(current_admin)
    import rating_constants as rc
    return {
        "config": rc.get_current_config(),
        "defaults": rc.DEFAULT_CONFIG,
        "roles": rc.ROLES,
    }


@router.put("/api/v1/admin/ratings/config")
async def api_rating_config_save(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Persist and immediately apply an edited rating-formula config."""
    require_superuser(current_admin)
    import rating_constants as rc

    body = await request.json()
    try:
        effective = rc.save_overrides_to_db(db, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "rating_config_save", {"config": effective})
    return {"config": effective}


@router.post("/api/v1/admin/ratings/config/reset")
async def api_rating_config_reset(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Drop the saved override and restore the shipped defaults."""
    require_superuser(current_admin)
    import rating_constants as rc

    effective = rc.reset_overrides_to_defaults(db)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "rating_config_reset", {})
    return {"config": effective}


@router.post("/api/v1/admin/ratings/adjust")
async def api_rating_adjust(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Manually add to or subtract from a player's rating for one role (or
    every role at once). Superuser-only — see the Рейтинг page's config
    panel. Mirrors /adminold/ratings/adjust's logic but as a JSON endpoint
    and gated on is_superuser rather than the ratings:edit permission,
    since this bypasses the normal points-from-actions computation."""
    require_superuser(current_admin)
    from rating_constants import ROLES
    from rating_engine import compute_rating, get_or_create_rating, insert_history

    body = await request.json()
    steam_id = str(body.get("steam_id") or "").strip()
    role = body.get("role") or ""
    points_delta = body.get("points_delta")
    reason = str(body.get("reason") or "").strip()

    if not steam_id:
        raise HTTPException(status_code=400, detail="Не указан steam_id")
    try:
        points_delta = float(points_delta)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="points_delta должен быть числом")
    if points_delta == 0:
        raise HTTPException(status_code=400, detail="points_delta не может быть 0")

    roles_to_adjust = ROLES if role == "all" else [role]
    if any(r not in ROLES for r in roles_to_adjust):
        raise HTTPException(status_code=400, detail="Некорректная роль")

    results = []
    for r in roles_to_adjust:
        record = get_or_create_rating(db, steam_id, r)
        old_points = record.cumulative_points
        old_rating = record.current_rating

        record.cumulative_points = max(0.0, record.cumulative_points + points_delta)
        record.current_rating = compute_rating(record.cumulative_points)

        insert_history(db, steam_id, r, "admin_adjust", points_delta,
                        old_points, record.cumulative_points,
                        old_rating, record.current_rating,
                        None, f"adjust_by_{current_admin.discord_username}: {reason}")
        results.append({
            "role": r,
            "cumulative_points": record.cumulative_points,
            "current_rating": record.current_rating,
        })

    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "rating_adjust", {
        "steam_id": steam_id, "role": role, "delta": points_delta, "reason": reason,
    })
    return {"status": "ok", "steam_id": steam_id, "results": results}


@router.post("/api/v1/admin/ratings/recalculate-all")
async def api_rating_recalculate_all(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "ratings", "recalculate"):
        raise HTTPException(status_code=403)
    try:
        from task_scheduler import scheduler, Priority
        from workers import task_recalculate_ratings
        scheduler.submit(task_recalculate_ratings, priority=Priority.HIGH, task_id="rating_recalc_manual", deduplicate=False, timeout=300.0)
        return {"status": "queued"}
    except ImportError:
        from workers import task_recalculate_ratings
        result = task_recalculate_ratings()
        return {"status": "ok", "players_recalculated": result.get("players_recalculated")}


# ══════════════════════════════════════════════════════════════════════════════
# SNAPSHOTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/ratings/snapshot/generate")
async def admin_rating_snapshot_generate(
    request: Request,
    title: str = Form(""),
    date_from: Optional[str] = Form(None),
    date_to: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Generate a rating snapshot from raw player_role_actions data."""
    if not check_permission(current_admin, "ratings", "edit"):
        raise HTTPException(status_code=403)

    from rating_constants import ROLES, K, S
    from rating_engine import calculate_all_ratings

    now = datetime.utcnow()

    # Calculate ratings from raw data
    ratings_data = calculate_all_ratings(db)

    # Enrich with player names
    steam_ids = list(ratings_data.keys())
    players_map = {}
    if steam_ids:
        players = db.query(PlayerData).filter(PlayerData.player_steamid.in_(steam_ids)).all()
        players_map = {p.player_steamid: (p.forced_display_name or p.player_last_time_name) for p in players}

    # Build leaderboards per role
    leaderboards = {}
    for role in ROLES:
        entries = []
        for steam_id, roles_dict in ratings_data.items():
            if role in roles_dict:
                entries.append({
                    "steam_id": steam_id,
                    "player_name": players_map.get(steam_id, steam_id),
                    "cumulative_points": roles_dict[role]["points"],
                    "rating": roles_dict[role]["rating"],
                })
        entries.sort(key=lambda x: x.get("rating", 0), reverse=True)
        leaderboards[role] = entries[:100]

    # Build snapshot
    snapshot_title = title.strip() or f"Рейтинг {now.strftime('%d.%m.%Y %H:%M')}"
    snapshot = {
        "meta": {
            "title": snapshot_title,
            "created_at": now.isoformat(),
            "created_by": current_admin.discord_username,
            "date_from": date_from if date_from else None,
            "date_to": date_to if date_to else None,
            "total_players": len(ratings_data),
            "params": {"K": K, "S": S},
        },
        "leaderboards": leaderboards,
    }

    # Save to file
    safe_title = now.strftime("%Y%m%d_%H%M%S")
    filename = f"rating_snapshot_{safe_title}.json"
    filepath = os.path.join(SNAPSHOTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "rating_snapshot_generate", {
        "filename": filename, "title": snapshot_title, "players": len(ratings_data)
    })
    push_flash(request, "success", f"Снапшот «{snapshot_title}» сгенерирован ({len(ratings_data)} игроков)")
    return RedirectResponse(url="/adminold/ratings", status_code=302)


@router.get("/adminold/ratings/snapshot/{filename}")
async def admin_rating_snapshot_download(
    filename: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Download a rating snapshot JSON file."""
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403)

    # Sanitize filename
    safe_name = os.path.basename(filename)
    filepath = os.path.join(SNAPSHOTS_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return FileResponse(path=filepath, filename=safe_name, media_type="application/json")


@router.post("/adminold/ratings/snapshot/{filename}/delete")
async def admin_rating_snapshot_delete(
    request: Request,
    filename: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Delete a rating snapshot."""
    if not check_permission(current_admin, "ratings", "edit"):
        raise HTTPException(status_code=403)

    safe_name = os.path.basename(filename)
    filepath = os.path.join(SNAPSHOTS_DIR, safe_name)
    if os.path.exists(filepath):
        os.remove(filepath)
        push_flash(request, "success", f"Снапшот {safe_name} удалён")
    else:
        push_flash(request, "error", "Файл не найден")

    return RedirectResponse(url="/adminold/ratings", status_code=302)


@router.get("/adminold/ratings/snapshot/{filename}/view", response_class=HTMLResponse)
async def admin_rating_snapshot_view(
    request: Request,
    filename: str,
    role: str = Query("captain"),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """View a rating snapshot in the browser."""
    if not check_permission(current_admin, "ratings", "view"):
        raise HTTPException(status_code=403)

    from rating_constants import ROLES

    safe_name = os.path.basename(filename)
    filepath = os.path.join(SNAPSHOTS_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Snapshot not found")

    with open(filepath, "r", encoding="utf-8") as f:
        snapshot = json.load(f)

    meta = snapshot.get("meta", {})
    leaderboards = snapshot.get("leaderboards", {})

    if role not in ROLES:
        role = "captain"

    current_leaderboard = leaderboards.get(role, [])

    return templates.TemplateResponse("admin/rating_snapshot_view.html", {
        "request": request,
        "admin": current_admin,
        "meta": meta,
        "filename": safe_name,
        "leaderboard": current_leaderboard,
        "selected_role": role,
        "roles": ROLES,
        "all_leaderboards": leaderboards,
    })


@router.post("/adminold/ratings/snapshot/{filename}/restore")
async def admin_rating_snapshot_restore(
    request: Request,
    filename: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Restore player_ratings table from a snapshot file (full rollback)."""
    if not check_permission(current_admin, "ratings", "edit"):
        raise HTTPException(status_code=403)

    from rating_constants import ROLES
    from datetime import datetime

    safe_name = os.path.basename(filename)
    filepath = os.path.join(SNAPSHOTS_DIR, safe_name)
    if not os.path.exists(filepath):
        push_flash(request, "error", "Файл снапшота не найден")
        return RedirectResponse(url="/adminold/ratings", status_code=302)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            snapshot = json.load(f)

        leaderboards = snapshot.get("leaderboards", {})
        now = datetime.utcnow()

        # Clear all current ratings
        db.query(PlayerRating).delete()

        # Restore from snapshot
        restored = 0
        for role, entries in leaderboards.items():
            if role not in ROLES:
                continue
            for entry in entries:
                steam_id = entry.get("steam_id", "")
                if not steam_id:
                    continue
                rating = entry.get("rating", 0.0)
                points = entry.get("cumulative_points", 0.0)
                if rating > 0:
                    db.add(PlayerRating(
                        steam_id=steam_id,
                        role=role,
                        cumulative_points=float(points),
                        current_rating=float(rating),
                        last_activity_date=now,
                        last_decay_applied_at=now,
                    ))
                    restored += 1

        db.commit()

        from routes.admin import log_admin_action
        log_admin_action(db, current_admin.id, "rating_snapshot_restore", {
            "filename": safe_name,
            "restored_records": restored,
        })
        push_flash(request, "success",
                   f"Рейтинг восстановлен из «{snapshot.get('meta', {}).get('title', safe_name)}» ({restored} записей)")
    except Exception as e:
        db.rollback()
        logger.exception("Snapshot restore failed: %s", e)
        push_flash(request, "error", f"Ошибка восстановления: {e}")

    return RedirectResponse(url="/adminold/ratings", status_code=302)


@router.post("/adminold/ratings/recalculate-all")
async def admin_rating_recalculate_all(
    request: Request,
    save_snapshot: str = Form("1"),
    snapshot_title: str = Form(""),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Submit full rating recalculation as a background task (HIGH priority)."""
    if not check_permission(current_admin, "ratings", "recalculate"):
        raise HTTPException(status_code=403)

    try:
        from task_scheduler import scheduler, Priority
        from workers import task_recalculate_ratings
        task_id = scheduler.submit(
            task_recalculate_ratings,
            priority=Priority.HIGH,
            task_id="rating_recalc_manual",
            deduplicate=False,
            timeout=300.0,
        )
        push_flash(request, "info", "Пересчёт рейтинга запущен в фоновом режиме (HIGH приоритет). Результат появится через 10-60 секунд.")
    except ImportError:
        # Fallback: run synchronously if scheduler not available
        from workers import task_recalculate_ratings
        try:
            result = task_recalculate_ratings()
            push_flash(request, "success", f"Рейтинг пересчитан для {result.get('players_recalculated', '?')} игроков.")
        except Exception as e:
            push_flash(request, "error", f"Ошибка пересчёта: {e}")
    return RedirectResponse(url="/adminold/ratings?tab=snapshots", status_code=302)
