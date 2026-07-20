"""
NexusData Discord Bot
- Syncs members to NexusData every 10 minutes
- Forwards all messages to a designated channel
- Anti-raid detection
- Publishes rating embeds every 5 minutes
- Polls NexusData for bot actions
- Twink detection
"""

import os
import asyncio
import logging
import json
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Tuple

import discord
from discord.ext import commands, tasks
from discord import app_commands
import httpx
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
API_URL = os.getenv("NEXUSDATA_API_URL", "https://nd.rp-asper.ru")
API_KEY = os.getenv("NEXUSDATA_API_KEY", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
FORWARD_CHANNEL_ID = int(os.getenv("FORWARD_CHANNEL_ID", "0"))
RATING_CHANNEL_ID = int(os.getenv("RATING_CHANNEL_ID", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("NexusBot")

# All server timestamps are stored/received in UTC; embeds should always show MSK
# regardless of the viewer's own Discord client timezone setting.
MSK_OFFSET = timedelta(hours=3)


def format_msk(dt: datetime) -> str:
    return (dt + MSK_OFFSET).strftime("%d.%m.%Y %H:%M")


def format_msk_time(dt: datetime) -> str:
    return (dt + MSK_OFFSET).strftime("%H:%M")


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Anti-raid tracking
_join_timestamps = []  # list of join times in last 60 seconds
RAID_THRESHOLD = 5  # joins per minute to trigger alert
RAID_WINDOW = 60  # seconds

# Discord channel IDs linked to a department "Чат" tab — refreshed every
# 10 min by sync_members_task, consulted in on_message to mirror messages.
_department_chat_channel_ids: set = set()

# Rating message IDs (persisted to JSON file)
RATING_IDS_FILE = "rating_message_ids.json"


def _load_rating_ids() -> dict:
    """Load rating message IDs from JSON file."""
    try:
        if os.path.exists(RATING_IDS_FILE):
            with open(RATING_IDS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_rating_ids(ids: dict):
    """Save rating message IDs to JSON file."""
    try:
        with open(RATING_IDS_FILE, "w") as f:
            json.dump(ids, f)
    except Exception as e:
        logger.warning(f"Failed to save rating IDs: {e}")


_rating_message_ids = _load_rating_ids()

# HTTP client
_http = httpx.AsyncClient(timeout=15.0, headers={"X-API-Key": API_KEY})


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

async def api_get(path: str):
    try:
        r = await _http.get(f"{API_URL}{path}")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"API GET {path} failed: {e}")
        return None


async def api_post(path: str, data: dict = None):
    try:
        r = await _http.post(f"{API_URL}{path}", json=data or {})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"API POST {path} failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    global FORWARD_CHANNEL_ID, RATING_CHANNEL_ID
    logger.info(f"Bot ready: {bot.user} (guilds: {len(bot.guilds)})")

    # Register persistent views (survive bot restarts)
    # Done in setup_hook below (classes defined later in file)

    # Load saved config from NexusData
    config = await api_get("/api/v1/bot/config")
    if config:
        try:
            if config.get("forward_channel_id"):
                FORWARD_CHANNEL_ID = int(config["forward_channel_id"])
                logger.info(f"Loaded forward_channel_id: {FORWARD_CHANNEL_ID}")
        except (ValueError, TypeError):
            pass
        try:
            if config.get("rating_channel_id"):
                RATING_CHANNEL_ID = int(config["rating_channel_id"])
                logger.info(f"Loaded rating_channel_id: {RATING_CHANNEL_ID}")
        except (ValueError, TypeError):
            pass

    sync_members_task.start()
    poll_actions_task.start()
    poll_fast_actions_task.start()
    poll_role_sync_actions_task.start()
    publish_rating_task.start()
    publish_department_ratings_task.start()
    round_status_task.start()

    # Initial ticket status refresh
    await refresh_ticket_status()


@bot.event
async def on_member_join(member: discord.Member):
    """Anti-raid: track joins and alert if threshold exceeded."""
    now = datetime.utcnow()
    _join_timestamps.append(now)

    # Clean old entries
    cutoff = now - timedelta(seconds=RAID_WINDOW)
    while _join_timestamps and _join_timestamps[0] < cutoff:
        _join_timestamps.pop(0)

    if len(_join_timestamps) >= RAID_THRESHOLD:
        # Potential raid!
        await api_post("/api/v1/bot/antiraid/event", {
            "event_type": "mass_join",
            "discord_id": str(member.id),
            "username": str(member),
            "details": {
                "joins_in_window": len(_join_timestamps),
                "window_seconds": RAID_WINDOW,
                "account_age_days": (now - member.created_at.replace(tzinfo=None)).days if member.created_at else None,
            },
        })
        logger.warning(f"RAID ALERT: {len(_join_timestamps)} joins in {RAID_WINDOW}s")

    # Check suspicious account (very new)
    if member.created_at:
        account_age = (now - member.created_at.replace(tzinfo=None)).days
        if account_age < 7:
            await api_post("/api/v1/bot/antiraid/event", {
                "event_type": "suspicious_account",
                "discord_id": str(member.id),
                "username": str(member),
                "details": {"account_age_days": account_age},
            })


# ═══════════════════════════════════════════════════════════
# TASKS
# ═══════════════════════════════════════════════════════════

@tasks.loop(minutes=10)
async def sync_members_task():
    """Sync all guild members to NexusData every 10 minutes."""
    global FORWARD_CHANNEL_ID, _department_chat_channel_ids

    # Reload forward channel config
    config = await api_get("/api/v1/bot/config")
    if config and config.get("forward_channel_id"):
        try:
            FORWARD_CHANNEL_ID = int(config["forward_channel_id"])
        except (ValueError, TypeError):
            pass

    # Reload department chat channel IDs (item 6)
    chat_channels = await api_get("/api/v1/bot/department-chat/channels")
    if chat_channels is not None:
        _department_chat_channel_ids = set(chat_channels.get("channel_ids") or [])

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    members_data = []
    for member in guild.members:
        if member.bot:
            continue
        members_data.append({
            "discord_id": str(member.id),
            "username": str(member),
            "display_name": member.display_name,
            "avatar": str(member.display_avatar.url) if member.display_avatar else None,
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "role_ids": [str(r.id) for r in member.roles],
        })

    if members_data:
        result = await api_post("/api/v1/bot/members/sync", {"members": members_data})
        if result:
            logger.info(f"Synced {result.get('synced', 0)} members to NexusData")


@tasks.loop(seconds=30)
async def poll_actions_task():
    """Poll NexusData for pending bot actions."""
    data = await api_get("/api/v1/bot/actions")
    if not data:
        return

    for action in data:
        action_id = action["id"]
        action_type = action["action_type"]
        # "check_channel_category" is handled exclusively by
        # poll_fast_actions_task (every ~2s) — the backend request that
        # queues it waits synchronously-ish for a fast answer, so leave it
        # for that loop rather than racing it here. "sync_department_role"
        # is handled by poll_role_sync_actions_task (every ~3s) — the site
        # is meant to be the source of truth for roles "instantly", not on
        # this loop's 30s cadence.
        if action_type in ("check_channel_category", "sync_department_role"):
            continue
        payload = action.get("payload", {})

        success, error_message = await execute_bot_action(action_type, payload)
        await api_post(f"/api/v1/bot/actions/{action_id}/ack", {
            "success": success,
            "error_message": None if success else (error_message or "Execution failed"),
        })


@tasks.loop(seconds=2)
async def poll_fast_actions_task():
    """Fast-polled sibling of poll_actions_task, exclusively for
    "check_channel_category" — routes/documents.py's verify-channel endpoint
    queues one of these and waits ~9s for an answer, so this needs a much
    tighter poll interval than the general 30s loop."""
    data = await api_get("/api/v1/bot/actions?action_type=check_channel_category")
    if not data:
        return

    for action in data:
        action_id = action["id"]
        payload = action.get("payload", {})
        channel_id_raw = payload.get("channel_id")
        category_id_raw = payload.get("category_id")

        try:
            channel_id = int(channel_id_raw)
        except (TypeError, ValueError):
            await api_post(f"/api/v1/bot/actions/{action_id}/ack", {
                "success": False, "error_message": "Некорректный ID канала",
            })
            continue

        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception as e:
                await api_post(f"/api/v1/bot/actions/{action_id}/ack", {
                    "success": False, "error_message": f"Канал не найден: {e}",
                })
                continue

        channel_category_id = str(getattr(channel, "category_id", None) or "")
        belongs = bool(category_id_raw) and channel_category_id == str(category_id_raw)
        await api_post(f"/api/v1/bot/actions/{action_id}/ack", {
            "success": True,
            "result": {
                "belongs_to_category": belongs,
                "channel_name": getattr(channel, "name", None),
            },
        })


@tasks.loop(seconds=3)
async def poll_role_sync_actions_task():
    """Dedicated fast poll for "sync_department_role" — the site is meant to
    be the source of truth for department roles applied "instantly", so this
    can't sit on the general 30s loop. Separate from poll_fast_actions_task
    (which has its own ~2s cadence reserved for the synchronous
    check_channel_category request/response contract) to keep the two
    unrelated concerns from racing each other."""
    data = await api_get("/api/v1/bot/actions?action_type=sync_department_role")
    if not data:
        return

    for action in data:
        action_id = action["id"]
        payload = action.get("payload", {})
        success, error_message = await execute_bot_action("sync_department_role", payload)
        await api_post(f"/api/v1/bot/actions/{action_id}/ack", {
            "success": success,
            "error_message": None if success else (error_message or "Execution failed"),
        })


@tasks.loop(hours=1)
async def publish_rating_task():
    """Publish/update rating embeds in the rating channel every hour."""
    global RATING_CHANNEL_ID

    # Reload channel from NexusData config (in case it was changed via !setrating)
    config = await api_get("/api/v1/bot/config")
    if config and config.get("rating_channel_id"):
        try:
            RATING_CHANNEL_ID = int(config["rating_channel_id"])
        except (ValueError, TypeError):
            pass

    if not RATING_CHANNEL_ID:
        return

    channel = bot.get_channel(RATING_CHANNEL_ID)
    if not channel:
        return

    roles = ["captain", "mechanic", "medic", "security", "engineer"]
    role_names = {
        "captain": "Капитан",
        "mechanic": "Механик",
        "medic": "Врач",
        "security": "СБ",
        "engineer": "Инженер",
    }
    role_emojis = {
        "captain": "<:captain:1464322437788209365>",
        "mechanic": "<:Mechanic:1471245157574901884>",
        "medic": "<:Medical_Doctor:1471245181151084646>",
        "security": "<:Security_Officer:1471245216303419604>",
        "engineer": "<:Engineer:1471244951160356927>",
    }

    for role in roles:
        data = await api_get(f"/api/v1/bot/ratings/leaderboard?role={role}&limit=10")
        if not data or not data.get("leaderboard"):
            await asyncio.sleep(15)
            continue

        lb = data["leaderboard"]
        lines = []
        for entry in lb:
            if entry["rank"] == 1:
                medal = "🥇"
            elif entry["rank"] == 2:
                medal = "🥈"
            elif entry["rank"] == 3:
                medal = "🥉"
            else:
                medal = f"**{entry['rank']}.**"

            # Live @mention so it always resolves to the player's current
            # Discord identity; falls back to the stored game nickname for
            # players with no confirmed Discord link.
            name = f"<@{entry['discord_id']}>" if entry.get("discord_id") else entry["player_name"]

            lines.append(f"{medal}  {name}  -  **{entry['rating']}**")

        description = "\n\n".join(lines) if lines else "Нет данных"
        emoji = role_emojis.get(role, "")
        title = f"{emoji} Рейтинг по роли {role_names.get(role, role)}"

        embed = discord.Embed(
            title=title,
            description=description,
            color=0x1a68de,
        )
        embed.set_footer(text=f"Сделано за сухарики | Обновлено {format_msk(datetime.utcnow())} МСК")

        # Edit existing message or send new
        msg_id = _rating_message_ids.get(role)
        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(embed=embed)
                continue
            except (discord.NotFound, discord.HTTPException):
                pass

        # Send new message
        try:
            msg = await channel.send(embed=embed)
            _rating_message_ids[role] = msg.id
            _save_rating_ids(_rating_message_ids)
        except Exception as e:
            logger.error(f"Failed to send rating embed for {role}: {e}")

        # Wait 15 seconds between messages to avoid rate limits
        await asyncio.sleep(15)


# Department rating message IDs (persisted to their own JSON file, same
# pattern as _rating_message_ids above but keyed by department_id).
DEPARTMENT_RATING_IDS_FILE = "department_rating_message_ids.json"


def _load_department_rating_ids() -> dict:
    try:
        if os.path.exists(DEPARTMENT_RATING_IDS_FILE):
            with open(DEPARTMENT_RATING_IDS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_department_rating_ids(ids: dict):
    try:
        with open(DEPARTMENT_RATING_IDS_FILE, "w") as f:
            json.dump(ids, f)
    except Exception as e:
        logger.warning(f"Failed to save department rating IDs: {e}")


_department_rating_message_ids = _load_department_rating_ids()


@tasks.loop(hours=1)
async def publish_department_ratings_task():
    """Publish/update each department's internal rating leaderboard embed,
    in its own configured channel, every hour. Mirrors publish_rating_task
    above (including the 15s stagger between embeds) — a department with no
    rating_channel_id configured is simply skipped."""
    depts_resp = await api_get("/api/v1/bot/department-ratings/channels")
    if not depts_resp or not depts_resp.get("departments"):
        return

    for dept in depts_resp["departments"]:
        department_id = dept["department_id"]
        channel_id = dept.get("rating_channel_id")
        if not channel_id:
            continue
        channel = bot.get_channel(int(channel_id))
        if not channel:
            await asyncio.sleep(15)
            continue

        data = await api_get(f"/api/v1/bot/department-ratings/leaderboard?department_id={department_id}&limit=15")
        if not data:
            await asyncio.sleep(15)
            continue

        lb = data.get("leaderboard") or []
        lines = []
        for i, entry in enumerate(lb, 1):
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"**{i}.**"
            name = f"<@{entry['discord_id']}>" if entry.get("discord_id") else (entry.get("admin_name") or "?")
            lines.append(f"{medal}  {name}  -  **{entry['total']}**")

        embed = discord.Embed(
            title=f"{dept.get('icon') or ''} Рейтинг отдела «{dept['name']}»".strip(),
            description="\n\n".join(lines) if lines else "Нет данных",
            color=0x1a68de,
        )
        embed.set_footer(text=f"Обновлено {format_msk(datetime.utcnow())} МСК")

        key = str(department_id)
        msg_id = _department_rating_message_ids.get(key)
        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(embed=embed)
            except (discord.NotFound, discord.HTTPException):
                msg_id = None
        if not msg_id:
            try:
                msg = await channel.send(embed=embed)
                _department_rating_message_ids[key] = msg.id
                _save_department_rating_ids(_department_rating_message_ids)
            except Exception as e:
                logger.error(f"Failed to send department rating embed for department {department_id}: {e}")

        # Wait 15 seconds between departments to avoid rate limits — same
        # precaution as publish_rating_task, whether we edited or sent.
        await asyncio.sleep(15)


# Ticket status message ID (persisted)
_ticket_status_msg_id = None


async def refresh_ticket_status():
    """Update ticket status embed. Called on ticket create/message/close."""
    global _ticket_status_msg_id

    config = await api_get("/api/v1/bot/config")
    if not config:
        return
    status_channel_id = config.get("ticket_status_channel_id")
    if not status_channel_id:
        return

    try:
        channel = bot.get_channel(int(status_channel_id))
    except (ValueError, TypeError):
        return
    if not channel:
        return

    tickets = await api_get("/api/v1/tickets/open")
    if tickets is None:
        return

    total = len(tickets)
    awaiting = sum(1 for t in tickets if t.get("awaiting_admin"))

    lines = []
    for t in tickets[:15]:
        status_icon = "🟡" if t.get("awaiting_admin") else "🟢"
        channel_link = f"<#{t['discord_channel_id']}>" if t.get("discord_channel_id") else "#?"
        lines.append(f"{status_icon} **#{t['id']}** {t['title'][:40]} - {channel_link}")

    embed = discord.Embed(
        title="📊 Статус обращений",
        description="\n".join(lines) if lines else "Нет открытых обращений",
        color=0x1a68de,
    )
    embed.add_field(name="Всего открытых", value=str(total), inline=True)
    embed.add_field(name="Ожидают ответа", value=str(awaiting), inline=True)
    embed.set_footer(text=f"Сделано за сухарики | Обновлено {format_msk(datetime.utcnow())} МСК")

    if _ticket_status_msg_id:
        try:
            msg = await channel.fetch_message(_ticket_status_msg_id)
            await msg.edit(embed=embed)
            return
        except (discord.NotFound, discord.HTTPException):
            _ticket_status_msg_id = None

    try:
        msg = await channel.send(embed=embed)
        _ticket_status_msg_id = msg.id
    except Exception as e:
        logger.error(f"Failed to send ticket status: {e}")


# Round status message IDs (persisted to JSON file, keyed by server name)
ROUND_STATUS_IDS_FILE = "round_status_message_ids.json"
ROUND_STATUS_SERVERS = ["ASPER1", "ASPER2"]


def _load_round_status_ids() -> dict:
    try:
        if os.path.exists(ROUND_STATUS_IDS_FILE):
            with open(ROUND_STATUS_IDS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_round_status_ids(ids: dict):
    try:
        with open(ROUND_STATUS_IDS_FILE, "w") as f:
            json.dump(ids, f)
    except Exception as e:
        logger.warning(f"Failed to save round status message IDs: {e}")


_round_status_message_ids = _load_round_status_ids()


def _format_shuttle_eta(shuttle_arrival_at: str) -> str:
    """shuttle_arrival_at is an ISO8601 UTC timestamp (suffixed 'Z') or None."""
    if not shuttle_arrival_at:
        return "-"
    try:
        arrival = datetime.fromisoformat(shuttle_arrival_at[:-1] if shuttle_arrival_at.endswith("Z") else shuttle_arrival_at)
    except ValueError:
        return "-"
    remaining = (arrival - datetime.utcnow()).total_seconds()
    arrival_msk = f"{format_msk_time(arrival)} МСК"
    if remaining <= 0:
        return f"В секторе... ({arrival_msk})"
    minutes, seconds = divmod(int(remaining), 60)
    countdown = f"через {minutes} мин {seconds:02d} сек" if minutes else f"через {seconds} сек"
    return f"{countdown} ({arrival_msk})"


async def refresh_round_status_embed(server_name: str):
    """Update (or send) the live round-status embed for one server.

    Called every minute by round_status_task, and immediately on round
    start/end via the 'refresh_round_status' bot action.
    """
    config = await api_get("/api/v1/bot/config")
    if not config:
        return
    channel_key = f"round_status_channel_{server_name.lower()}_id"
    channel_id = config.get(channel_key)
    if not channel_id:
        return

    try:
        channel = bot.get_channel(int(channel_id))
    except (ValueError, TypeError):
        return
    if not channel:
        return

    status = await api_get(f"/api/v1/bot/round-status?server_name={server_name}")
    if status is None:
        return

    if status.get("active"):
        title = f"🟢 {server_name} - идёт раунд"
        color = 0x22d06f
    else:
        title = f"⚪ {server_name} - раунд не идёт"
        color = 0x858a94

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="📍 Локация", value=status.get("location") or "-", inline=True)
    embed.add_field(name="🚢 Корабль", value=status.get("submarine_name") or "-", inline=True)
    embed.add_field(name="🎯 Миссия", value=status.get("mission") or "-", inline=False)
    embed.add_field(name="👥 Кол-во экипажа", value=str(status.get("player_count", 0)), inline=True)
    embed.add_field(name="🚀 Челнок снабжения", value=_format_shuttle_eta(status.get("shuttle_arrival_at")), inline=True)
    embed.set_footer(text=f"Сделано за сухарики | Обновлено {format_msk(datetime.utcnow())} МСК")

    msg_id = _round_status_message_ids.get(server_name)
    if msg_id:
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(embed=embed)
            return
        except (discord.NotFound, discord.HTTPException):
            pass

    try:
        msg = await channel.send(embed=embed)
        _round_status_message_ids[server_name] = msg.id
        _save_round_status_ids(_round_status_message_ids)
    except Exception as e:
        logger.error(f"Failed to send round status embed for {server_name}: {e}")


@tasks.loop(minutes=1)
async def round_status_task():
    """Refresh the round-status embed for every configured server every minute."""
    for server_name in ROUND_STATUS_SERVERS:
        await refresh_round_status_embed(server_name)


# ═══════════════════════════════════════════════════════════
# BOT ACTION EXECUTOR
# ═══════════════════════════════════════════════════════════

async def execute_bot_action(action_type: str, payload: dict) -> Tuple[bool, Optional[str]]:
    """Execute a bot action from NexusData queue. Returns (success, error_message) —
    error_message is stored on DiscordBotAction.error_message so failures are
    visible in the admin panel instead of only in the bot's own logs (see
    sync_department_role below, which used to swallow every exception and
    unconditionally report success — a role that failed to apply due to a
    permission/hierarchy issue looked identical to one that worked)."""
    try:
        if action_type == "send_message":
            channel_id = int(payload.get("channel_id", 0))
            content = payload.get("content", "")
            channel = bot.get_channel(channel_id)
            if channel:
                await channel.send(content[:2000])
                return True, None

        elif action_type == "refresh_round_status":
            server_name = payload.get("server_name")
            if server_name:
                await refresh_round_status_embed(server_name)
            return True, None

        elif action_type == "send_embed":
            # Supports both direct channel_id and channel_config_key (bot config lookup)
            channel_id_raw = payload.get("channel_id", 0)
            channel_config_key = payload.get("channel_config_key")

            if channel_config_key:
                config = await api_get("/api/v1/bot/config")
                if config and config.get(channel_config_key):
                    try:
                        channel_id_raw = int(config[channel_config_key])
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid channel_id in config key {channel_config_key}")
                        return True, None  # Don't fail - channel not configured

            if not channel_id_raw:
                logger.warning(f"send_embed: no channel_id resolved (config_key={channel_config_key})")
                return True, None  # Channel not configured — skip silently

            channel_id = int(channel_id_raw)
            embed_data = payload.get("embed", {})
            channel = bot.get_channel(channel_id)
            if channel:
                embed = discord.Embed.from_dict(embed_data)
                await channel.send(embed=embed)
                return True, None
            logger.warning(f"send_embed: channel {channel_id} not found")
            return True, None  # Channel might not be in cache yet

        elif action_type == "update_rank_role":
            # Update Discord roles when player rank changes
            guild = bot.get_guild(GUILD_ID)
            if not guild:
                return False, "Guild not found (bot not in cache for GUILD_ID)"
            discord_id = payload.get("discord_id")
            remove_role_id = payload.get("remove_role_id")
            add_role_id = payload.get("add_role_id")
            if not discord_id:
                return True, None

            member = guild.get_member(int(discord_id))
            if not member:
                try:
                    member = await guild.fetch_member(int(discord_id))
                except Exception:
                    logger.warning(f"Member {discord_id} not found in guild")
                    return True, None  # Don't fail - member might have left

            errors = []
            if remove_role_id:
                old_role = guild.get_role(int(remove_role_id))
                if old_role and old_role in member.roles:
                    try:
                        await member.remove_roles(old_role, reason=f"Rank changed: {payload.get('old_rank')} -> {payload.get('new_rank')}")
                    except Exception as e:
                        logger.warning(f"Failed to remove role {remove_role_id}: {e}")
                        errors.append(f"remove {remove_role_id}: {e}")
                elif not old_role:
                    errors.append(f"remove role {remove_role_id} not found on guild")

            if add_role_id:
                new_role = guild.get_role(int(add_role_id))
                if new_role and new_role not in member.roles:
                    try:
                        await member.add_roles(new_role, reason=f"Rank changed: {payload.get('old_rank')} -> {payload.get('new_rank')}")
                    except Exception as e:
                        logger.warning(f"Failed to add role {add_role_id}: {e}")
                        errors.append(f"add {add_role_id}: {e}")
                elif not new_role:
                    errors.append(f"add role {add_role_id} not found on guild")

            logger.info(f"Rank role updated for {member}: -{remove_role_id} +{add_role_id}")
            return (False, "; ".join(errors)) if errors else (True, None)

        elif action_type == "sync_department_role":
            # Site -> Discord half of department membership sync (item 4) —
            # queued by routes/departments.py's member add/update/remove
            # endpoints when held access levels (or department membership
            # itself) change. The other direction (Discord role -> site
            # membership) is workers.task_sync_department_roles.
            guild = bot.get_guild(GUILD_ID)
            if not guild:
                return False, "Guild not found (bot not in cache for GUILD_ID)"
            discord_id = payload.get("discord_id")
            add_role_id = payload.get("add_role_id")
            remove_role_id = payload.get("remove_role_id")
            if not discord_id:
                return True, None

            member = guild.get_member(int(discord_id))
            if not member:
                try:
                    member = await guild.fetch_member(int(discord_id))
                except Exception:
                    logger.warning(f"Member {discord_id} not found in guild")
                    return True, None  # Don't fail - member might have left

            # Real failures here are almost always a Discord-side setup
            # problem (bot's own role sits below the target role in the
            # hierarchy, or the bot lacks Manage Roles) — surfaced honestly
            # instead of the old "always report success" behavior so it's
            # visible on /discord-bot instead of only in bot logs.
            errors = []
            if remove_role_id:
                old_role = guild.get_role(int(remove_role_id))
                if old_role and old_role in member.roles:
                    try:
                        await member.remove_roles(old_role, reason="Department membership sync")
                    except Exception as e:
                        logger.warning(f"Failed to remove role {remove_role_id}: {e}")
                        errors.append(f"remove {remove_role_id}: {e}")
                elif not old_role:
                    errors.append(f"remove role {remove_role_id} not found on guild")

            if add_role_id:
                new_role = guild.get_role(int(add_role_id))
                if new_role and new_role not in member.roles:
                    try:
                        await member.add_roles(new_role, reason="Department membership sync")
                    except Exception as e:
                        logger.warning(f"Failed to add role {add_role_id}: {e}")
                        errors.append(f"add {add_role_id}: {e}")
                elif not new_role:
                    errors.append(f"add role {add_role_id} not found on guild")

            logger.info(f"Department role synced for {member}: -{remove_role_id} +{add_role_id}")
            return (False, "; ".join(errors)) if errors else (True, None)

        elif action_type == "trap_triggered":
            # Send trap notification to traps channel
            config = await api_get("/api/v1/bot/config")
            trap_channel_id = 0
            if config and config.get("trap_channel_id"):
                try:
                    trap_channel_id = int(config["trap_channel_id"])
                except (ValueError, TypeError):
                    pass

            if not trap_channel_id:
                logger.warning("Trap triggered but no trap_channel_id configured")
                return True, None  # Don't fail - just no channel set

            channel = bot.get_channel(trap_channel_id)
            if not channel:
                logger.warning(f"Trap channel {trap_channel_id} not found")
                return True, None

            embed = discord.Embed(
                title="🪤 Ловушка сработала!",
                color=0xff4444,
            )
            embed.add_field(name="Игрок", value=payload.get("player_name", "-"), inline=True)
            embed.add_field(name="SteamID", value=f"`{payload.get('steamid', '-')}`", inline=True)
            embed.add_field(name="Player ID", value=str(payload.get("player_id", "-")), inline=True)
            embed.add_field(name="Причина", value=payload.get("reason", "-"), inline=False)
            embed.set_footer(text=f"NexusData Trap System | {format_msk(datetime.utcnow())} МСК")

            await channel.send(content="<@&1506729102713950288>", embed=embed)
            return True, None

        elif action_type == "kick_member":
            guild = bot.get_guild(GUILD_ID)
            member_id = int(payload.get("discord_id", 0))
            reason = payload.get("reason", "NexusData action")
            if guild:
                member = guild.get_member(member_id)
                if member:
                    await member.kick(reason=reason)
                    return True, None
                return False, f"Member {member_id} not found in guild"
            return False, "Guild not found (bot not in cache for GUILD_ID)"

        elif action_type == "ban_member":
            guild = bot.get_guild(GUILD_ID)
            member_id = int(payload.get("discord_id", 0))
            reason = payload.get("reason", "NexusData action")
            if guild:
                member = guild.get_member(member_id)
                if member:
                    await member.ban(reason=reason)
                    return True, None
                return False, f"Member {member_id} not found in guild"
            return False, "Guild not found (bot not in cache for GUILD_ID)"

        elif action_type == "ticket_send_message":
            channel_id = int(payload.get("channel_id", 0))
            content = payload.get("content", "")
            channel = bot.get_channel(channel_id)
            if channel and content:
                await channel.send(content[:2000])
                return True, None

        elif action_type == "ticket_close_channel":
            channel_id = int(payload.get("channel_id", 0))
            channel = bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.send("🔒 Обращение закрыто. Канал будет удалён через 5 секунд.")
                    await asyncio.sleep(5)
                    await channel.delete(reason="Ticket closed")
                except Exception as e:
                    logger.warning(f"Failed to delete ticket channel: {e}")
                return True, None

        elif action_type == "send_sub_project_embed":
            # Send a new sub-project embed to the configured channel
            channel_id = int(payload.get("channel_id", 0))
            embed_data = payload.get("embed", {})
            project_id = payload.get("project_id")
            channel = bot.get_channel(channel_id)
            if channel:
                embed = discord.Embed.from_dict(embed_data)
                msg = await channel.send(embed=embed)
                # Report message_id back to NexusData
                if project_id:
                    await api_post(f"/api/v1/bot/sub-project/{project_id}/set-message-id", {
                        "message_id": str(msg.id)
                    })
                return True, None

        elif action_type == "send_document_embed":
            # Send a new document embed (item 9 — generalized document
            # Discord publish, any department document/folder, not just
            # orders/news). Mirrors send_sub_project_embed above.
            channel_id = int(payload.get("channel_id", 0))
            embed_data = payload.get("embed", {})
            document_id = payload.get("document_id")
            channel = bot.get_channel(channel_id)
            if channel:
                embed = discord.Embed.from_dict(embed_data)
                msg = await channel.send(embed=embed)
                if document_id:
                    await api_post(f"/api/v1/bot/document/{document_id}/set-message-id", {
                        "message_id": str(msg.id),
                        "channel_id": str(channel_id),
                    })
                return True, None

        elif action_type == "edit_embed":
            # Edit an existing embed message
            channel_id = int(payload.get("channel_id", 0))
            message_id = payload.get("message_id")
            embed_data = payload.get("embed", {})
            channel = bot.get_channel(channel_id)
            if channel and message_id:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    embed = discord.Embed.from_dict(embed_data)
                    await msg.edit(embed=embed)
                    return True, None
                except (discord.NotFound, discord.HTTPException) as e:
                    logger.warning(f"Failed to edit embed: {e}")
                    return False, str(e)

        elif action_type == "delete_message":
            # Delete a message from a channel
            channel_id = int(payload.get("channel_id", 0))
            message_id = payload.get("message_id")
            channel = bot.get_channel(channel_id)
            if channel and message_id:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    await msg.delete()
                    return True, None
                except (discord.NotFound, discord.HTTPException):
                    return True, None  # Already gone

        logger.warning(f"Unknown or failed bot action: {action_type}")
        return False, f"Unknown action type or unmet precondition: {action_type}"

    except Exception as e:
        logger.error(f"Bot action {action_type} failed: {e}")
        return False, str(e)


# ═══════════════════════════════════════════════════════════
# ADMIN COMMANDS
# ═══════════════════════════════════════════════════════════

@bot.command(name="setforward")
@commands.has_permissions(administrator=True)
async def cmd_set_forward(ctx, channel: discord.TextChannel):
    """!setforward #channel - Set message forwarding channel."""
    global FORWARD_CHANNEL_ID
    FORWARD_CHANNEL_ID = channel.id
    await api_post("/api/v1/bot/config", {"key": "forward_channel_id", "value": str(channel.id)})
    await ctx.send(f"✅ Пересылка сообщений настроена в {channel.mention}")


@bot.command(name="setrating")
@commands.has_permissions(administrator=True)
async def cmd_set_rating(ctx, channel: discord.TextChannel):
    """!setrating #channel - Set rating publication channel."""
    global RATING_CHANNEL_ID
    RATING_CHANNEL_ID = channel.id
    await api_post("/api/v1/bot/config", {"key": "rating_channel_id", "value": str(channel.id)})
    await ctx.send(f"✅ Рейтинг будет публиковаться в {channel.mention}")


@bot.command(name="settraps")
@commands.has_permissions(administrator=True)
async def cmd_set_traps(ctx, channel: discord.TextChannel):
    """!settraps #channel - Set trap notifications channel."""
    await api_post("/api/v1/bot/config", {"key": "trap_channel_id", "value": str(channel.id)})
    await ctx.send(f"✅ Уведомления о ловушках будут приходить в {channel.mention}")


@bot.command(name="setsubprojects")
@commands.has_permissions(administrator=True)
async def cmd_set_sub_projects(ctx, channel: discord.TextChannel):
    """!setsubprojects #channel - Set sub-projects publication channel."""
    await api_post("/api/v1/bot/config", {"key": "sub_projects_channel_id", "value": str(channel.id)})
    await ctx.send(f"✅ Корабли будут публиковаться в {channel.mention}")


@bot.command(name="setpunishments")
@commands.has_permissions(administrator=True)
async def cmd_set_punishments(ctx, channel: discord.TextChannel):
    """!setpunishments #channel - Set punishments notification channel (all types)."""
    await api_post("/api/v1/bot/config", {"key": "punishment_channel_id", "value": str(channel.id)})
    await ctx.send(f"✅ Все наказания будут отправляться в {channel.mention}")


@bot.command(name="setbans")
@commands.has_permissions(administrator=True)
async def cmd_set_bans(ctx, channel: discord.TextChannel):
    """!setbans #channel - Set bans-only notification channel."""
    await api_post("/api/v1/bot/config", {"key": "ban_channel_id", "value": str(channel.id)})
    await ctx.send(f"✅ Уведомления о банах будут приходить в {channel.mention}")


@bot.command(name="setdispatch1")
@commands.has_permissions(administrator=True)
async def cmd_set_dispatch1(ctx, channel: discord.TextChannel):
    """!setdispatch1 #channel - Set dispatch channel for ASPER1."""
    await api_post("/api/v1/bot/config", {"key": "dispatch_channel_asper1_id", "value": str(channel.id)})
    await ctx.send(f"✅ Диспетчерская ASPER1 будет в {channel.mention}")


@bot.command(name="setdispatch2")
@commands.has_permissions(administrator=True)
async def cmd_set_dispatch2(ctx, channel: discord.TextChannel):
    """!setdispatch2 #channel - Set dispatch channel for ASPER2."""
    await api_post("/api/v1/bot/config", {"key": "dispatch_channel_asper2_id", "value": str(channel.id)})
    await ctx.send(f"✅ Диспетчерская ASPER2 будет в {channel.mention}")


@bot.command(name="setroundstatus1")
@commands.has_permissions(administrator=True)
async def cmd_set_round_status1(ctx, channel: discord.TextChannel):
    """!setroundstatus1 #channel - Set live round-status channel for ASPER1."""
    await api_post("/api/v1/bot/config", {"key": "round_status_channel_asper1_id", "value": str(channel.id)})
    await ctx.send(f"✅ Статус раунда ASPER1 будет в {channel.mention}")
    await refresh_round_status_embed("ASPER1")


@bot.command(name="setroundstatus2")
@commands.has_permissions(administrator=True)
async def cmd_set_round_status2(ctx, channel: discord.TextChannel):
    """!setroundstatus2 #channel - Set live round-status channel for ASPER2."""
    await api_post("/api/v1/bot/config", {"key": "round_status_channel_asper2_id", "value": str(channel.id)})
    await ctx.send(f"✅ Статус раунда ASPER2 будет в {channel.mention}")
    await refresh_round_status_embed("ASPER2")


@bot.command(name="publishrating")
@commands.has_permissions(administrator=True)
async def cmd_publish_rating(ctx):
    """!publishrating - Force publish rating now."""
    await publish_rating_task()
    await ctx.send("✅ Рейтинг опубликован")


@bot.command(name="syncmembers")
@commands.has_permissions(administrator=True)
async def cmd_sync_members(ctx):
    """!syncmembers - Force sync members now."""
    await sync_members_task()
    await ctx.send("✅ Участники синхронизированы")


# ═══════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════
# (main block moved to end of file)
# ═══════════════════════════════════════════════════════════
# TICKET SYSTEM
# ═══════════════════════════════════════════════════════════

TICKET_TYPES = {
    "help": {"name": "Помощь с игрой", "emoji": "❓", "color": 0x2b7af0},
    "player_report": {"name": "Жалоба на игрока", "emoji": "⚠️", "color": 0xff8800},
    "bug": {"name": "Сообщить о баге", "emoji": "🐛", "color": 0x00cc88},
    "admin_report": {"name": "Жалоба на администрацию", "emoji": "🛡️", "color": 0xcc0044},
    "department_report": {"name": "Жалоба на работу отделов", "emoji": "📋", "color": 0x8844cc},
    "appeal": {"name": "Обжалование наказания", "emoji": "⚖️", "color": 0xffcc00},
    "construction_bureau": {"name": "Обращение в конструкторское бюро", "emoji": "🔧", "color": 0x4a9eff},
}

# pending_close[channel_id] = asyncio.Task — задача закрытия тикета
_pending_close: dict = {}


async def _create_ticket_channel(interaction: discord.Interaction, ticket_type: str):
    """Create a ticket channel directly (no modal). Called from button handlers."""
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    user = interaction.user
    is_admin = user.guild_permissions.administrator if hasattr(user, 'guild_permissions') else False

    # Check: already has open ticket in this category
    open_tickets = await api_get("/api/v1/tickets/open")
    if open_tickets:
        for t in open_tickets:
            if t.get("creator_discord_id") == str(user.id) and t.get("type") == ticket_type:
                await interaction.followup.send(
                    "❌ У вас уже есть открытое обращение в этой категории.",
                    ephemeral=True
                )
                return

        # 5-minute cooldown (non-admins only)
        if not is_admin:
            for t in open_tickets:
                if t.get("creator_discord_id") == str(user.id) and t.get("created_at"):
                    try:
                        created = datetime.fromisoformat(t["created_at"])
                        if datetime.utcnow() - created < timedelta(minutes=5):
                            await interaction.followup.send(
                                "❌ Подождите 5 минут перед созданием нового обращения.",
                                ephemeral=True
                            )
                            return
                    except (ValueError, TypeError):
                        pass

    config = await api_get("/api/v1/tickets/config")
    bot_config = await api_get("/api/v1/bot/config")
    if not config:
        await interaction.followup.send("Ошибка загрузки конфигурации", ephemeral=True)
        return

    category_id = (bot_config or {}).get("ticket_category_id") or config.get("category_id")
    type_config = config.get("types", {}).get(ticket_type, {})
    ping_roles = type_config.get("ping_roles", [])
    template = type_config.get("template", "Опишите вашу проблему подробно.")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for role_id in ping_roles:
        try:
            role = guild.get_role(int(role_id))
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        except (ValueError, TypeError):
            pass

    category = guild.get_channel(int(category_id)) if category_id else None
    channel_name = f"ticket-{user.name[:10]}-{ticket_type[:8]}"

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Тикет | {TICKET_TYPES.get(ticket_type, {}).get('name', ticket_type)} | {user.display_name}",
        )
    except Exception as e:
        await interaction.followup.send(f"Ошибка создания канала: {e}", ephemeral=True)
        return

    # Use display_name as the title
    title = f"{TICKET_TYPES.get(ticket_type, {}).get('name', ticket_type)}"

    result = await api_post("/api/v1/tickets/create", {
        "ticket_type": ticket_type,
        "title": title,
        "creator_discord_id": str(user.id),
        "creator_username": str(user),
        "creator_display_name": user.display_name,
        "discord_channel_id": str(channel.id),
    })

    ticket_id = result.get("ticket_id", "?") if result else "?"

    tinfo = TICKET_TYPES.get(ticket_type, {})
    silent_pings = " ".join(f"||<@&{rid}>||" for rid in ping_roles if rid)

    embed = discord.Embed(
        title=f"{tinfo.get('emoji', '🎫')} {tinfo.get('name', 'Обращение')} #{ticket_id}",
        description=(
            f"{user.mention}, опишите вашу ситуацию по шаблону:\n\n"
            f"```\n{template}\n```\n\n"
            "Нажмите **Закрыть обращение** если создали ошибочно."
        ),
        color=tinfo.get("color", 0x2b7af0),
    )
    embed.set_footer(text=f"Тикет #{ticket_id}")

    view = TicketCloseView(ticket_id=ticket_id, channel=channel)
    msg_content = f"{user.mention} {silent_pings}" if silent_pings.strip() else user.mention
    await channel.send(content=msg_content, embed=embed, view=view)

    await interaction.followup.send(f"✅ Обращение создано: {channel.mention}", ephemeral=True)
    await refresh_ticket_status()


class TicketCloseView(discord.ui.View):
    def __init__(self, ticket_id=None, channel=None):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.channel = channel

    @discord.ui.button(label="Закрыть обращение", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_btn")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        ticket_id = self.ticket_id

        # Never delete the channel unless the site-side close actually
        # succeeded — api_post silently returns None on any HTTP/network
        # failure, and blindly deleting anyway is exactly what orphaned a
        # site-side Ticket row stuck open forever with no channel left to
        # act on ("phantom tickets"). One retry, then bail out and leave the
        # channel intact — a stuck-open channel is recoverable, a deleted
        # one with a stuck-open DB row is not.
        result = await api_post(f"/api/v1/tickets/{ticket_id}/close", {"closed_by": interaction.user.display_name}) if ticket_id else None
        if ticket_id and result is None:
            result = await api_post(f"/api/v1/tickets/{ticket_id}/close", {"closed_by": interaction.user.display_name})

        if not ticket_id or result is None:
            await interaction.followup.send(
                "⚠️ Не удалось закрыть обращение на сайте — канал НЕ удалён, попробуйте ещё раз позже.",
                ephemeral=True,
            )
            return

        await interaction.followup.send("🔒 Тикет закрывается...", ephemeral=True)
        await refresh_ticket_status()
        await asyncio.sleep(3)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception as e:
            logger.warning(f"Failed to delete ticket channel: {e}")


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Помощь с игрой", style=discord.ButtonStyle.primary, emoji="❓", custom_id="ticket_help")
    async def btn_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _create_ticket_channel(interaction, "help")

    @discord.ui.button(label="Жалоба на игрока", style=discord.ButtonStyle.secondary, emoji="⚠️", custom_id="ticket_player_report")
    async def btn_player_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _create_ticket_channel(interaction, "player_report")

    @discord.ui.button(label="Сообщить о баге", style=discord.ButtonStyle.success, emoji="🐛", custom_id="ticket_bug")
    async def btn_bug(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _create_ticket_channel(interaction, "bug")

    @discord.ui.button(label="Жалоба на администрацию", style=discord.ButtonStyle.danger, emoji="🛡️", custom_id="ticket_admin_report")
    async def btn_admin_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _create_ticket_channel(interaction, "admin_report")

    @discord.ui.button(label="Жалоба на отделы", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="ticket_department_report")
    async def btn_department_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _create_ticket_channel(interaction, "department_report")

    @discord.ui.button(label="Обжалование наказания", style=discord.ButtonStyle.secondary, emoji="⚖️", custom_id="ticket_appeal")
    async def btn_appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _create_ticket_channel(interaction, "appeal")

    @discord.ui.button(label="Конструкторское бюро", style=discord.ButtonStyle.secondary, emoji="🔧", custom_id="ticket_construction_bureau")
    async def btn_construction_bureau(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _create_ticket_channel(interaction, "construction_bureau")


@bot.command(name="setuptickets")
@commands.has_permissions(administrator=True)
async def cmd_setup_tickets(ctx):
    """!setuptickets - Send the ticket panel message with buttons."""
    embed = discord.Embed(
        title="🎫 Система обращений",
        description=(
            "Если у вас возникла проблема или вопрос, создайте обращение нажав на соответствующую кнопку ниже.\n\n"
            "**Типы обращений:**\n"
            "❓ **Помощь с игрой** - вопросы по механикам, настройкам\n"
            "⚠️ **Жалоба на игрока** - нарушения правил другими игроками\n"
            "🐛 **Сообщить о баге** - технические проблемы\n"
            "🛡️ **Жалоба на администрацию** - некорректные действия администраторов\n"
            "📋 **Жалоба на работу отделов** - проблемы с работой отделов\n"
            "⚖️ **Обжалование наказания** - если считаете наказание несправедливым\n"
            "🔧 **Конструкторское бюро** - жалобы и предложения по кораблям/станциям флота\n\n"
            "После создания обращения вам будет создан приватный канал."
        ),
        color=0x1a68de,
    )
    embed.set_footer(text="Сделано за сухарики")

    view = TicketPanelView()
    await ctx.send(embed=embed, view=view)
    await ctx.message.delete()


@bot.tree.command(name="close", description="Закрыть тикет через указанное время (только для администраторов)")
@app_commands.describe(
    minutes="Через сколько минут закрыть (0 = через 15 секунд)",
    answer="Итоговый ответ по обращению"
)
async def slash_close_ticket(interaction: discord.Interaction, minutes: int, answer: str):
    """Slash command /close <minutes> <answer> — only for ping roles in ticket channels."""
    channel = interaction.channel

    # Must be a ticket channel
    if not channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ Эта команда доступна только в тикет-каналах.", ephemeral=True)
        return

    # Check permissions: user must have admin or be one of the ping roles for this ticket type
    user = interaction.user
    has_perm = user.guild_permissions.administrator if hasattr(user, 'guild_permissions') else False

    if not has_perm:
        # Load ticket config ping roles and check if user has any of them
        config = await api_get("/api/v1/tickets/config")
        if config:
            for ttype, tconf in config.get("types", {}).items():
                for role_id_str in tconf.get("ping_roles", []):
                    try:
                        role = interaction.guild.get_role(int(role_id_str))
                        if role and role in user.roles:
                            has_perm = True
                            break
                    except (ValueError, TypeError):
                        pass
                if has_perm:
                    break

    if not has_perm:
        await interaction.response.send_message("❌ У вас нет прав для закрытия тикета.", ephemeral=True)
        return

    channel_id_str = str(channel.id)

    # If there's already a pending close — cancel it (toggle behaviour)
    if channel_id_str in _pending_close:
        task = _pending_close.pop(channel_id_str)
        if not task.done():
            task.cancel()
        await interaction.response.send_message(
            "⏸️ Закрытие тикета приостановлено.",
            ephemeral=False
        )
        return

    # Determine delay
    delay_seconds = 15 if minutes == 0 else minutes * 60
    if minutes == 0:
        time_str = "15 секунд"
    elif minutes == 1:
        time_str = "1 минуту"
    elif 2 <= minutes <= 4:
        time_str = f"{minutes} минуты"
    else:
        time_str = f"{minutes} минут"

    # Find ticket_id by channel
    open_tickets = await api_get("/api/v1/tickets/open")
    ticket_id = None
    ticket_title = "Обращение"
    creator_discord_id = None
    if open_tickets:
        for t in open_tickets:
            if t.get("discord_channel_id") == channel_id_str:
                ticket_id = t["id"]
                ticket_title = t.get("title", "Обращение")
                creator_discord_id = t.get("creator_discord_id")
                break

    await interaction.response.send_message(
        f"🔒 Администратор закрыл тикет, он будет удалён через **{time_str}**...\n"
        f"💬 Ответ: {answer}",
        ephemeral=False
    )

    async def _do_close():
        await asyncio.sleep(delay_seconds)
        # Close in NexusData — never delete the channel unless this actually
        # succeeded (with one retry). Same phantom-ticket fix as the button
        # close handler: api_post/api_get both silently return None on any
        # failure, so the old code deleted the channel even when the site
        # never learned the ticket closed, or (worse) when ticket_id itself
        # failed to resolve via the separate /tickets/open lookup above.
        close_result = None
        if ticket_id:
            close_result = await api_post(f"/api/v1/tickets/{ticket_id}/close", {"closed_by": user.display_name})
            if close_result is None:
                close_result = await api_post(f"/api/v1/tickets/{ticket_id}/close", {"closed_by": user.display_name})

        if not ticket_id or close_result is None:
            _pending_close.pop(channel_id_str, None)
            try:
                await channel.send(
                    "⚠️ Не удалось закрыть обращение на сайте — канал НЕ будет удалён автоматически. "
                    "Попробуйте `/close` ещё раз позже."
                )
            except Exception as e:
                logger.warning(f"Failed to warn about failed ticket close in {channel_id_str}: {e}")
            await refresh_ticket_status()
            return

        # Send DM to ticket creator
        if creator_discord_id:
            try:
                creator = await bot.fetch_user(int(creator_discord_id))
                if creator:
                    dm_embed = discord.Embed(
                        title="🔒 Ваше обращение закрыто",
                        description=(
                            f"Ваш тикет на тему **{ticket_title}** был закрыт администратором.\n\n"
                            f"**Ответ по вашему запросу:**\n{answer}"
                        ),
                        color=0x2b7af0,
                    )
                    dm_embed.set_footer(text="Сделано за сухарики")
                    await creator.send(embed=dm_embed)
            except Exception as e:
                logger.warning(f"Failed to send DM to ticket creator {creator_discord_id}: {e}")

        # Delete channel
        try:
            await channel.delete(reason=f"Ticket closed by {user}")
        except Exception as e:
            logger.warning(f"Failed to delete ticket channel: {e}")

        _pending_close.pop(channel_id_str, None)
        await refresh_ticket_status()

    task = asyncio.create_task(_do_close())
    _pending_close[channel_id_str] = task


@bot.command(name="setticketcategory")
@commands.has_permissions(administrator=True)
async def cmd_set_ticket_category(ctx, category_id: str):
    """!setticketcategory <category_id> - Set category for ticket channels."""
    await api_post("/api/v1/bot/config", {"key": "ticket_category_id", "value": category_id})
    await ctx.send(f"✅ Категория для тикетов: {category_id}")


@bot.command(name="setticketstatus")
@commands.has_permissions(administrator=True)
async def cmd_set_ticket_status(ctx, channel: discord.TextChannel):
    """!setticketstatus #channel - Set ticket status channel."""
    await api_post("/api/v1/bot/config", {"key": "ticket_status_channel_id", "value": str(channel.id)})
    await ctx.send(f"✅ Статус тикетов будет в {channel.mention}")


# Sync messages from ticket channels to NexusData
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    # Check if this is a ticket channel
    if message.channel.name and message.channel.name.startswith("ticket-"):
        # Sync message to NexusData - find ticket by channel_id
        channel_id = str(message.channel.id)
        attachments = [{"url": a.url, "filename": a.filename, "content_type": a.content_type} for a in message.attachments]

        # First find ticket_id by channel
        open_tickets = await api_get("/api/v1/tickets/open")
        ticket_id = None
        if open_tickets:
            for t in open_tickets:
                if t.get("discord_channel_id") == channel_id:
                    ticket_id = t["id"]
                    break

        if ticket_id:
            await api_post("/api/v1/tickets/message", {
                "ticket_id": ticket_id,
                "author_discord_id": str(message.author.id),
                "author_username": str(message.author),
                "author_display_name": message.author.display_name,
                "author_avatar_url": str(message.author.display_avatar.url) if message.author.display_avatar else None,
                "content": message.content,
                "attachments": attachments,
                "discord_message_id": str(message.id),
            })
            # Update status embed
            await refresh_ticket_status()

    # Department chat channels (item 6): mirror messages from any linked
    # channel into NexusData. The bot's own sent messages (site → Discord,
    # via the "send_message" action) never reach here — message.author.bot
    # already short-circuited this handler above.
    if str(message.channel.id) in _department_chat_channel_ids:
        await api_post("/api/v1/bot/department-chat/messages", {
            "discord_channel_id": str(message.channel.id),
            "discord_message_id": str(message.id),
            "author_name": message.author.display_name,
            "author_avatar_url": str(message.author.display_avatar.url) if message.author.display_avatar else None,
            "content": message.content,
        })

    # Forward to designated channel (existing logic)
    global FORWARD_CHANNEL_ID
    if FORWARD_CHANNEL_ID and message.channel.id != FORWARD_CHANNEL_ID:
        forward_ch = bot.get_channel(FORWARD_CHANNEL_ID)
        if forward_ch:
            embed = discord.Embed(
                description=message.content[:4000] if message.content else None,
                color=0x2b2d31,
            )
            embed.set_author(
                name=f"{message.author.display_name} в #{message.channel.name}",
                icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
            )
            embed.set_footer(text=f"{format_msk(message.created_at.replace(tzinfo=None))} МСК")
            image_set = False
            files_to_send = []
            for att in message.attachments:
                content_type = att.content_type or ""
                if not image_set and content_type.startswith("image/"):
                    embed.set_image(url=att.url)
                    image_set = True
                elif content_type.startswith("video/") or content_type.startswith("audio/"):
                    try:
                        file = await att.to_file()
                        files_to_send.append(file)
                    except Exception:
                        embed.add_field(name="Видео/Аудио", value=att.url, inline=False)
                else:
                    try:
                        file = await att.to_file()
                        files_to_send.append(file)
                    except Exception:
                        embed.add_field(name="Файл", value=f"[{att.filename}]({att.url})", inline=False)
            if message.stickers:
                sticker = message.stickers[0]
                if not image_set and sticker.url:
                    embed.set_image(url=sticker.url)
            try:
                await forward_ch.send(embed=embed, files=files_to_send if files_to_send else discord.utils.MISSING)
            except Exception as e:
                logger.warning(f"Forward failed: {e}")

    await bot.process_commands(message)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    """Reverse half of the ticket phantom-row fix: if a ticket channel gets
    deleted OUT OF BAND (manually in Discord, bypassing the close button/
    /close command entirely), nothing else ever tells the site — the
    Ticket row would otherwise stay "open" forever with no channel left to
    act on. Best-effort: only touches tickets that are still open."""
    if not (channel.name and channel.name.startswith("ticket-")):
        return
    open_tickets = await api_get("/api/v1/tickets/open")
    if not open_tickets:
        return
    channel_id = str(channel.id)
    for t in open_tickets:
        if t.get("discord_channel_id") == channel_id:
            await api_post(f"/api/v1/tickets/{t['id']}/close", {"closed_by": "Discord (канал удалён вручную)"})
            await refresh_ticket_status()
            break


# Handle ticket_send_message and ticket_close_channel bot actions
# (already handled in execute_bot_action, add these cases)


# ═══════════════════════════════════════════════════════════
# RUN (must be at the very end after all class definitions)
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set in .env")
        exit(1)

    @bot.event
    async def setup_hook():
        """Register persistent views so buttons work after restart."""
        bot.add_view(TicketPanelView())
        bot.add_view(TicketCloseView())
        # Sync slash commands globally
        try:
            synced = await bot.tree.sync()
            logger.info(f"Synced {len(synced)} slash command(s)")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")

    bot.run(TOKEN)
