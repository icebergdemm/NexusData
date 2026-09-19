"""Shared ticket configuration normalization and validation.

The file on disk is still the bot's source of truth.  This module keeps the
legacy shape readable while giving the web editor and the Discord views a
single, explicit model for ticket types, buttons and Discord placements.
"""

from copy import deepcopy
from typing import Optional


DEFAULT_STATUS_CHANNEL_ID = "1507830881832931482"
DEFAULT_PUBLIC_PANEL_CHANNEL_ID = "1507830037338915017"
TECHNICAL_ADMIN_ROLE_IDS = frozenset({
    "1535306271568306216",
    "1536021581258821642",
    "1543720354520899817",
})

_DEFAULT_TYPE_FIELDS = {
    "emoji": "🎫",
    "color": "2b7af0",
    "template": "",
    "allow_cooldown": False,
}
_BUTTON_STYLES = {
    "help": "primary",
    "player_report": "secondary",
    "bug": "success",
    "admin_report": "danger",
    "department_report": "secondary",
    "appeal": "secondary",
    "construction_bureau": "secondary",
    "technical": "primary",
}


def _role_rules(type_config: dict) -> list[dict]:
    raw_rules = type_config.get("role_rules")
    if isinstance(raw_rules, list):
        result = []
        seen = set()
        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            role_id = str(item.get("role_id") or "").strip()
            if not role_id or role_id in seen:
                continue
            seen.add(role_id)
            result.append({
                "role_id": role_id,
                "access": bool(item.get("access", True)),
                "notify": bool(item.get("notify", True)),
            })
        return result

    ping_roles = type_config.get("ping_roles") or []
    return [
        {"role_id": str(role_id), "access": True, "notify": True}
        for role_id in ping_roles
        if str(role_id).strip()
    ]


def _legacy_ping_roles(rules: list[dict]) -> list[str]:
    return [rule["role_id"] for rule in rules if rule.get("notify")]


def _default_button(button_id: str, type_config: dict) -> dict:
    return {
        "ticket_type": button_id,
        "label": str(type_config.get("name") or button_id),
        "emoji": str(type_config.get("emoji") or "🎫"),
        "style": _BUTTON_STYLES.get(button_id, "secondary"),
        "enabled": True,
        "allowed_role_ids": sorted(TECHNICAL_ADMIN_ROLE_IDS) if button_id == "technical" else [],
    }


def normalize_tickets_config(config: object) -> dict:
    """Return a deep-copied v2 config while preserving legacy values."""
    raw = deepcopy(config) if isinstance(config, dict) else {}
    raw["schema_version"] = 2
    raw.setdefault("category_id", "")
    raw.setdefault("manager_role_ids", [])

    raw_types = raw.get("types") if isinstance(raw.get("types"), dict) else {}
    types: dict[str, dict] = {}
    for type_id, value in raw_types.items():
        if not isinstance(type_id, str) or not isinstance(value, dict):
            continue
        item = {**_DEFAULT_TYPE_FIELDS, **deepcopy(value)}
        item["name"] = str(item.get("name") or type_id)
        rules = _role_rules(item)
        item["role_rules"] = rules
        # Keep this alias until all old bot call sites are migrated.
        item["ping_roles"] = _legacy_ping_roles(rules)
        types[type_id] = item

    if "technical" not in types:
        technical = {**_DEFAULT_TYPE_FIELDS}
        technical.update({
            "name": "Технический тикет",
            "emoji": "🛠️",
            "color": "5865f2",
            "template": "Опишите цель технического обращения и приложите собранные материалы.",
        })
        technical["role_rules"] = [
            {"role_id": role_id, "access": True, "notify": True}
            for role_id in sorted(TECHNICAL_ADMIN_ROLE_IDS)
        ]
        technical["ping_roles"] = sorted(TECHNICAL_ADMIN_ROLE_IDS)
        types["technical"] = technical
    raw["types"] = types

    raw_buttons = raw.get("buttons") if isinstance(raw.get("buttons"), dict) else {}
    buttons: dict[str, dict] = {}
    for button_id, value in raw_buttons.items():
        if not isinstance(button_id, str) or not isinstance(value, dict):
            continue
        buttons[button_id] = deepcopy(value)
    for type_id, type_config in types.items():
        buttons.setdefault(type_id, _default_button(type_id, type_config))
    technical_button = buttons["technical"]
    technical_button.setdefault("allowed_role_ids", sorted(TECHNICAL_ADMIN_ROLE_IDS))
    technical_button.setdefault("label", "Создать технический тикет")
    technical_button.setdefault("emoji", "🛠️")
    technical_button.setdefault("style", "primary")
    technical_button.setdefault("enabled", True)
    technical_button.setdefault("ticket_type", "technical")
    raw["buttons"] = buttons

    status_channel_id = str(raw.get("status_channel_id") or DEFAULT_STATUS_CHANNEL_ID)
    raw["status_channel_id"] = status_channel_id
    public_channel_id = str(raw.get("public_panel_channel_id") or DEFAULT_PUBLIC_PANEL_CHANNEL_ID)
    raw["public_panel_channel_id"] = public_channel_id

    raw_placements = raw.get("placements") if isinstance(raw.get("placements"), dict) else {}
    placements = deepcopy(raw_placements)
    status = placements.get("status") if isinstance(placements.get("status"), dict) else {}
    status["channel_id"] = str(status.get("channel_id") or status_channel_id)
    status["kind"] = "ticket_status"
    status["button_ids"] = ["technical"] if "button_ids" not in status else list(status.get("button_ids") or [])
    placements["status"] = status

    public = placements.get("public_panel") if isinstance(placements.get("public_panel"), dict) else {}
    public["channel_id"] = str(public.get("channel_id") or public_channel_id)
    public["kind"] = "ticket_panel"
    if "button_ids" not in public:
        public["button_ids"] = [type_id for type_id in types if type_id != "technical"]
    placements["public_panel"] = public
    raw["placements"] = placements
    return raw


