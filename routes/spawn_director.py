"""Live adaptive monster-wave telemetry for the administrator dashboard."""

from datetime import datetime
from functools import lru_cache
import os
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from security import check_permission, verify_external_api_key_dependency
from models import AdminUser

settings = get_settings()
router = APIRouter(prefix="/api/v1/spawn-director", tags=["spawn-director"], dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [])
admin_router = APIRouter(prefix="/api/v1/admin/spawn-director", tags=["spawn-director-admin"])
_snapshots: dict[str, dict[str, Any]] = {}
_health_floors: dict[str, dict[str, float]] = {}
_DEFAULT_CONTENT_DIR = r"C:\Users\icegergert\Documents\BarotraumaWorkBench\ASPER\MorePlugins_Barotrauma\LuaCsForBarotrauma_source\Barotrauma\Content"
_BUNDLED_CATALOG_PATH = Path(__file__).resolve().parent.parent / "static" / "creature_catalog.json"
_DEPLOYED_CATALOG_PATH = Path("/home/container/static/creature_catalog.json")
_HARDCODED_CREATURE_IDS = (
    "Ancient", "Balloon", "Bonethresher", "Carrier", "Charybdis", "Charybdisold",
    "Coelanth", "Crawler", "Crawlerbroodmother", "Crawlerhusk", "Cyborgworm",
    "Defensebot", "Endworm", "Fractalguardian", "Fractalguardian_emp", "Fractalguardian2",
    "Guardianrepairbot", "Hammerhead", "Hammerheadgold", "Hammerheadmatriarch", "Hammerheadspawn",
    "Husk_chimera", "Husk_prowler", "Huskcontainer", "Jove", "Latcher", "Legacycharybdis",
    "Legacycrawler", "Legacyendworm", "Legacyfractalguardian", "Legacyfractalguardian2", "Legacyhusk",
    "Legacymantis", "Legacymoloch", "Legacytigerthresher", "Legacywatcher", "Leucocyte", "Mantis",
    "Moloch", "Molochbaby", "Molochblack", "Mudraptor", "Mudraptor_unarmored", "Orangeboy",
    "Peanut", "Portalguardian", "Psilotoad", "Spineling", "Spineling_giant", "Spineling_morbusine",
    "Swarmfeeder", "Terminalcell", "Tigerthresher", "Watcher",
)


