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
import logging
import json
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import get_settings
from discord_bot import data as bot_data

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
    global FORWARD_CHANNEL_ID, RATING_CHANNEL_ID
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
        data = await asyncio.to_thread(bot_data.get_ratings_leaderboard, role, 10)
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
    """Закрывает на сайте тикеты, чей канал в Discord уже не существует.

    Раньше единственный способ закрыть тикет — кнопка «Закрыть обращение» —
    почти всегда падал с ошибкой из-за бага с ticket_id=None у переживших
    рестарт кнопок (см. close_button). Сотрудники в ответ просто удаляли
    канал руками через Discord, не трогая сайт: канал пропадал, а запись
    Ticket оставалась status="open" НАВСЕГДА — сайт показывал такие
    обращения как открытые сколь угодно долго после того, как их фактически
    закрыли. Кнопка теперь чинится (self.ticket_id разрешается по каналу),
    но эта задача разгребает уже накопившиеся зависшие записи и страхует
    на случай, если канал всё-таки удалят не через бота.

    Существование канала проверяется через guild.get_channel: в отличие от
    участников гильдии (см. sync_members_task), кэш каналов не урезается
    настройками intents/chunking и надёжно отражает реальность без лишнего
    HTTP-запроса на каждый тикет.
    """
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    tickets = await asyncio.to_thread(bot_data.get_open_tickets)
    if not tickets:
        return

    closed_any = False
    for t in tickets:
        channel_id = t.get("discord_channel_id")
        if not channel_id:
            continue
        try:
            channel_exists = guild.get_channel(int(channel_id)) is not None
        except (ValueError, TypeError):
            continue
        if channel_exists:
            continue

        try:
            ok = await asyncio.to_thread(
                bot_data.close_ticket, t["id"], "Авто-закрытие (канал в Discord удалён)",
            )
        except Exception as e:
            logger.warning(f"Не удалось авто-закрыть тикет #{t['id']} с удалённым каналом: {e}")
            continue
        if ok:
            closed_any = True
            logger.info(f"Тикет #{t['id']} авто-закрыт: канал {channel_id} в Discord больше не существует")

    if closed_any:
        await refresh_ticket_status()


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

# pending_close[channel_id] = asyncio.Task — задача закрытия тикета
_pending_close: dict = {}


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
        logger.error(f"Failed to create ticket record: {e}")
        ticket_id = "?"

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


async def _close_ticket_with_retry(ticket_id, closed_by: str) -> bool:
    """One retry, then give up — never delete the ticket channel unless this
    actually succeeded. A stuck-open channel is recoverable; a deleted
    channel with a still-open site-side Ticket row ("phantom ticket") is
    not."""
    for _ in range(2):
        try:
            if await asyncio.to_thread(bot_data.close_ticket, ticket_id, closed_by):
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

        if not ticket_id or not await _close_ticket_with_retry(ticket_id, interaction.user.display_name):
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


def _has_ticket_member_overwrite(overwrite) -> bool:
    is_empty = getattr(overwrite, "is_empty", None)
    if callable(is_empty):
        return not is_empty()
    return any(
        getattr(overwrite, attribute, None) is not None
        for attribute in ("view_channel", "send_messages", "read_message_history")
    )


async def list_ticket_members(channel_id: str) -> dict:
    """Return live guild members with a member-specific overwrite in a ticket channel."""
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
    for member in getattr(guild, "members", []):
        if getattr(member, "bot", False):
            continue
        if not _has_ticket_member_overwrite(channel.overwrites_for(member)):
            continue
        display_avatar = getattr(member, "display_avatar", None)
        avatar_url = str(display_avatar.url) if display_avatar and getattr(display_avatar, "url", None) else None
        members.append({
            "discord_id": str(member.id),
            "username": getattr(member, "name", None),
            "display_name": getattr(member, "display_name", None),
            "avatar_url": avatar_url,
        })
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

    overwrite = channel.overwrites_for(member)
    has_member_overwrite = _has_ticket_member_overwrite(overwrite)
    if not has_member_overwrite:
        return {"status": "already_absent", "member_id": str(member.id)}

    try:
        await channel.set_permissions(
            member,
            overwrite=None,
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
    cooldown_days="Кулдаун повторного создания тикета этого типа (дней, 0 = без кулдауна)"
)
async def slash_close_ticket(interaction: discord.Interaction, minutes: int, answer: str, cooldown_days: int = 0):
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

    # Find ticket by channel
    ticket = await asyncio.to_thread(bot_data.get_ticket_by_channel, channel_id_str)
    ticket_id = ticket["id"] if ticket else None
    ticket_title = (ticket or {}).get("title", "Обращение")
    creator_discord_id = (ticket or {}).get("creator_discord_id")

    await interaction.response.send_message(
        f"🔒 Администратор закрыл тикет, он будет удалён через **{time_str}**...\n"
        f"💬 Ответ: {answer}",
        ephemeral=False
    )

    # На сайт этот ответ иначе никогда не попадает: канал (и все его
    # сообщения) удаляется через delay_seconds, а само сообщение бота
    # on_message пропускает целиком (message.author.bot). Без этого
    # финальное слово модератора терялось — в архиве тикета видно только
    # переписку ДО закрытия, а решение приходится искать в логах Discord,
    # которых для удалённого канала уже не существует. Пишем от имени
    # РЕАЛЬНОГО админа (не "бот"/"system") — это его ответ, бот лишь
    # передаёт его в канал.
    if ticket_id:
        await asyncio.to_thread(
            bot_data.add_ticket_message,
            ticket_id=ticket_id,
            author_discord_id=str(user.id),
            author_username=str(user),
            author_display_name=user.display_name,
            author_avatar_url=str(user.display_avatar.url) if user.display_avatar else None,
            content=f"🔒 Тикет закрыт. Ответ: {answer}",
        )

    async def _do_close():
        await asyncio.sleep(delay_seconds)

        if not ticket_id or not await _close_ticket_with_retry(ticket_id, user.display_name):
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

        # Delete channel
        try:
            await channel.delete(reason=f"Ticket closed by {user}")
        except Exception as e:
            logger.warning(f"Failed to delete ticket channel: {e}")

        _pending_close.pop(channel_id_str, None)
        await refresh_ticket_status()

    task = asyncio.create_task(_do_close())
    _pending_close[channel_id_str] = task


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


