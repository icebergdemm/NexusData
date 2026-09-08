"""Effective NexusData permission inspection.

This module deliberately keeps resource ACLs separate from the page/action
permission matrix. Documents and task boards are additive object permissions;
pretending they are just another boolean page flag would hide more than it
explains.
"""

import hashlib
import json
from typing import Any, Iterable

from sqlalchemy.orm import joinedload

from models import (
    AdminLoginKey,
    AdminPrivilegeGroup,
    AdminUser,
    Department,
    DepartmentAccessLevel,
    DepartmentMember,
    Document,
    DocumentPermission,
    PlayerData,
    TaskBoard,
    TaskBoardPermission,
)
from security import check_permission


def _raw_value(raw: Any, page: str, action: str):
    if not isinstance(raw, dict):
        return None
    pages = raw.get("pages", raw)
    if not isinstance(pages, dict) or not isinstance(pages.get(page), dict):
        return None
    value = pages[page].get(action)
    return value if isinstance(value, bool) else None


def _source(kind: str, value: bool, **details) -> dict:
    return {"kind": kind, "value": value, **details}


def effective_permission(admin, page: str, action: str, department_sources: Iterable[dict]) -> dict:
    """Return one effective decision and all explicit sources behind it."""
    if getattr(admin, "is_superuser", False):
        return {"value": True, "state": "allow", "sources": [_source("superuser", True)]}

    sources: list[dict] = []

    value = _raw_value(getattr(admin, "individual_permissions", None), page, action)
    if value is not None:
        sources.append(_source("individual", value))

    group = getattr(admin, "privilege_group", None)
    value = _raw_value(getattr(group, "permissions", None), page, action)
    if value is not None:
        sources.append(_source("group", value, group_id=group.id, group_name=group.name))

    merged_department_value = _raw_value(getattr(admin, "_department_site_permissions", None), page, action)
    has_department_detail = False
    for item in department_sources:
        # Module permissions are scoped to their department and are enforced
        # by check_department_permission. Only site_permissions participate in
        # the global page/action matrix; the complete module trace remains in
        # sources.department_module and the department level payload.
        if (item.get("module") == "site" and item.get("page") == page
                and item.get("action") == action and isinstance(item.get("value"), bool)):
            has_department_detail = True
            sources.append({"kind": "department_access_level", **item})
    if merged_department_value is not None and not has_department_detail:
        sources.append(_source("department_site", merged_department_value))

    explicit_values = [item["value"] for item in sources]
    deny_mode = getattr(admin, "_permission_policy_mode", "legacy_additive") == "deny_v2"
    if deny_mode and False in explicit_values:
        value, state = False, "deny"
    elif True in explicit_values:
        value, state = True, "allow"
    elif deny_mode and explicit_values:
        value, state = False, "deny"
    else:
        value = check_permission(admin, page, action)
        state = "allow" if value else "unset"
        if value and not sources:
            sources.append(_source("baseline", True))

    return {"value": bool(value), "state": state, "sources": sources}


def build_permission_snapshot(admin, registry: dict, department_sources: Iterable[dict]) -> dict:
    """Build every registry action, including actions with no explicit grant."""
    return {
        "pages": {
            page: {
                action["key"]: effective_permission(admin, page, action["key"], department_sources)
                for action in page_def["actions"]
            }
            for page, page_def in registry.items()
        }
    }


def department_sources_for_admin(db, admin_id: int) -> list[dict]:
    memberships = (
        db.query(DepartmentMember)
        .options(joinedload(DepartmentMember.department))
        .filter(DepartmentMember.admin_id == admin_id)
        .all()
    )
    if not memberships:
        return []

    dept_ids = [member.department_id for member in memberships]
    levels = db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.department_id.in_(dept_ids)).all()
    by_dept = {member.department_id: member for member in memberships}
    result = []
    for level in levels:
        member = by_dept.get(level.department_id)
        if not member or level.key not in (member.held_level_keys or []):
            continue
        for scope, raw in (("module", level.permissions), ("site", level.site_permissions)):
            pages = raw.get("pages", raw) if isinstance(raw, dict) else {}
            for page, actions in pages.items():
                if not isinstance(actions, dict):
                    continue
                for action, value in actions.items():
                    if isinstance(value, bool):
                        result.append({
                            "department_id": level.department_id,
                            "department_name": member.department.name if member.department else None,
                            "level_id": level.id,
                            "level_key": level.key,
                            "level_name": level.name,
                            "module": scope,
                            "page": page,
                            "action": action,
                            "value": value,
                        })
    return result


