"""
Department workspace system ("Рабочая зона" — Отделы): superuser-created
departments with Discord-role-driven membership and internal permissions.

Membership sync with Discord is bidirectional: workers.task_sync_department_roles
(every 10 min) pulls Discord role -> site membership; the member CRUD endpoints
below push site membership -> Discord role via "sync_department_role" bot actions.
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from datetime import datetime
import re
import secrets
import logging

from database import get_db
from security import check_permission, check_department_permission
from models import (
    AdminUser, AdminSession, Department, DepartmentAccessLevel, DepartmentMember, DepartmentRatingEntry,
    DepartmentChatChannel, DepartmentChatMessage,
    DepartmentDocumentFolder, DepartmentFolderPermission, DepartmentDocumentTag,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Workspace tabs a department can turn on/off (item 12 — "modularity").
MODULE_KEYS = ["documentation", "roster", "rating", "orders", "test_server", "chat", "news"]


# ── Auth (mirrors routes/documents.py's self-contained dependency) ──────────

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


def require_departments_page(admin: AdminUser, action: str = "view"):
    if not check_permission(admin, "departments", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return base or f"dept-{secrets.token_hex(4)}"


def get_department_or_404(db: Session, department_id: int) -> Department:
    dept = (
        db.query(Department)
        .options(joinedload(Department.access_levels))
        .filter(Department.id == department_id, Department.is_archived.is_(False))
        .first()
    )
    if not dept:
        raise HTTPException(status_code=404, detail="Отдел не найден")
    return dept


def get_member(db: Session, department_id: int, admin_id: int) -> Optional[DepartmentMember]:
    return (
        db.query(DepartmentMember)
        .filter(DepartmentMember.department_id == department_id, DepartmentMember.admin_id == admin_id)
        .first()
    )


def require_department_module(db: Session, department: Department, admin: AdminUser, module: str, action: str = "view") -> Optional[DepartmentMember]:
    member = get_member(db, department.id, admin.id)
    if not check_department_permission(admin, department, member, module, action):
        raise HTTPException(status_code=403, detail="Недостаточно прав в отделе")
    return member


def is_head_or_deputy_or_superuser(admin: AdminUser, member: Optional[DepartmentMember]) -> bool:
    """The department-management authority gate. is_deputy_head carries the
    exact same authority as is_head — the only thing reserved exclusively
    for a superuser is granting/revoking is_head itself."""
    return bool(admin.is_superuser or (member and (member.is_head or member.is_deputy_head)))


def _can_manage_server_privilege_binding(dept: Department, member: Optional[DepartmentMember], admin: AdminUser) -> bool:
    """Может ли текущий пользователь привязывать серверные группы к уровням.
    Разрешено суперпользователю всегда, либо держателю любого уровня с
    can_bind_server_privilege=True. Флаг выставляется ТОЛЬКО суперпользователем
    (см. api_update_access_level) и делегирует право управления привязками."""
    if admin.is_superuser:
        return True
    if not member:
        return False
    held = set(member.held_level_keys or [])
    for lvl in dept.access_levels:
        if not getattr(lvl, "can_bind_server_privilege", False):
            continue
        if lvl.key in held:
            return True
        if lvl.kind == "head" and member.is_head:
            return True
        if lvl.kind == "deputy_head" and member.is_deputy_head:
            return True
    return False


def _linked_admin_ids(db: Session, admin_ids: list) -> set:
    """Batch lookup — which of these admin ids have a linked in-game profile."""
    from models import PlayerData
    if not admin_ids:
        return set()
    rows = db.query(PlayerData.linked_admin_id).filter(PlayerData.linked_admin_id.in_(admin_ids)).all()
    return {r[0] for r in rows}


def _has_linked_profile(db: Session, admin_id: int) -> bool:
    from models import PlayerData
    return db.query(PlayerData).filter(PlayerData.linked_admin_id == admin_id).first() is not None


def _forced_names_by_admin_id(db: Session, admin_ids: list) -> dict:
    """Batch lookup — the linked in-game profile's admin-assigned display
    name (PlayerData.forced_display_name) for each admin id that has one
    set, so the roster can show it instead of/alongside the Discord name."""
    from models import PlayerData
    if not admin_ids:
        return {}
    rows = db.query(PlayerData.linked_admin_id, PlayerData.forced_display_name).filter(
        PlayerData.linked_admin_id.in_(admin_ids), PlayerData.forced_display_name.isnot(None)
    ).all()
    return {admin_id: name for admin_id, name in rows if name}


def _folder_visible(db: Session, folder: DepartmentDocumentFolder, admin: AdminUser, member: Optional[DepartmentMember], held_keys: set) -> bool:
    """A documentation folder with no permissions configured on it is
    private to its creator — mirrors routes/documents.py's
    get_document_access default-deny policy for the documents inside it.
    Visible to: the creator, an explicit DepartmentFolderPermission grant on
    the folder itself, or one on an ancestor folder that has
    auto_apply_permissions=True (same inheritance rule documents use)."""
    if admin.is_superuser or folder.created_by_admin_id == admin.id:
        return True

    applicable_folder_ids = [folder.id]
    current = folder
    seen = {folder.id}
    while current.parent_id and current.parent_id not in seen:
        parent = db.query(DepartmentDocumentFolder).filter(
            DepartmentDocumentFolder.id == current.parent_id, DepartmentDocumentFolder.department_id == folder.department_id
        ).first()
        if not parent:
            break
        if parent.auto_apply_permissions:
            applicable_folder_ids.append(parent.id)
        seen.add(parent.id)
        current = parent

    grants = db.query(DepartmentFolderPermission).filter(DepartmentFolderPermission.folder_id.in_(applicable_folder_ids)).all()
    return any(
        (g.grantee_type == "access_level" and g.access_level_key in held_keys)
        or (g.grantee_type == "member" and g.member_admin_id == admin.id)
        for g in grants
    )


def _grants_elevated_access(held_level_keys: list, is_head_flag: bool, is_deputy_flag: bool) -> bool:
    """True if this state includes anything beyond the base level — the
    threshold at which a linked player profile becomes mandatory."""
    return bool([k for k in (held_level_keys or []) if k != "base"]) or is_head_flag or is_deputy_flag


def _ensure_linked_or_link(db: Session, admin_id: int, elevated: bool, steamid: Optional[str]):
    """Guards non-base access grants: an employee must have a linked player
    profile before receiving one. If not yet linked, either link it now
    (given a steamid) or block with 428 so the frontend can prompt for one."""
    if not elevated or _has_linked_profile(db, admin_id):
        return
    steamid = (steamid or "").strip()
    if not steamid:
        raise HTTPException(
            status_code=428,
            detail="У сотрудника не привязан игровой профиль — укажите его SteamID, чтобы выдать уровень доступа",
        )
    from models import PlayerData
    player = db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()
    if not player:
        raise HTTPException(status_code=404, detail="Игрок с таким SteamID не найден в базе (сотрудник должен хотя бы раз зайти на сервер)")
    if player.linked_admin_id is not None and player.linked_admin_id != admin_id:
        raise HTTPException(status_code=409, detail="Этот SteamID уже привязан к другому аккаунту администратора")
    player.linked_admin_id = admin_id
    player.linked_admin_auto = False


def _role_ids_for_membership(department: Department, held_level_keys: list) -> set:
    """Discord role IDs a member should hold given their held access levels —
    the base "employee of this department" role plus each held level's own
    role, wherever those are configured. Used by the site->Discord half of
    item 4's bidirectional sync (the Discord->site half is
    workers.task_sync_department_roles)."""
    ids = set()
    if department.base_discord_role_id:
        ids.add(department.base_discord_role_id)
    for level in department.access_levels:
        if level.key in held_level_keys and level.discord_role_id:
            ids.add(level.discord_role_id)
    return ids


def _queue_role_sync(db: Session, target_admin: AdminUser, add_role_ids: set, remove_role_ids: set, queued_by_admin_id: int) -> None:
    """Dispatch Discord role grants/revokes for a department membership
    change directly to the in-process bot — see
    discord_bot/bot.py's execute_bot_action "sync_department_role" branch.
    No-op if the admin has no Discord ID.

    Only ever called from the member add/update/remove endpoints below —
    each of those flips the DepartmentMember row to is_auto=False in the
    same request, and workers.task_sync_department_roles (the Discord->site
    direction) never touches is_auto=False rows. That one-way handoff is
    the whole loop-safety contract: see task_sync_department_roles's
    docstring for the full explanation."""
    if not target_admin.discord_id:
        logger.warning(
            "Синхронизация ролей пропущена: у сотрудника id=%s не привязан Discord ID",
            target_admin.id,
        )
        return

    if not add_role_ids and not remove_role_ids:
        # Самый частый случай «выдал уровень, а роль не появилась»: у уровня
        # доступа (или у отдела — базовая роль) в настройках не указан
        # discord_role_id, поэтому отправлять нечего. Раньше это проходило
        # совершенно молча.
        logger.warning(
            "Синхронизация ролей: нечего отправлять для сотрудника id=%s — "
            "у уровней доступа не задан Discord-роль (discord_role_id)",
            target_admin.id,
        )
        return

    from discord_bot import bot as discord_bot
    try:
        for role_id in add_role_ids:
            discord_bot.schedule(
                discord_bot.execute_bot_action(
                    "sync_department_role",
                    {"discord_id": target_admin.discord_id, "add_role_id": role_id},
                ),
                description=f"выдача Discord-роли {role_id} сотруднику {target_admin.discord_id}",
            )
        for role_id in remove_role_ids:
            discord_bot.schedule(
                discord_bot.execute_bot_action(
                    "sync_department_role",
                    {"discord_id": target_admin.discord_id, "remove_role_id": role_id},
                ),
                description=f"снятие Discord-роли {role_id} с сотрудника {target_admin.discord_id}",
            )
    except RuntimeError as e:
        # Бот не запущен (нет токена/не стартовал). Изменение в отделе при
        # этом остаётся в силе — роли досинхронизирует фоновая задача
        # workers.task_reconcile_department_discord_roles, когда бот поднимется.
        logger.error("Синхронизация ролей не отправлена (бот не запущен): %s", e)


def _set_kit_binding(db: Session, level: DepartmentAccessLevel, raw: Optional[dict], admin_id: int) -> None:
    """Superuser-only create/replace/clear of a level's DepartmentKitBinding
    (item 6). Clearing revokes every outstanding grant immediately (rather
    than waiting for the next worker cycle, which can no longer see the
    binding once it's gone) — same kval_kits removal logic as
    workers.task_sync_department_kits, kept in lockstep with it."""
    from models import DepartmentKitBinding, DepartmentKitGrant, PlayerData

    existing = db.query(DepartmentKitBinding).filter(DepartmentKitBinding.access_level_id == level.id).first()

    if not raw or not raw.get("job_id"):
        if existing:
            for grant in db.query(DepartmentKitGrant).filter(DepartmentKitGrant.binding_id == existing.id).all():
                player = db.query(PlayerData).filter(PlayerData.player_id == grant.player_id).first()
                if player:
                    ach = dict(player.achievements or {}) if not isinstance(player.achievements, list) else {}
                    kval_kits = dict(ach.get("kval_kits", {}))
                    if kval_kits.get(existing.job_id) == existing.kit_level:
                        del kval_kits[existing.job_id]
                        ach["kval_kits"] = kval_kits
                        player.achievements = ach
                db.delete(grant)
            db.delete(existing)
        return

    job_id = str(raw["job_id"]).strip()
    try:
        kit_level = int(raw.get("kit_level"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="kit_level должен быть числом")
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id обязателен")

    if existing:
        # job_id/level changed out from under existing grants — they no
        # longer describe what's actually bound, so retract them; the next
        # worker cycle re-grants the new kit to whoever's still eligible.
        if existing.job_id != job_id or existing.kit_level != kit_level:
            for grant in db.query(DepartmentKitGrant).filter(DepartmentKitGrant.binding_id == existing.id).all():
                player = db.query(PlayerData).filter(PlayerData.player_id == grant.player_id).first()
                if player:
                    ach = dict(player.achievements or {}) if not isinstance(player.achievements, list) else {}
                    kval_kits = dict(ach.get("kval_kits", {}))
                    if kval_kits.get(existing.job_id) == existing.kit_level:
                        del kval_kits[existing.job_id]
                        ach["kval_kits"] = kval_kits
                        player.achievements = ach
                db.delete(grant)
        existing.job_id = job_id
        existing.kit_level = kit_level
    else:
        db.add(DepartmentKitBinding(access_level_id=level.id, job_id=job_id, kit_level=kit_level, created_by_admin_id=admin_id))


# ── Serialization ─────────────────────────────────────────────────────────

def serialize_access_level(level: DepartmentAccessLevel) -> dict:
    return {
        "id": level.id, "key": level.key, "name": level.name,
        "discord_role_id": level.discord_role_id, "kind": level.kind,
        "description": getattr(level, "description", None),
        "sort_order": getattr(level, "sort_order", 0) or 0,
        "can_bind_server_privilege": bool(getattr(level, "can_bind_server_privilege", False)),
        "permissions": level.permissions or {},
        "site_permissions": level.site_permissions or {},
        "server_privilege_group_id": level.server_privilege_group_id,
        "kit_binding": (
            {"job_id": level.kit_binding.job_id, "kit_level": level.kit_binding.kit_level}
            if level.kit_binding else None
        ),
    }


def serialize_member(
    member: DepartmentMember, linked_admin_ids: Optional[set] = None, db: Optional[Session] = None,
    forced_names: Optional[dict] = None,
) -> dict:
    admin = member.admin
    if linked_admin_ids is not None:
        has_linked_profile = member.admin_id in linked_admin_ids
    elif db is not None:
        has_linked_profile = _has_linked_profile(db, member.admin_id)
    else:
        has_linked_profile = False
    if forced_names is not None:
        forced_display_name = forced_names.get(member.admin_id)
    elif db is not None:
        forced_display_name = _forced_names_by_admin_id(db, [member.admin_id]).get(member.admin_id)
    else:
        forced_display_name = None
    return {
        "id": member.id,
        "admin_id": member.admin_id,
        "admin_name": admin.discord_username if admin else None,
        "admin_avatar": admin.discord_avatar if admin else None,
        # Admin-assigned in-game nickname (PlayerData.forced_display_name) of
        # this member's linked profile, when set — takes precedence over
        # admin_name wherever the roster displays a member's name.
        "forced_display_name": forced_display_name,
        "created_by_worker": bool(admin.created_by_worker) if admin else False,
        "held_level_keys": member.held_level_keys or [],
        "is_head": member.is_head,
        "is_deputy_head": member.is_deputy_head,
        "is_auto": member.is_auto,
        "added_at": member.added_at.isoformat() if member.added_at else None,
        # Non-base access requires a linked in-game (Steam) profile — used by
        # the roster page to warn about / highlight members missing one.
        "has_linked_profile": has_linked_profile,
    }


def serialize_department(dept: Department, db: Session, current_admin: AdminUser, member: Optional[DepartmentMember] = None) -> dict:
    if member is None:
        member = get_member(db, dept.id, current_admin.id)
    members = (
        db.query(DepartmentMember)
        .options(joinedload(DepartmentMember.admin))
        .filter(DepartmentMember.department_id == dept.id)
        .all()
    )
    head = next((m for m in members if m.is_head), None)
    my_access = {
        mod: {
            "view": check_department_permission(current_admin, dept, member, mod, "view"),
            "edit": check_department_permission(current_admin, dept, member, mod, "edit"),
        }
        for mod in MODULE_KEYS
    }
    return {
        "id": dept.id,
        "name": dept.name,
        "slug": dept.slug,
        "description": dept.description,
        "icon": dept.icon,
        "color": dept.color,
        "base_discord_role_id": dept.base_discord_role_id,
        "base_discord_role_name": dept.base_discord_role_name,
        "enabled_modules": dept.enabled_modules or [],
        "pterodactyl_server_uuid": dept.pterodactyl_server_uuid,
        "orders_channel_id": dept.orders_channel_id,
        "news_channel_id": dept.news_channel_id,
        "rating_channel_id": dept.rating_channel_id,
        "discord_category_id": dept.discord_category_id,
        "head_server_privilege_group_id": dept.head_server_privilege_group_id,
        "member_count": len(members),
        "head": serialize_member(head, db=db) if head else None,
        "is_member": member is not None,
        "is_head": bool(member and member.is_head),
        "is_deputy_head": bool(member and member.is_deputy_head),
        "can_manage_server_privilege_binding": _can_manage_server_privilege_binding(dept, member, current_admin),
        "my_access": my_access,
        "created_at": dept.created_at.isoformat() if dept.created_at else None,
    }


# ── Department CRUD ──────────────────────────────────────────────────────

@router.get("/api/v1/admin/departments")
async def api_list_departments(db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    q = (
        db.query(Department)
        .options(joinedload(Department.access_levels))
        .filter(Department.is_archived.is_(False))
    )
    # "view" is baseline for everyone now (own memberships only) — full
    # oversight of every department requires "view_all" or superuser,
    # same as documents:view vs documents:view_all.
    if not (current_admin.is_superuser or check_permission(current_admin, "departments", "view_all")):
        q = q.join(DepartmentMember, DepartmentMember.department_id == Department.id).filter(
            DepartmentMember.admin_id == current_admin.id
        )
    depts = q.order_by(Department.name).all()
    return {
        "departments": [serialize_department(d, db, current_admin) for d in depts],
        "can_create": check_permission(current_admin, "departments", "create"),
    }


@router.post("/api/v1/admin/departments")
async def api_create_department(request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    if not check_permission(current_admin, "departments", "create"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название обязательно")
    base_role_id = (body.get("base_discord_role_id") or "").strip() or None
    base_role_name = (body.get("base_discord_role_name") or "").strip() or None
    enabled_modules = [m for m in (body.get("enabled_modules") or []) if m in MODULE_KEYS]

    slug = _slugify(name)
    if db.query(Department).filter(Department.slug == slug).first():
        slug = f"{slug}-{secrets.token_hex(3)}"

    dept = Department(
        name=name,
        slug=slug,
        description=(body.get("description") or "").strip() or None,
        icon=(body.get("icon") or "").strip() or None,
        color=(body.get("color") or "").strip() or None,
        base_discord_role_id=base_role_id,
        base_discord_role_name=base_role_name,
        enabled_modules=enabled_modules,
        created_by_admin_id=current_admin.id,
    )
    db.add(dept)
    db.flush()

    if base_role_id:
        db.add(DepartmentAccessLevel(
            department_id=dept.id, key="base", name=base_role_name or "Сотрудник",
            discord_role_id=base_role_id, kind="base", permissions={},
        ))
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "create_department", {"target": {"department_id": dept.id, "name": dept.name}})

    return {"status": "ok", "department": serialize_department(dept, db, current_admin)}


@router.get("/api/v1/admin/departments/{department_id}")
async def api_get_department(department_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    # Same scoping as the list endpoint — a plain baseline "view" grant only
    # covers a member's own departments, not every department by ID guess.
    # 404 rather than 403 so a non-member can't even confirm the department
    # exists.
    if not (
        current_admin.is_superuser
        or check_permission(current_admin, "departments", "view_all")
        or member is not None
    ):
        raise HTTPException(status_code=404, detail="Отдел не найден")
    data = serialize_department(dept, db, current_admin, member=member)
    levels = sorted(dept.access_levels, key=lambda l: (getattr(l, "sort_order", 0) or 0, l.id))
    data["access_levels"] = [serialize_access_level(l) for l in levels]
    return data


@router.patch("/api/v1/admin/departments/{department_id}")
async def api_update_department(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    body = await request.json()
    if "description" in body:
        dept.description = (body.get("description") or "").strip() or None
    if "icon" in body:
        dept.icon = (body.get("icon") or "").strip() or None
    if "color" in body:
        dept.color = (body.get("color") or "").strip() or None

    # Structural/identity-sensitive fields — superuser only, per item 12's
    # explicit "модульность только для суперпользователя".
    if current_admin.is_superuser:
        if body.get("name", "").strip() if isinstance(body.get("name"), str) else False:
            dept.name = body["name"].strip()
        if "base_discord_role_id" in body:
            dept.base_discord_role_id = (body.get("base_discord_role_id") or "").strip() or None
        if "base_discord_role_name" in body:
            dept.base_discord_role_name = (body.get("base_discord_role_name") or "").strip() or None
        if "base_discord_role_id" in body or "base_discord_role_name" in body:
            # Keep the "base" access level row (if any) in sync with the
            # department's own base_discord_role_id/name — workers.
            # task_sync_department_roles now reads base_discord_role_id
            # directly so this isn't load-bearing for the sync itself
            # anymore, but the levels list should still reflect reality.
            base_level = db.query(DepartmentAccessLevel).filter(
                DepartmentAccessLevel.department_id == dept.id, DepartmentAccessLevel.kind == "base"
            ).first()
            if dept.base_discord_role_id:
                if base_level:
                    base_level.discord_role_id = dept.base_discord_role_id
                    if dept.base_discord_role_name:
                        base_level.name = dept.base_discord_role_name
                else:
                    db.add(DepartmentAccessLevel(
                        department_id=dept.id, key="base", name=dept.base_discord_role_name or "Сотрудник",
                        discord_role_id=dept.base_discord_role_id, kind="base", permissions={},
                    ))
            elif base_level:
                base_level.discord_role_id = None
        if "enabled_modules" in body:
            dept.enabled_modules = [m for m in (body.get("enabled_modules") or []) if m in MODULE_KEYS]
        if "pterodactyl_server_uuid" in body:
            dept.pterodactyl_server_uuid = (body.get("pterodactyl_server_uuid") or "").strip() or None
        if "orders_channel_id" in body:
            dept.orders_channel_id = (body.get("orders_channel_id") or "").strip() or None
        if "news_channel_id" in body:
            dept.news_channel_id = (body.get("news_channel_id") or "").strip() or None
        if "rating_channel_id" in body:
            dept.rating_channel_id = (body.get("rating_channel_id") or "").strip() or None
        if "discord_category_id" in body:
            # Purely a verification anchor for manually-entered publish
            # channel IDs (routes/documents.py's verify-channel endpoint) —
            # unset = no verification performed.
            dept.discord_category_id = (body.get("discord_category_id") or "").strip() or None
        if "head_server_privilege_group_id" in body:
            # Superuser-only link: a member who is_head/is_deputy_head
            # auto-receives this server privilege group — see
            # workers.task_sync_auto_server_privileges.
            raw = body.get("head_server_privilege_group_id")
            dept.head_server_privilege_group_id = int(raw) if raw not in (None, "") else None

    db.commit()
    return {"status": "ok", "department": serialize_department(dept, db, current_admin)}


@router.delete("/api/v1/admin/departments/{department_id}")
async def api_archive_department(department_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователя")
    dept = get_department_or_404(db, department_id)
    dept.is_archived = True
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "archive_department", {"target": {"department_id": dept.id, "name": dept.name}})
    return {"status": "ok"}


# ── Access levels (role tiers: base / qualification / head) ────────────────

@router.post("/api/v1/admin/departments/{department_id}/access-levels")
async def api_create_access_level(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название обязательно")
    # "head"/"deputy_head" kinds are identity-sensitive — holding one
    # auto-grants is_head/is_deputy_head via workers.task_sync_department_roles
    # — so only a superuser may create one. A head/deputy creating a level
    # always gets "qualification", regardless of what they send.
    requested_kind = body.get("kind") if body.get("kind") in ("qualification", "head", "deputy_head") else "qualification"
    kind = requested_kind if current_admin.is_superuser else "qualification"

    key = _slugify(name)
    if db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.department_id == dept.id, DepartmentAccessLevel.key == key).first():
        key = f"{key}-{secrets.token_hex(2)}"

    level = DepartmentAccessLevel(
        department_id=dept.id, key=key, name=name,
        discord_role_id=(body.get("discord_role_id") or "").strip() or None,
        kind=kind, permissions=body.get("permissions") or {},
    )
    db.add(level)
    db.commit()
    return {"status": "ok", "access_level": serialize_access_level(level)}


@router.patch("/api/v1/admin/departments/{department_id}/access-levels/{level_id}")
async def api_update_access_level(department_id: int, level_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    level = db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.id == level_id, DepartmentAccessLevel.department_id == dept.id).first()
    if not level:
        raise HTTPException(status_code=404, detail="Уровень доступа не найден")

    body = await request.json()
    # Heads may tune the department-module permission matrix; role-ID wiring
    # and site-wide hybrid grants (item 3) stay superuser-only — the latter
    # reaches beyond this department, so a head can't self-escalate via it.
    if "permissions" in body:
        raw_perms = body.get("permissions") or {}
        # "news":"edit" is never delegable through an access level — write
        # access to the news module is hardcoded head/superuser-only
        # (see get_document_access / require_document_write_access), so
        # strip it here too rather than let it sit in stored state and
        # mislead the "my_access" summary shown to the frontend.
        pages = raw_perms.get("pages") if isinstance(raw_perms, dict) else None
        if isinstance(pages, dict) and isinstance(pages.get("news"), dict):
            pages["news"] = {**pages["news"], "edit": False}
        level.permissions = raw_perms
    # Rename – allowed for head/deputy/superuser
    if "name" in body:
        new_name = (body.get("name") or "").strip() if isinstance(body.get("name"), str) else ""
        if not new_name:
            raise HTTPException(status_code=400, detail="Название обязательно")
        level.name = new_name
    # Discord role ID – allowed for head/deputy/superuser
    if "discord_role_id" in body:
        level.discord_role_id = (body.get("discord_role_id") or "").strip() or None
    if "description" in body:
        level.description = (body.get("description") or "").strip() or None
    # sort_order is handled via dedicated reorder endpoint; allow direct patch too
    if "sort_order" in body and current_admin.is_superuser:
        try:
            level.sort_order = int(body.get("sort_order"))
        except: pass
    if current_admin.is_superuser:
        # Kind change – superuser only, base level protected
        if "kind" in body:
            new_kind = body.get("kind")
            if new_kind not in ("qualification", "head", "deputy_head", "base"):
                raise HTTPException(status_code=400, detail="Недопустимый тип уровня доступа")
            if level.kind == "base" and new_kind != "base":
                raise HTTPException(status_code=400, detail="Тип базового уровня нельзя изменить")
            if new_kind == "base" and level.kind != "base":
                raise HTTPException(status_code=400, detail="Нельзя создать второй базовый уровень через изменение типа")
            level.kind = new_kind
        if "can_bind_server_privilege" in body:
            level.can_bind_server_privilege = bool(body.get("can_bind_server_privilege"))
    # site_permissions — суперпользователь всегда, либо держатель права на изменение уровней
    # может делегировать только те функции сайта, которые есть у него самого
    if "site_permissions" in body:
        from permissions_registry import PERMISSION_REGISTRY
        raw = body.get("site_permissions") or {}
        pages = raw.get("pages", raw) if isinstance(raw, dict) else {}
        cleaned = {}
        for page, actions in (pages or {}).items():
            if page not in PERMISSION_REGISTRY or not isinstance(actions, dict):
                continue
            valid_actions = {a["key"] for a in PERMISSION_REGISTRY[page]["actions"]}
            cleaned[page] = {
                k: v for k, v in actions.items()
                if k in valid_actions and isinstance(v, bool)
            }
        if not current_admin.is_superuser:
            if not is_head_or_deputy_or_superuser(current_admin, member):
                raise HTTPException(status_code=403, detail="Только суперпользователь может выдавать права сайта")
            # Проверка: можно выдавать только то, что есть у самого настройщика
            from security import check_permission as _check_perm
            for page, actions in cleaned.items():
                for act, allowed in actions.items():
                    if allowed and not _check_perm(current_admin, page, act):
                        raise HTTPException(status_code=403, detail=f"Нет права выдавать {page}:{act} — у вас самого его нет")
        level.site_permissions = {"pages": cleaned}
    if current_admin.is_superuser:
        if "kit_binding" in body:
            _set_kit_binding(db, level, body.get("kit_binding"), current_admin.id)
    else:
        if "kit_binding" in body:
            raise HTTPException(status_code=403, detail="Только суперпользователь может привязывать квал-киты")
    # Привязка серверной группы — суперпользователь либо держатель уровня с can_bind_server_privilege
    if "server_privilege_group_id" in body:
        if not _can_manage_server_privilege_binding(dept, member, current_admin):
            raise HTTPException(status_code=403, detail="Нет права привязывать группы серверных привилегий")
        raw = body.get("server_privilege_group_id")
        level.server_privilege_group_id = int(raw) if raw not in (None, "") else None
    db.commit()
    return {"status": "ok", "access_level": serialize_access_level(level)}


@router.post("/api/v1/admin/departments/{department_id}/access-levels/reorder")
async def api_reorder_access_levels(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    body = await request.json()
    ordered_ids = body.get("ordered_ids") or []
    for idx, lid in enumerate(ordered_ids):
        db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.id == lid, DepartmentAccessLevel.department_id == dept.id).update({DepartmentAccessLevel.sort_order: idx})
    db.commit()
    return {"status": "ok"}


@router.delete("/api/v1/admin/departments/{department_id}/access-levels/{level_id}")
async def api_delete_access_level(department_id: int, level_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Только глава отдела, заместитель или суперпользователь")
    level = db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.id == level_id, DepartmentAccessLevel.department_id == dept.id).first()
    if not level:
        raise HTTPException(status_code=404, detail="Уровень доступа не найден")
    if level.kind == "base":
        raise HTTPException(status_code=400, detail="Базовую роль нельзя удалить — измените её в настройках отдела")
    db.delete(level)
    db.commit()
    return {"status": "ok"}


# ── Roster (manual membership management) ───────────────────────────────

@router.get("/api/v1/admin/departments/{department_id}/members")
async def api_list_department_members(department_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "roster", "view")
    members = (
        db.query(DepartmentMember)
        .options(joinedload(DepartmentMember.admin))
        .filter(DepartmentMember.department_id == dept.id)
        .order_by(DepartmentMember.added_at)
        .all()
    )
    admin_ids = [m.admin_id for m in members]
    linked_admin_ids = _linked_admin_ids(db, admin_ids)
    forced_names = _forced_names_by_admin_id(db, admin_ids)
    return {"members": [serialize_member(m, linked_admin_ids=linked_admin_ids, forced_names=forced_names) for m in members]}


@router.post("/api/v1/admin/departments/{department_id}/members")
async def api_add_department_member(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    caller_member = require_department_module(db, dept, current_admin, "roster", "edit")
    if not is_head_or_deputy_or_superuser(current_admin, caller_member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    body = await request.json()
    discord_id = (body.get("discord_id") or "").strip()
    admin_id = body.get("admin_id")
    valid_keys = {l.key for l in dept.access_levels}
    held_level_keys = [k for k in (body.get("held_level_keys") or []) if k in valid_keys]
    # Head-of-department status is identity-sensitive (full control of the
    # department), same reasoning as locking discord_role_id edits to superuser.
    is_head_flag = bool(body.get("is_head")) and current_admin.is_superuser
    # Deputy head carries the same authority as head, but — unlike head — can
    # be granted by anyone who already has department-management authority
    # (checked above), not just a superuser.
    is_deputy_flag = bool(body.get("is_deputy_head"))

    target_admin = None
    created_new_account = False
    if admin_id:
        target_admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    elif discord_id:
        target_admin = db.query(AdminUser).filter(AdminUser.discord_id == discord_id).first()
        if not target_admin:
            # Auto-create an ND account for this Discord user, same
            # construction pattern as POST /api/v1/admin/users.
            target_admin = AdminUser(discord_id=discord_id, discord_username=f"User_{discord_id}", role="admin")
            db.add(target_admin)
            db.flush()
            created_new_account = True
        elif target_admin.is_deleted:
            # Previously soft-deleted — restore instead of silently adding a
            # member row for an account that still can't log in. Privileges
            # stay wiped (delete_admin's design); only login + membership
            # are restored. Treated like a fresh account for auto-link too.
            target_admin.is_deleted = 0
            created_new_account = True
    if not target_admin:
        raise HTTPException(status_code=400, detail="Укажите admin_id или discord_id")

    if get_member(db, dept.id, target_admin.id):
        raise HTTPException(status_code=409, detail="Этот пользователь уже состоит в отделе")

    # Item 1 — auto-link the freshly-created (or just-restored) account to
    # its in-game player profile, same conservative match rule as
    # workers.task_auto_link_admins (confirmed Discord ID, not already
    # linked). Skipped for an account that already existed and wasn't
    # deleted — nothing changed for it, so nothing to (re-)link.
    auto_link = "skipped"
    if created_new_account:
        from models import PlayerData
        # A restored account may already have kept its old player link
        # (delete_admin never touches PlayerData) — check that first so a
        # resurrection doesn't wrongly report "not_found" and pop the
        # link-failure warning for someone who was already linked.
        already_linked = db.query(PlayerData).filter(PlayerData.linked_admin_id == target_admin.id).first()
        if already_linked:
            auto_link = "linked"
        else:
            candidate = (
                db.query(PlayerData)
                .filter(
                    PlayerData.discord_id == target_admin.discord_id,
                    PlayerData.discord_id_confirmed == 1,
                    PlayerData.linked_admin_id.is_(None),
                )
                .first()
            )
            if candidate:
                candidate.linked_admin_id = target_admin.id
                candidate.linked_admin_auto = True
                auto_link = "linked"
            else:
                auto_link = "not_found"

    final_held_keys = held_level_keys or ["base"]
    elevated = _grants_elevated_access(final_held_keys, is_head_flag, is_deputy_flag)
    _ensure_linked_or_link(db, target_admin.id, elevated, body.get("steamid"))

    dm = DepartmentMember(
        department_id=dept.id,
        admin_id=target_admin.id,
        held_level_keys=final_held_keys,
        is_head=is_head_flag,
        is_deputy_head=is_deputy_flag,
        is_auto=False,
        added_by_admin_id=current_admin.id,
        role_state_updated_at=datetime.utcnow(),
    )
    db.add(dm)
    _queue_role_sync(db, target_admin, _role_ids_for_membership(dept, final_held_keys), set(), current_admin.id)
    db.commit()
    return {"status": "ok", "member": serialize_member(dm, db=db), "auto_link": auto_link}


@router.patch("/api/v1/admin/departments/{department_id}/members/{member_id}")
async def api_update_department_member(department_id: int, member_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    caller_member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, caller_member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    dm = db.query(DepartmentMember).filter(DepartmentMember.id == member_id, DepartmentMember.department_id == dept.id).first()
    if not dm:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    body = await request.json()
    valid_keys = {l.key for l in dept.access_levels}
    new_held_keys = (
        [k for k in (body.get("held_level_keys") or []) if k in valid_keys]
        if "held_level_keys" in body else (dm.held_level_keys or [])
    )
    new_is_head = bool(body.get("is_head")) if ("is_head" in body and current_admin.is_superuser) else dm.is_head
    new_is_deputy = bool(body.get("is_deputy_head")) if "is_deputy_head" in body else dm.is_deputy_head

    elevated = _grants_elevated_access(new_held_keys, new_is_head, new_is_deputy)
    _ensure_linked_or_link(db, dm.admin_id, elevated, body.get("steamid"))

    role_state_touched = False
    if "held_level_keys" in body:
        old_keys = dm.held_level_keys or []
        dm.held_level_keys = new_held_keys
        old_roles = _role_ids_for_membership(dept, old_keys)
        new_roles = _role_ids_for_membership(dept, new_held_keys)
        _queue_role_sync(db, dm.admin, new_roles - old_roles, old_roles - new_roles, current_admin.id)
        role_state_touched = True
    if "is_head" in body and current_admin.is_superuser:
        dm.is_head = new_is_head
        role_state_touched = True
    if "is_deputy_head" in body:
        dm.is_deputy_head = new_is_deputy
        role_state_touched = True
    dm.is_auto = False  # any manual edit takes this row out of worker-managed territory
    if role_state_touched:
        # Recency signal for task_sync_auto_server_privileges's "most recent
        # assignment wins" tie-break across departments.
        dm.role_state_updated_at = datetime.utcnow()
    db.commit()
    return {"status": "ok", "member": serialize_member(dm, db=db)}


@router.delete("/api/v1/admin/departments/{department_id}/members/{member_id}")
async def api_remove_department_member(department_id: int, member_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    caller_member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, caller_member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    dm = db.query(DepartmentMember).filter(DepartmentMember.id == member_id, DepartmentMember.department_id == dept.id).first()
    if not dm:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    _queue_role_sync(db, dm.admin, set(), _role_ids_for_membership(dept, dm.held_level_keys or []), current_admin.id)
    db.delete(dm)
    db.commit()
    return {"status": "ok"}


# ── Internal rating (item 7) ────────────────────────────────────────────────

def serialize_rating_entry(e: DepartmentRatingEntry, forced_names: Optional[dict] = None) -> dict:
    return {
        "id": e.id,
        "admin_id": e.admin_id,
        "admin_name": e.admin.discord_username if e.admin else None,
        "forced_display_name": (forced_names or {}).get(e.admin_id),
        "delta": e.delta,
        "reason": e.reason,
        "category": e.category,
        "created_by": e.created_by.discord_username if e.created_by else None,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/api/v1/admin/departments/{department_id}/rating")
async def api_department_rating_leaderboard(department_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "rating", "view")

    from sqlalchemy import func as sa_func

    totals = (
        db.query(DepartmentRatingEntry.admin_id, sa_func.sum(DepartmentRatingEntry.delta).label("total"))
        .filter(DepartmentRatingEntry.department_id == dept.id)
        .group_by(DepartmentRatingEntry.admin_id)
        .all()
    )
    totals_by_admin = {row.admin_id: row.total for row in totals}
    members = (
        db.query(DepartmentMember)
        .options(joinedload(DepartmentMember.admin))
        .filter(DepartmentMember.department_id == dept.id)
        .all()
    )
    forced_names = _forced_names_by_admin_id(db, [m.admin_id for m in members])
    leaderboard = sorted(
        (
            {
                "admin_id": m.admin_id,
                "admin_name": m.admin.discord_username if m.admin else None,
                "forced_display_name": forced_names.get(m.admin_id),
                "total": totals_by_admin.get(m.admin_id, 0),
            }
            for m in members
        ),
        key=lambda r: r["total"],
        reverse=True,
    )
    return {"leaderboard": leaderboard}


@router.get("/api/v1/admin/departments/{department_id}/rating/entries")
async def api_list_department_rating_entries(department_id: int, admin_id: Optional[int] = None, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "rating", "view")

    q = (
        db.query(DepartmentRatingEntry)
        .options(joinedload(DepartmentRatingEntry.admin), joinedload(DepartmentRatingEntry.created_by))
        .filter(DepartmentRatingEntry.department_id == dept.id)
    )
    if admin_id is not None:
        q = q.filter(DepartmentRatingEntry.admin_id == admin_id)
    entries = q.order_by(DepartmentRatingEntry.created_at.desc()).limit(200).all()
    forced_names = _forced_names_by_admin_id(db, [e.admin_id for e in entries])
    return {"entries": [serialize_rating_entry(e, forced_names) for e in entries]}


@router.post("/api/v1/admin/departments/{department_id}/rating/entries")
async def api_create_department_rating_entry(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "rating", "edit")

    body = await request.json()
    admin_id = body.get("admin_id")
    delta = body.get("delta")
    if not admin_id or not isinstance(delta, int) or delta == 0:
        raise HTTPException(status_code=400, detail="Укажите сотрудника и ненулевое значение баллов")
    if not get_member(db, dept.id, admin_id):
        raise HTTPException(status_code=404, detail="Этот сотрудник не состоит в отделе")

    entry = DepartmentRatingEntry(
        department_id=dept.id,
        admin_id=admin_id,
        delta=delta,
        reason=(body.get("reason") or "").strip() or None,
        category=(body.get("category") or "").strip() or None,
        created_by_admin_id=current_admin.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"status": "ok", "entry": serialize_rating_entry(entry)}


@router.delete("/api/v1/admin/departments/{department_id}/rating/entries/{entry_id}")
async def api_delete_department_rating_entry(department_id: int, entry_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "rating", "edit")

    entry = db.query(DepartmentRatingEntry).filter(DepartmentRatingEntry.id == entry_id, DepartmentRatingEntry.department_id == dept.id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(entry)
    db.commit()
    return {"status": "ok"}


# ── Reprimands (item 12) — head-only, unlike almost everything else here ───

def serialize_reprimand(r: "DepartmentReprimand") -> dict:
    return {
        "id": r.id,
        "admin_id": r.admin_id,
        "reason": r.reason,
        "issued_by": r.issued_by.discord_username if r.issued_by else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/api/v1/admin/departments/{department_id}/reprimands")
async def api_list_department_reprimands(department_id: int, admin_id: Optional[int] = None, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "roster", "view")

    from models import DepartmentReprimand
    q = (
        db.query(DepartmentReprimand)
        .options(joinedload(DepartmentReprimand.issued_by))
        .filter(DepartmentReprimand.department_id == dept.id)
    )
    if admin_id is not None:
        q = q.filter(DepartmentReprimand.admin_id == admin_id)
    reprimands = q.order_by(DepartmentReprimand.created_at.desc()).limit(200).all()
    return {"reprimands": [serialize_reprimand(r) for r in reprimands]}


@router.post("/api/v1/admin/departments/{department_id}/reprimands")
async def api_create_department_reprimand(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    caller_member = get_member(db, dept.id, current_admin.id)
    # Deliberately head-only — NOT is_deputy_head, unlike every other
    # department-authority check in this file. Explicit user request:
    # "Максимум может выбирать глава отдела (не зам)".
    if not (current_admin.is_superuser or (caller_member and caller_member.is_head)):
        raise HTTPException(status_code=403, detail="Выговор может выдать только глава отдела")

    body = await request.json()
    admin_id = body.get("admin_id")
    reason = (body.get("reason") or "").strip()
    if not admin_id or not reason:
        raise HTTPException(status_code=400, detail="Укажите сотрудника и причину")
    if not get_member(db, dept.id, admin_id):
        raise HTTPException(status_code=404, detail="Этот сотрудник не состоит в отделе")

    from models import DepartmentReprimand
    reprimand = DepartmentReprimand(
        department_id=dept.id, admin_id=admin_id, reason=reason, issued_by_admin_id=current_admin.id,
    )
    db.add(reprimand)
    db.commit()
    db.refresh(reprimand)
    return {"status": "ok", "reprimand": serialize_reprimand(reprimand)}


@router.delete("/api/v1/admin/departments/{department_id}/reprimands/{reprimand_id}")
async def api_delete_department_reprimand(department_id: int, reprimand_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    caller_member = get_member(db, dept.id, current_admin.id)
    if not (current_admin.is_superuser or (caller_member and caller_member.is_head)):
        raise HTTPException(status_code=403, detail="Выговор может отменить только глава отдела")

    from models import DepartmentReprimand
    reprimand = db.query(DepartmentReprimand).filter(DepartmentReprimand.id == reprimand_id, DepartmentReprimand.department_id == dept.id).first()
    if not reprimand:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(reprimand)
    db.commit()
    return {"status": "ok"}


# ── Test server — Pterodactyl power/status only (item 4) ───────────────────

PTERODACTYL_POWER_SIGNALS = {"start", "stop", "restart", "kill"}


def _pterodactyl_settings():
    from config import get_settings
    settings = get_settings()
    if not settings.pterodactyl_panel_url or not settings.pterodactyl_api_key:
        raise HTTPException(status_code=503, detail="Интеграция с Pterodactyl не настроена")
    return settings


@router.get("/api/v1/admin/departments/{department_id}/test-server/status")
async def api_department_test_server_status(department_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "test_server", "view")
    if not dept.pterodactyl_server_uuid:
        raise HTTPException(status_code=400, detail="Для отдела не привязан тестовый сервер")
    settings = _pterodactyl_settings()

    import httpx
    url = f"{settings.pterodactyl_panel_url.rstrip('/')}/api/client/servers/{dept.pterodactyl_server_uuid}/resources"
    headers = {"Authorization": f"Bearer {settings.pterodactyl_api_key}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json().get("attributes", {})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ошибка обращения к Pterodactyl: {e}")

    resources = data.get("resources", {})
    return {
        "state": data.get("current_state"),
        "cpu_percent": resources.get("cpu_absolute"),
        "memory_bytes": resources.get("memory_bytes"),
        "memory_limit_bytes": resources.get("memory_limit_bytes"),
        "disk_bytes": resources.get("disk_bytes"),
    }


@router.post("/api/v1/admin/departments/{department_id}/test-server/power")
async def api_department_test_server_power(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "test_server", "edit")
    if not dept.pterodactyl_server_uuid:
        raise HTTPException(status_code=400, detail="Для отдела не привязан тестовый сервер")

    body = await request.json()
    signal = body.get("action")
    if signal not in PTERODACTYL_POWER_SIGNALS:
        raise HTTPException(status_code=400, detail="Недопустимое действие")
    settings = _pterodactyl_settings()

    import httpx
    url = f"{settings.pterodactyl_panel_url.rstrip('/')}/api/client/servers/{dept.pterodactyl_server_uuid}/power"
    headers = {"Authorization": f"Bearer {settings.pterodactyl_api_key}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json={"signal": signal})
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ошибка обращения к Pterodactyl: {e}")

    return {"status": "ok"}


# ── Chat — multiple linked Discord channels per department (item 6) ────────

def serialize_chat_channel(c: DepartmentChatChannel) -> dict:
    return {"id": c.id, "discord_channel_id": c.discord_channel_id, "label": c.label}


def serialize_chat_message(m: DepartmentChatMessage) -> dict:
    return {
        "id": m.id,
        "author_name": m.author_name,
        "author_avatar_url": m.author_avatar_url,
        "content": m.content,
        "sent_by_admin": m.sent_by.discord_username if m.sent_by else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/api/v1/admin/departments/{department_id}/chat/channels")
async def api_list_chat_channels(department_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "chat", "view")
    channels = db.query(DepartmentChatChannel).filter(DepartmentChatChannel.department_id == dept.id).order_by(DepartmentChatChannel.id).all()
    return {"channels": [serialize_chat_channel(c) for c in channels]}


@router.post("/api/v1/admin/departments/{department_id}/chat/channels")
async def api_add_chat_channel(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    member = require_department_module(db, dept, current_admin, "chat", "edit")
    # Wiring a Discord channel ID is identity-sensitive, same reasoning as
    # access-level role IDs — restrict to head/superuser.
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    body = await request.json()
    discord_channel_id = (body.get("discord_channel_id") or "").strip()
    if not discord_channel_id:
        raise HTTPException(status_code=400, detail="Укажите ID канала Discord")

    channel = DepartmentChatChannel(
        department_id=dept.id,
        discord_channel_id=discord_channel_id,
        label=(body.get("label") or "").strip() or None,
    )
    db.add(channel)
    db.commit()
    return {"status": "ok", "channel": serialize_chat_channel(channel)}


@router.delete("/api/v1/admin/departments/{department_id}/chat/channels/{channel_id}")
async def api_delete_chat_channel(department_id: int, channel_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    member = require_department_module(db, dept, current_admin, "chat", "edit")
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    channel = db.query(DepartmentChatChannel).filter(DepartmentChatChannel.id == channel_id, DepartmentChatChannel.department_id == dept.id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Канал не найден")
    db.delete(channel)
    db.commit()
    return {"status": "ok"}


@router.get("/api/v1/admin/departments/{department_id}/chat/channels/{channel_id}/messages")
async def api_list_chat_messages(department_id: int, channel_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "chat", "view")

    channel = db.query(DepartmentChatChannel).filter(DepartmentChatChannel.id == channel_id, DepartmentChatChannel.department_id == dept.id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Канал не найден")

    messages = (
        db.query(DepartmentChatMessage)
        .options(joinedload(DepartmentChatMessage.sent_by))
        .filter(DepartmentChatMessage.channel_id == channel.id)
        .order_by(DepartmentChatMessage.created_at.desc())
        .limit(100)
        .all()
    )
    return {"messages": [serialize_chat_message(m) for m in reversed(messages)]}


@router.post("/api/v1/admin/departments/{department_id}/chat/channels/{channel_id}/messages")
async def api_send_chat_message(department_id: int, channel_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    require_departments_page(current_admin, "view")
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "chat", "edit")

    channel = db.query(DepartmentChatChannel).filter(DepartmentChatChannel.id == channel_id, DepartmentChatChannel.department_id == dept.id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Канал не найден")

    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    message = DepartmentChatMessage(
        channel_id=channel.id,
        author_name=current_admin.discord_username,
        author_avatar_url=current_admin.discord_avatar,
        content=content[:4000],
        sent_by_admin_id=current_admin.id,
    )
    db.add(message)
    db.commit()
    # Reuses the existing generic "send_message" bot action (same bridge
    # dispatch/tickets/etc. already use) — no new bot action_type needed.
    from discord_bot import bot as discord_bot
    discord_bot.schedule(discord_bot.execute_bot_action("send_message", {
        "channel_id": channel.discord_channel_id, "content": content[:2000],
    }))
    return {"status": "ok", "message": serialize_chat_message(message)}


# ── Document folders + per-folder ACLs (item 5) ─────────────────────────

def serialize_dept_folder(f: DepartmentDocumentFolder) -> dict:
    return {
        "id": f.id, "name": f.name, "parent_id": f.parent_id,
        "discord_channel_id": f.discord_channel_id,
        "auto_apply_permissions": f.auto_apply_permissions,
    }


def serialize_folder_permission(p: DepartmentFolderPermission) -> dict:
    return {
        "id": p.id,
        "grantee_type": p.grantee_type,
        "access_level_key": p.access_level_key,
        "member_admin_id": p.member_admin_id,
        "member_name": p.member.discord_username if p.member else None,
        "level": p.level,
    }


@router.get("/api/v1/admin/departments/{department_id}/document-folders")
async def api_list_dept_document_folders(department_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    # No require_departments_page gate here (unlike most of this router) —
    # same reasoning as routes/documents.py's department-scoped endpoints:
    # a department member browsing their own department's directory
    # shouldn't need a separate site-wide "departments" grant, only the
    # module permission require_department_module already checks below.
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "documentation", "view")
    folders = (
        db.query(DepartmentDocumentFolder)
        .filter(DepartmentDocumentFolder.department_id == dept.id)
        .order_by(DepartmentDocumentFolder.name)
        .all()
    )
    member = get_member(db, dept.id, current_admin.id)
    held_keys = set(member.held_level_keys or []) if member else set()
    visible = [f for f in folders if _folder_visible(db, f, current_admin, member, held_keys)]
    return {"folders": [serialize_dept_folder(f) for f in visible]}


@router.post("/api/v1/admin/departments/{department_id}/document-folders")
async def api_create_dept_document_folder(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "documentation", "edit")

    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название папки")
    parent_id = body.get("parent_id")
    if parent_id is not None:
        parent = db.query(DepartmentDocumentFolder).filter(
            DepartmentDocumentFolder.id == parent_id, DepartmentDocumentFolder.department_id == dept.id
        ).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Родительская папка не найдена")

    folder = DepartmentDocumentFolder(
        department_id=dept.id, name=name, parent_id=parent_id, created_by_admin_id=current_admin.id,
        discord_channel_id=(body.get("discord_channel_id") or "").strip() or None,
        auto_apply_permissions=bool(body.get("auto_apply_permissions")),
    )
    db.add(folder)
    db.commit()
    return {"status": "ok", "folder": serialize_dept_folder(folder)}


@router.patch("/api/v1/admin/departments/{department_id}/document-folders/{folder_id}")
async def api_rename_dept_document_folder(department_id: int, folder_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "documentation", "edit")
    folder = db.query(DepartmentDocumentFolder).filter(
        DepartmentDocumentFolder.id == folder_id, DepartmentDocumentFolder.department_id == dept.id
    ).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")

    body = await request.json()
    if body.get("name", "").strip() if isinstance(body.get("name"), str) else False:
        folder.name = body["name"].strip()
    if "parent_id" in body:
        new_parent_id = body["parent_id"]
        if new_parent_id is not None:
            new_parent = db.query(DepartmentDocumentFolder).filter(
                DepartmentDocumentFolder.id == new_parent_id, DepartmentDocumentFolder.department_id == dept.id
            ).first()
            if not new_parent:
                raise HTTPException(status_code=404, detail="Родительская папка не найдена")
        folder.parent_id = new_parent_id
    if "discord_channel_id" in body:
        folder.discord_channel_id = (body.get("discord_channel_id") or "").strip() or None
    if "auto_apply_permissions" in body:
        folder.auto_apply_permissions = bool(body.get("auto_apply_permissions"))
    db.commit()
    return {"status": "ok", "folder": serialize_dept_folder(folder)}


@router.delete("/api/v1/admin/departments/{department_id}/document-folders/{folder_id}")
async def api_delete_dept_document_folder(department_id: int, folder_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "documentation", "edit")
    folder = db.query(DepartmentDocumentFolder).filter(
        DepartmentDocumentFolder.id == folder_id, DepartmentDocumentFolder.department_id == dept.id
    ).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")

    from models import Document
    # Re-parent child folders/documents up one level, same UX as personal folders.
    db.query(DepartmentDocumentFolder).filter(DepartmentDocumentFolder.parent_id == folder.id).update({"parent_id": folder.parent_id})
    db.query(Document).filter(Document.department_folder_id == folder.id).update({"department_folder_id": folder.parent_id})
    db.delete(folder)
    db.commit()
    return {"status": "ok"}


@router.get("/api/v1/admin/departments/{department_id}/document-folders/{folder_id}/permissions")
async def api_list_folder_permissions(department_id: int, folder_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    folder = db.query(DepartmentDocumentFolder).filter(
        DepartmentDocumentFolder.id == folder_id, DepartmentDocumentFolder.department_id == dept.id
    ).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    grants = (
        db.query(DepartmentFolderPermission)
        .options(joinedload(DepartmentFolderPermission.member))
        .filter(DepartmentFolderPermission.folder_id == folder.id)
        .all()
    )
    return {"permissions": [serialize_folder_permission(g) for g in grants]}


@router.post("/api/v1/admin/departments/{department_id}/document-folders/{folder_id}/permissions")
async def api_grant_folder_permission(department_id: int, folder_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    # Identity-sensitive (grants extra access on top of the department's
    # normal module permissions) — head/superuser only, same reasoning as
    # access-level Discord-role wiring and chat-channel wiring.
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    folder = db.query(DepartmentDocumentFolder).filter(
        DepartmentDocumentFolder.id == folder_id, DepartmentDocumentFolder.department_id == dept.id
    ).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")

    body = await request.json()
    grantee_type = body.get("grantee_type")
    level = body.get("level") if body.get("level") in ("view", "edit") else "view"
    if grantee_type == "access_level":
        access_level_key = body.get("access_level_key")
        valid_keys = {l.key for l in dept.access_levels}
        if access_level_key not in valid_keys:
            raise HTTPException(status_code=400, detail="Неизвестный уровень доступа")
        grant = DepartmentFolderPermission(folder_id=folder.id, grantee_type="access_level", access_level_key=access_level_key, level=level)
    elif grantee_type == "member":
        member_admin_id = body.get("member_admin_id")
        if not get_member(db, dept.id, member_admin_id):
            raise HTTPException(status_code=400, detail="Этот пользователь не состоит в отделе")
        grant = DepartmentFolderPermission(folder_id=folder.id, grantee_type="member", member_admin_id=member_admin_id, level=level)
    else:
        raise HTTPException(status_code=400, detail="grantee_type должен быть access_level или member")

    db.add(grant)
    db.commit()
    return {"status": "ok", "permission": serialize_folder_permission(grant)}


@router.delete("/api/v1/admin/departments/{department_id}/document-folders/{folder_id}/permissions/{permission_id}")
async def api_revoke_folder_permission(department_id: int, folder_id: int, permission_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    member = get_member(db, dept.id, current_admin.id)
    if not is_head_or_deputy_or_superuser(current_admin, member):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    # folder_id was never checked against dept.id here — a head of ANY
    # department could revoke a folder-permission grant belonging to a
    # completely different department just by knowing its folder_id/
    # permission_id. Cross-check the folder first, like every sibling
    # endpoint above already does.
    folder = db.query(DepartmentDocumentFolder).filter(
        DepartmentDocumentFolder.id == folder_id, DepartmentDocumentFolder.department_id == dept.id
    ).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    grant = db.query(DepartmentFolderPermission).filter(
        DepartmentFolderPermission.id == permission_id, DepartmentFolderPermission.folder_id == folder.id
    ).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Право не найдено")
    db.delete(grant)
    db.commit()
    return {"status": "ok"}


# ── Document tags — each department has its own independent tag vocabulary ──
# (creation/deletion of a tag itself requires module-wide "documentation":
# "edit"; ASSIGNING an existing tag to one specific document only needs edit
# access on that document — see routes/documents.py's per-document endpoints,
# gated via the existing get_document_access/load_document_or_404, which
# already resolves department-wide "documentation":"edit" to "edit" access
# on every document in that department, same as any other edit action.)

def serialize_dept_tag(t: DepartmentDocumentTag) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color}


@router.get("/api/v1/admin/departments/{department_id}/document-tags")
async def api_list_department_document_tags(department_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "documentation", "view")
    tags = (
        db.query(DepartmentDocumentTag)
        .filter(DepartmentDocumentTag.department_id == dept.id)
        .order_by(DepartmentDocumentTag.name)
        .all()
    )
    return {"tags": [serialize_dept_tag(t) for t in tags]}


@router.post("/api/v1/admin/departments/{department_id}/document-tags")
async def api_create_department_document_tag(department_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "documentation", "edit")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название тега")
    existing = db.query(DepartmentDocumentTag).filter(
        DepartmentDocumentTag.department_id == dept.id, DepartmentDocumentTag.name == name
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Тег с таким названием уже есть")
    tag = DepartmentDocumentTag(
        department_id=dept.id, name=name, color=body.get("color") or "#5A646E",
        created_by_admin_id=current_admin.id,
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"status": "ok", "tag": serialize_dept_tag(tag)}


@router.delete("/api/v1/admin/departments/{department_id}/document-tags/{tag_id}")
async def api_delete_department_document_tag(department_id: int, tag_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin)):
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "documentation", "edit")
    tag = db.query(DepartmentDocumentTag).filter(
        DepartmentDocumentTag.id == tag_id, DepartmentDocumentTag.department_id == dept.id
    ).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Тег не найден")
    db.delete(tag)
    db.commit()
    return {"status": "ok"}
