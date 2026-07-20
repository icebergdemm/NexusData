"""
Analytics computation — pure functions shared by the periodic background
refresh (see workers.task_refresh_analytics) and the live API fallback in
routes/analytics.py.

Every function below (except compute_role_popularity, whose source table
carries no timestamp at all — see its docstring) takes an explicit
`since`/`until` datetime range instead of a fixed lookback window, so the
whole dashboard can be driven by one shared range picker (quick presets:
24h/week/month/year, or a custom start+end) — routes/analytics.py resolves
that range once and passes it to every one of these.

The heavy queries used to run on every dashboard page view. Now a background
task recomputes the DEFAULT-range snapshot every few minutes and the API
routes serve that cached snapshot instantly; only a non-default range (the
user picked something other than the default preset) still hits the DB live.
"""

from datetime import datetime, timedelta
from sqlalchemy import func, text, desc
from sqlalchemy.orm import Session

from models import OnlineSnapshot, PlayerSession, PlayerRoleStat, RoundEvent, PlayerData, PlayerStats


def _pick_bucket_minutes(since: datetime, until: datetime) -> int:
    """Pick a chart bucket size that keeps the point count reasonable
    regardless of how wide a range the user picked."""
    span_hours = max((until - since).total_seconds() / 3600, 1)
    if span_hours <= 30:
        return 15
    if span_hours <= 24 * 10:
        return 60
    if span_hours <= 24 * 45:
        return 240
    return 24 * 60


def _label_fmt(bucket_minutes: int) -> str:
    if bucket_minutes < 60:
        return "%H:%M"
    if bucket_minutes < 24 * 60:
        return "%d.%m %H:%M"
    return "%d.%m"


