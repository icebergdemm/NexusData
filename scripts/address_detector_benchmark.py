from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from pathlib import Path
from types import SimpleNamespace

from discord_bot.address_detector.onnx_analyzer import OnnxNliAnalyzer


SAMPLES = (
    "ты зачем опять это делаешь?",
    "Он буквально написал: «ты идиот», после чего ушёл.",
    "В русском языке местоимение «ты» используется в единственном числе.",
    "а ты?",
)


def _context(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        raw_content=text,
        cleaned_content=text,
        trigger_tokens=("ты",),
        reply=None,
        previous_messages=(),
    )


async def _run(model_path: Path, tokenizer_path: Path, runs: int) -> None:
    started = time.perf_counter()
    analyzer = OnnxNliAnalyzer.from_files(str(model_path), str(tokenizer_path))
    load_ms = (time.perf_counter() - started) * 1000
    print(f"model={analyzer.model_version} entailment_index={analyzer.entailment_index} load_ms={load_ms:.1f}")

    latencies: list[float] = []
    for _ in range(max(1, runs)):
        for sample in SAMPLES:
            started = time.perf_counter()
            result = await analyzer.analyze(_context(sample))
            latencies.append((time.perf_counter() - started) * 1000)
            print(f"verdict={result.verdict.value} confidence={result.confidence} text={sample}")
    ordered = sorted(latencies)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    print(
        f"warm_runs={len(latencies)} mean_ms={statistics.mean(latencies):.1f} "
        f"p50_ms={statistics.median(latencies):.1f} p95_ms={ordered[p95_index]:.1f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CPU benchmark for the optional address-detector ONNX model")
    parser.add_argument("--model", default="/home/container/models/address-detector/model.onnx")
    parser.add_argument("--tokenizer", default="/home/container/models/address-detector/tokenizer.json")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(_run(Path(args.model), Path(args.tokenizer), args.runs))


if __name__ == "__main__":
    main()
