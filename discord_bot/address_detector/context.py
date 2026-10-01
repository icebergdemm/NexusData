from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .filter import TriggerMatch


@dataclass(frozen=True)
class MessageSnapshot:
    message_id: int
    author_id: int | None
    author_display_name: str
    content: str
    created_at: datetime | None
    jump_url: str | None


@dataclass(frozen=True)
class AddressContext:
    message_id: int
    guild_id: int | None
    channel_id: int | None
    author_id: int | None
    author_display_name: str
    timestamp: datetime | None
    raw_content: str
    cleaned_content: str
    trigger_tokens: tuple[str, ...]
    is_reply: bool
    reply: MessageSnapshot | None
    reply_author_id: int | None
    mentions: tuple[str, ...]
    previous_messages: tuple[MessageSnapshot, ...]
    jump_url: str | None


def _snapshot(message: object | None) -> MessageSnapshot | None:
    if message is None:
        return None
    author = getattr(message, "author", None)
    display_name = (
        getattr(author, "display_name", None)
        or getattr(author, "global_name", None)
        or getattr(author, "name", None)
        or str(getattr(author, "id", "unknown"))
    )
    return MessageSnapshot(
        message_id=int(getattr(message, "id", 0)),
        author_id=getattr(author, "id", None),
        author_display_name=str(display_name),
        content=str(getattr(message, "content", "")),
        created_at=getattr(message, "created_at", None),
        jump_url=getattr(message, "jump_url", None),
    )


class ContextCollector:
    def __init__(self, history_limit: int = 4) -> None:
        self.history_limit = max(0, min(history_limit, 5))

    async def collect(
        self, message: object, match: TriggerMatch, base: AddressContext | None = None
    ) -> AddressContext:
        reference = getattr(message, "reference", None)
        reply = getattr(reference, "resolved", None) if reference is not None else None
        if reply is None and reference is not None and getattr(reference, "message_id", None) is not None:
            try:
                reply = await getattr(message, "channel").fetch_message(reference.message_id)
            except Exception:
                reply = None

        previous: list[MessageSnapshot] = []
        if self.history_limit:
            try:
                async for row in getattr(message, "channel").history(limit=self.history_limit, before=message):
                    snapshot = _snapshot(row)
                    if snapshot is not None:
                        previous.append(snapshot)
            except Exception:
                previous = []

        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        guild = getattr(message, "guild", None)
        display_name = (
            getattr(author, "display_name", None)
            or getattr(author, "global_name", None)
            or getattr(author, "name", None)
            or str(getattr(author, "id", "unknown"))
        )
        mentions = tuple(str(getattr(mention, "id")) for mention in getattr(message, "mentions", ()))
        reply_snapshot = _snapshot(reply)
        return AddressContext(
            message_id=base.message_id if base else int(getattr(message, "id")),
            guild_id=base.guild_id if base else getattr(guild, "id", None),
            channel_id=base.channel_id if base else getattr(channel, "id", None),
            author_id=base.author_id if base else getattr(author, "id", None),
            author_display_name=base.author_display_name if base else str(display_name),
            timestamp=base.timestamp if base else getattr(message, "created_at", None),
            raw_content=base.raw_content if base else str(getattr(message, "content", "")),
            cleaned_content=base.cleaned_content if base else match.cleaned_text,
            trigger_tokens=base.trigger_tokens if base else tuple(match.tokens),
            is_reply=reply_snapshot is not None,
            reply=reply_snapshot,
            reply_author_id=reply_snapshot.author_id if reply_snapshot else None,
            mentions=base.mentions if base else mentions,
            previous_messages=tuple(previous),
            jump_url=getattr(message, "jump_url", None),
        )
