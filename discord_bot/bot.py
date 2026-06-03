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

import discord
from discord.ext import commands, tasks
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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Anti-raid tracking
_join_timestamps = []  # list of join times in last 60 seconds
RAID_THRESHOLD = 5  # joins per minute to trigger alert
RAID_WINDOW = 60  # seconds

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
    publish_rating_task.start()

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
    global FORWARD_CHANNEL_ID

    # Reload forward channel config
    config = await api_get("/api/v1/bot/config")
    if config and config.get("forward_channel_id"):
        try:
            FORWARD_CHANNEL_ID = int(config["forward_channel_id"])
        except (ValueError, TypeError):
            pass

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
        payload = action.get("payload", {})

        success = await execute_bot_action(action_type, payload)
        await api_post(f"/api/v1/bot/actions/{action_id}/ack", {
            "success": success,
            "error_message": None if success else "Execution failed",
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

            name = entry["player_name"]
            if entry.get("discord_id"):
                name = f"<@{entry['discord_id']}>"

            lines.append(f"{medal}  {name}  -  **{entry['rating']}**")

        description = "\n\n".join(lines) if lines else "Нет данных"
        emoji = role_emojis.get(role, "")
        title = f"{emoji} Рейтинг по роли {role_names.get(role, role)}"

        embed = discord.Embed(
            title=title,
            description=description,
            color=0x1a68de,
        )
        now_str = datetime.utcnow().strftime("%d.%m.%Y %H:%M")
        embed.set_footer(text=f"Сделано за сухарики | Обновлено {now_str}")

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
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Всего открытых", value=str(total), inline=True)
    embed.add_field(name="Ожидают ответа", value=str(awaiting), inline=True)
    embed.set_footer(text="Сделано за сухарики")

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


# ═══════════════════════════════════════════════════════════
# BOT ACTION EXECUTOR
# ═══════════════════════════════════════════════════════════

async def execute_bot_action(action_type: str, payload: dict) -> bool:
    """Execute a bot action from NexusData queue."""
    try:
        if action_type == "send_message":
            channel_id = int(payload.get("channel_id", 0))
            content = payload.get("content", "")
            channel = bot.get_channel(channel_id)
            if channel:
                await channel.send(content[:2000])
                return True

        elif action_type == "send_embed":
            channel_id = int(payload.get("channel_id", 0))
            embed_data = payload.get("embed", {})
            channel = bot.get_channel(channel_id)
            if channel:
                embed = discord.Embed.from_dict(embed_data)
                await channel.send(embed=embed)
                return True

        elif action_type == "update_rank_role":
            # Update Discord roles when player rank changes
            guild = bot.get_guild(GUILD_ID)
            if not guild:
                return False
            discord_id = payload.get("discord_id")
            remove_role_id = payload.get("remove_role_id")
            add_role_id = payload.get("add_role_id")
            if not discord_id:
                return True

            member = guild.get_member(int(discord_id))
            if not member:
                try:
                    member = await guild.fetch_member(int(discord_id))
                except Exception:
                    logger.warning(f"Member {discord_id} not found in guild")
                    return True  # Don't fail - member might have left

            # Remove old rank role
            if remove_role_id:
                old_role = guild.get_role(int(remove_role_id))
                if old_role and old_role in member.roles:
                    try:
                        await member.remove_roles(old_role, reason=f"Rank changed: {payload.get('old_rank')} -> {payload.get('new_rank')}")
                    except Exception as e:
                        logger.warning(f"Failed to remove role {remove_role_id}: {e}")

            # Add new rank role
            if add_role_id:
                new_role = guild.get_role(int(add_role_id))
                if new_role and new_role not in member.roles:
                    try:
                        await member.add_roles(new_role, reason=f"Rank changed: {payload.get('old_rank')} -> {payload.get('new_rank')}")
                    except Exception as e:
                        logger.warning(f"Failed to add role {add_role_id}: {e}")

            logger.info(f"Rank role updated for {member}: -{remove_role_id} +{add_role_id}")
            return True

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
                return True  # Don't fail - just no channel set

            channel = bot.get_channel(trap_channel_id)
            if not channel:
                logger.warning(f"Trap channel {trap_channel_id} not found")
                return True

            embed = discord.Embed(
                title="🪤 Ловушка сработала!",
                color=0xff4444,
            )
            embed.add_field(name="Игрок", value=payload.get("player_name", "-"), inline=True)
            embed.add_field(name="SteamID", value=f"`{payload.get('steamid', '-')}`", inline=True)
            embed.add_field(name="Player ID", value=str(payload.get("player_id", "-")), inline=True)
            embed.add_field(name="Причина", value=payload.get("reason", "-"), inline=False)
            embed.timestamp = datetime.utcnow()
            embed.set_footer(text="NexusData Trap System")

            await channel.send(content="<@&1506729102713950288>", embed=embed)
            return True

        elif action_type == "kick_member":
            guild = bot.get_guild(GUILD_ID)
            member_id = int(payload.get("discord_id", 0))
            reason = payload.get("reason", "NexusData action")
            if guild:
                member = guild.get_member(member_id)
                if member:
                    await member.kick(reason=reason)
                    return True

        elif action_type == "ban_member":
            guild = bot.get_guild(GUILD_ID)
            member_id = int(payload.get("discord_id", 0))
            reason = payload.get("reason", "NexusData action")
            if guild:
                member = guild.get_member(member_id)
                if member:
                    await member.ban(reason=reason)
                    return True

        elif action_type == "ticket_send_message":
            channel_id = int(payload.get("channel_id", 0))
            content = payload.get("content", "")
            channel = bot.get_channel(channel_id)
            if channel and content:
                await channel.send(content[:2000])
                return True

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
                return True

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
                return True

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
                    return True
                except (discord.NotFound, discord.HTTPException) as e:
                    logger.warning(f"Failed to edit embed: {e}")
                    return False

        elif action_type == "delete_message":
            # Delete a message from a channel
            channel_id = int(payload.get("channel_id", 0))
            message_id = payload.get("message_id")
            channel = bot.get_channel(channel_id)
            if channel and message_id:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    await msg.delete()
                    return True
                except (discord.NotFound, discord.HTTPException):
                    return True  # Already gone

        logger.warning(f"Unknown or failed bot action: {action_type}")
        return False

    except Exception as e:
        logger.error(f"Bot action {action_type} failed: {e}")
        return False


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
}


class TicketModal(discord.ui.Modal):
    def __init__(self, ticket_type: str):
        tinfo = TICKET_TYPES.get(ticket_type, {})
        super().__init__(title=f"{tinfo.get('emoji', '🎫')} {tinfo.get('name', 'Обращение')}")
        self.ticket_type = ticket_type
        self.title_input = discord.ui.TextInput(
            label="Краткое описание проблемы",
            placeholder="Опишите в нескольких словах...",
            max_length=200,
            style=discord.TextStyle.short,
        )
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        title = self.title_input.value
        guild = interaction.guild
        user = interaction.user

        # Check if user is admin (skip cooldown)
        is_admin = user.guild_permissions.administrator if hasattr(user, 'guild_permissions') else False

        # Check: already has open ticket in this category
        open_tickets = await api_get("/api/v1/tickets/open")
        if open_tickets:
            for t in open_tickets:
                if t.get("creator_discord_id") == str(user.id) and t.get("type") == self.ticket_type:
                    await interaction.followup.send(
                        f"❌ У вас уже есть открытое обращение в этой категории. Закройте его перед созданием нового.",
                        ephemeral=True
                    )
                    return

            # Check: 5 minute cooldown (non-admins only)
            if not is_admin:
                from datetime import datetime, timedelta
                user_tickets = [t for t in open_tickets if t.get("creator_discord_id") == str(user.id)]
                # Also check recently closed tickets
                all_user_tickets = [t for t in open_tickets if t.get("creator_discord_id") == str(user.id)]
                for t in open_tickets:
                    if t.get("creator_discord_id") == str(user.id) and t.get("created_at"):
                        try:
                            created = datetime.fromisoformat(t["created_at"])
                            if datetime.utcnow() - created < timedelta(minutes=5):
                                await interaction.followup.send(
                                    f"❌ Подождите 5 минут перед созданием нового обращения.",
                                    ephemeral=True
                                )
                                return
                        except (ValueError, TypeError):
                            pass

        # Load config
        config = await api_get("/api/v1/tickets/config")
        bot_config = await api_get("/api/v1/bot/config")
        if not config:
            await interaction.followup.send("Ошибка загрузки конфигурации", ephemeral=True)
            return

        # Category from bot config (set via !setticketcategory)
        category_id = (bot_config or {}).get("ticket_category_id") or config.get("category_id")
        type_config = config.get("types", {}).get(self.ticket_type, {})
        ping_roles = type_config.get("ping_roles", [])
        template = type_config.get("template", "Опишите вашу проблему подробно.")

        # Create channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        # Add ping roles to overwrites
        for role_id in ping_roles:
            try:
                role = guild.get_role(int(role_id))
                if role:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            except (ValueError, TypeError):
                pass

        category = guild.get_channel(int(category_id)) if category_id else None
        channel_name = f"ticket-{user.name[:10]}-{self.ticket_type[:8]}"

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Тикет: {title} | {user.display_name}",
            )
        except Exception as e:
            await interaction.followup.send(f"Ошибка создания канала: {e}", ephemeral=True)
            return

        # Create ticket in NexusData
        result = await api_post("/api/v1/tickets/create", {
            "ticket_type": self.ticket_type,
            "title": title,
            "creator_discord_id": str(user.id),
            "creator_username": str(user),
            "creator_display_name": user.display_name,
            "discord_channel_id": str(channel.id),
        })

        ticket_id = result.get("ticket_id", "?") if result else "?"

        # Send welcome message with close button
        tinfo = TICKET_TYPES.get(self.ticket_type, {})
        # Role pings wrapped in spoiler tags (hidden text), user mention normal
        silent_pings = " ".join(f"||<@&{rid}>||" for rid in ping_roles if rid)

        embed = discord.Embed(
            title=f"{tinfo.get('emoji', '🎫')} {tinfo.get('name', 'Обращение')} #{ticket_id}",
            description=f"{user.mention}, опишите ситуацию в данном канале по следующему шаблону:\n\n```\n{template}\n```\n\nВ случае, если вы ошибочно создали обращение, нажмите кнопку **Закрыть обращение**.",
            color=tinfo.get("color", 0x2b7af0),
        )
        embed.set_footer(text=f"Тикет #{ticket_id} | {title}")

        view = TicketCloseView(ticket_id=ticket_id, channel=channel)
        msg_content = f"{user.mention} {silent_pings}" if silent_pings.strip() else user.mention
        await channel.send(content=msg_content, embed=embed, view=view)

        await interaction.followup.send(f"✅ Обращение создано: {channel.mention}", ephemeral=True)

        # Update status embed
        await refresh_ticket_status()


