"""
Проверщик кораблей (.sub) по ГОСТу КБ «Гайка».

Порт консольного Submarine checker v0.96 (Team Zero Tools) в сервис сайта, с
двумя принципиальными отличиями:

  1. Ни одного зашитого числа. Все пороги, коэффициенты, формулы, списки
     запрещённых предметов, классы и их наполнение живут в конфигурации
     (таблица sub_checker_config, значения по умолчанию — sub_checker_defaults),
     которую правит суперпользователь или обладатель права
     sub_checker:manage_rules / sub_checker:manage_classes.
  2. Результат — структура, а не текст в консоли: сводка, метрики, список
     находок с уровнем важности и разбор по классу корабля.

Формулы вычисляются собственным вычислителем на ast (_safe_eval): eval() над
строкой из БД, которую правит человек через веб-интерфейс, здесь недопустим,
поэтому разрешены только арифметика, сравнения и белый список функций.
"""

from __future__ import annotations

import ast
import gzip
import math
import operator
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from xml.etree import ElementTree as ET

from sub_checker_defaults import DEFAULT_CONFIG

# ── Безопасный вычислитель выражений ─────────────────────────────────────────

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}

_FUNCS = {
    "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor,
    "abs": abs, "min": min, "max": max, "round": round,
    "int": int, "float": float, "log": math.log, "pow": pow,
}


class FormulaError(ValueError):
    """Выражение из конфигурации не удалось разобрать или вычислить."""


