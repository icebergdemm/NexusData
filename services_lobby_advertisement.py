"""Pure decision and message-building helpers for lobby advertisements."""

from __future__ import annotations

import re
from datetime import datetime


MAX_LOBBY_AD_LENGTH = 200
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_INFLECTION_SUFFIXES = {
    "а", "ам", "ами", "ах", "е", "ем", "и", "ию", "ия", "ие", "ием",
    "ой", "ою", "ом", "у", "ы", "ь", "ов", "м", "ми", "х",
}


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(str(value or "").casefold())


def _token_matches_keyword(token: str, keyword: str) -> bool:
    if token == keyword:
        return True
    if len(keyword) < 4 or not token.startswith(keyword):
        return False
    return token[len(keyword):] in _INFLECTION_SUFFIXES


def lobby_keyword_matches(text: str, keywords: list[str]) -> bool:
    """Return whether text contains one configured keyword or its simple inflection."""
    text_tokens = _tokens(text)
    if not text_tokens:
        return False
    for keyword in keywords or []:
        keyword_tokens = _tokens(keyword)
        if not keyword_tokens:
            continue
        if all(
            any(_token_matches_keyword(token, keyword_token) for token in text_tokens)
            for keyword_token in keyword_tokens
        ):
            return True
    return False


def decide_lobby_ad_action(
    *,
    now: datetime,
    due_at: datetime,
    keyword_hit: bool,
    has_players: bool,
    server_running: bool,
    round_active: bool,
) -> tuple[str, str]:
    """Choose the next terminal action for one pending lobby cycle."""
    if round_active:
        return "skip", "round_started"

    timer_due = now >= due_at
    if not keyword_hit and not timer_due:
        return "wait", "pending"

    if not has_players:
        return "skip", "no_players"
    if not server_running:
        return "skip", "server_offline"
    return "send", "keyword" if keyword_hit and not timer_due else "timer"


def build_lobby_ad_message(template: str, discord_link: str) -> str:
    message = str(template or "").replace("{discord_link}", str(discord_link or "").strip())
    message = " ".join(message.split())
    if not message:
        raise ValueError("Lobby advertisement text must not be empty")
    if len(message) > MAX_LOBBY_AD_LENGTH:
        raise ValueError(f"Lobby advertisement text must be at most {MAX_LOBBY_AD_LENGTH} characters")
    return message


def build_lobby_ad_command(message: str) -> str:
    normalized = " ".join((message or "").split())
    if not normalized:
        raise ValueError("Lobby advertisement text must not be empty")
    if len(normalized) > MAX_LOBBY_AD_LENGTH:
        raise ValueError(f"Lobby advertisement text must be at most {MAX_LOBBY_AD_LENGTH} characters")
    escaped = normalized.replace("\\", "\\\\").replace('"', '\\"')
    return f'msg_box "{escaped}"'
