from __future__ import annotations

import asyncio
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
        channel_id = int(self.config.signals_channel_id)
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        return await self.bot.fetch_channel(channel_id)

    async def validate(self) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if not getattr(self.bot.intents, "message_content", False):
            errors.append("Message Content Intent is disabled")
        target = None
        try:
            target = await self.resolve_target()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            errors.append(f"signals channel unavailable: {type(exc).__name__}")
        if target is None:
            errors.append("signals channel/thread is not configured or unavailable")
        if self.config.moderator_role_id:
            guild = getattr(target, "guild", None) if target is not None else None
            if guild is None or guild.get_role(int(self.config.moderator_role_id)) is None:
                errors.append("moderator role unavailable")
        if self.config.redadmin_emoji_id:
            if self.bot.get_emoji(int(self.config.redadmin_emoji_id)) is None:
                errors.append("redadmin emoji unavailable; reaction lifecycle is degraded")
        else:
            errors.append("redadmin emoji is not configured; reaction lifecycle is degraded")
        if target is not None:
            me = getattr(getattr(target, "guild", None), "me", None)
            if me is not None:
                permissions = target.permissions_for(me)
                for permission in ("view_channel", "send_messages", "embed_links", "read_message_history"):
                    if not getattr(permissions, permission, False):
                        errors.append(f"missing permission: {permission}")
                if isinstance(target, discord.Thread) and not getattr(permissions, "send_messages_in_threads", False):
                    errors.append("missing permission: send_messages_in_threads")
        fatal = any("signals channel" in error or "moderator role" in error or "Message Content" in error for error in errors)
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
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning("address_detector incident_message_unavailable incident_id=%s", record.incident_id)
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
            embed.add_field(name="Изменение исходного сообщения", value=_clip(note, 1024), inline=False)
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
        embed = discord.Embed(title="Возможное нарушение формы общения", color=0xD64545)
        embed.add_field(name="Участник", value=_clip(context.author_display_name, 256), inline=True)
        embed.add_field(name="Discord ID", value=str(context.author_id), inline=True)
        embed.add_field(name="Результат", value=label, inline=True)
        embed.add_field(name="Сработало", value=_clip(", ".join(context.trigger_tokens), 1024), inline=True)
        embed.add_field(name="Сообщение", value=_clip(f"> {context.raw_content}", 1024), inline=False)
        lines = []
        for signal in reversed(incident.get("signals", [])):
            lines.append(f"> {signal['raw_content']} ([перейти]({signal['jump_url']})" if signal.get("jump_url") else f"> {signal['raw_content']}")
        if incident.get("message_count", 0) > len(lines):
            lines.append(f"+ ещё {incident['message_count'] - len(lines)} сообщений")
        embed.add_field(name="Контекст incident", value=_clip("\n".join(lines) or "нет", 1024), inline=False)
        embed.set_footer(text=f"Автоматический сигнал. Не является взысканием. classifier={result.classifier_version} confidence={result.confidence}")
        return embed
