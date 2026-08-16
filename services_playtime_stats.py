"""
Playtime Statistics Worker.
Runs daily at 00:00 MSK to calculate play time per player per day/week/month.
Stores results in JSON files to avoid DB bloat.
Also supports reward group conditions based on playtime.
"""

import os
import json
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PLAYTIME_STATS_DIR = "data/playtime_stats"
REWARD_GROUPS_FILE = "data/reward_groups.json"
PLAYTIME_CONDITIONS_FILE = "data/playtime_conditions.json"

MSK = timezone(timedelta(hours=3))


def _ensure_dirs():
    os.makedirs(PLAYTIME_STATS_DIR, exist_ok=True)
    os.makedirs("data", exist_ok=True)


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


def _today_msk() -> str:
    return _now_msk().strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════════════════════
# Playtime stats calculation
# ══════════════════════════════════════════════════════════════════════════════

def calculate_playtime_stats():
    """Calculate playtime stats for all players: per day, week, month.
    Queries PlayerSession table and aggregates durations.
    Saves to data/playtime_stats/YYYY-MM-DD.json
    """
    from database import SessionLocal
    from models import PlayerSession, PlayerData
    from sqlalchemy import func

    _ensure_dirs()
    now = _now_msk()
    today = now.date()

    db = SessionLocal()
    try:
        # Define time boundaries
        day_start = datetime(today.year, today.month, today.day)
        week_start = day_start - timedelta(days=today.weekday())  # Monday
        month_start = datetime(today.year, today.month, 1)

        # Query all sessions that overlap with each period
        all_sessions = db.query(PlayerSession).filter(
            PlayerSession.login_time >= month_start - timedelta(hours=3)  # UTC offset
        ).all()

        # Batch-fetch all involved players ONCE (avoids N+1)
        pids = {s.player_id for s in all_sessions if s.player_id}
        pd_map = {}
        if pids:
            for pd in db.query(
                PlayerData.player_id, PlayerData.player_steamid, PlayerData.player_last_time_name
            ).filter(PlayerData.player_id.in_(pids)).all():
                pd_map[pd.player_id] = (pd.player_steamid or "", pd.player_last_time_name or "")

        # Build per-player stats
        player_stats: Dict[str, Dict] = {}

        for s in all_sessions:
            if not s.player_id or not s.session_duration:
                continue

            pid = str(s.player_id)
            if pid not in player_stats:
                steamid, name = pd_map.get(s.player_id, ("", ""))
                player_stats[pid] = {
                    "player_id": s.player_id,
                    "steamid": steamid,
                    "name": name,
                    "day": 0,
                    "week": 0,
                    "month": 0,
                }

            login_utc = s.login_time
            duration = s.session_duration or 0

            # Convert to MSK for period checking
            login_msk = login_utc + timedelta(hours=3) if login_utc else None
            if not login_msk:
                continue

            # Check which periods this session falls into
            if login_msk.date() == today:
                player_stats[pid]["day"] += duration
            if login_msk >= week_start:
                player_stats[pid]["week"] += duration
            if login_msk >= month_start:
                player_stats[pid]["month"] += duration

        # Save to file
        output = {
            "generated_at": now.isoformat(),
            "date": today.isoformat(),
            "week_start": week_start.isoformat(),
            "month_start": month_start.isoformat(),
            "players": player_stats,
        }

        filepath = os.path.join(PLAYTIME_STATS_DIR, f"{today.isoformat()}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)

        # Also save as "latest.json" for quick access
        latest_path = os.path.join(PLAYTIME_STATS_DIR, "latest.json")
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)

        logger.info(f"Playtime stats generated for {today}: {len(player_stats)} players")
        return output

    except Exception as e:
        logger.error(f"Failed to calculate playtime stats: {e}")
        return None
    finally:
        db.expire_all()
        db.close()


