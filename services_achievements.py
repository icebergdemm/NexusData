"""
Ачивки: валидация схемы конструктора (событие → условия → награда, цепочки)
и справочники для админки/плагина.

Та же философия разделения ошибок/предупреждений, что и в
services_respawn_dolls.py:
  • ОШИБКА — объективно некорректно вне зависимости от контента сервера:
    неизвестное имя события/оператора, число вне диапазона, дублирующийся
    key, ссылка на несуществующую ачивку в requires. Сохранять нельзя.
  • ПРЕДУПРЕЖДЕНИЕ — идентификатор (предмет/талант/стат/профессия), которого
    нет в известных каталогах — на сервере могут стоять моды.

Плагин (Achievements.cs) получает ПОЛНЫЙ список enabled-определений при
каждом опросе (декларативная синхронизация — та же модель, что у
RespawnDolls, см. RESPAWN_DOLLS_API_GUIDE.md и ACHIEVEMENTS_API_GUIDE.md) и
сам считает прогресс/выдаёт награды; сеть используется только для раздачи
определений и приёма отчётов о разблокировке.
"""
import logging
import re
import secrets
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Типы зон мира — общий словарь для "docked".target_type и "zone_entered".zone_type
# (см. ZoneClassifier в Achievements.cs — оба события классифицируют submarine/
# открытую воду одной и той же функцией, чтобы значения не разъезжались).
ZONE_TYPES = (
    "submarine", "outpost", "wreck", "ruin", "beacon", "enemy_submarine",
    "cave", "abyss", "open_water", "unknown",
)

