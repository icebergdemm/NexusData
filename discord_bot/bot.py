"""
NexusData Discord Bot — in-process service (no longer a separate process
talking to NexusData over its own loopback HTTP API).
- Syncs members to NexusData every 10 minutes
- Forwards all messages to a designated channel
- Anti-raid detection
- Publishes rating embeds every hour
- Executes actions scheduled directly by the rest of the app (see schedule())
- Twink detection

See discord_bot/data.py for the direct DB-access helpers that replaced the
old api_get/api_post loopback calls, and supervise_bot()/schedule() at the
bottom of this file for how the rest of the app drives this bot.
"""

import asyncio
import hashlib
import logging
import json
import os
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

import aiofiles
import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import get_settings
from discord_bot import data as bot_data
from services import apply_punishment_discord_state
from services_ticket_media import (
    TicketMediaQuotaExceeded,
    new_storage_key,
    reserve_ticket_media,
    storage_path,
)

settings = get_settings()

GUILD_ID = int(settings.discord_guild_id or 0)

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

# Кэши discord.py урезаны под жёсткий лимит памяти контейнера (2 ГБ):
#  - chunk_guilds_at_startup=False: по умолчанию при включённом members-intent
#    библиотека выкачивает и ДЕРЖИТ В ПАМЯТИ всех участников сервера — на
#    крупной гильдии это десятки/сотни мегабайт постоянно занятыми. Там, где
#    участник действительно нужен, код уже делает fetch_member() при промахе
#    кэша, а периодическая синхронизация читает участников потоком
#    (см. sync_members_task).
#  - max_messages=100 вместо 1000: кэш сообщений нужен лишь для реакций на
#    свежие события, хранить тысячу объектов Message смысла нет.
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    chunk_guilds_at_startup=False,
    max_messages=100,
)

# Full Discord profiles (notably banners) require REST lookups and are not
# present in the lightweight member rows synchronized to SQL.  Keep them in
# memory for a short period so opening several ticket mentions does not turn
# into repeated Discord API calls.
_DISCORD_PROFILE_CACHE_TTL_SECONDS = 10 * 60
_DISCORD_PROFILE_CACHE_MAX_ENTRIES = 512
_discord_profile_cache: dict[int, tuple[float, dict]] = {}


def _discord_asset_url(asset, *, size: int) -> str | None:
    """Serialize an Asset without changing its animated format to PNG."""
    if not asset:
        return None
    try:
        return str(asset.with_size(size))
    except (AttributeError, TypeError, ValueError):
        return str(getattr(asset, "url", asset))


async def get_discord_user_profile(discord_id: int) -> dict:
    """Return the live Discord avatar/banner used by NexusData profiles.

    ``fetch_user`` supplies the global banner while ``fetch_member`` supplies
    guild-specific avatar/banner overrides.  The latter intentionally wins,
    matching what staff see for the same person inside this Discord server.
    """
    user_id = int(discord_id)
    now = asyncio.get_running_loop().time()
    cached = _discord_profile_cache.get(user_id)
    if cached and now - cached[0] < _DISCORD_PROFILE_CACHE_TTL_SECONDS:
        return dict(cached[1])

    user = await bot.fetch_user(user_id)
    member = None
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
    if guild:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None

    avatar = getattr(member, "display_avatar", None) or getattr(user, "display_avatar", None)
    banner = getattr(member, "banner", None) or getattr(user, "banner", None)
    profile = {
        "discord_id": str(user_id),
        "username": str(user),
        "display_name": (
            getattr(member, "display_name", None)
            or getattr(user, "global_name", None)
            or getattr(user, "display_name", None)
            or getattr(user, "name", None)
            or str(user_id)
        ),
        "avatar_url": _discord_asset_url(avatar, size=256),
        "banner_url": _discord_asset_url(banner, size=1024),
        "accent_color": str(getattr(user, "accent_color", None) or "") or None,
    }
    if len(_discord_profile_cache) >= _DISCORD_PROFILE_CACHE_MAX_ENTRIES:
        oldest_id = min(_discord_profile_cache, key=lambda key: _discord_profile_cache[key][0])
        _discord_profile_cache.pop(oldest_id, None)
    _discord_profile_cache[user_id] = (now, profile)
    return dict(profile)

# Anti-raid tracking
_join_timestamps = []  # list of join times in last 60 seconds
RAID_THRESHOLD = 5  # joins per minute to trigger alert
RAID_WINDOW = 60  # seconds

# Discord channel IDs linked to a department "Чат" tab — refreshed every
# 10 min by sync_members_task, consulted in on_message to mirror messages.
_department_chat_channel_ids: set = set()

# Bound config, reloaded from DiscordBotConfig at on_ready/sync_members_task.
FORWARD_CHANNEL_ID = 0
RATING_CHANNEL_ID = 0

# Persisted message-id files live next to this module, regardless of the
# current process's working directory (this used to be its own process
# launched from within discord_bot/; now it's imported as a module from the
# app root, so a bare relative filename would silently start a NEW empty
# file there instead of reusing the bot's existing persisted state).
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Rating message IDs (persisted to JSON file)
RATING_IDS_FILE = os.path.join(_BOT_DIR, "rating_message_ids.json")