def _floor_to_bucket(ts: datetime, bucket_minutes: int) -> datetime:
    epoch = datetime(1970, 1, 1)
    total_minutes = int((ts - epoch).total_seconds() // 60)
    floored = (total_minutes // bucket_minutes) * bucket_minutes
    return epoch + timedelta(minutes=floored)


def compute_online_timeline(db: Session, since: datetime, until: datetime, server: str = None) -> dict:
    q = db.query(OnlineSnapshot).filter(OnlineSnapshot.recorded_at >= since, OnlineSnapshot.recorded_at <= until)
    if server:
        q = q.filter(OnlineSnapshot.server_name == server.strip().upper())
    snapshots = q.order_by(OnlineSnapshot.recorded_at).all()

    bucket_minutes = _pick_bucket_minutes(since, until)
    label_fmt = _label_fmt(bucket_minutes)
    slots = {}
    for s in snapshots:
        slot_key = _floor_to_bucket(s.recorded_at, bucket_minutes)
        slots.setdefault(slot_key, []).append(s.player_count)

    labels, values_avg, values_peak = [], [], []
    for ts in sorted(slots.keys()):
        counts = slots[ts]
        labels.append(ts.strftime(label_fmt))
        values_avg.append(round(sum(counts) / len(counts), 1))
        values_peak.append(max(counts))

    return {"labels": labels, "avg": values_avg, "peak": values_peak, "total_snapshots": len(snapshots)}


def compute_role_popularity(db: Session) -> dict:
    """PlayerRoleStat is a running lifetime counter with no per-round
    timestamp at all, so this is always all-time — not affected by the
    dashboard's date-range picker."""
    rows = db.query(PlayerRoleStat.role, func.sum(PlayerRoleStat.rounds_played).label("total")).group_by(PlayerRoleStat.role).order_by(desc("total")).all()
    return {"roles": [r[0] for r in rows], "counts": [int(r[1] or 0) for r in rows]}


def compute_submarine_popularity(db: Session, since: datetime, until: datetime) -> dict:
    rows = (
        db.query(RoundEvent.submarine_name, func.count().label("cnt"))
        .filter(RoundEvent.event_type == "start", RoundEvent.created_at >= since, RoundEvent.created_at <= until, RoundEvent.submarine_name.isnot(None))
        .group_by(RoundEvent.submarine_name).order_by(desc("cnt")).limit(20).all()
    )
    return {"submarines": [r[0] for r in rows], "counts": [int(r[1]) for r in rows]}


def compute_peak_hours(db: Session, since: datetime, until: datetime, server: str = None) -> dict:
    q = db.query(OnlineSnapshot).filter(OnlineSnapshot.recorded_at >= since, OnlineSnapshot.recorded_at <= until)
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


def compute_session_duration(db: Session, since: datetime, until: datetime) -> dict:
    sessions = db.query(PlayerSession.session_duration).filter(
        PlayerSession.login_time >= since, PlayerSession.login_time <= until, PlayerSession.session_duration.isnot(None)
    ).all()

    buckets = [0] * 8
    bucket_labels = ["<5 мин", "5-15 мин", "15-30 мин", "30м-1ч", "1-2ч", "2-4ч", "4-8ч", "8ч+"]
    for (dur,) in sessions:
        if dur is None:
            continue
        m = dur / 60
        idx = 0 if m < 5 else 1 if m < 15 else 2 if m < 30 else 3 if m < 60 else 4 if m < 120 else 5 if m < 240 else 6 if m < 480 else 7
        buckets[idx] += 1
    return {"labels": bucket_labels, "counts": buckets, "total_sessions": len(sessions)}


def compute_server_comparison(db: Session, since: datetime, until: datetime) -> dict:
    result = {}
    for sn in ["ASPER1", "ASPER2"]:
        row = db.query(func.avg(OnlineSnapshot.player_count), func.max(OnlineSnapshot.player_count)).filter(
            OnlineSnapshot.server_name == sn, OnlineSnapshot.recorded_at >= since, OnlineSnapshot.recorded_at <= until
        ).first()
        rounds = db.query(func.count()).filter(
            RoundEvent.event_type == "start", RoundEvent.server_name == sn, RoundEvent.created_at >= since, RoundEvent.created_at <= until
        ).scalar() or 0
        result[sn] = {"avg_online": round(float(row[0] or 0), 1), "peak_online": int(row[1] or 0), "rounds_week": int(rounds)}
    return result


def compute_player_retention(db: Session, since: datetime, until: datetime) -> dict:
    """"New"/"returning" cohorts are measured over the trailing 30/7 days
    counted BACKWARD FROM `until` (not always "now") — so this stays
    consistent with whatever end-of-range the picker is currently showing,
    without redefining what "new"/"returning" mean for an arbitrary range."""
    month_ago = until - timedelta(days=30)
    week_ago = until - timedelta(days=7)

    total_new = db.query(func.count(func.distinct(PlayerSession.player_id))).filter(PlayerSession.login_time >= month_ago, PlayerSession.login_time <= until).scalar() or 0
    returning = db.execute(text("SELECT COUNT(*) FROM (SELECT player_id FROM player_sessions WHERE login_time >= :since AND login_time <= :until GROUP BY player_id HAVING COUNT(*) > 1) t"), {"since": month_ago, "until": until}).scalar() or 0
    active_week = db.execute(text("SELECT COUNT(DISTINCT player_id) FROM player_sessions WHERE login_time >= :week AND login_time <= :until AND player_id IN (SELECT DISTINCT player_id FROM player_sessions WHERE login_time >= :month AND login_time <= :until)"), {"week": week_ago, "until": until, "month": month_ago}).scalar() or 0

    return {"total_new_30d": total_new, "returning_30d": returning, "retention_pct": round(returning / max(total_new, 1) * 100, 1), "active_this_week": active_week}


def compute_top_players(db: Session, since: datetime, until: datetime, metric: str = "playtime", limit: int = 15) -> dict:
    if metric == "playtime":
        rows = db.execute(text("SELECT ps.player_id, pd.player_last_time_name, SUM(ps.session_duration) as total FROM player_sessions ps LEFT JOIN players_data pd ON pd.player_id = ps.player_id WHERE ps.login_time >= :since AND ps.login_time <= :until AND ps.session_duration IS NOT NULL GROUP BY ps.player_id ORDER BY total DESC LIMIT :lim"), {"since": since, "until": until, "lim": limit}).fetchall()
        return {"players": [{"name": r[1] or f"#{r[0]}", "value": int(r[2] or 0) // 3600, "unit": "ч"} for r in rows]}
    elif metric == "sessions":
        rows = db.execute(text("SELECT ps.player_id, pd.player_last_time_name, COUNT(*) as cnt FROM player_sessions ps LEFT JOIN players_data pd ON pd.player_id = ps.player_id WHERE ps.login_time >= :since AND ps.login_time <= :until GROUP BY ps.player_id ORDER BY cnt DESC LIMIT :lim"), {"since": since, "until": until, "lim": limit}).fetchall()
        return {"players": [{"name": r[1] or f"#{r[0]}", "value": int(r[2]), "unit": ""} for r in rows]}
    else:
        rows = db.query(PlayerData.player_last_time_name, PlayerStats.killed_monsters).join(PlayerStats, PlayerStats.player_id == PlayerData.player_id).order_by(desc(PlayerStats.killed_monsters)).limit(limit).all()
        return {"players": [{"name": r[0] or "?", "value": int(r[1] or 0), "unit": ""} for r in rows]}


# ── Default-view snapshot (what the dashboard requests on initial page load) ──

DEFAULT_PRESET = "week"


def default_range(now: datetime = None) -> tuple:
    now = now or datetime.utcnow()
    return now - timedelta(days=7), now


def compute_default_snapshot(db: Session) -> dict:
    """Compute the exact set of results the dashboard fetches on first load
    (no server filter, default "week" range). Cheap enough to run
    periodically in the background so page views never trigger a live
    recompute."""
    since, until = default_range()
    return {
        "online_timeline": compute_online_timeline(db, since=since, until=until, server=None),
        "peak_hours": compute_peak_hours(db, since=since, until=until, server=None),
        "session_duration": compute_session_duration(db, since=since, until=until),
        "role_popularity": compute_role_popularity(db),
        "submarine_popularity": compute_submarine_popularity(db, since=since, until=until),
        "player_retention": compute_player_retention(db, since=since, until=until),
        "top_players": compute_top_players(db, since=since, until=until, metric="playtime"),
        "server_comparison": compute_server_comparison(db, since=since, until=until),
        "generated_at": datetime.utcnow().isoformat(),
    }