def validate_tickets_config(config: object) -> Optional[str]:
    if not isinstance(config, dict):
        return "Ожидается объект конфигурации"

    types = config.get("types")
    if not isinstance(types, dict) or not types:
        return "types: ожидается непустой объект типов обращений"
    for type_id, type_config in types.items():
        if not isinstance(type_id, str) or not type_id.strip():
            return "types: ключ типа обращения не может быть пустым"
        if not isinstance(type_config, dict):
            return f"types.{type_id}: ожидается объект"
        if not str(type_config.get("name") or "").strip():
            return f"types.{type_id}: название не может быть пустым"
        rules = type_config.get("role_rules", [])
        if not isinstance(rules, list):
            return f"types.{type_id}.role_rules: ожидается список"
        seen_roles = set()
        for rule in rules:
            if not isinstance(rule, dict) or not str(rule.get("role_id") or "").strip():
                return f"types.{type_id}.role_rules: некорректное правило роли"
            role_id = str(rule["role_id"])
            if role_id in seen_roles:
                return f"types.{type_id}.role_rules: повторяющаяся роль {role_id}"
            seen_roles.add(role_id)

    buttons = config.get("buttons")
    if not isinstance(buttons, dict):
        return "buttons: ожидается объект кнопок"
    for button_id, button in buttons.items():
        if not isinstance(button, dict):
            return f"buttons.{button_id}: ожидается объект"
        ticket_type = button.get("ticket_type")
        if ticket_type not in types:
            return f"buttons.{button_id}.ticket_type: тип не найден"
        if button.get("style") not in {"primary", "secondary", "success", "danger"}:
            return f"buttons.{button_id}.style: недопустимый стиль"
        allowed = button.get("allowed_role_ids", [])
        if not isinstance(allowed, list) or len(set(map(str, allowed))) != len(allowed):
            return f"buttons.{button_id}.allowed_role_ids: некорректный список ролей"

    placements = config.get("placements")
    if not isinstance(placements, dict):
        return "placements: ожидается объект панелей"
    for placement_id, placement in placements.items():
        if not isinstance(placement, dict):
            return f"placements.{placement_id}: ожидается объект"
        button_ids = placement.get("button_ids", [])
        if not isinstance(button_ids, list) or len(button_ids) > 25:
            return f"placements.{placement_id}.button_ids: максимум 25 кнопок"
        if len(set(button_ids)) != len(button_ids):
            return f"placements.{placement_id}.button_ids: повторяющаяся кнопка"
        missing = [button_id for button_id in button_ids if button_id not in buttons]
        if missing:
            return f"placements.{placement_id}.button_ids: кнопка не найдена"

    return None
