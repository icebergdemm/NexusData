"""
Ачивки: контракт с плагином Achievements.cs (game_router) + админское API
(admin_router). Полный контракт — ACHIEVEMENTS_API_GUIDE.md.

Модель взаимодействия, как у respawn_dolls (routes/respawn_dolls.py):
ДЕКЛАРАТИВНАЯ синхронизация определений, а не очередь команд.

  1. GET /game/achievements/definitions отдаёт ПОЛНЫЙ список активных
     (enabled=True, применимых к этому серверу) определений. Плагин сам
     считает прогресс по игровым событиям (локально, в своей SQLite) и сам
     выдаёт награды персонажу — сеть в этом направлении не участвует.
  2. POST /game/achievements/report — плагин отчитывается о прогрессе и
     разблокировках. Идемпотентно: unlocked — терминальное состояние,
     повторный отчёт с тем же или меньшим progress ничего не портит.
  3. Ответ ВСЕГДА 2xx (см. respawn_dolls.py про то же самое) — сбой сборки
     списка не должен звонить кодом ошибки плагину, который на 5xx просто
     сохраняет предыдущее состояние и ничего не теряет.

Синхронизация прогресса между сайтом и локальным кэшем плагина — двусторонняя:

  • Плагин → сайт: POST /game/achievements/report (батч, при изменениях).
  • Сайт → плагин: GET /game/achievements/players/{steamid}, дёргается
    плагином при входе игрока на сервер. unlocked берётся ИМЕННО из этого
    ответа (сайт авторитетен для факта разблокировки — в т.ч. после
    admin-revoke), progress/distinct_values мержатся как максимум с
    локальными (чтобы не потерять ещё не отчитанный прогресс).

Поэтому и ручная выдача (POST .../grant), и сброс (DELETE .../revoke)
реально долетают до игрока в игре — но не мгновенно, а при следующем его
входе на сервер (см. ACHIEVEMENTS_API_GUIDE.md). Для немедленного эффекта в
текущей сессии на игровом сервере есть консольная команда `ach_sync
<steamid>`.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

import services_achievements as ach_service
from config import get_settings
from database import get_db, get_game_db
from models import AchievementDef, PlayerAchievementProgress, AdminUser, AdminSession, PlayerData
from security import check_permission, verify_external_api_key_dependency

logger = logging.getLogger(__name__)
settings = get_settings()

MSK = timezone(timedelta(hours=3))


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


# ── Игровой сервер (X-API-Key) ────────────────────────────────────────────
game_router = APIRouter(
    prefix="/api/v1/game",
    tags=["achievements"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

# ── Админ-панель (сессия) ─────────────────────────────────────────────────
admin_router = APIRouter(tags=["achievements-admin"])


class ReportEntry(BaseModel):
    steamid: str
    achievement_key: str
    progress: int = 0
    target: int = 1
    distinct_values: List[str] = []
    unlocked: bool = False
    unlocked_at: Optional[int] = None
    character_name: Optional[str] = None


class ReportBatch(BaseModel):
    server_name: str
    entries: List[ReportEntry] = []


def _serialize_for_plugin(d: AchievementDef) -> Dict[str, Any]:
    return {
        "key": d.key,
        "title": d.title,
        "description": d.description,
        "icon": d.icon,
        "category": d.category,
        "scope": d.scope,
        "requires": d.requires or [],
        "trigger": d.trigger or {},
        "rewards": d.rewards or [],
        "notify_discord": bool(d.notify_discord),
    }


@game_router.get("/achievements/definitions")
def get_definitions(server: str = Query(...), db: Session = Depends(get_game_db)):
    """Полный список активных определений ачивок для этого сервера.

    Пустой список — валидный ответ (плагин просто ничего не отслеживает).
    Ошибки гасятся намеренно: 5xx хуже, чем отдать то, что удалось собрать
    (или пусто) — см. модульный докстринг."""
    try:
        rows = db.query(AchievementDef).filter(AchievementDef.enabled == True).all()  # noqa: E712
        result = []
        for d in rows:
            servers = d.servers or []
            if servers and server not in servers:
                continue
            result.append(_serialize_for_plugin(d))
        return JSONResponse({"achievements": result})
    except Exception:
        logger.exception("achievements.definitions: не удалось собрать список для %s", server)
        return JSONResponse({"achievements": []})


@game_router.get("/achievements/players/{steamid}")
def get_player_progress_for_plugin(steamid: str, db: Session = Depends(get_game_db)):
    """Текущий прогресс/разблокировки игрока — плагин тянет это при входе
    игрока на сервер (как RoundManager_ND тянет /players/info) и СЛИВАЕТ со
    своим локальным состоянием: unlocked берётся ровно из ответа сайта
    (сайт — источник истины для факта разблокировки, в т.ч. после ручного
    сброса из админки — см. api_revoke_achievement, которая обнуляет
    запись, а не удаляет), progress/distinct_values — максимум с локальным
    (чтобы не потерять ещё не отчитанный прогресс этой же сессии). Ключ,
    которого нет в ответе — сайт о нём ещё не слышал, локальное состояние
    по нему не трогается. Подробности — ACHIEVEMENTS_API_GUIDE.md."""
    try:
        rows = db.query(PlayerAchievementProgress).filter(PlayerAchievementProgress.player_steamid == steamid).all()
        return JSONResponse({"progress": [
            {
                "achievement_key": r.achievement_key,
                "progress": r.progress,
                "target": r.target,
                "distinct_values": r.distinct_values or [],
                "unlocked": r.unlocked,
            } for r in rows
        ]})
    except Exception:
        logger.exception("achievements.players: не удалось собрать прогресс для %s", steamid)
        return JSONResponse({"progress": []})


@game_router.post("/achievements/report")
def report_progress(payload: ReportBatch, db: Session = Depends(get_game_db)):
    """Батч прогресса/разблокировок. Идемпотентен — см. модульный докстринг."""
    applied = 0
    newly_unlocked: List[tuple] = []  # (row, def) — для Discord DM после commit

    try:
        def_cache: Dict[str, Optional[AchievementDef]] = {}

        def get_def(k: str) -> Optional[AchievementDef]:
            if k not in def_cache:
                def_cache[k] = db.query(AchievementDef).filter(AchievementDef.key == k).first()
            return def_cache[k]

        for e in payload.entries:
            try:
                steamid = (e.steamid or "").strip()
                key = (e.achievement_key or "").strip()
                if not steamid or not key:
                    continue

                row = (
                    db.query(PlayerAchievementProgress)
                    .filter(
                        PlayerAchievementProgress.player_steamid == steamid,
                        PlayerAchievementProgress.achievement_key == key,
                    )
                    .first()
                )
                if row is None:
                    row = PlayerAchievementProgress(player_steamid=steamid, achievement_key=key)
                    db.add(row)

                # Терминальное состояние: не перезатираем уже разблокированную
                # запись (защита от повторной/запоздалой доставки батча).
                if row.unlocked:
                    applied += 1
                    continue

                row.progress = max(row.progress or 0, e.progress)
                row.target = e.target or row.target or 1
                if e.distinct_values:
                    merged = set(row.distinct_values or [])
                    merged.update(e.distinct_values)
                    row.distinct_values = sorted(merged)
                row.server_name = payload.server_name

                if e.unlocked:
                    row.unlocked = True
                    row.unlocked_at = (
                        datetime.fromtimestamp(e.unlocked_at, tz=MSK).replace(tzinfo=None)
                        if e.unlocked_at else _now_msk()
                    )
                    d = get_def(key)
                    if d is not None:
                        newly_unlocked.append((row, d, e.character_name))

                applied += 1
            except Exception:
                logger.exception("achievements.report: ошибка обработки записи %s/%s", e.steamid, e.achievement_key)

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("achievements.report: ошибка обработки батча от %s", payload.server_name)
        return JSONResponse({"status": "error", "applied": applied})

    for row, d, character_name in newly_unlocked:
        try:
            _notify_discord_unlock(db, row, d, character_name)
        except Exception:
            logger.exception("achievements.report: не удалось отправить уведомление о разблокировке %s/%s", row.player_steamid, row.achievement_key)

    return JSONResponse({"status": "ok", "applied": applied})


def _notify_discord_unlock(db: Session, row: PlayerAchievementProgress, d: AchievementDef, character_name: Optional[str]) -> None:
    """ЛС от бота игроку о разблокированной ачивке. Тихо no-op, если у
    ачивки notify_discord=False, у игрока не привязан/не подтверждён Discord,
    или бот недоступен — отсутствие ЛС не должно ронять приём отчёта."""
    if not d.notify_discord or row.discord_notified:
        return
    player = db.query(PlayerData).filter(PlayerData.player_steamid == row.player_steamid).first()
    if not player or not player.discord_id or not player.discord_id_confirmed:
        return

    from discord_bot import bot as discord_bot

    title = f"{d.icon} {d.title}" if d.icon else d.title
    embed = {
        "title": "🏆 Новая ачивка!",
        "description": f"**{title}**" + (f"\n{d.description}" if d.description else ""),
        "color": 0xf5c518,
        "fields": [
            {"name": "Персонаж", "value": character_name or "—", "inline": True},
        ],
        "footer": {"text": "NexusData · Достижения"},
    }
    discord_bot.schedule(discord_bot.execute_bot_action("send_dm", {
        "discord_id": player.discord_id,
        "embed": embed,
    }))
    row.discord_notified = True
    db.commit()


# ══════════════════════════════════════════════════════════════════════════
# Админское API
# ══════════════════════════════════════════════════════════════════════════

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            admin = (
                db.query(AdminUser)
                .options(joinedload(AdminUser.privilege_group))
                .filter(AdminUser.id == session.admin_id)
                .first()
            )
            if admin:
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require(admin: AdminUser, action: str):
    if not check_permission(admin, "achievements", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _serialize_admin(d: AchievementDef) -> Dict[str, Any]:
    return {
        "id": d.id,
        "key": d.key,
        "title": d.title,
        "description": d.description,
        "icon": d.icon,
        "category": d.category,
        "enabled": d.enabled,
        "hidden": d.hidden,
        "scope": d.scope,
        "servers": d.servers or [],
        "trigger": d.trigger or {},
        "rewards": d.rewards or [],
        "requires": d.requires or [],
        "chain_group": d.chain_group,
        "chain_order": d.chain_order,
        "sort_order": d.sort_order,
        "notify_discord": d.notify_discord,
        "created_by": d.created_by.discord_username if d.created_by else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _apply_payload(db: Session, row: AchievementDef, body: dict, is_new: bool) -> Dict[str, Any]:
    key = (body.get("key") or row.key or "").strip().lower()
    title = (body.get("title") or row.title or "").strip()
    scope = (body.get("scope") or row.scope or "persistent").strip()
    servers = body.get("servers", row.servers if row.servers is not None else [])
    requires = body.get("requires", row.requires if row.requires is not None else [])
    chain_order = body.get("chain_order", row.chain_order if row.chain_order is not None else 0)
    trigger = body.get("trigger", row.trigger if row.trigger is not None else {})
    rewards = body.get("rewards", row.rewards if row.rewards is not None else [])

    known_keys = {
        k for (k,) in db.query(AchievementDef.key).filter(AchievementDef.key != (row.key or "\0__new__")).all()
    }

    errors, warnings, clean_requires = ach_service.validate_achievement(
        key, title, scope, servers, requires, chain_order, trigger, rewards, known_keys, self_key=row.key,
    )

    if is_new:
        exists = db.query(AchievementDef).filter(AchievementDef.key == key).first()
        if exists:
            errors.append(f"key: ачивка «{key}» уже существует")

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors[:10]))

    # Явно переданный ключ (даже пустой/None) затирает поле: конструктор в
    # админке должен уметь СНЯТЬ описание/иконку/категорию, а не только
    # заменить их на непустое. Отсутствующий в body ключ по-прежнему
    # оставляет текущее значение (PATCH частичный).
    def _opt_text(name: str, current: Optional[str]) -> Optional[str]:
        raw = body[name] if name in body else current
        return (raw or "").strip() or None

    row.key = key
    row.title = title
    row.description = _opt_text("description", row.description)
    row.icon = _opt_text("icon", row.icon)
    row.category = _opt_text("category", row.category)
    row.enabled = bool(body.get("enabled", row.enabled if row.enabled is not None else True))
    row.hidden = bool(body.get("hidden", row.hidden if row.hidden is not None else False))
    row.scope = scope
    row.servers = servers or []
    row.trigger = trigger
    row.rewards = rewards or []
    row.requires = clean_requires
    row.chain_group = _opt_text("chain_group", row.chain_group)
    row.chain_order = int(chain_order or 0)
    row.sort_order = int(body.get("sort_order", row.sort_order if row.sort_order is not None else 0) or 0)
    row.notify_discord = bool(body.get("notify_discord", row.notify_discord if row.notify_discord is not None else True))
    return {"warnings": warnings}


@admin_router.get("/api/v1/admin/achievements")
async def api_list_achievements(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "view")
    rows = db.query(AchievementDef).options(joinedload(AchievementDef.created_by)).order_by(
        AchievementDef.chain_group, AchievementDef.chain_order, AchievementDef.sort_order, AchievementDef.id
    ).all()
    return JSONResponse({
        "achievements": [_serialize_admin(d) for d in rows],
        "can_edit": check_permission(current_admin, "achievements", "edit"),
        "can_grant": check_permission(current_admin, "achievements", "grant"),
        "reference": ach_service.achievements_reference(),
    })


@admin_router.post("/api/v1/admin/achievements")
async def api_create_achievement(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "edit")
    body = await request.json()
    row = AchievementDef(key="", title="", trigger={}, created_by_admin_id=current_admin.id)
    result = _apply_payload(db, row, body, is_new=True)
    db.add(row)
    db.commit()
    db.refresh(row)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "achievement_create", {"key": row.key})
    return JSONResponse({"achievement": _serialize_admin(row), **result})


@admin_router.patch("/api/v1/admin/achievements/{key}")
async def api_update_achievement(
    key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "edit")
    row = db.query(AchievementDef).filter(AchievementDef.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail="Ачивка не найдена")
    body = await request.json()
    # Переименование key — рвёт прогресс игроков (см. докстринг модели),
    # поэтому только явным флагом, а не молча по любому body.key.
    if not body.get("allow_key_change") and body.get("key") and body["key"] != row.key:
        raise HTTPException(status_code=400, detail="Смена key рвёт прогресс игроков — подтвердите allow_key_change=true")
    result = _apply_payload(db, row, body, is_new=False)
    db.commit()
    db.refresh(row)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "achievement_update", {"key": key})
    return JSONResponse({"achievement": _serialize_admin(row), **result})


@admin_router.delete("/api/v1/admin/achievements/{key}")
async def api_delete_achievement(
    key: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Удаляет определение безвозвратно + весь прогресс игроков по нему
    (иначе в player_achievement_progress копился бы мусор по несуществующим
    ключам). Если нужно просто временно отключить — используйте enabled=false
    через PATCH, это сохраняет прогресс."""
    _require(current_admin, "edit")
    row = db.query(AchievementDef).filter(AchievementDef.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail="Ачивка не найдена")

    # Фильтруем в Python, а не через JSON.contains() на уровне SQL: генерик
    # ColumnOperators.contains() для JSON-колонки на MySQL не гарантирует
    # корректного поведения (это LIKE-подстрока, а не проверка вхождения в
    # JSON-массив) — таблица маленькая, полное сканирование дёшево и точно корректно.
    blocking = [o.key for o in db.query(AchievementDef).all() if key in (o.requires or [])]
    if blocking:
        raise HTTPException(status_code=400, detail=f"На эту ачивку ссылаются в requires: {', '.join(blocking)} — сначала уберите ссылки")

    db.query(PlayerAchievementProgress).filter(PlayerAchievementProgress.achievement_key == key).delete()
    db.delete(row)
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "achievement_delete", {"key": key})
    return JSONResponse({"status": "ok"})


