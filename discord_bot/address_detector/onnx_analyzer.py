from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np

from .analyzer import AnalysisResult, Verdict


VIOLATION_HYPOTHESIS = (
    "Автор напрямую обращается к другому участнику на ты или использует "
    "неформальную форму второго лица единственного числа."
)
CLEAN_HYPOTHESIS = (
    "Форма ты используется как цитата, пример, обсуждение слова либо не "
    "является обращением к участнику."
)


class OnnxNliAnalyzer:
    def __init__(
        self,
        session: Any,
        tokenizer: Any,
        *,
        model_version: str = "onnx",
        entailment_index: int = 0,
    ) -> None:
        self.session = session
        self.tokenizer = tokenizer
        self.model_version = model_version
        self.entailment_index = entailment_index

    @classmethod
    def from_files(cls, model_path: str, tokenizer_path: str = "") -> "OnnxNliAnalyzer":
        model = Path(model_path)
        if not model.is_file():
            raise FileNotFoundError(f"ONNX model not found: {model}")
        tokenizer_file = Path(tokenizer_path) if tokenizer_path else model.parent / "tokenizer.json"
        if not tokenizer_file.is_file():
            raise FileNotFoundError(f"tokenizer not found: {tokenizer_file}")
        config_file = model.parent / "config.json"
        if not config_file.is_file():
            raise FileNotFoundError(f"model config not found: {config_file}")
        entailment_index = cls._read_entailment_index(config_file)
        import onnxruntime as ort
        from tokenizers import Tokenizer

        session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(tokenizer_file))
        return cls(
            session,
            tokenizer,
            model_version=f"onnx:{model.name}",
            entailment_index=entailment_index,
        )

    @staticmethod
    def _read_entailment_index(config_file: Path) -> int:
        with config_file.open("r", encoding="utf-8") as stream:
            config = json.load(stream)
        labels = config.get("id2label")
        if not isinstance(labels, dict):
            raise ValueError(f"model config has no id2label mapping: {config_file}")
        for index, label in labels.items():
            if str(label).strip().lower() == "entailment":
                return int(index)
        raise ValueError(f"model config has no entailment label: {config_file}")

    async def analyze(self, context: object) -> AnalysisResult:
        premise = self._premise(context)
        violation = await asyncio.to_thread(self._entailment_score, premise, VIOLATION_HYPOTHESIS)
        clean = await asyncio.to_thread(self._entailment_score, premise, CLEAN_HYPOTHESIS)
        if violation >= clean:
            return AnalysisResult(
                Verdict.LIKELY_VIOLATION if violation >= 0.5 else Verdict.UNCERTAIN,
                violation,
                "NLI violation hypothesis",
                self.model_version,
                ("nli_violation",),
            )
        return AnalysisResult(
            Verdict.CLEAN if clean >= 0.5 else Verdict.UNCERTAIN,
            clean,
            "NLI clean hypothesis",
            self.model_version,
            ("nli_clean",),
        )

    def _entailment_score(self, premise: str, hypothesis: str) -> float:
        encoding = self.tokenizer.encode(premise, hypothesis)
        values = {
            "input_ids": encoding.ids,
            "attention_mask": encoding.attention_mask,
            "token_type_ids": getattr(encoding, "type_ids", [0] * len(encoding.ids)),
        }
        feed = {}
        for input_meta in self.session.get_inputs():
            if input_meta.name in values:
                feed[input_meta.name] = np.asarray([values[input_meta.name]], dtype=np.int64)
        logits = np.asarray(self.session.run(None, feed)[0], dtype=np.float32).reshape(-1)
        logits = logits - np.max(logits)
        probabilities = np.exp(logits) / np.exp(logits).sum()
        if not 0 <= self.entailment_index < len(probabilities):
            raise ValueError("model entailment index is outside the logits output")
        return float(probabilities[self.entailment_index])

    @staticmethod
    def _premise(context: object) -> str:
        lines = [getattr(context, "raw_content", "")]
        reply = getattr(context, "reply", None)
        if reply is not None:
            lines.append(f"Reply: {getattr(reply, 'content', '')}")
        for item in getattr(context, "previous_messages", ()):
            lines.append(f"Context: {getattr(item, 'author_display_name', '')}: {getattr(item, 'content', '')}")
        return "\n".join(lines)


def load_optional_onnx(
    model_path: str,
    tokenizer_path: str = "",
    *,
    error_kind: list[str] | None = None,
) -> OnnxNliAnalyzer | None:
    if not model_path:
        return None
    try:
        return OnnxNliAnalyzer.from_files(model_path, tokenizer_path)
    except Exception as exc:
        import logging

        if error_kind is not None:
            error_kind.append(type(exc).__name__)
        logging.getLogger("NexusBot.address_detector").exception("address_detector model_load_failed")
        return None
