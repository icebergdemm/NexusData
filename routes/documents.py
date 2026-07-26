"""
Shared document library — real Word (.docx)/Excel (.xlsx) files edited via
ONLYOFFICE Document Server (see routes/onlyoffice.py for the editor
config/download/save-callback endpoints). This module covers metadata:
folders, per-document sharing (view/comment/edit), version history, starring,
and the department documentation/orders/news module wiring. All endpoints
are JSON, consumed by the React SPA's Documents pages.
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Form, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_
from typing import Optional
from datetime import datetime
import os
import shutil
import logging

from database import get_db, BLANK_DOCX_TEMPLATE_NAME, BLANK_XLSX_TEMPLATE_NAME
from security import check_permission
from config import get_settings
from models import (
    AdminUser, AdminSession, AdminPrivilegeGroup,
    Document, DocumentFolder, DocumentPermission, DocumentRevision,
    DocumentStar, DocumentTemplate, DepartmentDocumentTag, DocumentTagLink,
)

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter()

ACCESS_RANK = {"view": 1, "comment": 2, "edit": 3, "owner": 4}
VALID_LEVELS = {"view", "comment", "edit"}
FILE_TYPE_EXT = {"word": ".docx", "cell": ".xlsx", "pdf": ".pdf", "image": ".png"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
EXT_FILE_TYPE = {".docx": "word", ".xlsx": "cell", ".pdf": "pdf", **{ext: "image" for ext in IMAGE_EXTS}}


# ── Storage helpers ───────────────────────────────────────────────────────────

def _files_dir() -> str:
    d = os.path.join(settings.documents_upload_dir, "files")
    os.makedirs(d, exist_ok=True)
    return d


def _templates_dir() -> str:
    d = os.path.join(settings.documents_upload_dir, "templates")
    os.makedirs(d, exist_ok=True)
    return d


def _new_storage_filename(document_id: int, version: int, file_type: str, source_ext: Optional[str] = None) -> str:
    # Images don't have one fixed extension the way word/cell/pdf do — reuse
    # whatever the source file actually was (falls back to FILE_TYPE_EXT's
    # generic ".png" only if the caller has no source to derive it from).
    ext = source_ext if (file_type == "image" and source_ext) else FILE_TYPE_EXT.get(file_type, ".docx")
    return f"doc_{document_id}_v{version}{ext}"


def _extract_docx_excerpt(path: str, limit: int = 4000) -> str:
    """Best-effort text excerpt for the Discord publish embed. Word documents
    only — Excel files have no sensible "paragraph text" to excerpt."""
    try:
        import docx
        doc = docx.Document(path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return text[:limit]
    except Exception:
        return ""


# ── Auth (mirrors routes/logs.py's self-contained dependency) ───────────────

def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(
            AdminSession.token == token, AdminSession.revoked == 0
        ).first()
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


def require_documents_page(admin: AdminUser, action: str = "view"):
    if not check_permission(admin, "documents", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def require_document_write_access(db: Session, admin: AdminUser, department_id: Optional[int], action: str = "edit", module: str = "documentation", head_only_write: bool = False):
    """Gate for creating a document. A department-scoped document is governed
    by that department's "documentation"/"orders"/"news" module access
    instead of the site-wide documents permission, so department members
    don't need a separate site-wide grant just to write department docs.

    `head_only_write=True` additionally restricts action="edit" to the
    department head (or superuser) regardless of the module's own edit
    grant — used for the "Новости" module (item 6): "глава отдела или
    человек с доступом уровня главы" publishes news, everyone else with
    module view access can only read.
    """
    if department_id:
        from models import Department, DepartmentMember
        from security import check_department_permission

        dept = (
            db.query(Department)
            .options(joinedload(Department.access_levels))
            .filter(Department.id == department_id, Department.is_archived.is_(False))
            .first()
        )
        if not dept:
            raise HTTPException(status_code=404, detail="Отдел не найден")
        member = (
            db.query(DepartmentMember)
            .filter(DepartmentMember.department_id == department_id, DepartmentMember.admin_id == admin.id)
            .first()
        )
        if head_only_write and action == "edit" and not (admin.is_superuser or (member and member.is_head)):
            raise HTTPException(status_code=403, detail="Публиковать новости может только глава отдела")
        if not check_department_permission(admin, dept, member, module, action):
            raise HTTPException(status_code=403, detail="Недостаточно прав в отделе")
        return
    require_documents_page(admin, action)


# ── Access-level resolution ──────────────────────────────────────────────────

def get_document_access(db: Session, document: Document, admin: AdminUser) -> Optional[str]:
    """Highest access level the admin has on this document, or None.

    Regular documentation pages have NO automatic department-wide baseline:
    a document with nothing explicitly configured on it (or on its folder)
    is private to its creator — the department's "documentation" module
    view/edit permission only gates creating pages and browsing the
    directory/listing endpoints (require_document_write_access), never an
    individual page's content. "Приказы"/"Новости" are the deliberate
    exception — those two modules keep the department-wide baseline below
    since they only make sense as broadcasts everyone with module view
    access can read, same as before this policy split.
    """
    if admin.is_superuser or document.owner_admin_id == admin.id:
        return "owner"

    # Resolve department membership/held levels — needed by the permission
    # loop below (grantee_type="access_level" shares with "everyone
    # currently holding this department access level"), the orders/news
    # baseline, and the folder ACL section further down.
    member = None
    held_keys: set = set()
    dept = None
    if document.department_id:
        from models import Department, DepartmentMember

        dept = (
            db.query(Department)
            .options(joinedload(Department.access_levels))
            .filter(Department.id == document.department_id)
            .first()
        )
        if dept:
            member = (
                db.query(DepartmentMember)
                .filter(DepartmentMember.department_id == dept.id, DepartmentMember.admin_id == admin.id)
                .first()
            )
            held_keys = set(member.held_level_keys or []) if member else set()

    best = None
    for perm in document.permissions:
        matches = (
            (perm.grantee_type == "admin" and perm.grantee_admin_id == admin.id)
            or (perm.grantee_type == "privilege_group" and admin.privilege_group_id
                and perm.grantee_group_id == admin.privilege_group_id)
            or (perm.grantee_type == "access_level" and perm.grantee_access_level_key in held_keys)
            or (perm.grantee_type == "everyone")
        )
        if matches and (best is None or ACCESS_RANK[perm.level] > ACCESS_RANK[best]):
            best = perm.level

    if dept and (document.is_news or document.is_order):
        from security import check_department_permission

        module = "news" if document.is_news else "orders"
        if document.is_news:
            # News write access is head/superuser-only regardless of what
            # an access level's own "news":"edit" grant says (item 6) —
            # only view is delegable through the normal module grants.
            if admin.is_superuser or (member is not None and member.is_head):
                dept_level = "edit"
            elif check_department_permission(admin, dept, member, module, "view"):
                dept_level = "view"
            else:
                dept_level = None
        elif check_department_permission(admin, dept, member, module, "edit"):
            dept_level = "edit"
        elif check_department_permission(admin, dept, member, module, "view"):
            dept_level = "view"
        else:
            dept_level = None
        if dept_level and (best is None or ACCESS_RANK[dept_level] > ACCESS_RANK[best]):
            best = dept_level

    # Folder ACL (item 5) — a specific access-level tier or a specific
    # member can be granted view/edit on everything inside one folder, same
    # additive idea as DocumentPermission above but at folder granularity.
    # The document's OWN folder is always checked; an ANCESTOR folder's
    # grants also apply if THAT ancestor has auto_apply_permissions=True —
    # each folder independently decides whether it pushes its own grants
    # down to nested documents. Live inheritance: resolved fresh on every
    # access check, never copied into a DocumentPermission row.
    if document.department_folder_id:
        from models import DepartmentFolderPermission, DepartmentDocumentFolder

        held_keys = set(member.held_level_keys or []) if member else set()

        applicable_folder_ids = [document.department_folder_id]
        current = (
            db.query(DepartmentDocumentFolder)
            .filter(DepartmentDocumentFolder.id == document.department_folder_id, DepartmentDocumentFolder.department_id == document.department_id)
            .first()
        )
        seen = {document.department_folder_id}
        while current and current.parent_id and current.parent_id not in seen:
            parent = (
                db.query(DepartmentDocumentFolder)
                .filter(DepartmentDocumentFolder.id == current.parent_id, DepartmentDocumentFolder.department_id == document.department_id)
                .first()
            )
            if not parent:
                break
            if parent.auto_apply_permissions:
                applicable_folder_ids.append(parent.id)
            seen.add(parent.id)
            current = parent

        # Join-filtered to the document's OWN department as defense-in-depth
        # — write-time validation (PATCH /documents/{id}) now stops
        # department_folder_id from ever pointing at another department's
        # folder, but this guards any pre-existing bad data too: without it,
        # an access_level_key string that happens to collide between
        # departments (e.g. every department's "base") would wrongly match.
        grants = (
            db.query(DepartmentFolderPermission)
            .join(DepartmentDocumentFolder, DepartmentDocumentFolder.id == DepartmentFolderPermission.folder_id)
            .filter(
                DepartmentFolderPermission.folder_id.in_(applicable_folder_ids),
                DepartmentDocumentFolder.department_id == document.department_id,
            )
            .all()
        )
        for grant in grants:
            matches = (
                (grant.grantee_type == "access_level" and grant.access_level_key in held_keys)
                or (grant.grantee_type == "member" and grant.member_admin_id == admin.id)
            )
            if matches and (best is None or ACCESS_RANK[grant.level] > ACCESS_RANK[best]):
                best = grant.level

    return best


def load_document_or_404(db: Session, document_id: int, admin: AdminUser, min_level: str = "view",
                          include_deleted: bool = False) -> tuple[Document, str]:
    doc = (
        db.query(Document)
        .options(joinedload(Document.permissions))
        .filter(Document.id == document_id)
        .first()
    )
    if not doc or (doc.is_deleted and not include_deleted):
        raise HTTPException(status_code=404, detail="Документ не найден")

    access = get_document_access(db, doc, admin)
    if access is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if ACCESS_RANK[access] < ACCESS_RANK[min_level]:
        raise HTTPException(status_code=403, detail="Недостаточно прав на документ")
    return doc, access


def serialize_admin(admin: Optional[AdminUser]) -> Optional[dict]:
    if not admin:
        return None
    return {"id": admin.id, "name": admin.discord_username, "avatar": admin.discord_avatar}


def _document_access_level_names(db: Session, department_ids: set) -> dict:
    """{department_id: {access_level_key: (name, kind)}} — one batched query
    per document listing, used to render a human-readable sharing summary on
    document cards (owner-only, see _document_shared_summary)."""
    department_ids = {d for d in department_ids if d}
    if not department_ids:
        return {}
    from models import DepartmentAccessLevel
    out: dict = {}
    for lvl in db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.department_id.in_(department_ids)).all():
        out.setdefault(lvl.department_id, {})[lvl.key] = (lvl.name, lvl.kind)
    return out


def _document_shared_summary(doc: Document, level_names: dict) -> Optional[str]:
    """Human-readable "who else can see this" summary shown only to the
    document's owner on its card."""
    perms = doc.permissions or []
    if not perms:
        return "Только вам"
    if any(p.grantee_type == "everyone" for p in perms):
        return "Все администраторы"
    parts = []
    for p in perms:
        if p.grantee_type == "access_level":
            name, kind = level_names.get(p.grantee_access_level_key, (p.grantee_access_level_key, None))
            parts.append("весь отдел (база)" if kind == "base" else name)
        elif p.grantee_type == "admin":
            parts.append(p.grantee_admin.discord_username if p.grantee_admin else "сотрудник")
        elif p.grantee_type == "privilege_group":
            parts.append(p.grantee_group.name if p.grantee_group else "группа")
    return "Доступ: " + ", ".join(parts) if parts else "Только вам"