# ══════════════════════════════════════════════════════════════════════════
# Каталог событий — единственное место, где плагин и сайт должны совпадать.
# Меняя это, обязательно синхронизировать с Achievements.cs (AchievementHooks
# / PositionTracker) и ACHIEVEMENTS_API_GUIDE.md.
# ══════════════════════════════════════════════════════════════════════════
EVENT_CATALOG: Dict[str, Dict[str, Any]] = {
    "kill": {
        "label": "Убийство",
        "fields": {
            "target_type": {"label": "Тип цели", "values": ["monster", "human"]},
            "target_identifier": {"label": "Вид монстра / профессия жертвы-игрока"},
            "weapon": {"label": "Идентификатор оружия"},
            "cause_of_death": {"label": "Причина смерти (affliction/тип)"},
        },
    },
    "character_death": {
        "label": "Смерть персонажа игрока",
        "fields": {
            "job": {"label": "Профессия погибшего"},
            "cause_of_death": {"label": "Причина смерти"},
            "killed_by_player": {"label": "Убит другим игроком", "values": [True, False]},
        },
    },
    "skill_level": {
        "label": "Достигнут уровень навыка",
        "fields": {
            "skill": {"label": "Навык", "values": ["helm", "weapons", "medical", "electrical", "mechanical"]},
            "level": {"label": "Новый уровень (целое, 0..100)"},
            "prev_level": {"label": "Предыдущий уровень"},
        },
    },
    "talent_unlocked": {
        "label": "Разблокирован талант",
        "fields": {"talent_id": {"label": "Идентификатор таланта"}},
    },
    "docked": {
        "label": "Стыковка подлодки",
        "fields": {
            "job": {"label": "Профессия члена экипажа (событие шлётся каждому на борту)"},
            "target_type": {"label": "Тип цели стыковки", "values": list(ZONE_TYPES)},
        },
    },
    "zone_entered": {
        "label": "Вход в зону мира (пещера/затонувший корабль/руины/маяк/аванпост/бездна/открытая вода)",
        "fields": {
            "zone_type": {"label": "Тип зоны", "values": list(ZONE_TYPES)},
            "job": {"label": "Профессия"},
        },
    },
    "players_nearby": {
        "label": "Игроки поблизости (периодическая проверка, интервал — PositionTrackIntervalSeconds в конфиге плагина)",
        "fields": {
            "nearby_300": {"label": "Игроков в радиусе 300 игровых единиц"},
            "nearby_600": {"label": "Игроков в радиусе 600"},
            "nearby_1000": {"label": "Игроков в радиусе 1000"},
            "nearby_2000": {"label": "Игроков в радиусе 2000"},
            "nearby_5000": {"label": "Игроков в радиусе 5000"},
            "job": {"label": "Профессия"},
        },
    },
    "distance_traveled": {
        "label": "Перемещение по миру (накопительно — используйте trigger.sum_field=\"delta\")",
        "fields": {
            "delta": {"label": "Пройдено с прошлой проверки, игровые единицы (числовое, для sum_field)"},
            "job": {"label": "Профессия"},
        },
    },
    "item_crafted": {
        "label": "Предмет изготовлен на фабрикаторе",
        "fields": {
            "item_id": {"label": "Идентификатор предмета"},
            "job": {"label": "Профессия изготовившего"},
        },
    },
    "mission_complete": {
        "label": "Задание выполнено",
        "fields": {
            "mission_identifier": {"label": "Идентификатор задания"},
            "mission_type": {"label": "Тип задания"},
            "job": {"label": "Профессия (событие шлётся каждому в экипаже)"},
        },
    },
    "mission_failed": {
        "label": "Задание провалено",
        "fields": {
            "mission_identifier": {"label": "Идентификатор задания"},
            "mission_type": {"label": "Тип задания"},
        },
    },
    "round_end": {
        "label": "Конец раунда",
        "fields": {
            "survived": {"label": "Пережил раунд", "values": [True, False]},
            "job": {"label": "Профессия"},
            "duration_seconds": {"label": "Длительность раунда, сек"},
        },
    },
    "round_start": {
        "label": "Начало раунда",
        "fields": {
            "job": {"label": "Профессия"},
            "submarine": {"label": "Название подлодки"},
        },
    },
    "chat_message": {
        "label": "Сообщение в чат",
        "fields": {
            "text": {"label": "Текст сообщения"},
            "chat_type": {"label": "Тип чата (Default/Radio/Dead/…)"},
        },
    },
    "damage_dealt": {
        "label": "Урон нанесён (шлётся атакующему, если это игрок)",
        "fields": {
            "amount": {"label": "Величина урона (для trigger.sum_field — суммарный урон)"},
            "target_type": {"label": "Тип цели", "values": ["human", "monster"]},
            "target_identifier": {"label": "Вид монстра / профессия жертвы-игрока"},
        },
    },
    "damage_taken": {
        "label": "Урон получен (шлётся жертве, если это игрок)",
        "fields": {
            "amount": {"label": "Величина урона (для trigger.sum_field — суммарно полученный урон)"},
            "attacker_is_player": {"label": "Атаковал другой игрок", "values": [True, False]},
        },
    },
    "affliction_applied": {
        "label": "На персонажа наложен недуг (яд, радиация, заражение хаском, кровотечение и т.п.)",
        "fields": {
            "affliction_id": {"label": "Идентификатор недуга"},
            "strength": {"label": "Сила недуга"},
            "limb": {"label": "Конечность (Torso/Head/…)"},
        },
    },
    "item_broken": {
        "label": "Предмет сломан (прочность достигла 0)",
        "fields": {"item_id": {"label": "Идентификатор предмета"}},
    },
    "item_repaired": {
        "label": "Предмет полностью починен (прочность достигла максимума)",
        "fields": {"item_id": {"label": "Идентификатор предмета"}},
    },
    "item_deconstructed": {
        "label": "Предмет разобран на деконструкторе",
        "fields": {
            "item_id": {"label": "Идентификатор разобранного предмета"},
            "job": {"label": "Профессия разобравшего"},
        },
    },
    "experience_gained": {
        "label": "Получен опыт (используйте trigger.sum_field=\"amount\" для суммарного опыта)",
        "fields": {
            "amount": {"label": "Начислено опыта за раз"},
            "job": {"label": "Профессия"},
        },
    },
    "money_received": {
        "label": "Начислены деньги (кампания; используйте trigger.sum_field=\"amount\" для суммарного заработка)",
        "fields": {
            "amount": {"label": "Начислено за раз"},
            "job": {"label": "Профессия"},
        },
    },
    "order_given": {
        "label": "Персонажу назначен приказ (штурвал, ремонт, охрана и т.п.)",
        "fields": {
            "order_id": {"label": "Идентификатор приказа"},
            "job": {"label": "Профессия получившего приказ"},
        },
    },
    "character_revived": {
        "label": "Персонаж воскрешён (СЛР, дефибриллятор, консоль админа)",
        "fields": {"job": {"label": "Профессия воскрешённого"}},
    },
    "item_used": {
        "label": "Предмет применён (шприц, инструмент, оружие…). ЧАСТОЕ событие — задайте trigger.cooldown_seconds",
        "fields": {
            "item_id": {"label": "Идентификатор применённого предмета"},
            "job": {"label": "Профессия применившего"},
        },
    },
    "door_used": {
        "label": "Дверь/люк открыт или закрыт. ЧАСТОЕ событие — задайте trigger.cooldown_seconds",
        "fields": {
            "opened": {"label": "Открыта (иначе закрыта)", "values": [True, False]},
            "job": {"label": "Профессия"},
        },
    },
    "fire_started": {
        "label": "Возник пожар (шлётся тому, кто его вызвал, если это игрок)",
        "fields": {"job": {"label": "Профессия виновника"}},
    },
    "reactor_meltdown": {
        "label": "Расплавление реактора (шлётся всем живым игрокам на этой подлодке)",
        "fields": {"job": {"label": "Профессия"}},
    },
    "money_spent": {
        "label": "Потрачены деньги (кампания; используйте trigger.sum_field=\"amount\")",
        "fields": {
            "amount": {"label": "Потрачено за раз"},
            "job": {"label": "Профессия"},
        },
    },
    "hull_status": {
        "label": "Состояние помещения вокруг игрока (периодическая проверка, PositionTrackIntervalSeconds)",
        "fields": {
            "water_percent": {"label": "Затопление помещения, 0..100"},
            "oxygen_percent": {"label": "Кислород в помещении, 0..100"},
            "fire_nearby": {"label": "В помещении есть пожар", "values": [True, False]},
            "in_submarine": {"label": "Игрок внутри структуры (не в открытой воде)", "values": [True, False]},
            "job": {"label": "Профессия"},
        },
    },
    "depth_reached": {
        "label": "Глубина погружения (периодическая проверка) — используйте с op=gte",
        "fields": {
            "depth_meters": {"label": "Текущая глубина в игровых метрах"},
            "in_submarine": {"label": "Внутри структуры", "values": [True, False]},
            "job": {"label": "Профессия"},
        },
    },
    "item_picked_up": {
        "label": "Предмет подобран/взят в руки",
        "fields": {
            "item_id": {"label": "Идентификатор предмета"},
            "job": {"label": "Профессия"},
        },
    },
    "weapon_fired": {
        "label": "Выстрел из дальнобойного оружия. ЧАСТОЕ событие — задайте trigger.cooldown_seconds",
        "fields": {
            "item_id": {"label": "Идентификатор оружия"},
            "job": {"label": "Профессия стрелявшего"},
        },
    },
    "melee_attack": {
        "label": "Удар холодным оружием. ЧАСТОЕ событие — задайте trigger.cooldown_seconds",
        "fields": {
            "item_id": {"label": "Идентификатор оружия"},
            "job": {"label": "Профессия"},
        },
    },
    "wire_connected": {
        "label": "Провод подключён к разъёму (электромонтаж)",
        "fields": {
            "item_id": {"label": "Идентификатор провода"},
            "job": {"label": "Профессия монтажника"},
        },
    },
    "structure_damaged": {
        "label": "Повреждена конструкция подлодки (шлётся виновнику, если это игрок)",
        "fields": {
            "amount": {"label": "Величина повреждения (для trigger.sum_field)"},
            "job": {"label": "Профессия"},
        },
    },
    "character_healed": {
        "label": "Персонаж вылечен от недуга (шлётся ЛЕКАРЮ, если лечил игрок)",
        "fields": {
            "affliction_id": {"label": "Идентификатор/тип вылеченного недуга"},
            "amount": {"label": "На сколько снижена сила недуга"},
            "self_heal": {"label": "Лечил сам себя", "values": [True, False]},
            "job": {"label": "Профессия лекаря"},
        },
    },
    "character_stunned": {
        "label": "Персонаж оглушён (переход из «не оглушён» в «оглушён»)",
        "fields": {
            "duration": {"label": "Длительность оглушения, сек"},
            "job": {"label": "Профессия"},
        },
    },
    "undocked": {
        "label": "Расстыковка подлодки (шлётся каждому живому члену экипажа на борту)",
        "fields": {
            "target_type": {"label": "От чего отстыковались", "values": list(ZONE_TYPES)},
            "job": {"label": "Профессия"},
        },
    },
    "item_dropped": {
        "label": "Предмет выброшен из рук/инвентаря",
        "fields": {
            "item_id": {"label": "Идентификатор предмета"},
            "job": {"label": "Профессия"},
        },
    },
    "mission_started": {
        "label": "Задание начато (шлётся каждому живому члену экипажа)",
        "fields": {
            "mission_identifier": {"label": "Идентификатор задания"},
            "mission_type": {"label": "Тип задания"},
            "job": {"label": "Профессия"},
        },
    },
    "items_purchased": {
        "label": "Куплены товары в магазине аванпоста (кампания)",
        "fields": {
            "item_id": {"label": "Идентификатор купленного товара (событие на каждую позицию)"},
            "quantity": {"label": "Количество (для trigger.sum_field)"},
            "store": {"label": "Идентификатор магазина"},
            "job": {"label": "Профессия покупателя"},
        },
    },
    "reputation_changed": {
        "label": "Изменилась репутация у фракции (шлётся каждому живому члену экипажа)",
        "fields": {
            "faction": {"label": "Идентификатор фракции"},
            "change": {"label": "Величина изменения (может быть отрицательной)"},
            "value": {"label": "Новое значение репутации"},
            "job": {"label": "Профессия"},
        },
    },
    "container_opened": {
        "label": "Открыт контейнер (шкафчик, ящик, сейф). ЧАСТОЕ — задайте trigger.cooldown_seconds",
        "fields": {
            "item_id": {"label": "Идентификатор контейнера"},
            "job": {"label": "Профессия"},
        },
    },
    "ballast_flora_damaged": {
        "label": "Нанесён урон балластной флоре (используйте trigger.sum_field=\"amount\")",
        "fields": {
            "amount": {"label": "Величина урона"},
            "job": {"label": "Профессия"},
        },
    },
    "ballast_flora_killed": {
        "label": "Балластная флора полностью уничтожена (шлётся экипажу заражённой подлодки)",
        "fields": {"job": {"label": "Профессия"}},
    },
}

