from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import logging
import re
from typing import Protocol

logger = logging.getLogger("NexusBot.address_detector")


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

    _IDIOMATIC_EXCLAMATION_RE = re.compile(r"^\s*ух\s+ты\s*[.!?…]*\s*$", re.IGNORECASE)
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
        if self._IDIOMATIC_EXCLAMATION_RE.fullmatch(text):
            return AnalysisResult(
                Verdict.CLEAN,
                0.99,
                "standalone idiomatic exclamation",
                "rules",
                ("idiomatic_exclamation",),
            )
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


class HybridAnalyzer:
    def __init__(
        self,
        rules: Analyzer,
        model: Analyzer | None,
        *,
        likely_threshold: float = 0.75,
        clean_threshold: float = 0.80,
    ) -> None:
        self.rules = rules
        self.model = model
        self.likely_threshold = likely_threshold
        self.clean_threshold = clean_threshold

    async def analyze(self, context: object) -> AnalysisResult:
        rule_result = await self.rules.analyze(context)
        if rule_result.verdict is not Verdict.UNCERTAIN or self.model is None:
            return rule_result
        try:
            model_result = await self.model.analyze(context)
        except Exception:
            logger.exception("address_detector model_error")
            return AnalysisResult.uncertain("model error", classifier_version="onnx-fallback")
        confidence = model_result.confidence
        if model_result.verdict is Verdict.LIKELY_VIOLATION and confidence is not None and confidence >= self.likely_threshold:
            return model_result
        if model_result.verdict is Verdict.CLEAN and confidence is not None and confidence >= self.clean_threshold:
            return model_result
        return AnalysisResult(
            Verdict.UNCERTAIN,
            confidence,
            model_result.reason or "model confidence is between thresholds",
            model_result.classifier_version,
            model_result.matched_signals,
        )