def serialize_document_row(db: Session, doc: Document, admin: AdminUser, starred_ids: set, level_names: Optional[dict] = None) -> dict:
    is_mine = doc.owner_admin_id == admin.id
    return {
        "id": doc.id,
        "title": doc.title,
        "icon": doc.icon,
        "file_type": doc.file_type,
        "folder_id": doc.folder_id,
        "parent_document_id": doc.parent_document_id,
        "department_id": doc.department_id,
        "department_folder_id": doc.department_folder_id,
        "is_order": doc.is_order,
        "is_news": doc.is_news,
        "owner": serialize_admin(doc.owner),
        "last_edited_by": serialize_admin(doc.last_editor),
        "is_archived": doc.is_archived,
        "is_deleted": doc.is_deleted,
        "deleted_at": doc.deleted_at.isoformat() if doc.deleted_at else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "version": doc.version,
        "is_starred": doc.id in starred_ids,
        "access_level": get_document_access(db, doc, admin),
        "is_mine": is_mine,
        "shared_summary": _document_shared_summary(doc, (level_names or {}).get(doc.department_id, {})) if is_mine else None,
    }


def _find_blank_template(db: Session, file_type: str) -> Optional[DocumentTemplate]:
    name = BLANK_DOCX_TEMPLATE_NAME if file_type == "word" else BLANK_XLSX_TEMPLATE_NAME
    return db.query(DocumentTemplate).filter(DocumentTemplate.name == name, DocumentTemplate.file_type == file_type).first()


