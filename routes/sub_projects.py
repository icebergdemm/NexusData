"""
Admin panel routes for Sub-project management (submarine maps).
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
import re
import secrets
import logging
import os

from database import get_db
from security import check_permission
from config import get_settings
from models import AdminUser, AdminSession
from datetime import datetime

router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals.update(max=max, min=min, check_permission=check_permission)
settings = get_settings()
logger = logging.getLogger(__name__)


def _get_sub_service(db: Session):
    """Lazy import to avoid module-not-found at import time."""
    import services_sub_projects
    return services_sub_projects.SubProjectService(db)


def _has(admin: AdminUser, own_action: str, all_action: str, is_owner: bool) -> bool:
    """True if the admin holds the "_all" action (any project), or holds the
    own-project action AND actually owns this particular project."""
    if check_permission(admin, "sub_projects", all_action):
        return True
    return bool(is_owner) and check_permission(admin, "sub_projects", own_action)


def _slugify_category(name: str) -> str:
    """[a-z0-9]-only slug with a random fallback — a name written entirely in
    Cyrillic (or symbols) strips to "", which collided against the
    slug column's UNIQUE constraint on the second such category and surfaced
    as a generic "already exists" error (same fallback pattern as
    routes/departments.py's _slugify)."""
    base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return base or f"cat-{secrets.token_hex(4)}"


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


# ══════════════════════════════════════════════════════════════════════════════
# TEST SERVER — "Сервер 2" / ASPER2 power control, from the normal Sub-
# projects page. A DIFFERENT lever than a department's own test_server
# module (routes/departments.py) — this one is site-wide, gated by the
# sub_projects:manage_test_server permission, and always targets the one
# Pterodactyl server configured in settings.sub_projects_test_server_uuid
# (the same physical server Sub-projects/Полигон deploy .sub files to).
# ══════════════════════════════════════════════════════════════════════════════

PTERODACTYL_POWER_SIGNALS = {"start", "stop", "restart", "kill"}


def _pterodactyl_settings():
    if not settings.pterodactyl_panel_url or not settings.pterodactyl_api_key:
        raise HTTPException(status_code=503, detail="Интеграция с Pterodactyl не настроена")
    if not settings.sub_projects_test_server_uuid:
        raise HTTPException(status_code=400, detail="Тестовый сервер не привязан (sub_projects_test_server_uuid)")
    return settings


@router.get("/api/v1/admin/sub-projects/test-server/status")
async def api_sub_projects_test_server_status(current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "sub_projects", "manage_test_server"):
        raise HTTPException(status_code=403)
    cfg = _pterodactyl_settings()

    import httpx
    url = f"{cfg.pterodactyl_panel_url.rstrip('/')}/api/client/servers/{cfg.sub_projects_test_server_uuid}/resources"
    headers = {"Authorization": f"Bearer {cfg.pterodactyl_api_key}", "Accept": "application/json"}
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


@router.post("/api/v1/admin/sub-projects/test-server/power")
async def api_sub_projects_test_server_power(request: Request, current_admin: AdminUser = Depends(get_current_admin)):
    if not check_permission(current_admin, "sub_projects", "manage_test_server"):
        raise HTTPException(status_code=403)
    cfg = _pterodactyl_settings()

    body = await request.json()
    signal = body.get("action")
    if signal not in PTERODACTYL_POWER_SIGNALS:
        raise HTTPException(status_code=400, detail="Недопустимое действие")

    import httpx
    url = f"{cfg.pterodactyl_panel_url.rstrip('/')}/api/client/servers/{cfg.sub_projects_test_server_uuid}/power"
    headers = {"Authorization": f"Bearer {cfg.pterodactyl_api_key}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json={"signal": signal})
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ошибка обращения к Pterodactyl: {e}")
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE THUMBNAIL ENDPOINT (compressed, cached on disk)
# ══════════════════════════════════════════════════════════════════════════════

_THUMB_DIR = os.path.join(settings.sub_projects_upload_dir, "thumbs")
os.makedirs(_THUMB_DIR, exist_ok=True)


def _generate_thumbnail(source_path: str, thumb_path: str, max_width: int = 800, quality: int = 85):
    """Generate a compressed thumbnail from the source image, preserving transparency."""
    try:
        from PIL import Image
        img = Image.open(source_path)
        # Resize maintaining aspect ratio
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        # If image has alpha channel — save as WebP (supports transparency + good compression)
        if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            if img.mode == "P":
                img = img.convert("RGBA")
            img.save(thumb_path, "WEBP", quality=quality, method=4)
        else:
            # No transparency — save as JPEG
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(thumb_path, "JPEG", quality=quality, optimize=True)
    except Exception as e:
        logger.warning(f"Thumbnail generation failed for {source_path}: {e}")
        import shutil
        shutil.copyfile(source_path, thumb_path)


@router.get("/sub_projects/thumbs/{filename}")
async def sub_project_thumbnail(filename: str):
    """Serve compressed thumbnail of a sub-project image.

    Generates on first request and caches to disk.
    - PNG with transparency → WebP (85% quality, preserves alpha)
    - Other formats → JPEG (85% quality)
    Max width: 800px.
    """
    # Sanitize filename
    safe_name = os.path.basename(filename)
    if not safe_name or ".." in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # "v2" cache-busts thumbnails generated under the old 400px/60%-quality
    # defaults — old thumb_{name}.* files are simply left orphaned on disk.
    thumb_jpg = os.path.join(_THUMB_DIR, f"thumb_v2_{safe_name}.jpg")
    thumb_webp = os.path.join(_THUMB_DIR, f"thumb_v2_{safe_name}.webp")

    if os.path.exists(thumb_webp):
        return FileResponse(thumb_webp, media_type="image/webp",
                            headers={"Cache-Control": "public, max-age=86400"})
    if os.path.exists(thumb_jpg):
        return FileResponse(thumb_jpg, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})

    # Generate from source
    source_path = os.path.join(settings.sub_projects_upload_dir, "images", safe_name)
    if not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="Image not found")

    # Detect if source has transparency to choose output format
    has_alpha = False
    try:
        from PIL import Image
        with Image.open(source_path) as img:
            has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    except Exception:
        pass

    thumb_path = thumb_webp if has_alpha else thumb_jpg
    _generate_thumbnail(source_path, thumb_path)

    media_type = "image/webp" if has_alpha else "image/jpeg"
    return FileResponse(thumb_path, media_type=media_type,
                        headers={"Cache-Control": "public, max-age=86400"})


