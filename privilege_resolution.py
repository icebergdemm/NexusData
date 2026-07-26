"""
Shared resolution/merge logic for server privileges (ServerPlayerPrivilege).

A player can now have several rows at once — one manual grant per
server_scope (global/ASPER1/ASPER2) plus one department-derived (is_auto)
grant — rather than exactly one row. These are UNIONED at read time instead
of one clobbering the other. Used by routes/game_api.py, routes/cache_sync.py
and routes/admin.py's player-detail serializer, so the merge behaves
identically everywhere it's read.
"""
from typing import List, Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import ServerPlayerPrivilege, ServerPrivilegeGroup


def resolve_player_privilege_rows(db: Session, steamid: str, server_name: Optional[str] = None) -> List[ServerPlayerPrivilege]:
    """All ServerPlayerPrivilege rows that apply to this player.

    server_name=None returns every row regardless of scope (for admin
    display, where we want to show everything a player has). A given
    server_name returns rows scoped globally (server_scope IS NULL) OR
    scoped to that exact server — the same filter game_api.py used to apply
    to its single-row query.
    """
    q = db.query(ServerPlayerPrivilege).filter(ServerPlayerPrivilege.steamid == steamid)
    if server_name:
        sn = server_name.strip().upper()
        q = q.filter(or_(
            ServerPlayerPrivilege.server_scope.is_(None),
            ServerPlayerPrivilege.server_scope == sn,
        ))
    return q.all()


def merge_privilege_rows(db: Session, rows: List[ServerPlayerPrivilege]) -> dict:
    """Union commands/allowed_action_ids across every given row (manual +
    auto, however many server-scoped rows apply) — the actual "sum" the
    player effectively has. Also unions in each row's linked
    ServerPrivilegeGroup's own commands/allowed_action_ids, same dedup logic
    that used to be inlined per-row in routes/game_api.py."""
    commands: List[str] = []
    allowed_action_ids: List[str] = []
    group_keys: List[str] = []
    sources: List[dict] = []

    group_ids = {r.server_group_id for r in rows if r.server_group_id}
    groups_by_id = {}
    if group_ids:
        groups_by_id = {
            g.id: g for g in db.query(ServerPrivilegeGroup).filter(ServerPrivilegeGroup.id.in_(group_ids)).all()
        }

    for r in rows:
        for cmd in (r.commands or []):
            if cmd not in commands:
                commands.append(cmd)
        for aid in (r.allowed_action_ids or []):
            if aid not in allowed_action_ids:
                allowed_action_ids.append(aid)

        grp = groups_by_id.get(r.server_group_id) if r.server_group_id else None
        group_key = None
        if grp:
            group_key = grp.group_key
            for cmd in (grp.commands or []):
                if cmd not in commands:
                    commands.append(cmd)
            for aid in (grp.allowed_action_ids or []):
                if aid not in allowed_action_ids:
                    allowed_action_ids.append(aid)
        elif r.group_id:
            group_key = r.group_id

        if group_key and group_key not in group_keys:
            group_keys.append(group_key)

        sources.append({
            "id": r.id,
            "group_key": group_key,
            "server_scope": r.server_scope,
            "is_auto": bool(r.is_auto),
            "reason": r.reason,
        })

    return {
        "has_privileges": bool(rows),
        "commands": commands,
        "allowed_action_ids": allowed_action_ids,
        "group_key": group_keys[0] if group_keys else None,
        "group_keys": group_keys,
        "sources": sources,
    }
