"""Declarative control plane for the MonsterSpawnController game plugin."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from config import get_settings
from database import get_db, get_game_db
from models import AdminSession, AdminUser, MonsterSpawnControl
from security import check_permission, verify_external_api_key_dependency

settings = get_settings()

game_router = APIRouter(
    prefix="/api/v1/game/monster-spawn",
    tags=["monster-spawn-game"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)
admin_router = APIRouter(prefix="/api/v1/admin/monster-spawn", tags=["monster-spawn-admin"])


class PluginTelemetry(BaseModel):
    server: str
    applied_revision: int = 0
    config: Dict[str, Any] = Field(default_factory=dict)
    memory: Dict[str, Any] = Field(default_factory=dict)
    plans: List[Dict[str, Any]] = Field(default_factory=list)
    telemetry: Dict[str, Any] = Field(default_factory=dict)


class AdminUpdate(BaseModel):
    config: Optional[Dict[str, Any]] = None
    memory: Optional[Dict[str, Any]] = None
    plans: Optional[List[Dict[str, Any]]] = None


def _normalize_server(server: str) -> str:
    value = (server or "").strip().upper()
    if not value:
        raise HTTPException(status_code=400, detail="Не указан сервер")
    return value


def _get_or_create(db: Session, server: str) -> MonsterSpawnControl:
    row = db.query(MonsterSpawnControl).filter(MonsterSpawnControl.server == server).first()
    if row is None:
        row = MonsterSpawnControl(server=server)
        db.add(row)
        db.flush()
    return row


def _normalize_config(value: Any) -> Dict[str, Any]:
    """Keep old plugin snapshots compatible with the current controller contract."""
    config = dict(value) if isinstance(value, dict) else {}
    global_config = config.get("global") if isinstance(config.get("global"), dict) else {}
    defaults = {
        "intensity_gate_enabled": True,
        "intensity_threshold": 0.2,
        "intensity_threshold_increase": 0.0005,
        "wave_cooldown_seconds": 0,
        "respect_server_monster_settings": True,
        "use_swarm_behavior": True,
        "submarine_adaptive_influence": 0.25,
    }
    config["global"] = {**defaults, **global_config}
    waves = config.get("waves") if isinstance(config.get("waves"), list) else []
    normalized_waves = []
    for raw_wave in waves:
        if not isinstance(raw_wave, dict):
            continue
        wave = {
            "commonness": 1,
            "delay_between_spawns": 0.1,
            "scatter": 500,
            "offset": 0,
            "spawn_distance": 0,
            "reset_time": 0,
            "play_dead_probability": -1,
            **raw_wave,
        }
        monsters = wave.get("monsters") if isinstance(wave.get("monsters"), list) else []
        wave["monsters"] = [
            {
                "amount_min": 0,
                "amount_max": 0,
                "max_amount_per_level": 0,
                **monster,
            }
            for monster in monsters
            if isinstance(monster, dict)
        ]
        normalized_waves.append(wave)
    config["waves"] = normalized_waves
    return config


def _serialize(row: MonsterSpawnControl) -> dict:
    telemetry = row.telemetry if isinstance(row.telemetry, dict) else {}
    memory = row.memory if isinstance(row.memory, dict) else {}
    submarines = memory.get("submarines") if isinstance(memory.get("submarines"), dict) else {}
    current_submarine_key = telemetry.get("submarine")
    current_submarine = submarines.get(current_submarine_key) if current_submarine_key else None
    if not isinstance(current_submarine, dict):
        current_submarine = None

    return {
        "server": row.server,
        "config": _normalize_config(row.config),
        "memory": memory,
        "plans": row.plans if isinstance(row.plans, list) else [],
        "telemetry": telemetry,
        "dashboard": {
            "round_active": bool(telemetry.get("round_active", False)),
            "round_seconds": telemetry.get("round_seconds", 0),
            "submarine_key": current_submarine_key,
            "submarine_name": telemetry.get("submarine_name") or (current_submarine or {}).get("name"),
            "submarine": current_submarine,
            "intensity": telemetry.get("intensity", 0),
            "event_threshold": telemetry.get("event_threshold", 0.2),
            "pending_waves": telemetry.get("pending_waves", 0),
            "known_submarines": len(submarines),
        },
        "desired_revision": row.desired_revision or 0,
        "applied_revision": row.applied_revision or 0,
        "pending": (row.desired_revision or 0) != (row.applied_revision or 0),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number else default
    except (TypeError, ValueError):
        return default


def _average(values: Any, fallback: float = 0.0) -> float:
    if not isinstance(values, list):
        return fallback
    numbers = [_number(value) for value in values]
    return sum(numbers) / len(numbers) if numbers else fallback


def _build_spawn_analysis(row: MonsterSpawnControl) -> dict:
    """Build read-only operator guidance from the plugin's persisted memory."""
    memory = row.memory if isinstance(row.memory, dict) else {}
    raw_waves = memory.get("waves") if isinstance(memory.get("waves"), dict) else {}
    raw_submarines = memory.get("submarines") if isinstance(memory.get("submarines"), dict) else {}

    wave_logs = []
    for key, raw in raw_waves.items():
        if not isinstance(raw, dict):
            continue
        spawned = max(0, int(_number(raw.get("times_spawned"))))
        unresolved = max(0, int(_number(raw.get("times_unresolved"))))
        avg_repel = _number(raw.get("total_repel_seconds")) / spawned if spawned else 0.0
        avg_deaths = _number(raw.get("total_crew_deaths")) / spawned if spawned else 0.0
        avg_loss = _number(raw.get("total_sub_health_loss")) / spawned if spawned else 0.0
        wave_logs.append({
            "name": str(key),
            "times_spawned": spawned,
            "times_unresolved": unresolved,
            "avg_repel_seconds": round(avg_repel, 2),
            "avg_performance": round(_average(raw.get("recent_performance"), 0.0), 3),
            "avg_crew_deaths": round(avg_deaths, 2),
            "avg_sub_health_loss": round(avg_loss, 2),
            "last_played_utc": raw.get("last_played_utc"),
        })

    ship_logs = []
    for key, raw in raw_submarines.items():
        if not isinstance(raw, dict):
            continue
        spawned = max(0, int(_number(raw.get("times_spawned"))))
        ship_logs.append({
            "key": str(key),
            "name": str(raw.get("name") or key),
            "type": str(raw.get("type") or "unknown"),
            "rounds_played": max(0, int(_number(raw.get("rounds_played")))),
            "times_spawned": spawned,
            "avg_repel_seconds": round(_number(raw.get("total_repel_seconds")) / spawned if spawned else 0.0, 2),
            "performance_ema": round(_number(raw.get("performance_ema"), 1.0), 3),
            "avg_crew_deaths": round(_number(raw.get("total_crew_deaths")) / spawned if spawned else 0.0, 2),
            "avg_sub_health_loss": round(_number(raw.get("total_sub_health_loss")) / spawned if spawned else 0.0, 2),
            "recent_performance": [round(_number(value), 3) for value in raw.get("recent_performance", [])]
            if isinstance(raw.get("recent_performance"), list) else [],
            "last_played_utc": raw.get("last_played_utc"),
        })

    recommendations = []
    global_ema = _number(memory.get("global_performance_ema"), 1.0)
    observed_waves = [wave for wave in wave_logs if wave["times_spawned"] > 0]
    observed_ships = [ship for ship in ship_logs if ship["times_spawned"] > 0]

    if not observed_waves and not observed_ships:
        recommendations.append({
            "id": "collect-baseline",
            "severity": "info",
            "title": "Нужно накопить baseline",
            "reason": "В памяти пока нет завершённых наблюдений, поэтому изменение сложности будет предположением.",
            "evidence": {"waves": 0, "ships": 0},
            "suggested_adjustment": "Оставить текущие параметры без изменений до появления нескольких завершённых волн.",
            "confidence": 0.0,
            "read_only": True,
        })
    else:
        slow_waves = [wave for wave in observed_waves if wave["avg_performance"] > 0 and wave["avg_performance"] < 0.75]
        fast_waves = [wave for wave in observed_waves if wave["avg_performance"] >= 1.35]
        unresolved_waves = [wave for wave in wave_logs if wave["times_unresolved"] > 0]
        if slow_waves:
            wave = min(slow_waves, key=lambda item: item["avg_performance"])
            recommendations.append({
                "id": "ease-slow-wave",
                "severity": "warning",
                "title": f"Волна {wave['name']} перегружает экипаж",
                "reason": "Низкая наблюдаемая эффективность означает, что волна переживается дольше ожидаемого.",
                "evidence": {"wave": wave["name"], "performance": wave["avg_performance"], "repel_seconds": wave["avg_repel_seconds"]},
                "suggested_adjustment": "Рассмотреть уменьшение amount_multiplier или difficulty_multiplier для этого vanilla event.",
                "confidence": round(min(1.0, wave["times_spawned"] / 5), 2),
                "read_only": True,
            })
        if fast_waves:
            wave = max(fast_waves, key=lambda item: item["avg_performance"])
            recommendations.append({
                "id": "raise-fast-wave",
                "severity": "success",
                "title": f"Волна {wave['name']} зачищается слишком быстро",
                "reason": "Экипаж завершает столкновение заметно быстрее ожидаемого времени.",
                "evidence": {"wave": wave["name"], "performance": wave["avg_performance"], "repel_seconds": wave["avg_repel_seconds"]},
                "suggested_adjustment": "Рассмотреть повышение difficulty_multiplier или adaptive_amount_influence на малый шаг.",
                "confidence": round(min(1.0, wave["times_spawned"] / 5), 2),
                "read_only": True,
            })
        if unresolved_waves:
            recommendations.append({
                "id": "inspect-unresolved",
                "severity": "warning",
                "title": "Есть незавершённые наблюдения",
                "reason": "Часть волн не была корректно разрешена до завершения раунда или таймаута.",
                "evidence": {"waves": len(unresolved_waves), "unresolved": sum(wave["times_unresolved"] for wave in unresolved_waves)},
                "suggested_adjustment": "Сначала проверить tracking timeout и доступность spawn position, а не повышать сложность.",
                "confidence": 0.8,
                "read_only": True,
            })
        if global_ema < 0.8:
            recommendations.append({
                "id": "global-pressure",
                "severity": "warning",
                "title": "Общий уровень давления высок",
                "reason": "Global performance EMA ниже комфортного диапазона.",
                "evidence": {"global_performance_ema": round(global_ema, 3), "observed_waves": len(observed_waves)},
                "suggested_adjustment": "Рассмотреть мягкое снижение общей сложности и проверить корабли с наименьшим performance EMA.",
                "confidence": round(min(1.0, len(observed_waves) / 10), 2),
                "read_only": True,
            })

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "read_only": True,
        "global_performance_ema": round(global_ema, 3),
        "wave_logs": sorted(wave_logs, key=lambda item: item["avg_repel_seconds"], reverse=True),
        "ship_logs": sorted(ship_logs, key=lambda item: item["performance_ema"]),
        "recommendations": recommendations,
    }