@admin_router.post("/api/v1/admin/achievements/{key}/clone")
async def api_clone_achievement(
    key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "edit")
    src = db.query(AchievementDef).filter(AchievementDef.key == key).first()
    if not src:
        raise HTTPException(status_code=404, detail="Ачивка не найдена")
    body = await request.json() if await request.body() else {}
    new_key = (body.get("key") or f"{src.key}_copy").strip().lower()

    row = AchievementDef(
        key=new_key, title=body.get("title") or f"{src.title} (копия)",
        description=src.description, icon=src.icon, category=src.category,
        enabled=False, hidden=src.hidden, scope=src.scope, servers=list(src.servers or []),
        trigger=dict(src.trigger or {}), rewards=list(src.rewards or []), requires=list(src.requires or []),
        chain_group=src.chain_group, chain_order=src.chain_order, sort_order=src.sort_order,
        notify_discord=src.notify_discord, created_by_admin_id=current_admin.id,
    )
    result = _apply_payload(db, row, {
        "key": row.key, "title": row.title, "scope": row.scope, "servers": row.servers,
        "requires": row.requires, "chain_order": row.chain_order, "trigger": row.trigger, "rewards": row.rewards,
    }, is_new=True)
    db.add(row)
    db.commit()
    db.refresh(row)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "achievement_clone", {"source": key, "key": row.key})
    return JSONResponse({"achievement": _serialize_admin(row), **result})