def _create_document_file(db: Session, doc: Document, template: Optional[DocumentTemplate]):
    """Copies a template's file (or the seeded blank for doc.file_type) into
    a new storage_filename for a freshly-created Document row."""
    if template is None:
        template = _find_blank_template(db, doc.file_type)
    doc.storage_filename = _new_storage_filename(doc.id, 1, doc.file_type)
    dst = os.path.join(_files_dir(), doc.storage_filename)
    if template and template.storage_filename:
        src = os.path.join(_templates_dir(), template.storage_filename)
        if os.path.isfile(src):
            shutil.copyfile(src, dst)
            return
    # No template file on disk (shouldn't happen once seeding has run) — leave
    # an empty placeholder so the ONLYOFFICE download endpoint doesn't 404.
    open(dst, "wb").close()


# ══════════════════════════════════════════════════════════════════════════════
# FOLDERS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/documents/folders")
async def api_list_folders(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    folders = (
        db.query(DocumentFolder)
        .filter(DocumentFolder.owner_admin_id == current_admin.id)
        .order_by(DocumentFolder.name)
        .all()
    )
    return JSONResponse({
        "folders": [
            {"id": f.id, "name": f.name, "parent_id": f.parent_id}
            for f in folders
        ]
    })


@router.post("/api/v1/admin/documents/folders")
async def api_create_folder(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "edit")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название папки")
    parent_id = body.get("parent_id")
    if parent_id is not None:
        parent = db.query(DocumentFolder).filter(
            DocumentFolder.id == parent_id, DocumentFolder.owner_admin_id == current_admin.id
        ).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Родительская папка не найдена")

    folder = DocumentFolder(name=name, parent_id=parent_id, owner_admin_id=current_admin.id)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return JSONResponse({"id": folder.id, "name": folder.name, "parent_id": folder.parent_id})


@router.patch("/api/v1/admin/documents/folders/{folder_id}")
async def api_update_folder(
    folder_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "edit")
    folder = db.query(DocumentFolder).filter(
        DocumentFolder.id == folder_id, DocumentFolder.owner_admin_id == current_admin.id
    ).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")

    body = await request.json()
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Укажите название папки")
        folder.name = name
    if "parent_id" in body:
        new_parent_id = body["parent_id"]
        if new_parent_id == folder.id:
            raise HTTPException(status_code=400, detail="Папка не может быть вложена сама в себя")
        folder.parent_id = new_parent_id
    db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/api/v1/admin/documents/folders/{folder_id}")
async def api_delete_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "edit")
    folder = db.query(DocumentFolder).filter(
        DocumentFolder.id == folder_id, DocumentFolder.owner_admin_id == current_admin.id
    ).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")

    # Contained documents/subfolders move up to the parent's parent (root if none).
    db.query(Document).filter(Document.folder_id == folder.id).update({"folder_id": folder.parent_id})
    db.query(DocumentFolder).filter(DocumentFolder.parent_id == folder.id).update({"parent_id": folder.parent_id})
    db.delete(folder)
    db.commit()
    return JSONResponse({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENTS — list / create / read / update / trash
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/documents")
async def api_list_documents(
    scope: str = Query("mine", pattern="^(mine|shared|starred|trash|all)$"),
    folder_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")

    if scope == "all" and not check_permission(current_admin, "documents", "view_all"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    q = db.query(Document).options(
        joinedload(Document.owner), joinedload(Document.last_editor), joinedload(Document.permissions)
    )

    if scope == "mine":
        q = q.filter(Document.owner_admin_id == current_admin.id, Document.is_deleted.is_(False))
        if folder_id is not None:
            q = q.filter(Document.folder_id == folder_id)
        else:
            q = q.filter(Document.folder_id.is_(None))
    elif scope == "shared":
        group_id = current_admin.privilege_group_id
        conditions = [
            and_(DocumentPermission.grantee_type == "admin", DocumentPermission.grantee_admin_id == current_admin.id),
            DocumentPermission.grantee_type == "everyone",
        ]
        if group_id:
            conditions.append(and_(DocumentPermission.grantee_type == "privilege_group", DocumentPermission.grantee_group_id == group_id))
        q = (
            q.join(DocumentPermission, DocumentPermission.document_id == Document.id)
            .filter(or_(*conditions), Document.is_deleted.is_(False), Document.owner_admin_id != current_admin.id)
            .distinct()
        )
    elif scope == "starred":
        q = (
            q.join(DocumentStar, DocumentStar.document_id == Document.id)
            .filter(DocumentStar.admin_id == current_admin.id, Document.is_deleted.is_(False))
        )
    elif scope == "trash":
        q = q.filter(Document.owner_admin_id == current_admin.id, Document.is_deleted.is_(True))
    elif scope == "all":
        q = q.filter(Document.is_deleted.is_(False))

    if search:
        q = q.filter(Document.title.ilike(f"%{search}%"))

    order_col = Document.deleted_at.desc() if scope == "trash" else Document.updated_at.desc()
    docs = q.order_by(order_col).limit(500).all()

    starred_ids = {
        row[0] for row in db.query(DocumentStar.document_id).filter(DocumentStar.admin_id == current_admin.id).all()
    }
    level_names = _document_access_level_names(db, {d.department_id for d in docs})
    return JSONResponse({"documents": [serialize_document_row(db, d, current_admin, starred_ids, level_names) for d in docs]})


@router.post("/api/v1/admin/documents")
async def api_create_document(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    body = await request.json() if await request.body() else {}
    department_id = body.get("department_id")
    parent_document_id = body.get("parent_document_id")
    department_folder_id = body.get("department_folder_id")

    title = (body.get("title") or "").strip()
    is_order = bool(body.get("is_order_template"))
    is_news = bool(body.get("is_news"))
    file_type = body.get("file_type") or "word"
    # "pdf" is import-only (routes.documents.api_import_document) — there's
    # no sensible "blank PDF" template to seed a freshly-created one from.
    if file_type not in ("word", "cell"):
        raise HTTPException(status_code=400, detail="Неизвестный тип файла")

    template = None
    template_id = body.get("template_id")
    if template_id is not None:
        template = db.query(DocumentTemplate).filter(DocumentTemplate.id == template_id).first()
        if not template:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        if template.file_type not in ("word", "cell"):
            raise HTTPException(status_code=400, detail="Из PDF-шаблона нельзя создать документ")
        is_order = bool(template.is_order_template)
        file_type = template.file_type
        if not title:
            title = template.name

    module = "news" if is_news else ("orders" if is_order else "documentation")
    require_document_write_access(db, current_admin, department_id, "edit", module, head_only_write=is_news)

    folder_id = body.get("folder_id")
    if folder_id is not None:
        folder = db.query(DocumentFolder).filter(
            DocumentFolder.id == folder_id, DocumentFolder.owner_admin_id == current_admin.id
        ).first()
        if not folder:
            raise HTTPException(status_code=404, detail="Папка не найдена")

    if department_folder_id is not None:
        from models import DepartmentDocumentFolder
        dept_folder = db.query(DepartmentDocumentFolder).filter(
            DepartmentDocumentFolder.id == department_folder_id, DepartmentDocumentFolder.department_id == department_id
        ).first()
        if not dept_folder:
            raise HTTPException(status_code=404, detail="Папка отдела не найдена")

    if parent_document_id is not None:
        parent_doc = db.query(Document).filter(Document.id == parent_document_id, Document.is_deleted.is_(False)).first()
        if not parent_doc:
            raise HTTPException(status_code=404, detail="Родительская страница не найдена")

    doc = Document(
        title=title or "Без названия",
        file_type=file_type,
        folder_id=folder_id,
        parent_document_id=parent_document_id,
        department_id=department_id,
        department_folder_id=department_folder_id,
        is_order=is_order,
        is_news=is_news,
        owner_admin_id=current_admin.id,
        last_edited_by_admin_id=current_admin.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    _create_document_file(db, doc, template)
    db.commit()
    return JSONResponse({"id": doc.id})


@router.post("/api/v1/admin/documents/import")
async def api_import_document(
    file: UploadFile = File(...),
    department_id: Optional[int] = Form(None),
    folder_id: Optional[int] = Form(None),
    department_folder_id: Optional[int] = Form(None),
    parent_document_id: Optional[int] = Form(None),
    is_order_template: bool = Form(False),
    is_news: bool = Form(False),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Uploads a .docx/.xlsx/.pdf/image directly as a new Document — no
    conversion step: ONLYOFFICE opens word/cell/pdf natively, images get a
    plain built-in viewer instead (see api_get_document_image)."""
    module = "news" if is_news else ("orders" if is_order_template else "documentation")
    require_document_write_access(db, current_admin, department_id, "edit", module, head_only_write=is_news)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in EXT_FILE_TYPE:
        raise HTTPException(status_code=400, detail="Поддерживаются файлы .docx, .xlsx, .pdf, .jpg, .jpeg, .png, .gif, .webp")
    file_type = EXT_FILE_TYPE[ext]

    if department_folder_id is not None:
        from models import DepartmentDocumentFolder
        dept_folder = db.query(DepartmentDocumentFolder).filter(
            DepartmentDocumentFolder.id == department_folder_id, DepartmentDocumentFolder.department_id == department_id
        ).first()
        if not dept_folder:
            raise HTTPException(status_code=404, detail="Папка отдела не найдена")

    title = os.path.splitext(file.filename or "")[0].strip() or "Загруженный документ"
    doc = Document(
        title=title,
        file_type=file_type,
        folder_id=folder_id,
        department_folder_id=department_folder_id,
        parent_document_id=parent_document_id,
        department_id=department_id,
        is_order=bool(is_order_template),
        is_news=bool(is_news),
        owner_admin_id=current_admin.id,
        last_edited_by_admin_id=current_admin.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    doc.storage_filename = _new_storage_filename(doc.id, 1, file_type, source_ext=ext)
    dst = os.path.join(_files_dir(), doc.storage_filename)
    data = await file.read()
    with open(dst, "wb") as f:
        f.write(data)
    db.commit()
    return JSONResponse({"id": doc.id})


@router.get("/api/v1/admin/documents/{document_id}/children")
async def api_list_document_children(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Sub-pages nested under this document."""
    doc, _access = load_document_or_404(db, document_id, current_admin, "view")
    children = (
        db.query(Document)
        .filter(Document.parent_document_id == doc.id, Document.is_deleted.is_(False))
        .order_by(Document.title)
        .all()
    )
    return JSONResponse({
        "children": [
            {"id": c.id, "title": c.title, "icon": c.icon}
            for c in children
            if get_document_access(db, c, current_admin) is not None
        ]
    })


@router.get("/api/v1/admin/departments/{department_id}/documents")
async def api_list_department_documents(
    department_id: int,
    kind: str = Query("documentation", pattern="^(documentation|orders|news)$"),
    folder_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Pages in a department's shared documentation/orders/news space
    (each its own module access), optionally scoped to one
    DepartmentDocumentFolder — omit folder_id for the top-level (no folder)
    view, same convention the personal Documents page already uses."""
    require_document_write_access(db, current_admin, department_id, "view", kind)
    q = db.query(Document).options(
        joinedload(Document.owner), joinedload(Document.last_editor), joinedload(Document.permissions)
    ).filter(
        Document.department_id == department_id,
        Document.parent_document_id.is_(None),
        Document.is_deleted.is_(False),
    )
    if kind == "orders":
        q = q.filter(Document.is_order.is_(True))
    elif kind == "news":
        q = q.filter(Document.is_news.is_(True))
    else:
        q = q.filter(Document.is_order.is_(False), Document.is_news.is_(False))
    q = q.filter(Document.department_folder_id == folder_id) if folder_id is not None else q.filter(Document.department_folder_id.is_(None))
    docs = q.order_by(Document.title).all()
    # Module "view" access (checked above) only unlocks the directory itself
    # — an unshared documentation page is still private to its creator, so
    # each row needs its own get_document_access check (orders/news keep
    # their department-wide baseline and so pass this for everyone regardless).
    docs = [d for d in docs if get_document_access(db, d, current_admin) is not None]
    starred_ids = {
        row[0] for row in db.query(DocumentStar.document_id).filter(DocumentStar.admin_id == current_admin.id).all()
    }
    level_names = _document_access_level_names(db, {department_id})
    return JSONResponse({"documents": [serialize_document_row(db, d, current_admin, starred_ids, level_names) for d in docs]})


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES — a shared, growable library of starting-point document files
# ══════════════════════════════════════════════════════════════════════════════

def serialize_template(t: DocumentTemplate) -> dict:
    return {
        "id": t.id, "name": t.name, "description": t.description, "icon": t.icon,
        "file_type": t.file_type,
        "is_order_template": t.is_order_template,
        "created_by": serialize_admin(t.created_by),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("/api/v1/admin/documents/templates")
async def api_list_templates(
    is_order_template: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    # No site-wide gate: the template library itself isn't sensitive (it's
    # just starting-point content) — what's protected is the document it
    # seeds, via the normal per-document/department access checks. This lets
    # department members without a site Documents grant still pick an order
    # template from the department's "Приказы" tab.
    q = db.query(DocumentTemplate).options(joinedload(DocumentTemplate.created_by))
    if is_order_template is not None:
        q = q.filter(DocumentTemplate.is_order_template.is_(is_order_template))
    templates = q.order_by(DocumentTemplate.name).all()
    return JSONResponse({"templates": [serialize_template(t) for t in templates]})


@router.patch("/api/v1/admin/documents/templates/{template_id}")
async def api_update_template(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "documents", "manage_templates"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    template = db.query(DocumentTemplate).filter(DocumentTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    body = await request.json()
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Укажите название шаблона")
        template.name = name
    if "description" in body:
        template.description = (body["description"] or "").strip() or None
    if "icon" in body:
        template.icon = body["icon"] or None
    db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/api/v1/admin/documents/templates/{template_id}")
async def api_delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    if not check_permission(current_admin, "documents", "manage_templates"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    template = db.query(DocumentTemplate).filter(DocumentTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    db.delete(template)
    db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/api/v1/admin/documents/{document_id}/save-as-template")
async def api_save_document_as_template(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Copies a document's CURRENT file into a new reusable template —
    replaces the old Tiptap-JSON-based "save as template" (there's no raw
    content to submit anymore; a template is always derived from an
    existing document's file)."""
    if not check_permission(current_admin, "documents", "manage_templates"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    doc, _access = load_document_or_404(db, document_id, current_admin, "view")
    if doc.file_type not in ("word", "cell"):
        raise HTTPException(status_code=400, detail="PDF и изображения нельзя сохранить как шаблон")
    body = await request.json()
    name = (body.get("name") or doc.title or "Шаблон").strip()

    template = DocumentTemplate(
        name=name,
        description=(body.get("description") or "").strip() or None,
        icon=body.get("icon") or doc.icon,
        file_type=doc.file_type,
        created_by_admin_id=current_admin.id,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    template.storage_filename = f"tpl_{template.id}{FILE_TYPE_EXT.get(doc.file_type, '.docx')}"
    if doc.storage_filename:
        src = os.path.join(_files_dir(), doc.storage_filename)
        dst = os.path.join(_templates_dir(), template.storage_filename)
        if os.path.isfile(src):
            shutil.copyfile(src, dst)
    db.commit()
    return JSONResponse(serialize_template(template))


@router.get("/api/v1/admin/documents/share-candidates")
async def api_share_candidates(
    document_id: int = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Candidates for the share dialog's "Админ"/"Группа" pickers — scoped to
    the document's OWN department when it has one: "Группа" becomes that
    department's access levels (not ND-wide privilege groups, which have no
    relation to who can actually see a department document), and "Админ"
    is restricted to that department's members (not every ND admin)."""
    doc, _ = load_document_or_404(db, document_id, current_admin, "owner")

    if doc.department_id:
        from models import Department, DepartmentMember

        dept = db.query(Department).options(joinedload(Department.access_levels)).filter(Department.id == doc.department_id).first()
        member_admin_ids = {
            row[0] for row in db.query(DepartmentMember.admin_id).filter(DepartmentMember.department_id == doc.department_id).all()
        }
        admins = (
            db.query(AdminUser)
            .filter(AdminUser.is_deleted == 0, AdminUser.id != current_admin.id, AdminUser.id.in_(member_admin_ids))
            .order_by(AdminUser.discord_username)
            .all()
        )
        levels = dept.access_levels if dept else []
        return JSONResponse({
            "admins": [{"id": a.id, "name": a.discord_username} for a in admins],
            "groups": [{"id": lvl.key, "name": lvl.name, "is_base": lvl.kind == "base"} for lvl in levels],
            "group_grantee_type": "access_level",
            "is_department_doc": True,
        })

    admins = (
        db.query(AdminUser)
        .filter(AdminUser.is_deleted == 0, AdminUser.id != current_admin.id)
        .order_by(AdminUser.discord_username)
        .all()
    )
    groups = db.query(AdminPrivilegeGroup).order_by(AdminPrivilegeGroup.name).all()
    return JSONResponse({
        "admins": [{"id": a.id, "name": a.discord_username} for a in admins],
        "groups": [{"id": g.id, "name": g.name} for g in groups],
        "group_grantee_type": "privilege_group",
    })


@router.get("/api/v1/admin/documents/{document_id}")
async def api_get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    # Per-document access (owner/DocumentPermission/department) is the real
    # gate here — no separate site-wide precheck, so department members with
    # documentation access but no personal Documents grant can still open it.
    doc, access = load_document_or_404(db, document_id, current_admin, "view")
    is_starred = db.query(DocumentStar).filter(
        DocumentStar.document_id == doc.id, DocumentStar.admin_id == current_admin.id
    ).first() is not None

    return JSONResponse({
        "id": doc.id,
        "title": doc.title,
        "icon": doc.icon,
        "file_type": doc.file_type,
        "folder_id": doc.folder_id,
        "parent_document_id": doc.parent_document_id,
        "department_id": doc.department_id,
        "department_folder_id": doc.department_folder_id,
        "is_order": doc.is_order,
        "is_news": doc.is_news,
        "owner": serialize_admin(doc.owner),
        "last_edited_by": serialize_admin(doc.last_editor),
        "version": doc.version,
        "is_archived": doc.is_archived,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "access_level": access,
        "is_starred": is_starred,
        "discord_message_id": doc.discord_message_id,
    })


@router.get("/api/v1/admin/documents/{document_id}/image")
async def api_get_document_image(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Raw image bytes for file_type="image" documents — these have no
    ONLYOFFICE editor, just a plain viewer, so they're served directly
    (session-cookie gated, same access check as everything else) instead of
    through the ONLYOFFICE download-token flow."""
    doc, _access = load_document_or_404(db, document_id, current_admin, "view")
    if doc.file_type != "image" or not doc.storage_filename:
        raise HTTPException(status_code=404, detail="Файл не найден")
    path = os.path.join(_files_dir(), doc.storage_filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})


def serialize_doc_tag(t: DepartmentDocumentTag) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color}


@router.get("/api/v1/admin/documents/{document_id}/tags")
async def api_list_document_tags(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Tags currently on the document plus the department's remaining
    available tags (only department-scoped documents have any — the tag
    vocabulary itself belongs to a department, see DepartmentDocumentTag)."""
    doc, _access = load_document_or_404(db, document_id, current_admin, "view")
    if not doc.department_id:
        return JSONResponse({"tags": [], "available": []})
    tag_ids = {
        row[0] for row in db.query(DocumentTagLink.tag_id).filter(DocumentTagLink.document_id == doc.id).all()
    }
    all_tags = (
        db.query(DepartmentDocumentTag)
        .filter(DepartmentDocumentTag.department_id == doc.department_id)
        .order_by(DepartmentDocumentTag.name)
        .all()
    )
    return JSONResponse({
        "tags": [serialize_doc_tag(t) for t in all_tags if t.id in tag_ids],
        "available": [serialize_doc_tag(t) for t in all_tags if t.id not in tag_ids],
    })


@router.post("/api/v1/admin/documents/{document_id}/tags")
async def api_add_document_tag(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Assign an existing department tag to this document. Gated by ordinary
    "edit" document access — which already covers BOTH a department-wide
    "documentation":"edit" holder (can tag any document in the department)
    AND someone with only a document-specific edit/owner grant (can tag just
    this one), since get_document_access resolves both to "edit"/"owner"."""
    doc, _access = load_document_or_404(db, document_id, current_admin, "edit")
    body = await request.json()
    tag_id = body.get("tag_id")
    tag = db.query(DepartmentDocumentTag).filter(
        DepartmentDocumentTag.id == tag_id, DepartmentDocumentTag.department_id == doc.department_id
    ).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Тег не найден")
    existing = db.query(DocumentTagLink).filter(
        DocumentTagLink.document_id == doc.id, DocumentTagLink.tag_id == tag.id
    ).first()
    if not existing:
        db.add(DocumentTagLink(document_id=doc.id, tag_id=tag.id))
        db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/api/v1/admin/documents/{document_id}/tags/{tag_id}")
async def api_remove_document_tag(
    document_id: int,
    tag_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    doc, _access = load_document_or_404(db, document_id, current_admin, "edit")
    db.query(DocumentTagLink).filter(
        DocumentTagLink.document_id == doc.id, DocumentTagLink.tag_id == tag_id
    ).delete()
    db.commit()
    return JSONResponse({"status": "ok"})


@router.patch("/api/v1/admin/documents/{document_id}")
async def api_update_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Metadata-only (title/icon/folder placement) — the file's actual
    content is edited via ONLYOFFICE and only ever changes through the
    save callback in routes/onlyoffice.py, which is also the only place
    `version` is bumped (it doubles as the ONLYOFFICE `document.key`
    version tag, so a metadata-only rename must not touch it)."""
    doc, access = load_document_or_404(db, document_id, current_admin, "edit")
    body = await request.json()

    changed = False
    if "title" in body:
        new_title = (body["title"] or "Без названия").strip() or "Без названия"
        if new_title != doc.title:
            doc.title = new_title
            changed = True
    if "icon" in body:
        doc.icon = body["icon"]
        changed = True
    if "folder_id" in body and doc.owner_admin_id == current_admin.id:
        doc.folder_id = body["folder_id"]
        changed = True
    if "department_folder_id" in body and doc.department_id and access in ("edit", "owner"):
        new_folder_id = body["department_folder_id"]
        if new_folder_id is not None:
            # Never validated before — a caller with edit access to a
            # document could move it into ANY department's folder by ID,
            # picking up that other department's folder-ACL grants (a real
            # cross-department leak whenever an access-level key string
            # happens to collide between departments, e.g. "base").
            from models import DepartmentDocumentFolder
            target_folder = db.query(DepartmentDocumentFolder).filter(
                DepartmentDocumentFolder.id == new_folder_id, DepartmentDocumentFolder.department_id == doc.department_id
            ).first()
            if not target_folder:
                raise HTTPException(status_code=404, detail="Папка отдела не найдена")
        doc.department_folder_id = new_folder_id
        changed = True

    if changed:
        doc.last_edited_by_admin_id = current_admin.id
        db.commit()
        db.refresh(doc)

    return JSONResponse({"status": "ok", "version": doc.version, "updated_at": doc.updated_at.isoformat()})


def _resolve_document_publish_channel(db: Session, doc: "Document", dept) -> Optional[str]:
    """Default publish channel for a document, without prompting: the
    document's own department folder's configured channel first, falling
    back to the legacy order/news department-wide channel for documents
    that predate per-folder Discord config."""
    if doc.department_folder_id:
        from models import DepartmentDocumentFolder
        folder = db.query(DepartmentDocumentFolder).filter(DepartmentDocumentFolder.id == doc.department_folder_id).first()
        if folder and folder.discord_channel_id:
            return folder.discord_channel_id
    if doc.is_news and dept.news_channel_id:
        return dept.news_channel_id
    if doc.is_order and dept.orders_channel_id:
        return dept.orders_channel_id
    return None


@router.post("/api/v1/admin/documents/{document_id}/discord/verify-channel")
async def api_verify_document_discord_channel(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Near-synchronous check that a manually-entered channel ID actually
    belongs to the document's department's configured Discord category
    (Department.discord_category_id, superuser-only setting) — calls the
    in-process bot directly and awaits its answer (with a timeout), instead
    of the old queue-a-row-and-poll-it dance. If the department hasn't
    configured a category, verification is skipped (nothing to check
    against) rather than blocking publishing."""
    doc, _access = load_document_or_404(db, document_id, current_admin, "edit")
    if not doc.department_id:
        raise HTTPException(status_code=400, detail="Доступно только для документов отдела")

    from models import Department

    dept = db.query(Department).filter(Department.id == doc.department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Отдел не найден")

    body = await request.json()
    channel_id = (body.get("channel_id") or "").strip()
    if not channel_id:
        raise HTTPException(status_code=400, detail="Укажите ID канала")

    if not dept.discord_category_id:
        return JSONResponse({"verified": True, "skipped": True, "channel_name": None})

    import asyncio
    from discord_bot import bot as discord_bot
    try:
        result = await asyncio.wait_for(
            asyncio.wrap_future(discord_bot.schedule(
                discord_bot.check_channel_category(int(channel_id), dept.discord_category_id)
            )),
            timeout=9.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Бот не ответил вовремя — попробуйте ещё раз")
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка проверки канала: {e}")

    return JSONResponse({
        "verified": bool(result.get("belongs_to_category")),
        "skipped": False,
        "channel_name": result.get("channel_name"),
    })


@router.post("/api/v1/admin/documents/{document_id}/publish-discord")
async def api_publish_document_to_discord(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Publishes (or re-syncs) a department document to Discord — generalized
    to any document in a department (not just orders/news): the target
    channel is the document's folder's configured channel, an explicit
    channel_id passed in the body (manual flow, expected to have already
    been checked via verify-channel above), or — for orders/news — the
    department's own legacy channel. A document already published (has a
    discord_message_id) to the SAME channel gets its message edited in place
    ("Синхронизировать"); anything else sends a new message."""
    doc, _access = load_document_or_404(db, document_id, current_admin, "edit")
    if not doc.department_id:
        raise HTTPException(status_code=400, detail="Публикация в Discord доступна только для документов отдела")

    from models import Department

    dept = db.query(Department).filter(Department.id == doc.department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Отдел не найден")

    body = await request.json() if await request.body() else {}
    explicit_channel_id = (body.get("channel_id") or "").strip() or None
    channel_id = explicit_channel_id or _resolve_document_publish_channel(db, doc, dept)
    if not channel_id:
        raise HTTPException(status_code=400, detail="Не удалось определить канал публикации — укажите ID канала вручную")

    excerpt = ""
    if doc.file_type == "word" and doc.storage_filename:
        excerpt = _extract_docx_excerpt(os.path.join(_files_dir(), doc.storage_filename))

    embed = {
        "title": (doc.title or "Без названия")[:256],
        "description": excerpt.strip()[:4000] or "(открыть в редакторе)",
        "color": 0x1a68de,
        "footer": {"text": f"Опубликовано: {current_admin.discord_username or 'ND'}"},
    }

    from discord_bot import bot as discord_bot
    if doc.discord_message_id and channel_id == doc.discord_channel_id:
        discord_bot.schedule(discord_bot.execute_bot_action("edit_embed", {
            "channel_id": channel_id, "message_id": doc.discord_message_id, "embed": embed,
        }))
    else:
        discord_bot.schedule(discord_bot.execute_bot_action("send_document_embed", {
            "channel_id": channel_id, "embed": embed, "document_id": doc.id,
        }))
        doc.discord_channel_id = channel_id
        # Cleared, not guessed — the bot's callback (set-message-id) fills
        # this back in once the new message is actually sent.
        doc.discord_message_id = None
    db.commit()
    return JSONResponse({"status": "ok", "channel_id": channel_id, "is_sync": bool(doc.discord_message_id)})


@router.post("/api/v1/admin/documents/{document_id}/duplicate")
async def api_duplicate_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "edit")
    doc, _ = load_document_or_404(db, document_id, current_admin, "view")
    copy = Document(
        title=f"{doc.title} (копия)",
        icon=doc.icon,
        file_type=doc.file_type,
        owner_admin_id=current_admin.id,
        last_edited_by_admin_id=current_admin.id,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)

    source_ext = os.path.splitext(doc.storage_filename)[1] if doc.storage_filename else None
    copy.storage_filename = _new_storage_filename(copy.id, 1, copy.file_type, source_ext=source_ext)
    if doc.storage_filename:
        src = os.path.join(_files_dir(), doc.storage_filename)
        dst = os.path.join(_files_dir(), copy.storage_filename)
        if os.path.isfile(src):
            shutil.copyfile(src, dst)
        else:
            open(dst, "wb").close()
    else:
        open(os.path.join(_files_dir(), copy.storage_filename), "wb").close()
    db.commit()
    return JSONResponse({"id": copy.id})


@router.delete("/api/v1/admin/documents/{document_id}")
async def api_trash_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "owner")
    doc.is_deleted = True
    doc.deleted_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/api/v1/admin/documents/{document_id}/restore")
async def api_restore_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "owner", include_deleted=True)
    doc.is_deleted = False
    doc.deleted_at = None
    db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/api/v1/admin/documents/{document_id}/permanent")
async def api_permanently_delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "owner", include_deleted=True)
    if not doc.is_deleted:
        raise HTTPException(status_code=400, detail="Сначала переместите документ в корзину")
    db.delete(doc)
    db.commit()
    return JSONResponse({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# FAVORITES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/v1/admin/documents/{document_id}/star")
async def api_star_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    load_document_or_404(db, document_id, current_admin, "view")
    exists = db.query(DocumentStar).filter(
        DocumentStar.document_id == document_id, DocumentStar.admin_id == current_admin.id
    ).first()
    if not exists:
        db.add(DocumentStar(document_id=document_id, admin_id=current_admin.id))
        db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/api/v1/admin/documents/{document_id}/star")
async def api_unstar_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    db.query(DocumentStar).filter(
        DocumentStar.document_id == document_id, DocumentStar.admin_id == current_admin.id
    ).delete()
    db.commit()
    return JSONResponse({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# SHARING / PERMISSIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/documents/{document_id}/permissions")
async def api_list_permissions(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "owner")

    level_by_key = {}
    if doc.department_id and any(p.grantee_type == "access_level" for p in doc.permissions):
        from models import DepartmentAccessLevel
        level_by_key = {
            lvl.key: lvl
            for lvl in db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.department_id == doc.department_id).all()
        }

    return JSONResponse({
        "owner": serialize_admin(doc.owner),
        "is_department_doc": bool(doc.department_id),
        "permissions": [
            {
                "id": p.id,
                "grantee_type": p.grantee_type,
                "grantee_admin": serialize_admin(p.grantee_admin) if p.grantee_admin_id else None,
                "grantee_group": {"id": p.grantee_group.id, "name": p.grantee_group.name} if p.grantee_group_id and p.grantee_group else None,
                "grantee_access_level": (
                    {
                        "key": p.grantee_access_level_key,
                        "name": level_by_key[p.grantee_access_level_key].name if p.grantee_access_level_key in level_by_key else p.grantee_access_level_key,
                        "is_base": level_by_key[p.grantee_access_level_key].kind == "base" if p.grantee_access_level_key in level_by_key else False,
                    }
                    if p.grantee_access_level_key else None
                ),
                "level": p.level,
            }
            for p in doc.permissions
        ],
    })


@router.get("/api/v1/admin/documents/{document_id}/permissions/suggested-defaults")
async def api_suggested_document_permissions(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Prefill for the "configure sharing" prompt shown right after a
    department document is created (see the frontend's SetupPermissionsDialog):
    reuses the document's own DepartmentFolderPermission grants as "the
    folder's default access" — falling back to the department's base access
    level (view-only) when the folder has no grants of its own, or isn't set."""
    doc, _ = load_document_or_404(db, document_id, current_admin, "owner")
    if not doc.department_id:
        return JSONResponse({"suggestions": []})

    from models import DepartmentAccessLevel, DepartmentFolderPermission

    suggestions = []
    if doc.department_folder_id:
        folder_perms = db.query(DepartmentFolderPermission).filter(
            DepartmentFolderPermission.folder_id == doc.department_folder_id
        ).all()
        level_by_key = {
            lvl.key: lvl
            for lvl in db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.department_id == doc.department_id).all()
        }
        admin_by_id = {
            a.id: a for a in db.query(AdminUser).filter(
                AdminUser.id.in_({p.member_admin_id for p in folder_perms if p.member_admin_id})
            ).all()
        } if folder_perms else {}
        for p in folder_perms:
            if p.grantee_type == "access_level":
                lvl = level_by_key.get(p.access_level_key)
                suggestions.append({
                    "grantee_type": "access_level",
                    "grantee_id": p.access_level_key,
                    "level": p.level,
                    "label": lvl.name if lvl else p.access_level_key,
                    "is_base": bool(lvl and lvl.kind == "base"),
                })
            else:
                a = admin_by_id.get(p.member_admin_id)
                suggestions.append({
                    "grantee_type": "admin",
                    "grantee_id": p.member_admin_id,
                    "level": p.level,
                    "label": a.discord_username if a else "Сотрудник",
                    "is_base": False,
                })

    if not suggestions:
        base = db.query(DepartmentAccessLevel).filter(
            DepartmentAccessLevel.department_id == doc.department_id, DepartmentAccessLevel.kind == "base"
        ).first()
        if base:
            suggestions.append({
                "grantee_type": "access_level",
                "grantee_id": base.key,
                "level": "view",
                "label": base.name,
                "is_base": True,
            })

    return JSONResponse({"suggestions": suggestions})


@router.post("/api/v1/admin/documents/{document_id}/permissions")
async def api_grant_permission(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "owner")
    body = await request.json()
    grantee_type = body.get("grantee_type")
    level = body.get("level", "view")
    if grantee_type not in ("admin", "privilege_group", "access_level", "everyone"):
        raise HTTPException(status_code=400, detail="Некорректный тип получателя")
    if level not in VALID_LEVELS:
        raise HTTPException(status_code=400, detail="Некорректный уровень доступа")
    if grantee_type == "access_level" and not doc.department_id:
        raise HTTPException(status_code=400, detail="Уровни доступа доступны только для документов отдела")

    grantee_admin_id = body.get("grantee_id") if grantee_type == "admin" else None
    grantee_group_id = body.get("grantee_id") if grantee_type == "privilege_group" else None
    grantee_access_level_key = None
    if grantee_type == "access_level":
        from models import DepartmentAccessLevel
        key = str(body.get("grantee_id") or "")
        valid = db.query(DepartmentAccessLevel).filter(
            DepartmentAccessLevel.department_id == doc.department_id, DepartmentAccessLevel.key == key
        ).first()
        if not valid:
            raise HTTPException(status_code=404, detail="Уровень доступа не найден")
        grantee_access_level_key = key

    if grantee_type == "admin" and grantee_admin_id == doc.owner_admin_id:
        raise HTTPException(status_code=400, detail="Владелец уже имеет полный доступ")

    existing = None
    for p in doc.permissions:
        if (
            p.grantee_type == grantee_type and p.grantee_admin_id == grantee_admin_id
            and p.grantee_group_id == grantee_group_id and p.grantee_access_level_key == grantee_access_level_key
        ):
            existing = p
            break

    if existing:
        existing.level = level
    else:
        db.add(DocumentPermission(
            document_id=doc.id, grantee_type=grantee_type,
            grantee_admin_id=grantee_admin_id, grantee_group_id=grantee_group_id,
            grantee_access_level_key=grantee_access_level_key,
            level=level, granted_by_admin_id=current_admin.id,
        ))
    db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/api/v1/admin/documents/{document_id}/permissions/{permission_id}")
async def api_revoke_permission(
    document_id: int,
    permission_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "owner")
    perm = db.query(DocumentPermission).filter(
        DocumentPermission.id == permission_id, DocumentPermission.document_id == doc.id
    ).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Доступ не найден")
    db.delete(perm)
    db.commit()
    return JSONResponse({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# VERSION HISTORY
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/admin/documents/{document_id}/revisions")
async def api_list_revisions(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "view")
    revisions = (
        db.query(DocumentRevision)
        .options(joinedload(DocumentRevision.saved_by))
        .filter(DocumentRevision.document_id == doc.id)
        .order_by(DocumentRevision.created_at.desc())
        .all()
    )
    return JSONResponse({
        "revisions": [
            {
                "id": r.id, "title": r.title, "version": r.version,
                "saved_by": serialize_admin(r.saved_by),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in revisions
        ]
    })


@router.post("/api/v1/admin/documents/{document_id}/revisions/{revision_id}/restore")
async def api_restore_revision(
    document_id: int,
    revision_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "edit")
    rev = db.query(DocumentRevision).filter(
        DocumentRevision.id == revision_id, DocumentRevision.document_id == doc.id
    ).first()
    if not rev or not rev.storage_filename:
        raise HTTPException(status_code=404, detail="Версия не найдена")

    # Checkpoint the current state before overwriting, so restoring is itself undoable.
    db.add(DocumentRevision(
        document_id=doc.id, title=doc.title, storage_filename=doc.storage_filename,
        version=doc.version, saved_by_admin_id=current_admin.id,
    ))
    doc.version += 1
    new_filename = _new_storage_filename(doc.id, doc.version, doc.file_type)
    src = os.path.join(_files_dir(), rev.storage_filename)
    dst = os.path.join(_files_dir(), new_filename)
    if os.path.isfile(src):
        shutil.copyfile(src, dst)
    else:
        open(dst, "wb").close()
    doc.storage_filename = new_filename
    doc.last_edited_by_admin_id = current_admin.id
    db.commit()
    return JSONResponse({"status": "ok", "version": doc.version})
