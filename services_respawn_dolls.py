"""
Куклы респавна: валидация схемы и сериализация для игрового сервера.

Плагин к кривым данным устойчив (неизвестное поле игнорирует, неизвестный
предмет/талант пропускает с записью в лог), но админ должен узнать о проблеме
при сохранении, а не через полчаса из отчёта failed — поэтому валидация здесь.

Разделение строгих ошибок и предупреждений сделано намеренно:
  • ОШИБКА — то, что объективно некорректно независимо от сборки сервера:
    неверный слот/конечность, число вне диапазона, битый SteamID, срок в
    прошлом. Такое сохранять нельзя.
  • ПРЕДУПРЕЖДЕНИЕ — идентификатор, которого нет в известных справочниках
    (профессия, предмет, талант). Моды на игровом сервере легально добавляют
    свои, и жёсткий запрет ломал бы ивенты с модовым контентом. Админ видит
    список предупреждений при сохранении и решает сам.
"""
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# expires_at приходит/уходит как наивный MSK-wall-clock (см. routes/respawn_dolls.py
# _parse_expires_at и модульный докстринг там же) — тот же MSK, что и везде в этом
# файле. Использовать datetime.utcnow() или naive_dt.timestamp() напрямую здесь
# НЕЛЬЗЯ: первое сравнивает MSK-наивное значение с UTC-наивным (ошибка в 3 часа), а
# второе трактует наивное время как локальное время процесса — на сервере с TZ,
# отличным от MSK, все expires_at, уходящие плагину, сдвинулись бы на этот же оффсет
# (тот же класс бага, что уже описан в routes/replays.py про recorded_at).
MSK = timezone(timedelta(hours=3))


def _now_msk() -> datetime:
    return datetime.now(tz=MSK).replace(tzinfo=None)


# Значения перечислений движка — из инструкции к плагину (раздел 3.8/3.7).
INV_SLOTS = {
    "Head", "InnerClothes", "OuterClothes", "Headset", "Card", "Bag",
    "RightHand", "LeftHand", "HealthInterface",
}
LIMB_TYPES = {
    "Head", "Torso", "LeftArm", "RightArm", "LeftLeg", "RightLeg",
    "LeftHand", "RightHand", "LeftFoot", "RightFoot", "Waist", "Jaw",
}
SPAWN_MODES = {"auto", "player", "bot"}
BASE_JOBS = {
    "captain", "securityofficer", "medicaldoctor", "engineer", "mechanic", "assistant",
}
BASE_SKILLS = {"helm", "weapons", "medical", "electrical", "mechanical"}

MAX_ITEMS_PER_DOLL = 200
MAX_DOLLS_PER_RESPONSE = 200

_COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?|\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(\s*,\s*\d{1,3})?)$")
_STEAMID_RE = re.compile(r"^\d{17}$")

_item_catalog: Optional[Dict[str, Any]] = None
_appearance_catalog: Optional[Dict[str, Any]] = None
_doll_reference: Optional[Dict[str, Any]] = None

# draft — кукла собрана, но ещё не поставлена в очередь: в /dolls/pending она
# не попадает, игрок к ней не привязан. Привязка появляется отдельным шагом,
# когда куклу реально ставят на спавн, — так одну и ту же заготовку можно
# использовать для разных игроков и не выдумывать SteamID заранее.
DOLL_STATES = {
    "draft", "pending", "cancelled", "spawned", "spawned_with_errors", "failed", "expired",
}
EDITABLE_STATES = {"draft", "pending"}


