from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScopeConfig:
    """Runtime scope. Channel IDs are the only source of monitoring truth."""

    monitored_channel_ids: set[int] = field(default_factory=set)
    monitored_category_ids: set[int] = field(default_factory=set)
    excluded_channel_ids: set[int] = field(default_factory=set)


def _is_system_message(message: object) -> bool:
    message_type = getattr(message, "type", None)
    if message_type is None:
        return False
    return getattr(message_type, "name", str(message_type)) != "default"


def in_scope(message: object, config: ScopeConfig) -> bool:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    author = getattr(message, "author", None)
    channel_id = getattr(channel, "id", None)
    if guild is None or channel_id is None:
        return False
    if channel_id in config.excluded_channel_ids or channel_id not in config.monitored_channel_ids:
        return False
    if getattr(author, "bot", False) or getattr(message, "webhook_id", None) is not None:
        return False
    return not _is_system_message(message)
