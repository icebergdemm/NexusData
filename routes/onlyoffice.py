"""
ONLYOFFICE Document Server integration — the actual editing surface for the
document library (routes/documents.py covers metadata: folders, sharing,
version history, department wiring).

Three endpoints:
- GET  /api/v1/admin/documents/{id}/onlyoffice-config — admin-session-gated,
  builds the signed config the frontend hands to ONLYOFFICE's DocsAPI.
- GET  /api/v1/onlyoffice/files/{id}/download — called BY Document Server
  (not the browser), gated by a short-lived download token instead of a
  session cookie.
- POST /api/v1/onlyoffice/callback/{id} — called BY Document Server when it
  saves; gated by ONLYOFFICE's own JWT (a DIFFERENT secret than the download
  token, since Document Server itself must be configured with it).
"""

import os
import logging
from datetime import datetime, timedelta

import jwt
import httpx
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session

from database import get_db
from config import get_settings
from models import AdminUser, Document, DocumentRevision
from routes.documents import (
    get_current_admin, load_document_or_404,
    _files_dir, _new_storage_filename,
)

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter()

DOWNLOAD_TOKEN_ALGORITHM = "HS256"
DOWNLOAD_TOKEN_TTL_SECONDS = 5 * 60


def _sign_download_token(document_id: int) -> str:
    payload = {
        "document_id": document_id,
        "exp": datetime.utcnow() + timedelta(seconds=DOWNLOAD_TOKEN_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=DOWNLOAD_TOKEN_ALGORITHM)


def _verify_download_token(token: str, document_id: int):
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[DOWNLOAD_TOKEN_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired download token")
    if payload.get("document_id") != document_id:
        raise HTTPException(status_code=401, detail="Token does not match document")


def _document_type_for(file_type: str) -> str:
    if file_type == "cell":
        return "cell"
    if file_type == "pdf":
        return "pdf"
    return "word"


_FILE_TYPE_EXT = {"cell": "xlsx", "pdf": "pdf", "word": "docx"}


@router.get("/api/v1/admin/documents/{document_id}/onlyoffice-config")
async def api_onlyoffice_config(
    document_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    doc, access = load_document_or_404(db, document_id, current_admin, "view")
    if not doc.storage_filename:
        raise HTTPException(status_code=409, detail="У документа ещё нет файла")
    if doc.file_type == "image":
        raise HTTPException(status_code=400, detail="Изображения открываются встроенным просмотрщиком, не ONLYOFFICE")

    can_edit = access in ("edit", "owner")
    can_comment = access in ("comment", "edit", "owner")

    download_token = _sign_download_token(doc.id)
    document_url = f"{settings.onlyoffice_callback_base_url}/api/v1/onlyoffice/files/{doc.id}/download?token={download_token}"
    callback_url = f"{settings.onlyoffice_callback_base_url}/api/v1/onlyoffice/callback/{doc.id}"

    # PDF has no "track changes"/comment concept the way word/cell do in
    # ONLYOFFICE's editor — it opens in forms/annotation mode, gated purely
    # by "edit" (annotate) vs view-only.
    is_pdf = doc.file_type == "pdf"
    config = {
        "document": {
            "fileType": _FILE_TYPE_EXT.get(doc.file_type, "docx"),
            "key": f"{doc.id}-v{doc.version}",
            "title": doc.title or "Без названия",
            "url": document_url,
            "permissions": {
                "edit": can_edit,
                "comment": False if is_pdf else can_comment,
                "download": True,
                "print": True,
                "review": False if is_pdf else can_edit,
            },
        },
        "documentType": _document_type_for(doc.file_type),
        "editorConfig": {
            "callbackUrl": callback_url,
            "mode": "edit" if can_edit else "view",
            "lang": "ru",
            "user": {
                "id": str(current_admin.id),
                "name": current_admin.discord_username or f"Admin #{current_admin.id}",
            },
        },
    }
    config["token"] = jwt.encode(config, settings.onlyoffice_jwt_secret, algorithm="HS256")

    return JSONResponse({
        "documentServerUrl": settings.onlyoffice_document_server_url,
        "config": config,
    })


@router.get("/api/v1/onlyoffice/files/{document_id}/download")
async def api_onlyoffice_download(
    document_id: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """Fetched BY Document Server, not the browser — gated by the short-lived
    download token minted in api_onlyoffice_config, not a session cookie."""
    _verify_download_token(token, document_id)
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or not doc.storage_filename:
        raise HTTPException(status_code=404, detail="Файл не найден")
    path = os.path.join(_files_dir(), doc.storage_filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Файл не найден на диске")
    return FileResponse(path, filename=doc.storage_filename)


@router.post("/api/v1/onlyoffice/callback/{document_id}")
async def api_onlyoffice_callback(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Called BY Document Server whenever it saves — see
    https://api.onlyoffice.com/editors/callback for the status contract.
    Always returns {"error": 0} once handled, even for statuses we no-op on;
    {"error": 1} only signals a genuine failure on our side."""
    body = await request.json()

    # Fail closed rather than silently accepting unsigned saves: without a
    # configured secret there is no way to verify this request actually came
    # from Document Server, and this endpoint writes attacker-suppliable
    # bytes to disk as the new document content (see below).
    if not settings.onlyoffice_jwt_secret:
        raise HTTPException(status_code=401, detail="ONLYOFFICE_JWT_SECRET not configured on the server")

    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
    token = token or body.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing ONLYOFFICE token")
    try:
        decoded = jwt.decode(token, settings.onlyoffice_jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid ONLYOFFICE token")
    # Document Server signs the callback body itself under a "payload"
    # claim — trust ONLY that signed copy, never the raw unsigned POST
    # body, and never a token minted for something else. Without this,
    # any valid signature was accepted regardless of its claims, so the
    # editor-config token every viewer already receives in
    # api_onlyoffice_config's response (signed with this SAME secret)
    # could be replayed here against an arbitrary document_id with an
    # attacker-chosen "url" — an SSRF + cross-document content-overwrite
    # primitive requiring only "view" access to any one document.
    signed_body = decoded.get("payload")
    if not isinstance(signed_body, dict):
        raise HTTPException(status_code=401, detail="Invalid ONLYOFFICE token payload")
    body = signed_body

    status_code = body.get("status")
    if status_code != 2:
        # 1 = still editing, 3/4 = save error/no changes, 6/7 = force-saved
        # variants — none of these need us to fetch a new file.
        return JSONResponse({"error": 0})

    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        return JSONResponse({"error": 1})

    file_url = body.get("url")
    if not file_url:
        return JSONResponse({"error": 1})

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            data = resp.content
    except Exception as e:
        logger.warning("ONLYOFFICE callback: failed to fetch saved file for document %s: %s", document_id, e)
        return JSONResponse({"error": 1})

    if doc.storage_filename:
        db.add(DocumentRevision(
            document_id=doc.id, title=doc.title, storage_filename=doc.storage_filename,
            version=doc.version, saved_by_admin_id=None,
        ))

    doc.version += 1
    new_filename = _new_storage_filename(doc.id, doc.version, doc.file_type)
    with open(os.path.join(_files_dir(), new_filename), "wb") as f:
        f.write(data)
    doc.storage_filename = new_filename

    users = body.get("users") or []
    if users:
        try:
            editor_admin = db.query(AdminUser).filter(AdminUser.id == int(users[0])).first()
            if editor_admin:
                doc.last_edited_by_admin_id = editor_admin.id
        except (TypeError, ValueError):
            pass

    db.commit()
    return JSONResponse({"error": 0})