def appearance_catalog() -> Dict[str, Any]:
    """Каталог внешности (static/appearance_catalog.json), выгружен из
    Content/Characters/Human/Human.xml игрового контента.

    Семантика подтверждена по исходникам движка (CharacterInfo.RecreateHead /
    FilterElements):
      • head_tags сверяется ТОЧНЫМ равенством множества тегов пресета
        (SetEquals), т.е. корректное значение — весь набор, например
        ["head3","male"]. Если передан один тег, движок возьмёт первый
        пресет с ним; если совпадений нет — случайный.
      • Причёски/бороды/аксессуары фильтруются по правилу «теги элемента ⊆
        теги головы», поэтому индекс считается ВНУТРИ отфильтрованного по
        полу списка. Бороды помечены male — у женских голов их список пуст.
    """
    global _appearance_catalog
    if _appearance_catalog is None:
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "appearance_catalog.json")
            with open(path, encoding="utf-8") as f:
                _appearance_catalog = json.load(f)
        except Exception as e:
            logger.warning(f"Каталог внешности недоступен: {e}")
            _appearance_catalog = {"heads": [], "wearables": {}, "colors": {}, "personalities": []}
    return _appearance_catalog


def doll_reference() -> Dict[str, Any]:
    """Справочник конструктора (static/doll_reference.json): профессии с
    навыками по умолчанию и числом вариантов снаряжения, таланты по
    профессиям, недуги, типы статов — всё с русскими названиями.

    Выгружается из игрового контента скриптом tools/build_doll_catalog.py.
    Пустые списки — не ошибка: интерфейс тогда просто оставит свободный ввод
    идентификаторов вместо выпадающих списков.
    """
    global _doll_reference
    if _doll_reference is None:
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "doll_reference.json")
            with open(path, encoding="utf-8") as f:
                _doll_reference = json.load(f)
        except Exception as e:
            logger.warning(f"Справочник конструктора кукол недоступен: {e}")
            _doll_reference = {"jobs": [], "skill_labels": {}, "talents": {}, "afflictions": [], "stat_types": []}
    return _doll_reference


def _validate_identity_against_catalog(identity: Dict[str, Any], warnings: List[str]) -> None:
    """Сверяет внешность с реальным контентом. Только предупреждения: на
    сервере могут стоять моды со своими головами и причёсками."""
    cat = appearance_catalog()
    heads = cat.get("heads") or []
    if not heads:
        return

    tags = identity.get("head_tags")
    gender = None
    if isinstance(tags, list) and tags:
        tagset = {str(t).strip().lower() for t in tags}
        gender = "female" if "female" in tagset else ("male" if "male" in tagset else None)
        if not any(set(h["tags"]) == tagset for h in heads):
            if len(tagset) == 1:
                warnings.append(
                    f"identity.head_tags: {sorted(tagset)} — точного пресета нет, движок возьмёт первый подходящий"
                )
            else:
                warnings.append(
                    f"identity.head_tags: набор {sorted(tagset)} не совпадает ни с одним пресетом — будет выбрана случайная голова"
                )

    wear = (cat.get("wearables") or {}).get(gender or "male") or {}
    for field, kind, label in (
        ("hair_index", "hair", "причёсок"),
        ("beard_index", "beard", "бород"),
        ("face_attachment_index", "face_attachment", "аксессуаров"),
    ):
        idx = identity.get(field)
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
            continue  # -1/не задано — «нет», это валидно
        available = len(wear.get(kind) or [])
        if available == 0:
            warnings.append(f"identity.{field}: для этой головы {label} нет — значение будет проигнорировано")
        elif idx >= available:
            warnings.append(f"identity.{field}: индекс {idx} вне диапазона (доступно 0..{available - 1})")

    personality = (identity.get("personality") or "").strip()
    known = cat.get("personalities") or []
    if personality and known and personality not in known:
        warnings.append(
            f"identity.personality: «{personality}» нет в контенте (доступны: {', '.join(known)})"
        )


def _catalog() -> Dict[str, Any]:
    """Каталог предметов (static/item_catalog.json), читается один раз."""
    global _item_catalog
    if _item_catalog is None:
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "item_catalog.json")
            with open(path, encoding="utf-8") as f:
                _item_catalog = json.load(f)
        except Exception as e:
            logger.warning(f"Каталог предметов недоступен, проверка идентификаторов отключена: {e}")
            _item_catalog = {}
    return _item_catalog


