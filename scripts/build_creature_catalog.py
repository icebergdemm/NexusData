"""Rebuild static/creature_catalog.json from vanilla Barotrauma Content/Characters.

Usage (from repo root):
    python scripts/build_creature_catalog.py [path/to/Content/Characters] [output]

Defaults:
    source: C:\\Users\\icegergert\\Documents\\BarotraumaWorkBench\\ASPER\\MorePlugins_Barotrauma\\LuaCsForBarotrauma_source\\Barotrauma\\Content\\Characters
    output: static/creature_catalog.json (next to repo root)

Reads every <Character speciesname="..."> XML (top-level species folders plus
Content/Characters/Variants overrides), extracting the same fields the wave
director UI consumes: base vitality, AI params and target priorities.
Attribute matching is case-insensitive; booleans accept true/True/1/yes.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path(
    r"C:\Users\icegergert\Documents\BarotraumaWorkBench\ASPER\MorePlugins_Barotrauma"
    r"\LuaCsForBarotrauma_source\Barotrauma\Content\Characters"
)
DEFAULT_OUTPUT = REPO_ROOT / "static" / "creature_catalog.json"

# Vanilla Russian display names live in Content/Texts/Russian/RussianVanilla.xml
# as <character.<species-id>> tags; resolved relative to the Characters folder.
TEXTS_RU = Path("..") / "Texts" / "Russian" / "RussianVanilla.xml"

# Fallback Russian names for species without an official <character.*> string.
# Official strings always win; this only covers the gaps.
NAME_RU_FALLBACK = {
    "ancient": "Древний",
    "balloon": "Шар",
    "carrier": "Носитель",
    "crawlerhusk": "Ползун-хаск",
    "cyborgworm": "Киборг-червь",
    "guardianrepairbot": "Бот-ремонтник",
    "husk_chimera": "Хаск-химера",
    "husk_exosuit": "Хаск в экзокостюме",
    "husk_prowler": "Хаск-рыскатель",
    "huskcontainer": "Контейнер хаска",
    "jove": "Джов",
    "portalguardian": "Страж портала",
}

# Variant filename suffixes mapped to Russian qualifier labels.
VARIANT_SUFFIX_RU = [
    ("_mnamed", "именная особь"),
    ("_hatchling", "детёныш"),
    ("_morbusine", "морбузиновая особь"),
    ("_unarmored", "без брони"),
    ("_veteran", "ветеран"),
    ("_passive", "пассивная особь"),
    ("_large", "крупная особь"),
    ("_gold", "золотая особь"),
    ("_emp", "ЭМИ-вариант"),
    ("_pet", "питомец"),
    ("_m", "вариант миссии"),
]

# Folders that never contain a spawnable species definition themselves.
SKIP_DIRS = {"animations", "ragdolls"}

# XML roots that define a spawnable species: base definitions plus mission
# variants (<Charactervariant inherit="...">, e.g. Doomworm).
SPECIES_ROOTS = {"character", "charactervariant"}


def _attrs(element: ET.Element) -> dict[str, str]:
    return {k.lower(): v for k, v in element.attrib.items()}


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"true", "1", "yes"}


def _parse_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _is_character_file(path: Path) -> bool:
    """True when the XML root is a <Character> species definition."""
    if path.suffix.lower() != ".xml":
        return False
    if any(part.lower() in SKIP_DIRS for part in path.parts):
        return False
    try:
        with open(path, encoding="utf-8-sig") as source:
            root = ET.fromstring(source.read())
    except (OSError, ET.ParseError):
        return False
    return root.tag.lower() in SPECIES_ROOTS


def _parse_species(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8-sig") as source:
            root = ET.fromstring(source.read())
    except (OSError, ET.ParseError):
        return None
    if root.tag.lower() not in SPECIES_ROOTS:
        return None
    attrs = _attrs(root)
    species = attrs.get("speciesname") or path.stem
    is_variant = root.tag.lower() == "charactervariant"
    inherit = attrs.get("inherit", "") if is_variant else ""
    health = None
    for child in root:
        if child.tag.lower() == "health":
            health = child
            break
    health_attrs = _attrs(health) if health is not None else {}
    ai = None
    for child in root:
        if child.tag.lower() == "ai":
            ai = child
            break
    ai_attrs = _attrs(ai) if ai is not None else {}
    targets: list[dict[str, str]] = []
    if ai is not None:
        for node in ai:
            if node.tag.lower() != "target":
                continue
            node_attrs = _attrs(node)
            targets.append(
                {
                    "tag": node_attrs.get("tag", ""),
                    "state": node_attrs.get("state", ""),
                    "priority": node_attrs.get("priority", ""),
                    # Raw perception distances in game units (pixels, 100 = 1 m);
                    # 0 means "auto from perception" (see EnemyAIController).
                    "react": _parse_int(node_attrs.get("reactdistance")),
                    "attack": _parse_int(node_attrs.get("attackdistance")),
                }
            )
    return {
        "id": species,
        "_inherit": inherit,
        "_vitality_raw": health_attrs.get("vitality"),
        "_ai_attrs": ai_attrs,
        "name_ru": "",
        "group": attrs.get("group", "") or inherit,
        "base_hp": _parse_int(health_attrs.get("vitality"), 100),
        "needs_water": _parse_bool(attrs.get("needswater")),
        "needs_air": _parse_bool(attrs.get("needsair")),
        "combat_strength": _parse_int(ai_attrs.get("combatstrength")),
        "sight": _parse_int(ai_attrs.get("sight")),
        "hearing": _parse_int(ai_attrs.get("hearing")),
        "aggression_hurt": _parse_int(ai_attrs.get("aggressionhurt")),
        "aggression_greed": _parse_int(ai_attrs.get("aggressiongreed")),
        "flee_health_threshold": _parse_int(ai_attrs.get("fleehealththreshold"), 99),
        "aggressive_boarding": _parse_bool(ai_attrs.get("aggressiveboarding")),
        "targets": targets,
    }


def _load_official_ru_names(source: Path) -> dict[str, str]:
    """Parse <character.*> strings from the vanilla Russian localization."""
    try:
        root = ET.fromstring(open(source / TEXTS_RU, encoding="utf-8-sig").read())
    except (OSError, ET.ParseError):
        return {}
    names: dict[str, str] = {}
    for element in root.iter():
        tag = element.tag.strip().lower()
        if tag.startswith("character.") and (element.text or "").strip():
            names[tag[len("character."):]] = element.text.strip()
    return names


def _resolve_ru_name(species_id: str, inherit: str, official: dict[str, str]) -> str:
    key = species_id.lower()
    if key in official:
        return official[key]
    # Mission variants: base name + qualifier (e.g. "Ползун (детёныш)").
    if inherit and inherit.lower() in official:
        base_ru = official[inherit.lower()]
        lowered = key
        for suffix, label in VARIANT_SUFFIX_RU:
            if lowered.endswith(suffix):
                return f"{base_ru} ({label})"
        return f"{base_ru} (вариант)"
    # Legacy species are previous versions of the same creature.
    if key.startswith("legacy") and key[len("legacy"):] in official:
        return f"{official[key[len('legacy'):]]} (старая версия)"
    if key.endswith("old") and key[: -len("old")] in official:
        return f"{official[key[: -len('old')]]} (старая версия)"
    if key.endswith("husk"):
        head = key[: -len("husk")].rstrip("_")
        if head in official:
            return f"{official[head]}-хаск"
    # Standalone files named like variants (own <Character>, no inherit).
    for suffix, label in VARIANT_SUFFIX_RU:
        if key.endswith(suffix) and key[: -len(suffix)] in official:
            return f"{official[key[: -len(suffix)]]} ({label})"
    return NAME_RU_FALLBACK.get(key, "")


def collect(source: Path) -> list[dict]:
    species: dict[str, dict] = {}
    official_ru = _load_official_ru_names(source)
    # Top-level species folders: <Dir>/<Dir>.xml is the definition; other
    # top-level xml files (Variants overrides) are picked up by the sweep.
    for path in sorted(source.rglob("*.xml")):
        try:
            relative = path.relative_to(source)
        except ValueError:
            continue
        if len(relative.parts) > 3:  # Animations/Ragdolls payloads and deeper
            continue
        if not _is_character_file(path):
            continue
        entry = _parse_species(path)
        if entry is None:
            continue
        species[entry["id"]] = entry  # Variants override base on name clash
    # Variants without an explicit group inherit it from the base species;
    # missing AI/health fields are inherited too (variant XML holds overrides).
    # Russian names resolve last so variant rules can use the base species.
    for entry in species.values():
        inherit = entry.pop("_inherit", "")
        entry["_inherit_id"] = inherit
        vitality_raw = entry.pop("_vitality_raw")
        ai_attrs = entry.pop("_ai_attrs")
        base = species.get(inherit)
        if base is not None and base is not entry:
            if not vitality_raw:
                entry["base_hp"] = base["base_hp"]
            if entry["group"] == (base["id"]):
                entry["group"] = base["group"] or entry["group"]
            for key in (
                "combat_strength",
                "sight",
                "hearing",
                "aggression_hurt",
                "aggression_greed",
                "flee_health_threshold",
                "aggressive_boarding",
            ):
                xml_key = {
                    "combat_strength": "combatstrength",
                    "sight": "sight",
                    "hearing": "hearing",
                    "aggression_hurt": "aggressionhurt",
                    "aggression_greed": "aggressiongreed",
                    "flee_health_threshold": "fleehealththreshold",
                    "aggressive_boarding": "aggressiveboarding",
                }[key]
                if xml_key not in ai_attrs:
                    entry[key] = base[key]
            if not entry["targets"]:
                entry["targets"] = [dict(target) for target in base["targets"]]
    for entry in species.values():
        inherit = entry.pop("_inherit_id", "")
        entry["name_ru"] = _resolve_ru_name(entry["id"], inherit, official_ru)
    return [species[key] for key in sorted(species)]


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else DEFAULT_SOURCE
    output = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUTPUT
    if not source.is_dir():
        print(f"source directory not found: {source}")
        return 1
    creatures = collect(source)
    if not creatures:
        print(f"no species found in {source}")
        return 1
    payload = {"source": "Barotrauma vanilla Content/Characters", "creatures": creatures}
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
        target.write("\n")
    print(f"wrote {len(creatures)} species to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