def _resource_acl(db, admin: AdminUser, department_sources: list[dict]) -> dict:
    group_id = admin.privilege_group_id
    access_level_keys = {item["level_key"] for item in department_sources}

    document_rows = db.query(DocumentPermission).options(joinedload(DocumentPermission.document)).all()
    documents = []
    for row in document_rows:
        applies = (
            row.grantee_type == "everyone"
            or (row.grantee_type == "admin" and row.grantee_admin_id == admin.id)
            or (row.grantee_type == "privilege_group" and group_id is not None and row.grantee_group_id == group_id)
            or (row.grantee_type == "access_level" and row.grantee_access_level_key in access_level_keys)
        )
        if applies:
            documents.append({
                "id": row.id,
                "resource_id": row.document_id,
                "title": row.document.title if row.document else None,
                "grantee_type": row.grantee_type,
                "level": row.level,
            })

    board_rows = db.query(TaskBoardPermission).all()
    board_names = {
        board.id: board.name
        for board in db.query(TaskBoard).filter(TaskBoard.id.in_({row.board_id for row in board_rows})).all()
    } if board_rows else {}
    boards = []
    for row in board_rows:
        applies = (
            (row.grantee_type == "user" and row.grantee_key == str(admin.id))
            or (row.grantee_type == "group_nd" and group_id is not None and row.grantee_key == str(group_id))
            or (row.grantee_type == "dept_level" and row.grantee_key in {f"{item['department_id']}:{item['level_key']}" for item in department_sources})
        )
        if applies:
            boards.append({
                "id": row.id,
                "resource_id": row.board_id,
                "name": board_names.get(row.board_id),
                "grantee_type": row.grantee_type,
                "grantee_key": row.grantee_key,
                "can_view": bool(row.can_view),
                "can_create": bool(row.can_create),
                "can_edit": bool(row.can_edit),
                "can_delete": bool(row.can_delete),
            })
    return {"documents": documents, "task_boards": boards}


