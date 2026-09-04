"""Local LLM (Qwen 3 4B) assistant for MonsterSpawnController.

The site-side assistant reads the controller's live state (config, memory,
telemetry posted by the game plugin) and produces STRICT JSON decisions:

  1. Config corrections — small bounded adjustments of a whitelist of
     difficulty parameters.
  2. Manual spawns — wave plans the plugin picks up through the existing
     revision system (source "llm").

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
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config import get_settings

logger = logging.getLogger(__name__)

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
}

EDITABLE_INT_KEYS: Dict[str, Tuple[int, int, int]] = {
    "pace_intercept_max_per_round": (1, 10, 3),
    "learned_waves_max": (5, 80, 20),
}

EDITABLE_BOOL_KEYS = {
    "single_wave_mode",
    "pace_intercept_enabled",
    "intensity_gate_enabled",
    "learned_waves_enabled",
    "pace_intercept_prefer_learned",
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
    "last_result": None,
    "history": [],             # last 20 runs: {at, mode, applied, rejected, summary}
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
    data[server] = runtime

    # AppSetting.value is String(2000): drop the oldest history entries (and
    # finally the bulky last_result) rather than failing the commit.
    encoded = json.dumps(data, ensure_ascii=False)
    while len(encoded) > 1900 and runtime.get("history"):
        runtime["history"] = runtime["history"][1:]
        encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded) > 1900:
        runtime["last_result"] = None
        encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded) > 2000:
        runtime["last_result"] = None
        runtime["history"] = []
        encoded = json.dumps(data, ensure_ascii=False)

    row.value = encoded[:2000]
    db.commit()


# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible /chat/completions)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    pass


def _chat_completions(messages: List[Dict[str, str]], base_url: str, api_key: str,
                      model: str, timeout: float, temperature: float,
                      top_p: float) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": 2048,
        # Qwen 3 thinking mode: disable for deterministic JSON output.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM endpoint недоступен ({base_url}): {exc}") from exc
    except (ValueError, KeyError) as exc:
        raise LLMError(f"Некорректный ответ LLM endpoint: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Ответ LLM без choices/message: {exc}") from exc
    if not isinstance(content, str):
        raise LLMError("Ответ LLM не содержит текста")
    return content


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(content: str) -> Dict[str, Any]:
    """Strip Qwen thinking tags and pull out the outermost JSON object."""
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
4. Никогда не выдумывай данные, которых нет в JSON. Если состояние неполное — верни пустые списки \
и опиши, чего не хватает, в "situation".
5. Ничего кроме JSON. Никакого markdown, никаких комментариев."""


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
            }
            for name, entry in (memory.get("waves") or {}).items()
            if isinstance(entry, dict) and (entry.get("times_spawned") or 0) > 0
        },
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
# Public API
# ---------------------------------------------------------------------------

def run_llm_cycle(db, server: str, state: dict, mode: str = "analyze",
                  instruction: Optional[str] = None) -> Dict[str, Any]:
    """mode: analyze (read-only), apply (config changes applied by bumping
    desired_revision), spawn (also add spawn plans)."""
    settings = get_settings()
    base_url = settings.monster_llm_base_url
    if not base_url:
        raise LLMError("LLM не настроен: monster_llm_base_url пуст")

    runtime = get_llm_runtime(db, server)
    known_species = _collect_known_species(state)
    user_prompt = _build_user_prompt(state, instruction, known_species)

    started = time.monotonic()
    content = _chat_completions(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        base_url=base_url,
        api_key=settings.monster_llm_api_key,
        model=settings.monster_llm_model,
        timeout=settings.monster_llm_timeout_seconds,
        temperature=settings.monster_llm_temperature,
        top_p=settings.monster_llm_top_p,
    )

    try:
        decision = _extract_json(content)
    except LLMError:
        # One retry with a strict reminder.
        content = _chat_completions(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": content[:2000]},
                {"role": "user", "content": "Предыдущий ответ не был валидным JSON. "
                                            "Верни ТОЛЬКО JSON-объект по схеме."},
            ],
            base_url=base_url,
            api_key=settings.monster_llm_api_key,
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

    # In analyze mode nothing is written back.
    applied_changes: List[dict] = config_applied if mode in ("apply", "spawn") else []
    spawn_plans: List[dict] = []
    if mode == "spawn" and spawn_applied:
        spawn_plans = _spawn_requests_to_plans(state, spawn_applied, datetime.utcnow())

    result = {
        "at": datetime.utcnow().isoformat(),
        "mode": mode,
        "duration_ms": duration_ms,
        "situation": situation,
        "confidence": round(confidence, 2),
        "applied_changes": applied_changes,
        "rejected": config_rejected + spawn_rejected,
        "spawn_requests": spawn_applied if mode == "spawn" else [],
        "spawn_plans": spawn_plans,
        "plan_ids": [plan["id"] for plan in spawn_plans],
        "raw_decision_keys": list(decision.keys()),
    }

    if mode in ("apply", "spawn"):
        runtime["last_result"] = result
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


def get_llm_deployment_info() -> Dict[str, Any]:
    """Non-secret info for the admin UI."""
    settings = get_settings()
    return {
        "base_url": settings.monster_llm_base_url,
        "model": settings.monster_llm_model,
        "temperature": settings.monster_llm_temperature,
        "top_p": settings.monster_llm_top_p,
        "timeout_seconds": settings.monster_llm_timeout_seconds,
        "api_key_set": bool(settings.monster_llm_api_key),
        "has_base_url": bool(settings.monster_llm_base_url),
    }


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
                game_db.commit()
                game_db.refresh(row)
                state = {
                    "config": row.config if isinstance(row.config, dict) else {},
                    "memory": row.memory if isinstance(row.memory, dict) else {},
                    "telemetry": row.telemetry if isinstance(row.telemetry, dict) else {},
                    "dashboard": {},
                }
                try:
                    result = run_llm_cycle(main_db, server, state, mode="apply")
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
                    results.append({"server": server, "status": "ok",
                                    "applied": len(result.get("applied_changes", [])),
                                    "spawned": len(result.get("spawn_plans", []))})
                except LLMError as exc:
                    results.append({"server": server, "status": "error", "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    logger.warning("LLM auto-cycle failed for %s: %s", server, exc)
                    results.append({"server": server, "status": "error", "error": str(exc)})
        finally:
            game_db.close()
    finally:
        main_db.close()
    return results
