from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import discord

from .analyzer import AnalysisResult, Verdict
from .config import DetectorConfig
from .context import AddressContext
from .persistence import AddressDetectorStore, AnalysisRecord

logger = logging.getLogger("NexusBot.address_detector")


def _clip(value: str, limit: int) -> str:
    value = value or ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _safe(value: str) -> str:
    return (value or "").replace("@", "@\u200b")


class DiscordReactionPort:
    def __init__(self, bot: discord.Client, config: DetectorConfig) -> None:
        self.bot = bot
        self.config = config

    def emoji(self) -> discord.PartialEmoji:
        if not self.config.redadmin_emoji_id:
            raise RuntimeError("redadmin emoji is not configured")
        return discord.PartialEmoji(name="redadmin", id=int(self.config.redadmin_emoji_id))

    async def add(self, message: discord.Message) -> None:
        await message.add_reaction(self.emoji())

    async def remove(self, message: discord.Message) -> None:
        if not self.config.redadmin_emoji_id:
            return
        await message.remove_reaction(self.emoji(), self.bot.user)


class DiscordNotifier:
    def __init__(self, bot: discord.Client, config: DetectorConfig, store: AddressDetectorStore) -> None:
        self.bot = bot
        self.config = config
        self.store = store

    async def resolve_target(self) -> discord.abc.GuildChannel | discord.Thread | None:
        if not self.config.signals_channel_id:
            return None
        return await self._resolve_channel(int(self.config.signals_channel_id))

    async def _resolve_channel(self, channel_id: int) -> discord.abc.GuildChannel | discord.Thread | None:
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel

        # Active threads can be present in the guild cache without being in
        # Client.get_channel(). This is the exact shape returned by the live
        # viewer picker, so consult guild/thread caches before REST.
        for guild in getattr(self.bot, "guilds", ()):
            for getter_name in ("get_channel", "get_thread"):
                getter = getattr(guild, getter_name, None)
                if getter is not None:
                    channel = getter(channel_id)
                    if channel is not None:
                        return channel
            channel = next(
                (thread for thread in getattr(guild, "threads", ()) if int(getattr(thread, "id", 0)) == channel_id),
                None,
            )
            if channel is not None:
                return channel

        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

    async def validate(self) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if not getattr(self.bot.intents, "message_content", False):
            errors.append("Message Content Intent is disabled")
        target = None
        target = await self.resolve_target()
        if target is None and self.config.signals_channel_id:
            errors.append("signals channel unavailable")
        if target is None:
            errors.append("signals channel/thread is not configured or unavailable")
        guild = getattr(target, "guild", None) if target is not None else None
        if not self.config.moderator_role_id:
            errors.append("warning: moderator role is not configured; role ping is disabled")
        elif guild is None or guild.get_role(int(self.config.moderator_role_id)) is None:
            errors.append("warning: moderator role unavailable; role ping is disabled")
        if self.config.redadmin_emoji_id:
            if self.bot.get_emoji(int(self.config.redadmin_emoji_id)) is None:
                errors.append("warning: redadmin emoji unavailable; reaction lifecycle is degraded")
        else:
            errors.append("warning: redadmin emoji is not configured; reaction lifecycle is degraded")
        if target is not None:
            me = getattr(getattr(target, "guild", None), "me", None)
            if me is not None:
                permissions = target.permissions_for(me)
                for permission in ("view_channel", "send_messages", "embed_links", "read_message_history"):
                    if not getattr(permissions, permission, False):
                        errors.append(f"missing permission: {permission}")
                if isinstance(target, discord.Thread) and not getattr(permissions, "send_messages_in_threads", False):
                    errors.append("missing permission: send_messages_in_threads")
        for channel_id in self.config.monitored_channel_ids:
            monitored = self.bot.get_channel(channel_id)
            if monitored is not None:
                pass
            else:
                try:
                    monitored = await self.bot.fetch_channel(channel_id)
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    monitored = None
            if monitored is None:
                errors.append(f"warning: monitored channel unavailable: {channel_id}")
                continue
            monitored_guild = getattr(monitored, "guild", None)
            monitored_me = getattr(monitored_guild, "me", None)
            if monitored_me is not None and hasattr(monitored, "permissions_for"):
                permissions = monitored.permissions_for(monitored_me)
                for permission in ("view_channel", "read_message_history", "add_reactions"):
                    if not getattr(permissions, permission, False):
                        errors.append(f"warning: monitored channel {channel_id} missing permission: {permission}")
        fatal = any(not error.startswith("warning:") for error in errors)
        return not fatal, errors

    async def send_signal(self, context: AddressContext, result: AnalysisResult, record: AnalysisRecord) -> None:
        target = await self.resolve_target()
        if target is None:
            raise RuntimeError("signals target unavailable")
        incident = await asyncio.to_thread(self.store.get_incident, record.incident_id) if record.incident_id else None
        if incident is None:
            raise RuntimeError("incident snapshot unavailable")

        embed = self._embed(context, result, incident)
        role = None
        if self.config.moderator_role_id:
            role = getattr(getattr(target, "guild", None), "get_role", lambda _id: None)(int(self.config.moderator_role_id))
        allowed_mentions = discord.AllowedMentions(
            roles=[role] if role is not None and record.should_ping_moderators else [],
            users=False,
            everyone=False,
            replied_user=False,
        )
        content = role.mention if role is not None and record.should_ping_moderators else None
        existing_id = incident.get("admin_message_id")
        if existing_id:
            try:
                admin_message = await target.fetch_message(int(existing_id))
                await admin_message.edit(embed=embed, allowed_mentions=allowed_mentions)
                await asyncio.to_thread(self.store.set_admin_message_id, context.message_id, int(existing_id))
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning("address_detector incident_message_unavailable incident_id=%s", record.incident_id)
        marker = self._action_marker(result)
        if marker and hasattr(target, "history"):
            try:
                history = target.history(limit=None, after=context.timestamp) if context.timestamp else target.history(limit=100)
                async for previous in history:
                    if any(marker in (item.footer.text or "") for item in getattr(previous, "embeds", ())):
                        await asyncio.to_thread(self.store.set_admin_message_id, context.message_id, int(previous.id))
                        return
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                logger.warning("address_detector action_dedup_history_unavailable incident_id=%s", record.incident_id)
                raise
        sent = await self._send_with_retry(target, content, embed, allowed_mentions)
        await asyncio.to_thread(self.store.set_admin_message_id, context.message_id, sent.id)

    async def mark_edit_clean(self, message_id: int) -> None:
        signal = await asyncio.to_thread(self.store.get_signal, message_id)
        if signal is None or not signal.admin_message_id:
            return
        await self._mark_signal(signal.admin_message_id, signal.status_note or "Текущая версия больше не срабатывает")

    async def mark_deleted(self, message_id: int) -> None:
        signal = await asyncio.to_thread(self.store.get_signal, message_id)
        if signal is None or not signal.admin_message_id:
            return
        await self._mark_signal(signal.admin_message_id, signal.status_note or "Исходное сообщение удалено автором/недоступно")

    async def _mark_signal(self, admin_message_id: str, note: str) -> None:
        target = await self.resolve_target()
        if target is None:
            return
        try:
            message = await target.fetch_message(int(admin_message_id))
            embed = message.embeds[0].copy() if message.embeds else discord.Embed(title="Сигнал модерации")
            fields = [field for field in embed.fields if field.name != "Состояние исходного сообщения"]
            embed.clear_fields()
            for field in fields:
                embed.add_field(name=field.name, value=field.value, inline=field.inline)
            embed.add_field(name="Состояние исходного сообщения", value=_clip(note, 1024), inline=False)
            await message.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.warning("address_detector signal_update_failed admin_message_id=%s", admin_message_id)

    async def _send_with_retry(self, target: Any, content: str | None, embed: discord.Embed, allowed_mentions: discord.AllowedMentions):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await target.send(content=content, embed=embed, allowed_mentions=allowed_mentions)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise last_error or RuntimeError("signal delivery failed")

    @staticmethod
    def _embed(context: AddressContext, result: AnalysisResult, incident: dict) -> discord.Embed:
        label = {
            Verdict.UNCERTAIN: "Требует проверки",
            Verdict.LIKELY_VIOLATION: "Вероятное нарушение",
            Verdict.CLEAN: "Чисто",
        }[result.verdict]
        embed = discord.Embed(title=f"Возможное нарушение формы общения — {label}", url=context.jump_url or None, color=0xD64545)
        quote = _clip(_safe(context.raw_content), 600) or "(сообщение без текста)"
        if context.jump_url:
            embed.description = f"[Открыть исходное сообщение]({context.jump_url})\n> {quote}"
        else:
            embed.description = f"> {quote}"
        embed.add_field(name="Участник", value=_clip(_safe(context.author_display_name), 256), inline=True)
        embed.add_field(name="Канал", value=f"<#{context.channel_id}>" if context.channel_id else "-", inline=True)
        lines: list[str] = []
        current_message_id = str(context.message_id)
        for signal in reversed(incident.get("signals", [])):
            if str(signal.get("message_id")) == current_message_id:
                continue
            if signal.get("jump_url"):
                lines.append(f"[Предыдущее сообщение]({signal['jump_url']})")
            if len(lines) >= 3:
                break
        omitted = max(0, int(incident.get("message_count", 0)) - 1 - len(lines))
        if omitted:
            lines.append(f"+ ещё {omitted} сообщений")
        context_value = "\n".join(lines) or "нет предыдущих сообщений"
        embed.add_field(name="Контекст incident", value=_clip(context_value, 1024), inline=False)
        footer = "Автоматический сигнал. Не является взысканием"
        marker = DiscordNotifier._action_marker(result)
        if marker:
            footer += f" · {marker}"
        embed.set_footer(text=footer)
        return embed

    @staticmethod
    def _action_marker(result: AnalysisResult) -> str | None:
        if not result.idempotency_key:
            return None
        return "AD-" + hashlib.sha256(result.idempotency_key.encode()).hexdigest()[:24]
