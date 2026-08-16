"""
Пре-резолвинг китов на сайте — переносит на сервер всю логику, которую
раньше делал плагин (RoundManager_ND.cs::GiveCustomKit) сам, при каждом
спавне персонажа: подбор базового кита по уровню, подбор квал-кита по
уровню, применение ar_delete-фильтра от квал-кита к базовому, проверку
спец-кита (может сработать и от базового, и от квал-кита) с полным
замещением выдачи, резолвинг вариации (1/2/3).

Клиент теперь получает ГОТОВЫЕ наборы на каждую (профессия, уровень набора,
уровень квал-кита, вариация) — деление на "основной набор / квал-кит /
спец-набор" с сайта не видно вообще, только плоский список предметов +
таланты + статы. Единственное, что остаётся клиенту — выбрать нужный бакет
по (job_id, kit_level игрока, kval_level игрока, вариация), с тем же
фолбэком "нет точного уровня → берём ближайший меньший", который был и
раньше (это выбор БАКЕТА, а не резолвинг содержимого — оставлен клиенту
осознанно, там знают точный уровень игрока в момент спавна).

ВАЖНО: это порт алгоритма из RoundManager_ND.cs::GiveCustomKit (шаги 4-7 —
индивидуальные предметы/статы/таланты игрока, шаги 1-3, сюда НЕ входят,
это per-player runtime-состояние, никак не связанное с китами). При любых
изменениях в C# GiveCustomKit — синхронизировать здесь же, иначе два конца
разойдутся молча.
"""
from typing import Any, Dict, List, Optional

_SPECIAL_KEYS = {"ar_delete", "talents", "stats"}


def _resolve_variation_items(kit: Dict[str, Any], variation: int) -> Dict[str, Any]:
    """Зеркало ResolveKitItemsForVariation: V=1 или отсутствующая
    variation_N_items → фолбэк на items."""
    if variation == 2 and kit.get("variation_2_items"):
        return kit["variation_2_items"]
    if variation == 3 and kit.get("variation_3_items"):
        return kit["variation_3_items"]
    return kit.get("items") or {}


def _flatten_items(raw: Dict[str, Any], ar_delete: Optional[set] = None) -> List[Dict[str, Any]]:
    """Зеркало SpawnKitItems: спецключи (ar_delete/talents/stats) всегда
    пропускаются как предметы; ar_delete-фильтр (только для базового кита)
    убирает конкретные item_id. Числовое значение → {item_id, qty}, объект
    с "nested" → {item_id, nested}. Порядок словаря сохраняется (Python 3.7+
    dict сохраняет порядок вставки, как и было в исходном JSON)."""
    out = []
    for item_id, value in (raw or {}).items():
        if item_id in _SPECIAL_KEYS:
            continue
        if ar_delete and item_id in ar_delete:
            continue
        if isinstance(value, bool):
            continue  # не должно встречаться, но bool — подкласс int в Python
        if isinstance(value, (int, float)):
            qty = int(value)
            if qty > 0:
                out.append({"item_id": item_id, "qty": qty})
        elif isinstance(value, dict):
            nested = value.get("nested")
            if isinstance(nested, dict) and nested:
                out.append({"item_id": item_id, "nested": nested})
    return out


def _extract_ar_delete(kval_kit: Optional[Dict[str, Any]]) -> set:
    if not kval_kit:
        return set()
    raw = (kval_kit.get("items") or {}).get("ar_delete")
    if isinstance(raw, list):
        return {x for x in raw if isinstance(x, str)}
    return set()


