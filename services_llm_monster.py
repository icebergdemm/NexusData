"""LLM assistant for MonsterSpawnController (via the shared AI aggregator).

The site-side assistant is a CLIENT of the same AI aggregator that serves
player dossiers (AI_DOSSIER_URL + AI_DOSSIER_API_KEY, see
services_ai_client.py): every generation is queued on the single
AI_REQUEST_LOCK, so monster cycles never run concurrently with dossier or
health-check generations on the Qwen/KoboldCpp host. Per-consumer settings
(model name, sampling, timeout) stay in MONSTER_LLM_*; endpoint, auth and
TLS always come from the aggregator.

It reads the controller's live state (config, memory, telemetry posted by
the game plugin) and produces STRICT JSON decisions plus a human-readable
analysis for the operator:

  1. Config corrections — small bounded adjustments of a whitelist of
     difficulty parameters.
  2. Manual spawns — wave plans the plugin picks up through the existing
     revision system (source "llm").
  3. Analyzer text — crew efficiency summary + operator recommendations in
     plain Russian text (always returned, even in analyze mode).

Anti-hallucination design (do not weaken these guarantees):
  * The model never returns free-form config. It returns an array of
    operations, each validated against a whitelist of editable keys with
    hard numeric bounds; anything else is dropped.
  * Every numeric operation is clamped to a per-key max step (|delta|) and
    to a global [min, max] range relative to the CURRENT live value, so a
    hallucinated "set difficulty to 9999" can never be applied.
  * The response must be valid JSON (json.loads) and match the schema;
    malformed answers are retried once, then discarded with an error entry.
  * Spawns are only allowed for species that already exist in the wave
    memory or current config (no invented species names); unknown species
    are dropped before the plan reaches the plugin.
  * Every run is persisted (last_result) so the operator sees exactly what
    the model decided, what was applied and what was rejected.
"""

import json
import logging
import math
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import get_settings
from services_ai_client import AIClientError, chat_content, post_chat_completions

logger = logging.getLogger(__name__)

TASK_KIND = "monster_llm"  # task-log kind for the AI Centre journal

SETTINGS_KEY = "monster_spawn_llm_config_v1"

# ---------------------------------------------------------------------------
# Editable parameter registry — the ONLY keys the LLM may touch.
# Each entry: (min_value, max_value, max_abs_delta_per_run, value_type)
# ---------------------------------------------------------------------------

EDITABLE_FLOAT_KEYS: Dict[str, Tuple[float, float, float]] = {
    # key: (hard_min, hard_max, max |delta| per run)
    "max_short_term_difficulty": (1.0, 30.0, 3.0),
    "target_difficulty_per_minute": (0.1, 3.0, 0.4),
    "min_seconds_between_spawns": (15.0, 600.0, 90.0),
    "idle_spawn_after_seconds": (60.0, 900.0, 120.0),
    "intensity_threshold": (0.05, 0.9, 0.15),
    "max_wave_tracking_seconds": (120.0, 1200.0, 300.0),
    "danger_bias_strength": (0.0, 1.5, 0.3),
    "bypass_difficulty_escalation": (0.0, 1.0, 0.2),
    "struggle_delay_max_factor": (1.0, 4.0, 0.5),
    "pace_intercept_cooldown_seconds": (30.0, 900.0, 180.0),
    # Политика столкновения с волной и спавн-схемы.
    "unengaged_block_timeout_seconds": (15.0, 300.0, 30.0),
    "bypass_distance_meters": (100.0, 400.0, 50.0),
    "spawn_min_distance_meters": (50.0, 400.0, 50.0),
    # Стартовая задержка и адаптация к силе экипажа.
    "initial_grace_min_progress": (0.0, 0.5, 0.05),
    "initial_grace_min_round_seconds": (0.0, 3600.0, 300.0),
    "player_count_scale_per_extra": (0.0, 0.5, 0.1),
    "player_hp_scale_per_extra": (0.0, 0.3, 0.05),
    "player_power_exponent": (0.3, 1.5, 0.1),
    "player_scale_max": (1.0, 5.0, 0.5),
}

EDITABLE_INT_KEYS: Dict[str, Tuple[int, int, int]] = {
    "pace_intercept_max_per_round": (1, 10, 3),
    "learned_waves_max": (5, 80, 20),
    # 0 = без ограничений по одновременным волнам.
    "max_simultaneous_waves": (0, 4, 1),
    "balance_baseline_players": (1, 16, 2),
    "chain_max_depth": (0, 5, 1),
}

EDITABLE_BOOL_KEYS = {
    "single_wave_mode",
    "pace_intercept_enabled",
    "intensity_gate_enabled",
    "learned_waves_enabled",
    "pace_intercept_prefer_learned",
    "spawn_scheme_enabled",
    "initial_grace_enabled",
    "initial_grace_require_both",
}

