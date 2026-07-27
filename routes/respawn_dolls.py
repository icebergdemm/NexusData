"""
Куклы респавна: контракт с плагином RespawnDolls + админское API.

Модель взаимодействия ДЕКЛАРАТИВНАЯ, а не очередь команд: плагин раз в ~10 с
забирает ПОЛНЫЙ список актуальных кукол и приводит своё состояние к нему.
Из этого следуют два жёстких требования, которые здесь соблюдаются:

  1. GET /dolls/pending отдаёт ПОЛНЫЙ список, а не дельту. Кукла, не попавшая
     в ответ, считается сервером снятой со спавна — это и есть механизм отмены.
  2. Ответ ВСЕГДА 2xx, даже при внутренней ошибке. Любой не-2xx плагин считает
     сбоем связи и сохраняет предыдущее состояние реестра; отдать 500 вместо
     пустого списка — значит оставить на сервере кукол, которые уже отменены.

POST /dolls/status идемпотентен: плагин возвращает недоставленные отчёты в
очередь и повторяет их, поэтому один и тот же doll_id+status приходит не раз.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

import services_respawn_dolls as dolls_service
from config import get_settings
from database import get_db, get_game_db
from models import AdminUser, AdminSession, RespawnDoll
from security import check_permission, verify_external_api_key_dependency

settings = get_settings()
logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


# Терминальные состояния: кукла отработала, повторные отчёты их не перезаписывают.
TERMINAL_STATES = {"spawned", "spawned_with_errors", "failed", "expired"}

# ── Игровой сервер (X-API-Key) ────────────────────────────────────────────
game_router = APIRouter(
    prefix="/api/v1/game",
    tags=["respawn-dolls"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)

# ── Админ-панель (сессия) ─────────────────────────────────────────────────
admin_router = APIRouter(tags=["respawn-dolls-admin"])


class DollStatusReport(BaseModel):
    doll_id: str
    status: str
    steamid: Optional[str] = None
    character_name: Optional[str] = None
    job: Optional[str] = None
    message: Optional[str] = None
    at: Optional[int] = None


class DollStatusBatch(BaseModel):
    server: str
    reports: List[DollStatusReport] = []


@game_router.get("/dolls/pending")
def dolls_pending(server: str = Query(...), db: Session = Depends(get_game_db)):
    """Полный список актуальных кукол сервера.

    Пустой список — валидный ответ, он снимает со спавна всё. Исключения
    гасятся намеренно (см. модуль-докстринг): при сбое честнее отдать 200 с
    тем, что удалось собрать, чем 5xx, который заморозит реестр на сервере.
    """
    try:
        now = _now_msk()
        rows = (
            db.query(RespawnDoll)
            .filter(
                RespawnDoll.server == server,
                RespawnDoll.state == "pending",
                or_(RespawnDoll.expires_at.is_(None), RespawnDoll.expires_at > now),
            )
            .order_by(RespawnDoll.priority.desc(), RespawnDoll.created_at.asc())
            .limit(dolls_service.MAX_DOLLS_PER_RESPONSE)
            .all()
        )
        return JSONResponse({"dolls": [dolls_service.serialize_for_plugin(r) for r in rows]})
    except Exception:
        logger.exception("dolls_pending: не удалось собрать список кукол для %s", server)
        return JSONResponse({"dolls": []})


@game_router.post("/dolls/status")
def dolls_status(payload: DollStatusBatch, db: Session = Depends(get_game_db)):
    """Батч отчётов о судьбе кукол. Идемпотентен."""
    applied = 0
    try:
        for rep in payload.reports:
            row = db.query(RespawnDoll).filter(RespawnDoll.public_id == rep.doll_id).first()
            if row is None:
                continue
            # Защита от повторной доставки: судьба уже зафиксирована.
            if row.state in TERMINAL_STATES:
                continue

            row.state = rep.status
            row.state_message = (rep.message or None)
            if rep.status in ("spawned", "spawned_with_errors"):
                row.spawned_at = _now_msk()
                row.spawned_steamid = rep.steamid
                row.spawned_character_name = rep.character_name
                # one_shot=False — это шаблон: он должен продолжать выдаваться
                # и применяться при каждом респавне привязанных игроков.
                if not row.one_shot:
                    row.state = "pending"
            applied += 1
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("dolls_status: ошибка обработки отчётов от %s", payload.server)
        # 2xx намеренно: иначе плагин будет вечно повторять эти же отчёты.
        return JSONResponse({"status": "error", "applied": applied})
    return JSONResponse({"status": "ok", "applied": applied})


# ══════════════════════════════════════════════════════════════════════════
# Админское API
# ══════════════════════════════════════════════════════════════════════════

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
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
    if not check_permission(admin, "respawn_dolls", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _serialize_admin(row: RespawnDoll) -> dict:
    return {
        "public_id": row.public_id,
        "server": row.server,
        "label": row.label,
        "priority": row.priority,
        "one_shot": row.one_shot,
        "spawn_mode": row.spawn_mode,
        "team": row.team,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "bind_steamids": row.bind_steamids or [],
        "spec": row.spec or {},
        "state": row.state,
        "state_message": row.state_message,
        "spawned_at": row.spawned_at.isoformat() if row.spawned_at else None,
        "spawned_steamid": row.spawned_steamid,
        "spawned_character_name": row.spawned_character_name,
        "created_by": row.created_by.discord_username if row.created_by else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _parse_expires_at(raw) -> Optional[datetime]:
    """Принимает unix-секунды или ISO-строку; пусто/0 — бессрочно."""
    if raw in (None, "", 0, "0"):
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=MSK).replace(tzinfo=None)
        return datetime.fromisoformat(str(raw).replace("Z", ""))
    except (ValueError, OSError, OverflowError):
        raise HTTPException(status_code=400, detail="expires_at: некорректная дата")


def _apply_payload(row: RespawnDoll, body: dict) -> dict:
    """Валидирует и раскладывает тело запроса по колонкам. Возвращает
    предупреждения (не блокируют сохранение — см. services_respawn_dolls)."""
    spec = body.get("spec")
    if spec is None:
        spec = {}
    server = (body.get("server") or row.server or "").strip()
    spawn_mode = (body.get("spawn_mode") or row.spawn_mode or "auto").strip()
    priority = body.get("priority", row.priority if row.priority is not None else 0)
    team = body.get("team", row.team if row.team is not None else 0)
    expires_at = _parse_expires_at(body.get("expires_at")) if "expires_at" in body else row.expires_at
    bind = body.get("bind_steamids", row.bind_steamids or [])

    meta_errors, meta_warnings, clean_ids = dolls_service.validate_doll_meta(
        server, bind, spawn_mode, expires_at, team, priority
    )
    spec_errors, spec_warnings = dolls_service.validate_doll_spec(spec)

    errors = meta_errors + spec_errors
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors[:10]))

    row.server = server
    row.label = (body.get("label") or row.label or "").strip() or None
    row.spec = spec
    row.priority = int(priority or 0)
    row.one_shot = bool(body.get("one_shot", row.one_shot if row.one_shot is not None else True))
    row.expires_at = expires_at
    row.bind_steamids = clean_ids
    row.spawn_mode = spawn_mode
    row.team = int(team or 0)
    return {"warnings": meta_warnings + spec_warnings}


@admin_router.get("/api/v1/admin/dolls")
async def api_list_dolls(
    server: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "view")
    q = db.query(RespawnDoll).options(joinedload(RespawnDoll.created_by))
    if server:
        q = q.filter(RespawnDoll.server == server)
    if state:
        q = q.filter(RespawnDoll.state == state)
    rows = q.order_by(RespawnDoll.created_at.desc()).limit(500).all()
    return JSONResponse({
        "dolls": [_serialize_admin(r) for r in rows],
        "can_create": check_permission(current_admin, "respawn_dolls", "create"),
        "can_cancel": check_permission(current_admin, "respawn_dolls", "cancel"),
        "reference": {
            "jobs": sorted(dolls_service.BASE_JOBS),
            "skills": sorted(dolls_service.BASE_SKILLS),
            "slots": sorted(dolls_service.INV_SLOTS),
            "limbs": sorted(dolls_service.LIMB_TYPES),
            "spawn_modes": sorted(dolls_service.SPAWN_MODES),
        },
    })


@admin_router.get("/api/v1/admin/dolls/appearance")
async def api_appearance_catalog(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Справочник внешности: пресеты голов, причёски/бороды/аксессуары по полу,
    палитры цветов и черты характера — выгружено из игрового контента
    (Content/Characters/Human/Human.xml), чтобы админ выбирал из реального
    списка, а не угадывал числовые индексы."""
    _require(current_admin, "view")
    return JSONResponse(dolls_service.appearance_catalog())