def _extract_talents(kval_kit: Optional[Dict[str, Any]]) -> List[str]:
    if not kval_kit:
        return []
    raw = (kval_kit.get("items") or {}).get("talents")
    return [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []


def _extract_stats(kval_kit: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not kval_kit:
        return {}
    raw = (kval_kit.get("items") or {}).get("stats")
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _kit_dict(kit) -> Dict[str, Any]:
    """SQLAlchemy Kit row -> plain dict (тот же набор полей, что и KitOut)."""
    return {
        "id": kit.id, "job_id": kit.job_id, "kit_level": kit.kit_level,
        "items": kit.items or {}, "variation_2_items": kit.variation_2_items,
        "variation_3_items": kit.variation_3_items,
        "is_special": bool(kit.is_special), "special_variation": kit.special_variation,
    }


def build_resolved_kits(kits: List[Any]) -> Dict[str, Any]:
    """Строит resolved_kits[job_id][base_level][kval_level][variation] =
    {items, talents, stats, is_special} по ВСЕМ имеющимся в БД китам.

    job_id здесь — профессия БЕЗ суффикса "_k" (напр. "captain"), у которой
    может не быть собственных базовых китов вовсе, если это чисто
    квал-профессия — такие пропускаются (нет базового кита -> нечего
    резолвить, как и в GiveCustomKit: "Базовый кит не найден" -> return).
    kval_level="0" — отдельный бакет "без квал-кита" (соответствует
    отсутствию/kval_level<=0 у игрока)."""
    by_job: Dict[str, List[Dict[str, Any]]] = {}
    for kit in kits:
        d = _kit_dict(kit)
        by_job.setdefault(d["job_id"], []).append(d)

    result: Dict[str, Any] = {}

    for job_id, job_kits in by_job.items():
        if job_id.endswith("_k"):
            continue  # квал-киты обрабатываются как часть базовой профессии ниже
        kval_job_id = f"{job_id}_k"
        kval_kits = by_job.get(kval_job_id, [])
        kval_levels = sorted({0} | {k["kit_level"] for k in kval_kits})

        base_by_level: Dict[str, Any] = {}
        for base_level in sorted({k["kit_level"] for k in job_kits}):
            base_kit = next((k for k in job_kits if k["kit_level"] == base_level), None)
            if base_kit is None:
                continue

            kval_by_level: Dict[str, Any] = {}
            for kval_level in kval_levels:
                kval_kit = None
                if kval_level > 0:
                    kval_kit = next((k for k in kval_kits if k["kit_level"] == kval_level), None)
                    if kval_kit is None:
                        continue  # уровень выше есть только на бумаге — нечего резолвить

                variations: Dict[str, Any] = {}
                for variation in (1, 2, 3):
                    variations[str(variation)] = _resolve_one(base_kit, kval_kit, variation)
                kval_by_level[str(kval_level)] = variations

            if kval_by_level:
                base_by_level[str(base_level)] = kval_by_level

        if base_by_level:
            result[job_id] = base_by_level

    return result


def _resolve_one(base_kit: Dict[str, Any], kval_kit: Optional[Dict[str, Any]], variation: int) -> Dict[str, Any]:
    """Один (job, base_level, kval_level, variation) — зеркало хвоста
    GiveCustomKit (шаги 4-7 + спец-кит)."""
    special_kit = None
    if base_kit["is_special"] and base_kit["special_variation"] == variation:
        special_kit = base_kit
    elif kval_kit and kval_kit["is_special"] and kval_kit["special_variation"] == variation:
        special_kit = kval_kit

    if special_kit is not None:
        # Спец-кит полностью замещает выдачу (base+kval) — GiveCustomKit
        # делает return сразу после него, таланты/статы квал-кита не
        # применяются даже если спец-кит — это сам квал-кит.
        items = _flatten_items(_resolve_variation_items(special_kit, variation))
        return {"items": items, "talents": [], "stats": {}, "is_special": True}

    ar_delete = _extract_ar_delete(kval_kit)
    base_items = _flatten_items(_resolve_variation_items(base_kit, variation), ar_delete=ar_delete)
    kval_items = _flatten_items(_resolve_variation_items(kval_kit, variation)) if kval_kit else []

    return {
        "items": base_items + kval_items,
        "talents": _extract_talents(kval_kit),
        "stats": _extract_stats(kval_kit),
        "is_special": False,
    }