def _current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token,
            AdminSession.revoked == 0,
        ).first()
        if session and (session.expires_at is None or session.expires_at > datetime.utcnow()):
            admin = db.query(AdminUser).options(joinedload(AdminUser.privilege_group)).filter(
                AdminUser.id == session.admin_id
            ).first()
            if admin:
                from security import compute_department_site_permissions

                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require(admin: AdminUser, action: str) -> None:
    if not check_permission(admin, "monster_spawn", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


@game_router.get("/state")
def plugin_state(server: str = Query(...), db: Session = Depends(get_game_db)):
    server = _normalize_server(server)
    row = _get_or_create(db, server)
    row.last_seen_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _serialize(row)


@game_router.post("/telemetry")
def plugin_telemetry(payload: PluginTelemetry, db: Session = Depends(get_game_db)):
    server = _normalize_server(payload.server)
    row = _get_or_create(db, server)
    reported_revision = max(0, payload.applied_revision)

    row.applied_revision = reported_revision
    row.telemetry = payload.telemetry
    row.last_seen_at = datetime.utcnow()

    # An old plugin snapshot must not overwrite edits that are still waiting
    # to be applied. Once the desired revision is acknowledged, plugin state
    # becomes authoritative again (including runtime plan status changes).
    if reported_revision >= (row.desired_revision or 0):
        row.config = payload.config
        row.memory = payload.memory
        row.plans = payload.plans

    db.commit()
    db.refresh(row)
    return {"status": "ok", "desired_revision": row.desired_revision}


@admin_router.get("")
def admin_state(
    server: str = Query(...),
    admin: AdminUser = Depends(_current_admin),
    db: Session = Depends(get_game_db),
):
    _require(admin, "view")
    row = _get_or_create(db, _normalize_server(server))
    db.commit()
    db.refresh(row)
    return _serialize(row)


@admin_router.put("")
def admin_update(
    payload: AdminUpdate,
    server: str = Query(...),
    admin: AdminUser = Depends(_current_admin),
    db: Session = Depends(get_game_db),
):
    _require(admin, "edit")
    if payload.memory is not None:
        _require(admin, "manage_memory")

    row = _get_or_create(db, _normalize_server(server))
    changed = False
    if payload.config is not None:
        row.config = payload.config
        changed = True
    if payload.memory is not None:
        row.memory = payload.memory
        changed = True
    if payload.plans is not None:
        row.plans = payload.plans
        changed = True
    if not changed:
        raise HTTPException(status_code=400, detail="Нет изменений")

    row.desired_revision = (row.desired_revision or 0) + 1
    row.updated_by_admin_id = admin.id
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _serialize(row)


@admin_router.get("/analysis")
def admin_analysis(
    server: str = Query(...),
    admin: AdminUser = Depends(_current_admin),
    db: Session = Depends(get_game_db),
):
    """Return operator suggestions only; this endpoint never mutates controller state."""
    _require(admin, "view")
    row = _get_or_create(db, _normalize_server(server))
    db.commit()
    db.refresh(row)
    return _build_spawn_analysis(row)
