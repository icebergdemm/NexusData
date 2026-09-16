from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Protocol


class Verdict(StrEnum):
    CLEAN = "CLEAN"
    UNCERTAIN = "UNCERTAIN"
    LIKELY_VIOLATION = "LIKELY_VIOLATION"


@dataclass(frozen=True)
class AnalysisResult:
    verdict: Verdict
    confidence: float | None = None
    reason: str = ""
    classifier_version: str = "rules"
    matched_signals: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def uncertain(cls, reason: str, *, classifier_version: str = "fallback") -> "AnalysisResult":
        return cls(
            verdict=Verdict.UNCERTAIN,
            reason=reason,
            classifier_version=classifier_version,
        )


class Analyzer(Protocol):
    async def analyze(self, context: object) -> AnalysisResult:
        """Classify a collected address-detector context."""


class RuleAnalyzer:
    """Explainable rules for the obvious cases before optional local ML."""

    _META_RE = re.compile(
        r"(?:буквально\s+написал|цитат\w*|пример\w*\s+(?:плохого\s+)?сообщени|"
        r"в\s+русском\s+язык\w*\s+местоимени|правил\w*\s+запреща\w*\s+обраща|"
        r"что\s+означа\w*\s+слов\w*)",
        re.IGNORECASE,
    )

    async def analyze(self, context: object) -> AnalysisResult:
        tokens = tuple(getattr(context, "trigger_tokens", ()))
        text = getattr(context, "cleaned_content", "")
        if not tokens:
            return AnalysisResult(Verdict.CLEAN, 0.99, "no trigger tokens", "rules")
        if self._META_RE.search(text):
            return AnalysisResult(
                Verdict.CLEAN,
                0.93,
                "metalinguistic or quoted use",
                "rules",
                ("meta_discussion",),
            )

        reply = getattr(context, "reply", None)
        mentions = getattr(context, "mentions", ())
        if reply is not None or mentions:
            signals = tuple(
                signal
                for signal, present in (("reply", reply is not None), ("mention", bool(mentions)))
                if present
            )
            return AnalysisResult(
                Verdict.LIKELY_VIOLATION,
                0.9,
                "direct second-person address signal",
                "rules",
                signals,
            )
        return AnalysisResult(
            Verdict.UNCERTAIN,
            0.5,
            "isolated pronoun without direct addressee",
            "rules",
            ("isolated_pronoun",),
        )