# Sync messages from ticket channels to NexusData
@bot.event
async def on_message(message: discord.Message):
    global FORWARD_CHANNEL_ID
    if message.author.bot:
        await bot.process_commands(message)
        return

    # A mute is intentionally enforced by deleting messages instead of changing
    # every channel's permissions. This covers channels created after the mute
    # and leaves every ticket channel available for appeals/support.
    channel_name = getattr(message.channel, "name", "") or ""
    active_mutes = await asyncio.to_thread(bot_data.get_active_mutes)
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
    if message.channel.name and message.channel.name.startswith("ticket-"):
        # Sync message to NexusData - find ticket by channel_id
        channel_id = str(message.channel.id)
        attachments = [{"url": a.url, "filename": a.filename, "content_type": a.content_type} for a in message.attachments]

        # First find ticket_id by channel
        open_tickets = await asyncio.to_thread(bot_data.get_open_tickets)
        ticket_id = None
        matched_ticket = None
        if open_tickets:
            for t in open_tickets:
                if t.get("discord_channel_id") == channel_id:
                    ticket_id = t["id"]
                    matched_ticket = t
                    break

        if ticket_id:
            await asyncio.to_thread(
                bot_data.add_ticket_message,
                ticket_id=ticket_id,
                author_discord_id=str(message.author.id),
                author_username=str(message.author),
                author_display_name=message.author.display_name,
                author_avatar_url=str(message.author.display_avatar.url) if message.author.display_avatar else None,
                content=message.content,
                attachments=attachments,
                discord_message_id=str(message.id),
            )
            # Update status embed
            await refresh_ticket_status()

            # Деактивация кнопки "Закрыть обращение" после ответа админа (п.8): как только
            # в тикете пишет сотрудник с ролью ответчика — кнопка становится неинтерактивной.
            if not message.author.bot and ticket_id:
                config_for_staff = await asyncio.to_thread(bot_data.get_tickets_config)
                if await _user_has_ticket_staff_permission(message.author, message.guild, config_for_staff):
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
            if channel_id in _pending_close and not message.author.bot:
                ticket_type = (matched_ticket or {}).get("type")
                config = await asyncio.to_thread(bot_data.get_tickets_config)
                if await _user_has_ticket_staff_permission(message.author, message.guild, config):
                    task = _pending_close.pop(channel_id, None)
                    if task and not task.done():
                        task.cancel()
                        try:
                            await message.channel.send(
                                f"⏸️ Закрытие тикета автоматически отменено — {message.author.mention} ответил(а) в обращении."
                            )
                        except Exception as e:
                            logger.warning(f"Failed to announce auto-cancel close in {channel_id}: {e}")

    # Department chat channels (item 6): mirror messages from any linked
    # channel into NexusData. The bot's own sent messages (site → Discord,
    # via the "send_message" action) never reach here — message.author.bot
    # already short-circuited this handler above.
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
    """Reverse half of the ticket phantom-row fix: if a ticket channel gets
    deleted OUT OF BAND (manually in Discord, bypassing the close button/
    /close command entirely), nothing else ever tells the site — the
    Ticket row would otherwise stay "open" forever with no channel left to
    act on. Best-effort: only touches tickets that are still open."""
    if not (channel.name and channel.name.startswith("ticket-")):
        return
    open_tickets = await asyncio.to_thread(bot_data.get_open_tickets)
    if not open_tickets:
        return
    channel_id = str(channel.id)
    for t in open_tickets:
        if t.get("discord_channel_id") == channel_id:
            await asyncio.to_thread(bot_data.close_ticket, t["id"], "Discord (канал удалён вручную)")
            await refresh_ticket_status()
            break


@bot.event
async def setup_hook():
    """Register persistent views so buttons work after restart, and sync
    slash commands. Runs on module import (not gated behind __main__ like it
    used to be) since this module is now imported, not exec'd as a script."""
    bot.add_view(TicketPanelView())
    bot.add_view(TicketCloseView())
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")


# ═══════════════════════════════════════════════════════════
# SUPERVISOR — how the rest of the app drives this bot
# ═══════════════════════════════════════════════════════════

_bot_loop: Optional[asyncio.AbstractEventLoop] = None


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
    global _bot_loop

    if not settings.discord_bot_token:
        logger.warning("discord_bot_token not configured — Discord bot will not start")
        return

    _bot_loop = asyncio.get_running_loop()
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
