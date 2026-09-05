"""
Sauron's Eye — player behavior analysis (performance-safe version).

All queries use hard LIMITs and avoid in-Python O(n²) loops over full tables.
Session analysis removed (was O(n²) cross-join in Python).
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import bindparam, text

from models import PlayerData
from utils import is_bogon_ip
from services_ip_filter import build_analysis_ip_policy

logger = logging.getLogger(__name__)

RISK_LOW    = "low"
RISK_MEDIUM = "medium"
RISK_HIGH   = "high"

# Hard caps — keep memory bounded
_MAX_TWINK_PAIRS = 200
_MAX_DEVIANT     = 100

# Keep the cheap SQL self-joins from filling their hard caps with container,
# LAN, loopback, CGNAT, multicast, and documentation addresses.  The Python
# check below remains the final guard for IPv6 and less common reserved ranges.
_NON_GLOBAL_IPV4_SQL = """NOT (
    {alias}.ip LIKE '0.%' OR {alias}.ip LIKE '10.%' OR {alias}.ip LIKE '127.%'
    OR {alias}.ip LIKE '169.254.%' OR {alias}.ip LIKE '192.0.2.%'
    OR {alias}.ip LIKE '192.168.%' OR {alias}.ip LIKE '198.18.%'
    OR {alias}.ip LIKE '198.19.%' OR {alias}.ip LIKE '198.51.100.%'
    OR {alias}.ip LIKE '203.0.113.%'
    OR {alias}.ip LIKE '172.16.%' OR {alias}.ip LIKE '172.17.%'
    OR {alias}.ip LIKE '172.18.%' OR {alias}.ip LIKE '172.19.%'
    OR {alias}.ip LIKE '172.20.%' OR {alias}.ip LIKE '172.21.%'
    OR {alias}.ip LIKE '172.22.%' OR {alias}.ip LIKE '172.23.%'
    OR {alias}.ip LIKE '172.24.%' OR {alias}.ip LIKE '172.25.%'
    OR {alias}.ip LIKE '172.26.%' OR {alias}.ip LIKE '172.27.%'
    OR {alias}.ip LIKE '172.28.%' OR {alias}.ip LIKE '172.29.%'
    OR {alias}.ip LIKE '172.30.%' OR {alias}.ip LIKE '172.31.%'
    OR {alias}.ip LIKE '100.64.%' OR {alias}.ip LIKE '100.65.%'
    OR {alias}.ip LIKE '100.66.%' OR {alias}.ip LIKE '100.67.%'
    OR {alias}.ip LIKE '100.68.%' OR {alias}.ip LIKE '100.69.%'
    OR {alias}.ip LIKE '100.70.%' OR {alias}.ip LIKE '100.71.%'
    OR {alias}.ip LIKE '100.72.%' OR {alias}.ip LIKE '100.73.%'
    OR {alias}.ip LIKE '100.74.%' OR {alias}.ip LIKE '100.75.%'
    OR {alias}.ip LIKE '100.76.%' OR {alias}.ip LIKE '100.77.%'
    OR {alias}.ip LIKE '100.78.%' OR {alias}.ip LIKE '100.79.%'
    OR {alias}.ip LIKE '100.8%.%' OR {alias}.ip LIKE '100.9%.%'
    OR {alias}.ip LIKE '100.10%.%' OR {alias}.ip LIKE '100.11%.%'
    OR {alias}.ip LIKE '100.12%.%' OR {alias}.ip LIKE '100.127.%'
    OR {alias}.ip LIKE '224.%' OR {alias}.ip LIKE '225.%'
    OR {alias}.ip LIKE '226.%' OR {alias}.ip LIKE '227.%'
    OR {alias}.ip LIKE '228.%' OR {alias}.ip LIKE '229.%'
    OR {alias}.ip LIKE '230.%' OR {alias}.ip LIKE '231.%'
    OR {alias}.ip LIKE '232.%' OR {alias}.ip LIKE '233.%'
    OR {alias}.ip LIKE '234.%' OR {alias}.ip LIKE '235.%'
    OR {alias}.ip LIKE '236.%' OR {alias}.ip LIKE '237.%'
    OR {alias}.ip LIKE '238.%' OR {alias}.ip LIKE '239.%'
)"""


# ═══════════════════════════════════════════════════════════════════════
# TWINK DETECTION — pure SQL, no Python cross-joins
# ═══════════════════════════════════════════════════════════════════════

def detect_all_twink_pairs(db: Session, days: int = 90) -> List[dict]:
    """
    Find player pairs sharing IPs or subnets using SQL self-joins only.
    Returns at most _MAX_TWINK_PAIRS results.
    """
    policy = build_analysis_ip_policy(db)
    ignored_ips = policy.exact_ignored_ips

    # Signal 1: shared exact IP (weight 3)
    ip_pairs = db.execute(text(f"""
        SELECT a.player_id AS pid_a, b.player_id AS pid_b,
               3 AS weight, CONCAT('IP: ', a.ip) AS reason, a.ip AS shared_ip
        FROM player_ips a
        JOIN player_ips b ON a.ip = b.ip AND a.player_id < b.player_id
        WHERE {_NON_GLOBAL_IPV4_SQL.format(alias='a')}
          AND a.ip NOT IN :ignored_ips
        LIMIT 2000
    """).bindparams(bindparam("ignored_ips", expanding=True)), {"ignored_ips": ignored_ips}).fetchall()

    # Signal 2: shared subnet but different IPs (weight 2)
    subnet_pairs = db.execute(text(f"""
        SELECT a.player_id AS pid_a, b.player_id AS pid_b,
               2 AS weight, CONCAT('Подсеть: ', a.subnet) AS reason,
               a.ip AS ip_a, b.ip AS ip_b
        FROM player_ips a
        JOIN player_ips b ON a.subnet = b.subnet
                          AND a.player_id < b.player_id
                          AND a.ip != b.ip
        WHERE {_NON_GLOBAL_IPV4_SQL.format(alias='a')}
          AND {_NON_GLOBAL_IPV4_SQL.format(alias='b')}
          AND a.ip NOT IN :ignored_ips
          AND b.ip NOT IN :ignored_ips
        LIMIT 2000
    """).bindparams(bindparam("ignored_ips", expanding=True)), {"ignored_ips": ignored_ips}).fetchall()

    # Merge into edges dict
    edges: Dict[Tuple[int, int], dict] = {}

    def _add(pid_a, pid_b, weight, reason, sig_type):
        key = (min(pid_a, pid_b), max(pid_a, pid_b))
        if key not in edges:
            edges[key] = {"score": 0, "signals": [], "pid_a": key[0], "pid_b": key[1]}
        # Deduplicate by type+reason
        if not any(s["label"] == reason for s in edges[key]["signals"]):
            edges[key]["score"] += weight
            edges[key]["signals"].append({"type": sig_type, "label": reason, "weight": weight})

    for r in ip_pairs:
        if not is_bogon_ip(r.shared_ip) and not policy.is_ignored(r.shared_ip):
            _add(r.pid_a, r.pid_b, r.weight, r.reason, "shared_ip")
    for r in subnet_pairs:
        if not is_bogon_ip(r.ip_a) and not is_bogon_ip(r.ip_b) and not policy.is_ignored(r.ip_a) and not policy.is_ignored(r.ip_b):
            _add(r.pid_a, r.pid_b, r.weight, r.reason, "shared_subnet")

    # Signal 3: similar name prefix via SQL (weight 1)
    name_pairs = db.execute(text("""
        SELECT a.player_id AS pid_a, b.player_id AS pid_b,
               1 AS weight,
               CONCAT('Похожее имя: ', LEFT(a.nickname, 6), '...') AS reason
        FROM player_names a
        JOIN player_names b ON LEFT(a.nickname, 6) = LEFT(b.nickname, 6)
                            AND a.player_id < b.player_id
                            AND LENGTH(a.nickname) >= 6
        LIMIT 500
    """)).fetchall()

    for r in name_pairs:
        _add(r.pid_a, r.pid_b, r.weight, r.reason, "similar_name")

    if not edges:
        return []

    # Filter: minimum score 2
    strong = [(k, v) for k, v in edges.items() if v["score"] >= 2]
    strong.sort(key=lambda x: -x[1]["score"])
    strong = strong[:_MAX_TWINK_PAIRS]

    # Bulk-load player info
    all_pids = set()
    for _, e in strong:
        all_pids.add(e["pid_a"])
        all_pids.add(e["pid_b"])

    players = {p.player_id: p for p in
               db.query(PlayerData).filter(PlayerData.player_id.in_(list(all_pids))).all()}

    results = []
    for _, e in strong:
        pa = players.get(e["pid_a"])
        pb = players.get(e["pid_b"])
        score = e["score"]
        risk = RISK_HIGH if score >= 5 else RISK_MEDIUM if score >= 3 else RISK_LOW
        results.append({
            "pid_a":    e["pid_a"],
            "pid_b":    e["pid_b"],
            "name_a":   pa.player_last_time_name if pa else f"#{e['pid_a']}",
            "name_b":   pb.player_last_time_name if pb else f"#{e['pid_b']}",
            "steamid_a": pa.player_steamid if pa else "",
            "steamid_b": pb.player_steamid if pb else "",
            "score":    score,
            "risk":     risk,
            "signals":  e["signals"],
            "signal_count": len(e["signals"]),
        })

    return results


# ═══════════════════════════════════════════════════════════════════════
# DEVIANT BEHAVIOR — aggregated SQL only
# ═══════════════════════════════════════════════════════════════════════

def detect_deviant_behavior(db: Session, days: int = 30, min_incidents: int = 2) -> List[dict]:
    """
    Detect deviant players using aggregated SQL queries only.
    No full-table scans in Python.
    """
    since = datetime.utcnow() - timedelta(days=days)
    scores: Dict[int, dict] = {}

    def _add(player_id: int, incident: dict):
        if player_id not in scores:
            scores[player_id] = {"player_id": player_id, "incidents": [], "total_score": 0}
        scores[player_id]["incidents"].append(incident)
        scores[player_id]["total_score"] += incident["score"]

    # 1. Teamkills
    for row in db.execute(text("""
        SELECT player_id, SUM(value) AS total, COUNT(*) AS cnt
        FROM player_role_actions WHERE action_type = 'player_killed'
        GROUP BY player_id HAVING cnt >= :min LIMIT 200
    """), {"min": min_incidents}).fetchall():
        _add(row.player_id, {
            "type": "teamkill",
            "label": f"Убийств союзников: {int(row.total)}",
            "score": min(10, int(row.total) * 2),
            "icon": "💀",
        })

    # 2. Excessive team damage
    for row in db.execute(text("""
        SELECT player_id, SUM(value) AS total_dmg
        FROM player_role_actions WHERE action_type = 'damage_dealt_players'
        GROUP BY player_id HAVING total_dmg > 500 LIMIT 200
    """)).fetchall():
        _add(row.player_id, {
            "type": "team_damage",
            "label": f"Урон по союзникам: {int(row.total_dmg)} HP",
            "score": min(8, int(row.total_dmg) // 200),
            "icon": "⚔️",
        })

    # 3. Self-deaths
    for row in db.execute(text("""
        SELECT player_id, COUNT(*) AS cnt
        FROM player_role_actions WHERE action_type = 'self_death'
        GROUP BY player_id HAVING cnt >= :min LIMIT 200
    """), {"min": min_incidents + 1}).fetchall():
        _add(row.player_id, {
            "type": "self_death",
            "label": f"Самоубийств: {row.cnt}",
            "score": min(6, row.cnt),
            "icon": "☠️",
        })

    # 4. Reactor abuse (extreme values)
    for row in db.execute(text("""
        SELECT player_id, COUNT(*) AS cnt, MAX(value) AS max_val
        FROM player_role_actions
        WHERE action_type = 'operate_reactor' AND value > 500
        GROUP BY player_id HAVING cnt >= :min LIMIT 100
    """), {"min": min_incidents}).fetchall():
        _add(row.player_id, {
            "type": "reactor_abuse",
            "label": f"Экстремальные действия с реактором: {row.cnt}× (max {int(row.max_val)})",
            "score": min(8, row.cnt // 2),
            "icon": "⚛️",
        })

    # 5. Rapid reconnects
    for row in db.execute(text("""
        SELECT player_id, COUNT(*) AS cnt
        FROM player_sessions
        WHERE login_time >= :since AND session_duration IS NOT NULL AND session_duration < 120
        GROUP BY player_id HAVING cnt >= 5 LIMIT 100
    """), {"since": since}).fetchall():
        _add(row.player_id, {
            "type": "rapid_reconnect",
            "label": f"Быстрых сессий (<2 мин): {row.cnt} за {days} дней",
            "score": min(5, row.cnt // 3),
            "icon": "🔄",
        })

    if not scores:
        return []

    # Filter weak signals
    filtered = [(pid, d) for pid, d in scores.items() if d["total_score"] >= 3]
    filtered.sort(key=lambda x: -x[1]["total_score"])
    filtered = filtered[:_MAX_DEVIANT]

    # Enrich with player info
    pids = [x[0] for x in filtered]
    players = {p.player_id: p for p in
               db.query(PlayerData).filter(PlayerData.player_id.in_(pids)).all()}

    results = []
    for pid, data in filtered:
        p = players.get(pid)
        score = data["total_score"]
        risk = RISK_HIGH if score >= 10 else RISK_MEDIUM if score >= 5 else RISK_LOW
        results.append({
            **data,
            "risk":           risk,
            "player_name":    p.player_last_time_name if p else f"#{pid}",
            "player_steamid": p.player_steamid if p else "",
        })

    return results


# ═══════════════════════════════════════════════════════════════════════
# SUMMARY — lightweight counts only, no heavy analysis
# ═══════════════════════════════════════════════════════════════════════

def get_sauron_summary(db: Session) -> dict:
    """
    Quick counts using cheap SQL — does NOT run full analysis.
    """
    # Count distinct IP-sharing pairs (cheap)
    policy = build_analysis_ip_policy(db)
    twink_count = db.execute(text(f"""
        SELECT COUNT(*) FROM (
            SELECT a.player_id
            FROM player_ips a
            JOIN player_ips b ON a.ip = b.ip AND a.player_id < b.player_id
            WHERE {_NON_GLOBAL_IPV4_SQL.format(alias="a")}
              AND a.ip NOT IN :ignored_ips
            GROUP BY a.player_id
            LIMIT 500
        ) t
    """).bindparams(bindparam("ignored_ips", expanding=True)), {"ignored_ips": policy.exact_ignored_ips}).scalar() or 0

    # Count players with teamkills
    deviant_count = db.execute(text("""
        SELECT COUNT(DISTINCT player_id) FROM player_role_actions
        WHERE action_type = 'player_killed'
    """)).scalar() or 0

    return {
        "twink_pairs_total": twink_count,
        "twink_high_risk": 0,  # only populated by full scan
        "deviant_total": deviant_count,
        "deviant_high_risk": 0,
    }
