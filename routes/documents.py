"""
Shared document editor — a Google-Docs-like rich text editor with a shared
document library, folders, per-document sharing (view/comment/edit),
version history, and inline comments.

Content is stored as Tiptap/ProseMirror JSON. A plain-text extract is kept
alongside it for search. All endpoints are JSON, consumed by the React SPA's
Documents pages.
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_
from typing import Optional
from datetime import datetime, timedelta
import os
import shutil
import logging

from database import get_db
from security import check_permission
from config import get_settings
from models import (
    AdminUser, AdminSession, AdminPrivilegeGroup,
    Document, DocumentFolder, DocumentPermission, DocumentRevision,
    DocumentComment, DocumentStar,
)

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter()

ACCESS_RANK = {"view": 1, "comment": 2, "edit": 3, "owner": 4}
VALID_LEVELS = {"view", "comment", "edit"}


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
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def require_documents_page(admin: AdminUser, action: str = "view"):
    if not check_permission(admin, "documents", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


# ── Access-level resolution ──────────────────────────────────────────────────

def get_document_access(document: Document, admin: AdminUser) -> Optional[str]:
    """Highest access level the admin has on this document, or None."""
    if admin.is_superuser or document.owner_admin_id == admin.id:
        return "owner"

    best = None
    for perm in document.permissions:
        matches = (
            (perm.grantee_type == "admin" and perm.grantee_admin_id == admin.id)
            or (perm.grantee_type == "privilege_group" and admin.privilege_group_id
                and perm.grantee_group_id == admin.privilege_group_id)
            or (perm.grantee_type == "everyone")
        )
        if matches and (best is None or ACCESS_RANK[perm.level] > ACCESS_RANK[best]):
            best = perm.level
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

    access = get_document_access(doc, admin)
    if access is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if ACCESS_RANK[access] < ACCESS_RANK[min_level]:
        raise HTTPException(status_code=403, detail="Недостаточно прав на документ")
    return doc, access


def extract_plain_text(node) -> str:
    if not isinstance(node, dict):
        return ""
    parts = []
    if node.get("type") == "text":
        parts.append(node.get("text", ""))
    for child in (node.get("content") or []):
        parts.append(extract_plain_text(child))
    return " ".join(p for p in parts if p)


def serialize_admin(admin: Optional[AdminUser]) -> Optional[dict]:
    if not admin:
        return None
    return {"id": admin.id, "name": admin.discord_username, "avatar": admin.discord_avatar}


def serialize_document_row(doc: Document, admin: AdminUser, starred_ids: set) -> dict:
    return {
        "id": doc.id,
        "title": doc.title,
        "icon": doc.icon,
        "folder_id": doc.folder_id,
        "owner": serialize_admin(doc.owner),
        "last_edited_by": serialize_admin(doc.last_editor),
        "is_archived": doc.is_archived,
        "is_deleted": doc.is_deleted,
        "deleted_at": doc.deleted_at.isoformat() if doc.deleted_at else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "version": doc.version,
        "is_starred": doc.id in starred_ids,
        "access_level": get_document_access(doc, admin),
        "is_mine": doc.owner_admin_id == admin.id,
    }


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

    if scope == "all" and not current_admin.is_superuser:
        raise HTTPException(status_code=403, detail="Только для суперпользователя")

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
        like = f"%{search}%"
        q = q.filter(or_(Document.title.ilike(like), Document.content_text.ilike(like)))

    order_col = Document.deleted_at.desc() if scope == "trash" else Document.updated_at.desc()
    docs = q.order_by(order_col).limit(500).all()

    starred_ids = {
        row[0] for row in db.query(DocumentStar.document_id).filter(DocumentStar.admin_id == current_admin.id).all()
    }
    return JSONResponse({"documents": [serialize_document_row(d, current_admin, starred_ids) for d in docs]})


@router.post("/api/v1/admin/documents")
async def api_create_document(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "edit")
    body = await request.json() if await request.body() else {}
    title = (body.get("title") or "Без названия").strip() or "Без названия"
    folder_id = body.get("folder_id")
    if folder_id is not None:
        folder = db.query(DocumentFolder).filter(
            DocumentFolder.id == folder_id, DocumentFolder.owner_admin_id == current_admin.id
        ).first()
        if not folder:
            raise HTTPException(status_code=404, detail="Папка не найдена")

    doc = Document(
        title=title,
        folder_id=folder_id,
        owner_admin_id=current_admin.id,
        last_edited_by_admin_id=current_admin.id,
        content={"type": "doc", "content": [{"type": "paragraph"}]},
        content_text="",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return JSONResponse({"id": doc.id})


@router.get("/api/v1/admin/documents/share-candidates")
async def api_share_candidates(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
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
    })


@router.get("/api/v1/admin/documents/{document_id}")
async def api_get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, access = load_document_or_404(db, document_id, current_admin, "view")
    is_starred = db.query(DocumentStar).filter(
        DocumentStar.document_id == doc.id, DocumentStar.admin_id == current_admin.id
    ).first() is not None

    return JSONResponse({
        "id": doc.id,
        "title": doc.title,
        "icon": doc.icon,
        "content": doc.content,
        "folder_id": doc.folder_id,
        "owner": serialize_admin(doc.owner),
        "last_edited_by": serialize_admin(doc.last_editor),
        "version": doc.version,
        "is_archived": doc.is_archived,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "access_level": access,
        "is_starred": is_starred,
    })


@router.patch("/api/v1/admin/documents/{document_id}")
async def api_update_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, access = load_document_or_404(db, document_id, current_admin, "edit")
    body = await request.json()

    base_version = body.get("base_version")
    if base_version is not None and base_version != doc.version:
        return JSONResponse(
            status_code=409,
            content={
                "error": "conflict",
                "detail": "Документ был изменён в другом месте. Обновите страницу.",
                "current_version": doc.version,
                "current_title": doc.title,
                "current_content": doc.content,
            },
        )

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
    if "content" in body:
        # Snapshot a checkpoint revision if this is the first save, or enough
        # time passed since the last one — groups edits into Google-Docs-like
        # "named version" checkpoints instead of one row per keystroke.
        last_rev = (
            db.query(DocumentRevision)
            .filter(DocumentRevision.document_id == doc.id)
            .order_by(DocumentRevision.created_at.desc())
            .first()
        )
        if not last_rev or last_rev.created_at < datetime.utcnow() - timedelta(minutes=5):
            db.add(DocumentRevision(
                document_id=doc.id, title=doc.title, content=doc.content,
                version=doc.version, saved_by_admin_id=current_admin.id,
            ))
        doc.content = body["content"]
        doc.content_text = extract_plain_text(body["content"])[:65000]
        changed = True

    if changed:
        doc.version += 1
        doc.last_edited_by_admin_id = current_admin.id
        db.commit()
        db.refresh(doc)

    return JSONResponse({"status": "ok", "version": doc.version, "updated_at": doc.updated_at.isoformat()})


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
        content=doc.content,
        content_text=doc.content_text,
        owner_admin_id=current_admin.id,
        last_edited_by_admin_id=current_admin.id,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
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
    return JSONResponse({
        "owner": serialize_admin(doc.owner),
        "permissions": [
            {
                "id": p.id,
                "grantee_type": p.grantee_type,
                "grantee_admin": serialize_admin(p.grantee_admin) if p.grantee_admin_id else None,
                "grantee_group": {"id": p.grantee_group.id, "name": p.grantee_group.name} if p.grantee_group_id and p.grantee_group else None,
                "level": p.level,
            }
            for p in doc.permissions
        ],
    })


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
    if grantee_type not in ("admin", "privilege_group", "everyone"):
        raise HTTPException(status_code=400, detail="Некорректный тип получателя")
    if level not in VALID_LEVELS:
        raise HTTPException(status_code=400, detail="Некорректный уровень доступа")

    grantee_admin_id = body.get("grantee_id") if grantee_type == "admin" else None
    grantee_group_id = body.get("grantee_id") if grantee_type == "privilege_group" else None

    if grantee_type == "admin" and grantee_admin_id == doc.owner_admin_id:
        raise HTTPException(status_code=400, detail="Владелец уже имеет полный доступ")

    existing = None
    for p in doc.permissions:
        if p.grantee_type == grantee_type and p.grantee_admin_id == grantee_admin_id and p.grantee_group_id == grantee_group_id:
            existing = p
            break

    if existing:
        existing.level = level
    else:
        db.add(DocumentPermission(
            document_id=doc.id, grantee_type=grantee_type,
            grantee_admin_id=grantee_admin_id, grantee_group_id=grantee_group_id,
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


@router.get("/api/v1/admin/documents/{document_id}/revisions/{revision_id}")
async def api_get_revision(
    document_id: int,
    revision_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "view")
    rev = db.query(DocumentRevision).filter(
        DocumentRevision.id == revision_id, DocumentRevision.document_id == doc.id
    ).first()
    if not rev:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    return JSONResponse({"id": rev.id, "title": rev.title, "content": rev.content, "created_at": rev.created_at.isoformat()})


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
    if not rev:
        raise HTTPException(status_code=404, detail="Версия не найдена")

    # Checkpoint the current state before overwriting, so restoring is itself undoable.
    db.add(DocumentRevision(
        document_id=doc.id, title=doc.title, content=doc.content,
        version=doc.version, saved_by_admin_id=current_admin.id,
    ))
    doc.title = rev.title or doc.title
    doc.content = rev.content
    doc.content_text = extract_plain_text(rev.content or {})[:65000]
    doc.version += 1
    doc.last_edited_by_admin_id = current_admin.id
    db.commit()
    return JSONResponse({"status": "ok", "version": doc.version})


# ══════════════════════════════════════════════════════════════════════════════
# COMMENTS
# ══════════════════════════════════════════════════════════════════════════════

def serialize_comment(c: DocumentComment) -> dict:
    return {
        "id": c.id,
        "parent_id": c.parent_id,
        "author": serialize_admin(c.author),
        "body": c.body,
        "anchor_text": c.anchor_text,
        "mark_id": c.mark_id,
        "resolved": c.resolved,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/api/v1/admin/documents/{document_id}/comments")
async def api_list_comments(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "view")
    comments = (
        db.query(DocumentComment)
        .options(joinedload(DocumentComment.author))
        .filter(DocumentComment.document_id == doc.id)
        .order_by(DocumentComment.created_at)
        .all()
    )
    return JSONResponse({"comments": [serialize_comment(c) for c in comments]})


@router.post("/api/v1/admin/documents/{document_id}/comments")
async def api_create_comment(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "comment")
    body = await request.json()
    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Комментарий не может быть пустым")

    comment = DocumentComment(
        document_id=doc.id,
        parent_id=body.get("parent_id"),
        author_admin_id=current_admin.id,
        body=text[:2000],
        anchor_text=(body.get("anchor_text") or None),
        mark_id=body.get("mark_id"),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    comment.author = current_admin
    return JSONResponse(serialize_comment(comment))


@router.patch("/api/v1/admin/documents/{document_id}/comments/{comment_id}")
async def api_update_comment(
    document_id: int,
    comment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, access = load_document_or_404(db, document_id, current_admin, "comment")
    comment = db.query(DocumentComment).filter(
        DocumentComment.id == comment_id, DocumentComment.document_id == doc.id
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Комментарий не найден")

    body = await request.json()
    if "resolved" in body:
        comment.resolved = bool(body["resolved"])
    db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/api/v1/admin/documents/{document_id}/comments/{comment_id}")
async def api_delete_comment(
    document_id: int,
    comment_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, access = load_document_or_404(db, document_id, current_admin, "comment")
    comment = db.query(DocumentComment).filter(
        DocumentComment.id == comment_id, DocumentComment.document_id == doc.id
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    if comment.author_admin_id != current_admin.id and access != "owner":
        raise HTTPException(status_code=403, detail="Можно удалять только свои комментарии")
    db.delete(comment)
    db.commit()
    return JSONResponse({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# INLINE IMAGE UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/v1/admin/documents/{document_id}/upload-image")
async def api_upload_image(
    document_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    require_documents_page(current_admin, "view")
    doc, _ = load_document_or_404(db, document_id, current_admin, "edit")

    ext = os.path.splitext(file.filename or "")[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        raise HTTPException(status_code=400, detail="Недопустимый формат изображения")

    img_dir = os.path.join(settings.documents_upload_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    safe_name = f"doc_{document_id}_{int(datetime.utcnow().timestamp() * 1000)}{ext}"
    save_path = os.path.join(img_dir, safe_name)
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return JSONResponse({"url": f"/documents/images/{safe_name}"})