@admin_router.post("/api/v1/admin/dolls")
async def api_create_doll(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "create")
    body = await request.json()

    row = RespawnDoll(
        public_id=dolls_service.generate_public_id(),
        server="",
        spec={},
        created_by_admin_id=current_admin.id,
        state="pending",
    )
    result = _apply_payload(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "doll_create",
                     {"public_id": row.public_id, "server": row.server, "label": row.label})
    return JSONResponse({"doll": _serialize_admin(row), **result})


@admin_router.patch("/api/v1/admin/dolls/{public_id}")
async def api_update_doll(
    public_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Правка возможна только до спавна: после него персонаж уже в мире и
    менять его описание бессмысленно (и вводило бы в заблуждение)."""
    _require(current_admin, "create")
    row = db.query(RespawnDoll).filter(RespawnDoll.public_id == public_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Кукла не найдена")
    if row.state != "pending":
        raise HTTPException(status_code=400, detail=f"Куклу в состоянии «{row.state}» изменить нельзя")

    body = await request.json()
    result = _apply_payload(row, body)
    db.commit()
    db.refresh(row)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "doll_update", {"public_id": public_id})
    return JSONResponse({"doll": _serialize_admin(row), **result})


@admin_router.delete("/api/v1/admin/dolls/{public_id}")
async def api_cancel_doll(
    public_id: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Отмена — это смена state на cancelled, а НЕ удаление строки: история
    нужна админке, а плагину достаточно, что кукла исчезла из /dolls/pending
    (на следующем опросе, ≤10 с)."""
    _require(current_admin, "cancel")
    row = db.query(RespawnDoll).filter(RespawnDoll.public_id == public_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Кукла не найдена")
    if row.state != "pending":
        raise HTTPException(status_code=400, detail=f"Кукла уже в состоянии «{row.state}» — отменять нечего")

    row.state = "cancelled"
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "doll_cancel", {"public_id": public_id})
    return JSONResponse({"status": "ok", "doll": _serialize_admin(row)})


@admin_router.post("/api/v1/admin/dolls/{public_id}/clone")
async def api_clone_doll(
    public_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Клон исходной куклы в состоянии pending — основа для пачки однотипных
    кукол. В теле можно сразу переопределить bind_steamids/label."""
    _require(current_admin, "create")
    src = db.query(RespawnDoll).filter(RespawnDoll.public_id == public_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Кукла не найдена")

    body = await request.json() if await request.body() else {}
    row = RespawnDoll(
        public_id=dolls_service.generate_public_id(),
        server=src.server,
        label=body.get("label") or (f"{src.label} (копия)" if src.label else None),
        spec=dict(src.spec or {}),
        priority=src.priority,
        one_shot=src.one_shot,
        expires_at=src.expires_at,
        bind_steamids=body.get("bind_steamids", []),
        spawn_mode=src.spawn_mode,
        team=src.team,
        state="pending",
        created_by_admin_id=current_admin.id,
    )
    # Прогоняем через ту же валидацию: у клона могли поменять привязки.
    result = _apply_payload(row, {
        "server": row.server, "label": row.label, "spec": row.spec,
        "priority": row.priority, "one_shot": row.one_shot,
        "expires_at": int(row.expires_at.timestamp()) if row.expires_at else None,
        "bind_steamids": row.bind_steamids, "spawn_mode": row.spawn_mode, "team": row.team,
    })
    db.add(row)
    db.commit()
    db.refresh(row)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "doll_clone",
                     {"source": public_id, "public_id": row.public_id})
    return JSONResponse({"doll": _serialize_admin(row), **result})