@lru_cache(maxsize=1)
def _creature_catalog() -> list[dict[str, Any]]:
    """Read vanilla creature XML. Set BAROTRAUMA_CONTENT_DIR on deployed NexusData."""
    root = os.environ.get("BAROTRAUMA_CONTENT_DIR", _DEFAULT_CONTENT_DIR)
    characters = os.path.join(root, "Characters")
    if not os.path.isdir(characters):
        for catalog_path in (_DEPLOYED_CATALOG_PATH, _BUNDLED_CATALOG_PATH):
            try:
                with open(catalog_path, encoding="utf-8") as source:
                    return json.load(source).get("creatures", [])
            except (OSError, ValueError):
                continue
        return [{
            "id": creature_id,
            "group": "",
            "base_hp": 0,
            "needs_water": False,
            "needs_air": False,
            "combat_strength": 0,
            "sight": 0,
            "hearing": 0,
            "aggression_hurt": 0,
            "aggression_greed": 0,
            "flee_health_threshold": 0,
            "aggressive_boarding": False,
            "targets": [],
        } for creature_id in _HARDCODED_CREATURE_IDS]
    creatures: list[dict[str, Any]] = []
    for directory, _, files in os.walk(characters):
        for filename in files:
            if not filename.endswith(".xml") or filename.lower().endswith("ragdoll.xml"):
                continue
            try:
                character = ET.parse(os.path.join(directory, filename)).getroot()
                if character.tag.lower() != "character" or character.attrib.get("humanoid", "False").lower() == "true":
                    continue
                health, ai = character.find("health"), character.find("ai")
                if health is None:
                    continue
                targets = [] if ai is None else [
                    {"tag": target.attrib.get("tag", "?"), "state": target.attrib.get("state", "Observe"), "priority": target.attrib.get("priority", "0")}
                    for target in ai.findall("target")
                ]
                creatures.append({
                    "id": character.attrib.get("speciesname", os.path.splitext(filename)[0]),
                    "group": character.attrib.get("group", ""),
                    "base_hp": float(health.attrib.get("vitality", 0)),
                    "needs_water": character.attrib.get("needswater", "False").lower() == "true",
                    "needs_air": character.attrib.get("needsair", "False").lower() == "true",
                    "combat_strength": float((ai.attrib if ai is not None else {}).get("combatstrength", 0)),
                    "sight": float((ai.attrib if ai is not None else {}).get("sight", 0)),
                    "hearing": float((ai.attrib if ai is not None else {}).get("hearing", 0)),
                    "aggression_hurt": float((ai.attrib if ai is not None else {}).get("aggressionhurt", 0)),
                    "aggression_greed": float((ai.attrib if ai is not None else {}).get("aggressiongreed", 0)),
                    "flee_health_threshold": float((ai.attrib if ai is not None else {}).get("fleehealththreshold", 0)),
                    "aggressive_boarding": (ai.attrib if ai is not None else {}).get("aggressiveboarding", "False").lower() == "true",
                    "targets": targets,
                })
            except (ET.ParseError, OSError, ValueError):
                continue
    if creatures:
        return sorted(creatures, key=lambda creature: creature["id"].lower())
    return [{
        "id": creature_id,
        "group": "",
        "base_hp": 0,
        "needs_water": False,
        "needs_air": False,
        "combat_strength": 0,
        "sight": 0,
        "hearing": 0,
        "aggression_hurt": 0,
        "aggression_greed": 0,
        "flee_health_threshold": 0,
        "aggressive_boarding": False,
        "targets": [],
    } for creature_id in _HARDCODED_CREATURE_IDS]


def _require_view(request: Request, db: Session) -> AdminUser:
    from routes.admin import load_current_admin
    admin = load_current_admin(request, db)
    if not check_permission(admin, "world_state", "view"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для просмотра директора волн")
    return admin


@router.post("/snapshot")
async def ingest_snapshot(body: dict[str, Any]) -> dict[str, str]:
    server = str(body.get("server_name") or "UNKNOWN").strip().upper()
    _snapshots[server] = {
        "server_name": server,
        "mode": body.get("mode", "observe"),
        "crew_power": body.get("crew_power", 0),
        "threat_multiplier": body.get("threat_multiplier", 1),
        "learned_baseline": body.get("learned_baseline", 0),
        "baseline_samples": body.get("baseline_samples", 0),
        "explanation": body.get("explanation", ""),
        "waves": body.get("waves", []),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    return {"status": "ok"}


@router.get("/health-floor")
def get_health_floor(server_name: str = "UNKNOWN") -> dict[str, float]:
    return _health_floors.get(server_name.strip().upper(), {"MinMonsterHealthMultiplier": 0.0, "MinMonsterHealthFixed": 0.0})


@admin_router.get("/state")
def get_state(request: Request, server_name: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_view(request, db)
    server = server_name.strip().upper() if server_name else None
    snapshots = list(_snapshots.values())
    return {"servers": [snapshot for snapshot in snapshots if not server or snapshot["server_name"] == server]}


@admin_router.get("/creatures")
def get_creatures(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_view(request, db)
    creatures = _creature_catalog()
    if not creatures:
        raise HTTPException(status_code=503, detail="Каталог недоступен: задайте BAROTRAUMA_CONTENT_DIR")
    return {"creatures": creatures}


@admin_router.put("/health-floor/{server_name}")
def set_health_floor(server_name: str, body: dict[str, Any], request: Request, db: Session = Depends(get_db)) -> dict[str, float]:
    admin = _require_view(request, db)
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Изменять порог здоровья может только superuser")
    multiplier = max(0.0, float(body.get("multiplier", 0.0)))
    fixed = max(0.0, float(body.get("fixed", 0.0)))
    value = {"MinMonsterHealthMultiplier": multiplier, "MinMonsterHealthFixed": fixed}
    _health_floors[server_name.strip().upper()] = value
    return value