class TicketCloseView(discord.ui.View):
    def __init__(self, ticket_id=None, channel=None):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.channel = channel

    @discord.ui.button(label="Закрыть обращение", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_btn")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Тикет закрывается...", ephemeral=True)

        channel = interaction.channel
        channel_id = str(channel.id)

        # Close in NexusData (find ticket by channel_id)
        # Use the stored ticket_id if available, otherwise let API find by channel
        ticket_id = self.ticket_id
        if ticket_id:
            await api_post(f"/api/v1/tickets/{ticket_id}/close", {
                "closed_by": interaction.user.display_name,
            })

        # Update status embed
        await refresh_ticket_status()

        # Delete channel after short delay
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
        await interaction.response.send_modal(TicketModal("help"))

    @discord.ui.button(label="Жалоба на игрока", style=discord.ButtonStyle.secondary, emoji="⚠️", custom_id="ticket_player_report")
    async def btn_player_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("player_report"))

    @discord.ui.button(label="Сообщить о баге", style=discord.ButtonStyle.success, emoji="🐛", custom_id="ticket_bug")
    async def btn_bug(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("bug"))

    @discord.ui.button(label="Жалоба на администрацию", style=discord.ButtonStyle.danger, emoji="🛡️", custom_id="ticket_admin_report")
    async def btn_admin_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("admin_report"))

    @discord.ui.button(label="Жалоба на отделы", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="ticket_department_report")
    async def btn_department_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("department_report"))

    @discord.ui.button(label="Обжалование наказания", style=discord.ButtonStyle.secondary, emoji="⚖️", custom_id="ticket_appeal")
    async def btn_appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("appeal"))


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
            "⚖️ **Обжалование наказания** - если считаете наказание несправедливым\n\n"
            "После создания обращения вам будет создан приватный канал."
        ),
        color=0x1a68de,
    )
    embed.set_footer(text="Сделано за сухарики")

    view = TicketPanelView()
    await ctx.send(embed=embed, view=view)
    await ctx.message.delete()


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

    # Forward to designated channel (existing logic)
    global FORWARD_CHANNEL_ID
    if FORWARD_CHANNEL_ID and message.channel.id != FORWARD_CHANNEL_ID:
        forward_ch = bot.get_channel(FORWARD_CHANNEL_ID)
        if forward_ch:
            embed = discord.Embed(
                description=message.content[:4000] if message.content else None,
                color=0x2b2d31,
                timestamp=message.created_at,
            )
            embed.set_author(
                name=f"{message.author.display_name} в #{message.channel.name}",
                icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
            )
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

    bot.run(TOKEN)