VANILLA_FLOAT_KEYS: Dict[str, Tuple[float, float, float]] = {
    "vanilla_events.amount_multiplier": (0.1, 3.0, 0.3),
    "vanilla_events.difficulty_multiplier": (0.3, 2.5, 0.3),
    "vanilla_events.hold_seconds": (0.0, 120.0, 30.0),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _get_path(config: Dict[str, Any], dotted: str) -> Tuple[bool, Any]:
    parts = dotted.split(".")
    node: Any = config
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _set_path(config: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = config
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


# ---------------------------------------------------------------------------
# Per-server runtime config (AppSetting JSON)
# ---------------------------------------------------------------------------

DEFAULT_RUNTIME = {
    "enabled": False,
    "auto_apply": False,       # false = model decisions wait for operator
    "auto_interval_minutes": 0,
    "last_run_at": None,
    "history": [],             # short breadcrumbs: {at, mode, applied, rejected, summary<=150}
    # Async manual runs (POST /llm/run returns 202 immediately, a worker
    # executes, the UI polls runtime): at most one pending + one active.
    # Full runs (text, counts, validation details) live in the durable
    # monster_spawn_llm_runs table — the AppSetting row is String(2000) and
    # used to silently truncate last_result, so operators never saw results.
    "pending_run": None,       # {id, mode, instruction<=300, requested_at, requested_by, attempts}
    "active_run": None,        # pending + {started_at, lease_expires_at}
}


def get_llm_runtime(db, server: str) -> Dict[str, Any]:
    """db: SQLAlchemy session bound to the main DB (AppSetting lives there)."""
    from models import AppSetting

    row = db.query(AppSetting).filter(AppSetting.key == SETTINGS_KEY).first()
    data: Dict[str, Any] = {}
    if row is not None and row.value:
        try:
            data = json.loads(row.value)
        except (ValueError, TypeError):
            data = {}
    per_server = data.get(server) if isinstance(data.get(server), dict) else {}
    runtime = {**DEFAULT_RUNTIME, **per_server}
    return runtime


def save_llm_runtime(db, server: str, runtime: Dict[str, Any]) -> None:
    from models import AppSetting

    row = db.query(AppSetting).filter(AppSetting.key == SETTINGS_KEY).first()
    data: Dict[str, Any] = {}
    if row is not None and row.value:
        try:
            data = json.loads(row.value)
        except (ValueError, TypeError):
            data = {}
    if row is None:
        row = AppSetting(key=SETTINGS_KEY, value="{}")
        db.add(row)

    # Legacy keys no longer live here: results are in monster_spawn_llm_runs
    # (the old String(2000) truncation used to wipe last_result entirely,
    # which is why the assistant "never showed anything").
    runtime.pop("last_result", None)

    data[server] = runtime

    # AppSetting.value is String(2000): already-bounded history and run dicts
    # normally fit; trim defensively rather than truncating mid-JSON.
    encoded = json.dumps(data, ensure_ascii=False)
    while len(encoded) > 1900 and runtime.get("history"):
        runtime["history"] = runtime["history"][1:]
        encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded) > 1900:
        runtime["history"] = []
        encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded) > 2000:
        # Keep control keys — remaining payload must be tiny; never hand the
        # runtime a truncated JSON blob.
        runtime["history"] = []
        encoded = json.dumps(data, ensure_ascii=False)

    row.value = encoded[:2000]
    db.commit()


# ---------------------------------------------------------------------------
# LLM client — via the shared AI aggregator (queues on AI_REQUEST_LOCK)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    pass


def _chat_completions(messages: List[Dict[str, str]], model: str, timeout: float,
                      temperature: float, top_p: float) -> str:
    """One generation through the shared AI aggregator as a client.

    Endpoint/auth/TLS/queueing are owned by services_ai_client; only the
    model name and sampling params are monster-specific."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": 2048,
        # Thinking mode: disable for deterministic JSON output.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        data, _elapsed_ms = post_chat_completions(payload, timeout=timeout)
        return chat_content(data)
    except AIClientError as exc:
        raise LLMError(str(exc)) from exc


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(content: str) -> Dict[str, Any]:
    """Strip thinking tags and pull out the outermost JSON object."""
    cleaned = _THINK_RE.sub("", content or "").strip()
    match = _JSON_RE.search(cleaned)
    if not match:
        raise LLMError("В ответе модели нет JSON-объекта")
    try:
        parsed = json.loads(match.group(0))
    except ValueError as exc:
        raise LLMError(f"Ответ модели не является валидным JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("Модель вернула JSON, но не объект")
    return parsed


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Ты — ассистент оператора игрового сервера Barotrauma, управляющий адаптивным \
спавнером монстров MonsterSpawnController. Ты получаешь состояние контроллера в JSON: глобальную \
конфигурацию, накопленную память (эффективность экипажей по волнам и кораблям), живую телеметрию \
раунда (активные волны, темп корабля, изученные ванильные волны, оценки угрозы видов).

Твоя задача — вернуть СТРОГО ОДИН JSON-объект без какого-либо текста вне JSON, со схемой:
{
  "situation": "краткое описание обстановки по-русски, 1-2 предложения",
  "crew_summary": "текстовая сводка эффективности экипажа по-русски, 2-4 предложения: как экипаж справляется, потери, темп, проблемные волны",
  "recommendations": [
    {"title": "короткий заголовок совета по-русски",
     "detail": "текстовое пояснение для оператора по-русски, 1-3 предложения"}
  ],
  "config_changes": [
    {"key": "<имя параметра из списка разрешённых>", "new_value": <число или true/false>,
     "reason": "краткая причина по-русски"}
  ],
  "spawn_requests": [
    {"species": "<вид монстра>", "count": <1-6>, "hp_multiplier": <0.5-2.0>,
     "position_hint": "ahead|around|below", "reason": "краткая причина по-русски"}
  ],
  "confidence": <0.0-1.0>
}

Жёсткие правила:
1. Разрешены ТОЛЬКО параметры из списка editable_keys в данных пользователя. Другие ключи запрещены.
2. Меняй параметры плавно: новый баланс должен оставаться в min/max, указанных для ключа. \
Резкие скачки запрещены. Если данных мало (мало спавнов/раундов) — не меняй ничего.
3. spawn_requests используй только если обстановка явно требует вмешательства: экипаж бездействует, \
корабль продвигается слишком быстро или оператор дал прямую команду. Виды бери только из \
known_species. count не больше 6, hp_multiplier в пределах 0.5-2.0.
4. crew_summary и recommendations возвращай ВСЕГДА, даже в режиме анализа без изменений: это \
текстовый разбор для оператора. Не выдумывай цифры — опирайся только на memory_brief, telemetry \
и dashboard из JSON. Если данных мало — так и напиши, дай не более 3 осторожных рекомендаций.
5. Никогда не выдумывай данные, которых нет в JSON. Если состояние неполное — верни пустые списки \
и опиши, чего не хватает, в "situation".
6. Ничего кроме JSON. Никакого markdown, никаких комментариев."""


