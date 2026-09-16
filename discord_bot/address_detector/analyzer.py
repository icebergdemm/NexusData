from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
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