# ── Reusable helpers ─────────────────────────────────────────────────────────

async def ensure_csrf(request: Request, db: Session):
    if request.url.path.startswith("/api/") and getattr(request.state, "nd_auth_type", None) == "login_key":
        return
    form = await request.form()
    csrf_token = form.get("csrf_token")
    session_csrf = request.session.get("csrf_token")
    if csrf_token and session_csrf and secrets.compare_digest(str(csrf_token), str(session_csrf)):
        return
    token = request.cookies.get("admin_token")
    if token:
        now = datetime.utcnow()
        s = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if s and (s.expires_at is None or s.expires_at > now):
            return
    raise HTTPException(status_code=403, detail="Invalid CSRF token")


def push_flash(request: Request, level: str, message: str):
    flashes = request.session.get("_flashes", [])
    flashes.append({"level": level, "message": message})
    request.session["_flashes"] = flashes


def pop_flashes(request: Request):
    flashes = request.session.get("_flashes", [])
    if "_flashes" in request.session:
        request.session.pop("_flashes", None)
    return flashes


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PAGE
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/adminold/sub-projects", response_class=HTMLResponse)
async def sub_projects_list(
    request: Request,
    search: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    crew_min: Optional[str] = Query(None),
    crew_max: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """List all sub-projects with search, filters, and categories."""
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    # Parse optional int params (HTML forms send "" for empty fields)
    crew_min_int = int(crew_min) if crew_min and crew_min.strip().isdigit() else None
    crew_max_int = int(crew_max) if crew_max and crew_max.strip().isdigit() else None
    category_id_int = int(category_id) if category_id and category_id.strip().isdigit() else None
    search_str = search.strip() if search and search.strip() else None
    author_str = author.strip() if author and author.strip() else None

    svc = _get_sub_service(db)
    projects = svc.get_all(
        search=search_str,
        author=author_str,
        crew_min=crew_min_int,
        crew_max=crew_max_int,
        category_id=category_id_int,
    )

    projects_data = []
    for p in projects:
        versions = svc.get_versions(p.id)
        categories = svc.get_project_categories(p.id)
        projects_data.append({
            "project": p,
            "version_count": len(versions),
            "latest_version": versions[0] if versions else None,
            "categories": categories,
        })

    # Data for filters
    all_categories = svc.get_all_categories()
    all_authors = svc.get_unique_authors()

    # Superuser: category permission settings
    from models import AdminPrivilegeGroup, AppSetting
    nd_privilege_groups = db.query(AdminPrivilegeGroup).order_by(AdminPrivilegeGroup.name).all()

    def _get_setting(key, default=""):
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        return row.value if row else default

    cat_create_groups = _get_setting("sub_cat_create_groups", "[]")
    cat_assign_groups = _get_setting("sub_cat_assign_groups", "[]")
    cat_default_id = _get_setting("sub_cat_default_id", "")

    import json as _json
    try:
        cat_create_groups = [str(x) for x in _json.loads(cat_create_groups)]
    except Exception:
        cat_create_groups = []
    try:
        cat_assign_groups = [str(x) for x in _json.loads(cat_assign_groups)]
    except Exception:
        cat_assign_groups = []

    return templates.TemplateResponse("admin/sub_projects.html", {
        "request": request,
        "admin": current_admin,
        "projects_data": projects_data,
        "flashes": pop_flashes(request),
        "sftp_server_1_name": settings.sftp_server_1_name,
        "sftp_server_2_name": settings.sftp_server_2_name,
        # Filters
        "search_query": search_str or "",
        "filter_author": author_str or "",
        "filter_crew_min": crew_min_int,
        "filter_crew_max": crew_max_int,
        "filter_category_id": category_id_int,
        "all_categories": all_categories,
        "all_authors": all_authors,
        # Category permissions (superuser)
        "nd_privilege_groups": nd_privilege_groups,
        "cat_create_groups": cat_create_groups,
        "cat_assign_groups": cat_assign_groups,
        "cat_default_id": cat_default_id,
    })


# ══════════════════════════════════════════════════════════════════════════════
# SUB-PROJECTS JSON API (for the React SPA)
# ══════════════════════════════════════════════════════════════════════════════

def _serialize_category(c) -> dict:
    return {
        "id": c.id, "name": c.name, "slug": c.slug, "description": c.description,
        "color": c.color, "icon": c.icon, "sort_order": c.sort_order, "parent_id": c.parent_id,
        "is_default": bool(c.is_default),
    }


def _serialize_version(v) -> dict:
    return {
        "id": v.id, "project_id": v.project_id, "version_number": v.version_number,
        "filename": v.filename, "file_size": v.file_size, "changelog": v.changelog,
        "deployed_server_1": bool(v.deployed_server_1), "deployed_server_2": bool(v.deployed_server_2),
        "uploaded_at": v.uploaded_at.isoformat() if v.uploaded_at else None,
        "is_deleted": bool(v.is_deleted),
    }


def _serialize_project(p) -> dict:
    return {
        "id": p.id, "name": p.name, "author": p.author, "project_type": p.project_type,
        "crew": p.crew, "weapons": p.weapons, "has_reactor": p.has_reactor,
        "fabricator_medfab": p.fabricator_medfab, "has_deconstructor": p.has_deconstructor,
        "docking_ports": p.docking_ports, "decks": p.decks, "max_speed": p.max_speed,
        "extra_equipment": p.extra_equipment, "features": p.features, "description": p.description,
        "image_filename": p.image_filename, "discord_message_id": p.discord_message_id,
        "created_by_admin_id": p.created_by_admin_id,
        "department_id": p.department_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/api/v1/admin/sub-projects")
async def api_sub_projects_list(
    search: Optional[str] = Query(None), author: Optional[str] = Query(None),
    crew_min: Optional[int] = Query(None), crew_max: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403)

    svc = _get_sub_service(db)
    projects = svc.get_all(search=search, author=author, crew_min=crew_min, crew_max=crew_max, category_id=category_id)

    projects_data = []
    for p in projects:
        versions = svc.get_versions(p.id)
        categories = svc.get_project_categories(p.id)
        projects_data.append({
            "project": _serialize_project(p),
            "version_count": len(versions),
            "latest_version": _serialize_version(versions[0]) if versions else None,
            "categories": [_serialize_category(c) for c in categories],
        })

    return {
        "projects": projects_data,
        "all_categories": [_serialize_category(c) for c in svc.get_all_categories()],
        "all_authors": svc.get_unique_authors(),
        "sftp_server_1_name": settings.sftp_server_1_name,
        "sftp_server_2_name": settings.sftp_server_2_name,
        "can_create": check_permission(current_admin, "sub_projects", "create"),
        "can_manage_categories": check_permission(current_admin, "sub_projects", "manage_categories"),
        "can_manage_test_server": check_permission(current_admin, "sub_projects", "manage_test_server"),
        "can_reorder": check_permission(current_admin, "sub_projects", "edit_all"),
    }


@router.post("/api/v1/admin/sub-projects/reorder")
async def api_sub_projects_reorder(
    request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    """Drag-and-drop card reorder on the Sub-projects grid. Gated by
    edit_all (not the own-project "edit") since a single drag can reshuffle
    cards belonging to different owners at once."""
    if not check_permission(current_admin, "sub_projects", "edit_all"):
        raise HTTPException(status_code=403)
    body = await request.json()
    project_ids = body.get("project_ids") or []
    svc = _get_sub_service(db)
    svc.reorder(project_ids)
    return {"status": "ok"}


@router.get("/api/v1/admin/sub-projects/{project_id}")
async def api_sub_project_detail(
    project_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403)

    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    all_versions = svc.get_versions(project_id, include_deleted=True)
    versions = [v for v in all_versions if not v.is_deleted]
    is_owner = project.created_by_admin_id == current_admin.id
    can_restore = _has(current_admin, "restore_version", "restore_version_all", is_owner)
    # Deleted versions are only worth sending down (and only visible) to
    # someone who could actually restore one — otherwise it's just dead
    # weight/an info leak about deleted content for everyone else.
    deleted_versions = [v for v in all_versions if v.is_deleted] if can_restore else []
    categories = svc.get_project_categories(project_id)

    can_edit_category_assign = (
        check_permission(current_admin, "sub_projects", "edit")
        or check_permission(current_admin, "sub_projects", "edit_all")
    )

    return {
        "project": _serialize_project(project),
        "versions": [_serialize_version(v) for v in versions],
        "deleted_versions": [_serialize_version(v) for v in deleted_versions],
        "categories": [_serialize_category(c) for c in categories],
        # Full catalog for the category picker — the dedicated
        # /sub-projects-categories endpoint is manage_categories-gated (it's
        # the category-management page), but assigning an EXISTING category
        # to a project only needs edit/edit_all (see assign/remove below), so
        # the picker's option list is served from here at the same "view"
        # gate this whole detail endpoint already requires.
        "all_categories": [_serialize_category(c) for c in svc.get_all_categories()],
        "sftp_server_1_name": settings.sftp_server_1_name,
        "sftp_server_2_name": settings.sftp_server_2_name,
        "is_owner": is_owner,
        "can_edit_this": _has(current_admin, "edit", "edit_all", is_owner),
        "can_delete_this": _has(current_admin, "delete", "delete_all", is_owner),
        "can_upload_version": _has(current_admin, "upload_version", "upload_version_all", is_owner),
        "can_delete_version": _has(current_admin, "delete_version", "delete_version_all", is_owner),
        "can_restore_version": can_restore,
        "can_download": _has(current_admin, "download", "download_all", is_owner),
        "can_deploy_server_1": check_permission(current_admin, "sub_projects", "deploy_server_1"),
        "can_deploy_server_2": check_permission(current_admin, "sub_projects", "deploy_server_2"),
        "can_undeploy_server_1": check_permission(current_admin, "sub_projects", "undeploy_server_1"),
        "can_undeploy_server_2": check_permission(current_admin, "sub_projects", "undeploy_server_2"),
        "can_publish_discord": _has(current_admin, "publish_discord", "publish_discord_all", is_owner),
        "can_change_owner": check_permission(current_admin, "sub_projects", "change_owner"),
        "can_edit_categories": can_edit_category_assign,
        "can_check_gost": check_permission(current_admin, "sub_checker", "check"),
        # Последний отчёт по каждой версии — карточка проекта показывает
        # результат прошлой проверки рядом с версией, не запуская новую.
        "checks": _latest_checks(db, project_id),
    }


def _latest_checks(db: Session, project_id: int) -> dict:
    """version_id → краткая сводка последней проверки этой версии.

    Тянутся ТОЛЬКО нужные колонки: в SubCheckResult.report лежит полный отчёт
    на несколько десятков килобайт, и загружать его сотнями строк ради пары
    счётчиков в карточке проекта — верный способ раздуть ответ до мегабайт."""
    from models import SubCheckResult
    rows = (
        db.query(
            SubCheckResult.id, SubCheckResult.version_id, SubCheckResult.passed,
            SubCheckResult.error_count, SubCheckResult.warning_count,
            SubCheckResult.ship_class, SubCheckResult.tonnage, SubCheckResult.created_at,
        )
        .filter(SubCheckResult.project_id == project_id, SubCheckResult.version_id.isnot(None))
        .order_by(SubCheckResult.id.desc())
        .limit(200)
        .all()
    )
    out: dict = {}
    for row in rows:
        key = str(row.version_id)
        if key in out:
            continue
        out[key] = {
            "id": row.id, "passed": bool(row.passed),
            "error_count": row.error_count, "warning_count": row.warning_count,
            "ship_class": row.ship_class, "tonnage": row.tonnage,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
    return out


def _sub_project_form_to_data(name, author, project_type, crew, crew_min, crew_avg, crew_max,
                               weapons, has_reactor, fabricator_medfab, has_deconstructor,
                               docking_ports, decks, max_speed, extra_equipment, features, description) -> dict:
    if crew_min is not None and crew_avg is not None and crew_max is not None:
        crew_str = f"{crew_min}-{crew_avg}-{crew_max}"
    else:
        crew_str = (crew or "").strip() or None
    return {
        "name": name.strip(), "author": author.strip(),
        "project_type": (project_type or "").strip() or None, "crew": crew_str,
        "weapons": (weapons or "").strip() or None, "has_reactor": (has_reactor or "").strip() or None,
        "fabricator_medfab": (fabricator_medfab or "").strip() or None,
        "has_deconstructor": (has_deconstructor or "").strip() or None,
        "docking_ports": (docking_ports or "").strip() or None, "decks": (decks or "").strip() or None,
        "max_speed": (max_speed or "").strip() or None, "extra_equipment": (extra_equipment or "").strip() or None,
        "features": (features or "").strip() or None, "description": (description or "").strip() or None,
    }


@router.post("/api/v1/admin/sub-projects")
async def api_sub_project_create(
    request: Request,
    name: str = Form(...), author: str = Form(...), project_type: Optional[str] = Form(None),
    crew: Optional[str] = Form(None), crew_min: Optional[int] = Form(None), crew_avg: Optional[int] = Form(None),
    crew_max: Optional[int] = Form(None), weapons: Optional[str] = Form(None), has_reactor: Optional[str] = Form(None),
    fabricator_medfab: Optional[str] = Form(None), has_deconstructor: Optional[str] = Form(None),
    docking_ports: Optional[str] = Form(None), decks: Optional[str] = Form(None), max_speed: Optional[str] = Form(None),
    extra_equipment: Optional[str] = Form(None), features: Optional[str] = Form(None), description: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "create"):
        raise HTTPException(status_code=403)

    svc = _get_sub_service(db)
    data = _sub_project_form_to_data(name, author, project_type, crew, crew_min, crew_avg, crew_max,
                                      weapons, has_reactor, fabricator_medfab, has_deconstructor,
                                      docking_ports, decks, max_speed, extra_equipment, features, description)
    project = svc.create(data, current_admin.id)
    if image and image.filename:
        svc.save_image(project.id, image)

    default_category = svc.get_default_category()
    if default_category:
        svc.assign_category(project.id, default_category.id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_create", {"project_id": project.id, "name": project.name})
    return {"status": "ok", "project": _serialize_project(project)}


@router.post("/api/v1/admin/sub-projects/{project_id}")
async def api_sub_project_update(
    project_id: int, request: Request,
    name: str = Form(...), author: str = Form(...), project_type: Optional[str] = Form(None),
    crew: Optional[str] = Form(None), crew_min: Optional[int] = Form(None), crew_avg: Optional[int] = Form(None),
    crew_max: Optional[int] = Form(None), weapons: Optional[str] = Form(None), has_reactor: Optional[str] = Form(None),
    fabricator_medfab: Optional[str] = Form(None), has_deconstructor: Optional[str] = Form(None),
    docking_ports: Optional[str] = Form(None), decks: Optional[str] = Form(None), max_speed: Optional[str] = Form(None),
    extra_equipment: Optional[str] = Form(None), features: Optional[str] = Form(None), description: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    svc = _get_sub_service(db)
    existing = svc.get_by_id(project_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Проект не найден")

    is_owner = existing.created_by_admin_id == current_admin.id
    if not _has(current_admin, "edit", "edit_all", is_owner):
        raise HTTPException(status_code=403, detail="Недостаточно прав для редактирования этого проекта")

    data = _sub_project_form_to_data(name, author, project_type, crew, crew_min, crew_avg, crew_max,
                                      weapons, has_reactor, fabricator_medfab, has_deconstructor,
                                      docking_ports, decks, max_speed, extra_equipment, features, description)
    project = svc.update(project_id, data)
    if image and image.filename:
        svc.save_image(project_id, image)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_update", {"project_id": project_id, "name": project.name})
    return {"status": "ok", "project": _serialize_project(project)}


@router.delete("/api/v1/admin/sub-projects/{project_id}")
async def api_sub_project_delete(
    project_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    is_owner = project.created_by_admin_id == current_admin.id
    if not _has(current_admin, "delete", "delete_all", is_owner):
        raise HTTPException(status_code=403)
    project_name = project.name
    svc.delete(project_id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_delete", {"project_id": project_id, "name": project_name})
    return {"status": "ok"}


@router.post("/api/v1/admin/sub-projects/{project_id}/versions")
async def api_sub_project_upload_version(
    project_id: int, sub_file: UploadFile = File(...), changelog: Optional[str] = Form(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    is_owner = project.created_by_admin_id == current_admin.id
    if not _has(current_admin, "upload_version", "upload_version_all", is_owner):
        raise HTTPException(status_code=403, detail="Вы можете загружать версии только для своих проектов")
    if not sub_file.filename or not sub_file.filename.endswith(".sub"):
        raise HTTPException(status_code=400, detail="Файл должен иметь расширение .sub")

    version = svc.upload_version(project_id, sub_file, changelog or "", current_admin.id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_upload", {"project_id": project_id, "version_id": version.id, "version_number": version.version_number})

    # Автопроверка по ГОСТу сразу после загрузки: результат едет тем же
    # ответом, чтобы карточка проекта показала отчёт во всплывающем окне, не
    # заставляя жать «Проверить корабль» отдельно. Провалившаяся проверка
    # (битый файл, отключённый чекер) возвращает None и загрузку не отменяет —
    # версия уже сохранена и это её состояние не меняет.
    from routes.sub_checker import check_version_file
    check = check_version_file(db, version, admin_id=current_admin.id, is_auto=True)
    return {"status": "ok", "version": _serialize_version(version), "check": check}


@router.delete("/api/v1/admin/sub-projects/{project_id}/versions/{version_id}")
async def api_sub_project_delete_version(
    project_id: int, version_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    is_owner = project.created_by_admin_id == current_admin.id
    if not _has(current_admin, "delete_version", "delete_version_all", is_owner):
        raise HTTPException(status_code=403, detail="Недостаточно прав для удаления версии")
    svc.delete_version(version_id, admin_id=current_admin.id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_delete", {"project_id": project_id, "version_id": version_id})
    return {"status": "ok"}


@router.get("/api/v1/admin/sub-projects/{project_id}/versions/{version_id}/download")
async def api_sub_project_download_version(
    project_id: int, version_id: int,
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    """JSON-API twin of the legacy /adminold download route — the React SPA
    linked directly at the legacy /admin/... URL (which doesn't exist,
    the legacy handler lives at /adminold/...), silently falling through to
    the SPA catch-all instead of downloading. Same permission/file logic."""
    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    is_owner = project.created_by_admin_id == current_admin.id
    if not _has(current_admin, "download", "download_all", is_owner):
        raise HTTPException(status_code=403, detail="Скачивание файлов доступно только для своих проектов")

    version = svc.get_version_by_id(version_id)
    if not version or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="Версия не найдена")

    file_path = os.path.join(settings.sub_projects_upload_dir, "files", version.stored_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    return FileResponse(
        path=file_path,
        filename=version.filename,
        media_type="application/octet-stream",
    )


@router.post("/api/v1/admin/sub-projects/{project_id}/versions/{version_id}/deploy")
async def api_sub_project_deploy_version(
    project_id: int, version_id: int, request: Request,
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    body = await request.json()
    server = body.get("server")
    if server not in (1, 2):
        raise HTTPException(status_code=400, detail="Некорректный номер сервера")
    if not check_permission(current_admin, "sub_projects", f"deploy_server_{server}"):
        raise HTTPException(status_code=403, detail=f"Недостаточно прав для деплоя на Сервер {server}")

    svc = _get_sub_service(db)
    svc.deploy_version(version_id, server)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_deploy", {"project_id": project_id, "version_id": version_id, "server": server})
    return {"status": "ok"}


@router.post("/api/v1/admin/sub-projects/{project_id}/versions/{version_id}/undeploy")
async def api_sub_project_undeploy_version(
    project_id: int, version_id: int, request: Request,
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    body = await request.json()
    server = body.get("server")
    if server not in (1, 2):
        raise HTTPException(status_code=400, detail="Некорректный номер сервера")
    if not check_permission(current_admin, "sub_projects", f"undeploy_server_{server}"):
        raise HTTPException(status_code=403, detail=f"Недостаточно прав для отката с Сервера {server}")

    svc = _get_sub_service(db)
    svc.undeploy_version(version_id, server)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_undeploy", {"project_id": project_id, "version_id": version_id, "server": server})
    return {"status": "ok"}


@router.post("/api/v1/admin/sub-projects/{project_id}/discord/publish")
async def api_sub_project_discord_publish(
    project_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    is_owner = project.created_by_admin_id == current_admin.id
    if not _has(current_admin, "publish_discord", "publish_discord_all", is_owner):
        raise HTTPException(status_code=403)
    base_url = str(request.base_url).rstrip("/")
    msg_id = svc.publish_to_discord(project_id, base_url)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_discord_publish", {"project_id": project_id})
    return {"status": "ok", "message_id": msg_id}


@router.post("/api/v1/admin/sub-projects/{project_id}/discord/unpublish")
async def api_sub_project_discord_delete(
    project_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    is_owner = project.created_by_admin_id == current_admin.id
    if not _has(current_admin, "publish_discord", "publish_discord_all", is_owner):
        raise HTTPException(status_code=403)
    ok = svc.delete_discord_message(project_id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_discord_delete", {"project_id": project_id})
    return {"status": "ok" if ok else "failed"}


@router.get("/api/v1/admin/sub-projects-categories")
async def api_sub_project_categories_list(
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "manage_categories"):
        raise HTTPException(status_code=403)
    svc = _get_sub_service(db)
    return {"categories": [_serialize_category(c) for c in svc.get_all_categories()]}


@router.post("/api/v1/admin/sub-projects-categories")
async def api_sub_project_category_create(
    request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "manage_categories"):
        raise HTTPException(status_code=403)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    final_slug = (body.get("slug") or "").strip() or _slugify_category(name)

    svc = _get_sub_service(db)
    cat = svc.create_category(
        name=name, slug=final_slug, description=(body.get("description") or "").strip() or None,
        color=body.get("color") or "#1a68de", icon=body.get("icon") or "🚢",
        sort_order=body.get("sort_order") or 0, parent_id=body.get("parent_id") or None,
        admin_id=current_admin.id, is_default=bool(body.get("is_default")),
    )
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_category_create", {"name": name})
    return {"status": "ok", "category": _serialize_category(cat)}


@router.post("/api/v1/admin/sub-projects-categories/{cat_id}")
async def api_sub_project_category_update(
    cat_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "manage_categories"):
        raise HTTPException(status_code=403)
    body = await request.json()
    name = (body.get("name") or "").strip()
    final_slug = (body.get("slug") or "").strip() or _slugify_category(name)

    svc = _get_sub_service(db)
    update_data = {
        "name": name, "slug": final_slug, "description": (body.get("description") or "").strip() or None,
        "color": body.get("color") or "#1a68de", "icon": body.get("icon") or "🚢",
        "sort_order": body.get("sort_order") or 0, "parent_id": body.get("parent_id") or None,
    }
    if "is_default" in body:
        update_data["is_default"] = bool(body.get("is_default"))
    svc.update_category(cat_id, update_data)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_category_update", {"id": cat_id, "name": name})
    return {"status": "ok"}


@router.delete("/api/v1/admin/sub-projects-categories/{cat_id}")
async def api_sub_project_category_delete(
    cat_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "manage_categories"):
        raise HTTPException(status_code=403)
    svc = _get_sub_service(db)
    svc.delete_category(cat_id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_category_delete", {"id": cat_id})
    return {"status": "ok"}


@router.post("/api/v1/admin/sub-projects/{project_id}/categories/{cat_id}")
async def api_sub_project_assign_category(
    project_id: int, cat_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    # No ownership check today (matches pre-rework behavior — bare "edit"
    # already reached any project for category assign/remove specifically).
    if not (check_permission(current_admin, "sub_projects", "edit") or check_permission(current_admin, "sub_projects", "edit_all")):
        raise HTTPException(status_code=403)
    svc = _get_sub_service(db)
    svc.assign_category(project_id, cat_id)
    return {"status": "ok"}


@router.delete("/api/v1/admin/sub-projects/{project_id}/categories/{cat_id}")
async def api_sub_project_remove_category(
    project_id: int, cat_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not (check_permission(current_admin, "sub_projects", "edit") or check_permission(current_admin, "sub_projects", "edit_all")):
        raise HTTPException(status_code=403)
    svc = _get_sub_service(db)
    svc.remove_category(project_id, cat_id)
    return {"status": "ok"}


@router.get("/api/v1/admin/sub-projects/change-owner-candidates")
async def api_sub_project_change_owner_candidates(
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "change_owner"):
        raise HTTPException(status_code=403)
    admins = db.query(AdminUser).filter(AdminUser.is_deleted == 0).order_by(AdminUser.discord_username).all()
    return {"admins": [{"id": a.id, "name": a.discord_username} for a in admins]}


@router.post("/api/v1/admin/sub-projects/{project_id}/change-owner")
async def api_sub_project_change_owner(
    project_id: int, request: Request, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    """JSON/SPA port of the legacy /adminold change-owner action — same
    change_owner-gated capability, no ownership concept (reassigning
    ownership is itself an administrative action on top of any project)."""
    if not check_permission(current_admin, "sub_projects", "change_owner"):
        raise HTTPException(status_code=403, detail="Недостаточно прав для смены владельца")

    body = await request.json()
    new_owner_admin_id = body.get("new_owner_admin_id")
    if not isinstance(new_owner_admin_id, int):
        raise HTTPException(status_code=400, detail="new_owner_admin_id обязателен")

    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    new_owner = db.query(AdminUser).filter(AdminUser.id == new_owner_admin_id).first()
    if not new_owner:
        raise HTTPException(status_code=404, detail="Администратор не найден")

    project.created_by_admin_id = new_owner_admin_id
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_change_owner", {
        "project_id": project_id, "new_owner_id": new_owner_admin_id, "new_owner_name": new_owner.discord_username,
    })
    return {"status": "ok", "project": _serialize_project(project)}


@router.post("/api/v1/admin/sub-projects/{project_id}/versions/{version_id}/restore")
async def api_sub_project_restore_version(
    project_id: int, version_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    """JSON/SPA port of the legacy /adminold version-restore action."""
    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    is_owner = project.created_by_admin_id == current_admin.id
    if not _has(current_admin, "restore_version", "restore_version_all", is_owner):
        raise HTTPException(status_code=403, detail="Недостаточно прав для восстановления версии")

    version = svc.get_version_by_id(version_id)
    if not version or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="Версия не найдена")

    svc.restore_version(version_id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_restore", {"project_id": project_id, "version_id": version_id})
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# PROJECT DETAIL
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/adminold/sub-projects/{project_id}", response_class=HTMLResponse)
async def sub_project_detail(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    versions = svc.get_versions(project_id)

    # Load all admins for owner change selector
    from models import AdminUser as AU
    all_admins = db.query(AU).order_by(AU.discord_username).all()
    current_owner = db.query(AU).filter(AU.id == project.created_by_admin_id).first()
    current_owner_name = current_owner.discord_username if current_owner else None

    return templates.TemplateResponse("admin/sub_project_detail.html", {
        "request": request,
        "admin": current_admin,
        "project": project,
        "versions": versions,
        "all_admins": all_admins,
        "current_owner_name": current_owner_name,
        "flashes": pop_flashes(request),
        "sftp_server_1_name": settings.sftp_server_1_name,
        "sftp_server_2_name": settings.sftp_server_2_name,
    })


# ══════════════════════════════════════════════════════════════════════════════
# CREATE PROJECT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/sub-projects/create")
async def sub_project_create(
    request: Request,
    name: str = Form(...),
    author: str = Form(...),
    project_type: Optional[str] = Form(None),
    crew: Optional[str] = Form(None),
    crew_min: Optional[int] = Form(None),
    crew_avg: Optional[int] = Form(None),
    crew_max: Optional[int] = Form(None),
    weapons: Optional[str] = Form(None),
    has_reactor: Optional[str] = Form(None),
    fabricator_medfab: Optional[str] = Form(None),
    has_deconstructor: Optional[str] = Form(None),
    docking_ports: Optional[str] = Form(None),
    decks: Optional[str] = Form(None),
    max_speed: Optional[str] = Form(None),
    extra_equipment: Optional[str] = Form(None),
    features: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "edit") and not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    # Build crew string from min/avg/max fields (prefer over raw crew string)
    if crew_min is not None and crew_avg is not None and crew_max is not None:
        crew_str = f"{crew_min}-{crew_avg}-{crew_max}"
    else:
        crew_str = (crew or "").strip() or None

    svc = _get_sub_service(db)
    data = {
        "name": name.strip(),
        "author": author.strip(),
        "project_type": (project_type or "").strip() or None,
        "crew": crew_str,
        "weapons": (weapons or "").strip() or None,
        "has_reactor": (has_reactor or "").strip() or None,
        "fabricator_medfab": (fabricator_medfab or "").strip() or None,
        "has_deconstructor": (has_deconstructor or "").strip() or None,
        "docking_ports": (docking_ports or "").strip() or None,
        "decks": (decks or "").strip() or None,
        "max_speed": (max_speed or "").strip() or None,
        "extra_equipment": (extra_equipment or "").strip() or None,
        "features": (features or "").strip() or None,
        "description": (description or "").strip() or None,
    }

    project = svc.create(data, current_admin.id)

    if image and image.filename:
        svc.save_image(project.id, image)

    # Apply default category if configured
    from models import AppSetting
    default_cat_row = db.query(AppSetting).filter(AppSetting.key == "sub_cat_default_id").first()
    if default_cat_row and default_cat_row.value and default_cat_row.value.strip().isdigit():
        try:
            svc.assign_category(project.id, int(default_cat_row.value))
        except Exception:
            pass

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_create", {"project_id": project.id, "name": project.name})
    push_flash(request, "success", f"Проект «{project.name}» создан")
    return RedirectResponse(url=f"/adminold/sub-projects/{project.id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# UPDATE PROJECT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/sub-projects/{project_id}/update")
async def sub_project_update(
    request: Request,
    project_id: int,
    name: str = Form(...),
    author: str = Form(...),
    project_type: Optional[str] = Form(None),
    crew: Optional[str] = Form(None),
    crew_min: Optional[int] = Form(None),
    crew_avg: Optional[int] = Form(None),
    crew_max: Optional[int] = Form(None),
    weapons: Optional[str] = Form(None),
    has_reactor: Optional[str] = Form(None),
    fabricator_medfab: Optional[str] = Form(None),
    has_deconstructor: Optional[str] = Form(None),
    docking_ports: Optional[str] = Form(None),
    decks: Optional[str] = Form(None),
    max_speed: Optional[str] = Form(None),
    extra_equipment: Optional[str] = Form(None),
    features: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    # With 'edit' permission: can edit own project; with 'delete': any project
    svc_check = _get_sub_service(db)
    existing = svc_check.get_by_id(project_id)
    if not existing:
        push_flash(request, "error", "Проект не найден")
        return RedirectResponse(url="/adminold/sub-projects", status_code=302)

    can_edit_any = check_permission(current_admin, "sub_projects", "delete")
    can_edit_own = check_permission(current_admin, "sub_projects", "edit") or check_permission(current_admin, "sub_projects", "view")
    is_owner = existing.created_by_admin_id == current_admin.id

    if not can_edit_any and not (can_edit_own and is_owner):
        raise HTTPException(status_code=403, detail="Недостаточно прав для редактирования этого проекта")

    await ensure_csrf(request, db)

    # Build crew string
    if crew_min is not None and crew_avg is not None and crew_max is not None:
        crew_str = f"{crew_min}-{crew_avg}-{crew_max}"
    else:
        crew_str = (crew or "").strip() or None

    svc = _get_sub_service(db)
    data = {
        "name": name.strip(),
        "author": author.strip(),
        "project_type": (project_type or "").strip() or None,
        "crew": crew_str,
        "weapons": (weapons or "").strip() or None,
        "has_reactor": (has_reactor or "").strip() or None,
        "fabricator_medfab": (fabricator_medfab or "").strip() or None,
        "has_deconstructor": (has_deconstructor or "").strip() or None,
        "docking_ports": (docking_ports or "").strip() or None,
        "decks": (decks or "").strip() or None,
        "max_speed": (max_speed or "").strip() or None,
        "extra_equipment": (extra_equipment or "").strip() or None,
        "features": (features or "").strip() or None,
        "description": (description or "").strip() or None,
    }

    project = svc.update(project_id, data)
    if not project:
        push_flash(request, "error", "Проект не найден")
        return RedirectResponse(url="/adminold/sub-projects", status_code=302)

    if image and image.filename:
        svc.save_image(project_id, image)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_update", {"project_id": project_id, "name": project.name})
    push_flash(request, "success", f"Проект «{project.name}» обновлён")
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# DELETE PROJECT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/sub-projects/{project_id}/delete")
async def sub_project_delete(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        push_flash(request, "error", "Проект не найден")
        return RedirectResponse(url="/adminold/sub-projects", status_code=302)

    project_name = project.name
    svc.delete(project_id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_delete", {"project_id": project_id, "name": project_name})
    push_flash(request, "success", f"Проект «{project_name}» удалён")
    return RedirectResponse(url="/adminold/sub-projects", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# VERSION UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/sub-projects/{project_id}/versions/upload")
async def sub_project_upload_version(
    request: Request,
    project_id: int,
    sub_file: UploadFile = File(...),
    changelog: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    # view perm + owner OR edit/delete
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        push_flash(request, "error", "Проект не найден")
        return RedirectResponse(url="/adminold/sub-projects", status_code=302)

    # With view: only upload to own project. With edit/delete: any project
    can_any = check_permission(current_admin, "sub_projects", "edit") or check_permission(current_admin, "sub_projects", "delete")
    if not can_any and project.created_by_admin_id != current_admin.id:
        raise HTTPException(status_code=403, detail="Вы можете загружать версии только для своих проектов")

    if not sub_file.filename or not sub_file.filename.endswith(".sub"):
        push_flash(request, "error", "Файл должен иметь расширение .sub")
        return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)

    version = svc.upload_version(project_id, sub_file, changelog or "", current_admin.id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_upload", {
        "project_id": project_id, "version_id": version.id, "version_number": version.version_number
    })
    push_flash(request, "success", f"Версия {version.version_number} загружена")
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# VERSION DELETE
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/sub-projects/{project_id}/versions/{version_id}/delete")
async def sub_project_delete_version(
    request: Request,
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403, detail="Только пользователи с правом 'удалить' могут удалять версии")
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    svc.delete_version(version_id, admin_id=current_admin.id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_delete", {"project_id": project_id, "version_id": version_id})
    push_flash(request, "success", "Версия помечена как удалённая")
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# SFTP DEPLOY / UNDEPLOY
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/sub-projects/{project_id}/versions/{version_id}/deploy")
async def sub_project_deploy_version(
    request: Request,
    project_id: int,
    version_id: int,
    server: int = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    # edit or delete permission required for deployment
    if not (check_permission(current_admin, "sub_projects", "edit") or check_permission(current_admin, "sub_projects", "delete")):
        raise HTTPException(status_code=403, detail="Для развёртывания необходимо право 'изменить' или 'удалить'")
    await ensure_csrf(request, db)

    if server not in (1, 2):
        push_flash(request, "error", "Некорректный номер сервера")
        return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)

    svc = _get_sub_service(db)
    try:
        svc.deploy_version(version_id, server)
        server_name = settings.sftp_server_1_name if server == 1 else settings.sftp_server_2_name
        push_flash(request, "success", f"Версия развёрнута на {server_name}")
    except HTTPException as e:
        push_flash(request, "error", e.detail)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_deploy", {
        "project_id": project_id, "version_id": version_id, "server": server
    })
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


@router.post("/adminold/sub-projects/{project_id}/versions/{version_id}/undeploy")
async def sub_project_undeploy_version(
    request: Request,
    project_id: int,
    version_id: int,
    server: int = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not (check_permission(current_admin, "sub_projects", "edit") or check_permission(current_admin, "sub_projects", "delete")):
        raise HTTPException(status_code=403, detail="Для отката необходимо право 'изменить' или 'удалить'")
    await ensure_csrf(request, db)

    if server not in (1, 2):
        push_flash(request, "error", "Некорректный номер сервера")
        return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)

    svc = _get_sub_service(db)
    try:
        svc.undeploy_version(version_id, server)
        server_name = settings.sftp_server_1_name if server == 1 else settings.sftp_server_2_name
        push_flash(request, "success", f"Версия удалена с {server_name}")
    except HTTPException as e:
        push_flash(request, "error", e.detail)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_undeploy", {
        "project_id": project_id, "version_id": version_id, "server": server
    })
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# DISCORD WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/sub-projects/{project_id}/discord/publish")
async def sub_project_discord_publish(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    base_url = str(request.base_url).rstrip("/")

    try:
        msg_id = svc.publish_to_discord(project_id, base_url)
        push_flash(request, "success", f"Embed опубликован в Discord (ID: {msg_id})")
    except HTTPException as e:
        push_flash(request, "error", e.detail)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_discord_publish", {"project_id": project_id})
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


@router.post("/adminold/sub-projects/{project_id}/discord/delete")
async def sub_project_discord_delete(
    request: Request,
    project_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    ok = svc.delete_discord_message(project_id)
    if ok:
        push_flash(request, "success", "Сообщение удалено из Discord")
    else:
        push_flash(request, "error", "Не удалось удалить сообщение")

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_discord_delete", {"project_id": project_id})
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# FILE DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/adminold/sub-projects/{project_id}/versions/{version_id}/download")
async def sub_project_download_version(
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403)

    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    # With view-only: can download only from own projects
    can_any = check_permission(current_admin, "sub_projects", "edit") or check_permission(current_admin, "sub_projects", "delete")
    if not can_any and project.created_by_admin_id != current_admin.id:
        raise HTTPException(status_code=403, detail="Скачивание файлов доступно только для своих проектов")

    version = svc.get_version_by_id(version_id)
    if not version or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="Версия не найдена")

    file_path = os.path.join(settings.sub_projects_upload_dir, "files", version.stored_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    return FileResponse(
        path=file_path,
        filename=version.filename,
        media_type="application/octet-stream",
    )


# ══════════════════════════════════════════════════════════════════════════════
# CHANGE PROJECT OWNER (delete permission only)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/adminold/sub-projects/{project_id}/change-owner")
async def sub_project_change_owner(
    request: Request,
    project_id: int,
    new_owner_admin_id: int = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Change the owner of a project. Only admins with 'delete' permission can do this."""
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403, detail="Только пользователи с правом 'удалить' могут менять владельца")
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project:
        push_flash(request, "error", "Проект не найден")
        return RedirectResponse(url="/adminold/sub-projects", status_code=302)

    # Verify the new owner exists
    from models import AdminUser as AU
    new_owner = db.query(AU).filter(AU.id == new_owner_admin_id).first()
    if not new_owner:
        push_flash(request, "error", "Администратор не найден")
        return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)

    project.created_by_admin_id = new_owner_admin_id
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_change_owner", {
        "project_id": project_id,
        "new_owner_id": new_owner_admin_id,
        "new_owner_name": new_owner.discord_username,
    })
    push_flash(request, "success", f"Владелец проекта изменён на {new_owner.discord_username}")
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# CATEGORIES MANAGEMENT (superuser or sub_projects.delete permission)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/adminold/sub-projects/categories", response_class=HTMLResponse)
async def sub_project_categories_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Category management page."""
    if not check_permission(current_admin, "sub_projects", "manage_categories"):
        raise HTTPException(status_code=403)
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    svc = _get_sub_service(db)
    categories = svc.get_all_categories()

    return templates.TemplateResponse("admin/sub_project_categories.html", {
        "request": request,
        "admin": current_admin,
        "categories": categories,
        "flashes": pop_flashes(request),
    })


@router.post("/adminold/sub-projects/categories/create")
async def sub_project_category_create(
    request: Request,
    name: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    color: str = Form("#1a68de"),
    icon: str = Form("🚢"),
    sort_order: int = Form(0),
    parent_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "manage_categories"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    final_slug = slug.strip() or _slugify_category(name)

    svc.create_category(
        name=name.strip(),
        slug=final_slug,
        description=description.strip() or None,
        color=color,
        icon=icon,
        sort_order=sort_order,
        parent_id=parent_id if parent_id else None,
        admin_id=current_admin.id,
    )

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_category_create", {"name": name})
    push_flash(request, "success", f"Категория «{name}» создана")
    return RedirectResponse(url="/adminold/sub-projects/categories", status_code=302)


@router.post("/adminold/sub-projects/categories/{cat_id}/update")
async def sub_project_category_update(
    request: Request,
    cat_id: int,
    name: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    color: str = Form("#1a68de"),
    icon: str = Form("🚢"),
    sort_order: int = Form(0),
    parent_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "manage_categories"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    final_slug = slug.strip() or _slugify_category(name)

    svc.update_category(cat_id, {
        "name": name.strip(),
        "slug": final_slug,
        "description": description.strip() or None,
        "color": color,
        "icon": icon,
        "sort_order": sort_order,
        "parent_id": parent_id if parent_id else None,
    })

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_category_update", {"id": cat_id, "name": name})
    push_flash(request, "success", f"Категория «{name}» обновлена")
    return RedirectResponse(url="/adminold/sub-projects/categories", status_code=302)


@router.post("/adminold/sub-projects/categories/{cat_id}/delete")
async def sub_project_category_delete(
    request: Request,
    cat_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "manage_categories"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    svc.delete_category(cat_id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_category_delete", {"id": cat_id})
    push_flash(request, "success", "Категория удалена")
    return RedirectResponse(url="/adminold/sub-projects/categories", status_code=302)


@router.post("/adminold/sub-projects/{project_id}/categories/assign")
async def sub_project_assign_category(
    request: Request,
    project_id: int,
    category_id: int = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    svc.assign_category(project_id, category_id)
    push_flash(request, "success", "Категория назначена")
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


@router.post("/adminold/sub-projects/{project_id}/categories/{cat_id}/remove")
async def sub_project_remove_category(
    request: Request,
    project_id: int,
    cat_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "edit"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    svc.remove_category(project_id, cat_id)
    push_flash(request, "success", "Категория снята")
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


@router.post("/adminold/sub-projects/{project_id}/versions/{version_id}/restore")
async def sub_project_restore_version(
    request: Request,
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Restore a soft-deleted version."""
    if not check_permission(current_admin, "sub_projects", "delete"):
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    svc = _get_sub_service(db)
    svc.restore_version(version_id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_restore", {"project_id": project_id, "version_id": version_id})
    push_flash(request, "success", "Версия восстановлена")
    return RedirectResponse(url=f"/adminold/sub-projects/{project_id}", status_code=302)


@router.post("/adminold/sub-projects/category-settings")
async def sub_project_category_settings(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Save category permission settings. Superuser only."""
    if not current_admin.is_superuser:
        raise HTTPException(status_code=403)
    await ensure_csrf(request, db)

    from models import AppSetting
    import json as _json

    form = await request.form()
    create_groups = [int(x) for x in form.getlist("cat_create_groups") if str(x).isdigit()]
    assign_groups = [int(x) for x in form.getlist("cat_assign_groups") if str(x).isdigit()]
    default_id = (form.get("cat_default_id") or "").strip()

    def _set(key, val):
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = val
        else:
            db.add(AppSetting(key=key, value=val))

    _set("sub_cat_create_groups", _json.dumps(create_groups))
    _set("sub_cat_assign_groups", _json.dumps(assign_groups))
    _set("sub_cat_default_id", default_id)
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_cat_settings", {
        "create_groups": create_groups,
        "assign_groups": assign_groups,
        "default_id": default_id,
    })
    push_flash(request, "success", "Настройки категорий сохранены")
    return RedirectResponse(url="/adminold/sub-projects", status_code=302)
