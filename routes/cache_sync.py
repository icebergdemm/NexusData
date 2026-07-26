"""
Game-server cache sync API.

Lets the Barotrauma plugin (PlayerCacheStore.cs / RoundManager_ND.cs) check
whether its local SQLite cache of player profiles, privileges and reference
data (kit catalog + static mappings) is still fresh — without re-downloading
anything that hasn't changed. The plugin sends the hashes it currently has
cached; this only reports what's stale/unknown, it never returns the full
payloads itself (those still come from the existing /players/info,
/game/privileges/player and /kits endpoints, or the /reference bundle below
for a full/forced reference refresh).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Dict, Optional
from pydantic import BaseModel, Field

from database import get_game_db
from security import verify_external_api_key_dependency
from config import get_settings
from models import PlayerData, ServerPlayerPrivilege, Kit
from cache_versioning import (
    compute_player_profile_hash,
    compute_privilege_hash,
    compute_reference_data_hash,
)
from privilege_resolution import merge_privilege_rows

settings = get_settings()

api_router = APIRouter(
    prefix="/api/v1/server/cache",
    tags=["cache-sync"],
    dependencies=[Depends(verify_external_api_key_dependency)] if settings.require_auth else [],
)


class CacheCheckRequest(BaseModel):
    players: Dict[str, str] = Field(default_factory=dict, description="steamid -> locally cached profile_hash")
    privileges: Dict[str, str] = Field(default_factory=dict, description="steamid -> locally cached privilege_hash")
    reference_hash: Optional[str] = Field(None, description="Locally cached reference-bundle hash (kits + mappings)")
    server_name: Optional[str] = Field(None, description="Requesting server (ASPER1/ASPER2) — needed to resolve which server_scope privilege rows apply")


def _reference_mappings() -> dict:
    return {
        "kits_mapping": settings.kits_mapping,
        "ranks_mapping": settings.ranks_mapping,
        "roles_mapping": settings.roles_mapping,
        "rank_discord_roles": settings.rank_discord_roles,
        "server_permissions_mapping": settings.server_permissions_mapping,
    }


@api_router.post("/check")
def check_cache(body: CacheCheckRequest, db: Session = Depends(get_game_db)):
    """
    Batch change-detection for everything the plugin has cached locally.
    Bounded by whatever the plugin sends (its online + recently-seen
    players), so hashing on the fly here is cheap — no persisted hash
    columns needed.
    """
    stale_players = []
    unknown_players = []
    if body.players:
        rows = db.query(PlayerData).filter(PlayerData.player_steamid.in_(list(body.players.keys()))).all()
        found = {r.player_steamid: r for r in rows}
        for steamid, local_hash in body.players.items():
            row = found.get(steamid)
            if row is None:
                unknown_players.append(steamid)
            elif compute_player_profile_hash(row) != local_hash:
                stale_players.append(steamid)

    stale_privileges = []
    if body.privileges:
        query = db.query(ServerPlayerPrivilege).filter(ServerPlayerPrivilege.steamid.in_(list(body.privileges.keys())))
        if body.server_name:
            sn = body.server_name.strip().upper()
            query = query.filter(or_(
                ServerPlayerPrivilege.server_scope.is_(None),
                ServerPlayerPrivilege.server_scope == sn,
            ))
        rows_by_steamid: Dict[str, list] = {}
        for r in query.all():
            rows_by_steamid.setdefault(r.steamid, []).append(r)

        for steamid, local_hash in body.privileges.items():
            merged = merge_privilege_rows(db, rows_by_steamid.get(steamid, []))
            if compute_privilege_hash(merged) != local_hash:
                stale_privileges.append(steamid)

    kits = db.query(Kit).all()
    reference_hash = compute_reference_data_hash(kits, _reference_mappings())
    reference_stale = body.reference_hash is not None and body.reference_hash != reference_hash

    return {
        "stale_players": stale_players,
        "unknown_players": unknown_players,
        "stale_privileges": stale_privileges,
        "reference_stale": reference_stale,
        "reference_hash": reference_hash,
    }


@api_router.get("/reference")
def get_reference_bundle(db: Session = Depends(get_game_db)):
    """
    Full reference/service-data bundle: kit catalog + all static mappings
    ("наборы и прочая служебная информация"). Used for the plugin's initial
    load and for a forced 'sync_reference' action — bundled as one call
    since this data is small and changes rarely.
    """
    from schemas import KitOut

    kits = db.query(Kit).all()
    mappings = _reference_mappings()
    return {
        "kits": [KitOut.model_validate(k) for k in kits],
        **mappings,
        "reference_hash": compute_reference_data_hash(kits, mappings),
    }