def get_latest_playtime_stats() -> Optional[Dict]:
    """Load the latest playtime stats from disk."""
    _ensure_dirs()
    path = os.path.join(PLAYTIME_STATS_DIR, "latest.json")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Reward Groups (configurable groups of items/talents/stats)
# ══════════════════════════════════════════════════════════════════════════════

def load_reward_groups() -> List[Dict]:
    """Load configured reward groups from JSON."""
    _ensure_dirs()
    if os.path.exists(REWARD_GROUPS_FILE):
        try:
            with open(REWARD_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_reward_groups(groups: List[Dict]):
    """Save reward groups to JSON."""
    _ensure_dirs()
    with open(REWARD_GROUPS_FILE, 'w', encoding='utf-8') as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


def load_playtime_conditions() -> List[Dict]:
    """Load playtime-based reward conditions from JSON."""
    _ensure_dirs()
    if os.path.exists(PLAYTIME_CONDITIONS_FILE):
        try:
            with open(PLAYTIME_CONDITIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_playtime_conditions(conditions: List[Dict]):
    """Save playtime conditions to JSON."""
    _ensure_dirs()
    with open(PLAYTIME_CONDITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(conditions, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# Rating reward conditions (top-N по расписанию: день недели + время МСК)
# ══════════════════════════════════════════════════════════════════════════════
#
# ПОЛНОСТЬЮ ПЕРЕДЕЛАНО (была ежедневная в полночь безусловная раздача без
# отмены — см. историю check_and_apply_rating_rewards ниже и её докстринг).
# Формат правила теперь:
#   {id, role, schedule: {weekday: 0..6 (0=Пн), time_msk: "HH:MM"},
#    places: [{place, reward_group_id}]}
# `id` — стабильный идентификатор правила (не пересчитывается от содержимого,
# иначе переименование/перестановка правил рвало бы связь с уже выданными
# наградами в RatingRewardHolder/RatingRewardScheduleState) — назначается
# один раз при первом сохранении правила без id.

RATING_REWARDS_FILE = "data/rating_rewards.json"


def _generate_rule_id() -> str:
    return f"rr_{secrets.token_hex(4)}"


def load_rating_rewards() -> List[Dict]:
    """Load rating-based reward conditions, auto-assigning `id` to любому
    правилу, у которого его ещё нет (напр. сохранённому до этого рефакторинга,
    либо созданному через API без id) — и сразу персистит эту миграцию на
    диск, чтобы id не менялся между вызовами."""
    _ensure_dirs()
    rules = []
    if os.path.exists(RATING_REWARDS_FILE):
        try:
            with open(RATING_REWARDS_FILE, 'r', encoding='utf-8') as f:
                rules = json.load(f)
        except Exception:
            rules = []

    changed = False
    for rule in rules:
        if not rule.get("id"):
            rule["id"] = _generate_rule_id()
            changed = True
        if not isinstance(rule.get("schedule"), dict):
            # Старый формат (check_interval_days) или вообще без расписания —
            # безопасный дефолт: понедельник, полночь МСК. Админ должен
            # перенастроить через UI, это не попытка угадать прежнюю семантику.
            rule["schedule"] = {"weekday": 0, "time_msk": "00:00"}
            changed = True
    if changed:
        save_rating_rewards(rules)
    return rules


def save_rating_rewards(rewards: List[Dict]):
    """Save rating reward conditions. Проставляет id тем правилам, у которых
    его ещё нет (тот же приём, что и в load_rating_rewards — сохранение
    может прийти и напрямую из API, в обход load)."""
    _ensure_dirs()
    for rule in rewards:
        if not rule.get("id"):
            rule["id"] = _generate_rule_id()
    with open(RATING_REWARDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(rewards, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# Apply rewards to players
# ══════════════════════════════════════════════════════════════════════════════

def apply_reward_group_to_player(player_steamid: str, group_id: str):
    """Apply a reward group's items/stats/talents to a player's achievements.

    КРИТИЧНО: `ach` обязан быть НОВЫМ dict-объектом, а не тем же самым, что
    уже лежит в player.achievements. `player.achievements or {}` при
    непустом achievements возвращает ССЫЛКУ НА ТОТ ЖЕ объект — мутация
    "на месте" с последующим `player.achievements = ach` присваивает
    атрибуту тот же объект, который там и так уже был, и SQLAlchemy эту
    "смену" не видит (нечего сравнивать со старым значением — оно и есть
    новое). Из-за этого одна и та же строка ОТСЮДА реально работала только
    на первый вызов для игрока (когда achievements=None и `or {}` создаёт
    настоящий новый объект) — на любой повторный вызов (а значит на любого
    игрока, уже получавшего хоть одну награду когда-либо) изменения тихо
    переставали сохраняться: без исключения, без ошибки в логах, commit
    "проходил", но данные не менялись. Это и есть основная причина
    "некорректно работает выдача наград за места в рейтинге" — воспроизведено
    юнит-тестом на реальной БД (см. test_rating_rewards в истории задачи)."""
    from database import SessionLocal
    from models import PlayerData

    groups = load_reward_groups()
    group = next((g for g in groups if g.get("id") == group_id), None)
    if not group:
        logger.warning(f"Reward group {group_id} not found")
        return False

    db = SessionLocal()
    try:
        player = db.query(PlayerData).filter(PlayerData.player_steamid == player_steamid).first()
        if not player:
            logger.warning(f"Player {player_steamid} not found for reward")
            return False

        ach = {} if isinstance(player.achievements, list) else dict(player.achievements or {})

        # Apply items
        items = dict(ach.get("items", {}))
        for item_id, qty in (group.get("items") or {}).items():
            items[item_id] = items.get(item_id, 0) + qty
        ach["items"] = items

        # Apply stats
        stats = dict(ach.get("stats", {}))
        for key, val in (group.get("stats") or {}).items():
            if key == "all":
                stats["all"] = stats.get("all", 0) + val
            else:
                job_stats = dict(stats.get(key, {}))
                for sk, sv in val.items() if isinstance(val, dict) else []:
                    job_stats[sk] = job_stats.get(sk, 0) + sv
                stats[key] = job_stats
        ach["stats"] = stats

        # Apply talents
        talents = dict(ach.get("talents", {}))
        for job_id, talent_list in (group.get("talents") or {}).items():
            existing = list(talents.get(job_id, []))
            for t in talent_list:
                if t not in existing:
                    existing.append(t)
            talents[job_id] = existing
        ach["talents"] = talents

        player.achievements = ach
        db.commit()
        logger.info(f"Applied reward group {group_id} to player {player_steamid}")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to apply reward group: {e}")
        return False
    finally:
        db.close()


def revoke_reward_group_from_player(player_steamid: str, group_id: str) -> bool:
    """Обратная операция к apply_reward_group_to_player — вычитает ровно то,
    что эта группа когда-то добавила (та же арифметика, в обратную сторону).

    Ограничение по талантам: если ОДИН И ТОТ ЖЕ talent_id выдан игроку сразу
    несколькими независимыми источниками (эта группа + другая активная
    награда/ачивка), revoke снимет его, даже если другой источник по-прежнему
    "считает", что он его выдал — отдельного учёта "кто именно выдал этот
    конкретный талант" в текущей модели данных (плоский achievements JSON)
    нет. Для items/stats это не проблема — там ведётся числовой баланс."""
    from database import SessionLocal
    from models import PlayerData

    groups = load_reward_groups()
    group = next((g for g in groups if g.get("id") == group_id), None)
    if not group:
        logger.warning(f"Reward group {group_id} not found for revoke")
        return False

    db = SessionLocal()
    try:
        player = db.query(PlayerData).filter(PlayerData.player_steamid == player_steamid).first()
        if not player:
            return False

        # См. предупреждение в apply_reward_group_to_player выше — тот же
        # обязательный НОВЫЙ dict-объект, иначе SQLAlchemy не увидит изменение.
        ach = {} if isinstance(player.achievements, list) else dict(player.achievements or {})

        items = dict(ach.get("items", {}))
        for item_id, qty in (group.get("items") or {}).items():
            left = items.get(item_id, 0) - qty
            if left > 0:
                items[item_id] = left
            else:
                items.pop(item_id, None)
        ach["items"] = items

        stats = dict(ach.get("stats", {}))
        for key, val in (group.get("stats") or {}).items():
            if key == "all":
                left = stats.get("all", 0) - val
                stats["all"] = max(0, left)
            else:
                job_stats = dict(stats.get(key, {}))
                for sk, sv in val.items() if isinstance(val, dict) else []:
                    left = job_stats.get(sk, 0) - sv
                    if left > 0:
                        job_stats[sk] = left
                    else:
                        job_stats.pop(sk, None)
                if job_stats:
                    stats[key] = job_stats
                else:
                    stats.pop(key, None)
        ach["stats"] = stats

        talents = dict(ach.get("talents", {}))
        for job_id, talent_list in (group.get("talents") or {}).items():
            existing = [t for t in talents.get(job_id, []) if t not in talent_list]
            if existing:
                talents[job_id] = existing
            else:
                talents.pop(job_id, None)
        ach["talents"] = talents

        player.achievements = ach
        db.commit()
        logger.info(f"Revoked reward group {group_id} from player {player_steamid}")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to revoke reward group: {e}")
        return False
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Расписание правил (день недели + время МСК) и реконсиляция держателей
# ══════════════════════════════════════════════════════════════════════════════
#
# Старая check_and_apply_rating_rewards() была фундаментально сломана:
#   1. Не было расписания — раздача шла безусловно каждую полночь.
#   2. Не было отзыва — apply_reward_group_to_player только ДОБАВЛЯЕТ, и без
#      понятия "уже выдано в этом цикле" награда лидеру насчитывалась ЗАНОВО
#      каждую полночь, пока он держит место (предметы копились бесконечно).
#   3. Игрок, потерявший место, продолжал владеть уже выданной наградой
#      навсегда — забрать её было нечем и не на основании чего.
# reconcile_rating_rewards() читает текущих держателей (RatingRewardHolder),
# сравнивает с тем, кто СЕЙЧАС занимает место в рейтинге, и делает МИНИМАЛЬНО
# необходимое: снимает у того, кто больше не держит место (или у кого
# сменилась выдаваемая группа), выдаёт новому держателю — и ничего не делает,
# если всё уже верно (идемпотентно, безопасно вызывать сколько угодно раз).

WEEKDAY_LABELS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _cycle_key(now_msk: datetime) -> str:
    """ISO-неделя вида '2026-W33' — единица цикла: правило срабатывает не
    больше одного раза за календарную ISO-неделю."""
    iso = now_msk.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _rule_due(rule: Dict, now_msk: datetime, last_cycle_key: Optional[str]) -> Tuple[bool, str]:
    """Правило "наступило", если сейчас его день недели, время >= назначенного
    (и не сильно позже — окно в 1 час на случай, если сервер был недоступен
    ровно в нужную минуту, но не бесконечно, иначе после долгого простоя
    правило сработало бы даже спустя дни) — и оно ещё не срабатывало в
    текущем ISO-цикле."""
    schedule = rule.get("schedule") or {}
    weekday = schedule.get("weekday")
    time_msk = schedule.get("time_msk") or "00:00"
    if weekday is None or not isinstance(weekday, int) or not (0 <= weekday <= 6):
        return False, ""
    try:
        hh, mm = (int(x) for x in str(time_msk).split(":", 1))
    except (ValueError, TypeError):
        hh, mm = 0, 0

    cycle = _cycle_key(now_msk)
    if cycle == last_cycle_key:
        return False, cycle  # уже сработало в этом цикле

    if now_msk.weekday() != weekday:
        return False, cycle

    scheduled_today = now_msk.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now_msk < scheduled_today:
        return False, cycle
    if now_msk - scheduled_today > timedelta(hours=1):
        return False, cycle  # окно упущено — ждём следующего цикла, не досрабатываем задним числом

    return True, cycle


def _get_or_create_schedule_state(db, rule_id: str):
    from models import RatingRewardScheduleState
    state = db.query(RatingRewardScheduleState).filter(RatingRewardScheduleState.rule_id == rule_id).first()
    if not state:
        state = RatingRewardScheduleState(rule_id=rule_id)
        db.add(state)
    return state


def reconcile_rating_rewards(force: bool = False, rule_id: Optional[str] = None) -> Dict:
    """Приводит текущих держателей наград за место в рейтинге в соответствие
    с настоящим состоянием лидерборда. force=True игнорирует расписание
    (кнопка "Применить сейчас" в админке) — при этом ISO-цикл ВСЁ РАВНО
    обновляется, чтобы принудительный прогон не привёл к повторному
    срабатыванию по расписанию в течение той же недели.

    rule_id — прогнать только одно правило (не используется воркером,
    полезно для точечной проверки из админки)."""
    from database import SessionLocal
    from models import PlayerRating, RatingRewardHolder
    from sqlalchemy import desc

    rules = load_rating_rewards()
    if rule_id:
        rules = [r for r in rules if r.get("id") == rule_id]
    if not rules:
        return {"processed_rules": 0, "granted": 0, "revoked": 0}

    now = _now_msk()
    granted = 0
    revoked = 0
    processed = 0

    db = SessionLocal()
    try:
        for rule in rules:
            rid = rule.get("id")
            role = rule.get("role", "")
            places = rule.get("places", [])
            if not rid or not role or not places:
                continue

            state = _get_or_create_schedule_state(db, rid)
            if not force:
                due, cycle = _rule_due(rule, now, state.last_cycle_key)
                if not due:
                    continue
            else:
                cycle = _cycle_key(now)

            processed += 1

            max_place = max((p.get("place", 0) for p in places), default=0)
            records = (
                db.query(PlayerRating)
                .filter(PlayerRating.role == role, PlayerRating.current_rating > 0)
                .order_by(desc(PlayerRating.current_rating))
                .limit(max_place)
                .all()
            ) if max_place > 0 else []

            for place_config in places:
                place = place_config.get("place", 0)
                group_id = place_config.get("reward_group_id", "")
                if place < 1 or not group_id:
                    continue

                current_steamid = records[place - 1].steam_id if place <= len(records) else None

                holder = (
                    db.query(RatingRewardHolder)
                    .filter(RatingRewardHolder.rule_id == rid, RatingRewardHolder.place == place)
                    .first()
                )
                prev_steamid = holder.steam_id if holder else None
                prev_group_id = holder.reward_group_id if holder else None

                if prev_steamid == current_steamid and prev_group_id == group_id:
                    continue  # уже верно выдано — не трогаем

                # Снять у прежнего держателя, если он был и его группа
                # известна (иначе нечего/нечем снимать корректно).
                if prev_steamid and prev_group_id:
                    if revoke_reward_group_from_player(prev_steamid, prev_group_id):
                        revoked += 1

                # Выдать новому (если место сейчас вообще кем-то занято).
                if current_steamid:
                    if apply_reward_group_to_player(current_steamid, group_id):
                        granted += 1

                if holder is None:
                    holder = RatingRewardHolder(rule_id=rid, place=place)
                    db.add(holder)
                holder.steam_id = current_steamid
                holder.reward_group_id = group_id if current_steamid else None
                holder.granted_at = now

            state.last_cycle_key = cycle
            state.last_run_at = now
            db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to reconcile rating rewards: {e}")
    finally:
        db.close()

    return {"processed_rules": processed, "granted": granted, "revoked": revoked}


def get_rating_reward_holders() -> List[Dict]:
    """Текущие держатели наград по всем правилам — для отображения в админке
    (видно, у кого что реально выдано прямо сейчас, без похода в БД руками)."""
    from database import SessionLocal
    from models import RatingRewardHolder, PlayerData

    db = SessionLocal()
    try:
        rows = db.query(RatingRewardHolder).filter(RatingRewardHolder.steam_id.isnot(None)).all()
        steamids = {r.steam_id for r in rows}
        names = {}
        if steamids:
            for p in db.query(PlayerData.player_steamid, PlayerData.player_last_time_name, PlayerData.forced_display_name).filter(PlayerData.player_steamid.in_(steamids)).all():
                names[p.player_steamid] = p.forced_display_name or p.player_last_time_name
        return [
            {
                "rule_id": r.rule_id, "place": r.place, "steam_id": r.steam_id,
                "player_name": names.get(r.steam_id),
                "reward_group_id": r.reward_group_id,
                "granted_at": r.granted_at.isoformat() if r.granted_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def check_and_apply_playtime_rewards():
    """Check playtime conditions and apply reward groups to qualifying players."""
    conditions = load_playtime_conditions()
    if not conditions:
        return

    stats = get_latest_playtime_stats()
    if not stats or not stats.get("players"):
        return

    for condition in conditions:
        period = condition.get("period", "day")  # day, week, month
        min_seconds = condition.get("min_seconds", 0)
        group_id = condition.get("reward_group_id", "")
        if not group_id or min_seconds <= 0:
            continue

        for pid, pdata in stats["players"].items():
            playtime = pdata.get(period, 0)
            if playtime >= min_seconds:
                steamid = pdata.get("steamid", "")
                if steamid:
                    apply_reward_group_to_player(steamid, group_id)


# ══════════════════════════════════════════════════════════════════════════════
# Background Worker (daily at 00:00 MSK)
# ══════════════════════════════════════════════════════════════════════════════

_playtime_stats_worker_running = False


def _playtime_stats_worker_loop():
    """Background worker: runs daily at 00:00 MSK."""
    global _playtime_stats_worker_running
    _playtime_stats_worker_running = True
    logger.info("Playtime stats worker started")

    # Initial calculation on startup
    time.sleep(10)  # Wait for DB to be ready
    calculate_playtime_stats()

    while _playtime_stats_worker_running:
        now = _now_msk()
        # Calculate seconds until next 00:00 MSK
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        wait_seconds = (tomorrow - now).total_seconds()

        # Sleep until midnight (check every 60s if we should stop)
        slept = 0
        while slept < wait_seconds and _playtime_stats_worker_running:
            time.sleep(60)
            slept += 60

        if not _playtime_stats_worker_running:
            break

        # It's midnight MSK — run calculations
        logger.info("Midnight MSK — running playtime stats calculation")
        calculate_playtime_stats()

        # Награды по рейтингу теперь проверяются отдельной периодической
        # задачей по расписанию (workers.task_check_rating_reward_schedules,
        # раз в 5 минут — правило само решает, наступило ли его время), а не
        # раз в сутки здесь. См. reconcile_rating_rewards выше.
        try:
            check_and_apply_playtime_rewards()
        except Exception as e:
            logger.error(f"Playtime rewards check failed: {e}")

    logger.info("Playtime stats worker stopped")


def start_playtime_stats_worker():
    """Start the daily playtime stats worker."""
    global _playtime_stats_worker_running
    if _playtime_stats_worker_running:
        return
    t = threading.Thread(target=_playtime_stats_worker_loop, daemon=True, name="PlaytimeStatsWorker")
    t.start()


def stop_playtime_stats_worker():
    """Stop the background playtime stats worker."""
    global _playtime_stats_worker_running
    _playtime_stats_worker_running = False