def generate_public_id() -> str:
    """Идентификатор куклы, уходящий плагину как "id"."""
    return f"d-{secrets.token_hex(6)}"


def normalize_steamid(raw: str) -> Optional[str]:
    """«STEAM64_7656…» и прочие обёртки → голые 17 цифр, иначе None.

    Префикс снимается ЯВНО, а не «выкинем все нецифры»: в самом слове
    STEAM64 есть цифры, и наивная зачистка давала бы 19-значный мусор
    (64 + сам id), который затем не проходил проверку.
    """
    text = str(raw or "").strip()
    text = re.sub(r"^steam(64)?[_:\-]?", "", text, flags=re.IGNORECASE)
    digits = re.sub(r"\D", "", text)
    return digits if _STEAMID_RE.match(digits) else None


def _check_color(value: Any, field: str, errors: List[str]) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, str) or not _COLOR_RE.match(value.strip()):
        errors.append(f"{field}: цвет должен быть в формате #RRGGBB, #RRGGBBAA или 'r,g,b[,a]'")


def _validate_items(items: Any, errors: List[str], warnings: List[str], counter: List[int], path: str = "inventory.items") -> None:
    """Рекурсивная проверка предметов (вложенность произвольной глубины)."""
    if items is None:
        return
    if not isinstance(items, list):
        errors.append(f"{path}: должен быть списком")
        return

    catalog = _catalog()
    for i, raw in enumerate(items):
        here = f"{path}[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{here}: должен быть объектом")
            continue

        counter[0] += 1
        if counter[0] > MAX_ITEMS_PER_DOLL:
            errors.append(f"Слишком много предметов: максимум {MAX_ITEMS_PER_DOLL} на куклу")
            return

        identifier = (raw.get("identifier") or "").strip()
        if not identifier:
            errors.append(f"{here}: не указан identifier предмета")
        elif catalog and identifier not in catalog:
            warnings.append(f"{here}: предмет «{identifier}» не найден в каталоге — проверьте идентификатор (или это предмет из мода)")

        amount = raw.get("amount", 1)
        if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= 100):
            errors.append(f"{here}: amount должен быть целым числом 1..100")

        condition = raw.get("condition")
        if condition is not None:
            if isinstance(condition, bool) or not isinstance(condition, (int, float)) or not 0 <= condition <= 100:
                errors.append(f"{here}: condition — прочность в процентах, 0..100")

        quality = raw.get("quality")
        if quality is not None:
            if isinstance(quality, bool) or not isinstance(quality, int) or not 0 <= quality <= 3:
                errors.append(f"{here}: quality должен быть 0..3")

        slot = raw.get("slot")
        if slot not in (None, "") and slot not in INV_SLOTS:
            errors.append(f"{here}: неизвестный слот «{slot}». Допустимые: {', '.join(sorted(INV_SLOTS))}")

        tags = raw.get("tags")
        if tags is not None and not isinstance(tags, list):
            errors.append(f"{here}: tags должен быть списком строк")

        _validate_items(raw.get("contained"), errors, warnings, counter, f"{here}.contained")


