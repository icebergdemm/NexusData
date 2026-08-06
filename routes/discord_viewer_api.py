"""
Discord-вьюер (пункт 4): просмотр каналов сервера от лица бота с интерфейсом,
приближённым к Discord — список каналов, лента сообщений, отправка
сообщений/файлов, экспорт чата в JSON, список участников с аватарками и
привязанным Steam-профилем, управление ролями.

Все операции с живыми данными Discord (каналы/сообщения/роли) идут через
discord_bot.bot.schedule()+asyncio.wrap_future — бот живёт в собственном
event loop (см. discord_bot/bot.py), это тот же мост, что уже использует
routes/documents.py для проверки канала. Список участников — исключение:
он читается напрямую из таблицы DiscordMember (её каждые 10 минут наполняет
sync_members_task), это быстрее и не гоняет бота лишний раз ради страницы,
которую открывают часто.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from security import check_permission
from models import DiscordMember, PlayerData, AdminUser, AdminSession
from datetime import datetime

router = APIRouter()


def _get_admin(request: Request, db: Session) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            from sqlalchemy.orm import joinedload
            admin = db.query(AdminUser).options(joinedload(AdminUser.privilege_group)).filter(AdminUser.id == session.admin_id).first()
            if admin:
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


async def _call_bot(coro, timeout: float = 12.0):
    """Мост к боту с честной ошибкой вместо зависшего запроса, если бот не
    подключён к Discord или не отвечает в разумное время."""
    from discord_bot import bot as discord_bot
    try:
        return await asyncio.wait_for(asyncio.wrap_future(discord_bot.schedule(coro)), timeout=timeout)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Бот не ответил вовремя — попробуйте ещё раз")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка Discord: {e}")


@router.get("/api/v1/admin/discord-bot/viewer/tree")
async def api_viewer_tree(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)
    from discord_bot import bot as discord_bot
    result = await _call_bot(discord_bot.viewer_get_tree())
    result["can_send"] = check_permission(admin, "discord_bot", "broadcast")
    result["can_manage_roles"] = check_permission(admin, "discord_bot", "manage_config")
    return result


@router.get("/api/v1/admin/discord-bot/viewer/channels/{channel_id}/messages")
async def api_viewer_messages(channel_id: int, request: Request, before: Optional[int] = None, limit: int = 50, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)
    from discord_bot import bot as discord_bot
    messages = await _call_bot(discord_bot.viewer_get_messages(channel_id, before, limit))
    return {"messages": messages}


@router.post("/api/v1/admin/discord-bot/viewer/channels/{channel_id}/messages")
async def api_viewer_send_message(
    channel_id: int, request: Request,
    content: str = Form(""), file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "broadcast"):
        raise HTTPException(status_code=403)
    if not content.strip() and not file:
        raise HTTPException(status_code=400, detail="Пустое сообщение: нужен текст или файл")

    file_bytes = await file.read() if file else None
    if file_bytes and len(file_bytes) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Файл больше 8 МБ — лимит вложений Discord без буста сервера")

    from discord_bot import bot as discord_bot
    msg = await _call_bot(discord_bot.viewer_send_message(
        channel_id, content, file.filename if file else None, file_bytes,
    ))

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_viewer_send_message", {"channel_id": channel_id})
    return msg


@router.get("/api/v1/admin/discord-bot/viewer/channels/{channel_id}/export")
async def api_viewer_export(channel_id: int, request: Request, limit: int = 5000, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)
    from discord_bot import bot as discord_bot
    messages = await _call_bot(discord_bot.viewer_export_messages(channel_id, limit), timeout=120.0)

    import json as _json
    body = _json.dumps({"channel_id": str(channel_id), "exported_at": datetime.utcnow().isoformat(), "messages": messages}, ensure_ascii=False, indent=2)

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_viewer_export", {"channel_id": channel_id, "count": len(messages)})
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="discord_channel_{channel_id}.json"'},
    )


@router.get("/api/v1/admin/discord-bot/viewer/roles")
async def api_viewer_roles(request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)
    from discord_bot import bot as discord_bot
    roles = await _call_bot(discord_bot.viewer_get_roles())
    return {"roles": roles}


@router.get("/api/v1/admin/discord-bot/viewer/members")
async def api_viewer_members(request: Request, search: str = "", limit: int = 200, offset: int = 0, db: Session = Depends(get_db)):
    """Из таблицы DiscordMember (см. докстринг файла) — не живой запрос к
    Discord, поэтому открывается мгновенно даже на крупном сервере."""
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)

    q = db.query(DiscordMember)
    if search.strip():
        like = f"%{search.strip()}%"
        q = q.filter((DiscordMember.username.ilike(like)) | (DiscordMember.display_name.ilike(like)) | (DiscordMember.discord_id == search.strip()))
    total = q.count()
    members = q.order_by(DiscordMember.display_name).offset(max(0, offset)).limit(max(1, min(limit, 500))).all()

    discord_ids = [m.discord_id for m in members]
    steam_by_discord = {}
    if discord_ids:
        for p in db.query(PlayerData).filter(PlayerData.discord_id.in_(discord_ids)).all():
            steam_by_discord[p.discord_id] = {
                "steamid": p.player_steamid,
                "player_name": p.player_last_time_name,
                "avatar_url": p.steam_avatar_url,
                "confirmed": bool(p.discord_id_confirmed),
            }

    return {
        "total": total,
        "members": [
            {
                "discord_id": m.discord_id,
                "username": m.username,
                "display_name": m.display_name,
                "avatar_url": m.avatar,
                "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                "role_ids": m.role_ids or [],
                "steam": steam_by_discord.get(m.discord_id),
            }
            for m in members
        ],
    }


@router.get("/api/v1/admin/discord-bot/viewer/members/{discord_id}")
async def api_viewer_member_detail(discord_id: str, request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "view"):
        raise HTTPException(status_code=403)

    member = db.query(DiscordMember).filter(DiscordMember.discord_id == discord_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Участник не найден (нет в последней синхронизации)")
    steam = db.query(PlayerData).filter(PlayerData.discord_id == discord_id).first()

    return {
        "discord_id": member.discord_id,
        "username": member.username,
        "display_name": member.display_name,
        "avatar_url": member.avatar,
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "last_synced_at": member.last_synced_at.isoformat() if member.last_synced_at else None,
        "role_ids": member.role_ids or [],
        "steam": {
            "steamid": steam.player_steamid, "player_name": steam.player_last_time_name,
            "avatar_url": steam.steam_avatar_url, "confirmed": bool(steam.discord_id_confirmed),
        } if steam else None,
        "can_manage_roles": check_permission(admin, "discord_bot", "manage_config"),
    }


@router.put("/api/v1/admin/discord-bot/viewer/members/{discord_id}/roles")
async def api_viewer_member_roles(discord_id: str, request: Request, db: Session = Depends(get_db)):
    admin = _get_admin(request, db)
    if not check_permission(admin, "discord_bot", "manage_config"):
        raise HTTPException(status_code=403)

    body = await request.json()
    add_role_ids = body.get("add") or []
    remove_role_ids = body.get("remove") or []
    if not add_role_ids and not remove_role_ids:
        raise HTTPException(status_code=400, detail="Нечего менять")

    from discord_bot import bot as discord_bot
    result = await _call_bot(discord_bot.viewer_update_member_roles(int(discord_id), add_role_ids, remove_role_ids))

    # Держим локальный снимок в актуальном состоянии — иначе список
    # участников до следующей 10-минутной синхронизации показывал бы старые роли.
    member = db.query(DiscordMember).filter(DiscordMember.discord_id == discord_id).first()
    if member:
        member.role_ids = result.get("role_ids", member.role_ids)
        db.commit()

    from routes.admin import log_admin_action
    log_admin_action(db, admin.id, "discord_viewer_update_roles", {"discord_id": discord_id, "add": add_role_ids, "remove": remove_role_ids})

    if result.get("errors"):
        return JSONResponse(status_code=207, content={"status": "partial", "role_ids": result["role_ids"], "errors": result["errors"]})
    return {"status": "ok", "role_ids": result["role_ids"]}
