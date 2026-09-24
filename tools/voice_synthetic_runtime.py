"""Minimal isolated runtime for MelodyMachine's fake-voice classifier."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path
import wave

from tools.voice_synthetic_constants import MODEL_REVISION


SAMPLE_RATE = 16_000
SOURCE_RATE = 48_000


def _fake_class_index(id2label: dict) -> int:
    fake_indices = [
        int(index) for index, label in id2label.items()
        if str(label).casefold() == "fake"
    ]
    if len(fake_indices) != 1:
        raise ValueError(f"expected one fake class, got {id2label}")
    return fake_indices[0]


def _load(model_dir: str, device: str = "cpu"):
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if os.name != "nt":
        try:
            os.nice(10)
        except OSError:
            pass

    with contextlib.redirect_stdout(sys.stderr):
        import numpy
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

        torch.set_num_threads(1)
        processor = AutoFeatureExtractor.from_pretrained(model_dir, local_files_only=True)
        model = AutoModelForAudioClassification.from_pretrained(
            model_dir,
            local_files_only=True,
            use_safetensors=True,
            low_cpu_mem_usage=True,
        )
        model.eval()
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")
        model.to(device)
    id_to_label = {
        int(index): str(label).casefold()
        for index, label in model.config.id2label.items()
    }
    try:
        fake_index = _fake_class_index(id_to_label)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return processor, model, torch, numpy, fake_index


def _samples(audio: str | Path | bytes):
    payload = audio if isinstance(audio, bytes) else Path(audio).read_bytes()
    with wave.open(io.BytesIO(payload), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, SOURCE_RATE):
            raise ValueError("classifier input must be mono 48 kHz PCM16 WAV")
    from transformers.pipelines.audio_classification import ffmpeg_read

    return ffmpeg_read(payload, SAMPLE_RATE)


def _classify(audio: str | Path | bytes, processor, model, torch, numpy, fake_index: int) -> float:
    samples = _samples(audio)
    if samples.size == 0:
        raise ValueError("classifier input is empty")
    inputs = processor(samples, sampling_rate=SAMPLE_RATE, return_tensors="pt")
    with torch.inference_mode():
        logits = model(**inputs).logits
        score = torch.softmax(logits, dim=-1)[0, fake_index].item()
    return float(score)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--benchmark")
    args = parser.parse_args()
    try:
        processor, model, torch, numpy, fake_index = _load(args.model_dir)
    except Exception as exc:
        print(json.dumps({"event": "error", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1

    if args.benchmark:
        started = time.perf_counter()
        score = _classify(args.benchmark, processor, model, torch, numpy, fake_index)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        try:
            import resource

            peak_rss_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        except ImportError:
            peak_rss_mb = None
        print(json.dumps({
            "fake_score": score,
            "inference_ms": elapsed_ms,
            "process_peak_rss_mb": peak_rss_mb,
            "torch_threads": torch.get_num_threads(),
            "model_revision": MODEL_REVISION,
        }))
        return 0

    print(json.dumps({"event": "ready", "fake_class": "fake"}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("op") == "stop":
                return 0
            if request.get("op") != "classify":
                raise ValueError("unknown classifier operation")
            score = _classify(str(request["wav"]), processor, model, torch, numpy, fake_index)
            response = {"fake_score": score}
        except Exception as exc:
            response = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