CONDITION_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "contains"}
REWARD_TYPES = {"item", "stat", "skill", "talent"}
SCOPES = {"persistent", "round"}
BASE_SKILLS = {"helm", "weapons", "medical", "electrical", "mechanical"}

_KEY_RE = re.compile(r"^[a-z0-9_]{2,64}$")


def generate_public_id() -> str:
    """Резервный key, если админ не задал свой (не рекомендуется — см. UI)."""
    return f"ach_{secrets.token_hex(4)}"


# ── Справочники (переиспользуем то, что уже выгружено для кукол) ──────────

def _item_catalog() -> Dict[str, Any]:
    from services_respawn_dolls import _catalog
    return _catalog()


def _doll_reference() -> Dict[str, Any]:
    from services_respawn_dolls import doll_reference
    return doll_reference()


def achievements_reference() -> Dict[str, Any]:
    """Справочник для конструктора в админке: события+поля, операторы, типы
    наград, плюс профессии/навыки/таланты/статы — те же выгрузки, что уже
    делает конструктор кукол (static/doll_reference.json), чтобы не вести
    два независимых каталога игрового контента."""
    ref = _doll_reference()
    return {
        "events": {k: {"label": v["label"], "fields": v["fields"]} for k, v in EVENT_CATALOG.items()},
        "condition_ops": sorted(CONDITION_OPS),
        "reward_types": sorted(REWARD_TYPES),
        "scopes": sorted(SCOPES),
        "skills": sorted(BASE_SKILLS),
        "jobs": ref.get("jobs") or [],
        "talents": ref.get("talents") or {},
        "stat_types": ref.get("stat_types") or [],
    }


