"""Local-only UI fixture. Requires --local-stub; never imports a live bot token."""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def create_stub_app(db_url: str) -> FastAPI:
    os.environ["DATABASE_URL"] = db_url
    os.environ["DISCORD_BOT_TOKEN"] = ""
    from database import Base, get_db
    from models import DiscordMember, PlayerData
    from routes import discord_viewer_api
    from discord_bot import bot as bot_module

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[DiscordMember.__table__, PlayerData.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as db:
        if not db.query(DiscordMember).count():
            db.add_all([
                DiscordMember(discord_id="1001", username="captain", display_name="Снегирь-2", avatar="", role_ids=["r-captain", "r-staff"]),
                DiscordMember(discord_id="1002", username="mechanic", display_name="Тайфун", avatar="", role_ids=["r-crew"]),
                DiscordMember(discord_id="1003", username="medic", display_name="Мурена", avatar="", role_ids=[]),
            ])
            db.commit()

    channels = [
        {"id": "2001", "name": "general", "position": 1, "can_send": True, "category_id": "3001", "type": "text", "voice_members": []},
        {"id": "2002", "name": "ops", "position": 2, "can_send": True, "category_id": "3001", "type": "text", "voice_members": []},
        {"id": "2101", "name": "general-discussion", "position": 0, "can_send": True, "category_id": "3001", "parent_id": "2001", "type": "thread", "voice_members": []},
        {"id": "2201", "name": "Bridge", "position": 3, "can_send": False, "category_id": "3001", "type": "voice", "voice_members": [
            {"id": "1001", "display_name": "Снегирь-2", "avatar_url": None, "self_mute": False, "self_deaf": False, "mute": False, "deaf": False, "video": False, "streaming": True},
            {"id": "1002", "display_name": "Тайфун", "avatar_url": None, "self_mute": True, "self_deaf": False, "mute": False, "deaf": False, "video": False, "streaming": False},
        ]},
        {"id": "2202", "name": "Ready Room", "position": 4, "can_send": False, "category_id": "3001", "type": "stage", "voice_members": []},
    ]
    messages = {
        "2001": [{"id": "9003", "channel_id": "2001", "content": "Реактор готов, проверка связи.", "author": {"id": "1001", "username": "captain", "display_name": "Снегирь-2", "avatar_url": None, "bot": False}, "created_at": datetime.now(timezone.utc).isoformat(), "edited_at": None, "attachments": [], "embeds": [], "reply_to_id": None, "reply_to": None, "pinned": True, "reactions": [{"emoji": "✅", "count": 3, "me": True}, {"emoji": "🔥", "count": 1, "me": False}]},
                    {"id": "9002", "channel_id": "2001", "content": "Экипаж, ответьте по готовности.", "author": {"id": "1002", "username": "mechanic", "display_name": "Тайфун", "avatar_url": None, "bot": False}, "created_at": datetime.now(timezone.utc).isoformat(), "edited_at": None, "attachments": [], "embeds": [], "reply_to_id": None, "reply_to": None, "pinned": False, "reactions": []}],
        "2002": [{"id": "8001", "channel_id": "2002", "content": "Расписание дежурств на сегодня.", "author": {"id": "1003", "username": "medic", "display_name": "Мурена", "avatar_url": None, "bot": False}, "created_at": datetime.now(timezone.utc).isoformat(), "edited_at": None, "attachments": [], "embeds": [], "reply_to_id": None, "reply_to": None, "pinned": False, "reactions": []}],
        "2101": [],
    }
    roles = [
        {"id": "r-staff", "name": "Администрация", "color": "#5865f2", "position": 50, "managed": False, "mentionable": True},
        {"id": "r-captain", "name": "Капитаны", "color": "#ed4245", "position": 40, "managed": False, "mentionable": True},
        {"id": "r-crew", "name": "Экипаж", "color": "#23a55a", "position": 10, "managed": False, "mentionable": True},
    ]

    async def viewer_tree():
        return {"guild": {"id": "123", "name": "ASPER • Operations", "icon_url": None}, "categories": [{"id": "3001", "name": "ОСНОВНЫЕ", "position": 1}], "channels": channels, "can_send": True, "can_manage_roles": True}
    async def viewer_archived_threads(channel_id, source, before=None):
        threads = [{"id": "2102", "name": "archived-maintenance", "position": 0, "can_send": False, "category_id": "3001", "parent_id": str(channel_id), "type": "thread", "archived": True, "voice_members": []}]
        if str(channel_id) != "2001" or source != "public" or before:
            threads = []
        return {"threads": threads, "before": None, "has_more": False}
    async def viewer_messages(channel_id, before, limit, around=None):
        items = list(messages.get(str(channel_id), []))
        if around:
            return items
        if before:
            items = [item for item in items if int(item["id"]) < int(before)]
        return items[:limit]
    async def viewer_search(filters, offset=0, limit=25):
        rows = [item for batch in messages.values() for item in batch if filters.get("content", "").lower() in item["content"].lower()]
        return {"messages": rows[offset:offset + limit], "total_results": len(rows), "offset": offset, "has_more": offset + limit < len(rows), "indexing": False, "retry_after": None}
    async def viewer_audit(before=None, limit=50, user_id=None, action_type=None):
        entries = [{"id": "7003", "action_type": 11, "action": "channel_update", "actor": {"id": "1001", "name": "Снегирь-2"}, "target": {"id": "2001", "name": "general"}, "created_at": datetime.now(timezone.utc).isoformat(), "reason": "Проверка панели", "changes": [{"key": "topic", "old_value": "старое", "new_value": "актуальное"}]}, {"id": "7002", "action_type": 20, "action": "member_leave", "actor": None, "target": {"id": "1003", "name": "Мурена"}, "created_at": datetime.now(timezone.utc).isoformat(), "reason": None, "changes": []}, {"id": "6999", "action_type": 10, "action": "channel_create", "actor": {"id": "1002", "name": "Тайфун"}, "target": {"id": "2201", "name": "Bridge"}, "created_at": datetime.now(timezone.utc).isoformat(), "reason": "Старая запись", "changes": []}]
        remaining = [entry for entry in entries if not before or int(entry["id"]) < int(before)]
        page = remaining[:min(limit, 2)]
        return {"entries": page, "before": page[-1]["id"] if page else None, "has_more": len(remaining) > len(page)}

    for name, implementation in {
        "viewer_get_tree": viewer_tree, "viewer_get_archived_threads": viewer_archived_threads,
        "viewer_get_messages": viewer_messages,
        "viewer_validate_channel": lambda *_args: asyncio.sleep(0, result=True),
        "viewer_search_messages": viewer_search, "viewer_get_roles": lambda: asyncio.sleep(0, result=roles),
        "viewer_get_audit_log": viewer_audit,
        "viewer_get_reaction_users": lambda *_args: asyncio.sleep(0, result={"users": [{"id": "1001", "username": "captain", "display_name": "Снегирь-2", "avatar_url": None, "bot": False}], "next_after": None}),
        "viewer_toggle_reaction": lambda *_args: asyncio.sleep(0, result={"me": True}),
        "viewer_create_channel_invite": lambda *_args: asyncio.sleep(0, result={"url": "https://discord.gg/local-stub", "code": "local-stub"}),
        "viewer_set_channel_member_access": lambda *_args: asyncio.sleep(0, result={"effective_view_channel": True}),
        "viewer_delete_channel": lambda *_args: asyncio.sleep(0, result={"deleted": True}),
        "viewer_move_member": lambda *_args: asyncio.sleep(0, result={"member_id": "1001", "channel_id": "2201"}),
        "viewer_set_message_pinned": lambda *_args: asyncio.sleep(0, result={"pinned": True}),
        "viewer_delete_message": lambda *_args: asyncio.sleep(0, result={"deleted": True}),
        "viewer_create_message_thread": lambda *_args: asyncio.sleep(0, result={"id": "2102", "name": "new-thread", "parent_id": "2001"}),
    }.items():
        setattr(bot_module, name, implementation)

    async def passthrough(coro, **_kwargs): return await coro
    discord_viewer_api._call_bot = passthrough
    admin = SimpleNamespace(id=9001, is_superuser=True, discord_username="Local viewer admin")
    discord_viewer_api._get_admin = lambda *_args: admin
    discord_viewer_api._get_websocket_admin = lambda *_args: admin
    discord_viewer_api.check_permission = lambda *_args: True
    import routes.admin as admin_routes
    admin_routes.log_admin_action = lambda *_args, **_kwargs: None

    app = FastAPI()
    app.include_router(discord_viewer_api.router)
    app.state.local_admin = {"id": 9001, "discord_username": "Local viewer admin", "discord_avatar": None, "is_superuser": True, "permissions": {"discord_bot": {"view": True, "broadcast": True, "manage_config": True}}, "my_departments": [], "linked_player": {"player_id": 1, "player_steamid": "0", "forced_display_name": "Local"}, "csrf_token": "local-only"}

    @app.get("/api/v1/me")
    async def current_admin(): return app.state.local_admin

    @app.get("/api/v1/admin/discord-bot")
    async def bot_status(): return {"members_count": 3, "bot_online": True, "last_sync": None, "config": {}, "antiraid_events": [], "twink_suspects": [], "failed_role_syncs": [], "can_edit": True, "can_manage_config": True}

    @app.get("/api/v1/me/notifications")
    async def notifications(): return {"notifications": []}

    def get_test_db():
        db = Session()
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db] = get_test_db
    app.state.local_test_engine = engine
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated Discord viewer site fixture")
    parser.add_argument("--local-stub", action="store_true", help="required safety switch; all Discord methods are fixtures")
    args = parser.parse_args()
    if not args.local_stub:
        parser.error("refusing to run without --local-stub")
    with tempfile.TemporaryDirectory(prefix="nexus-discord-viewer-") as temp_dir:
        app = create_stub_app(f"sqlite:///{Path(temp_dir) / 'viewer-test.db'}")
        config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        frontend = None
        try:
            thread.start()
            npm_cmd = shutil.which("npm.cmd") or shutil.which("npm")
            if not npm_cmd:
                raise RuntimeError("npm.cmd is not available on PATH")
            if npm_cmd.lower().endswith(".cmd"):
                frontend = subprocess.Popen(["cmd.exe", "/c", npm_cmd, "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"], cwd=ROOT / "frontend")
            else:
                frontend = subprocess.Popen([npm_cmd, "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"], cwd=ROOT / "frontend")
            print("Local stub admin: Local viewer admin (9001)")
            print("Open http://127.0.0.1:5173/admin/discord-bot — classic UI with Discord API fixtures only.", flush=True)
            while thread.is_alive() and frontend.poll() is None:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True
            if frontend and frontend.poll() is None:
                frontend.terminate()
                frontend.wait(timeout=10)
            thread.join(timeout=10)
            app.state.local_test_engine.dispose()


if __name__ == "__main__":
    main()