def _build_user_prompt(state: dict, instruction: Optional[str], known_species: List[str]) -> str:
    settings = get_settings()
    editable = {**EDITABLE_FLOAT_KEYS, **VANILLA_FLOAT_KEYS}
    registry = {
        key: {"min": bounds[0], "max": bounds[1], "max_step": bounds[2], "type": "float"}
        for key, bounds in editable.items()
    }
    registry.update({
        key: {"min": bounds[0], "max": bounds[1], "max_step": bounds[2], "type": "int"}
        for key, bounds in EDITABLE_INT_KEYS.items()
    })
    registry.update({key: {"type": "bool"} for key in EDITABLE_BOOL_KEYS})

    config = state.get("config") or {}
    global_config = config.get("global") or {}
    current_values = {}
    for key in registry:
        found, value = _get_path(config, f"global.{key}")
        if found:
            current_values[key] = value

    telemetry = state.get("telemetry") or {}
    dashboard = state.get("dashboard") or {}
    memory = state.get("memory") or {}

    brief_memory = {
        "global_performance_ema": memory.get("global_performance_ema"),
        "rounds_recorded": memory.get("rounds_recorded"),
        "waves": {
            name: {
                "times_spawned": entry.get("times_spawned"),
                "times_bypassed": entry.get("times_bypassed"),
                "avg_performance": (
                    round(sum(entry.get("recent_performance", [])) / len(entry["recent_performance"]), 3)
                    if isinstance(entry.get("recent_performance"), list) and entry["recent_performance"]
                    else None
                ),
                "avg_crew_deaths": (
                    round(float(entry.get("total_crew_deaths", 0)) / float(entry.get("times_spawned") or 1), 2)
                    if isinstance(entry.get("times_spawned"), (int, float)) and entry.get("times_spawned")
                    else None
                ),
                "avg_sub_health_loss": (
                    round(float(entry.get("total_sub_health_loss", 0)) / float(entry.get("times_spawned") or 1), 2)
                    if isinstance(entry.get("times_spawned"), (int, float)) and entry.get("times_spawned")
                    else None
                ),
            }
            for name, entry in (memory.get("waves") or {}).items()
            if isinstance(entry, dict) and (entry.get("times_spawned") or 0) > 0
        },
        "ships": [
            {
                "name": entry.get("name") or name,
                "rounds_played": entry.get("rounds_played"),
                "times_spawned": entry.get("times_spawned"),
                "performance_ema": entry.get("performance_ema"),
            }
            for name, entry in (memory.get("submarines") or {}).items()
            if isinstance(entry, dict)
        ][:12],
    }

    payload = {
        "editable_keys": registry,
        "current_values": current_values,
        "known_species": sorted(set(known_species)),
        "dashboard": dashboard,
        "telemetry": telemetry,
        "memory_brief": brief_memory,
        "auto_interval_minutes": settings.monster_llm_auto_interval_minutes,
    }
    if instruction:
        payload["operator_instruction"] = instruction

    return "Состояние контроллера:\n" + json.dumps(payload, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# Decision validation / application
# ---------------------------------------------------------------------------

def _validate_config_changes(state: dict, changes: Any) -> Tuple[List[dict], List[str]]:
    applied: List[dict] = []
    rejected: List[str] = []
    if not isinstance(changes, list):
        rejected.append("config_changes: не список")
        return applied, rejected

    config = state.get("config") or {}
    for change in changes[:12]:
        if not isinstance(change, dict):
            rejected.append(f"операция не объект: {change!r}")
            continue
        key = change.get("key")
        reason = str(change.get("reason") or "")[:200]
        registry: Dict[str, Tuple[Any, Any, Any]] = {
            **EDITABLE_FLOAT_KEYS, **VANILLA_FLOAT_KEYS,
        }
        if key in EDITABLE_INT_KEYS:
            registry[key] = EDITABLE_INT_KEYS[key]
        if key not in registry and key not in EDITABLE_BOOL_KEYS:
            rejected.append(f"{key}: ключ не входит в whitelist")
            continue

        if key in EDITABLE_BOOL_KEYS:
            value = change.get("new_value")
            if not isinstance(value, bool):
                rejected.append(f"{key}: new_value не bool")
                continue
            _set_path(config, f"global.{key}", value)
            applied.append({"key": key, "new_value": value, "reason": reason})
            continue

        low, high, max_step = registry[key]
        found, current = _get_path(config, f"global.{key}")
        if not found or not isinstance(current, (int, float)):
            rejected.append(f"{key}: текущее значение не найдено")
            continue
        new_value = change.get("new_value")
        if isinstance(new_value, bool) or not isinstance(new_value, (int, float)) \
                or isinstance(new_value, float) and not math.isfinite(new_value):
            rejected.append(f"{key}: new_value не число")
            continue

        new_value = float(new_value)
        clamped = _clamp(new_value, low, high)
        if abs(clamped - float(current)) > max_step:
            direction = 1.0 if clamped > current else -1.0
            clamped = float(current) + direction * max_step
            clamped = _clamp(clamped, low, high)
            rejected.append(f"{key}: шаг {abs(new_value - float(current)):.2f} превышает {max_step}, "
                            f"ужесточено до {clamped:.2f}")
        if key in EDITABLE_INT_KEYS:
            new_typed = int(round(clamped))
        elif key in EDITABLE_FLOAT_KEYS:
            new_typed = round(clamped, 4)
        else:
            new_typed = round(clamped, 4)
        _set_path(config, f"global.{key}", new_typed)
        applied.append({"key": key, "new_value": new_typed, "old_value": current, "reason": reason})
    return applied, rejected


def _validate_spawn_requests(state: dict, requests: Any) -> Tuple[List[dict], List[str]]:
    """Validate spawn requests; wave plans are created in the coordinate space
    relative to the submarine by the plugin, so we only need species + counts.
    Plans use spawn_type hints; the plugin computes the actual position."""
    applied: List[dict] = []
    rejected: List[str] = []
    if not isinstance(requests, list):
        rejected.append("spawn_requests: не список")
        return applied, rejected

    memory = state.get("memory") or {}
    known = set()
    for entry in (memory.get("species_threats") or {}):
        known.add(str(entry))
    for entry in (memory.get("species_mobility") or {}):
        known.add(str(entry))
    for wave in ((state.get("config") or {}).get("waves") or []):
        for monster in (wave.get("monsters") or []):
            if isinstance(monster, dict) and monster.get("species"):
                known.add(str(monster["species"]).lower())

    for request in requests[:6]:
        if not isinstance(request, dict):
            rejected.append(f"spawn запрос не объект: {request!r}")
            continue
        species = str(request.get("species") or "").strip().lower()
        if not species:
            rejected.append("spawn запрос без species")
            continue
        if species not in known:
            rejected.append(f"{species}: вид отсутствует в known_species")
            continue
        try:
            count = int(request.get("count", 1))
        except (TypeError, ValueError):
            rejected.append(f"{species}: count не число")
            continue
        count = _clamp(count, 1, 6)
        try:
            hp = float(request.get("hp_multiplier", 1.0))
        except (TypeError, ValueError):
            hp = 1.0
        hp = _clamp(hp, 0.5, 2.0)
        position = str(request.get("position_hint") or "around").lower()
        if position not in ("ahead", "around", "below"):
            position = "around"
        applied.append({
            "species": species,
            "count": count,
            "hp_multiplier": hp,
            "position_hint": position,
            "reason": str(request.get("reason") or "")[:200],
        })
    return applied, rejected


def _validate_recommendations(decision: Dict[str, Any]) -> Tuple[str, List[dict]]:
    """Analyzer text: crew efficiency summary + operator recommendations.

    Purely informational — never applied to config/plans, only shown in UI.
    Truncated defensively so a verbose model cannot overflow AppSetting."""
    crew_summary = str(decision.get("crew_summary") or "").strip()[:1000]
    raw = decision.get("recommendations")
    recommendations: List[dict] = []
    if isinstance(raw, list):
        for entry in raw[:5]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()[:120]
            detail = str(entry.get("detail") or "").strip()[:500]
            if not title and not detail:
                continue
            recommendations.append({"title": title or "Рекомендация", "detail": detail})
    return crew_summary, recommendations


def _spawn_requests_to_plans(state: dict, requests: List[dict], run_at: datetime) -> List[dict]:
    """Build plugin SpawnPlan entries. X/Y are filled by the plugin-relative
    hint system: the plugin spawns 'planned' plans at the exact coordinates,
    so we put the sub's approximate ahead/around offset in world units. The
    plugin accepts any plan status=planned and spawns it at X/Y; for hint
    positions we use the submarine's live world position from telemetry."""
    telemetry = state.get("telemetry") or {}
    sub_x = _number(telemetry.get("sub_x"))
    sub_y = _number(telemetry.get("sub_y"))

    offsets = {
        "ahead": (6000.0, 0.0),
        "around": (3500.0, 800.0),
        "below": (0.0, -2500.0),
    }
    plans = []
    for index, request in enumerate(requests):
        dx, dy = offsets.get(request["position_hint"], offsets["around"])
        wave_name = f"llm:{request['species']}:{run_at.strftime('%H%M%S')}"
        plan = {
            "id": f"llm-{run_at.strftime('%Y%m%d%H%M%S')}-{index}",
            "wave_name": wave_name,
            "name": wave_name,
            "source": "llm",
            "status": "planned",
            "x": sub_x + dx,
            "y": sub_y + dy,
            "note": request["reason"],
            "triggered_at": None,
            "chance": 1.0,
            "commonness": 1.0,
            "spawn_type": request["position_hint"],
            "delay_between_spawns": 0.2,
            "scatter": 800,
            "offset": 0,
            "spawn_distance": 0,
            "spawn_point_tag": "",
            "reset_time": 0,
            "play_dead_probability": -1,
            "difficulty": -1,
            "min_progress": 0,
            "max_progress": 0,
            "min_round_time": 0,
            "min_players": 0,
            "max_sub_health": 0,
            "max_crew_health": 0,
            "monsters": [{
                "species": request["species"],
                "count": request["count"],
                "amount_min": 0,
                "amount_max": 0,
                "max_amount_per_level": 0,
                "scale_with_players": 0,
                "hp_multiplier": request["hp_multiplier"],
                "hp_scale_with_progress": 0,
                "mobility": 1,
            }],
        }
        plans.append(plan)
    return plans


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _record_history(runtime: dict, entry: dict) -> None:
    entry["summary"] = str(entry.get("summary") or "")[:150]
    history = runtime.setdefault("history", [])
    history.append(entry)
    del history[:-8]


# ---------------------------------------------------------------------------
# Durable run records (monster_spawn_llm_runs in the main DB)
# ---------------------------------------------------------------------------

def _create_run_record(main_db, server: str, mode: str, instruction: Optional[str],
                       source: str, run_id: str, admin_id: Optional[int]):
    from models import MonsterSpawnLLMRun

    row = MonsterSpawnLLMRun(
        server=server,
        run_id=run_id,
        mode=mode,
        source=source,
        instruction=(instruction or "")[:2000] or None,
        requested_by_admin_id=admin_id,
        requested_at=datetime.utcnow(),
        status="queued",
    )
    main_db.add(row)
    main_db.commit()
    return row


def _find_run_record(main_db, server: str, run_id: str):
    from models import MonsterSpawnLLMRun

    if not run_id:
        return None
    return main_db.query(MonsterSpawnLLMRun).filter(
        MonsterSpawnLLMRun.server == server,
        MonsterSpawnLLMRun.run_id == run_id,
    ).first()


def _finish_run_record(main_db, row, result: dict, error: Optional[str]) -> None:
    from models import MonsterSpawnLLMRun

    if row is None:
        return
    try:
        now = datetime.utcnow()
        row.finished_at = now
        if error:
            row.status = "failed"
            row.error_message = error[:2000]
        else:
            row.status = "succeeded"
            try:
                row.confidence = float(result.get("confidence") or 0.0)
            except (TypeError, ValueError):
                row.confidence = 0.0
            row.duration_ms = int(result.get("duration_ms") or 0)
            row.applied_count = len(result.get("applied_changes") or [])
            row.rejected_count = len(result.get("rejected") or [])
            row.spawned_count = len(result.get("spawn_plans") or [])
            row.result_json = result
        main_db.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            main_db.rollback()
        except Exception:
            pass
        logger.warning("Failed to persist monster LLM run record: %s", exc)


def _task(kind: str, ref: str, stage: str, message: str = "", level: str = "info") -> None:
    from services_ai_tasklog import ai_task_log

    from database import SessionLocal
    db_session = next(SessionLocal())
    try:
        ai_task_log(db_session, kind, ref, stage, message, level)
    finally:
        db_session.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_llm_cycle(db, server: str, state: dict, mode: str = "analyze",
                  instruction: Optional[str] = None, task_ref: Optional[str] = None) -> Dict[str, Any]:
    """mode: analyze (read-only analyzer: situation + crew summary +
    recommendations, no config writes), apply (config changes applied by
    bumping desired_revision), spawn (also add spawn plans)."""
    ref = task_ref or server
    settings = get_settings()
    if not (settings.ai_dossier_url or "").strip() or not settings.ai_dossier_api_key:
        _task(TASK_KIND, ref, "failed", "AI-агрегатор не настроен (AI_DOSSIER_URL/AI_DOSSIER_API_KEY)", "error")
        raise LLMError("AI-агрегатор не настроен: задайте AI_DOSSIER_URL и AI_DOSSIER_API_KEY")

    runtime = get_llm_runtime(db, server)
    known_species = _collect_known_species(state)
    user_prompt = _build_user_prompt(state, instruction, known_species)
    _task(TASK_KIND, ref, "state_built",
          f"известных видов: {len(known_species)}, волн в памяти: {len((state.get('memory') or {}).get('waves') or {})}; промпт {len(user_prompt)} симв.")

    started = time.monotonic()
    _task(TASK_KIND, ref, "request_sent", f"отправка запроса в агрегатор (модель {settings.monster_llm_model}, режим {mode})")
    content = _chat_completions(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=settings.monster_llm_model,
        timeout=settings.monster_llm_timeout_seconds,
        temperature=settings.monster_llm_temperature,
        top_p=settings.monster_llm_top_p,
    )
    _task(TASK_KIND, ref, "response_received", f"ответ получен за {int((time.monotonic() - started) * 1000)} мс, {len(content)} симв.")

    try:
        decision = _extract_json(content)
        _task(TASK_KIND, ref, "json_validated", "JSON-схема ответа модели корректна")
    except LLMError:
        # One retry with a strict reminder.
        _task(TASK_KIND, ref, "retry", "ответ не был валидным JSON — повторная генерация с строгим напоминанием", "warning")
        content = _chat_completions(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": content[:2000]},
                {"role": "user", "content": "Предыдущий ответ не был валидным JSON. "
                                            "Верни ТОЛЬКО JSON-объект по схеме."},
            ],
            model=settings.monster_llm_model,
            timeout=settings.monster_llm_timeout_seconds,
            temperature=0.0,
            top_p=settings.monster_llm_top_p,
        )
        decision = _extract_json(content)

    duration_ms = int((time.monotonic() - started) * 1000)
    situation = str(decision.get("situation") or "")[:500]
    confidence = decision.get("confidence", 0.0)
    try:
        confidence = _clamp(float(confidence), 0.0, 1.0)
    except (TypeError, ValueError):
        confidence = 0.0

    config_applied, config_rejected = _validate_config_changes(state, decision.get("config_changes"))
    spawn_applied, spawn_rejected = _validate_spawn_requests(state, decision.get("spawn_requests"))
    crew_summary, recommendations = _validate_recommendations(decision)

    # In analyze mode nothing is written back to config/plans, but the
    # analyzer text (situation + crew summary + recommendations) is still
    # returned and persisted so the operator can read it in the UI.
    applied_changes: List[dict] = config_applied if mode in ("apply", "spawn") else []
    spawn_plans: List[dict] = []
    if mode == "spawn" and spawn_applied:
        spawn_plans = _spawn_requests_to_plans(state, spawn_applied, datetime.utcnow())

    result = {
        "at": datetime.utcnow().isoformat(),
        "mode": mode,
        "duration_ms": duration_ms,
        "situation": situation,
        "crew_summary": crew_summary,
        "recommendations": recommendations,
        "confidence": round(confidence, 2),
        "applied_changes": applied_changes,
        "rejected": config_rejected + spawn_rejected,
        "spawn_requests": spawn_applied if mode == "spawn" else [],
        "spawn_plans": spawn_plans,
        "plan_ids": [plan["id"] for plan in spawn_plans],
        "raw_decision_keys": list(decision.keys()),
    }
    _task(TASK_KIND, ref, "validated",
          f"валидация: применено {len(applied_changes)}, отклонено {len(result['rejected'])}, "
          f"спавн-волн {len(spawn_plans)} (уверенность {result['confidence']:.0%})")

    _record_history(runtime, {
        "at": result["at"],
        "mode": mode,
        "summary": situation,
        "applied": len(applied_changes),
        "rejected": len(result["rejected"]),
        "confidence": result["confidence"],
    })
    runtime["last_run_at"] = result["at"]
    save_llm_runtime(db, server, runtime)
    _task(TASK_KIND, ref, "completed", f"цикл завершён: {situation[:150]}")

    return result