# ── Валидация условий (рекурсивное дерево all/any/not либо лист) ──────────

def _validate_condition_node(node: Any, errors: List[str], warnings: List[str], path: str) -> None:
    if not isinstance(node, dict):
        errors.append(f"{path}: условие должно быть объектом")
        return

    combinators = [k for k in ("all", "any", "not") if k in node]
    if combinators:
        if len(combinators) > 1:
            errors.append(f"{path}: одновременно указаны {combinators} — используйте только один комбинатор на уровень")
            return
        key = combinators[0]
        if key == "not":
            _validate_condition_node(node["not"], errors, warnings, f"{path}.not")
        else:
            children = node[key]
            if not isinstance(children, list) or not children:
                errors.append(f"{path}.{key}: должен быть непустым списком условий")
                return
            for i, child in enumerate(children):
                _validate_condition_node(child, errors, warnings, f"{path}.{key}[{i}]")
        return

    # Лист: {"field": ..., "op": ..., "value": ...}
    field = node.get("field")
    op = node.get("op")
    if not field or not isinstance(field, str):
        errors.append(f"{path}.field: обязательное строковое поле")
    if op not in CONDITION_OPS:
        errors.append(f"{path}.op: допустимы {', '.join(sorted(CONDITION_OPS))}")
    if op in ("in", "not_in") and not isinstance(node.get("value"), list):
        errors.append(f"{path}.value: для op={op} нужен список")
    if "value" not in node:
        errors.append(f"{path}.value: обязательное поле")