def build_authorization_trace(db, admin_id: int) -> dict:
    admin = (
        db.query(AdminUser)
        .options(joinedload(AdminUser.privilege_group))
        .filter(AdminUser.id == admin_id)
        .first()
    )
    if not admin:
        return None

    from permissions_registry import PERMISSION_REGISTRY
    from security import compute_department_site_permissions, get_permission_policy_mode

    admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
    admin._permission_policy_mode = get_permission_policy_mode(db)
    sources = department_sources_for_admin(db, admin.id)
    memberships = (
        db.query(DepartmentMember)
        .options(joinedload(DepartmentMember.department))
        .filter(DepartmentMember.admin_id == admin.id)
        .all()
    )
    levels = db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.department_id.in_([m.department_id for m in memberships])).all() if memberships else []
    level_by_dept = {}
    for level in levels:
        level_by_dept.setdefault(level.department_id, []).append(level)

    departments = []
    for member in memberships:
        departments.append({
            "id": member.department_id,
            "name": member.department.name if member.department else None,
            "held_level_keys": member.held_level_keys or [],
            "is_head": bool(member.is_head),
            "is_deputy_head": bool(member.is_deputy_head),
            "is_auto": bool(member.is_auto),
            "levels": [{
                "id": level.id,
                "key": level.key,
                "name": level.name,
                "kind": level.kind,
                "held": level.key in (member.held_level_keys or []),
                "permissions": level.permissions or {},
                "site_permissions": level.site_permissions or {},
            } for level in level_by_dept.get(member.department_id, [])],
        })

    player = db.query(PlayerData).filter(PlayerData.linked_admin_id == admin.id).first()
    login_keys = db.query(AdminLoginKey).filter(AdminLoginKey.admin_id == admin.id).order_by(AdminLoginKey.created_at.desc()).all()
    return {
        "admin": {
            "id": admin.id,
            "discord_id": admin.discord_id,
            "discord_username": admin.discord_username,
            "discord_avatar": admin.discord_avatar,
            "role": admin.role,
            "is_superuser": bool(admin.is_superuser),
            "policy_mode": admin._permission_policy_mode,
        },
        "effective": build_permission_snapshot(admin, PERMISSION_REGISTRY, sources),
        "sources": {
            "individual": admin.individual_permissions or {},
            "group": ({"id": admin.privilege_group.id, "name": admin.privilege_group.name, "permissions": admin.privilege_group.permissions or {}}
                      if admin.privilege_group else None),
            "department_site": [item for item in sources if item.get("module") == "site"],
            "department_module": [item for item in sources if item.get("module") == "module"],
        },
        "departments": departments,
        "linked_player": ({"player_id": player.player_id, "name": player.display_name} if player else None),
        "login_keys": [{
            "id": key.id,
            "prefix": key.key_prefix,
            "label": key.label,
            "created_at": key.created_at.isoformat() if key.created_at else None,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            "revoked": bool(key.revoked),
            "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
            "use_count": key.use_count or 0,
        } for key in login_keys],
        "resource_acl": _resource_acl(db, admin, sources),
    }


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_policy_impact(db, target_policy: str) -> dict:
    from permissions_registry import PERMISSION_REGISTRY
    from security import get_permission_policy_mode

    current_policy = get_permission_policy_mode(db)
    admins = (
        db.query(AdminUser)
        .options(joinedload(AdminUser.privilege_group))
        .filter(AdminUser.is_deleted == 0)
        .order_by(AdminUser.id)
        .all()
    )
    affected_admins = []
    total_changes = 0
    canonical_admins = []
    for admin in admins:
        sources = department_sources_for_admin(db, admin.id)
        from security import compute_department_site_permissions
        admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
        admin._permission_policy_mode = current_policy
        before = build_permission_snapshot(admin, PERMISSION_REGISTRY, sources)
        admin._permission_policy_mode = target_policy
        after = build_permission_snapshot(admin, PERMISSION_REGISTRY, sources)
        changes = []
        for page, actions in after["pages"].items():
            for action, decision in actions.items():
                if decision["value"] != before["pages"][page][action]["value"]:
                    total_changes += 1
                    changes.append({"page": page, "action": action, "from": before["pages"][page][action]["value"], "to": decision["value"]})
        if changes:
            affected_admins.append({
                "id": admin.id,
                "name": admin.discord_username,
                "changes": changes,
            })
        canonical_admins.append({"id": admin.id, "before": before, "after": after})

    digest = canonical_digest({
        "current_policy": current_policy,
        "target_policy": target_policy,
        "admins": canonical_admins,
    })
    return {
        "current_policy": current_policy,
        "target_policy": target_policy,
        "digest": digest,
        "affected_admin_count": len(affected_admins),
        "changed_permission_count": total_changes,
        "affected_admins": affected_admins,
    }


def permission_summary(db, admin: AdminUser) -> dict:
    from permissions_registry import PERMISSION_REGISTRY
    from security import compute_department_site_permissions, get_permission_policy_mode

    sources = department_sources_for_admin(db, admin.id)
    admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
    admin._permission_policy_mode = get_permission_policy_mode(db)
    snapshot = build_permission_snapshot(admin, PERMISSION_REGISTRY, sources)
    conflict_count = 0
    for page in PERMISSION_REGISTRY:
        for action in PERMISSION_REGISTRY[page]["actions"]:
            source_values = snapshot["pages"][page][action["key"]]["sources"]
            values = {source["value"] for source in source_values if isinstance(source.get("value"), bool)}
            if values == {True, False}:
                conflict_count += 1
    return {
        "department_count": db.query(DepartmentMember).filter(DepartmentMember.admin_id == admin.id).count(),
        "conflict_count": conflict_count,
        "effective": {
            page: snapshot["pages"][page]["view"]["value"]
            for page in ("ratings", "admin", "players")
            if page in snapshot["pages"] and "view" in snapshot["pages"][page]
        },
    }