def _load_rating_ids() -> dict:
    try:
        if os.path.exists(RATING_IDS_FILE):
            with open(RATING_IDS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_rating_ids(ids: dict):
    try:
        with open(RATING_IDS_FILE, "w") as f:
            json.dump(ids, f)
    except Exception as e:
        logger.warning(f"Failed to save rating IDs: {e}")


_rating_message_ids = _load_rating_ids()


# ═══════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    global FORWARD_CHANNEL_ID, RATING_CHANNEL_ID, _ticket_ipc_recovery_done
    logger.info(f"Bot ready: {bot.user} (guilds: {len(bot.guilds)})")

    # Проверяем GUILD_ID сразу при подключении, а не при первой попытке выдать
    # роль: неверный или незаполненный идентификатор ломает ВСЮ работу с
    # ролями и отделами, но проявляется отложенно и выглядит как «Discord
    # недоступен». Здесь видно, что бот подключён, и сразу понятно, тот ли
    # сервер указан.
    if not GUILD_ID:
        logger.error(
            "GUILD_ID не задан — работа с ролями работать не будет. "
            "Укажите GUILD_ID в .env (идентификатор Discord-сервера)."
        )
    elif bot.get_guild(GUILD_ID) is None:
        logger.error(
            f"GUILD_ID={GUILD_ID}: бот не состоит в этом Discord-сервере. "
            f"Доступны: {[(g.id, g.name) for g in bot.guilds]}"
        )

    config = await asyncio.to_thread(bot_data.get_bot_config)
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
    publish_rating_task.start()
    publish_department_ratings_task.start()
    round_status_task.start()
    reconcile_ticket_channels_task.start()
    if not _ticket_ipc_recovery_done:
        try:
            recovered = await asyncio.to_thread(bot_data.recover_interrupted_ticket_bot_actions)
            _ticket_ipc_recovery_done = True
            if recovered:
                logger.warning("Recovered %s interrupted ticket Discord action(s)", recovered)
        except Exception:
            logger.exception("Could not recover interrupted ticket Discord actions on ready")
    if not ticket_bot_action_task.is_running():
        ticket_bot_action_task.start()
    if settings.ticket_history_backfill_enabled:
        ticket_history_backfill_task.change_interval(
            seconds=float(settings.ticket_history_backfill_interval_seconds)
        )
        if not ticket_history_backfill_task.is_running():
            # discord.ext.tasks executes its first iteration immediately, so
            # old CDN-only ticket evidence starts migrating on bot startup.
            ticket_history_backfill_task.start()
    mute_voice_cleanup_task.start()

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
        await asyncio.to_thread(
            bot_data.report_antiraid_event,
            event_type="mass_join",
            discord_id=str(member.id),
            username=str(member),
            details={
                "joins_in_window": len(_join_timestamps),
                "window_seconds": RAID_WINDOW,
                "account_age_days": (now - member.created_at.replace(tzinfo=None)).days if member.created_at else None,
            },
        )
        logger.warning(f"RAID ALERT: {len(_join_timestamps)} joins in {RAID_WINDOW}s")

    # Check suspicious account (very new)
    if member.created_at:
        account_age = (now - member.created_at.replace(tzinfo=None)).days
        if account_age < 7:
            await asyncio.to_thread(
                bot_data.report_antiraid_event,
                event_type="suspicious_account",
                discord_id=str(member.id),
                username=str(member),
                details={"account_age_days": account_age},
            )


# ═══════════════════════════════════════════════════════════
# TASKS
# ═══════════════════════════════════════════════════════════

@tasks.loop(minutes=10)
async def sync_members_task():
    """Sync all guild members to NexusData every 10 minutes."""
    global FORWARD_CHANNEL_ID, _department_chat_channel_ids

    # Reload forward channel config
    config = await asyncio.to_thread(bot_data.get_bot_config)
    if config and config.get("forward_channel_id"):
        try:
            FORWARD_CHANNEL_ID = int(config["forward_channel_id"])
        except (ValueError, TypeError):
            pass

    # Reload department chat channel IDs (item 6)
    _department_chat_channel_ids = set(await asyncio.to_thread(bot_data.list_department_chat_channel_ids))

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    # Участники читаются ПОТОКОМ через HTTP (fetch_members), а не из кэша
    # guild.members: постоянный кэш всех участников отключён ради экономии
    # памяти (см. chunk_guilds_at_startup выше). Записываем в БД порциями,
    # чтобы в памяти одновременно жил лишь небольшой кусок списка.
    batch, synced = [], 0
    try:
        async for member in guild.fetch_members(limit=None):
            if member.bot:
                continue
            batch.append({
                "discord_id": str(member.id),
                "username": str(member),
                "display_name": member.display_name,
                "avatar": str(member.display_avatar.url) if member.display_avatar else None,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                "role_ids": [str(r.id) for r in member.roles],
            })
            if len(batch) >= 500:
                res = await asyncio.to_thread(bot_data.sync_members, batch, False)
                synced += (res or {}).get("synced", 0)
                batch = []
    except Exception as e:
        logger.error(f"Не удалось получить участников гильдии: {e}")
        return

    if batch:
        res = await asyncio.to_thread(bot_data.sync_members, batch, False)
        synced += (res or {}).get("synced", 0)

    # Автосопоставление имён с игроками — один раз в конце, а не на каждую
    # порцию: операция тяжёлая (читает всех участников и игроков разом).
    if synced:
        await asyncio.to_thread(bot_data.automatch_discord_ids)
    logger.info(f"Synced {synced} members to NexusData")


@tasks.loop(hours=1)
async def publish_rating_task():
    """Publish/update rating embeds in the rating channel every hour."""
    global RATING_CHANNEL_ID

    # Reload channel from NexusData config (in case it was changed via !setrating)
    config = await asyncio.to_thread(bot_data.get_bot_config)
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
        data = await asyncio.to_thread(bot_data.get_ratings_leaderboard, role, 10, "mission")
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
            except discord.NotFound:
                pass  # сообщение реально удалено — заведём новое ниже
            except discord.HTTPException as e:
                # Временная неполадка Discord — НЕ повод считать сообщение
                # потерянным: иначе на каждый лимит запросов/5xx в чат уходит
                # лишняя копия таблицы рейтинга, а старая остаётся висеть.
                logger.warning(f"Не удалось обновить рейтинг {role}, попробуем в следующий раз: {e}")
                await asyncio.sleep(15)
                continue

        # Send new message
        try:
            msg = await channel.send(embed=embed)
            _rating_message_ids[role] = msg.id
            _save_rating_ids(_rating_message_ids)
        except Exception as e:
            logger.error(f"Failed to send rating embed for {role}: {e}")

        # Wait 15 seconds between messages to avoid rate limits
        await asyncio.sleep(15)

    # Mission keeps its existing per-role messages above. PvP is deliberately
    # a separate message: merging it into those embeds makes the two scoring
    # systems look comparable when they are not, and quickly runs into the
    # embed field limit once the boards grow.
    pvp_data = await asyncio.to_thread(bot_data.get_ratings_leaderboard, "all", 10, "pvp")
    pvp_lb = (pvp_data or {}).get("leaderboard") or []
    pvp_lines = []
    for entry in pvp_lb:
        if entry["rank"] == 1:
            medal = "🥇"
        elif entry["rank"] == 2:
            medal = "🥈"
        elif entry["rank"] == 3:
            medal = "🥉"
        else:
            medal = f"**{entry['rank']}.**"
        name = f"<@{entry['discord_id']}>" if entry.get("discord_id") else entry["player_name"]
        pvp_lines.append(f"{medal}  {name}  -  **{entry['rating']}**")

    pvp_embed = discord.Embed(
        title="⚔ PvP рейтинг",
        description="\n\n".join(pvp_lines) if pvp_lines else "Нет данных",
        color=0xb33939,
    )
    pvp_embed.set_footer(text=f"Сделано за сухарики | Обновлено {format_msk(datetime.utcnow())} МСК")

    pvp_message_id = _rating_message_ids.get("pvp")
    if pvp_message_id:
        try:
            message = await channel.fetch_message(int(pvp_message_id))
            await message.edit(embed=pvp_embed)
            return
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            logger.warning(f"Не удалось обновить PvP рейтинг, попробуем в следующий раз: {e}")
            return
    try:
        message = await channel.send(embed=pvp_embed)
        _rating_message_ids["pvp"] = message.id
        _save_rating_ids(_rating_message_ids)
    except Exception as e:
        logger.error(f"Failed to send PvP rating embed: {e}")


# Department rating message IDs (persisted to their own JSON file, same
# pattern as _rating_message_ids above but keyed by department_id).
DEPARTMENT_RATING_IDS_FILE = os.path.join(_BOT_DIR, "department_rating_message_ids.json")


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
    departments = await asyncio.to_thread(bot_data.list_department_rating_channels)
    if not departments:
        return

    for dept in departments:
        department_id = dept["department_id"]
        channel_id = dept.get("rating_channel_id")
        if not channel_id:
            continue
        channel = bot.get_channel(int(channel_id))
        if not channel:
            await asyncio.sleep(15)
            continue

        data = await asyncio.to_thread(bot_data.get_department_rating_leaderboard, department_id, 15)
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
        skip_send = False
        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(embed=embed)
            except discord.NotFound:
                msg_id = None  # сообщение реально удалено — заведём новое ниже
            except discord.HTTPException as e:
                # Временная неполадка — не значит, что сообщение потеряно.
                logger.warning(f"Не удалось обновить рейтинг отдела {department_id}, попробуем позже: {e}")
                skip_send = True
        if not msg_id and not skip_send:
            try:
                msg = await channel.send(embed=embed)
                _department_rating_message_ids[key] = msg.id
                _save_department_rating_ids(_department_rating_message_ids)
            except Exception as e:
                logger.error(f"Failed to send department rating embed for department {department_id}: {e}")

        # Wait 15 seconds between departments to avoid rate limits — same
        # precaution as publish_rating_task, whether we edited or sent.
        await asyncio.sleep(15)


# Ticket status message ID — persisted to disk (same pattern as
# _round_status_message_ids below): a bare module-level variable resets to
# None on every restart, and refresh_ticket_status() then sent a brand new
# message every single time instead of editing the old one, leaving a trail
# of stale "📊 Статус обращений" embeds in the channel.
TICKET_STATUS_ID_FILE = os.path.join(_BOT_DIR, "ticket_status_message_id.json")


def _load_ticket_status_id() -> Optional[int]:
    try:
        if os.path.exists(TICKET_STATUS_ID_FILE):
            with open(TICKET_STATUS_ID_FILE, "r") as f:
                return json.load(f).get("message_id")
    except Exception:
        pass
    return None


def _save_ticket_status_id(message_id: Optional[int]) -> None:
    try:
        with open(TICKET_STATUS_ID_FILE, "w") as f:
            json.dump({"message_id": message_id}, f)
    except Exception as e:
        logger.warning(f"Failed to save ticket status message ID: {e}")


_ticket_status_msg_id = _load_ticket_status_id()


async def refresh_ticket_status():
    """Update ticket status embed. Called on ticket create/message/close."""
    global _ticket_status_msg_id

    config = await asyncio.to_thread(bot_data.get_bot_config)
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

    tickets = await asyncio.to_thread(bot_data.get_open_tickets)
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
        except discord.NotFound:
            # Сообщение действительно удалено — только тогда заводим новое.
            _ticket_status_msg_id = None
            _save_ticket_status_id(None)
        except discord.HTTPException as e:
            # Временная неполадка Discord (лимит запросов, 5xx) — НЕ повод
            # считать сообщение потерянным и плодить дубликат: молча выходим,
            # следующий вызов (тикет создан/закрыт/новое сообщение) попробует
            # снова с тем же id.
            logger.warning(f"Не удалось обновить статус тикетов, попробуем позже: {e}")
            return

    try:
        msg = await channel.send(embed=embed)
        _ticket_status_msg_id = msg.id
        _save_ticket_status_id(msg.id)
    except Exception as e:
        logger.error(f"Failed to send ticket status: {e}")


# Round status message IDs (persisted to JSON file, keyed by server name)
ROUND_STATUS_IDS_FILE = os.path.join(_BOT_DIR, "round_status_message_ids.json")
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
    start/end via the 'refresh_round_status' action (see execute_bot_action).
    """
    config = await asyncio.to_thread(bot_data.get_bot_config)
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

    status = await asyncio.to_thread(bot_data.get_round_status, server_name)
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
        except discord.NotFound:
            pass  # сообщение реально удалено — заведём новое ниже
        except discord.HTTPException as e:
            # Временная неполадка Discord (лимит запросов, 5xx на fetch) —
            # раньше это тоже трактовалось как «сообщения больше нет» и
            # каждую минуту при таком сбое в канал улетала лишняя копия
            # статуса раунда, а старая молча оставалась висеть. Здесь просто
            # пропускаем цикл — round_status_task вызовет эту функцию снова
            # через минуту с тем же message_id.
            logger.warning(f"Не удалось обновить статус раунда {server_name}, попробуем через минуту: {e}")
            return

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


@tasks.loop(minutes=30)
async def reconcile_ticket_channels_task():
    """Marks tickets incomplete when their Discord channel vanished out of band.

    Раньше единственный способ закрыть тикет — кнопка «Закрыть обращение» —
    почти всегда падал с ошибкой из-за бага с ticket_id=None у переживших
    рестарт кнопок (см. close_button). Ручное удаление канала через Discord не
    является доказательством завершённой архивации, поэтому такие записи
    остаются открытыми и получают явный статус incomplete для проверки.

    Кэш используется как быстрый положительный ответ. Его отсутствие не
    считается удалением: перед SQL-закрытием это подтверждается Discord API,
    иначе кратковременный cache miss мог закрыть живой тикет.
    """
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    tickets = await asyncio.to_thread(bot_data.get_open_tickets)
    if not tickets:
        return

    closed_any = False
    for t in tickets:
        # A manual Discord deletion is an evidence exception.  Keep it open
        # for review; this reconciliation loop must not overwrite that state
        # with a false SQL closure after the Gateway event has been handled.
        if t.get("archive_state") == "incomplete":
            continue
        channel_id = t.get("discord_channel_id")
        if not channel_id:
            continue
        try:
            numeric_channel_id = int(channel_id)
        except (ValueError, TypeError):
            continue
        if guild.get_channel(numeric_channel_id) is not None:
            continue
        try:
            await bot.fetch_channel(numeric_channel_id)
            continue
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning(
                "Не удалось проверить Discord-канал %s тикета #%s: %s",
                channel_id,
                t["id"],
                type(exc).__name__,
            )
            continue

        try:
            ok = await asyncio.to_thread(
                bot_data.mark_ticket_archive_incomplete, t["id"],
            )
        except Exception as e:
            logger.warning(f"Не удалось пометить тикет #{t['id']} как incomplete после удаления канала: {e}")
            continue
        if ok:
            closed_any = True
            logger.warning(
                "Тикет #%s помечен как incomplete: канал %s удалён в Discord "
                "до подтверждения архива",
                t["id"],
                channel_id,
            )

    if closed_any:
        await refresh_ticket_status()


_ticket_history_backfill_offset = 0
_ticket_history_backfill_after: dict[int, int] = {}
_ticket_history_backfill_retry: set[int] = set()


@tasks.loop(minutes=15)
async def ticket_history_backfill_task():
    """Incrementally mirror evidence from every still-open ticket channel.

    This is intentionally independent from channel reconciliation/closing:
    it never changes ticket status, channel permissions, or deletes anything.
    A bounded round-robin page lets large installations converge over several
    iterations without monopolising the Discord connection.
    """
    global _ticket_history_backfill_offset

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logger.warning("Ticket history backfill skipped: configured guild is unavailable")
        return

    tickets = [
        item for item in await asyncio.to_thread(bot_data.get_open_tickets)
        if item.get("discord_channel_id")
    ]
    if not tickets:
        _ticket_history_backfill_offset = 0
        _ticket_history_backfill_after.clear()
        _ticket_history_backfill_retry.clear()
        return

    tickets.sort(key=lambda item: int(item["id"]))
    valid_ids = {int(item["id"]) for item in tickets}
    for stale_id in set(_ticket_history_backfill_after) - valid_ids:
        _ticket_history_backfill_after.pop(stale_id, None)
    _ticket_history_backfill_retry.intersection_update(valid_ids)

    channel_limit = min(
        len(tickets),
        int(settings.ticket_history_backfill_channels_per_run),
    )
    start = _ticket_history_backfill_offset % len(tickets)
    selected = [tickets[(start + index) % len(tickets)] for index in range(channel_limit)]
    _ticket_history_backfill_offset = (start + channel_limit) % len(tickets)

    page_limit = int(settings.ticket_history_backfill_messages_per_channel)
    pause_seconds = float(settings.ticket_history_backfill_pause_seconds)
    fetched_total = 0
    captured_total = 0
    failed_total = 0

    for ticket in selected:
        ticket_id = int(ticket["id"])
        try:
            channel_id = int(ticket["discord_channel_id"])
        except (TypeError, ValueError):
            logger.warning(
                "Ticket #%s history backfill skipped: invalid Discord channel id",
                ticket_id,
            )
            continue

        channel = guild.get_channel(channel_id)
        if channel is None or not hasattr(channel, "history"):
            # The separate reconciliation task owns missing-channel status.
            logger.info(
                "Ticket #%s history backfill skipped: Discord channel %s is not cached",
                ticket_id,
                channel_id,
            )
            continue

        after_id = _ticket_history_backfill_after.get(ticket_id)
        if not after_id:
            # A fresh audit cycle gets one chance to clear a previous
            # transient failure. A new failure below will add it back.
            _ticket_history_backfill_retry.discard(ticket_id)
        history_options = {"limit": page_limit, "oldest_first": True}
        if after_id:
            history_options["after"] = discord.Object(id=after_id)

        fetched_for_ticket = 0
        last_seen_id = after_id
        page_completed = False
        try:
            async for historical in channel.history(**history_options):
                fetched_for_ticket += 1
                fetched_total += 1
                last_seen_id = int(historical.id)
                try:
                    await _capture_ticket_message(ticket_id, historical)
                    captured_total += 1
                    persisted = await asyncio.to_thread(
                        bot_data.get_ticket_message_by_discord_id,
                        ticket_id,
                        str(historical.id),
                    )
                    if (
                        not persisted
                        or _attachment_collection_has_failure(
                            ticket_id, (persisted or {}).get("attachments")
                        )
                        or _archive_has_failure(
                            ticket_id, (persisted or {}).get("reference_snapshot")
                        )
                    ):
                        failed_total += 1
                        _ticket_history_backfill_retry.add(ticket_id)
                        logger.warning(
                            "Ticket #%s background history message %s still has "
                            "incomplete media; it will be retried on the next audit cycle",
                            ticket_id,
                            historical.id,
                        )
                except Exception as exc:
                    failed_total += 1
                    _ticket_history_backfill_retry.add(ticket_id)
                    logger.exception(
                        "Ticket #%s background history message %s failed: %s",
                        ticket_id,
                        historical.id,
                        type(exc).__name__,
                    )
                if pause_seconds:
                    await asyncio.sleep(pause_seconds)
            page_completed = True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            failed_total += 1
            _ticket_history_backfill_retry.add(ticket_id)
            logger.warning(
                "Ticket #%s history page could not be read: %s",
                ticket_id,
                type(exc).__name__,
            )
        except Exception as exc:
            failed_total += 1
            _ticket_history_backfill_retry.add(ticket_id)
            logger.exception(
                "Ticket #%s unexpected history backfill error: %s",
                ticket_id,
                type(exc).__name__,
            )

        if last_seen_id:
            _ticket_history_backfill_after[ticket_id] = last_seen_id
        # Fewer than a full page means this pass reached the current end. Keep
        # the high-water mark for cheap incremental scans; only restart from
        # the beginning when this audit observed incomplete evidence.
        if (
            page_completed
            and fetched_for_ticket < page_limit
            and ticket_id in _ticket_history_backfill_retry
        ):
            _ticket_history_backfill_after.pop(ticket_id, None)

    logger.info(
        "Ticket history backfill page: channels=%s fetched=%s captured=%s failed=%s",
        len(selected),
        fetched_total,
        captured_total,
        failed_total,
    )


_ticket_ipc_tasks: set[asyncio.Task] = set()
_ticket_ipc_active_ticket_ids: set[int] = set()
_TICKET_IPC_CONCURRENCY = 2


async def _run_ticket_ipc_action(action: dict) -> None:
    action_id = int(action["id"])
    payload = dict(action.get("payload") or {})
    ticket_id = int(payload.get("ticket_id", 0) or 0)
    try:
        success, error = await execute_bot_action(action["action_type"], payload)
        if action["action_type"] == "ticket_send_message" and payload.get("message_id"):
            await asyncio.to_thread(
                bot_data.set_ticket_message_delivery,
                int(payload.get("ticket_id", 0)),
                int(payload["message_id"]),
                "sent" if success else "failed",
                None if success else error,
            )
        if not success and action["action_type"] == "ticket_close_channel":
            attempt = int(payload.get("_attempt", 0) or 0)
            if attempt < 8:
                delay = min(300, 5 * (2 ** attempt))
                await asyncio.to_thread(
                    bot_data.requeue_ticket_bot_action,
                    action_id,
                    error or "Evidence-first close will be retried",
                    delay_seconds=delay,
                    increment_attempt=True,
                )
                logger.warning(
                    "Ticket close IPC action %s deferred for retry %s in %ss: %s",
                    action_id,
                    attempt + 1,
                    delay,
                    error,
                )
                return
        await asyncio.to_thread(
            bot_data.finish_ticket_bot_action,
            action_id,
            bool(success),
            error,
        )
    except asyncio.CancelledError:
        await asyncio.to_thread(
            bot_data.requeue_ticket_bot_action,
            action_id,
            "Discord worker stopped during execution",
            delay_seconds=2,
        )
        raise
    except Exception as exc:
        logger.exception("Ticket IPC action %s failed unexpectedly", action_id)
        if action["action_type"] == "ticket_send_message" and payload.get("message_id"):
            await asyncio.to_thread(
                bot_data.set_ticket_message_delivery,
                ticket_id,
                int(payload["message_id"]),
                "failed",
                f"{type(exc).__name__}: {exc}",
            )
        if action["action_type"] == "ticket_close_channel":
            attempt = int(payload.get("_attempt", 0) or 0)
            if attempt < 8:
                delay = min(300, 5 * (2 ** attempt))
                await asyncio.to_thread(
                    bot_data.requeue_ticket_bot_action,
                    action_id,
                    f"{type(exc).__name__}: {exc}",
                    delay_seconds=delay,
                    increment_attempt=True,
                )
                return
        await asyncio.to_thread(
            bot_data.finish_ticket_bot_action,
            action_id,
            False,
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        if ticket_id:
            _ticket_ipc_active_ticket_ids.discard(ticket_id)


@tasks.loop(seconds=0.5)
async def ticket_bot_action_task():
    """Consume the durable ticket IPC queue owned by the Discord process."""
    if not bot.is_ready():
        return
    available = _TICKET_IPC_CONCURRENCY - len(_ticket_ipc_tasks)
    for _ in range(max(0, available)):
        action = await asyncio.to_thread(bot_data.claim_next_ticket_bot_action)
        if not action:
            break
        ticket_id = int((action.get("payload") or {}).get("ticket_id", 0) or 0)
        if ticket_id and ticket_id in _ticket_ipc_active_ticket_ids:
            # Preserve send-before-close ordering for one ticket. Requeueing the
            # head is cheap and the next loop iteration runs after 500 ms.
            await asyncio.to_thread(
                bot_data.requeue_ticket_bot_action,
                int(action["id"]),
                "Waiting for an earlier action on the same ticket",
            )
            break
        if ticket_id:
            _ticket_ipc_active_ticket_ids.add(ticket_id)
        task = asyncio.create_task(_run_ticket_ipc_action(action))
        _ticket_ipc_tasks.add(task)
        def _release_ticket_action(done_task, *, claimed_ticket_id=ticket_id):
            _ticket_ipc_tasks.discard(done_task)
            if claimed_ticket_id:
                _ticket_ipc_active_ticket_ids.discard(claimed_ticket_id)

        task.add_done_callback(_release_ticket_action)


@tasks.loop(minutes=5)
async def mute_voice_cleanup_task():
    """Авто-снятие голосовых ограничений после истечения мута (п.7)."""
    try:
        raw = await asyncio.to_thread(bot_data.get_json_bot_config, "discord_mutes", {})
        now = datetime.utcnow()
        expired_ids = []
        for did, val in list(raw.items()):
            try:
                if datetime.fromisoformat(str(val.get("expires_at", ""))) <= now:
                    expired_ids.append(did)
            except Exception:
                expired_ids.append(did)
        if not expired_ids:
            # still prune stale via get_active_mutes
            await asyncio.to_thread(bot_data.get_active_mutes)
            return
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            await asyncio.to_thread(bot_data.get_active_mutes)
            return
        for did in expired_ids:
            try:
                member = guild.get_member(int(did))
                if not member:
                    try:
                        member = await guild.fetch_member(int(did))
                    except Exception:
                        continue
                await _restore_voice_on_unmute(member, "авто-размут (истёк срок)")
            except Exception as e:
                logger.warning(f"mute_voice_cleanup failed for {did}: {e}")
        # после снятия ограничений — почистить хранилище мутов
        await asyncio.to_thread(bot_data.get_active_mutes)
    except Exception as e:
        logger.warning(f"mute_voice_cleanup_task error: {e}")


# ═══════════════════════════════════════════════════════════
# CHANNEL-CATEGORY CHECK (near-synchronous, called via schedule()+wrap_future)
# ═══════════════════════════════════════════════════════════

async def check_channel_category(channel_id: int, category_id: str) -> dict:
    """Direct replacement for the old queued "check_channel_category" bot
    action — routes/documents.py's verify-channel endpoint now calls this via
    schedule()+asyncio.wrap_future instead of polling a DiscordBotAction row
    for ~9s. Raises on failure (channel not found) so the caller's await
    surfaces it directly."""
    channel = bot.get_channel(channel_id)
    if not channel:
        channel = await bot.fetch_channel(channel_id)  # raises on failure

    channel_category_id = str(getattr(channel, "category_id", None) or "")
    belongs = bool(category_id) and channel_category_id == str(category_id)
    return {"belongs_to_category": belongs, "channel_name": getattr(channel, "name", None)}


# ═══════════════════════════════════════════════════════════
# DISCORD VIEWER — просмотр/отправка сообщений от лица бота
# (routes/discord_viewer_api.py, все вызовы через schedule()+wrap_future,
# т.к. discord.py живёт в собственном event loop — см. schedule() ниже)
# ═══════════════════════════════════════════════════════════

def _viewer_guild() -> discord.Guild:
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        raise RuntimeError("Бот не подключён к серверу Discord (GUILD_ID недоступен)")
    return guild


def _serialize_author(user) -> dict:
    return {
        "id": str(user.id),
        "username": str(user),
        "display_name": getattr(user, "display_name", None) or user.name,
        "avatar_url": str(user.display_avatar.url) if getattr(user, "display_avatar", None) else None,
        "bot": bool(getattr(user, "bot", False)),
    }


def _serialize_message(msg: discord.Message) -> dict:
    return {
        "id": str(msg.id),
        "content": msg.content,
        "author": _serialize_author(msg.author),
        "created_at": msg.created_at.isoformat(),
        "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
        "attachments": [
            {"id": str(a.id), "filename": a.filename, "url": a.url, "content_type": a.content_type, "size": a.size}
            for a in msg.attachments
        ],
        "embeds": [e.to_dict() for e in msg.embeds],
        "reply_to_id": str(msg.reference.message_id) if msg.reference and msg.reference.message_id else None,
    }


async def viewer_get_tree() -> dict:
    """Категории/каналы, доступные для просмотра именно этому боту (иначе
    список выглядел бы полным, а половина каналов при открытии падала бы
    403-й ошибкой Discord). Голосовые и форумные каналы не отдаются — вьюер
    работает только с текстовыми (в т.ч. announcement-каналами)."""
    guild = _viewer_guild()
    me = guild.me
    channels = []
    for ch in guild.text_channels:
        perms = ch.permissions_for(me)
        if not perms.view_channel:
            continue
        channels.append({
            "id": str(ch.id), "name": ch.name, "position": ch.position,
            "can_send": perms.send_messages,
            "category_id": str(ch.category_id) if ch.category_id else None,
        })
    categories = [{"id": str(c.id), "name": c.name, "position": c.position} for c in guild.categories]
    categories.sort(key=lambda c: c["position"])
    channels.sort(key=lambda c: c["position"])
    return {
        "guild": {"id": str(guild.id), "name": guild.name, "icon_url": str(guild.icon.url) if guild.icon else None},
        "categories": categories,
        "channels": channels,
    }


async def viewer_get_messages(channel_id: int, before: Optional[int], limit: int) -> list:
    channel = bot.get_channel(channel_id)
    if not channel:
        channel = await bot.fetch_channel(channel_id)
    limit = max(1, min(limit, 100))
    before_obj = discord.Object(id=before) if before else None
    out = []
    async for msg in channel.history(limit=limit, before=before_obj):
        out.append(_serialize_message(msg))
    return out  # порядок — от новых к старым (как отдаёт history())


async def viewer_send_message(channel_id: int, content: str, filename: Optional[str], file_bytes: Optional[bytes]) -> dict:
    channel = bot.get_channel(channel_id)
    if not channel:
        channel = await bot.fetch_channel(channel_id)
    import io
    file_obj = discord.File(io.BytesIO(file_bytes), filename=filename) if file_bytes else None
    msg = await channel.send(content=(content[:2000] if content else None), file=file_obj)
    return _serialize_message(msg)


async def viewer_export_messages(channel_id: int, max_messages: int) -> list:
    """Полная выгрузка канала в JSON (см. routes/discord_viewer_api.py).
    Ограничено max_messages, чтобы случайный экспорт древнего канала с
    десятками тысяч сообщений не подвесил бота на несколько минут внутри
    одного действия на общем event loop."""
    channel = bot.get_channel(channel_id)
    if not channel:
        channel = await bot.fetch_channel(channel_id)
    max_messages = max(1, min(max_messages, 20000))
    out = []
    async for msg in channel.history(limit=max_messages, oldest_first=True):
        out.append(_serialize_message(msg))
    return out


_PUNISHMENT_EMBED_TYPES = {
    "⚠️ Предупреждение": "warning",
    "📋 Выговор": "reprimand",
    "👢 Кик": "kick",
    "🔨 Бан": "ban",
}


def _punishment_embed_signature(embed_data: dict) -> Optional[tuple[str, str, str]]:
    """Extract the stable identity fields from a punishment embed.

    Only the exact embed shape emitted by the punishment renderer is managed.
    """
    if (embed_data.get("footer") or {}).get("text") != "1984":
        return None
    punishment_type = _PUNISHMENT_EMBED_TYPES.get(embed_data.get("title"))
    if not punishment_type:
        return None

    fields = {field.get("name"): field.get("value", "") for field in embed_data.get("fields", [])}
    player_value = str(fields.get("Игрок", ""))
    steamid_match = re.search(r"`([^`]+)`", player_value)
    player_steamid = steamid_match.group(1).strip() if steamid_match else player_value.split("(", 1)[0].strip()
    if not player_steamid or "Выдал" not in fields or "Причина" not in fields:
        return None
    return punishment_type, player_steamid, str(fields["Причина"]).strip()


async def send_punishment_embed(punishment_id: int, embed_data: dict, state_hash: str, include_ban_channel: bool) -> dict:
    """Send one punishment to configured channels and persist message IDs."""
    config = await asyncio.to_thread(bot_data.get_bot_config)
    keys = ["punishment_channel_id"] + (["ban_channel_id"] if include_ban_channel else [])
    channel_ids = []
    for key in keys:
        raw_channel_id = (config or {}).get(key)
        if not raw_channel_id:
            continue
        try:
            channel_id = int(raw_channel_id)
        except (TypeError, ValueError):
            logger.warning("Invalid punishment channel config key %s", key)
            continue
        if channel_id not in channel_ids:
            channel_ids.append(channel_id)

    sent = []
    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                logger.warning("Punishment channel %s unavailable: %s", channel_id, type(exc).__name__)
                continue
        message = await channel.send(embed=discord.Embed.from_dict(embed_data))
        await asyncio.to_thread(
            bot_data.record_punishment_discord_message,
            punishment_id, channel_id, message.id, state_hash,
        )
        sent.append({"channel_id": str(channel_id), "message_id": str(message.id)})
    return {"sent": sent}


async def sync_punishment_discord_messages(changes: list[dict]) -> list[dict]:
    """Edit tracked punishment messages and report per-message outcomes."""
    outcomes = []
    for change in changes:
        channel_id = str(change["channel_id"])
        message_id = str(change["message_id"])
        outcome = {
            "link_id": change["link_id"],
            "status": "error",
            "error": None,
        }
        try:
            channel = bot.get_channel(int(channel_id))
            if not channel:
                channel = await bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            if not bot.user or message.author.id != bot.user.id:
                outcome["error"] = "message is not authored by this bot"
                outcomes.append(outcome)
                continue

            embed_dicts = [embed.to_dict() for embed in message.embeds]
            matched = False
            for embed_data in embed_dicts:
                if _punishment_embed_signature(embed_data):
                    matched = True
                    if apply_punishment_discord_state(embed_data, change["state"]):
                        await message.edit(embeds=[discord.Embed.from_dict(data) for data in embed_dicts])
                    break
            if not matched:
                outcome["error"] = "unexpected punishment embed format"
                outcomes.append(outcome)
                continue
            outcome["status"] = "synced"
        except discord.NotFound:
            outcome["status"] = "missing"
            outcome["error"] = "message or channel not found"
        except (discord.Forbidden, discord.HTTPException) as exc:
            outcome["error"] = type(exc).__name__
        except Exception as exc:
            outcome["error"] = type(exc).__name__
        outcomes.append(outcome)
    return outcomes


async def viewer_get_roles() -> list:
    guild = _viewer_guild()
    return [
        {"id": str(r.id), "name": r.name, "color": f"#{r.color.value:06x}" if r.color.value else None,
         "position": r.position, "managed": r.managed, "mentionable": r.mentionable}
        for r in guild.roles if r.name != "@everyone"
    ]


async def viewer_update_member_roles(member_id: int, add_role_ids: list, remove_role_ids: list) -> dict:
    guild = _viewer_guild()
    member = guild.get_member(member_id)
    if not member:
        member = await guild.fetch_member(member_id)
    errors = []
    for rid in add_role_ids:
        role = guild.get_role(int(rid))
        if not role:
            errors.append(f"роль {rid} не найдена")
            continue
        try:
            await member.add_roles(role, reason="Ручное назначение через Discord-вьюер")
        except Exception as e:
            errors.append(f"выдать {rid}: {e}")
    for rid in remove_role_ids:
        role = guild.get_role(int(rid))
        if not role:
            errors.append(f"роль {rid} не найдена")
            continue
        try:
            await member.remove_roles(role, reason="Ручное снятие через Discord-вьюер")
        except Exception as e:
            errors.append(f"снять {rid}: {e}")
    return {"role_ids": [str(r.id) for r in member.roles], "errors": errors}


# ═══════════════════════════════════════════════════════════
# BOT ACTION EXECUTOR
# ═══════════════════════════════════════════════════════════

# Bounded in-memory log of recent sync_department_role failures — surfaced on
# /adminold/discord-bot and /api/v1/admin/discord-bot (see
# routes/discord_bot_api.py's panel_router) so a broken auto role-push is
# visible there instead of only in the bot's own logs. Replaces the old
# DiscordBotAction.error_message column, which had nowhere to write to once
# the action queue itself was removed.
_recent_role_sync_failures = deque(maxlen=50)


def get_recent_role_sync_failures() -> list:
    return list(_recent_role_sync_failures)


async def execute_bot_action(action_type: str, payload: dict) -> Tuple[bool, Optional[str]]:
    """Execute a bot action. Returns (success, error_message).

    Called directly via schedule(execute_bot_action(...)) from every part of
    the app that used to queue a DiscordBotAction row — the action_type/
    payload contract is unchanged, only the delivery mechanism (direct call
    instead of a polled DB queue) is different, so every call site keeps
    passing the exact same two arguments it always did."""
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

        elif action_type == "send_punishment_embed":
            await send_punishment_embed(
                int(payload["punishment_id"]),
                payload.get("embed", {}),
                payload.get("state_hash"),
                bool(payload.get("include_ban_channel")),
            )
            return True, None

        elif action_type == "send_embed":
            # Supports both direct channel_id and channel_config_key (bot config lookup)
            channel_id_raw = payload.get("channel_id", 0)
            channel_config_key = payload.get("channel_config_key")

            if channel_config_key:
                config = await asyncio.to_thread(bot_data.get_bot_config)
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

        elif action_type == "send_dm":
            # Личное сообщение конкретному пользователю Discord (payload:
            # discord_id + content и/или embed) — используется, например,
            # уведомлением о разблокировке ачивки (routes/achievements.py).
            # Намеренно тихий no-op на любой сбой (ЛС закрыты, вышел с
            # сервера, бот не видит пользователя и т.п.): отсутствие ЛС не
            # должно быть поводом ретраить или ронять вызывающий эндпоинт.
            discord_id = payload.get("discord_id")
            if not discord_id:
                return True, None
            try:
                user = bot.get_user(int(discord_id)) or await bot.fetch_user(int(discord_id))
            except Exception:
                logger.warning(f"send_dm: пользователь {discord_id} не найден")
                return True, None

            content = payload.get("content")
            embed_data = payload.get("embed")
            try:
                if embed_data:
                    await user.send(content=content, embed=discord.Embed.from_dict(embed_data))
                elif content:
                    await user.send(content=content[:2000])
                return True, None
            except discord.Forbidden:
                logger.info(f"send_dm: у {discord_id} закрыты ЛС от бота")
                return True, None
            except Exception as e:
                logger.warning(f"send_dm: не удалось отправить {discord_id}: {e}")
                return True, None

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
                # Самая частая причина: бот не подключён к Discord (неверный
                # токен, идёт переподключение) либо в GUILD_ID указан не тот
                # сервер. Записываем в список сбоев, чтобы это было видно на
                # странице «Discord-бот», а не только в логах.
                msg = ("Сервер Discord недоступен боту: проверьте GUILD_ID и то, "
                       "что бот подключён (см. статус на странице Discord-бота)")
                _recent_role_sync_failures.append({
                    "discord_id": payload.get("discord_id"),
                    "add_role_id": payload.get("add_role_id"),
                    "remove_role_id": payload.get("remove_role_id"),
                    "error_message": msg,
                    "executed_at": datetime.utcnow().isoformat(),
                })
                return False, msg
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
            if errors:
                _recent_role_sync_failures.append({
                    "discord_id": discord_id,
                    "add_role_id": add_role_id,
                    "remove_role_id": remove_role_id,
                    "error_message": "; ".join(errors),
                    "executed_at": datetime.utcnow().isoformat(),
                })
                return False, "; ".join(errors)
            return True, None

        elif action_type == "trap_triggered":
            # Send trap notification to traps channel
            config = await asyncio.to_thread(bot_data.get_bot_config)
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

        elif action_type == "ticket_send_message":
            channel_id = int(payload.get("channel_id", 0))
            ticket_id = int(payload.get("ticket_id", 0))
            local_message_id = int(payload.get("message_id", 0) or 0)
            content = payload.get("content", "")
            attachment_paths = list(payload.get("attachment_paths") or [])
            ticket = await asyncio.to_thread(
                bot_data.get_ticket_by_channel, str(channel_id), True
            )
            if not ticket_id or not ticket or int(ticket["id"]) != ticket_id:
                return False, "Ticket/channel binding could not be verified"
            if ticket.get("status") == "closed":
                return False, "Ticket is already closed"
            if local_message_id:
                delivery = await asyncio.to_thread(
                    bot_data.get_ticket_message_delivery,
                    ticket_id,
                    local_message_id,
                )
                if delivery and delivery.get("status") == "sent" and delivery.get("discord_message_id"):
                    return True, None
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except discord.NotFound:
                    return False, "Ticket channel no longer exists"
                except (discord.Forbidden, discord.HTTPException) as exc:
                    return False, f"Ticket channel could not be fetched: {type(exc).__name__}"
            if channel and (content or attachment_paths):
                nonce = f"ndt{local_message_id}" if local_message_id else None
                if (
                    nonce
                    and int(payload.get("_execution_count", 0) or 0) > 1
                    and hasattr(channel, "history")
                ):
                    try:
                        async for recent in channel.history(limit=100):
                            if str(getattr(recent, "nonce", "")) != nonce:
                                continue
                            await asyncio.to_thread(
                                bot_data.set_ticket_message_delivery,
                                ticket_id,
                                local_message_id,
                                "sent",
                                None,
                                str(recent.id),
                            )
                            return True, None
                    except (discord.Forbidden, discord.HTTPException):
                        # Sending below remains the authoritative operation; a
                        # missing history permission will produce its own clear
                        # Discord failure if it also prevents delivery.
                        pass
                if len(attachment_paths) > 10:
                    return False, "Discord permits at most 10 attachments per message"
                files = []
                try:
                    ticket_media_root = (
                        Path(settings.ticket_attachments_dir).expanduser().resolve(strict=False)
                        / str(ticket_id)
                    ).resolve(strict=False)
                    for item in attachment_paths:
                        if isinstance(item, dict):
                            path = str(item.get("path") or "")
                            filename = str(item.get("filename") or os.path.basename(path))
                        else:
                            path = str(item or "")
                            filename = os.path.basename(path)
                        try:
                            resolved_path = Path(path).expanduser().resolve(strict=True)
                        except (OSError, RuntimeError, ValueError):
                            return False, "A local ticket attachment is unavailable"
                        if (
                            not path
                            or not resolved_path.is_file()
                            or not resolved_path.is_relative_to(ticket_media_root)
                        ):
                            return False, "A local ticket attachment is unavailable"
                        # discord.File ultimately sends this as a multipart
                        # filename. Strip directories/control path tricks from
                        # caller-provided metadata even after the local path
                        # itself has been securely constrained above.
                        safe_filename = Path(filename.replace("\\", "/")).name or "file"
                        files.append(discord.File(resolved_path, filename=safe_filename))
                    sent_message = await channel.send(
                        content=content[:2000] if content else None,
                        files=files,
                        nonce=nonce,
                    )
                    if local_message_id:
                        await asyncio.to_thread(
                            bot_data.set_ticket_message_delivery,
                            ticket_id,
                            local_message_id,
                            "sent",
                            None,
                            str(sent_message.id),
                        )
                    return True, None
                finally:
                    for file in files:
                        file.close()

        elif action_type == "ticket_close_channel":
            channel_id = int(payload.get("channel_id", 0))
            ticket_id = int(payload.get("ticket_id", 0))
            closed_by = str(payload.get("closed_by") or "NexusData")[:255]
            force_close = bool(payload.get("force"))
            if not ticket_id:
                return False, "ticket_id is required before deleting a ticket channel"
            ticket = await asyncio.to_thread(
                bot_data.get_ticket_by_channel, str(channel_id), True
            )
            if not ticket or int(ticket["id"]) != ticket_id:
                return False, "Ticket/channel binding could not be verified"
            auto_close_before = payload.get("auto_close_before")
            if auto_close_before and not await asyncio.to_thread(
                bot_data.ticket_is_stale_unanswered,
                ticket_id,
                auto_close_before,
            ):
                # The queued job outlived its premise (for example an admin
                # answered while Discord was offline). This is a successful
                # no-op, not a retryable transport failure.
                return True, None
            channel = bot.get_channel(channel_id)
            channel_missing = False
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except discord.NotFound:
                    channel_missing = True
                except (discord.Forbidden, discord.HTTPException) as exc:
                    return False, f"Ticket channel could not be fetched: {type(exc).__name__}"
            if channel:
                try:
                    await _freeze_reconcile_and_delete_ticket_channel(
                        ticket_id,
                        channel,
                        reason="Ticket closed from NexusData",
                        closed_by=closed_by,
                        auto_close_before=auto_close_before,
                        force=force_close,
                    )
                except _TicketAutoCloseNoLongerEligible:
                    return True, None
                except TicketArchiveIncomplete as e:
                    logger.warning("Failed to close ticket channel: %s", e)
                    return False, (
                        "Не сохранено вложений: "
                        f"{len(e.defects)}. Канал оставлен открытым — повторите позже "
                        "или закройте принудительно."
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete ticket channel: {e}")
                    return False, f"Ticket channel retained: {type(e).__name__}"
                return True, None
            if not channel_missing:
                return False, "Ticket channel is unavailable"
            # Missing is ambiguous: a moderator may have deleted the channel
            # manually while this queued action was waiting. Only a recovery
            # job emitted *after* our final history sweep and successful
            # channel.delete() is allowed to finish SQL closure. Otherwise
            # preserve a visible evidence exception instead of falsely
            # claiming that the archive is complete.
            if payload.get("evidence_archive_confirmed") is True:
                if await _close_ticket_with_retry(
                    ticket_id,
                    closed_by,
                    archive_state=str(payload.get("archive_state") or "complete"),
                ):
                    return True, None
                return False, "Ticket channel is gone but SQL close could not be recorded"
            if force_close:
                # The channel is already gone and a moderator explicitly asked
                # for the closure: record it while keeping the archive marked
                # incomplete, instead of leaving an uncloseable phantom row.
                await asyncio.to_thread(bot_data.mark_ticket_archive_incomplete, ticket_id)
                if await asyncio.to_thread(
                    bot_data.close_ticket, ticket_id, closed_by, force=True,
                ):
                    return True, None
            if await asyncio.to_thread(bot_data.mark_ticket_archive_incomplete, ticket_id):
                return True, None
            current = await asyncio.to_thread(
                bot_data.get_ticket_by_channel, str(channel_id), True
            )
            if current and current.get("archive_state") == "incomplete":
                return True, None
            return False, "Ticket channel disappeared before evidence archive confirmation"

        elif action_type == "send_sub_project_embed":
            # Send a new sub-project embed to the configured channel
            # FIX: upload image as Discord attachment when image_path is provided —
            # previously the embed only referenced a remote URL (public_site_url)
            # which Discord must fetch server-side; that silently fails when the
            # URL is internal (127.0.0.1) or not publicly reachable, so the text
            # posts but the image never appears. Attachment upload makes Discord
            # host the file itself and always shows the image.
            channel_id = int(payload.get("channel_id", 0))
            embed_data = payload.get("embed", {})
            project_id = payload.get("project_id")
            image_path = payload.get("image_path")
            channel = bot.get_channel(channel_id)
            if channel:
                embed = discord.Embed.from_dict(embed_data)
                file = None
                if image_path and os.path.exists(image_path):
                    try:
                        file = discord.File(image_path, filename=os.path.basename(image_path))
                        # Ensure embed points to the attachment (in case caller didn't set it)
                        if not embed.image.url or not embed.image.url.startswith("attachment://"):
                            embed.set_image(url=f"attachment://{os.path.basename(image_path)}")
                    except Exception as e:
                        logger.warning(f"send_sub_project_embed: failed to attach image {image_path}: {e}")
                msg = await channel.send(embed=embed, file=file) if file else await channel.send(embed=embed)
                # Report message_id back to NexusData
                if project_id:
                    await asyncio.to_thread(bot_data.set_sub_project_message_id, project_id, str(msg.id))
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
                    await asyncio.to_thread(bot_data.set_document_message_id, document_id, str(msg.id), str(channel_id))
                return True, None

        elif action_type == "edit_embed":
            # Edit an existing embed message — supports attachment image as well
            channel_id = int(payload.get("channel_id", 0))
            message_id = payload.get("message_id")
            embed_data = payload.get("embed", {})
            image_path = payload.get("image_path")
            channel = bot.get_channel(channel_id)
            if channel and message_id:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    embed = discord.Embed.from_dict(embed_data)
                    file = None
                    if image_path and os.path.exists(image_path):
                        try:
                            file = discord.File(image_path, filename=os.path.basename(image_path))
                            if not embed.image.url or not embed.image.url.startswith("attachment://"):
                                embed.set_image(url=f"attachment://{os.path.basename(image_path)}")
                        except Exception as e:
                            logger.warning(f"edit_embed: failed to attach image {image_path}: {e}")
                    if file:
                        # discord.py 2.x: edit with new attachment via attachments/file param
                        try:
                            await msg.edit(embed=embed, attachments=[file])
                        except TypeError:
                            await msg.edit(embed=embed, attachments=[file])
                    else:
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
    await asyncio.to_thread(bot_data.set_bot_config, "forward_channel_id", str(channel.id))
    await ctx.send(f"✅ Пересылка сообщений настроена в {channel.mention}")


@bot.command(name="setrating")
@commands.has_permissions(administrator=True)
async def cmd_set_rating(ctx, channel: discord.TextChannel):
    """!setrating #channel - Set rating publication channel."""
    global RATING_CHANNEL_ID
    RATING_CHANNEL_ID = channel.id
    await asyncio.to_thread(bot_data.set_bot_config, "rating_channel_id", str(channel.id))
    await ctx.send(f"✅ Рейтинг будет публиковаться в {channel.mention}")


@bot.command(name="settraps")
@commands.has_permissions(administrator=True)
async def cmd_set_traps(ctx, channel: discord.TextChannel):
    """!settraps #channel - Set trap notifications channel."""
    await asyncio.to_thread(bot_data.set_bot_config, "trap_channel_id", str(channel.id))
    await ctx.send(f"✅ Уведомления о ловушках будут приходить в {channel.mention}")


@bot.command(name="setsubprojects")
@commands.has_permissions(administrator=True)
async def cmd_set_sub_projects(ctx, channel: discord.TextChannel):
    """!setsubprojects #channel - Set sub-projects publication channel."""
    await asyncio.to_thread(bot_data.set_bot_config, "sub_projects_channel_id", str(channel.id))
    await ctx.send(f"✅ Корабли будут публиковаться в {channel.mention}")


@bot.command(name="setpunishments")
@commands.has_permissions(administrator=True)
async def cmd_set_punishments(ctx, channel: discord.TextChannel):
    """!setpunishments #channel - Set punishments notification channel (all types)."""
    await asyncio.to_thread(bot_data.set_bot_config, "punishment_channel_id", str(channel.id))
    await ctx.send(f"✅ Все наказания будут отправляться в {channel.mention}")


@bot.command(name="setbans")
@commands.has_permissions(administrator=True)
async def cmd_set_bans(ctx, channel: discord.TextChannel):
    """!setbans #channel - Set bans-only notification channel."""
    await asyncio.to_thread(bot_data.set_bot_config, "ban_channel_id", str(channel.id))
    await ctx.send(f"✅ Уведомления о банах будут приходить в {channel.mention}")


@bot.command(name="setdispatch1")
@commands.has_permissions(administrator=True)
async def cmd_set_dispatch1(ctx, channel: discord.TextChannel):
    """!setdispatch1 #channel - Set dispatch channel for ASPER1."""
    await asyncio.to_thread(bot_data.set_bot_config, "dispatch_channel_asper1_id", str(channel.id))
    await ctx.send(f"✅ Диспетчерская ASPER1 будет в {channel.mention}")


@bot.command(name="setdispatch2")
@commands.has_permissions(administrator=True)
async def cmd_set_dispatch2(ctx, channel: discord.TextChannel):
    """!setdispatch2 #channel - Set dispatch channel for ASPER2."""
    await asyncio.to_thread(bot_data.set_bot_config, "dispatch_channel_asper2_id", str(channel.id))
    await ctx.send(f"✅ Диспетчерская ASPER2 будет в {channel.mention}")


@bot.command(name="setroundstatus1")
@commands.has_permissions(administrator=True)
async def cmd_set_round_status1(ctx, channel: discord.TextChannel):
    """!setroundstatus1 #channel - Set live round-status channel for ASPER1."""
    await asyncio.to_thread(bot_data.set_bot_config, "round_status_channel_asper1_id", str(channel.id))
    await ctx.send(f"✅ Статус раунда ASPER1 будет в {channel.mention}")
    await refresh_round_status_embed("ASPER1")


@bot.command(name="setroundstatus2")
@commands.has_permissions(administrator=True)
async def cmd_set_round_status2(ctx, channel: discord.TextChannel):
    """!setroundstatus2 #channel - Set live round-status channel for ASPER2."""
    await asyncio.to_thread(bot_data.set_bot_config, "round_status_channel_asper2_id", str(channel.id))
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

class _PendingTicketClose:
    """Ownership token for one delayed close operation.

    The token is inserted into ``_pending_close`` before the command performs
    its first await.  That makes the check/reserve step atomic on the bot event
    loop, while still allowing a second ``/close`` (or a staff reply) to cancel
    an operation whose asyncio task has not been created yet.
    """

    __slots__ = ("task", "cancelled")

    def __init__(self):
        self.task: Optional[asyncio.Task] = None
        self.cancelled = False


# pending_close[channel_id] = _PendingTicketClose
_pending_close: dict[str, _PendingTicketClose] = {}


def _reserve_pending_ticket_close(channel_id: str) -> Optional[_PendingTicketClose]:
    """Reserve a channel synchronously, or toggle/cancel its current close."""
    existing = _pending_close.pop(channel_id, None)
    if existing is not None:
        existing.cancelled = True
        if existing.task is not None and not existing.task.done():
            existing.task.cancel()
        return None

    state = _PendingTicketClose()
    _pending_close[channel_id] = state
    return state


def _release_pending_ticket_close(channel_id: str, state: _PendingTicketClose) -> None:
    """Release only our own reservation; never remove a newer close request."""
    if _pending_close.get(channel_id) is state:
        _pending_close.pop(channel_id, None)


def _pending_ticket_close_is_active(channel_id: str, state: _PendingTicketClose) -> bool:
    return not state.cancelled and _pending_close.get(channel_id) is state
# Suppresses the manual-delete handler while an evidence-first close owns the
# channel. The helper records SQL closure before removing the marker.
_ticket_channels_closing: set[int] = set()


def _parse_mute_duration(value: str) -> timedelta:
    """Parse compact Russian durations: 1с, 30м, 2нед3д, 1ч30м, and ASCII c."""
    import re

    units = {"с": 1, "c": 1, "м": 60, "ч": 3600, "д": 86400,
             "нед": 7 * 86400, "мес": 30 * 86400, "г": 365 * 86400}
    normalized = (value or "").strip().lower().replace(" ", "")
    parts = re.findall(r"(\d+)(нед|мес|[сcмчдг])", normalized)
    if not parts or "".join(f"{number}{unit}" for number, unit in parts) != normalized:
        raise ValueError("Используйте формат вроде 1ч30м, 2нед3д или 1д")
    seconds = sum(int(number) * units[unit] for number, unit in parts)
    if seconds <= 0 or seconds > 3650 * 86400:
        raise ValueError("Допустимая длительность: от 1 секунды до 10 лет")
    return timedelta(seconds=seconds)


async def _user_has_mute_permission(user) -> bool:
    if getattr(user.guild_permissions, "administrator", False):
        return True
    raw = (await asyncio.to_thread(bot_data.get_bot_config)).get("mute_manager_role_ids", "")
    allowed = {x.strip() for x in raw.split(",") if x.strip()}
    return bool(allowed & {str(r.id) for r in getattr(user, "roles", [])})


async def _restrict_voice_on_mute(member: discord.Member, reason: str):
    """п.7: ограничить доступ в голосовые каналы при муте — server mute + deny connect/speak."""
    guild = member.guild
    if not guild:
        return
    # server-mute если уже в войсе
    try:
        if member.voice and member.voice.channel:
            await member.edit(mute=True, reason=f"Мут: {reason}"[:512])
    except Exception as e:
        logger.warning(f"Failed to server-mute {member}: {e}")
    # deny на все голосовые каналы (включая будущие — cover через пермишены)
    for ch in guild.voice_channels:
        try:
            await ch.set_permissions(member, connect=False, speak=False, reason=f"Мут: {reason}"[:512])
        except Exception as e:
            logger.warning(f"Failed to set voice overwrite for {member} in {ch}: {e}")


async def _restore_voice_on_unmute(member: discord.Member, reason: str):
    guild = member.guild
    if not guild:
        return
    try:
        if member.voice and member.voice.channel:
            await member.edit(mute=False, reason=f"Размут: {reason}"[:512] if reason else "Размут")
    except Exception as e:
        logger.warning(f"Failed to server-unmute {member}: {e}")
    for ch in guild.voice_channels:
        try:
            # снимаем персональный оверрайт (возврат к ролям по умолчанию)
            await ch.set_permissions(member, overwrite=None, reason=f"Размут: {reason}"[:512] if reason else "Размут")
        except Exception as e:
            logger.warning(f"Failed to clear voice overwrite for {member} in {ch}: {e}")


async def _send_mute_notification(channel, title: str, target, moderator, reason: str, expires_at: Optional[datetime] = None):
    config = await asyncio.to_thread(bot_data.get_bot_config)
    # Отправляем и в punishment_channel_id, и в ban_channel_id (если назначены) — требование п.4
    channel_ids = []
    for key in ("punishment_channel_id", "ban_channel_id"):
        cid = config.get(key)
        if cid:
            try:
                cid_int = int(cid)
                if cid_int not in channel_ids:
                    channel_ids.append(cid_int)
            except (ValueError, TypeError):
                continue
    if not channel_ids:
        return
    embed = discord.Embed(title=title, color=0xe67e22)
    embed.add_field(name="Нарушитель", value=target.mention, inline=False)
    embed.add_field(name="Выдал", value=moderator.mention, inline=False)
    if expires_at:
        ts = int(expires_at.replace(tzinfo=timezone.utc).timestamp()) if expires_at.tzinfo is None else int(expires_at.timestamp())
        embed.add_field(name="До", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
    # причина обязательна для мута (п.2 и п.4) — всегда выводим
    embed.add_field(name="Причина", value=(reason or "Не указана")[:1024], inline=False)
    for cid in channel_ids:
        target_channel = bot.get_channel(cid)
        if target_channel:
            try:
                await target_channel.send(embed=embed)
            except Exception as e:
                logger.warning(f"_send_mute_notification failed for channel {cid}: {e}")


async def _create_ticket_channel(interaction: discord.Interaction, ticket_type: str):
    """Create a ticket channel directly (no modal). Called from button handlers."""
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    user = interaction.user
    is_admin = user.guild_permissions.administrator if hasattr(user, 'guild_permissions') else False

    # Check: already has open ticket in this category
    open_tickets = await asyncio.to_thread(bot_data.get_open_tickets)
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

    config = await asyncio.to_thread(bot_data.get_tickets_config)
    bot_config = await asyncio.to_thread(bot_data.get_bot_config)
    if not config:
        await interaction.followup.send("Ошибка загрузки конфигурации", ephemeral=True)
        return

    # Кулдаун повторного создания (п.9)
    tconf_check = (config.get("types", {}) or {}).get(ticket_type, {})
    if tconf_check.get("allow_cooldown"):
        expires = await asyncio.to_thread(bot_data.check_ticket_cooldown, str(user.id), ticket_type)
        if expires:
            ts = int(expires.replace(tzinfo=timezone.utc).timestamp()) if expires.tzinfo is None else int(expires.timestamp())
            await interaction.followup.send(f"❌ У вас кулдаун на создание обращений этого типа до <t:{ts}:F> (<t:{ts}:R>).", ephemeral=True)
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

    try:
        ticket_id = await asyncio.to_thread(
            bot_data.create_ticket,
            ticket_type=ticket_type,
            title=title,
            creator_discord_id=str(user.id),
            creator_username=str(user),
            creator_display_name=user.display_name,
            discord_channel_id=str(channel.id),
        )
    except Exception as e:
        logger.exception("Failed to create ticket record for Discord channel %s", channel.id)
        # A channel without its SQL owner can never be archived or shown on
        # the site. Remove it immediately instead of creating a misleading
        # untracked ticket whose evidence will later disappear.
        try:
            await channel.delete(reason="NexusData ticket DB registration failed")
        except Exception:
            logger.exception(
                "Could not remove untracked ticket channel %s after DB failure",
                channel.id,
            )
        await interaction.followup.send(
            "❌ Не удалось зарегистрировать обращение. Канал не был открыт; попробуйте ещё раз позже.",
            ephemeral=True,
        )
        return

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

    msg_content = f"{user.mention} {silent_pings}" if silent_pings.strip() else user.mention
    view = TicketCloseView(ticket_id=ticket_id, channel=channel)
    await channel.send(content=msg_content, embed=embed, view=view)

    await interaction.followup.send(f"✅ Обращение создано: {channel.mention}", ephemeral=True)
    await refresh_ticket_status()


async def _close_ticket_with_retry(
    ticket_id,
    closed_by: str,
    *,
    archive_state: str = "complete",
) -> bool:
    """One retry, then give up — never delete the ticket channel unless this
    actually succeeded. A stuck-open channel is recoverable; a deleted
    channel with a still-open site-side Ticket row ("phantom ticket") is
    not."""
    for _ in range(2):
        try:
            if await asyncio.to_thread(
                bot_data.close_ticket,
                ticket_id,
                closed_by,
                evidence_archive_confirmed=True,
                archive_state=archive_state,
            ):
                return True
        except Exception as e:
            logger.warning(f"close_ticket({ticket_id}) failed: {e}")
    return False


class TicketCloseView(discord.ui.View):
    def __init__(self, ticket_id=None, channel=None):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.channel = channel

    @discord.ui.button(label="Закрыть обращение", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_btn")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel

        # self.ticket_id живёт только в памяти ЭТОГО экземпляра View. bot.add_view
        # (register_persistent_views ниже) регистрирует на старте ОБЩИЙ экземпляр
        # без ticket_id — он и подхватывает клики по кнопкам старых сообщений
        # (отправленных в прошлых запусках бота), поэтому self.ticket_id там
        # всегда None. Канал, из которого пришло взаимодействие, всегда известен
        # и не зависит от рестартов — ищем тикет по нему, если своего id нет.
        ticket_id = self.ticket_id
        if not ticket_id:
            ticket = await asyncio.to_thread(bot_data.get_ticket_by_channel, str(channel.id))
            ticket_id = ticket["id"] if ticket else None

        if not ticket_id:
            await interaction.followup.send(
                "⚠️ Не удалось закрыть обращение на сайте — канал НЕ удалён, попробуйте ещё раз позже.",
                ephemeral=True,
            )
            return

        try:
            await _freeze_reconcile_and_delete_ticket_channel(
                ticket_id,
                channel,
                reason=f"Ticket closed by {interaction.user}",
                closed_by=interaction.user.display_name,
            )
        except Exception as exc:
            logger.exception(
                "Ticket #%s channel retained because evidence-first close failed: %s",
                ticket_id,
                type(exc).__name__,
            )
            await interaction.followup.send(
                "⚠️ Не удалось надёжно сохранить историю и вложения — канал НЕ удалён. "
                "Повторите закрытие позже.",
                ephemeral=True,
            )
            return

        await interaction.followup.send("🔒 Тикет закрыт, история и файлы сохранены.", ephemeral=True)
        await refresh_ticket_status()


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


async def _user_has_ticket_staff_permission(user, guild, config: dict) -> bool:
    """Администратор сервера или обладатель одной из настроенных ролей
    персонала тикетов — общая проверка для /close и /ticket_add.

    Роли берутся из ДВУХ мест: явного config["manager_role_ids"] (настраивается
    на сайте, вкладка Discord бот → «Настройки тикетов» — это ЕДИНЫЙ список
    именно для управления тикетами) и, для обратной совместимости с уже
    настроенными типами, ping_roles каждого типа обращения (раньше /close
    проверял только их — те, у кого есть хоть одна из ролей уведомления по
    любому типу, могут закрывать любой тикет; сохраняем это поведение, а не
    сужаем его молча)."""
    if hasattr(user, "guild_permissions") and user.guild_permissions.administrator:
        return True
    if not config or not guild:
        return False

    role_ids = set(config.get("manager_role_ids") or [])
    for tconf in (config.get("types") or {}).values():
        role_ids.update(tconf.get("ping_roles") or [])

    user_role_ids = {r.id for r in getattr(user, "roles", [])}
    for role_id_str in role_ids:
        try:
            if int(role_id_str) in user_role_ids:
                return True
        except (ValueError, TypeError):
            continue
    return False


async def list_ticket_members(channel_id: str) -> dict:
    """Return current non-bot members who can view the ticket channel.

    Effective channel permissions are the source of truth.  The guild member
    cache is intentionally not populated at startup, so members are streamed
    from Discord and checked against the channel's role and member overwrites.
    """
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return {"status": "error", "code": "guild_not_found", "detail": "Бот не подключён к Discord-серверу"}

    try:
        channel = bot.get_channel(int(channel_id))
    except (TypeError, ValueError):
        return {"status": "error", "code": "channel_not_found", "detail": "Некорректный Discord-канал тикета"}
    if not channel:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except discord.NotFound:
            return {"status": "error", "code": "channel_not_found", "detail": "Discord-канал тикета уже удалён"}
        except discord.Forbidden:
            return {"status": "error", "code": "channel_forbidden", "detail": "Бот не может открыть Discord-канал тикета"}
        except Exception as exc:
            logger.warning("Failed to fetch ticket channel %s for member listing: %s", channel_id, exc)
            return {"status": "error", "code": "channel_lookup_failed", "detail": "Discord не ответил при проверке канала"}

    members = []
    seen_ids = set()

    def add_member(member) -> None:
        if getattr(member, "bot", False) or member.id in seen_ids:
            return
        if not channel.permissions_for(member).view_channel:
            return
        seen_ids.add(member.id)
        display_avatar = getattr(member, "display_avatar", None)
        avatar_url = str(display_avatar.url) if display_avatar and getattr(display_avatar, "url", None) else None
        members.append({
            "discord_id": str(member.id),
            "username": getattr(member, "name", None),
            "display_name": getattr(member, "display_name", None),
            "avatar_url": avatar_url,
        })

    fetch_members = getattr(guild, "fetch_members", None)
    try:
        if fetch_members is not None:
            async for member in fetch_members(limit=None):
                add_member(member)
        else:
            for member in getattr(guild, "members", []):
                add_member(member)
    except discord.Forbidden:
        return {"status": "error", "code": "members_forbidden", "detail": "Бот не может получить участников Discord-сервера"}
    except discord.HTTPException as exc:
        logger.warning("Failed to fetch guild members for ticket %s: %s", channel_id, exc)
        return {"status": "error", "code": "members_lookup_failed", "detail": "Discord не ответил при получении участников тикета"}
    except Exception as exc:
        logger.warning("Failed to list effective ticket members %s: %s", channel_id, exc)
        return {"status": "error", "code": "members_lookup_failed", "detail": "Не удалось проверить участников тикета в Discord"}

    # chunk_guilds_at_startup=False means a direct member overwrite may be
    # represented by discord.Object instead of a cached Member. Resolve those
    # explicit targets as a fallback; role-based access was handled above.
    for target in getattr(channel, "overwrites", {}):
        if isinstance(target, discord.Role) or getattr(target, "id", None) == getattr(guild, "id", None):
            continue
        target_id = getattr(target, "id", None)
        if target_id is None or target_id in seen_ids:
            continue
        member = target if getattr(target, "display_name", None) is not None else guild.get_member(target_id)
        if not member:
            try:
                member = await guild.fetch_member(target_id)
            except (discord.NotFound, discord.Forbidden):
                continue
            except Exception as exc:
                logger.warning("Failed to resolve direct ticket member %s: %s", target_id, exc)
                continue
        add_member(member)

    return {"status": "ok", "members": members}


async def add_ticket_member(
    channel_id: str,
    member_id: str,
    added_by: str,
    ticket_id: int,
) -> dict:
    """Grant one live Discord member access to an open ticket channel.

    This is deliberately request/response based: the web panel must not write
    a successful audit record before Discord confirms the permission change.
    """
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return {"status": "error", "code": "guild_not_found", "detail": "Бот не подключён к Discord-серверу"}

    try:
        discord_member_id = int(member_id)
    except (TypeError, ValueError):
        return {"status": "error", "code": "member_not_found", "detail": "Некорректный Discord ID участника"}

    member = guild.get_member(discord_member_id)
    if not member:
        try:
            member = await guild.fetch_member(discord_member_id)
        except discord.NotFound:
            return {"status": "error", "code": "member_not_found", "detail": "Участник больше не состоит на Discord-сервере"}
        except discord.Forbidden:
            return {"status": "error", "code": "member_forbidden", "detail": "Бот не может проверить участника на Discord-сервере"}
        except Exception as exc:
            logger.warning("Failed to fetch ticket member %s: %s", member_id, exc)
            return {"status": "error", "code": "member_lookup_failed", "detail": "Discord не ответил при проверке участника"}

    if getattr(member, "bot", False):
        return {"status": "error", "code": "member_is_bot", "detail": "Ботов нельзя добавлять в тикеты"}

    channel = bot.get_channel(int(channel_id))
    if not channel:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except discord.NotFound:
            return {"status": "error", "code": "channel_not_found", "detail": "Discord-канал тикета уже удалён"}
        except discord.Forbidden:
            return {"status": "error", "code": "channel_forbidden", "detail": "Бот не может открыть Discord-канал тикета"}
        except Exception as exc:
            logger.warning("Failed to fetch ticket channel %s: %s", channel_id, exc)
            return {"status": "error", "code": "channel_lookup_failed", "detail": "Discord не ответил при проверке канала"}

    overwrite = channel.overwrites_for(member)
    if (
        overwrite.view_channel is True
        and overwrite.send_messages is True
        and overwrite.read_message_history is True
    ):
        return {"status": "already_present", "member_id": str(member.id)}

    try:
        await channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            reason=f"Ticket #{ticket_id}: added from NexusData by {added_by}",
        )
    except discord.Forbidden:
        return {"status": "error", "code": "channel_forbidden", "detail": "У бота нет прав менять доступ к Discord-каналу"}
    except discord.NotFound:
        return {"status": "error", "code": "channel_not_found", "detail": "Discord-канал тикета уже удалён"}
    except Exception as exc:
        logger.warning("Failed to grant ticket channel access for %s: %s", member_id, exc)
        return {"status": "error", "code": "permission_failed", "detail": "Не удалось выдать доступ в Discord"}

    announcement = f"Участник {member.mention} добавлен в тикет администратором {added_by}."
    try:
        message = await channel.send(
            announcement,
            allowed_mentions=discord.AllowedMentions(
                users=[member], roles=False, everyone=False, replied_user=False
            ),
        )
    except discord.Forbidden:
        return {"status": "partial", "detail": "Доступ выдан, но бот не смог отправить сообщение в тикет"}
    except Exception as exc:
        logger.warning("Failed to announce ticket member %s: %s", member_id, exc)
        return {"status": "partial", "detail": "Доступ выдан, но сообщение в тикет не отправлено"}

    saved = await asyncio.to_thread(
        bot_data.add_ticket_message,
        ticket_id=ticket_id,
        author_discord_id="system",
        author_username=added_by,
        author_display_name=f"[ND] {added_by}",
        content=announcement,
        discord_message_id=str(message.id),
    )
    if not saved:
        return {"status": "partial", "detail": "Доступ и сообщение отправлены, но локальная история тикета не обновилась"}

    return {
        "status": "ok",
        "member_id": str(member.id),
        "message_id": str(message.id),
        "announcement": announcement,
    }


async def remove_ticket_member(
    channel_id: str,
    member_id: str,
    removed_by: str,
    ticket_id: int,
) -> dict:
    """Remove one member-specific access overwrite from an open ticket channel."""
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return {"status": "error", "code": "guild_not_found", "detail": "Бот не подключён к Discord-серверу"}

    try:
        discord_member_id = int(member_id)
    except (TypeError, ValueError):
        return {"status": "error", "code": "member_not_found", "detail": "Некорректный Discord ID участника"}

    member = guild.get_member(discord_member_id)
    if not member:
        try:
            member = await guild.fetch_member(discord_member_id)
        except discord.NotFound:
            return {"status": "error", "code": "member_not_found", "detail": "Участник больше не состоит на Discord-сервере"}
        except discord.Forbidden:
            return {"status": "error", "code": "member_forbidden", "detail": "Бот не может проверить участника на Discord-сервере"}
        except Exception as exc:
            logger.warning("Failed to fetch ticket member %s for removal: %s", member_id, exc)
            return {"status": "error", "code": "member_lookup_failed", "detail": "Discord не ответил при проверке участника"}

    if getattr(member, "bot", False):
        return {"status": "error", "code": "member_is_bot", "detail": "Ботов нельзя удалять из тикетов"}
    if getattr(guild, "owner_id", None) == member.id:
        return {"status": "error", "code": "member_unremovable", "detail": "Нельзя убрать доступ у владельца Discord-сервера"}
    if getattr(getattr(member, "guild_permissions", None), "administrator", False):
        return {"status": "error", "code": "member_unremovable", "detail": "Нельзя убрать доступ у администратора Discord-сервера"}

    channel = bot.get_channel(int(channel_id))
    if not channel:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except discord.NotFound:
            return {"status": "error", "code": "channel_not_found", "detail": "Discord-канал тикета уже удалён"}
        except discord.Forbidden:
            return {"status": "error", "code": "channel_forbidden", "detail": "Бот не может открыть Discord-канал тикета"}
        except Exception as exc:
            logger.warning("Failed to fetch ticket channel %s for removal: %s", channel_id, exc)
            return {"status": "error", "code": "channel_lookup_failed", "detail": "Discord не ответил при проверке канала"}

    try:
        has_access = channel.permissions_for(member).view_channel
    except Exception as exc:
        logger.warning("Failed to check ticket access for %s: %s", member_id, exc)
        return {"status": "error", "code": "permission_lookup_failed", "detail": "Не удалось проверить доступ участника к тикету"}
    if not has_access:
        return {"status": "already_absent", "member_id": str(member.id)}

    try:
        await channel.set_permissions(
            member,
            view_channel=False,
            send_messages=False,
            read_message_history=False,
            attach_files=False,
            reason=f"Ticket #{ticket_id}: removed from NexusData by {removed_by}",
        )
    except discord.Forbidden:
        return {"status": "error", "code": "channel_forbidden", "detail": "У бота нет прав менять доступ к Discord-каналу"}
    except discord.NotFound:
        return {"status": "error", "code": "channel_not_found", "detail": "Discord-канал тикета уже удалён"}
    except Exception as exc:
        logger.warning("Failed to remove ticket channel access for %s: %s", member_id, exc)
        return {"status": "error", "code": "permission_failed", "detail": "Не удалось убрать доступ в Discord"}

    announcement = f"Участник {member.display_name} удалён из тикета администратором {removed_by}."
    try:
        message = await channel.send(
            announcement,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.Forbidden:
        return {"status": "partial", "detail": "Доступ убран, но бот не смог отправить сообщение в тикет"}
    except Exception as exc:
        logger.warning("Failed to announce ticket member removal %s: %s", member_id, exc)
        return {"status": "partial", "detail": "Доступ убран, но сообщение в тикет не отправлено"}

    try:
        saved = await asyncio.to_thread(
            bot_data.add_ticket_message,
            ticket_id=ticket_id,
            author_discord_id="system",
            author_username=removed_by,
            author_display_name=f"[ND] {removed_by}",
            content=announcement,
            discord_message_id=str(message.id),
        )
    except Exception as exc:
        logger.warning("Failed to save ticket member removal %s: %s", member_id, exc)
        saved = False
    if not saved:
        return {"status": "partial", "detail": "Доступ убран, но локальная история тикета не обновилась"}

    return {
        "status": "ok",
        "member_id": str(member.id),
        "message_id": str(message.id),
        "announcement": announcement,
    }


@bot.tree.command(name="mute", description="Запретить пользователю писать вне тикетов")
@app_commands.describe(member="Нарушитель", duration="Например: 1ч30м, 2нед3д", reason="Причина")
async def slash_mute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
    if not await _user_has_mute_permission(interaction.user):
        await interaction.response.send_message("❌ У вас нет прав на мут.", ephemeral=True)
        return
    try:
        delta = _parse_mute_duration(duration)
    except ValueError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return
    expires_at = datetime.utcnow() + delta
    await asyncio.to_thread(bot_data.set_mute, str(member.id), expires_at, {
        "id": str(interaction.user.id), "name": interaction.user.display_name,
    }, reason)
    await asyncio.to_thread(bot_data.log_discord_command, "mute", {
        "moderator_id": str(interaction.user.id), "moderator": interaction.user.display_name,
        "target_id": str(member.id), "target": member.display_name, "duration": duration,
        "expires_at": expires_at.isoformat(), "reason": reason,
    })
    ts = int(expires_at.replace(tzinfo=timezone.utc).timestamp()) if expires_at.tzinfo is None else int(expires_at.timestamp())
    await interaction.response.send_message(f"🔇 {member.mention} получил(а) мут до <t:{ts}:F> (<t:{ts}:R>). Причина: {reason}")
    try:
        await _restrict_voice_on_mute(member, reason)
    except Exception as exc:
        logger.warning(f"Failed to restrict voice on mute for {member}: {exc}")
    try:
        await _send_mute_notification(interaction.channel, "🔇 Мут", member, interaction.user, reason, expires_at)
    except Exception as exc:
        logger.warning("Failed to publish mute notification: %s", exc)


@bot.tree.command(name="unmute", description="Снять мут с пользователя")
@app_commands.describe(member="Пользователь", reason="Причина снятия мута")
async def slash_unmute(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
    if not await _user_has_mute_permission(interaction.user):
        await interaction.response.send_message("❌ У вас нет прав на снятие мута.", ephemeral=True)
        return
    removed = await asyncio.to_thread(bot_data.remove_mute, str(member.id))
    if not removed:
        await interaction.response.send_message("У этого пользователя нет активного мута.", ephemeral=True)
        return
    await asyncio.to_thread(bot_data.log_discord_command, "unmute", {
        "moderator_id": str(interaction.user.id), "moderator": interaction.user.display_name,
        "target_id": str(member.id), "target": member.display_name, "reason": reason or "",
    })
    await interaction.response.send_message(f"🔊 Мут с {member.mention} снят." + (f" Причина: {reason}" if reason else ""))
    try:
        await _restore_voice_on_unmute(member, reason or "")
    except Exception as exc:
        logger.warning(f"Failed to restore voice on unmute for {member}: {exc}")
    try:
        await _send_mute_notification(interaction.channel, "🔊 Мут снят", member, interaction.user, reason or "")
    except Exception as exc:
        logger.warning("Failed to publish unmute notification: %s", exc)


@bot.tree.command(name="close", description="Закрыть тикет через указанное время (только для администраторов)")
@app_commands.describe(
    minutes="Через сколько минут закрыть (0 = через 15 секунд)",
    answer="Итоговый ответ по обращению",
    cooldown_days="Кулдаун повторного создания тикета этого типа (дней, 0 = без кулдауна)",
    force="Закрыть, даже если часть вложений не удалось сохранить (архив будет помечен неполным)",
)
async def slash_close_ticket(
    interaction: discord.Interaction,
    minutes: int,
    answer: str,
    cooldown_days: int = 0,
    force: bool = False,
):
    """Slash command /close <minutes> <answer> — only for ping roles in ticket channels."""
    channel = interaction.channel

    # Must be a ticket channel
    if not channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ Эта команда доступна только в тикет-каналах.", ephemeral=True)
        return

    user = interaction.user
    config = await asyncio.to_thread(bot_data.get_tickets_config)
    has_perm = await _user_has_ticket_staff_permission(user, interaction.guild, config)

    if not has_perm:
        await interaction.response.send_message("❌ У вас нет прав для закрытия тикета.", ephemeral=True)
        return

    await asyncio.to_thread(bot_data.log_discord_command, "close", {
        "moderator_id": str(user.id), "moderator": user.display_name,
        "channel_id": str(channel.id), "minutes": minutes, "answer": answer,
    })

    channel_id_str = str(channel.id)

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

    # Find ticket by channel
    ticket = await asyncio.to_thread(bot_data.get_ticket_by_channel, channel_id_str)
    ticket_id = ticket["id"] if ticket else None
    ticket_title = (ticket or {}).get("title", "Обращение")
    creator_discord_id = (ticket or {}).get("creator_discord_id")

    # Reserve synchronously before the next await. Two concurrent commands can
    # therefore never both schedule a close for the same channel. A second
    # command preserves the established toggle behaviour and cancels even the
    # short setup phase before the first asyncio task exists.
    pending_state = _reserve_pending_ticket_close(channel_id_str)
    if pending_state is None:
        await interaction.response.send_message(
            "⏸️ Закрытие тикета приостановлено.",
            ephemeral=False,
        )
        return

    close_notice = (
        f"🔒 Администратор закрыл тикет, он будет удалён через **{time_str}**...\n"
        f"💬 Ответ: {answer}"
    )
    try:
        await interaction.response.send_message(close_notice, ephemeral=False)
    except BaseException:
        _release_pending_ticket_close(channel_id_str, pending_state)
        raise

    # Persist the closing answer under the real moderator profile. Bot
    # messages are now archived too, so bind this row to Discord's response
    # ID: whichever arrives first (Gateway event or this write) enriches the
    # same row instead of duplicating the final decision in the transcript.
    if ticket_id:
        try:
            response_message = None
            try:
                response_message = await interaction.original_response()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            await asyncio.to_thread(
                bot_data.add_ticket_message,
                ticket_id=ticket_id,
                author_discord_id=str(user.id),
                author_username=str(user),
                author_display_name=user.display_name,
                author_avatar_url=str(user.display_avatar.url) if user.display_avatar else None,
                content=close_notice,
                discord_message_id=(
                    str(response_message.id) if response_message is not None else None
                ),
                created_at=(
                    response_message.created_at if response_message is not None else None
                ),
                is_bot=True,
            )
        except BaseException:
            _release_pending_ticket_close(channel_id_str, pending_state)
            raise

    async def _do_close():
        await asyncio.sleep(delay_seconds)

        if not ticket_id:
            _release_pending_ticket_close(channel_id_str, pending_state)
            try:
                await channel.send(
                    "⚠️ Не удалось закрыть обращение на сайте — канал НЕ будет удалён автоматически. "
                    "Попробуйте `/close` ещё раз позже."
                )
            except Exception as e:
                logger.warning(f"Failed to warn about failed ticket close in {channel_id_str}: {e}")
            await refresh_ticket_status()
            return

        try:
            await _freeze_reconcile_and_delete_ticket_channel(
                ticket_id,
                channel,
                reason=f"Ticket closed by {user}",
                closed_by=user.display_name,
                force=force,
            )
        except Exception as exc:
            _release_pending_ticket_close(channel_id_str, pending_state)
            logger.exception(
                "Ticket #%s channel retained because evidence-first close failed: %s",
                ticket_id,
                type(exc).__name__,
            )
            # A recoverable gap is worth another attempt; tell the moderator how
            # to close anyway instead of leaving the ticket stuck forever.
            hint = (
                " Если файлы так и не сохраняются — `/close` с параметром `force: True` "
                "закроет обращение и пометит архив неполным."
                if isinstance(exc, TicketArchiveIncomplete)
                else ""
            )
            try:
                await channel.send(
                    "⚠️ Закрытие отменено: не удалось надёжно сохранить историю или вложения. "
                    f"Канал оставлен на месте; повторите `/close` позже.{hint}"
                )
            except Exception:
                pass
            await refresh_ticket_status()
            return

        # Кулдаун повторного создания тикета этого типа (п.9): если allow_cooldown включён для типа
        if cooldown_days and cooldown_days > 0 and ticket and ticket.get("ticket_type"):
            tconf = config.get("types", {}).get(ticket.get("ticket_type"), {})
            if tconf.get("allow_cooldown"):
                try:
                    await asyncio.to_thread(bot_data.set_ticket_cooldown, creator_discord_id, ticket.get("ticket_type"), cooldown_days)
                except Exception as e:
                    logger.warning(f"Failed to set ticket cooldown: {e}")

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
                    if cooldown_days and cooldown_days > 0:
                        dm_embed.add_field(name="Кулдаун", value=f"Повторное обращение этого типа доступно через {cooldown_days} дн.", inline=False)
                    dm_embed.set_footer(text="Сделано за сухарики")
                    await creator.send(embed=dm_embed)
            except Exception as e:
                logger.warning(f"Failed to send DM to ticket creator {creator_discord_id}: {e}")

        _release_pending_ticket_close(channel_id_str, pending_state)
        await refresh_ticket_status()

    if not _pending_ticket_close_is_active(channel_id_str, pending_state):
        return
    task = asyncio.create_task(_do_close())
    pending_state.task = task


@bot.tree.command(name="ticket_add", description="Добавить участника в текущий тикет (только для персонала)")
@app_commands.describe(member="Кого добавить в тикет — можно ввести Discord ID напрямую")
async def slash_ticket_add_member(interaction: discord.Interaction, member: discord.Member):
    """Slash command /ticket_add <member> — доступ по роли (см.
    _user_has_ticket_staff_permission), а не для всех подряд: канал тикета
    приватный именно потому, что в него не должен попадать кто попало."""
    channel = interaction.channel

    if not channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ Эта команда доступна только в тикет-каналах.", ephemeral=True)
        return

    config = await asyncio.to_thread(bot_data.get_tickets_config)
    if not await _user_has_ticket_staff_permission(interaction.user, interaction.guild, config):
        await interaction.response.send_message("❌ У вас нет прав для этой команды.", ephemeral=True)
        return

    try:
        await channel.set_permissions(
            member, view_channel=True, send_messages=True, read_message_history=True,
            reason=f"Добавлен в тикет админом {interaction.user}",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав, чтобы изменить доступ к каналу.", ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Не удалось добавить участника: {e}", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ {member.mention} добавлен(а) в этот тикет.", ephemeral=False)
    await asyncio.to_thread(bot_data.log_discord_command, "ticket_add", {
        "moderator_id": str(interaction.user.id), "moderator": interaction.user.display_name,
        "member_id": str(member.id), "channel_id": str(channel.id),
    })


@bot.tree.command(name="ticket_remove", description="Убрать участника из текущего тикета (только для персонала)")
@app_commands.describe(member="Кого убрать из тикета — нельзя убрать автора обращения")
async def slash_ticket_remove_member(interaction: discord.Interaction, member: discord.Member):
    """Slash command /ticket_remove <member> — обратная операция к
    /ticket_add. Автора тикета убрать нельзя: у него доступ к собственному
    обращению не привилегия, которую отбирают вручную, а гарантия — если
    обращение исчерпано, для этого есть /close, а не выкидывание заявителя
    из его же тикета."""
    channel = interaction.channel

    if not channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ Эта команда доступна только в тикет-каналах.", ephemeral=True)
        return

    config = await asyncio.to_thread(bot_data.get_tickets_config)
    if not await _user_has_ticket_staff_permission(interaction.user, interaction.guild, config):
        await interaction.response.send_message("❌ У вас нет прав для этой команды.", ephemeral=True)
        return

    ticket = await asyncio.to_thread(bot_data.get_ticket_by_channel, str(channel.id))
    if not ticket:
        await interaction.response.send_message(
            "❌ Не удалось определить автора этого тикета (обращение уже закрыто на сайте?) — "
            "команда недоступна, пока это не выяснится.",
            ephemeral=True,
        )
        return
    if ticket.get("creator_discord_id") == str(member.id):
        await interaction.response.send_message(
            "❌ Нельзя убрать из тикета его автора. Если обращение исчерпано — закройте его командой `/close`.",
            ephemeral=True,
        )
        return

    try:
        # overwrite=None стирает персональное разрешение целиком (то самое,
        # что выставляет /ticket_add), а не просто выставляет запрет поверх
        # него — так участник откатывается к обычным правам @everyone/ролей
        # канала, а не получает противоречащий ролям персональный deny.
        await channel.set_permissions(
            member, overwrite=None,
            reason=f"Убран из тикета админом {interaction.user}",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ У бота недостаточно прав, чтобы изменить доступ к каналу.", ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Не удалось убрать участника: {e}", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ {member.mention} убран(а) из этого тикета.", ephemeral=False)
    await asyncio.to_thread(bot_data.log_discord_command, "ticket_remove", {
        "moderator_id": str(interaction.user.id), "moderator": interaction.user.display_name,
        "member_id": str(member.id), "channel_id": str(channel.id),
    })


@bot.command(name="setticketcategory")
@commands.has_permissions(administrator=True)
async def cmd_set_ticket_category(ctx, category_id: str):
    """!setticketcategory <category_id> - Set category for ticket channels."""
    await asyncio.to_thread(bot_data.set_bot_config, "ticket_category_id", category_id)
    await ctx.send(f"✅ Категория для тикетов: {category_id}")


@bot.command(name="setticketstatus")
@commands.has_permissions(administrator=True)
async def cmd_set_ticket_status(ctx, channel: discord.TextChannel):
    """!setticketstatus #channel - Set ticket status channel."""
    await asyncio.to_thread(bot_data.set_bot_config, "ticket_status_channel_id", str(channel.id))
    await ctx.send(f"✅ Статус тикетов будет в {channel.mention}")


_TICKET_ATTACHMENT_CHUNK_SIZE = 256 * 1024
_ticket_attachment_downloads = asyncio.Semaphore(2)


def _attachment_value(attachment, name: str, default=None):
    if isinstance(attachment, dict):
        return attachment.get(name, default)
    return getattr(attachment, name, default)


def _ticket_attachment_base(attachment) -> dict:
    """Serialize public Discord attachment metadata without trusting its name."""
    return {
        "id": str(_attachment_value(attachment, "id", "") or ""),
        "filename": str(_attachment_value(attachment, "filename") or "file"),
        "content_type": _attachment_value(attachment, "content_type"),
        "file_size": int(
            _attachment_value(attachment, "size", _attachment_value(attachment, "file_size", 0)) or 0
        ),
        "width": _attachment_value(attachment, "width"),
        "height": _attachment_value(attachment, "height"),
        "source_url": str(
            _attachment_value(attachment, "url", _attachment_value(attachment, "source_url", "")) or ""
        ),
        "proxy_url": str(_attachment_value(attachment, "proxy_url", "") or ""),
        "storage_key": None,
        "storage_namespace": None,
        "archive_status": "failed",
        "archive_error": None,
        "sha256": None,
        "archived_at": None,
    }


def _existing_attachment(existing: list, attachment):
    attachment_id = str(_attachment_value(attachment, "id", "") or "")
    for candidate in existing or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("id") or candidate.get("discord_attachment_id") or "")
        if attachment_id and candidate_id == attachment_id:
            return candidate
    return None


def _archived_attachment_is_reusable(ticket_id: int, attachment: Optional[dict]) -> bool:
    if not attachment:
        return False
    if attachment.get("archive_status") != "stored":
        return False
    key = attachment.get("storage_key")
    namespace = attachment.get("storage_namespace")
    if not key or not namespace:
        return False
    try:
        return storage_path(ticket_id, namespace, key).is_file()
    except (OSError, ValueError):
        return False


class _TicketAttachmentTooLarge(Exception):
    pass


async def _download_ticket_attachment(
    session,
    attachment,
    destination: Path,
    byte_limit: int,
    quota_reservation,
) -> tuple[int, str]:
    """Download one Discord CDN object incrementally and return size/hash."""
    urls = []
    for candidate in (
        _attachment_value(attachment, "url", _attachment_value(attachment, "source_url")),
        _attachment_value(attachment, "proxy_url"),
    ):
        value = str(candidate or "")
        if value and value not in urls:
            urls.append(value)
    if not urls:
        raise RuntimeError("attachment URL is missing")

    last_error = "download failed"
    part_path = destination.with_name(f".{destination.name}.part")
    for url in urls:
        try:
            part_path.unlink(missing_ok=True)
            digest = hashlib.sha256()
            written = 0
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    last_error = f"Discord CDN returned HTTP {response.status}"
                    continue
                if response.content_length is not None and response.content_length > byte_limit:
                    raise _TicketAttachmentTooLarge()
                async with aiofiles.open(part_path, "wb") as output:
                    async for chunk in response.content.iter_chunked(_TICKET_ATTACHMENT_CHUNK_SIZE):
                        next_written = written + len(chunk)
                        if next_written > byte_limit:
                            raise _TicketAttachmentTooLarge()
                        # Capacity is reserved before bytes reach disk. This
                        # also covers Discord responses without a trustworthy
                        # Content-Length header.
                        quota_reservation.grow_to(next_written)
                        written = next_written
                        digest.update(chunk)
                        await output.write(chunk)
            os.chmod(part_path, 0o600)
            os.replace(part_path, destination)
            return written, digest.hexdigest()
        except _TicketAttachmentTooLarge:
            part_path.unlink(missing_ok=True)
            raise
        except TicketMediaQuotaExceeded:
            part_path.unlink(missing_ok=True)
            raise
        except asyncio.CancelledError:
            # Cancellation during shutdown must not leave a quota-consuming
            # partial which has no corresponding TicketMessage record.
            part_path.unlink(missing_ok=True)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            part_path.unlink(missing_ok=True)
            last_error = f"{type(exc).__name__}: download failed"
    raise RuntimeError(last_error)


async def _archive_ticket_attachments(
    ticket_id: int,
    namespace: str,
    discord_attachments,
    budget: dict,
    existing: Optional[list] = None,
) -> list[dict]:
    """Mirror attachments with per-file/message caps and bounded concurrency."""
    attachments = list(discord_attachments or [])
    if not attachments:
        return []

    per_file_limit = int(settings.ticket_attachment_max_bytes)
    message_limit = int(settings.ticket_attachment_message_max_bytes)
    timeout_seconds = float(settings.ticket_attachment_download_timeout_seconds)
    result = []
    timeout = aiohttp.ClientTimeout(
        total=timeout_seconds,
        connect=min(15.0, timeout_seconds),
        sock_read=timeout_seconds,
    )
    connector = aiohttp.TCPConnector(limit=2)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for attachment in attachments:
            previous = _existing_attachment(existing or [], attachment)
            if _archived_attachment_is_reusable(ticket_id, previous):
                result.append(previous)
                if previous.get("archive_status") == "stored":
                    budget["used"] += int(previous.get("file_size") or 0)
                continue

            metadata = _ticket_attachment_base(attachment)
            declared_size = max(0, int(metadata["file_size"] or 0))
            remaining = message_limit - int(budget.get("used", 0))
            if declared_size > per_file_limit:
                metadata["archive_status"] = "too_large"
                metadata["archive_error"] = "per_file_limit"
                result.append(metadata)
                continue
            if remaining <= 0 or (declared_size and declared_size > remaining):
                metadata["archive_status"] = "too_large"
                metadata["archive_error"] = "message_limit"
                result.append(metadata)
                continue

            key = new_storage_key(metadata["filename"])
            metadata["storage_key"] = key
            metadata["storage_namespace"] = namespace
            quota_reservation = None
            try:
                # The first reservation may scan an existing multi-gigabyte
                # archive; keep that filesystem walk off Discord's event loop.
                quota_reservation = await asyncio.to_thread(
                    reserve_ticket_media,
                    ticket_id,
                    # Discord's attachment ``size`` is metadata, not a
                    # trustworthy Content-Length. In practice the CDN body
                    # can be larger (notably for snapshots/forwarded media).
                    # Reserve an unknown-length stream so the lease may grow
                    # up to the configured per-file and remaining disk quota.
                    0,
                )
                destination = storage_path(ticket_id, namespace, key)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Re-resolve after mkdir so a pre-existing symlink below the
                # configured media root cannot redirect the final write.
                destination = storage_path(ticket_id, namespace, key)
                async with _ticket_attachment_downloads:
                    written, digest = await _download_ticket_attachment(
                        session,
                        attachment,
                        destination,
                        min(per_file_limit, remaining),
                        quota_reservation,
                    )
                quota_reservation.commit(written)
                metadata["file_size"] = written
                metadata["archive_status"] = "stored"
                metadata["archive_error"] = None
                metadata["sha256"] = digest
                metadata["archived_at"] = datetime.now(timezone.utc).isoformat()
                budget["used"] += written
            except _TicketAttachmentTooLarge:
                metadata["storage_key"] = None
                metadata["storage_namespace"] = None
                metadata["archive_status"] = "too_large"
                metadata["archive_error"] = "size_limit_exceeded_while_downloading"
            except TicketMediaQuotaExceeded as exc:
                metadata["storage_key"] = None
                metadata["storage_namespace"] = None
                metadata["archive_status"] = "quota_exceeded"
                metadata["archive_error"] = f"{exc.scope}_quota"
                logger.warning(
                    "Ticket #%s attachment %s not archived: %s quota "
                    "(%s used + %s requested > %s bytes)",
                    ticket_id,
                    metadata.get("id") or metadata.get("filename"),
                    exc.scope,
                    exc.used_bytes,
                    exc.requested_bytes,
                    exc.limit_bytes,
                )
            except Exception as exc:
                metadata["storage_key"] = None
                metadata["storage_namespace"] = None
                metadata["archive_status"] = "failed"
                metadata["archive_error"] = f"{type(exc).__name__}: archive failed"[:200]
                logger.warning(
                    "Ticket #%s attachment %s archive failed: %s",
                    ticket_id,
                    metadata.get("id") or metadata.get("filename"),
                    type(exc).__name__,
                )
            finally:
                if quota_reservation is not None:
                    quota_reservation.release()
            result.append(metadata)
    return result


def _serialize_ticket_sticker(sticker) -> dict:
    sticker_format = getattr(sticker, "format", None)
    return {
        "id": str(getattr(sticker, "id", "") or ""),
        "name": str(getattr(sticker, "name", "") or ""),
        "url": str(getattr(sticker, "url", "") or ""),
        "format": getattr(sticker_format, "value", sticker_format),
    }


def _serialize_ticket_author(user) -> dict:
    serialized = _serialize_author(user)
    return {
        "discord_id": serialized.get("id"),
        "username": serialized.get("username"),
        "display_name": serialized.get("display_name"),
        "avatar_url": serialized.get("avatar_url"),
    }


def _reference_source(reference) -> dict:
    source = {
        "message_id": str(reference.message_id) if getattr(reference, "message_id", None) else None,
        "channel_id": str(reference.channel_id) if getattr(reference, "channel_id", None) else None,
        "guild_id": str(reference.guild_id) if getattr(reference, "guild_id", None) else None,
    }
    try:
        source["jump_url"] = str(reference.jump_url)
    except (AttributeError, TypeError, ValueError):
        source["jump_url"] = None
    return source


async def _resolve_ticket_reference(reference):
    """Best-effort resolution; inaccessible forwards deliberately stay anonymous."""
    resolved = getattr(reference, "resolved", None)
    if isinstance(resolved, discord.Message):
        return resolved, resolved.channel

    channel_id = getattr(reference, "channel_id", None)
    message_id = getattr(reference, "message_id", None)
    if not channel_id:
        return None, None
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, TypeError, ValueError):
            return None, None
    if not message_id or not hasattr(channel, "fetch_message"):
        return None, channel
    try:
        return await channel.fetch_message(int(message_id)), channel
    except (discord.Forbidden, discord.NotFound, discord.HTTPException, TypeError, ValueError):
        return None, channel


def _existing_forward_attachments(existing_reference: Optional[dict], index: int) -> list:
    if not isinstance(existing_reference, dict):
        return []
    # New rows use the application's normalized top-level contract. Keep the
    # nested fallback so a partially deployed instance can enrich rows written
    # by an older bot revision without downloading duplicate files.
    direct = existing_reference.get("attachments") or []
    if direct:
        return direct
    snapshots = existing_reference.get("message_snapshots") or []
    if index >= len(snapshots) or not isinstance(snapshots[index], dict):
        return []
    wrapped = snapshots[index].get("message") or {}
    return wrapped.get("attachments") or [] if isinstance(wrapped, dict) else []


def _existing_reference_payload(existing_reference: Optional[dict]) -> dict:
    if not isinstance(existing_reference, dict):
        return {}
    snapshots = existing_reference.get("message_snapshots") or []
    for item in snapshots:
        if isinstance(item, dict) and isinstance(item.get("message"), dict):
            return item["message"]
    if isinstance(existing_reference.get("message"), dict):
        return existing_reference["message"]
    return existing_reference


async def _build_ticket_reference_snapshot(
    ticket_id: int,
    message: discord.Message,
    budget: dict,
    existing_reference: Optional[dict],
) -> Optional[dict]:
    reference = getattr(message, "reference", None)
    snapshots = list(getattr(message, "message_snapshots", []) or [])
    if reference is None and not snapshots:
        return None

    reference_type = getattr(reference, "type", None)
    # MessageReferenceType was added after discord.py 2.3. Older deployed
    # environments still need to archive ordinary replies without crashing
    # the entire history backfill. Snapshot presence remains the authoritative
    # forward signal; enum name/value checks cover newer and partially updated
    # clients without importing a missing module attribute.
    reference_type_enum = getattr(discord, "MessageReferenceType", None)
    forward_type = getattr(reference_type_enum, "forward", None)
    is_forward = bool(snapshots) or (
        reference_type is not None
        and (
            (forward_type is not None and reference_type == forward_type)
            or getattr(reference_type, "name", None) == "forward"
            or getattr(reference_type, "value", None) == 1
        )
    )
    source = _reference_source(reference) if reference is not None else {
        "message_id": None, "channel_id": None, "guild_id": None, "jump_url": None,
    }
    resolved, channel = await _resolve_ticket_reference(reference) if reference is not None else (None, None)
    existing_source = existing_reference.get("source") if isinstance(existing_reference, dict) else None
    if isinstance(existing_source, dict):
        source = {**existing_source, **{key: value for key, value in source.items() if value is not None}}
    if channel is not None:
        source["channel_name"] = getattr(channel, "name", None)
    # Discord intentionally omits the original author from a native forward.
    # It is legal to retain a previously resolved author, but never infer one
    # from the user who performed the forward.
    author = _serialize_ticket_author(resolved.author) if resolved is not None else None
    if author is None and isinstance(existing_reference, dict):
        author = existing_reference.get("author")
        if author is None and isinstance(existing_source, dict):
            author = existing_source.get("author")

    if is_forward:
        archived_attachments = []
        contents = []
        embeds = []
        stickers = []
        created_at = None
        edited_at = None
        for index, snapshot in enumerate(snapshots):
            namespace = f"msg-{message.id}-fwd-{index}"
            archived = await _archive_ticket_attachments(
                ticket_id,
                namespace,
                getattr(snapshot, "attachments", []),
                budget,
                _existing_forward_attachments(existing_reference, index),
            )
            content = getattr(snapshot, "content", "") or ""
            if content:
                contents.append(content)
            if created_at is None and getattr(snapshot, "created_at", None):
                created_at = snapshot.created_at.isoformat()
            if edited_at is None and getattr(snapshot, "edited_at", None):
                edited_at = snapshot.edited_at.isoformat()
            archived_attachments.extend(archived)
            embeds.extend(embed.to_dict() for embed in getattr(snapshot, "embeds", []))
            stickers.extend(_serialize_ticket_sticker(item) for item in getattr(snapshot, "stickers", []))

        # An empty re-fetch must not erase evidence saved by the Gateway event.
        if not snapshots and isinstance(existing_reference, dict):
            previous = _existing_reference_payload(existing_reference)
            archived_attachments = list(previous.get("attachments") or [])
            contents = [previous.get("content")] if previous.get("content") else []
            embeds = list(previous.get("embeds") or [])
            stickers = list(previous.get("stickers") or [])
            created_at = previous.get("created_at")
            edited_at = previous.get("edited_at")

        return {
            "kind": "forward",
            "source": source,
            "author": author,
            "content": "\n\n".join(contents) or None,
            "created_at": created_at,
            "edited_at": edited_at,
            "attachments": archived_attachments,
            "embeds": embeds,
            "stickers": stickers,
        }

    if resolved is not None:
        archived = await _archive_ticket_attachments(
            ticket_id,
            f"msg-{message.id}-reply",
            getattr(resolved, "attachments", []),
            budget,
            _existing_reference_payload(existing_reference).get("attachments") or [],
        )
        return {
            "kind": "reply",
            "source": source,
            "author": author,
            "content": getattr(resolved, "content", "") or "",
            "created_at": resolved.created_at.isoformat() if getattr(resolved, "created_at", None) else None,
            "edited_at": resolved.edited_at.isoformat() if getattr(resolved, "edited_at", None) else None,
            "attachments": archived,
            "embeds": [embed.to_dict() for embed in getattr(resolved, "embeds", [])],
            "stickers": [_serialize_ticket_sticker(item) for item in getattr(resolved, "stickers", [])],
        }

    # Replies can point at an inaccessible or already deleted message. Retain
    # an earlier resolved snapshot if one exists; otherwise preserve the IDs
    # and explicitly leave the unknown author empty.
    if isinstance(existing_reference, dict):
        previous = _existing_reference_payload(existing_reference)
        archived = await _archive_ticket_attachments(
            ticket_id,
            f"msg-{message.id}-reply",
            previous.get("attachments") or [],
            budget,
            previous.get("attachments") or [],
        )
        return {
            "kind": "reply",
            "source": source,
            "author": author or previous.get("author"),
            "content": previous.get("content"),
            "created_at": previous.get("created_at"),
            "edited_at": previous.get("edited_at"),
            "attachments": archived,
            "embeds": list(previous.get("embeds") or []),
            "stickers": list(previous.get("stickers") or []),
        }
    return {
        "kind": "reply",
        "source": source,
        "author": None,
        "content": None,
        "created_at": None,
        "attachments": [],
        "embeds": [],
    }


def _iter_attachment_records(value, *, in_attachments: bool = False):
    """Yield every attachment-like record inside an archive payload.

    Attachment dictionaries are recognizable by filename; source and author
    dictionaries can also contain an ``id`` and must not be mistaken for
    media.  Entries directly inside an ``attachments`` list are always media,
    including the bare CDN URLs written by pre-archive revisions.
    """
    if isinstance(value, dict):
        if (
            in_attachments
            or "filename" in value
            or "archive_status" in value
            or "storage_key" in value
        ):
            yield value
            return
        for key, item in value.items():
            yield from _iter_attachment_records(item, in_attachments=(key == "attachments"))
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_attachment_records(item, in_attachments=in_attachments)
        return
    if in_attachments and value is not None:
        # A legacy row can hold a bare CDN link instead of a record. Its
        # signed URL has long expired, so it is evidence that exists only as
        # metadata — never a stored file.
        yield {"filename": str(value), "source_url": str(value), "archive_status": "legacy"}


def _attachment_collection_has_failure(ticket_id: int, attachments) -> bool:
    return any(
        not _archived_attachment_is_reusable(ticket_id, record)
        for record in _iter_attachment_records(attachments, in_attachments=True)
    )


def _archive_has_failure(ticket_id: int, value) -> bool:
    return any(
        not _archived_attachment_is_reusable(ticket_id, record)
        for record in _iter_attachment_records(value)
    )


# Discord will never hand these back on a retry: the file is bigger than the
# archive is allowed to keep, or there is no room left for it. Re-reading the
# channel a thousand times cannot change that verdict. ``legacy`` joins them
# because such a record is only an expired signed CDN link left by a
# pre-archive revision — the live copy of that file is re-downloaded and merged
# under its own record, and nothing can ever be fetched from the old link.
_PERMANENT_ARCHIVE_STATUSES = frozenset({"too_large", "quota_exceeded", "legacy"})


def _archive_defects(ticket_id: int, row: dict, live_discord_ids: Optional[set]) -> list[dict]:
    """Classify the unarchived media of one persisted message row.

    ``permanent`` marks evidence that no future attempt can recover, so the
    evidence-first close must be allowed to finish instead of leaving the
    ticket uncloseable forever. Everything else stays retryable and keeps
    blocking the irreversible channel deletion.
    """
    discord_id = str(row.get("discord_message_id") or "")
    # A row whose Discord message is gone (deleted by its author, or written
    # before the archive existed) can no longer be re-downloaded: its CDN link
    # is signed and expired, and history() will never return it again.
    source_gone = live_discord_ids is not None and (
        not discord_id or discord_id not in live_discord_ids
    )
    defects = []
    records = list(_iter_attachment_records(row.get("attachments"), in_attachments=True))
    records.extend(_iter_attachment_records(row.get("reference_snapshot")))
    for record in records:
        if _archived_attachment_is_reusable(ticket_id, record):
            continue
        status = str(record.get("archive_status") or "legacy").lower()
        defects.append({
            "message_id": row.get("id"),
            "discord_message_id": discord_id or None,
            "filename": str(record.get("filename") or "file"),
            "status": status,
            "permanent": bool(source_gone or status in _PERMANENT_ARCHIVE_STATUSES),
        })
    return defects


async def _collect_ticket_archive_defects(
    ticket_id: int,
    live_discord_ids: Optional[set],
) -> list[dict]:
    rows = await asyncio.to_thread(bot_data.get_ticket_message_archives, ticket_id)
    return [
        defect
        for row in rows
        for defect in _archive_defects(ticket_id, row, live_discord_ids)
    ]


def _describe_archive_defects(defects: list[dict], limit: int = 3) -> str:
    head = ", ".join(
        f"{item['filename']} ({item['status']})" for item in defects[:limit]
    )
    if len(defects) > limit:
        head += f" +{len(defects) - limit}"
    return head


async def _capture_ticket_message(ticket_id: int, message: discord.Message) -> int:
    # A reply sent from NexusData is persisted before Discord delivery.  Link
    # the Gateway/history echo to that row before doing any archive work so the
    # moderator identity and already-stored upload are retained without a
    # duplicate message. Other bots and webhooks intentionally continue below:
    # their messages are third-party evidence, not site-authored echoes.
    own_bot_id = getattr(getattr(bot, "user", None), "id", None)
    if (
        own_bot_id is not None
        and bool(getattr(message.author, "bot", False))
        and int(message.author.id) == int(own_bot_id)
    ):
        linked_id = await asyncio.to_thread(
            bot_data.link_site_ticket_message,
            ticket_id,
            str(message.id),
            message.content,
            message.attachments,
            message.created_at,
        )
        if linked_id:
            return linked_id

    existing = await asyncio.to_thread(
        bot_data.get_ticket_message_by_discord_id,
        ticket_id,
        str(message.id),
    )
    budget = {"used": 0}
    attachments = await _archive_ticket_attachments(
        ticket_id,
        f"msg-{message.id}",
        message.attachments,
        budget,
        (existing or {}).get("attachments") or [],
    )
    reference_snapshot = await _build_ticket_reference_snapshot(
        ticket_id,
        message,
        budget,
        (existing or {}).get("reference_snapshot"),
    )
    outer_embeds = [embed.to_dict() for embed in getattr(message, "embeds", [])]
    outer_stickers = [
        _serialize_ticket_sticker(item) for item in getattr(message, "stickers", [])
    ]
    if outer_embeds or outer_stickers:
        reference_snapshot = dict(reference_snapshot or {"kind": "message_content"})
        reference_snapshot["outer_embeds"] = outer_embeds
        reference_snapshot["outer_stickers"] = outer_stickers
    local_id = await asyncio.to_thread(
        bot_data.add_ticket_message,
        ticket_id=ticket_id,
        author_discord_id=str(message.author.id),
        author_username=str(message.author),
        author_display_name=message.author.display_name,
        author_avatar_url=str(message.author.display_avatar.url) if message.author.display_avatar else None,
        content=message.content,
        attachments=attachments,
        discord_message_id=str(message.id),
        reference_snapshot=reference_snapshot,
        created_at=message.created_at,
        is_bot=bool(getattr(message.author, "bot", False)),
    )
    if not local_id:
        raise RuntimeError(f"ticket {ticket_id} no longer exists")
    return local_id


# How many extra download attempts one close makes for evidence that a retry
# could still recover. Discord CDN hiccups clear within seconds; anything that
# survives this is either permanent or worth a later attempt, not an endless
# loop holding a frozen channel.
_TICKET_ARCHIVE_RETRY_PASSES = 2


class TicketArchiveIncomplete(RuntimeError):
    """Recoverable evidence is still missing, so the channel must survive."""

    def __init__(self, ticket_id: int, defects: list[dict]):
        self.ticket_id = ticket_id
        self.defects = defects
        super().__init__(
            f"ticket {ticket_id} still has {len(defects)} recoverable "
            f"unarchived item(s): {_describe_archive_defects(defects)}"
        )


# Media archiving of a live message runs after the row is already visible.
# Keep a strong reference per ticket so the task is not garbage collected, and
# so a close can wait for the mirroring it started.
_ticket_media_tasks: dict[int, set] = {}

# A CDN hiccup is usually over in seconds. Retry a few times before leaving the
# gap to the periodic backfill, which may be up to 15 minutes away.
_TICKET_MEDIA_ARCHIVE_ATTEMPTS = 3
_TICKET_MEDIA_ARCHIVE_RETRY_SECONDS = 5.0


def _ticket_message_has_media(message) -> bool:
    return bool(
        getattr(message, "attachments", None)
        or getattr(message, "reference", None)
        or getattr(message, "message_snapshots", None)
    )


def _pending_attachment_record(attachment) -> dict:
    """Metadata for a file whose download has not finished yet."""
    record = _ticket_attachment_base(attachment)
    record["archive_status"] = "pending"
    return record


async def _capture_ticket_message_now(ticket_id: int, message: discord.Message) -> Optional[int]:
    """Persist a ticket message immediately, then mirror its media.

    Downloading attachments takes seconds — a large video, a slow CDN, another
    file already occupying the download slot — and the transcript used to show
    nothing at all until that finished. The row is written first with its files
    marked ``pending``; a follow-up task archives them and enriches the same
    row, so both the site and the evidence archive start from the same instant.
    """
    if not _ticket_message_has_media(message):
        # Nothing to download: the ordinary path is already a single write.
        return await _capture_ticket_message(ticket_id, message)

    own_bot_id = getattr(getattr(bot, "user", None), "id", None)
    if (
        own_bot_id is not None
        and bool(getattr(message.author, "bot", False))
        and int(message.author.id) == int(own_bot_id)
    ):
        # A site-authored reply is already persisted with its uploads; only the
        # full path knows how to link this echo to that existing row.
        return await _capture_ticket_message(ticket_id, message)

    local_id = await asyncio.to_thread(
        bot_data.add_ticket_message,
        ticket_id=ticket_id,
        author_discord_id=str(message.author.id),
        author_username=str(message.author),
        author_display_name=message.author.display_name,
        author_avatar_url=str(message.author.display_avatar.url) if message.author.display_avatar else None,
        content=message.content,
        attachments=[
            _pending_attachment_record(attachment)
            for attachment in getattr(message, "attachments", []) or []
        ],
        discord_message_id=str(message.id),
        created_at=message.created_at,
        is_bot=bool(getattr(message.author, "bot", False)),
    )
    if local_id:
        _schedule_ticket_media_archive(ticket_id, message)
    return local_id


def _schedule_ticket_media_archive(ticket_id: int, message: discord.Message) -> None:
    task = asyncio.create_task(_archive_ticket_message_media(ticket_id, message))
    tasks = _ticket_media_tasks.setdefault(int(ticket_id), set())
    tasks.add(task)

    def _done(finished, ticket_id=int(ticket_id)):
        pending = _ticket_media_tasks.get(ticket_id)
        if pending is None:
            return
        pending.discard(finished)
        if not pending:
            _ticket_media_tasks.pop(ticket_id, None)

    task.add_done_callback(_done)


async def _archive_ticket_message_media(ticket_id: int, message: discord.Message) -> None:
    """Mirror one message's media into the already visible row."""
    for attempt in range(_TICKET_MEDIA_ARCHIVE_ATTEMPTS):
        try:
            await _capture_ticket_message(ticket_id, message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Ticket #%s media archive attempt %s for message %s failed: %s",
                ticket_id,
                attempt + 1,
                message.id,
                type(exc).__name__,
            )
        else:
            persisted = await asyncio.to_thread(
                bot_data.get_ticket_message_by_discord_id,
                ticket_id,
                str(message.id),
            )
            if persisted is None:
                return
            if not (
                _attachment_collection_has_failure(ticket_id, persisted.get("attachments"))
                or _archive_has_failure(ticket_id, persisted.get("reference_snapshot"))
            ):
                return
        if attempt + 1 < _TICKET_MEDIA_ARCHIVE_ATTEMPTS:
            await asyncio.sleep(_TICKET_MEDIA_ARCHIVE_RETRY_SECONDS * (attempt + 1))
    logger.warning(
        "Ticket #%s message %s still has unarchived media; leaving it to the "
        "periodic backfill",
        ticket_id,
        message.id,
    )


async def _await_ticket_media_tasks(ticket_id: int, timeout: float = 30.0) -> None:
    """Let in-flight mirroring finish before a final history sweep starts."""
    pending = set(_ticket_media_tasks.get(int(ticket_id)) or ())
    if not pending:
        return
    logger.info(
        "Ticket #%s close waits for %s in-flight media archive task(s)",
        ticket_id,
        len(pending),
    )
    done, still_running = await asyncio.wait(pending, timeout=timeout)
    if still_running:
        # The sweep below re-reads and re-archives every message anyway, so a
        # slow download is not a reason to hold the frozen channel longer.
        logger.warning(
            "Ticket #%s close continues with %s media task(s) still running",
            ticket_id,
            len(still_running),
        )


async def _reconcile_ticket_channel_history(
    ticket_id: int,
    channel,
    *,
    force: bool = False,
    defects: Optional[list] = None,
) -> int:
    """Upsert every message before a ticket channel is destroyed.

    Media that a retry can still recover keeps blocking the irreversible
    deletion. Media that no retry can ever recover — a file above the archive
    limit, an exhausted quota, or a message deleted from Discord before it was
    mirrored — is reported through ``defects`` instead, so the ticket can be
    closed with a visible ``partial`` archive rather than staying open forever.
    ``force`` additionally downgrades recoverable defects to a report; it is
    reserved for an explicit staff decision.
    """
    await _await_ticket_media_tasks(ticket_id)
    reconciled = 0
    live_discord_ids: set[str] = set()
    async for historical in channel.history(limit=None, oldest_first=True):
        live_discord_ids.add(str(historical.id))
        await _capture_ticket_message(ticket_id, historical)
        reconciled += 1

    # A deleted Discord message no longer appears in history, but its archived
    # row remains evidence. Audit all retained rows before allowing the
    # irreversible channel deletion, including failed/oversized old media.
    remaining = await _collect_ticket_archive_defects(ticket_id, live_discord_ids)
    for attempt in range(_TICKET_ARCHIVE_RETRY_PASSES):
        retryable = {
            item["discord_message_id"]
            for item in remaining
            if not item["permanent"] and item["discord_message_id"] in live_discord_ids
        }
        if not retryable:
            break
        logger.info(
            "Ticket #%s retrying %s message(s) with recoverable archive gaps (pass %s)",
            ticket_id,
            len(retryable),
            attempt + 1,
        )
        for discord_message_id in sorted(retryable):
            try:
                message = await channel.fetch_message(int(discord_message_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                # Gone or unreadable between the sweep and the retry: the next
                # audit reclassifies it against the history we actually saw.
                continue
            await _capture_ticket_message(ticket_id, message)
        remaining = await _collect_ticket_archive_defects(ticket_id, live_discord_ids)

    if defects is not None:
        defects.extend(remaining)
    unrecoverable = [item for item in remaining if item["permanent"]]
    recoverable = [item for item in remaining if not item["permanent"]]
    if recoverable and not force:
        raise TicketArchiveIncomplete(ticket_id, recoverable)
    if remaining:
        logger.warning(
            "Ticket #%s closes with an incomplete archive: %s permanent, %s forced (%s)",
            ticket_id,
            len(unrecoverable),
            len(recoverable),
            _describe_archive_defects(remaining),
        )
    logger.info("Ticket #%s final Discord history reconciliation: %s message(s)", ticket_id, reconciled)
    return reconciled


def _clone_permission_overwrite(overwrite: discord.PermissionOverwrite) -> discord.PermissionOverwrite:
    allow, deny = overwrite.pair()
    return discord.PermissionOverwrite.from_pair(allow, deny)


class _TicketAutoCloseNoLongerEligible(RuntimeError):
    """A queued stale-close became obsolete while the channel was frozen."""


async def _freeze_reconcile_and_delete_ticket_channel(
    ticket_id: int,
    channel,
    *,
    reason: str,
    closed_by: str,
    auto_close_before=None,
    force: bool = False,
) -> int:
    """Make a ticket read-only, persist its final history, then delete it.

    The permission edit is a Discord-side acknowledgement barrier: once it
    succeeds, ordinary participants cannot race a new attachment into the gap
    between ``history()`` and deletion.  If either reconciliation or deletion
    fails, the exact original overwrites are restored so the recoverable
    channel does not remain accidentally locked.
    """
    original_overwrites = {
        target: _clone_permission_overwrite(overwrite)
        for target, overwrite in channel.overwrites.items()
    }
    frozen_overwrites = {
        target: _clone_permission_overwrite(overwrite)
        for target, overwrite in original_overwrites.items()
    }

    default_role = channel.guild.default_role
    everyone = frozen_overwrites.get(default_role, discord.PermissionOverwrite())
    everyone.send_messages = False
    everyone.send_messages_in_threads = False
    frozen_overwrites[default_role] = everyone

    # An explicit participant/role allow wins over @everyone's deny, so every
    # existing overwrite must be denied as well. Keep the bot writable while
    # it finishes moderation work on the frozen channel.
    bot_member = channel.guild.me
    for target, overwrite in frozen_overwrites.items():
        if bot_member is not None and target == bot_member:
            continue
        overwrite.send_messages = False
        overwrite.send_messages_in_threads = False
    if bot_member is not None:
        bot_overwrite = frozen_overwrites.get(bot_member, discord.PermissionOverwrite())
        bot_overwrite.view_channel = True
        bot_overwrite.read_message_history = True
        bot_overwrite.send_messages = True
        bot_overwrite.send_messages_in_threads = True
        frozen_overwrites[bot_member] = bot_overwrite

    channel_id = int(channel.id)
    _ticket_channels_closing.add(channel_id)
    delete_completed = False
    try:
        await channel.edit(
            overwrites=frozen_overwrites,
            reason=f"Freeze before evidence archive: ticket #{ticket_id}",
        )
        defects: list[dict] = []
        reconciled = await _reconcile_ticket_channel_history(
            ticket_id, channel, force=force, defects=defects,
        )
        if auto_close_before and not await asyncio.to_thread(
            bot_data.ticket_is_stale_unanswered,
            ticket_id,
            auto_close_before,
        ):
            raise _TicketAutoCloseNoLongerEligible(
                f"ticket {ticket_id} was answered while its history was reconciled"
            )
        await channel.delete(reason=reason)
        delete_completed = True
        # Evidence that no retry could ever recover is kept as a visible
        # exception on the closed ticket instead of a silent "complete".
        archive_state = "partial" if defects else "complete"
        if not await _close_ticket_with_retry(
            ticket_id, closed_by, archive_state=archive_state,
        ):
            # The irreversible Discord operation already succeeded. Persist a
            # recovery job before surfacing the error so a transient SQL outage
            # cannot leave an uncloseable phantom ticket after process restart.
            try:
                await asyncio.to_thread(
                    bot_data.enqueue_ticket_bot_action,
                    "ticket_close_channel",
                    {
                        "ticket_id": ticket_id,
                        "channel_id": channel_id,
                        "closed_by": closed_by,
                        "evidence_archive_confirmed": True,
                        "archive_state": archive_state,
                        **(
                            {"auto_close_before": auto_close_before}
                            if auto_close_before
                            else {}
                        ),
                    },
                    dedupe_key=f"ticket-close:{ticket_id}",
                )
            except Exception:
                logger.exception(
                    "Ticket #%s was deleted but its SQL-close recovery action could not be persisted",
                    ticket_id,
                )
            raise RuntimeError(f"ticket {ticket_id} channel deleted but SQL close failed")
        return reconciled
    except BaseException:
        if not delete_completed:
            restore_task = None
            try:
                restore_task = asyncio.create_task(
                    channel.edit(
                        overwrites=original_overwrites,
                        reason=f"Restore after failed ticket close: #{ticket_id}",
                    )
                )
                # Do not let an HTTP request timeout cancel the compensating
                # permission write after Discord already accepted the freeze.
                await asyncio.shield(restore_task)
            except discord.NotFound:
                # Deleted out-of-band while closing: nothing remains to restore.
                pass
            except BaseException as restore_exc:
                if restore_task is not None and not restore_task.done():
                    try:
                        await restore_task
                    except discord.NotFound:
                        pass
                    except BaseException:
                        pass
                logger.exception(
                    "Ticket #%s permission restore failed after close error: %s",
                    ticket_id,
                    type(restore_exc).__name__,
                )
        raise
    finally:
        _ticket_channels_closing.discard(channel_id)


# Sync messages from ticket channels to NexusData
@bot.event
async def on_message(message: discord.Message):
    global FORWARD_CHANNEL_ID
    channel_name = getattr(message.channel, "name", "") or ""
    is_bot_author = bool(getattr(message.author, "bot", False))
    if is_bot_author and not channel_name.startswith("ticket-"):
        await bot.process_commands(message)
        return

    # A mute is intentionally enforced by deleting messages instead of changing
    # every channel's permissions. This covers channels created after the mute
    # and leaves every ticket channel available for appeals/support.
    active_mutes = (
        {}
        if is_bot_author
        else await asyncio.to_thread(bot_data.get_active_mutes)
    )
    if str(message.author.id) in active_mutes and not channel_name.startswith("ticket-"):
        # п.6: логируем удалённое сообщение замьюченного в канал логирования особым embed
        content_copy = message.content
        attachments_copy = list(message.attachments)
        author_name = message.author.display_name
        author_mention = message.author.mention
        channel_mention = getattr(message.channel, "mention", f"#{channel_name}") if hasattr(message.channel, "mention") else f"#{channel_name}"
        mute_info = active_mutes.get(str(message.author.id), {})
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
        # отправляем в FORWARD-канал специальным embed (не мешает дальнейшей обработке)
        try:
            if FORWARD_CHANNEL_ID:
                forward_ch = bot.get_channel(FORWARD_CHANNEL_ID)
                if forward_ch:
                    embed = discord.Embed(
                        title="🔇 Удалено сообщение от замьюченного",
                        description=content_copy[:4000] if content_copy else "*без текста*",
                        color=0xff4444,
                    )
                    embed.set_author(name=f"{author_name} ({author_mention})", icon_url=message.author.display_avatar.url if message.author.display_avatar else None)
                    embed.add_field(name="Канал", value=channel_mention, inline=True)
                    embed.add_field(name="Автор", value=f"{author_name} <@{message.author.id}>", inline=True)
                    if mute_info.get("reason"):
                        embed.add_field(name="Причина мута", value=str(mute_info["reason"])[:1024], inline=False)
                    if mute_info.get("expires_at"):
                        try:
                            exp = datetime.fromisoformat(str(mute_info["expires_at"]))
                            ts = int(exp.replace(tzinfo=timezone.utc).timestamp()) if exp.tzinfo is None else int(exp.timestamp())
                            embed.add_field(name="Мут до", value=f"<t:{ts}:F>", inline=True)
                        except Exception:
                            pass
                    embed.set_footer(text=f"ID {message.author.id} | {format_msk(message.created_at.replace(tzinfo=None))} МСК")
                    files_to_send = []
                    for att in attachments_copy[:4]:
                        try:
                            files_to_send.append(await att.to_file())
                        except Exception:
                            embed.add_field(name="Файл", value=f"[{att.filename}]({att.url})", inline=False)
                    await forward_ch.send(embed=embed, files=files_to_send if files_to_send else discord.utils.MISSING)
        except Exception as e:
            logger.warning(f"Failed to log muted message to forward channel: {e}")
        return

    # Check if this is a ticket channel
    if channel_name.startswith("ticket-"):
        channel_id = str(message.channel.id)
        # Direct lookup avoids scanning every open ticket for every message.
        # include_closed also closes the small race where the site has marked
        # a ticket closed but the last Gateway event arrives before deletion.
        matched_ticket = await asyncio.to_thread(
            bot_data.get_ticket_by_channel,
            channel_id,
            True,
        )
        ticket_id = matched_ticket["id"] if matched_ticket else None

        if ticket_id:
            try:
                await _capture_ticket_message_now(ticket_id, message)
            except Exception as exc:
                logger.exception(
                    "Failed to archive ticket #%s message %s: %s",
                    ticket_id,
                    message.id,
                    type(exc).__name__,
                )
            else:
                await refresh_ticket_status()

            # Деактивация кнопки "Закрыть обращение" после ответа админа (п.8): как только
            # в тикете пишет сотрудник с ролью ответчика — кнопка становится неинтерактивной.
            is_ticket_staff = False
            if not message.author.bot and ticket_id:
                config_for_staff = await asyncio.to_thread(bot_data.get_tickets_config)
                is_ticket_staff = await _user_has_ticket_staff_permission(
                    message.author,
                    message.guild,
                    config_for_staff,
                )
                if is_ticket_staff:
                    await asyncio.to_thread(
                        bot_data.mark_ticket_answered,
                        ticket_id,
                        message.created_at,
                    )
                    try:
                        async for msg in message.channel.history(limit=20):
                            if msg.author.id == bot.user.id and msg.components:
                                for row in msg.components:
                                    for comp in row.children:
                                        if getattr(comp, 'custom_id', None) == 'ticket_close_btn' and not comp.disabled:
                                            view = TicketCloseView(ticket_id=ticket_id)
                                            for child in view.children:
                                                child.disabled = True
                                            await msg.edit(view=view)
                                            break
                                break
                    except Exception as e:
                        logger.warning(f"Failed to disable close button in {channel_id}: {e}")

            # Отмена отложенного /close сообщением от сотрудника (item 6):
            # раньше единственный способ отменить закрытие — набрать /close
            # ЕЩЁ РАЗ (toggle), что неочевидно и требует помнить точную
            # команду. Теперь достаточно ответить в тикете, будучи держателем
            # одной из ping_roles ЭТОГО типа обращения — тех же ролей, что
            # были потегованы при создании тикета (см. _create_ticket_channel).
            # Сообщения от автора обращения или посторонних закрытие не отменяют.
            if channel_id in _pending_close and is_ticket_staff:
                pending_state = _pending_close.pop(channel_id, None)
                if pending_state is not None:
                    pending_state.cancelled = True
                    if pending_state.task is not None and not pending_state.task.done():
                        pending_state.task.cancel()
                    try:
                        await message.channel.send(
                            f"⏸️ Закрытие тикета автоматически отменено — {message.author.mention} ответил(а) в обращении."
                        )
                    except Exception as e:
                        logger.warning(f"Failed to announce auto-cancel close in {channel_id}: {e}")

    # Third-party bots and webhooks inside ticket channels are evidence and
    # were captured above. Stop here to avoid mirroring/forwarding automated
    # messages into other channels and creating feedback loops.
    if is_bot_author:
        await bot.process_commands(message)
        return

    # Department chat channels (item 6): mirror messages from any linked
    # channel into NexusData.
    if str(message.channel.id) in _department_chat_channel_ids:
        await asyncio.to_thread(
            bot_data.ingest_department_chat_message,
            discord_channel_id=str(message.channel.id),
            discord_message_id=str(message.id),
            author_name=message.author.display_name,
            author_avatar_url=str(message.author.display_avatar.url) if message.author.display_avatar else None,
            content=message.content,
        )

    # Forward to designated channel (existing logic)
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
    """Record an evidence exception for an out-of-band ticket deletion.

    A Gateway delete event arrives after Discord has removed the channel, so
    it cannot prove that the final history and attachments were archived.  Do
    not close the SQL row; preserve it as an explicit incomplete archive for
    moderator recovery/review.  The evidence-first close helper is guarded by
    ``_ticket_channels_closing`` and owns successful closure attribution.
    """
    if int(channel.id) in _ticket_channels_closing:
        # The evidence-first close helper owns SQL attribution and any retry.
        # Treating its Gateway event as a manual deletion races closed_by.
        return
    if not (channel.name and channel.name.startswith("ticket-")):
        return
    channel_id = str(channel.id)
    open_tickets = await asyncio.to_thread(bot_data.get_open_tickets)
    if not open_tickets:
        return
    for t in open_tickets:
        if t.get("discord_channel_id") == channel_id:
            try:
                marked = await asyncio.to_thread(
                    bot_data.mark_ticket_archive_incomplete,
                    t["id"],
                )
            except Exception:
                logger.exception(
                    "Не удалось пометить тикет #%s как incomplete после удаления канала",
                    t["id"],
                )
                return
            if marked:
                await refresh_ticket_status()
            break


@bot.event
async def setup_hook():
    """Register persistent views so buttons work after restart, and sync
    slash commands. Runs on module import (not gated behind __main__ like it
    used to be) since this module is now imported, not exec'd as a script."""
    bot.add_view(TicketPanelView())
    bot.add_view(TicketCloseView())
    # A global sync propagates lazily: for up to an hour after a command's
    # signature changes, clients keep the old definition and Discord answers a
    # click on it with "this command is outdated" instead of running it. The
    # same commands copied into the configured guild take effect immediately
    # and hide the global duplicates of the same name inside that guild, so a
    # restart makes the new definition usable at once.
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        try:
            bot.tree.copy_global_to(guild=guild)
            synced_guild = await bot.tree.sync(guild=guild)
            logger.info(
                "Synced %s slash command(s) to guild %s", len(synced_guild), GUILD_ID,
            )
        except Exception as e:
            # Never fail startup over this: the global sync below still lands.
            logger.error(f"Failed to sync guild slash commands: {e}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")


# ═══════════════════════════════════════════════════════════
# SUPERVISOR — how the rest of the app drives this bot
# ═══════════════════════════════════════════════════════════

_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_ticket_ipc_recovery_done = False


async def dispatch_ticket_bot_action(
    action_type: str,
    payload: dict,
    *,
    timeout: float,
    queued_by_admin_id: int | None = None,
    dedupe_key: str | None = None,
) -> tuple[Optional[bool], Optional[str], int]:
    """Durably deliver a ticket action across single or multi-worker setups.

    The database row is the ownership boundary: request cancellation or a
    reverse-proxy timeout only stops this status poll, never the Discord work.
    ``None`` means accepted/still processing, while ``True``/``False`` are
    terminal outcomes observed before the caller's deadline.
    """
    action_id = await asyncio.to_thread(
        bot_data.enqueue_ticket_bot_action,
        action_type,
        payload,
        queued_by_admin_id=queued_by_admin_id,
        dedupe_key=dedupe_key,
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout))
    last_poll_error = None
    while True:
        try:
            status = await asyncio.to_thread(bot_data.get_ticket_bot_action_status, action_id)
            last_poll_error = None
        except Exception as exc:
            status = None
            last_poll_error = f"{type(exc).__name__}: {exc}"
        if status:
            if status.get("state") == "done":
                return True, None, action_id
            if status.get("state") == "failed":
                return False, status.get("error") or "Discord action failed", action_id
        remaining = deadline - loop.time()
        if remaining <= 0:
            detail = "Действие принято и продолжает выполняться в фоне"
            if last_poll_error:
                detail += f"; статус временно недоступен ({last_poll_error})"
            return None, detail, action_id
        await asyncio.sleep(min(0.25, remaining))


def schedule(coro, description: str = ""):
    """Thread-safe way to fire a coroutine at the bot's event loop from ANY
    context — a sync/threadpool-dispatched route handler or an async one.
    Returns a concurrent.futures.Future; most callers fire-and-forget it, but
    routes/documents.py's verify-channel endpoint awaits it (via
    asyncio.wrap_future) for check_channel_category's near-synchronous
    request/response contract. This is the direct replacement for every
    `db.add(DiscordBotAction(...))` call site — same action_type/payload
    shape, delivered immediately instead of polled.

    Результат ОБЯЗАТЕЛЬНО логируется. Почти все вызывающие работают по схеме
    «отправил и забыл», а execute_bot_action возвращает (False, причина)
    вместо исключения — без этого колбэка любой сбой пропадал бесследно:
    сайт рапортовал об успехе, в Discord ничего не происходило, в логах было
    пусто. Самый коварный случай — бот запущен, но ещё (или уже) не подключён к
    Discord: _bot_loop к этому моменту уже установлен, задача принимается,
    а get_guild() возвращает None.
    """
    if _bot_loop is None:
        raise RuntimeError("Discord bot is not running (schedule() called before supervise_bot() started it)")

    label = description or getattr(coro, "__qualname__", "действие бота")
    if not bot.is_ready():
        logger.warning(
            f"{label}: бот ещё не подключён к Discord — действие, скорее всего, "
            f"не выполнится (проверьте токен и логи запуска бота)"
        )

    future = asyncio.run_coroutine_threadsafe(coro, _bot_loop)

    def _log_outcome(f):
        try:
            result = f.result()
        except Exception:
            logger.exception(f"{label}: действие бота завершилось исключением")
            return
        # execute_bot_action возвращает (успех, причина)
        if isinstance(result, tuple) and len(result) == 2 and result[0] is False:
            logger.error(f"{label}: действие бота не выполнено — {result[1]}")

    future.add_done_callback(_log_outcome)
    return future


async def supervise_bot():
    """Runs the bot with retry/backoff instead of the old blocking
    bot.run(TOKEN) — lets this coroutine be scheduled alongside the rest of
    the app's startup (main.py's startup hook in single-process mode, or
    scheduler_process.py in multi-worker mode) instead of needing its own
    dedicated process."""
    global _bot_loop, _ticket_ipc_recovery_done

    if not settings.discord_bot_token:
        logger.warning("discord_bot_token not configured — Discord bot will not start")
        return

    _bot_loop = asyncio.get_running_loop()
    try:
        recovered = await asyncio.to_thread(bot_data.recover_interrupted_ticket_bot_actions)
        _ticket_ipc_recovery_done = True
        if recovered:
            logger.warning("Recovered %s interrupted ticket Discord action(s)", recovered)
    except Exception:
        # on_ready retries this once Discord is connected; bot availability
        # must not depend on the database being healthy at process bootstrap.
        logger.exception("Could not recover interrupted ticket Discord actions at startup")
    backoff = 5
    while True:
        try:
            await bot.start(settings.discord_bot_token)
            break  # bot.close() was called deliberately — stop supervising
        except Exception:
            logger.exception(f"Discord bot crashed — restarting in {backoff}s")
            try:
                await bot.close()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    # Quick standalone check — normal operation is via supervise_bot() called
    # from main.py / scheduler_process.py.
    asyncio.run(supervise_bot())