def validate_doll_spec(spec: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Проверяет тело куклы. Возвращает (ошибки, предупреждения)."""
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(spec, dict):
        return ["Тело куклы должно быть объектом"], []

    # identity
    identity = spec.get("identity")
    if identity is not None:
        if not isinstance(identity, dict):
            errors.append("identity: должен быть объектом")
        else:
            for field in ("hair_color", "facial_hair_color", "skin_color"):
                _check_color(identity.get(field), f"identity.{field}", errors)
            for field in ("hair_index", "beard_index", "moustache_index", "face_attachment_index"):
                val = identity.get(field)
                if val is not None and (isinstance(val, bool) or not isinstance(val, int)):
                    errors.append(f"identity.{field}: должен быть целым числом (-1 — отсутствует)")
            tags = identity.get("head_tags")
            if tags is not None and not isinstance(tags, list):
                errors.append("identity.head_tags: должен быть списком строк")
            else:
                _validate_identity_against_catalog(identity, warnings)

    # job
    job = spec.get("job")
    if job is not None:
        if not isinstance(job, dict):
            errors.append("job: должен быть объектом")
        else:
            ident = (job.get("identifier") or "").strip()
            if not ident:
                errors.append("job.identifier: укажите профессию")
            elif ident not in BASE_JOBS:
                warnings.append(f"job.identifier: профессия «{ident}» не из базового набора — убедитесь, что она есть на сервере (мод)")
            variant = job.get("variant")
            if variant is not None and (isinstance(variant, bool) or not isinstance(variant, int) or variant < 0):
                errors.append("job.variant: неотрицательное целое число")
            skills = job.get("skills")
            if skills is not None:
                if not isinstance(skills, dict):
                    errors.append("job.skills: должен быть объектом {навык: уровень}")
                else:
                    for name, level in skills.items():
                        if isinstance(level, bool) or not isinstance(level, (int, float)) or not 0 <= level <= 100:
                            errors.append(f"job.skills.{name}: уровень должен быть 0..100")
                        elif name not in BASE_SKILLS:
                            warnings.append(f"job.skills.{name}: навык не из базового набора")

    # progression
    prog = spec.get("progression")
    if prog is not None:
        if not isinstance(prog, dict):
            errors.append("progression: должен быть объектом")
        else:
            for field in ("experience", "additional_talent_points"):
                val = prog.get(field)
                if val is not None and (isinstance(val, bool) or not isinstance(val, int) or val < 0):
                    errors.append(f"progression.{field}: неотрицательное целое число")
            talents = prog.get("talents")
            if talents is not None and not isinstance(talents, list):
                errors.append("progression.talents: должен быть списком идентификаторов")
            elif talents:
                # Талант из чужой профессии движок просто не применит — это
                # предупреждение, а не ошибка: на сервере могут стоять моды.
                job_id = ((spec.get("job") or {}).get("identifier") or "").strip() if isinstance(spec.get("job"), dict) else ""
                known = {t["identifier"] for t in (doll_reference().get("talents") or {}).get(job_id, [])}
                if known:
                    for t in talents:
                        if str(t).strip() and str(t).strip() not in known:
                            warnings.append(f"progression.talents: «{t}» не из дерева профессии «{job_id}» — может не примениться")

    # stats
    stats = spec.get("stats")
    if stats is not None:
        if not isinstance(stats, list):
            errors.append("stats: должен быть списком")
        else:
            known_stats = set(doll_reference().get("stat_types") or [])
            for i, st in enumerate(stats):
                if not isinstance(st, dict):
                    errors.append(f"stats[{i}]: должен быть объектом")
                    continue
                stat_type = (st.get("stat_type") or "").strip()
                if not stat_type:
                    errors.append(f"stats[{i}].stat_type: обязательное поле")
                elif known_stats and stat_type not in known_stats:
                    warnings.append(f"stats[{i}].stat_type: «{stat_type}» нет в перечне движка — стат будет проигнорирован")
                value = st.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"stats[{i}].value: должно быть числом")

    # health
    health = spec.get("health")
    if health is not None:
        if not isinstance(health, dict):
            errors.append("health: должен быть объектом")
        else:
            affl = health.get("afflictions")
            if affl is not None:
                if not isinstance(affl, list):
                    errors.append("health.afflictions: должен быть списком")
                else:
                    known_affl = {a["identifier"] for a in (doll_reference().get("afflictions") or [])}
                    for i, a in enumerate(affl):
                        if not isinstance(a, dict):
                            errors.append(f"health.afflictions[{i}]: должен быть объектом")
                            continue
                        ident = (a.get("identifier") or "").strip()
                        if not ident:
                            errors.append(f"health.afflictions[{i}].identifier: обязательное поле")
                        elif known_affl and ident not in known_affl:
                            warnings.append(f"health.afflictions[{i}]: недуг «{ident}» не найден в контенте — проверьте идентификатор")
                        strength = a.get("strength")
                        if strength is not None and (isinstance(strength, bool) or not isinstance(strength, (int, float))):
                            errors.append(f"health.afflictions[{i}].strength: должно быть числом")
                        limb = a.get("limb")
                        if limb not in (None, "") and limb not in LIMB_TYPES:
                            errors.append(f"health.afflictions[{i}].limb: неизвестная конечность «{limb}»")

    # inventory
    inventory = spec.get("inventory")
    if inventory is not None:
        if not isinstance(inventory, dict):
            errors.append("inventory: должен быть объектом")
        else:
            _validate_items(inventory.get("items"), errors, warnings, [0])

    return errors, warnings


def validate_doll_meta(
    server: str,
    bind_steamids: Any,
    spawn_mode: str,
    expires_at: Optional[datetime],
    team: Any,
    priority: Any,
) -> Tuple[List[str], List[str], List[str]]:
    """Проверяет верхнеуровневые поля. Возвращает (ошибки, предупреждения,
    нормализованные steamid)."""
    errors: List[str] = []
    warnings: List[str] = []
    clean_ids: List[str] = []

    if not (server or "").strip():
        errors.append("Укажите сервер")

    if bind_steamids is not None:
        if not isinstance(bind_steamids, list):
            errors.append("bind_steamids: должен быть списком")
        else:
            for raw in bind_steamids:
                norm = normalize_steamid(raw)
                if norm is None:
                    errors.append(f"bind_steamids: «{raw}» не похож на SteamID64 (17 цифр)")
                elif norm not in clean_ids:
                    clean_ids.append(norm)

    if spawn_mode not in SPAWN_MODES:
        errors.append(f"spawn_mode: допустимы {', '.join(sorted(SPAWN_MODES))}")
    elif spawn_mode == "player" and not clean_ids:
        # Плагин такую куклу никогда не заспавнит — предупреждаем сразу.
        warnings.append("spawn_mode=player без привязанных SteamID: кукла никогда не заспавнится")

    if expires_at is not None and expires_at <= _now_msk():
        errors.append("expires_at: срок должен быть в будущем")

    if team is not None and (isinstance(team, bool) or not isinstance(team, int) or team not in (0, 1, 2)):
        errors.append("team: допустимы 0 (не трогать), 1 или 2")

    if priority is not None and (isinstance(priority, bool) or not isinstance(priority, int)):
        errors.append("priority: должен быть целым числом")

    return errors, warnings, clean_ids


def serialize_for_plugin(row) -> Dict[str, Any]:
    """Кукла в формате, который ждёт плагин (раздел 3 инструкции).

    Тело (identity/job/...) отдаётся как есть, сверху накладываются
    верхнеуровневые поля из колонок — они источник истины, а не то, что могло
    остаться в spec.
    """
    doll = dict(row.spec or {})
    doll.update({
        "id": row.public_id,
        "status": "pending",
        "label": row.label,
        "priority": row.priority or 0,
        "one_shot": bool(row.one_shot),
        # Плагин ждёт unix-секунды; 0 — бессрочно. row.expires_at — наивный
        # MSK-wall-clock, поэтому эпоху считаем через replace(tzinfo=MSK), а
        # не голым .timestamp() — тот трактовал бы наивное значение как
        # локальное время ЭТОГО процесса и на сервере с не-MSK TZ сдвинул бы
        # срок жизни куклы на офсет хоста (см. комментарий у MSK выше).
        "expires_at": int(row.expires_at.replace(tzinfo=MSK).timestamp()) if row.expires_at else 0,
        "bind_steamids": row.bind_steamids or [],
        "spawn_mode": row.spawn_mode or "auto",
        "team": row.team or 0,
    })
    return doll
