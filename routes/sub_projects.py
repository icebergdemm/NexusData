"""
Admin panel routes for Sub-project management (submarine maps).
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
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


# ── Reusable helpers ─────────────────────────────────────────────────────────

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
        if session and (session.expires_at is None or session.expires_at > now):
            from sqlalchemy.orm import joinedload
            admin = (
                db.query(AdminUser)
                .options(joinedload(AdminUser.privilege_group))
                .filter(AdminUser.id == session.admin_id)
                .first()
            )
            if admin:
                request.session["username"] = admin.discord_username
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


async def ensure_csrf(request: Request, db: Session):
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

@router.get("/admin/sub-projects", response_class=HTMLResponse)
async def sub_projects_list(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """List all sub-projects."""
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    svc = _get_sub_service(db)
    projects = svc.get_all()

    projects_data = []
    for p in projects:
        versions = svc.get_versions(p.id)
        projects_data.append({
            "project": p,
            "version_count": len(versions),
            "latest_version": versions[0] if versions else None,
        })

    return templates.TemplateResponse("admin/sub_projects.html", {
        "request": request,
        "admin": current_admin,
        "projects_data": projects_data,
        "flashes": pop_flashes(request),
        "sftp_server_1_name": settings.sftp_server_1_name,
        "sftp_server_2_name": settings.sftp_server_2_name,
    })


# ══════════════════════════════════════════════════════════════════════════════
# PROJECT DETAIL
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/sub-projects/{project_id}", response_class=HTMLResponse)
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

@router.post("/admin/sub-projects/create")
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

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_create", {"project_id": project.id, "name": project.name})
    push_flash(request, "success", f"Проект «{project.name}» создан")
    return RedirectResponse(url=f"/admin/sub-projects/{project.id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# UPDATE PROJECT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/sub-projects/{project_id}/update")
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
        return RedirectResponse(url="/admin/sub-projects", status_code=302)

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
        return RedirectResponse(url="/admin/sub-projects", status_code=302)

    if image and image.filename:
        svc.save_image(project_id, image)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_update", {"project_id": project_id, "name": project.name})
    push_flash(request, "success", f"Проект «{project.name}» обновлён")
    return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# DELETE PROJECT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/sub-projects/{project_id}/delete")
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
        return RedirectResponse(url="/admin/sub-projects", status_code=302)

    project_name = project.name
    svc.delete(project_id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_delete", {"project_id": project_id, "name": project_name})
    push_flash(request, "success", f"Проект «{project_name}» удалён")
    return RedirectResponse(url="/admin/sub-projects", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# VERSION UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/sub-projects/{project_id}/versions/upload")
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
        return RedirectResponse(url="/admin/sub-projects", status_code=302)

    # With view: only upload to own project. With edit/delete: any project
    can_any = check_permission(current_admin, "sub_projects", "edit") or check_permission(current_admin, "sub_projects", "delete")
    if not can_any and project.created_by_admin_id != current_admin.id:
        raise HTTPException(status_code=403, detail="Вы можете загружать версии только для своих проектов")

    if not sub_file.filename or not sub_file.filename.endswith(".sub"):
        push_flash(request, "error", "Файл должен иметь расширение .sub")
        return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)

    version = svc.upload_version(project_id, sub_file, changelog or "", current_admin.id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_upload", {
        "project_id": project_id, "version_id": version.id, "version_number": version.version_number
    })
    push_flash(request, "success", f"Версия {version.version_number} загружена")
    return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# VERSION DELETE
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/sub-projects/{project_id}/versions/{version_id}/delete")
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
    svc.delete_version(version_id)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_delete", {"project_id": project_id, "version_id": version_id})
    push_flash(request, "success", "Версия удалена")
    return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# SFTP DEPLOY / UNDEPLOY
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/sub-projects/{project_id}/versions/{version_id}/deploy")
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
        return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)

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
    return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)


@router.post("/admin/sub-projects/{project_id}/versions/{version_id}/undeploy")
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
        return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)

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
    return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# DISCORD WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/sub-projects/{project_id}/discord/publish")
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
    return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)


@router.post("/admin/sub-projects/{project_id}/discord/delete")
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
    return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# FILE DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/sub-projects/{project_id}/versions/{version_id}/download")
async def sub_project_download_version(
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "sub_projects", "view"):
        raise HTTPException(status_code=403)

    svc = _get_sub_service(db)
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

@router.post("/admin/sub-projects/{project_id}/change-owner")
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
        return RedirectResponse(url="/admin/sub-projects", status_code=302)

    # Verify the new owner exists
    from models import AdminUser as AU
    new_owner = db.query(AU).filter(AU.id == new_owner_admin_id).first()
    if not new_owner:
        push_flash(request, "error", "Администратор не найден")
        return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)

    project.created_by_admin_id = new_owner_admin_id
    db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_change_owner", {
        "project_id": project_id,
        "new_owner_id": new_owner_admin_id,
        "new_owner_name": new_owner.discord_username,
    })
    push_flash(request, "success", f"Владелец проекта изменён на {new_owner.discord_username}")
    return RedirectResponse(url=f"/admin/sub-projects/{project_id}", status_code=302)
