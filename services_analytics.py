"""
Analytics computation — pure functions shared by the periodic background
refresh (see workers.task_refresh_analytics) and the live API fallback in
routes/analytics.py.

The heavy queries used to run on every dashboard page view. Now a background
task recomputes the default-view snapshot every few minutes and the API
routes serve that cached snapshot instantly; only non-default parameter
combinations (e.g. a custom period picked interactively) still hit the DB.
"""

from datetime import datetime, timedelta
from sqlalchemy import func, text, desc
from sqlalchemy.orm import Session

from models import OnlineSnapshot, PlayerSession, PlayerRoleStat, RoundEvent, PlayerData, PlayerStats


def compute_online_timeline(db: Session, period: str = "6h", server: str = None) -> dict:
    now = datetime.utcnow()
    delta = {"6h": timedelta(hours=6), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}.get(period, timedelta(hours=24))
    since = now - delta

    q = db.query(OnlineSnapshot).filter(OnlineSnapshot.recorded_at >= since)
    if server:
        q = q.filter(OnlineSnapshot.server_name == server.strip().upper())
    snapshots = q.order_by(OnlineSnapshot.recorded_at).all()

    slot_mins = {"6h": 5, "24h": 15, "7d": 60, "30d": 240}.get(period, 15)
    slots = {}
    for s in snapshots:
        ts = s.recorded_at
        slot_key = ts.replace(minute=(ts.minute // slot_mins) * slot_mins, second=0, microsecond=0)
        slots.setdefault(slot_key, []).append(s.player_count)

    labels, values_avg, values_peak = [], [], []
    for ts in sorted(slots.keys()):
        counts = slots[ts]
        if period in ("6h", "24h"):
            labels.append(ts.strftime("%H:%M"))
        elif period == "7d":
            labels.append(ts.strftime("%d.%m %H:%M"))
        else:
            labels.append(ts.strftime("%d.%m"))
        values_avg.append(round(sum(counts) / len(counts), 1))
        values_peak.append(max(counts))

    return {"labels": labels, "avg": values_avg, "peak": values_peak, "total_snapshots": len(snapshots)}


def compute_role_popularity(db: Session) -> dict:
    rows = db.query(PlayerRoleStat.role, func.sum(PlayerRoleStat.rounds_played).label("total")).group_by(PlayerRoleStat.role).order_by(desc("total")).all()
    return {"roles": [r[0] for r in rows], "counts": [int(r[1] or 0) for r in rows]}


def compute_submarine_popularity(db: Session, period: str = "30d") -> dict:
    delta = {"7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90), "all": timedelta(days=36500)}.get(period, timedelta(days=30))
    since = datetime.utcnow() - delta
    rows = (
        db.query(RoundEvent.submarine_name, func.count().label("cnt"))
        .filter(RoundEvent.event_type == "start", RoundEvent.created_at >= since, RoundEvent.submarine_name.isnot(None))
        .group_by(RoundEvent.submarine_name).order_by(desc("cnt")).limit(20).all()
    )
    return {"submarines": [r[0] for r in rows], "counts": [int(r[1]) for r in rows]}


def compute_peak_hours(db: Session, server: str = None) -> dict:
    since = datetime.utcnow() - timedelta(days=14)
    q = db.query(OnlineSnapshot).filter(OnlineSnapshot.recorded_at >= since)
    if server:
        q = q.filter(OnlineSnapshot.server_name == server.strip().upper())
    snapshots = q.all()

    hourly = {}
    for s in snapshots:
        h = s.recorded_at.hour
        hourly.setdefault(h, []).append(s.player_count)

    labels = [f"{h:02d}:00" for h in range(24)]
    avg_v = [round(sum(hourly.get(h, [0])) / max(len(hourly.get(h, [0])), 1), 1) for h in range(24)]
    peak_v = [max(hourly.get(h, [0])) for h in range(24)]
    return {"labels": labels, "avg": avg_v, "peak": peak_v}


def compute_session_duration(db: Session, period: str = "30d") -> dict:
    delta = {"7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}.get(period, timedelta(days=30))
    since = datetime.utcnow() - delta
    sessions = db.query(PlayerSession.session_duration).filter(PlayerSession.login_time >= since, PlayerSession.session_duration.isnot(None)).all()

    buckets = [0] * 8
    bucket_labels = ["<5 мин", "5-15 мин", "15-30 мин", "30м-1ч", "1-2ч", "2-4ч", "4-8ч", "8ч+"]
    for (dur,) in sessions:
        if dur is None:
            continue
        m = dur / 60
        idx = 0 if m < 5 else 1 if m < 15 else 2 if m < 30 else 3 if m < 60 else 4 if m < 120 else 5 if m < 240 else 6 if m < 480 else 7
        buckets[idx] += 1
    return {"labels": bucket_labels, "counts": buckets, "total_sessions": len(sessions)}


def compute_server_comparison(db: Session) -> dict:
    since = datetime.utcnow() - timedelta(days=7)
    result = {}
    for sn in ["ASPER1", "ASPER2"]:
        row = db.query(func.avg(OnlineSnapshot.player_count), func.max(OnlineSnapshot.player_count)).filter(OnlineSnapshot.server_name == sn, OnlineSnapshot.recorded_at >= since).first()
        rounds = db.query(func.count()).filter(RoundEvent.event_type == "start", RoundEvent.server_name == sn, RoundEvent.created_at >= since).scalar() or 0
        result[sn] = {"avg_online": round(float(row[0] or 0), 1), "peak_online": int(row[1] or 0), "rounds_week": int(rounds)}
    return result


def compute_player_retention(db: Session) -> dict:
    now = datetime.utcnow()
    month_ago = now - timedelta(days=30)
    week_ago = now - timedelta(days=7)

    total_new = db.query(func.count(func.distinct(PlayerSession.player_id))).filter(PlayerSession.login_time >= month_ago).scalar() or 0
    returning = db.execute(text("SELECT COUNT(*) FROM (SELECT player_id FROM player_sessions WHERE login_time >= :since GROUP BY player_id HAVING COUNT(*) > 1) t"), {"since": month_ago}).scalar() or 0
    active_week = db.execute(text("SELECT COUNT(DISTINCT player_id) FROM player_sessions WHERE login_time >= :week AND player_id IN (SELECT DISTINCT player_id FROM player_sessions WHERE login_time >= :month)"), {"week": week_ago, "month": month_ago}).scalar() or 0

    return {"total_new_30d": total_new, "returning_30d": returning, "retention_pct": round(returning / max(total_new, 1) * 100, 1), "active_this_week": active_week}


def compute_top_players(db: Session, metric: str = "playtime", period: str = "7d", limit: int = 15) -> dict:
    delta = {"7d": timedelta(days=7), "30d": timedelta(days=30), "all": timedelta(days=36500)}.get(period, timedelta(days=7))
    since = datetime.utcnow() - delta

    if metric == "playtime":
        rows = db.execute(text("SELECT ps.player_id, pd.player_last_time_name, SUM(ps.session_duration) as total FROM player_sessions ps LEFT JOIN players_data pd ON pd.player_id = ps.player_id WHERE ps.login_time >= :since AND ps.session_duration IS NOT NULL GROUP BY ps.player_id ORDER BY total DESC LIMIT :lim"), {"since": since, "lim": limit}).fetchall()
        return {"players": [{"name": r[1] or f"#{r[0]}", "value": int(r[2] or 0) // 3600, "unit": "ч"} for r in rows]}
    elif metric == "sessions":
        rows = db.execute(text("SELECT ps.player_id, pd.player_last_time_name, COUNT(*) as cnt FROM player_sessions ps LEFT JOIN players_data pd ON pd.player_id = ps.player_id WHERE ps.login_time >= :since GROUP BY ps.player_id ORDER BY cnt DESC LIMIT :lim"), {"since": since, "lim": limit}).fetchall()
        return {"players": [{"name": r[1] or f"#{r[0]}", "value": int(r[2]), "unit": ""} for r in rows]}
    else:
        rows = db.query(PlayerData.player_last_time_name, PlayerStats.killed_monsters).join(PlayerStats, PlayerStats.player_id == PlayerData.player_id).order_by(desc(PlayerStats.killed_monsters)).limit(limit).all()
        return {"players": [{"name": r[0] or "?", "value": int(r[1] or 0), "unit": ""} for r in rows]}


# ── Default-view snapshot (what the dashboard requests on initial page load) ──

DEFAULT_ONLINE_PERIOD = "6h"
DEFAULT_SESSION_PERIOD = "7d"
DEFAULT_SUB_PERIOD = "30d"
DEFAULT_TOP_METRIC = "playtime"
DEFAULT_TOP_PERIOD = "7d"


def compute_default_snapshot(db: Session) -> dict:
    """Compute the exact set of results the dashboard fetches on first load
    (no server filter, default periods). Cheap enough to run periodically in
    the background so page views never trigger a live recompute."""
    return {
        "online_timeline": compute_online_timeline(db, period=DEFAULT_ONLINE_PERIOD, server=None),
        "peak_hours": compute_peak_hours(db, server=None),
        "session_duration": compute_session_duration(db, period=DEFAULT_SESSION_PERIOD),
        "role_popularity": compute_role_popularity(db),
        "submarine_popularity": compute_submarine_popularity(db, period=DEFAULT_SUB_PERIOD),
        "player_retention": compute_player_retention(db),
        "top_players": compute_top_players(db, metric=DEFAULT_TOP_METRIC, period=DEFAULT_TOP_PERIOD),
        "server_comparison": compute_server_comparison(db),
        "generated_at": datetime.utcnow().isoformat(),
    }
