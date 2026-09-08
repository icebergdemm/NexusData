"""
API страницы «Звуки для ивентов».

Работа с .wav/.mp3 на игровых серверах по SFTP плюс локальные сжатые копии —
вся логика в services_sounds.py, здесь только права, валидация и HTTP.

Права (permissions_registry, страница "sounds"): view / upload / rename /
delete / restore. Настройки SFTP-каталогов правит ТОЛЬКО суперпользователь —
это отдельная проверка is_superuser, а не право, потому что настройка меняет
то, куда пишутся файлы на боевых серверах.
"""
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

import services_sounds
from database import get_db
from models import AdminUser, AdminSession
from security import check_permission

logger = logging.getLogger(__name__)
router = APIRouter()


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


def _require(admin: AdminUser, action: str):
    if not check_permission(admin, "sounds", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _require_superuser(admin: AdminUser):
    if not admin.is_superuser:
        raise HTTPException(status_code=403, detail="Доступно только суперпользователю")


@router.get("/api/v1/admin/sounds")
async def api_list_sounds(
    server: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Звуки одного сервера: лежащие на нём + удалённые с восстановимой копией."""
    _require(current_admin, "view")
    try:
        data = await run_in_threadpool(services_sounds.list_sounds, db, server)
    except ValueError as e:
        # Не настроено (нет каталога/SFTP) — это состояние конфигурации, а не
        # ошибка сервера: отдаём 200 с флагом, чтобы страница показала подсказку.
        return JSONResponse({"server_name": server, "files": [], "deleted": [],
                             "not_configured": True, "error": str(e)})
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return JSONResponse({**data, "not_configured": False})


@router.get("/api/v1/admin/sounds/permissions")
async def api_sound_permissions(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Что текущему администратору доступно на странице — чтобы интерфейс не
    показывал кнопки, которые всё равно вернут 403."""
    _require(current_admin, "view")
    return JSONResponse({
        "can_upload": check_permission(current_admin, "sounds", "upload"),
        "can_rename": check_permission(current_admin, "sounds", "rename"),
        "can_delete": check_permission(current_admin, "sounds", "delete"),
        "can_restore": check_permission(current_admin, "sounds", "restore"),
        "is_superuser": bool(current_admin.is_superuser),
        "servers": [name for _num, name in services_sounds.SERVERS],
        "max_size_mb": services_sounds.MAX_SOUND_BYTES // (1024 * 1024),
    })


@router.post("/api/v1/admin/sounds/upload")
async def api_upload_sound(
    server: str = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "upload")
    data = await file.read()
    try:
        result = await run_in_threadpool(
            services_sounds.upload_sound, db, server, file.filename or "", data, current_admin.id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sound_upload",
                     {"server": server, "filename": result["filename"]})
    return JSONResponse(result)


@router.post("/api/v1/admin/sounds/rename")
async def api_rename_sound(
    request: Request,
    server: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "rename")
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    new_name = (body.get("new_name") or "").strip()
    if not filename or not new_name:
        raise HTTPException(status_code=400, detail="Укажите текущее и новое имя файла")
    try:
        result = await run_in_threadpool(
            services_sounds.rename_sound, db, server, filename, new_name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sound_rename",
                     {"server": server, "from": filename, "to": result["filename"]})
    return JSONResponse(result)


@router.delete("/api/v1/admin/sounds")
async def api_delete_sound(
    server: str = Query(...),
    filename: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Удаляет звук с игрового сервера. Локальная копия сохраняется — звук
    можно вернуть через /restore."""
    _require(current_admin, "delete")
    try:
        result = await run_in_threadpool(
            services_sounds.delete_sound, db, server, filename, current_admin.id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sound_delete",
                     {"server": server, "filename": result["filename"],
                      "restorable": result["restorable"]})
    return JSONResponse(result)


@router.post("/api/v1/admin/sounds/restore")
async def api_restore_sound(
    request: Request,
    server: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require(current_admin, "restore")
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Укажите имя файла")
    try:
        result = await run_in_threadpool(
            services_sounds.restore_sound, db, server, filename, current_admin.id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sound_restore",
                     {"server": server, "filename": result["filename"]})
    return JSONResponse(result)


@router.delete("/api/v1/admin/sounds/backup")
async def api_purge_sound_backup(
    server: str = Query(...),
    filename: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Окончательно убрать локальную копию уже удалённого звука.

    Требует права delete: это единственное действие, после которого звук
    восстановить уже нельзя.
    """
    _require(current_admin, "delete")
    await run_in_threadpool(services_sounds.purge_backup, db, server, filename)

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sound_purge_backup",
                     {"server": server, "filename": filename})
    return JSONResponse({"status": "ok"})


@router.get("/api/v1/admin/sounds/file")
async def api_get_sound_file(
    server: str = Query(...),
    filename: str = Query(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Сам файл — для прослушивания в браузере и скачивания."""
    _require(current_admin, "view")
    try:
        data, clean = await run_in_threadpool(
            services_sounds.get_sound_bytes, db, server, filename
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    media = "audio/mpeg" if clean.lower().endswith(".mp3") else "audio/wav"
    # Имена звуков часто кириллические ("Свой БСО.mp3") — HTTP-заголовки
    # обязаны быть latin-1, поэтому голый filename="..." с не-ASCII падал
    # с UnicodeEncodeError прямо в Response.__init__ (500 без вменяемого
    # ответа клиенту). ASCII-заглушка в filename= — для древних клиентов,
    # которые не понимают filename*=; настоящее имя — в filename* (RFC 5987).
    ascii_fallback = clean.encode("ascii", "replace").decode("ascii").replace('"', "_")
    encoded = quote(clean, safe="")
    return Response(
        content=data,
        media_type=media,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}',
        },
    )


# ── Настройки (только суперпользователь) ─────────────────────────────────

@router.get("/api/v1/admin/sounds/settings")
async def api_get_sound_settings(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_superuser(current_admin)
    return JSONResponse({"servers": services_sounds.get_settings_overview(db)})


@router.post("/api/v1/admin/sounds/settings")
async def api_save_sound_settings(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Сохраняет каталог со звуками для каждого сервера.

    Реквизиты SFTP (хост/логин/пароль) здесь НЕ дублируются: используются уже
    настроенные подключения к игровым серверам из .env — это те же машины.
    """
    _require_superuser(current_admin)
    body = await request.json()
    for entry in body.get("servers") or []:
        try:
            num = int(entry.get("server_number"))
        except (TypeError, ValueError):
            continue
        if num not in (1, 2):
            continue
        services_sounds.set_sounds_dir(db, num, entry.get("sounds_dir"))

    from routes.admin import log_admin_action
    log_admin_action(db, current_admin.id, "sound_settings_update", {})
    return JSONResponse({"servers": services_sounds.get_settings_overview(db)})


@router.post("/api/v1/admin/sounds/settings/test")
async def api_test_sound_connection(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Проверка подключения к каталогу звуков без сохранения — чтобы не искать
    опечатку в пути методом «сохранил и посмотрел, что сломалось»."""
    _require_superuser(current_admin)
    body = await request.json()
    try:
        num = int(body.get("server_number"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный сервер")

    path = (body.get("sounds_dir") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="Укажите каталог")

    def _probe():
        from services_sftp_filemanager import SFTPFileManager
        mgr = SFTPFileManager(num)
        if not mgr.is_configured():
            raise ValueError("SFTP для этого сервера не настроен в .env")
        mgr.base_dir = path
        files = mgr.list_directory("")
        audio = [
            f.name for f in files
            if not f.is_dir and f.name.lower().endswith((".wav", ".mp3"))
        ]
        return {"ok": True, "total_entries": len(files), "sound_files": len(audio)}

    try:
        return JSONResponse(await run_in_threadpool(_probe))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не удалось прочитать каталог: {e}")