def _collect_known_species(state: dict) -> List[str]:
    known = set()
    memory = state.get("memory") or {}
    for key in ("species_threats", "species_mobility"):
        section = memory.get(key)
        if isinstance(section, dict):
            known.update(str(name) for name in section.keys())
    for wave in ((state.get("config") or {}).get("waves") or []):
        if not isinstance(wave, dict):
            continue
        for monster in (wave.get("monsters") or []):
            if isinstance(monster, dict) and monster.get("species"):
                known.add(str(monster["species"]).lower())
    known.discard("")
    return sorted(known)


def check_auto_cycle_due(runtime: dict) -> bool:
    """True, if an auto-cycle run is due (interval > 0 and elapsed)."""
    interval = runtime.get("auto_interval_minutes") or 0
    if interval <= 0 or not runtime.get("enabled"):
        return False
    last = runtime.get("last_run_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last))
    except (TypeError, ValueError):
        return True
    elapsed_minutes = (datetime.utcnow() - last_dt).total_seconds() / 60.0
    return elapsed_minutes >= interval


def list_llm_runs(db, server: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Durable run records for the admin UI (newest first)."""
    from models import MonsterSpawnLLMRun

    server = (server or "").strip().upper()
    query = db.query(MonsterSpawnLLMRun)
    if server:
        query = query.filter(MonsterSpawnLLMRun.server == server)
    rows = query.order_by(MonsterSpawnLLMRun.id.desc()).limit(min(max(limit, 1), 100)).all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "server": row.server,
            "mode": row.mode,
            "source": row.source,
            "status": row.status,
            "instruction": (row.instruction or "")[:300],
            "requested_by_admin_id": row.requested_by_admin_id,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "duration_ms": row.duration_ms,
            "confidence": row.confidence,
            "applied_count": row.applied_count,
            "rejected_count": row.rejected_count,
            "spawned_count": row.spawned_count,
            "error_message": (row.error_message or "")[:500],
            "result": row.result_json,
        }
        for row in rows
    ]


def get_llm_deployment_info() -> Dict[str, Any]:
    """Non-secret info for the admin UI (transport = shared AI aggregator)."""
    settings = get_settings()
    return {
        "base_url": settings.ai_dossier_url,
        "model": settings.monster_llm_model,
        "temperature": settings.monster_llm_temperature,
        "top_p": settings.monster_llm_top_p,
        "timeout_seconds": settings.monster_llm_timeout_seconds,
        "api_key_set": bool(settings.ai_dossier_api_key),
        "has_base_url": bool((settings.ai_dossier_url or "").strip()),
        "via": "ai-aggregator",
    }


# ---------------------------------------------------------------------------
# Async manual runs: POST /llm/run only queues (HTTP 202), a scheduler worker
# executes, the UI polls runtime. A synchronous cycle (queue wait on the
# shared aggregator lock + up to two model timeouts) easily outlives the
# 180 s proxy read timeout, which is exactly the stuck "Модель думает…" +
# HTTP 504 operators saw. Never block an HTTP request on a generation.
# ---------------------------------------------------------------------------

class QueueBusyError(LLMError):
    """A manual LLM run is already queued or running for this server."""
    pass


MAX_RUN_ATTEMPTS = 3


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _active_run_live(active: Any, now: datetime) -> bool:
    """True if a claimed run still holds a valid lease (worker alive)."""
    if not isinstance(active, dict):
        return False
    lease = _parse_iso(active.get("lease_expires_at"))
    return lease is not None and lease > now


def _run_lease_seconds() -> float:
    """An active run holds the aggregator lock for at most two model
    timeouts; the lease covers that plus queue/DB overhead."""
    return float(get_settings().monster_llm_timeout_seconds) * 2.0 + 120.0


def _reap_stale_active(runtime: dict, now: datetime, main_db=None, server: str = "") -> bool:
    """Reclaim an active run whose worker died (lease expired).

    Returns True if the runtime changed. A run that already burned all
    attempts is dropped with a history entry, otherwise it goes back to
    pending so the next worker tick retries it."""
    active = runtime.get("active_run")
    if not isinstance(active, dict):
        runtime.pop("active_run", None)
        return False
    if _active_run_live(active, now):
        return False
    attempts = active.get("attempts", 0)
    try:
        attempts = int(attempts)
    except (TypeError, ValueError):
        attempts = 0
    if attempts >= MAX_RUN_ATTEMPTS:
        _record_history(runtime, {
            "at": datetime.utcnow().isoformat(),
            "mode": str(active.get("mode") or "?"),
            "summary": "Запуск прерван: воркер не завершил работу",
            "applied": 0,
            "rejected": 0,
            "confidence": 0.0,
        })
        if main_db is not None and server:
            run_row = _find_run_record(main_db, server, str(active.get("id") or ""))
            _finish_run_record(main_db, run_row, {}, "Запуск прерван: воркер не завершил работу, все попытки исчерпаны")
        runtime.pop("active_run", None)
        return True
    pending = dict(active)
    pending.pop("started_at", None)
    pending.pop("lease_expires_at", None)
    runtime["pending_run"] = pending
    runtime.pop("active_run", None)
    return True


def queue_llm_run(db, server: str, mode: str = "analyze",
                  instruction: Optional[str] = None,
                  admin_id: Optional[int] = None) -> Dict[str, Any]:
    """Enqueue one manual LLM run and return immediately (HTTP 202).

    Raises QueueBusyError if a run is already queued/running, LLMError if
    the mode is unknown or the aggregator is not configured (fail fast
    instead of queueing work that can never run)."""
    if mode not in ("analyze", "apply", "spawn"):
        raise LLMError(f"Неизвестный режим LLM-цикла: {mode}")
    settings = get_settings()
    if not (settings.ai_dossier_url or "").strip() or not settings.ai_dossier_api_key:
        raise LLMError("AI-агрегатор не настроен: задайте AI_DOSSIER_URL и AI_DOSSIER_API_KEY")

    runtime = get_llm_runtime(db, server)
    now = datetime.utcnow()
    if _reap_stale_active(runtime, now, db, server):
        save_llm_runtime(db, server, runtime)
        runtime = get_llm_runtime(db, server)
    if isinstance(runtime.get("pending_run"), dict) or _active_run_live(runtime.get("active_run"), now):
        raise QueueBusyError("LLM-цикл уже стоит в очереди или выполняется — дождитесь результата")

    run_id = uuid.uuid4().hex[:12]
    full_instruction = str(instruction or "")[:2000]
    _create_run_record(db, server, mode, full_instruction, "manual", run_id, admin_id)

    # pending_run lives in the String(2000) AppSetting row — keep it short;
    # the full instruction is durable in the run record.
    run = {
        "id": run_id,
        "mode": mode,
        "instruction": full_instruction[:300],
        "requested_at": now.isoformat(),
        "requested_by": admin_id,
        "attempts": 0,
    }
    runtime["pending_run"] = run
    save_llm_runtime(db, server, runtime)
    return run


def _apply_llm_result(game_db, row, state: dict, result: dict, admin_id: Optional[int]) -> bool:
    """Write validated changes through the revision channel. Returns True if
    the desired revision was bumped."""
    changed = False
    if result.get("applied_changes"):
        row.config = state["config"]
        changed = True
    if result.get("spawn_plans"):
        plans = row.plans if isinstance(row.plans, list) else []
        plans.extend(result["spawn_plans"])
        row.plans = plans
        changed = True
    if changed:
        row.desired_revision = (row.desired_revision or 0) + 1
        if admin_id is not None:
            row.updated_by_admin_id = admin_id
        row.updated_at = datetime.utcnow()
    return changed


def _record_run_failure(main_db, server: str, active: dict, error: str) -> None:
    runtime = get_llm_runtime(main_db, server)
    runtime["active_run"] = None
    _record_history(runtime, {
        "at": datetime.utcnow().isoformat(),
        "mode": str(active.get("mode") or "?"),
        "summary": f"Ошибка: {error}"[:150],
        "applied": 0,
        "rejected": 0,
        "confidence": 0.0,
    })
    save_llm_runtime(main_db, server, runtime)
    run_row = _find_run_record(main_db, server, str(active.get("id") or ""))
    _finish_run_record(main_db, run_row, {}, error)
    _task(TASK_KIND, f"{server}:{active.get('id')}", "failed", f"Ошибка цикла: {error}"[:300], "error")


def _process_server_llm_queue(main_db, game_db, server: str, row) -> Optional[Dict[str, Any]]:
    """Claim and execute at most one queued manual run for a server."""
    runtime = get_llm_runtime(main_db, server)
    now = datetime.utcnow()
    if _reap_stale_active(runtime, now, main_db, server):
        save_llm_runtime(main_db, server, runtime)
    if _active_run_live(runtime.get("active_run"), now):
        return None
    pending = runtime.get("pending_run")
    if not isinstance(pending, dict):
        return None

    active = dict(pending)
    try:
        active["attempts"] = int(pending.get("attempts", 0)) + 1
    except (TypeError, ValueError):
        active["attempts"] = 1
    active["started_at"] = now.isoformat()
    active["lease_expires_at"] = (now + timedelta(seconds=_run_lease_seconds())).isoformat()
    runtime["active_run"] = active
    runtime["pending_run"] = None
    save_llm_runtime(main_db, server, runtime)

    run_id = str(active.get("id") or "")
    task_ref = f"{server}:{run_id}"
    _task(TASK_KIND, task_ref, "claimed", f"воркер взял задачу (режим {active.get('mode')}, попытка {active['attempts']})")
    run_row = _find_run_record(main_db, server, run_id)
    if run_row is not None:
        run_row.status = "running"
        run_row.started_at = now
        main_db.commit()

    game_db.commit()
    game_db.refresh(row)
    state = {
        "config": row.config if isinstance(row.config, dict) else {},
        "memory": row.memory if isinstance(row.memory, dict) else {},
        "telemetry": row.telemetry if isinstance(row.telemetry, dict) else {},
        "dashboard": {},
    }
    mode = active.get("mode") or "analyze"
    instruction = active.get("instruction") or None
    try:
        result = run_llm_cycle(main_db, server, state, mode=mode, instruction=instruction, task_ref=task_ref)
    except LLMError as exc:
        _record_run_failure(main_db, server, active, str(exc))
        return {"server": server, "status": "error", "run_id": active.get("id"), "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM manual run failed for %s: %s", server, exc)
        _record_run_failure(main_db, server, active, f"Ошибка LLM-цикла: {exc}")
        return {"server": server, "status": "error", "run_id": active.get("id"), "error": str(exc)}

    admin_id = active.get("requested_by")
    _apply_llm_result(game_db, row, state, result, admin_id)
    game_db.commit()
    _task(TASK_KIND, task_ref, "applied",
          f"изменения записаны (desired_revision {row.desired_revision}); применено {len(result.get('applied_changes', []))}, спавн-волн {len(result.get('spawn_plans', []))}")

    # run_llm_cycle already persisted history; persist the result and clear
    # the active lease.
    _finish_run_record(main_db, run_row, result, None)
    runtime = get_llm_runtime(main_db, server)
    runtime["active_run"] = None
    save_llm_runtime(main_db, server, runtime)
    return {"server": server, "status": "ok", "run_id": active.get("id"),
            "applied": len(result.get("applied_changes", [])),
            "spawned": len(result.get("spawn_plans", []))}


def _pending_servers_from_setting(value: Optional[str]) -> set[str]:
    """Return normalized server keys with a durable pending run."""
    if not value:
        return set()
    try:
        stored = json.loads(value)
    except (TypeError, ValueError):
        return set()
    if not isinstance(stored, dict):
        return set()
    return {
        str(server).strip().upper()
        for server, runtime in stored.items()
        if isinstance(runtime, dict) and isinstance(runtime.get("pending_run"), dict)
    }


def process_pending_llm_runs() -> List[Dict[str, Any]]:
    """Worker entry: execute queued manual runs, one per server. Never raises.

    The queue is stored in the main DB while controller state is stored in the
    game DB. Do not use the latter as the queue's index: a server can have a
    queued run before its controller snapshot exists (or after it was removed).
    """
    from database import GameSessionLocal, SessionLocal
    from models import AppSetting, MonsterSpawnControl

    results: List[Dict[str, Any]] = []
    main_db = next(SessionLocal())
    try:
        # AppSetting contains one JSON object keyed by server. Find queued
        # servers there first so a missing game-state row cannot strand a run.
        setting = main_db.query(AppSetting).filter(AppSetting.key == SETTINGS_KEY).first()
        queued_servers = _pending_servers_from_setting(setting.value if setting is not None else None)

        game_db = next(GameSessionLocal())
        try:
            rows = game_db.query(MonsterSpawnControl).all()
            rows_by_server = {
                (row.server or "").strip().upper(): row
                for row in rows
                if (row.server or "").strip()
            }
            servers = set(rows_by_server) | queued_servers
            for server in servers:
                if not server:
                    continue
                row = rows_by_server.get(server)
                if row is None:
                    # The admin UI normally creates this row on first load,
                    # but the worker must also handle a queued run submitted
                    # during a controller/plugin restart.
                    row = MonsterSpawnControl(server=server)
                    game_db.add(row)
                    game_db.flush()
                try:
                    outcome = _process_server_llm_queue(main_db, game_db, server, row)
                    if outcome is not None:
                        results.append(outcome)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("LLM manual run failed for %s: %s", server, exc)
                    results.append({"server": server, "status": "error", "error": str(exc)})
        finally:
            game_db.close()
    finally:
        main_db.close()
    return results


def run_auto_cycle_for_all_servers() -> List[Dict[str, Any]]:
    """Called periodically by the scheduler: runs apply-cycles for servers
    whose LLM runtime is enabled and due. Never raises."""
    from database import GameSessionLocal, SessionLocal
    from models import MonsterSpawnControl

    results: List[Dict[str, Any]] = []
    main_db = next(SessionLocal())
    try:
        game_db = next(GameSessionLocal())
        try:
            rows = game_db.query(MonsterSpawnControl).all()
            for row in rows:
                server = (row.server or "").strip().upper()
                if not server:
                    continue
                runtime = get_llm_runtime(main_db, server)
                if not check_auto_cycle_due(runtime):
                    continue
                # A queued/running manual run owns this server right now; the
                # worker will handle it — don't pile a second cycle behind it.
                if isinstance(runtime.get("pending_run"), dict) \
                        or _active_run_live(runtime.get("active_run"), datetime.utcnow()):
                    continue
                game_db.commit()
                game_db.refresh(row)
                state = {
                    "config": row.config if isinstance(row.config, dict) else {},
                    "memory": row.memory if isinstance(row.memory, dict) else {},
                    "telemetry": row.telemetry if isinstance(row.telemetry, dict) else {},
                    "dashboard": {},
                }
                run_id = uuid.uuid4().hex[:12]
                _create_run_record(main_db, server, "apply", None, "auto", run_id, None)
                task_ref = f"{server}:{run_id}"
                _task(TASK_KIND, task_ref, "claimed", "автоцикл запущен планировщиком")
                try:
                    result = run_llm_cycle(main_db, server, state, mode="apply", task_ref=task_ref)
                    if result.get("applied_changes"):
                        row.config = state["config"]
                        row.desired_revision = (row.desired_revision or 0) + 1
                        row.updated_at = datetime.utcnow()
                    if result.get("spawn_plans"):
                        plans = row.plans if isinstance(row.plans, list) else []
                        plans.extend(result["spawn_plans"])
                        row.plans = plans
                        row.desired_revision = (row.desired_revision or 0) + 1
                        row.updated_at = datetime.utcnow()
                    game_db.commit()
                    run_row = _find_run_record(main_db, server, run_id)
                    _finish_run_record(main_db, run_row, result, None)
                    results.append({"server": server, "status": "ok",
                                    "applied": len(result.get("applied_changes", [])),
                                    "spawned": len(result.get("spawn_plans", []))})
                except LLMError as exc:
                    run_row = _find_run_record(main_db, server, run_id)
                    _finish_run_record(main_db, run_row, {}, str(exc))
                    _task(TASK_KIND, task_ref, "failed", f"Ошибка автоцикла: {exc}"[:300], "error")
                    results.append({"server": server, "status": "error", "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    logger.warning("LLM auto-cycle failed for %s: %s", server, exc)
                    run_row = _find_run_record(main_db, server, run_id)
                    _finish_run_record(main_db, run_row, {}, f"Ошибка автоцикла: {exc}")
                    _task(TASK_KIND, task_ref, "failed", f"Ошибка автоцикла: {exc}"[:300], "error")
                    results.append({"server": server, "status": "error", "error": str(exc)})
        finally:
            game_db.close()
    finally:
        main_db.close()
    return results
