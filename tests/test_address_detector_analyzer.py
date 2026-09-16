import asyncio
from types import SimpleNamespace

from discord_bot.address_detector.analyzer import AnalysisResult, HybridAnalyzer, Verdict


class Rules:
    async def analyze(self, context):
        return AnalysisResult.uncertain("rules need model")


class Model:
    def __init__(self, result):
        self.result = result

    async def analyze(self, context):
        return self.result


def test_hybrid_uses_likely_threshold() -> None:
    result = asyncio.run(
        HybridAnalyzer(
            Rules(), Model(AnalysisResult(Verdict.LIKELY_VIOLATION, 0.76, "model", "onnx")),
            likely_threshold=0.75,
            clean_threshold=0.80,
        ).analyze(SimpleNamespace())
    )

    assert result.verdict is Verdict.LIKELY_VIOLATION


def test_hybrid_falls_back_to_uncertain_without_model() -> None:
    result = asyncio.run(HybridAnalyzer(Rules(), None).analyze(SimpleNamespace()))

    assert result.verdict is Verdict.UNCERTAIN
