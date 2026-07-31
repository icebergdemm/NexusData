"""
Проверщик кораблей по ГОСТу КБ «Гайка».

Три группы ручек:
  * конфигурация норматива (чтение — sub_checker:view, правка —
    sub_checker:manage_rules / manage_classes);
  * запуск проверки: разовый файл, конкретная версия Sub-проекта
    (sub_checker:check);
  * история проверок и отдельный отчёт (sub_checker:view).

Автопроверка при загрузке новой версии вызывается не отсюда, а из
routes/sub_projects.py через check_version_file — чтобы результат приезжал
тем же ответом, что и сама загрузка, и фронтенд мог сразу показать его во
всплывающем окне.
"""

import logging
import os
from copy import deepcopy
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session, defer, joinedload

from config import get_settings
from database import get_db
from models import (
    AdminUser, AdminSession, SubCheckerConfig, SubCheckerConfigRevision,
    SubCheckResult, SubProject, SubProjectVersion,
)
from security import check_permission
import services_sub_checker as checker
from sub_checker_defaults import DEFAULT_CONFIG

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

# Потолок на размер .sub. Реальные карты флота — единицы мегабайт; всё, что
# сильно больше, скорее ошибка или попытка положить сервис разбором XML.
MAX_SUB_BYTES = 64 * 1024 * 1024


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = (
            db.query(AdminSession)
            .filter(AdminSession.token == token, AdminSession.revoked == 0)
            .first()
        )
        if session and (session.expires_at is None or session.expires_at > now):
            admin = (
                db.query(AdminUser)
                .options(joinedload(AdminUser.privilege_group))
                .filter(AdminUser.id == session.admin_id)
                .first()
            )
            if admin:
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require(admin: AdminUser, action: str) -> None:
    if not check_permission(admin, "sub_checker", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


# ── Конфигурация ─────────────────────────────────────────────────────────────

def get_config_row(db: Session) -> SubCheckerConfig:
    """Единственная строка конфигурации, с посевом заводских значений при
    первом обращении."""
    row = db.query(SubCheckerConfig).order_by(SubCheckerConfig.id.asc()).first()
    if row is None:
        # deepcopy, чтобы объект в БД не оказался тем же самым словарём, что и
        # DEFAULT_CONFIG: правка конфигурации в одной сессии иначе меняла бы
        # заводские значения для всего процесса.
        row = SubCheckerConfig(data=deepcopy(DEFAULT_CONFIG), revision=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def get_active_config(db: Session) -> tuple[dict, int]:
    row = get_config_row(db)
    return checker.merge_config(row.data), row.revision


def _serialize_config(row: SubCheckerConfig, admin: AdminUser) -> dict:
    return {
        "config": checker.merge_config(row.data),
        "revision": row.revision,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by.discord_username if row.updated_by else None,
        "can_manage_rules": check_permission(admin, "sub_checker", "manage_rules"),
        "can_manage_classes": check_permission(admin, "sub_checker", "manage_classes"),
        "can_check": check_permission(admin, "sub_checker", "check"),
        # Пробный контекст показывается в редакторе формул: по нему видно,
        # какими переменными вообще можно пользоваться в выражениях.
        "formula_context": sorted(checker.SAMPLE_CONTEXT.keys()),
    }


@router.get("/api/v1/admin/sub-checker/config")
async def api_sub_checker_config(
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "view")
    return _serialize_config(get_config_row(db), current_admin)


@router.put("/api/v1/admin/sub-checker/config")
async def api_sub_checker_config_save(
    request: Request, db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Сохранить конфигурацию целиком.

    Раздел classes требует manage_classes, всё остальное — manage_rules;
    тот, у кого есть только одно из двух прав, сохраняет свой раздел, а
    чужой берётся из текущей конфигурации без изменений.
    """
    can_rules = check_permission(current_admin, "sub_checker", "manage_rules")
    can_classes = check_permission(current_admin, "sub_checker", "manage_classes")
    if not can_rules and not can_classes:
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    body = await request.json()
    incoming = body.get("config")
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="Ожидается объект config")

    row = get_config_row(db)
    current = checker.merge_config(row.data)

    merged = dict(current)
    if can_rules:
        for key in ("formulas", "equipment", "autofill", "forbidden_items",
                    "rules", "manual_checks", "custom_rules", "options"):
            if key in incoming:
                merged[key] = incoming[key]
    if can_classes and "classes" in incoming:
        merged["classes"] = incoming["classes"]

    problems = checker.validate_config(merged)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems[:10]))

    db.add(SubCheckerConfigRevision(
        revision=row.revision, data=current,
        comment=(body.get("comment") or None), created_by_admin_id=current_admin.id,
    ))
    row.data = merged
    row.revision = (row.revision or 0) + 1
    row.updated_by_admin_id = current_admin.id
    db.commit()
    db.refresh(row)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_checker_config_update", {"revision": row.revision})
    return _serialize_config(row, current_admin)


@router.post("/api/v1/admin/sub-checker/config/reset")
async def api_sub_checker_config_reset(
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    """Сброс к заводским значениям (ГОСТ КБ «Гайка» + чекер v0.96).
    Требует ОБА права: сброс затрагивает и правила, и классы."""
    _require(current_admin, "manage_rules")
    _require(current_admin, "manage_classes")

    row = get_config_row(db)
    db.add(SubCheckerConfigRevision(
        revision=row.revision, data=checker.merge_config(row.data),
        comment="Сброс к заводским настройкам", created_by_admin_id=current_admin.id,
    ))
    row.data = deepcopy(DEFAULT_CONFIG)
    row.revision = (row.revision or 0) + 1
    row.updated_by_admin_id = current_admin.id
    db.commit()
    db.refresh(row)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sub_checker_config_reset", {"revision": row.revision})
    return _serialize_config(row, current_admin)


@router.get("/api/v1/admin/sub-checker/config/revisions")
async def api_sub_checker_config_revisions(
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "view")
    rows = (
        db.query(SubCheckerConfigRevision)
        .options(
            joinedload(SubCheckerConfigRevision.created_by),
            # Снимок конфигурации — под сотню килобайт; в списке ревизий он не
            # показывается и приезжает только при откате.
            defer(SubCheckerConfigRevision.data),
        )
        .order_by(SubCheckerConfigRevision.id.desc())
        .limit(50)
        .all()
    )
    return {"revisions": [{
        "id": r.id, "revision": r.revision, "comment": r.comment,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "created_by": r.created_by.discord_username if r.created_by else None,
    } for r in rows]}


@router.post("/api/v1/admin/sub-checker/config/revisions/{revision_id}/restore")
async def api_sub_checker_config_restore(
    revision_id: int, db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "manage_rules")
    _require(current_admin, "manage_classes")

    snapshot = db.query(SubCheckerConfigRevision).filter(SubCheckerConfigRevision.id == revision_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Ревизия не найдена")

    row = get_config_row(db)
    db.add(SubCheckerConfigRevision(
        revision=row.revision, data=checker.merge_config(row.data),
        comment=f"Откат к ревизии {snapshot.revision}", created_by_admin_id=current_admin.id,
    ))
    row.data = checker.merge_config(snapshot.data)
    row.revision = (row.revision or 0) + 1
    row.updated_by_admin_id = current_admin.id
    db.commit()
    db.refresh(row)
    return _serialize_config(row, current_admin)


# ── Запуск проверки ──────────────────────────────────────────────────────────

def _store_result(
    db: Session, report: dict, revision: int, *, admin_id: Optional[int],
    project_id: Optional[int] = None, version_id: Optional[int] = None,
    filename: Optional[str] = None, is_auto: bool = False,
) -> SubCheckResult:
    ship_class = (report.get("class") or {}).get("name")
    tonnage = (report.get("metrics") or {}).get("tonnage")
    result = SubCheckResult(
        project_id=project_id, version_id=version_id,
        filename=filename, submarine_name=report.get("name"),
        ship_class=ship_class,
        tonnage=float(tonnage) if isinstance(tonnage, (int, float)) else None,
        passed=bool(report.get("passed")),
        error_count=int(report.get("total_errors", report.get("errors", 0)) or 0),
        warning_count=int(report.get("total_warnings", report.get("warnings", 0)) or 0),
        config_revision=revision, is_auto=is_auto, report=report,
        checked_by_admin_id=admin_id,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def run_check_on_bytes(
    db: Session, raw: bytes, filename: str, *, admin_id: Optional[int],
    project_id: Optional[int] = None, version_id: Optional[int] = None,
    is_auto: bool = False, store: bool = True,
) -> dict:
    """Единая точка запуска проверки — используется и ручкой загрузки файла,
    и автопроверкой из routes/sub_projects.py."""
    config, revision = get_active_config(db)
    report = checker.check_sub_file(raw, config, filename)
    result_id = None
    if store:
        result_id = _store_result(
            db, report, revision, admin_id=admin_id, project_id=project_id,
            version_id=version_id, filename=filename, is_auto=is_auto,
        ).id
    return {"result_id": result_id, "config_revision": revision, "report": report}


def check_version_file(
    db: Session, version: SubProjectVersion, *, admin_id: Optional[int], is_auto: bool,
) -> Optional[dict]:
    """Проверить файл конкретной версии проекта. None — если файла нет на
    диске или он не разобрался (для автопроверки это не повод ронять саму
    загрузку версии)."""
    path = os.path.join(settings.sub_projects_upload_dir, "files", version.stored_filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return run_check_on_bytes(
            db, raw, version.filename or version.stored_filename, admin_id=admin_id,
            project_id=version.project_id, version_id=version.id, is_auto=is_auto,
        )
    except Exception as e:  # noqa: BLE001 — битый .sub не должен ломать загрузку
        logger.warning("Автопроверка версии %s не удалась: %s", version.id, e)
        return None


@router.post("/api/v1/admin/sub-checker/check")
async def api_sub_checker_check_upload(
    sub_file: UploadFile = File(...), db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Разовая проверка загруженного файла — вкладка «Проверка файла»."""
    _require(current_admin, "check")
    if not sub_file.filename or not sub_file.filename.lower().endswith(".sub"):
        raise HTTPException(status_code=400, detail="Файл должен иметь расширение .sub")

    raw = await sub_file.read()
    if len(raw) > MAX_SUB_BYTES:
        raise HTTPException(status_code=400, detail="Файл слишком большой")
    try:
        return run_check_on_bytes(db, raw, sub_file.filename, admin_id=current_admin.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/v1/admin/sub-checker/identifiers")
async def api_sub_checker_identifiers(
    sub_file: UploadFile = File(...), current_admin: AdminUser = Depends(get_current_admin),
):
    """Идентификаторы предметов из файла — подсказка при настройке требований
    к наполнению класса (иначе их приходится вспоминать наизусть)."""
    _require(current_admin, "manage_classes")
    raw = await sub_file.read()
    if len(raw) > MAX_SUB_BYTES:
        raise HTTPException(status_code=400, detail="Файл слишком большой")
    try:
        return {"identifiers": checker.extract_identifiers(raw)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/v1/admin/sub-checker/projects/{project_id}/versions/{version_id}/check")
async def api_sub_checker_check_version(
    project_id: int, version_id: int, db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Проверка конкретной версии проекта — кнопка «Проверить корабль»."""
    _require(current_admin, "check")

    version = (
        db.query(SubProjectVersion)
        .filter(SubProjectVersion.id == version_id, SubProjectVersion.project_id == project_id)
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    project = db.query(SubProject).filter(SubProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    path = os.path.join(settings.sub_projects_upload_dir, "files", version.stored_filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Файл версии не найден на диске")

    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return run_check_on_bytes(
            db, raw, version.filename or version.stored_filename, admin_id=current_admin.id,
            project_id=project_id, version_id=version_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── История ──────────────────────────────────────────────────────────────────

def _serialize_result(r: SubCheckResult, with_report: bool = False) -> dict:
    data = {
        "id": r.id, "project_id": r.project_id, "version_id": r.version_id,
        "project_name": r.project.name if r.project else None,
        "version_number": r.version.version_number if r.version else None,
        "filename": r.filename, "submarine_name": r.submarine_name,
        "ship_class": r.ship_class, "tonnage": r.tonnage,
        "passed": bool(r.passed), "error_count": r.error_count, "warning_count": r.warning_count,
        "config_revision": r.config_revision, "is_auto": bool(r.is_auto),
        "checked_by": r.checked_by.discord_username if r.checked_by else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if with_report:
        data["report"] = r.report
    return data


@router.get("/api/v1/admin/sub-checker/history")
async def api_sub_checker_history(
    page: int = Query(1, ge=1), project_id: Optional[int] = Query(None),
    db: Session = Depends(get_db), current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "view")
    page_size = 25
    q = (
        db.query(SubCheckResult)
        .options(
            joinedload(SubCheckResult.checked_by),
            joinedload(SubCheckResult.project),
            joinedload(SubCheckResult.version),
            # Полный отчёт в списке не нужен, а весит десятки килобайт на строку.
            defer(SubCheckResult.report),
        )
    )
    if project_id:
        q = q.filter(SubCheckResult.project_id == project_id)
    total = q.count()
    rows = (
        q.order_by(SubCheckResult.created_at.desc(), SubCheckResult.id.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    return {
        "results": [_serialize_result(r) for r in rows],
        "total": total, "page": page,
        "total_pages": (total + page_size - 1) // page_size if total else 1,
    }


@router.get("/api/v1/admin/sub-checker/results/{result_id}")
async def api_sub_checker_result(
    result_id: int, db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "view")
    row = (
        db.query(SubCheckResult)
        .options(
            joinedload(SubCheckResult.checked_by),
            joinedload(SubCheckResult.project),
            joinedload(SubCheckResult.version),
        )
        .filter(SubCheckResult.id == result_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return _serialize_result(row, with_report=True)
