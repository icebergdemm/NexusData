from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_TRIGGER_WORDS = (
    "ты",
    "тебя",
    "тебе",
    "тобой",
    "твой",
    "твоя",
    "твоё",
    "твое",
    "твои",
    "твоего",
    "твоей",
    "твоему",
    "твоим",
    "твоих",
    "твою",
    "твоими",
)
_TRIGGER_RE = re.compile(r"(?<!\w)(?:" + "|".join(_TRIGGER_WORDS) + r")(?!\w)", re.IGNORECASE)
_FENCED_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\r\n]*`", re.MULTILINE)
_MARKDOWN_URL_RE = re.compile(r"\[[^\]\r\n]*\]\(\s*(?:https?://|discord(?:app)?://)[^\s)]+\s*\)", re.IGNORECASE)
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
_META_EXAMPLE_RE = re.compile(
    r"(?:пример\s+обращения|правил\w*\s+запреща\w*\s+обраща|в\s+русском\s+язык\w*\s+местоимени)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TriggerMatch:
    cleaned_text: str
    tokens: list[str]


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("\u00a0", " ")


def _remove_ignored_regions(text: str) -> str:
    text = _FENCED_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _MARKDOWN_URL_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    return re.sub(r"^\s*>.*$", " ", text, flags=re.MULTILINE)


def find_triggers(content: str | None) -> TriggerMatch:
    """Find explicit second-person pronouns outside obvious non-message regions."""

    normalised = _normalise(content or "")
    cleaned = _remove_ignored_regions(normalised)
    if _META_EXAMPLE_RE.search(cleaned):
        return TriggerMatch(cleaned_text=cleaned, tokens=[])

    folded = cleaned.casefold()
    tokens: list[str] = []
    for match in _TRIGGER_RE.finditer(folded):
        token = match.group(0)
        if token not in tokens:
            tokens.append(token)
    return TriggerMatch(cleaned_text=cleaned, tokens=tokens)