def _safe_eval(expr: str, ctx: dict[str, Any]) -> Any:
    """Вычислить арифметическое/логическое выражение над ctx.

    Разрешены: числа, имена из ctx, +-*/%//**, сравнения, and/or/not, тернарник
    и вызовы функций из _FUNCS. Всё остальное (атрибуты, индексы, лямбды,
    импорты, вызовы чего угодно ещё) — ошибка, а не выполнение.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"Синтаксическая ошибка в формуле «{expr}»: {e}") from e

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool)) or node.value is None:
                return node.value
            raise FormulaError("В формуле допустимы только числа")
        if isinstance(node, ast.Name):
            if node.id in ctx:
                return ctx[node.id]
            if node.id in _FUNCS:
                return _FUNCS[node.id]
            raise FormulaError(f"Неизвестная переменная «{node.id}»")
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.BoolOp):
            values = [ev(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                if type(op) not in _CMP_OPS:
                    raise FormulaError("Недопустимое сравнение")
                right = ev(comparator)
                if not _CMP_OPS[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return ev(node.body) if ev(node.test) else ev(node.orelse)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise FormulaError("Разрешены только функции: " + ", ".join(sorted(_FUNCS)))
            if node.keywords:
                raise FormulaError("Именованные аргументы в формулах не поддерживаются")
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise FormulaError(f"Недопустимая конструкция в формуле: {type(node).__name__}")

    return ev(tree)


def validate_expression(expr: str, sample_ctx: dict[str, Any]) -> Optional[str]:
    """None, если выражение разбирается и считается на пробном контексте,
    иначе текст ошибки — используется при сохранении настроек, чтобы
    сломанная формула не доехала до боевой проверки."""
    try:
        _safe_eval(expr, sample_ctx)
        return None
    except FormulaError as e:
        return str(e)
    except ZeroDivisionError:
        return None  # деление на ноль зависит от корабля, а не от формулы
    except Exception as e:  # noqa: BLE001 — любая ошибка вычисления информативна
        return str(e)


# ── Разбор .sub ──────────────────────────────────────────────────────────────

def _f(value: Optional[str], default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rect_area(rect: Optional[str]) -> int:
    """Площадь Hull по атрибуту rect="x,y,ширина,высота" (в см²)."""
    if not rect:
        return 0
    parts = rect.split(",")
    if len(parts) < 4:
        return 0
    return int(abs(abs(_f(parts[2])) * abs(_f(parts[3]))))


@dataclass
class ParsedSub:
    """Плоское представление корабля — то, что дальше проверяют правила."""
    name: str
    length_cm: float
    height_cm: float
    manually_outfitted: bool
    game_version: Optional[str]
    sub_class_attr: Optional[str]
    hulls_cm2: int
    ballast_cm2: int
    reactors: list[dict] = field(default_factory=list)
    engines: list[dict] = field(default_factory=list)
    pumps: list[dict] = field(default_factory=list)
    steering: list[dict] = field(default_factory=list)
    batteries: list[dict] = field(default_factory=list)
    oxygen_generators: list[dict] = field(default_factory=list)
    power_distributors: int = 0
    total_power_consumption: float = 0.0
    total_engine_force: float = 0.0
    autofill: list[dict] = field(default_factory=list)
    item_counts: dict[str, int] = field(default_factory=dict)
    interactable_items: dict[str, int] = field(default_factory=dict)
    indestructible_structures: list[str] = field(default_factory=list)
    emp_immune: list[dict] = field(default_factory=list)
    drones: list["ParsedSub"] = field(default_factory=list)
    item_count: int = 0


def parse_sub_bytes(raw: bytes) -> ET.Element:
    """.sub — это gzip'нутый XML. Файлы, сохранённые без сжатия, тоже
    встречаются (например, после ручной правки), поэтому распаковка
    необязательна."""
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError as e:
            raise ValueError(f"Не удалось распаковать .sub: {e}") from e
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError(f"Файл не является корректным .sub (XML): {e}") from e


def parse_submarine(root: ET.Element, config: dict, depth: int = 0) -> ParsedSub:
    """Разобрать <Submarine> (или <LinkedSubmarine>) в ParsedSub."""
    equipment = config.get("equipment", {})
    excluded_tags = set(
        equipment.get("power", {}).get("consumption_excluded_tags")
        or DEFAULT_CONFIG["equipment"]["power"]["consumption_excluded_tags"]
    )
    exempt_tags = config.get("autofill", {}).get("exempt_tags") or []

    # LinkedSubmarine вынимаются до обхода, чтобы их содержимое не смешалось
    # с содержимым корабля-носителя (как в оригинальном чекере).
    drone_nodes = root.findall("LinkedSubmarine")
    for node in drone_nodes:
        root.remove(node)

    parent_map = {child: parent for parent in root.iter() for child in parent}

    dimensions = (root.get("dimensions") or "0,0").split(",")
    length_cm = _f(dimensions[0] if dimensions else 0)
    height_cm = _f(dimensions[1] if len(dimensions) > 1 else 0)

    hulls_cm2 = 0
    ballast_cm2 = 0
    ballast_hull_ids: set[str] = set()
    for hull in root.findall("Hull"):
        area = _rect_area(hull.get("rect"))
        hulls_cm2 += area
        room = hull.get("RoomName") or ""
        if "roomname.ballasttank" in room or "roomname.droneballast" in room:
            ballast_cm2 += area
            if hull.get("ID"):
                ballast_hull_ids.add(hull.get("ID"))

    parsed = ParsedSub(
        name=root.get("name") or "Без имени",
        length_cm=length_cm,
        height_cm=height_cm,
        manually_outfitted=(root.get("ismanuallyoutfitted") or "").lower() == "true",
        game_version=root.get("gameversion"),
        sub_class_attr=root.get("class"),
        hulls_cm2=hulls_cm2,
        ballast_cm2=ballast_cm2,
    )

    items = root.findall("Item")
    parsed.item_count = len(items)
    for item in items:
        identifier = item.get("identifier") or ""
        parsed.item_counts[identifier] = parsed.item_counts.get(identifier, 0) + 1
        if (item.get("NonInteractable") or "").lower() == "false":
            parsed.interactable_items[identifier] = parsed.interactable_items.get(identifier, 0) + 1

    for structure in root.findall("Structure"):
        if (structure.get("Indestructible") or "").lower() == "true":
            parsed.indestructible_structures.append(structure.get("identifier") or "?")

    for reactor in root.findall("Item/Reactor"):
        owner = parent_map[reactor]
        parsed.reactors.append({
            "type": owner.get("identifier"),
            "scale": _f(owner.get("Scale"), 1.0),
            "max_power_output": _f(reactor.get("MaxPowerOutput")),
            "meltdown_delay": _f(reactor.get("MeltdownDelay")),
            "fire_delay": _f(reactor.get("FireDelay")),
            "fuel_consumption_rate": _f(reactor.get("FuelConsumptionRate")),
            "vulnerable_to_emp": (reactor.get("VulnerableToEMP") or "").lower() == "true",
            "power_consumption": _f(reactor.get("PowerConsumption")),
        })

    for engine in root.findall("Item/Engine"):
        owner = parent_map[engine]
        max_force = _f(engine.get("MaxForce"))
        parsed.total_engine_force += max_force
        parsed.engines.append({
            "type": owner.get("identifier"),
            "scale": _f(owner.get("Scale"), 1.0),
            "max_force": max_force,
            "disable_propeller_damage": (engine.get("DisablePropellerDamage") or "").lower() == "true",
            "power_consumption": _f(engine.get("PowerConsumption")),
            "min_voltage": _f(engine.get("MinVoltage")),
            "vulnerable_to_emp": (engine.get("VulnerableToEMP") or "").lower() == "true",
        })

    for pump in root.findall("Item/Pump"):
        owner = parent_map[pump]
        tags = (owner.get("Tags") or "").lower()
        # Насос считается балластным, если он лежит внутри балластного отсека
        # (linked) или уже помечен как ballast — второе как раз и проверяется.
        linked = set((owner.get("linked") or "").split(","))
        parsed.pumps.append({
            "type": owner.get("identifier"),
            "scale": _f(owner.get("Scale"), 1.0),
            "max_flow": _f(pump.get("MaxFlow")),
            "power_consumption": _f(pump.get("PowerConsumption")),
            "tags": tags,
            "in_ballast_room": bool(linked & ballast_hull_ids),
        })

    for steering in root.findall("Item/Steering"):
        owner = parent_map[steering]
        parsed.steering.append({
            "type": owner.get("identifier"),
            "neutral_ballast_level": _f(steering.get("NeutralBallastLevel"), -1.0),
        })

    for container in root.findall("Item/PowerContainer"):
        owner = parent_map[container]
        parsed.batteries.append({
            "type": owner.get("identifier"),
            "scale": _f(owner.get("Scale"), 1.0),
            "capacity": _f(container.get("Capacity")),
            "max_recharge_speed": _f(container.get("MaxRechargeSpeed")),
        })

    for generator in root.findall("Item/OxygenGenerator"):
        owner = parent_map[generator]
        parsed.oxygen_generators.append({
            "type": owner.get("identifier"),
            "generated_amount": _f(generator.get("GeneratedAmount")),
            "power_consumption": _f(generator.get("PowerConsumption")),
        })

    parsed.power_distributors = len(root.findall("Item/PowerDistributor"))

    for node in root.findall(".//*[@PowerConsumption]"):
        if node.tag not in excluded_tags:
            parsed.total_power_consumption += _f(node.get("PowerConsumption"))

    for node in root.iter():
        if (node.get("VulnerableToEMP") or "").lower() == "false":
            owner = parent_map.get(node)
            parsed.emp_immune.append({
                "component": node.tag,
                "item": (owner.get("identifier") if owner is not None else None) or "?",
            })

    for container in root.findall("Item/ItemContainer"):
        if (container.get("AutoFill") or "").lower() != "true":
            continue
        owner = parent_map[container]
        if (owner.get("NonInteractable") or "").lower() != "false":
            continue
        tags = owner.get("Tags") or ""
        if any(tag and tag in tags for tag in exempt_tags):
            continue
        parsed.autofill.append({"identifier": owner.get("identifier") or "?"})

    if config.get("options", {}).get("check_drones", True) and depth < 3:
        for node in drone_nodes:
            try:
                parsed.drones.append(parse_submarine(node, config, depth + 1))
            except Exception:  # noqa: BLE001 — битый дрон не должен ронять отчёт
                continue

    return parsed


# ── Метрики ──────────────────────────────────────────────────────────────────

_METRIC_ORDER = [
    "length_m", "height_m", "raw_area", "hull_area", "factor",
    "volume", "tonnage", "max_speed", "optimal_ballast", "power_load",
]


def _base_context(sub: ParsedSub) -> dict[str, Any]:
    return {
        "length_cm": sub.length_cm,
        "height_cm": sub.height_cm,
        "hulls_cm2": float(sub.hulls_cm2),
        "ballast_cm2": float(sub.ballast_cm2),
        "total_engine_force": sub.total_engine_force,
        "total_power_consumption": sub.total_power_consumption,
        "total_power_output": sum(r["max_power_output"] for r in sub.reactors),
        "reactor_count": float(len(sub.reactors)),
        "engine_count": float(len(sub.engines)),
        "pump_count": float(len(sub.pumps)),
        "steering_count": float(len(sub.steering)),
        "drone_count": float(len(sub.drones)),
        "item_count": float(sub.item_count),
    }


def compute_metrics(sub: ParsedSub, config: dict) -> tuple[dict[str, Any], list[str]]:
    """Посчитать все формулы из конфигурации по порядку зависимостей.

    Формула, которая не посчиталась (деление на ноль на корабле с нулевыми
    габаритами, опечатка в выражении), даёт None и запись в списке проблем —
    остальные метрики при этом считаются дальше.
    """
    formulas = config.get("formulas") or DEFAULT_CONFIG["formulas"]
    ctx = _base_context(sub)
    problems: list[str] = []

    # Сначала известный порядок (метрики ссылаются друг на друга), потом всё
    # остальное, что администратор мог добавить своими руками.
    keys = [k for k in _METRIC_ORDER if k in formulas]
    keys += [k for k in formulas if k not in keys]

    for key in keys:
        spec = formulas[key]
        expr = spec.get("expr") if isinstance(spec, dict) else str(spec)
        if not expr:
            ctx[key] = None
            continue
        try:
            value = _safe_eval(expr, ctx)
        except ZeroDivisionError:
            value = None
            problems.append(f"Метрика «{_label(spec, key)}»: деление на ноль")
        except FormulaError as e:
            value = None
            problems.append(f"Метрика «{_label(spec, key)}»: {e}")
        except Exception as e:  # noqa: BLE001
            value = None
            problems.append(f"Метрика «{_label(spec, key)}»: {e}")
        ctx[key] = value

    return ctx, problems


def _label(spec: Any, key: str) -> str:
    return spec.get("label") or key if isinstance(spec, dict) else key


# ── Правила ──────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    rule_id: str
    severity: str          # error | warning | info
    title: str
    detail: str = ""
    category: str = "general"
    subject: str = ""      # к чему относится (тип оборудования, предмет)

    def as_dict(self) -> dict:
        return {
            "rule_id": self.rule_id, "severity": self.severity, "title": self.title,
            "detail": self.detail, "category": self.category, "subject": self.subject,
        }


class _RuleSet:
    """Обёртка над списком правил из конфигурации: включено ли правило и с
    каким уровнем важности сообщать о нарушении."""

    def __init__(self, config: dict):
        self._rules = {}
        for rule in config.get("rules") or DEFAULT_CONFIG["rules"]:
            self._rules[rule.get("id")] = rule

    def enabled(self, rule_id: str) -> bool:
        rule = self._rules.get(rule_id)
        return bool(rule and rule.get("enabled", True))

    def severity(self, rule_id: str, fallback: str = "error") -> str:
        rule = self._rules.get(rule_id)
        return (rule.get("severity") if rule else None) or fallback

    def title(self, rule_id: str, fallback: str = "") -> str:
        rule = self._rules.get(rule_id)
        return (rule.get("label") if rule else None) or fallback or rule_id

    def category(self, rule_id: str) -> str:
        rule = self._rules.get(rule_id)
        return (rule.get("category") if rule else None) or "general"


def _by_type(mapping: dict, type_name: Optional[str], default: Any) -> Any:
    if not mapping:
        return default
    return mapping.get(type_name or "", default)


def _check_reactors(sub: ParsedSub, cfg: dict, rules: _RuleSet) -> list[Finding]:
    out: list[Finding] = []
    spec = cfg.get("equipment", {}).get("reactors") or DEFAULT_CONFIG["equipment"]["reactors"]
    for reactor in sub.reactors:
        name = reactor["type"] or "реактор"
        base_power = _by_type(spec.get("base_power_by_type"), reactor["type"], spec.get("base_power_default", 5000.0))
        if rules.enabled("reactor_scale") and base_power:
            try:
                required = _safe_eval(spec.get("min_scale_expr", ""), {
                    "max_power_output": reactor["max_power_output"], "base_power": base_power,
                    "scale": reactor["scale"],
                })
            except Exception as e:  # noqa: BLE001
                out.append(Finding("reactor_scale", "warning", "Формула масштаба реактора не вычислена",
                                   str(e), rules.category("reactor_scale"), name))
            else:
                if reactor["scale"] < required:
                    out.append(Finding(
                        "reactor_scale", rules.severity("reactor_scale"),
                        "Неверный масштаб реактора",
                        f"Scale {reactor['scale']:.2f} при мощности {reactor['max_power_output']:.0f} кВт; "
                        f"минимум {required:.2f}",
                        rules.category("reactor_scale"), name))
        if rules.enabled("reactor_fuel_rate"):
            minimum = float(spec.get("min_fuel_consumption_rate", 0.2))
            if reactor["fuel_consumption_rate"] < minimum:
                out.append(Finding(
                    "reactor_fuel_rate", rules.severity("reactor_fuel_rate"),
                    "Заниженный расход топлива",
                    f"FuelConsumptionRate {reactor['fuel_consumption_rate']:g}, минимум {minimum:g}",
                    rules.category("reactor_fuel_rate"), name))
        if rules.enabled("reactor_emp") and not reactor["vulnerable_to_emp"]:
            out.append(Finding(
                "reactor_emp", rules.severity("reactor_emp"), "Реактор не повреждается от ЭМИ",
                "VulnerableToEMP=False", rules.category("reactor_emp"), name))
    return out


def _check_engines(sub: ParsedSub, cfg: dict, rules: _RuleSet) -> list[Finding]:
    out: list[Finding] = []
    spec = cfg.get("equipment", {}).get("engines") or DEFAULT_CONFIG["equipment"]["engines"]
    min_scale = float(spec.get("min_scale", 0.5))
    for engine in sub.engines:
        name = engine["type"] or "двигатель"
        max_force = _by_type(spec.get("max_force_by_type"), engine["type"], spec.get("max_force_default", 50.0))
        power_factor = _by_type(spec.get("power_factor_by_type"), engine["type"], spec.get("power_factor_default", 35.0))

        if rules.enabled("engine_scale") and engine["scale"] < min_scale:
            out.append(Finding("engine_scale", rules.severity("engine_scale"), "Неверный масштаб двигателя",
                               f"Scale {engine['scale']:.2f}, минимум {min_scale:g}",
                               rules.category("engine_scale"), name))
        if rules.enabled("engine_force") and engine["max_force"] > max_force:
            out.append(Finding("engine_force", rules.severity("engine_force"), "Превышена максимальная сила",
                               f"MaxForce {engine['max_force']:g}, предел {max_force:g}",
                               rules.category("engine_force"), name))
        if rules.enabled("engine_power"):
            try:
                required = _safe_eval(spec.get("min_power_expr", ""), {
                    "max_force": engine["max_force"], "power_factor": power_factor,
                    "power_consumption": engine["power_consumption"],
                })
            except Exception as e:  # noqa: BLE001
                out.append(Finding("engine_power", "warning", "Формула потребления двигателя не вычислена",
                                   str(e), rules.category("engine_power"), name))
            else:
                if engine["power_consumption"] < required:
                    out.append(Finding(
                        "engine_power", rules.severity("engine_power"), "Заниженное потребление двигателя",
                        f"PowerConsumption {engine['power_consumption']:g}, минимум {required:g}",
                        rules.category("engine_power"), name))
        if rules.enabled("engine_propeller_damage") and engine["disable_propeller_damage"]:
            out.append(Finding("engine_propeller_damage", rules.severity("engine_propeller_damage"),
                               "Винт не наносит урон", "DisablePropellerDamage=True",
                               rules.category("engine_propeller_damage"), name))
        if rules.enabled("engine_emp") and not engine["vulnerable_to_emp"]:
            out.append(Finding("engine_emp", rules.severity("engine_emp"), "Двигатель не повреждается от ЭМИ",
                               "VulnerableToEMP=False", rules.category("engine_emp"), name))
    return out


def _check_pumps(sub: ParsedSub, cfg: dict, rules: _RuleSet) -> list[Finding]:
    out: list[Finding] = []
    spec = cfg.get("equipment", {}).get("pumps") or DEFAULT_CONFIG["equipment"]["pumps"]
    allowed = [t for t in (spec.get("allowed_types") or []) if t]
    ballast_tag = (spec.get("ballast_tag") or "ballast").lower()

    for pump in sub.pumps:
        name = pump["type"] or "насос"
        base_flow = _by_type(spec.get("base_flow_by_type"), pump["type"], spec.get("base_flow_default", 100.0))
        power_factor = _by_type(spec.get("power_factor_by_type"), pump["type"], spec.get("power_factor_default", 0.6))

        if rules.enabled("pump_allowed_types") and allowed and pump["type"] not in allowed:
            out.append(Finding("pump_allowed_types", rules.severity("pump_allowed_types"),
                               "Недопустимый тип насоса",
                               f"Разрешены: {', '.join(allowed)}", rules.category("pump_allowed_types"), name))
        if rules.enabled("pump_scale") and base_flow:
            try:
                required = _safe_eval(spec.get("min_scale_expr", ""), {
                    "max_flow": pump["max_flow"], "base_flow": base_flow, "scale": pump["scale"],
                })
            except Exception as e:  # noqa: BLE001
                out.append(Finding("pump_scale", "warning", "Формула масштаба насоса не вычислена",
                                   str(e), rules.category("pump_scale"), name))
            else:
                if pump["scale"] < required:
                    out.append(Finding("pump_scale", rules.severity("pump_scale"), "Неверный масштаб насоса",
                                       f"Scale {pump['scale']:.2f} при MaxFlow {pump['max_flow']:g}; "
                                       f"минимум {required:.2f}",
                                       rules.category("pump_scale"), name))
        if rules.enabled("pump_power"):
            try:
                required = _safe_eval(spec.get("min_power_expr", ""), {
                    "max_flow": pump["max_flow"], "power_factor": power_factor,
                    "power_consumption": pump["power_consumption"],
                })
            except Exception as e:  # noqa: BLE001
                out.append(Finding("pump_power", "warning", "Формула потребления насоса не вычислена",
                                   str(e), rules.category("pump_power"), name))
            else:
                if pump["power_consumption"] < required:
                    out.append(Finding("pump_power", rules.severity("pump_power"),
                                       "Заниженное потребление насоса",
                                       f"PowerConsumption {pump['power_consumption']:g}, минимум {required:g}",
                                       rules.category("pump_power"), name))
        if rules.enabled("ballast_pump_tag") and pump["in_ballast_room"] and ballast_tag not in pump["tags"]:
            out.append(Finding("ballast_pump_tag", rules.severity("ballast_pump_tag"),
                               "Балластный насос без метки",
                               f"Насос стоит в балластном отсеке, но метки «{ballast_tag}» нет",
                               rules.category("ballast_pump_tag"), name))
    return out


def _check_power(sub: ParsedSub, cfg: dict, rules: _RuleSet, metrics: dict) -> list[Finding]:
    out: list[Finding] = []
    spec = cfg.get("equipment", {}).get("power") or DEFAULT_CONFIG["equipment"]["power"]
    battery_spec = cfg.get("equipment", {}).get("batteries") or DEFAULT_CONFIG["equipment"]["batteries"]

    if rules.enabled("power_distributors"):
        limit = int(spec.get("max_power_distributors", 1))
        if sub.power_distributors > limit:
            out.append(Finding("power_distributors", rules.severity("power_distributors"),
                               "Слишком много распределительных щитков",
                               f"Найдено {sub.power_distributors}, допускается {limit}",
                               rules.category("power_distributors")))

    if rules.enabled("power_load"):
        load = metrics.get("power_load")
        if load is None:
            out.append(Finding("power_load", "warning", "Загрузка энергосистемы не рассчитана",
                               "Суммарная мощность реакторов равна нулю", rules.category("power_load")))
        else:
            low = float(spec.get("power_load_min", 0.5))
            high = float(spec.get("power_load_max", 1.0))
            if load < low or load > high:
                out.append(Finding("power_load", rules.severity("power_load", "warning"),
                                   "Мощность реактора вне рекомендованных рамок",
                                   f"Загрузка {load:.2f} при рекомендуемом диапазоне {low:g}–{high:g}",
                                   rules.category("power_load")))

    if rules.enabled("battery_scale"):
        tolerance = float(battery_spec.get("scale_tolerance", 0.01))
        for battery in sub.batteries:
            name = battery["type"] or "накопитель"
            base = _by_type(battery_spec.get("base_capacity_by_type"), battery["type"],
                            battery_spec.get("base_capacity_default", 1000.0))
            if not base:
                continue
            try:
                required = _safe_eval(battery_spec.get("min_scale_expr", ""), {
                    "capacity": battery["capacity"], "base_capacity": base, "scale": battery["scale"],
                })
            except Exception as e:  # noqa: BLE001
                out.append(Finding("battery_scale", "warning", "Формула масштаба накопителя не вычислена",
                                   str(e), rules.category("battery_scale"), name))
                continue
            if battery["scale"] + tolerance < required:
                out.append(Finding("battery_scale", rules.severity("battery_scale"),
                                   "Ёмкость накопителя не соответствует размеру",
                                   f"Scale {battery['scale']:.2f} при ёмкости {battery['capacity']:g}; "
                                   f"минимум {required:.2f}",
                                   rules.category("battery_scale"), name))
    return out


def _check_oxygen(sub: ParsedSub, cfg: dict, rules: _RuleSet) -> list[Finding]:
    if not rules.enabled("oxygen_generator"):
        return []
    spec = cfg.get("equipment", {}).get("oxygen_generators") or DEFAULT_CONFIG["equipment"]["oxygen_generators"]
    tiers = sorted(spec.get("tiers") or [], key=lambda t: float(t.get("max_output", 0)))
    out: list[Finding] = []
    for generator in sub.oxygen_generators:
        name = generator["type"] or "генератор кислорода"
        tier = next((t for t in tiers if generator["generated_amount"] <= float(t.get("max_output", 0))), None)
        if tier is None:
            out.append(Finding("oxygen_generator", rules.severity("oxygen_generator"),
                               "Производство кислорода вне таблицы ГОСТа",
                               f"GeneratedAmount {generator['generated_amount']:g} превышает все ступени",
                               rules.category("oxygen_generator"), name))
            continue
        required = float(tier.get("min_power", 0))
        if generator["power_consumption"] < required:
            out.append(Finding("oxygen_generator", rules.severity("oxygen_generator"),
                               "Заниженное потребление генератора кислорода",
                               f"PowerConsumption {generator['power_consumption']:g} при производстве "
                               f"{generator['generated_amount']:g}; минимум {required:g}",
                               rules.category("oxygen_generator"), name))
    return out


def _check_general(sub: ParsedSub, cfg: dict, rules: _RuleSet, metrics: dict) -> list[Finding]:
    out: list[Finding] = []

    if rules.enabled("dimensions"):
        if sub.length_cm <= 0 or sub.height_cm <= 0:
            out.append(Finding("dimensions", rules.severity("dimensions"), "Некорректные габариты корабля",
                               f"dimensions = {sub.length_cm:g},{sub.height_cm:g}", rules.category("dimensions")))
        elif sub.hulls_cm2 <= 0:
            out.append(Finding("dimensions", rules.severity("dimensions"), "У корабля нет отсеков",
                               "Сумма площадей Hull равна нулю — тоннаж и класс рассчитать нельзя",
                               rules.category("dimensions")))

    if rules.enabled("manually_outfitted") and not sub.manually_outfitted:
        out.append(Finding("manually_outfitted", rules.severity("manually_outfitted"),
                           "Не установлена галочка «Снабжение вручную»",
                           "ismanuallyoutfitted=false — корабль будет наполнен игрой автоматически",
                           rules.category("manually_outfitted")))

    if rules.enabled("indestructible_walls") and sub.indestructible_structures:
        counts: dict[str, int] = {}
        for identifier in sub.indestructible_structures:
            counts[identifier] = counts.get(identifier, 0) + 1
        detail = ", ".join(f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:15])
        out.append(Finding("indestructible_walls", rules.severity("indestructible_walls"),
                           f"Неразрушаемые структуры: {len(sub.indestructible_structures)}",
                           detail, rules.category("indestructible_walls")))

    if rules.enabled("ballast_level"):
        optimal = metrics.get("optimal_ballast")
        if optimal is not None:
            for steering in sub.steering:
                actual = steering["neutral_ballast_level"]
                if actual < 0:
                    continue
                if abs(actual - optimal) > 0.005:
                    out.append(Finding("ballast_level", rules.severity("ballast_level", "warning"),
                                       "Уровень балласта отличается от расчётного",
                                       f"NeutralBallastLevel {actual:.4f}, оптимально {optimal:.4f}",
                                       rules.category("ballast_level"), steering["type"] or "терминал"))

    if rules.enabled("equipment_emp") and sub.emp_immune:
        counts: dict[str, int] = {}
        for entry in sub.emp_immune:
            key = f"{entry['item']} ({entry['component']})"
            counts[key] = counts.get(key, 0) + 1
        detail = ", ".join(f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:20])
        out.append(Finding("equipment_emp", rules.severity("equipment_emp"),
                           f"Оборудование без урона от ЭМИ: {len(sub.emp_immune)}",
                           detail, rules.category("equipment_emp")))

    if rules.enabled("autofill") and sub.autofill:
        # Одной находкой, а не по находке на идентификатор: «Автозаполнение»
        # включено по умолчанию у доброй половины игровых префабов, и разбитый
        # по предметам список из четырёх десятков строк топит собой весь
        # остальной отчёт.
        counts: dict[str, int] = {}
        for entry in sub.autofill:
            counts[entry["identifier"]] = counts.get(entry["identifier"], 0) + 1
        detail = ", ".join(f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        out.append(Finding("autofill", rules.severity("autofill"),
                           f"Включено автозаполнение: {len(sub.autofill)} контейнер(ов)",
                           detail, rules.category("autofill")))

    if rules.enabled("forbidden_items"):
        for entry in cfg.get("forbidden_items") or []:
            if not entry.get("enabled", True):
                continue
            needle = (entry.get("identifier") or "").lower()
            if not needle:
                continue
            total = sum(count for ident, count in sub.interactable_items.items() if needle in ident.lower())
            if total:
                label = entry.get("label") or needle
                out.append(Finding("forbidden_items", rules.severity("forbidden_items"),
                                   "Запрещённый предмет", f"{label} — {total} шт.",
                                   rules.category("forbidden_items"), needle))
    return out


def _check_custom(cfg: dict, metrics: dict) -> list[Finding]:
    out: list[Finding] = []
    for rule in cfg.get("custom_rules") or []:
        if not rule.get("enabled", True):
            continue
        expr = rule.get("expr") or ""
        if not expr:
            continue
        label = rule.get("label") or rule.get("id") or "Пользовательское правило"
        try:
            ok = bool(_safe_eval(expr, metrics))
        except Exception as e:  # noqa: BLE001
            out.append(Finding(rule.get("id") or "custom", "warning",
                               f"Правило «{label}» не вычислено", str(e), "custom"))
            continue
        if not ok:
            out.append(Finding(rule.get("id") or "custom", rule.get("severity") or "warning",
                               label, rule.get("message") or f"Не выполнено условие: {expr}", "custom"))
    return out


# ── Класс корабля ────────────────────────────────────────────────────────────

def resolve_class(tonnage: Optional[float], cfg: dict) -> Optional[dict]:
    if tonnage is None:
        return None
    for entry in cfg.get("classes") or []:
        if not entry.get("enabled", True):
            continue
        low = entry.get("min_tonnage")
        high = entry.get("max_tonnage")
        if low is not None and tonnage < float(low):
            continue
        if high is not None and tonnage >= float(high):
            continue
        return entry
    return None


def _check_class(sub: ParsedSub, cfg: dict, rules: _RuleSet, metrics: dict) -> tuple[list[Finding], Optional[dict]]:
    tonnage = metrics.get("tonnage")
    ship_class = resolve_class(tonnage, cfg)
    out: list[Finding] = []

    if rules.enabled("class_match") and ship_class is None:
        detail = ("Тоннаж не рассчитан" if tonnage is None
                  else f"Тоннаж {tonnage:.0f} т не попадает ни в один настроенный класс")
        out.append(Finding("class_match", rules.severity("class_match"),
                           "Класс корабля не определён", detail, rules.category("class_match")))
        return out, None

    if ship_class is None:
        return out, None

    class_report: dict[str, Any] = {
        "key": ship_class.get("key"),
        "name": ship_class.get("name"),
        "min_tonnage": ship_class.get("min_tonnage"),
        "max_tonnage": ship_class.get("max_tonnage"),
        "tonnage": tonnage,
        "requirements": ship_class.get("requirements") or {},
        "groups": [],
    }

    if rules.enabled("class_items"):
        severity = rules.severity("class_items")
        for group in ship_class.get("item_groups") or []:
            if not group.get("enabled", True):
                continue
            rows = []
            for requirement in group.get("items") or []:
                if not requirement.get("enabled", True):
                    continue
                identifier = requirement.get("identifier") or ""
                actual = sub.item_counts.get(identifier, 0)
                minimum = requirement.get("min")
                maximum = requirement.get("max")
                ok = True
                if minimum is not None and actual < int(minimum):
                    ok = False
                if maximum is not None and actual > int(maximum):
                    ok = False
                rows.append({
                    "identifier": identifier,
                    "label": requirement.get("label") or identifier,
                    "required": minimum, "max": maximum, "actual": actual, "ok": ok,
                })
                if not ok:
                    limit = (f"не более {maximum}" if minimum is None
                             else (f"{minimum}–{maximum}" if maximum is not None else f"не менее {minimum}"))
                    out.append(Finding("class_items", severity,
                                       f"{group.get('label') or 'Наполнение'}: {requirement.get('label') or identifier}",
                                       f"на корабле {actual} шт., требуется {limit}",
                                       "class", identifier))
            class_report["groups"].append({
                "key": group.get("key"), "label": group.get("label"), "items": rows,
                "ok": all(r["ok"] for r in rows) if rows else True,
            })

    return out, class_report


# ── Точка входа ──────────────────────────────────────────────────────────────

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _round(value: Any, digits: int = 2) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def _report_for(sub: ParsedSub, cfg: dict, rules: _RuleSet, is_drone: bool = False) -> dict:
    metrics, metric_problems = compute_metrics(sub, cfg)

    findings: list[Finding] = []
    findings += _check_general(sub, cfg, rules, metrics)
    findings += _check_reactors(sub, cfg, rules)
    findings += _check_engines(sub, cfg, rules)
    findings += _check_pumps(sub, cfg, rules)
    findings += _check_power(sub, cfg, rules, metrics)
    findings += _check_oxygen(sub, cfg, rules)
    findings += _check_custom(cfg, metrics)

    class_report = None
    if not is_drone:
        class_findings, class_report = _check_class(sub, cfg, rules, metrics)
        findings += class_findings

    for problem in metric_problems:
        findings.append(Finding("formula", "warning", "Метрика не рассчитана", problem, "formulas"))

    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.category, f.title))

    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")

    return {
        "name": sub.name,
        "is_drone": is_drone,
        "passed": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "infos": sum(1 for f in findings if f.severity == "info"),
        "metrics": {
            key: _round(metrics.get(key))
            for key in list(cfg.get("formulas") or DEFAULT_CONFIG["formulas"])
        },
        "raw": {
            "length_cm": sub.length_cm, "height_cm": sub.height_cm,
            "hulls_cm2": sub.hulls_cm2, "ballast_cm2": sub.ballast_cm2,
            "total_engine_force": _round(sub.total_engine_force),
            "total_power_consumption": _round(sub.total_power_consumption),
            "total_power_output": _round(sum(r["max_power_output"] for r in sub.reactors)),
            "game_version": sub.game_version,
            "sub_class_attr": sub.sub_class_attr,
            "manually_outfitted": sub.manually_outfitted,
            "item_count": sub.item_count,
        },
        "equipment": {
            "reactors": sub.reactors, "engines": sub.engines, "pumps": sub.pumps,
            "steering": sub.steering, "batteries": sub.batteries,
            "oxygen_generators": sub.oxygen_generators,
            "power_distributors": sub.power_distributors,
        },
        "class": class_report,
        "findings": [f.as_dict() for f in findings],
    }


def check_sub_file(raw: bytes, config: dict, filename: str = "") -> dict:
    """Проверить .sub-файл целиком. Возвращает готовый к отдаче отчёт."""
    cfg = merge_config(config)
    root = parse_sub_bytes(raw)
    rules = _RuleSet(cfg)

    sub = parse_submarine(root, cfg)
    report = _report_for(sub, cfg, rules)

    drones = [_report_for(drone, cfg, rules, is_drone=True) for drone in sub.drones]
    report["drones"] = drones
    report["filename"] = filename
    report["manual_checks"] = [
        {"id": c.get("id"), "label": c.get("label"), "gost": c.get("gost", "")}
        for c in (cfg.get("manual_checks") or []) if c.get("enabled", True)
    ]

    total_errors = report["errors"] + sum(d["errors"] for d in drones)
    total_warnings = report["warnings"] + sum(d["warnings"] for d in drones)
    report["total_errors"] = total_errors
    report["total_warnings"] = total_warnings
    report["passed"] = total_errors == 0
    report["summary"] = _summary(report["name"], total_errors, total_warnings)
    return report


def _summary(name: str, errors: int, warnings: int) -> str:
    if errors == 0 and warnings == 0:
        return f"«{name}» соответствует ГОСТу: нарушений не найдено."
    parts = []
    if errors:
        parts.append(f"{errors} нарушени{'е' if errors == 1 else ('я' if errors < 5 else 'й')}")
    if warnings:
        parts.append(f"{warnings} предупреждени{'е' if warnings == 1 else ('я' if warnings < 5 else 'й')}")
    return f"«{name}»: " + ", ".join(parts) + "."


# ── Конфигурация ─────────────────────────────────────────────────────────────

def merge_config(config: Optional[dict]) -> dict:
    """Дополнить сохранённую конфигурацию заводскими значениями на верхнем
    уровне. Нужно, чтобы конфиг, сохранённый до появления нового раздела,
    не ронял проверку отсутствующим ключом."""
    merged = dict(DEFAULT_CONFIG)
    if not config:
        return merged
    merged.update({k: v for k, v in config.items() if v is not None})
    for key in ("equipment", "formulas", "options", "autofill"):
        base = dict(DEFAULT_CONFIG.get(key) or {})
        base.update(merged.get(key) or {})
        merged[key] = base
    return merged


SAMPLE_CONTEXT = {
    # Пробный контекст для проверки формул при сохранении настроек.
    "length_cm": 4698.0, "height_cm": 1347.0, "hulls_cm2": 3_000_000.0,
    "ballast_cm2": 200_000.0, "total_engine_force": 100.0,
    "total_power_consumption": 4000.0, "total_power_output": 5000.0,
    "reactor_count": 1.0, "engine_count": 2.0, "pump_count": 5.0,
    "steering_count": 1.0, "drone_count": 0.0, "item_count": 900.0,
    "length_m": 46.98, "height_m": 13.47, "raw_area": 632.8, "hull_area": 300.0,
    "factor": 2.1, "volume": 19000.0, "tonnage": 9000.0, "max_speed": 30.0,
    "optimal_ballast": 1.05, "power_load": 0.8,
    # Контексты частных формул оборудования.
    "max_power_output": 5000.0, "base_power": 5000.0, "scale": 0.5,
    "max_force": 50.0, "power_factor": 35.0, "power_consumption": 1750.0,
    "max_flow": 300.0, "base_flow": 300.0, "capacity": 1000.0, "base_capacity": 1000.0,
}

_EXPRESSION_FIELDS = [
    ("equipment", "reactors", "min_scale_expr"),
    ("equipment", "engines", "min_power_expr"),
    ("equipment", "pumps", "min_scale_expr"),
    ("equipment", "pumps", "min_power_expr"),
    ("equipment", "batteries", "min_scale_expr"),
]


def validate_config(config: dict) -> list[str]:
    """Проверить конфигурацию перед сохранением. Возвращает список ошибок —
    пустой список означает, что настройки можно записывать."""
    errors: list[str] = []

    for key, spec in (config.get("formulas") or {}).items():
        expr = spec.get("expr") if isinstance(spec, dict) else str(spec)
        if not expr:
            errors.append(f"Формула «{key}»: выражение не задано")
            continue
        problem = validate_expression(expr, SAMPLE_CONTEXT)
        if problem:
            errors.append(f"Формула «{key}»: {problem}")

    for section, block, field_name in _EXPRESSION_FIELDS:
        expr = ((config.get(section) or {}).get(block) or {}).get(field_name)
        if expr:
            problem = validate_expression(expr, SAMPLE_CONTEXT)
            if problem:
                errors.append(f"{block}.{field_name}: {problem}")

    for rule in config.get("custom_rules") or []:
        expr = rule.get("expr")
        if not expr:
            errors.append(f"Правило «{rule.get('label') or rule.get('id')}»: выражение не задано")
            continue
        problem = validate_expression(expr, SAMPLE_CONTEXT)
        if problem:
            errors.append(f"Правило «{rule.get('label') or rule.get('id')}»: {problem}")

    seen_class_keys = set()
    for entry in config.get("classes") or []:
        key = entry.get("key")
        if not key:
            errors.append("У класса корабля не задан ключ")
        elif key in seen_class_keys:
            errors.append(f"Ключ класса «{key}» повторяется")
        else:
            seen_class_keys.add(key)
        low, high = entry.get("min_tonnage"), entry.get("max_tonnage")
        if low is not None and high is not None and float(low) >= float(high):
            errors.append(f"Класс «{entry.get('name') or key}»: нижняя граница тоннажа не меньше верхней")

    return errors


_IDENT_RE = re.compile(r"^[a-zA-Z0-9_.\-]+$")


def extract_identifiers(raw: bytes) -> list[dict]:
    """Список идентификаторов предметов в файле с количествами — подсказка
    для настройки требований по классу («что вообще бывает в наших .sub»)."""
    root = parse_sub_bytes(raw)
    counts: dict[str, int] = {}
    for item in root.iter("Item"):
        identifier = item.get("identifier") or ""
        if identifier and _IDENT_RE.match(identifier):
            counts[identifier] = counts.get(identifier, 0) + 1
    return [{"identifier": k, "count": v} for k, v in sorted(counts.items())]