def validate_trigger(trigger: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(trigger, dict):
        return ["trigger: должен быть объектом"], []

    event = trigger.get("event")
    if not event or event not in EVENT_CATALOG:
        errors.append(f"trigger.event: обязательное поле, допустимы {', '.join(sorted(EVENT_CATALOG))}")

    count = trigger.get("count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        errors.append("trigger.count: целое число ≥ 1")

    distinct_by = trigger.get("distinct_by")
    if distinct_by is not None:
        if not isinstance(distinct_by, str) or not distinct_by:
            errors.append("trigger.distinct_by: строка — имя поля события")
        elif event in EVENT_CATALOG and distinct_by not in EVENT_CATALOG[event]["fields"]:
            warnings.append(
                f"trigger.distinct_by: у события «{event}» нет поля «{distinct_by}» — "
                f"известные поля: {', '.join(EVENT_CATALOG[event]['fields'])}"
            )

    # sum_field — накопительный режим (напр. distance_traveled: прогресс = сумма
    # значений поля delta по всем подходящим событиям, а не число событий).
    # Несовместим с distinct_by — это два разных способа считать progress.
    sum_field = trigger.get("sum_field")
    if sum_field is not None:
        if not isinstance(sum_field, str) or not sum_field:
            errors.append("trigger.sum_field: строка — имя числового поля события")
        elif event in EVENT_CATALOG and sum_field not in EVENT_CATALOG[event]["fields"]:
            warnings.append(
                f"trigger.sum_field: у события «{event}» нет поля «{sum_field}» — "
                f"известные поля: {', '.join(EVENT_CATALOG[event]['fields'])}"
            )
        if distinct_by:
            errors.append("trigger: distinct_by и sum_field взаимоисключающие — выберите один режим подсчёта")

    # ── Модификаторы засчитывания (все опциональны, применяются ПОСЛЕ того,
    # как условия совпали, но ДО приращения прогресса — см. AchievementEngine.Fire).
    cooldown = trigger.get("cooldown_seconds")
    if cooldown is not None and (isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or cooldown < 0):
        errors.append("trigger.cooldown_seconds: неотрицательное число секунд")

    max_per_round = trigger.get("max_per_round")
    if max_per_round is not None and (isinstance(max_per_round, bool) or not isinstance(max_per_round, int) or max_per_round < 1):
        errors.append("trigger.max_per_round: целое число ≥ 1")

    window = trigger.get("window_seconds")
    if window is not None:
        if isinstance(window, bool) or not isinstance(window, (int, float)) or window <= 0:
            errors.append("trigger.window_seconds: положительное число секунд")
        if distinct_by or sum_field:
            errors.append(
                "trigger.window_seconds работает только с обычным счётчиком событий — "
                "уберите distinct_by/sum_field"
            )

    reset_on_death = trigger.get("reset_on_death")
    if reset_on_death is not None and not isinstance(reset_on_death, bool):
        errors.append("trigger.reset_on_death: true/false")

    # Частые события без cooldown/окна легко «нафармить» — предупреждаем, но не
    # запрещаем (админ может осознанно считать каждое срабатывание).
    if event in ("item_used", "door_used", "chat_message", "players_nearby", "hull_status",
                 "weapon_fired", "melee_attack", "depth_reached", "container_opened") \
            and not cooldown and not window and not distinct_by:
        warnings.append(
            f"trigger: событие «{event}» срабатывает часто — без cooldown_seconds/window_seconds "
            "ачивка может накрутиться за секунды"
        )

    conditions = trigger.get("conditions")
    if conditions is not None:
        _validate_condition_node(conditions, errors, warnings, "trigger.conditions")
        # Мягкая подсказка по неизвестным полям — не блокирует сохранение,
        # т.к. дерево может ссылаться на поля вложенных комбинаторов, которые
        # уже проверены структурно выше.
        if event in EVENT_CATALOG:
            _warn_unknown_fields(conditions, EVENT_CATALOG[event]["fields"].keys(), warnings, "trigger.conditions")

    return errors, warnings


def _warn_unknown_fields(node: Any, known: Any, warnings: List[str], path: str) -> None:
    if not isinstance(node, dict):
        return
    for key in ("all", "any"):
        if key in node and isinstance(node[key], list):
            for i, child in enumerate(node[key]):
                _warn_unknown_fields(child, known, warnings, f"{path}.{key}[{i}]")
            return
    if "not" in node:
        _warn_unknown_fields(node["not"], known, warnings, f"{path}.not")
        return
    field = node.get("field")
    if field and field not in known:
        warnings.append(f"{path}: поле «{field}» не из известного набора события ({', '.join(known)})")


# ── Валидация наград ────────────────────────────────────────────────────

def validate_rewards(rewards: Any) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if rewards is None:
        return errors, warnings
    if not isinstance(rewards, list):
        return ["rewards: должен быть списком"], []

    catalog = _item_catalog()
    ref = _doll_reference()
    known_stats = set(ref.get("stat_types") or [])
    all_talents: Set[str] = set()
    for job_talents in (ref.get("talents") or {}).values():
        for t in job_talents:
            all_talents.add(t.get("identifier"))

    for i, r in enumerate(rewards):
        here = f"rewards[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{here}: должен быть объектом")
            continue
        rtype = r.get("type")
        if rtype not in REWARD_TYPES:
            errors.append(f"{here}.type: допустимы {', '.join(sorted(REWARD_TYPES))}")
            continue

        if rtype == "item":
            identifier = (r.get("identifier") or "").strip()
            if not identifier:
                errors.append(f"{here}.identifier: обязательное поле")
            elif catalog and identifier not in catalog:
                warnings.append(f"{here}: предмет «{identifier}» не найден в каталоге (или это предмет из мода)")
            amount = r.get("amount", 1)
            if not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= 100:
                errors.append(f"{here}.amount: целое число 1..100")
            quality = r.get("quality")
            if quality is not None and (isinstance(quality, bool) or not isinstance(quality, int) or not 0 <= quality <= 3):
                errors.append(f"{here}.quality: 0..3")
            condition = r.get("condition")
            if condition is not None and (isinstance(condition, bool) or not isinstance(condition, (int, float)) or not 0 <= condition <= 100):
                errors.append(f"{here}.condition: 0..100")

        elif rtype == "stat":
            stat_type = (r.get("stat_type") or "").strip()
            if not stat_type:
                errors.append(f"{here}.stat_type: обязательное поле")
            elif known_stats and stat_type not in known_stats:
                warnings.append(f"{here}.stat_type: «{stat_type}» нет в перечне движка")
            value = r.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{here}.value: должно быть числом")
            if not (r.get("identifier") or "").strip():
                errors.append(f"{here}.identifier: обязателен уникальный ключ бонуса (напр. «ach_<key>»)")

        elif rtype == "skill":
            skill = (r.get("skill") or "").strip()
            if skill not in BASE_SKILLS:
                warnings.append(f"{here}.skill: «{skill}» не из базового набора ({', '.join(sorted(BASE_SKILLS))})")
            amount = r.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
                errors.append(f"{here}.amount: ненулевое целое число")

        elif rtype == "talent":
            talent_id = (r.get("talent_id") or "").strip()
            if not talent_id:
                errors.append(f"{here}.talent_id: обязательное поле")
            elif all_talents and talent_id not in all_talents:
                warnings.append(f"{here}.talent_id: «{talent_id}» не найден в дереве талантов ни одной профессии")

    return errors, warnings


# ── Валидация верхнего уровня определения ──────────────────────────────

def validate_achievement_meta(
    key: str, title: str, scope: str, servers: Any, requires: Any, chain_order: Any,
    known_keys: Set[str], self_key: Optional[str] = None,
) -> Tuple[List[str], List[str], List[str]]:
    """Возвращает (ошибки, предупреждения, нормализованные requires)."""
    errors: List[str] = []
    warnings: List[str] = []
    clean_requires: List[str] = []

    if not key or not _KEY_RE.match(key):
        errors.append("key: 2..64 символов, только [a-z0-9_]")
    if not (title or "").strip():
        errors.append("title: обязательное поле")
    if scope not in SCOPES:
        errors.append(f"scope: допустимы {', '.join(sorted(SCOPES))}")

    if servers is not None:
        if not isinstance(servers, list) or not all(isinstance(s, str) for s in servers):
            errors.append("servers: список строк (имён серверов) или пустой список")

    if requires is not None:
        if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
            errors.append("requires: список ключей ачивок")
        else:
            for r in requires:
                if r == self_key:
                    errors.append(f"requires: ачивка не может требовать саму себя ({r})")
                elif r not in known_keys:
                    errors.append(f"requires: ачивка «{r}» не найдена")
                elif r not in clean_requires:
                    clean_requires.append(r)

    if chain_order is not None and (isinstance(chain_order, bool) or not isinstance(chain_order, int)):
        errors.append("chain_order: целое число")

    return errors, warnings, clean_requires


def validate_achievement(
    key: str, title: str, scope: str, servers: Any, requires: Any, chain_order: Any,
    trigger: Any, rewards: Any, known_keys: Set[str], self_key: Optional[str] = None,
) -> Tuple[List[str], List[str], List[str]]:
    """Полная валидация определения одним вызовом — используется всеми
    admin-эндпоинтами (create/update/clone), чтобы правила не расходились."""
    meta_errors, meta_warnings, clean_requires = validate_achievement_meta(
        key, title, scope, servers, requires, chain_order, known_keys, self_key
    )
    trigger_errors, trigger_warnings = validate_trigger(trigger)
    reward_errors, reward_warnings = validate_rewards(rewards)

    errors = meta_errors + trigger_errors + reward_errors
    warnings = meta_warnings + trigger_warnings + reward_warnings
    return errors, warnings, clean_requires
