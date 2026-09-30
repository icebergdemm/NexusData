"""
"Тестовый полигон ASPER" — Sub-projects scoped to departments.

Deliberately separate from routes/sub_projects.py: access here runs ENTIRELY
through each department's own "test_server" module permission
(check_department_permission — the exact same gate that already controls
that department's Pterodactyl power/status endpoints), never through the
site-wide sub_projects permission registry. A department member (including
heads, via check_department_permission's unconditional is_head bypass) sees
this zone's projects for their own department(s) only; regular ND users with
no test_server access to any department see nothing here at all.

Deploys are hard-locked to "Сервер 2" (ASPER2) at the service layer
(services_sub_projects.py) — this file doesn't even expose a server choice.
Creation only takes name/author/image — no descriptive fields.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Form, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload

from database import get_db
from config import get_settings
from models import AdminUser, Department, DepartmentMember
from routes.departments import get_current_admin, get_member, require_department_module, get_department_or_404
from routes.sub_projects import _get_sub_service, _serialize_project, _serialize_version
from security import check_department_permission, check_permission

router = APIRouter()
settings = get_settings()


def _departments_with_test_server_access(db: Session, admin: AdminUser, action: str = "view") -> list[int]:
    """Every non-archived department id where the admin currently has
    test_server:{action} — is_head/is_deputy_head pass unconditionally via
    check_department_permission, superuser sees every department."""
    if admin.is_superuser:
        return [d.id for d in db.query(Department).filter(Department.is_archived.is_(False)).all()]
    depts = (
        db.query(Department)
        .join(DepartmentMember, DepartmentMember.department_id == Department.id)
        .options(joinedload(Department.access_levels))
        .filter(DepartmentMember.admin_id == admin.id, DepartmentMember.is_excluded.is_(False), Department.is_archived.is_(False))
        .all()
    )
    out = []
    for dept in depts:
        member = get_member(db, dept.id, admin.id)
        if check_department_permission(admin, dept, member, "test_server", action):
            out.append(dept.id)
    return out


def _load_polygon_project_or_404(db: Session, project_id: int):
    svc = _get_sub_service(db)
    project = svc.get_by_id(project_id)
    if not project or project.department_id is None:
        raise HTTPException(status_code=404, detail="Проект Полигона не найден")
    return project, svc


def _require_polygon_access(db: Session, admin: AdminUser, project, action: str) -> Department:
    dept = get_department_or_404(db, project.department_id)
    require_department_module(db, dept, admin, "test_server", action)
    return dept


@router.get("/api/v1/admin/sub-projects-polygon")
async def api_polygon_list(
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    view_dept_ids = _departments_with_test_server_access(db, current_admin, "view")
    edit_dept_ids = set(_departments_with_test_server_access(db, current_admin, "edit"))

    svc = _get_sub_service(db)
    projects = svc.get_polygon_projects(view_dept_ids, search=search)

    dept_names = {}
    if view_dept_ids:
        dept_names = {d.id: d.name for d in db.query(Department).filter(Department.id.in_(view_dept_ids)).all()}

    projects_data = []
    for p in projects:
        versions = svc.get_versions(p.id)
        projects_data.append({
            "project": _serialize_project(p),
            "department_name": dept_names.get(p.department_id),
            "version_count": len(versions),
            "latest_version": _serialize_version(versions[0]) if versions else None,
            "can_edit_this": p.department_id in edit_dept_ids,
        })

    creatable_departments = []
    if edit_dept_ids:
        missing_ids = edit_dept_ids - set(dept_names.keys())
        if missing_ids:
            dept_names.update({d.id: d.name for d in db.query(Department).filter(Department.id.in_(missing_ids)).all()})
        creatable_departments = [{"id": did, "name": dept_names[did]} for did in edit_dept_ids if did in dept_names]

    return {
        "projects": projects_data,
        "creatable_departments": creatable_departments,
        "sftp_server_2_name": settings.sftp_server_2_name,
        "has_any_access": bool(view_dept_ids),
    }


@router.get("/api/v1/admin/sub-projects-polygon/{project_id}")
async def api_polygon_detail(
    project_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    project, svc = _load_polygon_project_or_404(db, project_id)
    dept = _require_polygon_access(db, current_admin, project, "view")
    member = get_member(db, dept.id, current_admin.id)
    can_edit = check_department_permission(current_admin, dept, member, "test_server", "edit")

    versions = svc.get_versions(project_id)
    return {
        "project": _serialize_project(project),
        "department_id": dept.id,
        "department_name": dept.name,
        "versions": [_serialize_version(v) for v in versions],
        "can_edit_this": can_edit,
        "sftp_server_2_name": settings.sftp_server_2_name,
        "can_check_gost": check_permission(current_admin, "sub_checker", "check"),
        "checks": _latest_polygon_checks(db, project_id),
    }


def _latest_polygon_checks(db: Session, project_id: int) -> dict:
    from routes.sub_projects import _latest_checks
    return _latest_checks(db, project_id)


@router.post("/api/v1/admin/sub-projects-polygon")
async def api_polygon_create(
    name: str = Form(...), author: str = Form(...), department_id: int = Form(...),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    dept = get_department_or_404(db, department_id)
    require_department_module(db, dept, current_admin, "test_server", "edit")

    svc = _get_sub_service(db)
    project = svc.create({"name": name.strip(), "author": author.strip()}, current_admin.id, department_id=department_id)
    if image and image.filename:
        svc.save_image(project.id, image)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_polygon_create", {
        "project_id": project.id, "name": project.name, "department_id": department_id,
    })
    return {"status": "ok", "project": _serialize_project(project)}


@router.post("/api/v1/admin/sub-projects-polygon/{project_id}")
async def api_polygon_update(
    project_id: int, name: str = Form(...), author: str = Form(...),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    project, svc = _load_polygon_project_or_404(db, project_id)
    _require_polygon_access(db, current_admin, project, "edit")

    project = svc.update(project_id, {"name": name.strip(), "author": author.strip()})
    if image and image.filename:
        svc.save_image(project_id, image)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_polygon_update", {"project_id": project_id, "name": project.name})
    return {"status": "ok", "project": _serialize_project(project)}


@router.delete("/api/v1/admin/sub-projects-polygon/{project_id}")
async def api_polygon_delete(
    project_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    project, svc = _load_polygon_project_or_404(db, project_id)
    _require_polygon_access(db, current_admin, project, "edit")

    project_name = project.name
    svc.delete(project_id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_project_polygon_delete", {"project_id": project_id, "name": project_name})
    return {"status": "ok"}


@router.post("/api/v1/admin/sub-projects-polygon/{project_id}/versions")
async def api_polygon_upload_version(
    project_id: int, sub_file: UploadFile = File(...), changelog: Optional[str] = Form(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    project, svc = _load_polygon_project_or_404(db, project_id)
    _require_polygon_access(db, current_admin, project, "edit")

    if not sub_file.filename or not sub_file.filename.endswith(".sub"):
        raise HTTPException(status_code=400, detail="Файл должен иметь расширение .sub")

    version = svc.upload_version(project_id, sub_file, changelog or "", current_admin.id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_polygon_upload", {
        "project_id": project_id, "version_id": version.id, "version_number": version.version_number,
    })
    # Автопроверка по ГОСТу — как и в обычной зоне Sub-проектов.
    from routes.sub_checker import check_version_file
    check = check_version_file(db, version, admin_id=current_admin.id, is_auto=True)
    return {"status": "ok", "version": _serialize_version(version), "check": check}


@router.delete("/api/v1/admin/sub-projects-polygon/{project_id}/versions/{version_id}")
async def api_polygon_delete_version(
    project_id: int, version_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    project, svc = _load_polygon_project_or_404(db, project_id)
    _require_polygon_access(db, current_admin, project, "edit")

    svc.delete_version(version_id, admin_id=current_admin.id)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_polygon_delete", {"project_id": project_id, "version_id": version_id})
    return {"status": "ok"}


@router.get("/api/v1/admin/sub-projects-polygon/{project_id}/versions/{version_id}/download")
async def api_polygon_download_version(
    project_id: int, version_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    project, svc = _load_polygon_project_or_404(db, project_id)
    _require_polygon_access(db, current_admin, project, "view")

    version = svc.get_version_by_id(version_id)
    if not version or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="Версия не найдена")

    file_path = os.path.join(settings.sub_projects_upload_dir, "files", version.stored_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")
    return FileResponse(path=file_path, filename=version.filename, media_type="application/octet-stream")


@router.post("/api/v1/admin/sub-projects-polygon/{project_id}/versions/{version_id}/deploy")
async def api_polygon_deploy_version(
    project_id: int, version_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    """Always Сервер 2 (ASPER2) — Полигон has no server choice."""
    project, svc = _load_polygon_project_or_404(db, project_id)
    _require_polygon_access(db, current_admin, project, "edit")

    svc.deploy_version(version_id, 2)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_polygon_deploy", {"project_id": project_id, "version_id": version_id})
    return {"status": "ok"}


@router.post("/api/v1/admin/sub-projects-polygon/{project_id}/versions/{version_id}/undeploy")
async def api_polygon_undeploy_version(
    project_id: int, version_id: int, db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    project, svc = _load_polygon_project_or_404(db, project_id)
    _require_polygon_access(db, current_admin, project, "edit")

    svc.undeploy_version(version_id, 2)
    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_version_polygon_undeploy", {"project_id": project_id, "version_id": version_id})
    return {"status": "ok"}