@admin_router.get("/api/v1/admin/achievements/players/{steamid}")
async def api_player_progress(
    steamid: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "view")
    rows = db.query(PlayerAchievementProgress).filter(PlayerAchievementProgress.player_steamid == steamid).all()
    defs = {d.key: d for d in db.query(AchievementDef).all()}
    out = []
    for r in rows:
        d = defs.get(r.achievement_key)
        out.append({
            "achievement_key": r.achievement_key,
            "title": d.title if d else r.achievement_key,
            "icon": d.icon if d else None,
            "category": d.category if d else None,
            "progress": r.progress,
            "target": r.target,
            "distinct_values": r.distinct_values or [],
            "unlocked": r.unlocked,
            "unlocked_at": r.unlocked_at.isoformat() if r.unlocked_at else None,
            "granted_manually": r.granted_manually,
        })
    return JSONResponse({"progress": out})


@admin_router.post("/api/v1/admin/achievements/players/{steamid}/grant")
async def api_grant_achievement(
    steamid: str,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Ручная выдача — только запись + Discord-уведомление (награды
    предмет/стат/навык/талант она НЕ выдаёт в игре — для этого используйте
    существующую выдачу наград на странице игрока). См. докстринг модуля."""
    _require(current_admin, "grant")
    body = await request.json()
    key = (body.get("achievement_key") or "").strip()
    d = db.query(AchievementDef).filter(AchievementDef.key == key).first()
    if not d:
        raise HTTPException(status_code=404, detail="Ачивка не найдена")

    row = (
        db.query(PlayerAchievementProgress)
        .filter(PlayerAchievementProgress.player_steamid == steamid, PlayerAchievementProgress.achievement_key == key)
        .first()
    )
    if row is None:
        row = PlayerAchievementProgress(player_steamid=steamid, achievement_key=key)
        db.add(row)

    already_unlocked = row.unlocked
    row.progress = row.target = max(1, (d.trigger or {}).get("count", 1))
    row.unlocked = True
    row.unlocked_at = row.unlocked_at or _now_msk()
    row.granted_manually = True
    db.commit()
    db.refresh(row)

    if not already_unlocked:
        try:
            _notify_discord_unlock(db, row, d, None)
        except Exception:
            logger.exception("grant_achievement: не удалось отправить Discord-уведомление")

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "achievement_grant", {"steamid": steamid, "key": key})
    return JSONResponse({"status": "ok"})


@admin_router.delete("/api/v1/admin/achievements/players/{steamid}/{key}")
async def api_revoke_achievement(
    steamid: str,
    key: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Сбрасывает прогресс/разблокировку. Запись НЕ удаляется, а обнуляется
    (unlocked=False, progress=0) — это важно: плагин при следующем входе
    игрока читает GET /game/achievements/players/{steamid} и берёт unlocked
    ИМЕННО из ответа сайта, а обнулённая-но-присутствующая запись — это
    единственный способ отличить «сайт явно сбросил» от «сайт ещё не
    получал отчётов по этому ключу» (при удалении строки эти два случая
    неразличимы, и локальный анлок в кэше плагина никогда бы не сбросился).
    Реальный повторный анлок игрок получит на следующем входе на игровой
    сервер — см. ACHIEVEMENTS_API_GUIDE.md, раздел «Ручная выдача и сброс»."""
    _require(current_admin, "grant")
    row = (
        db.query(PlayerAchievementProgress)
        .filter(PlayerAchievementProgress.player_steamid == steamid, PlayerAchievementProgress.achievement_key == key)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Прогресс не найден")
    row.unlocked = False
    row.progress = 0
    row.distinct_values = []
    row.unlocked_at = None
    row.granted_manually = False
    row.discord_notified = False
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "achievement_revoke", {"steamid": steamid, "key": key})
    return JSONResponse({"status": "ok"})
