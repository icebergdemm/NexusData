"""External ONNX voiceprint extraction for one validated replay."""
import hashlib
import importlib.util
import os
import shutil
import struct
import tempfile
import wave
from pathlib import Path

from services_voice_profiles import (
    MFCC_V1_MIN_QUALITY_MS, ONNX_VOICEPRINT_DIMENSIONS,
    analyze_pcm16_speech, collect_nwdemo_voice_stats, decode_vpk1,
    onnx_voiceprint_embedding, resample_pcm16_for_voiceprint,
    select_nwdemo_voiceprint_packets, select_voiceprint_pcm16,
    voiceprint_onnx_session, ONNX_VOICEPRINT_MODEL_SHA256, write_vpk1,
    VoicePacket,
)


def available():
    try:
        decoder = os.environ.get("VOICE_PROFILE_DECODER_PATH", "")
        if not Path(decoder).is_file() and not shutil.which(decoder):
            return False
        if str(decoder).lower().endswith((".dll", ".csproj")) and not shutil.which("dotnet"):
            return False
        return bool(importlib.util.find_spec("onnxruntime"))
    except Exception:
        return False


def _read_wav(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 48_000):
            raise ValueError("voiceprint decoder returned an unexpected WAV format")
        count = reader.getnframes()
        payload = reader.readframes(count)
    return list(struct.unpack(f"<{count}h", payload)) if count else []


def process(job: dict, input_path: Path, output_dir: Path, cancelled, progress):
    replay_id = int(job["payload"]["replay_id"])
    replay_hash = hashlib.sha256()
    with input_path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            if cancelled.is_set():
                raise RuntimeError("Job cancelled")
            replay_hash.update(block)
    digest = replay_hash.hexdigest()
    allow_empty = bool(job["payload"].get("allow_empty_world"))
    stats = collect_nwdemo_voice_stats(input_path, allow_empty_world=allow_empty)
    samples = select_nwdemo_voiceprint_packets(input_path, stats, allow_empty_world=allow_empty)
    decoder = os.environ.get("VOICE_PROFILE_DECODER_PATH")
    if not decoder:
        raise RuntimeError("VOICE_PROFILE_DECODER_PATH is not configured on this worker")
    model_path = Path(os.environ.get("NEXUSDATA_JOB_MODEL_PATH", ""))
    if not model_path.is_file():
        raise RuntimeError("NexusData did not provide the voiceprint model for this job")
    model_digest = hashlib.sha256()
    with model_path.open("rb") as model_file:
        for block in iter(lambda: model_file.read(1024 * 1024), b""):
            model_digest.update(block)
    model_hash = model_digest.hexdigest()
    if model_hash != ONNX_VOICEPRINT_MODEL_SHA256:
        raise RuntimeError("Voiceprint model hash differs from the pinned model")
    session = voiceprint_onnx_session(str(model_path))
    fingerprints = []
    steam_ids = sorted({key[0] for key in samples})
    for index, steam_id in enumerate(steam_ids, start=1):
        if cancelled.is_set():
            raise RuntimeError("Job cancelled")
        normal = samples.get((steam_id, False), [])
        radio = samples.get((steam_id, True), [])
        if not normal and not radio:
            continue
        chosen = normal or radio
        channel = "normal" if normal else "radio"
        with tempfile.TemporaryDirectory(prefix="nd-voiceprint-") as temporary:
            temp = Path(temporary)
            packets = [VoicePacket(packet.time_ms, index, packet.data, packet.radio) for packet in chosen]
            source = temp / "voice.vpk1"
            decoded = temp / "voice.wav"
            index_path = temp / "voice.index.json"
            write_vpk1(source, packets)
            decode_vpk1(decoder, source, decoded, index_path)
            speech = analyze_pcm16_speech(_read_wav(decoded))
            if not speech.accepted and normal and radio:
                chosen = radio
                channel = "radio"
                packets = [VoicePacket(packet.time_ms, index, packet.data, packet.radio) for packet in chosen]
                write_vpk1(source, packets)
                decode_vpk1(decoder, source, decoded, index_path)
                speech = analyze_pcm16_speech(_read_wav(decoded))
            if speech.accepted:
                capped = select_voiceprint_pcm16(speech.voiced_samples, max_samples=48_000 * 30)
                vector = onnx_voiceprint_embedding(resample_pcm16_for_voiceprint(capped), session)
                if len(vector) != ONNX_VOICEPRINT_DIMENSIONS:
                    raise ValueError("Voiceprint model returned an unexpected vector size")
                fingerprints.append({
                    "steam_id": steam_id, "source_channel": channel,
                    "vector": [float(value) for value in vector],
                    "quality": "good" if speech.speech_ms >= MFCC_V1_MIN_QUALITY_MS else "low",
                    "duration_ms": min(speech.speech_ms, 30_000), "packet_count": len(chosen),
                })
        if index % 10 == 0:
            progress({"speakers_completed": index, "speakers_seen": len(steam_ids)})
    return {"schema": "nexusdata.voiceprint.v1", "replay_id": replay_id,
            "replay_sha256": digest, "processed_steam_ids": steam_ids,
            "fingerprints": fingerprints}, None


HANDLERS = {"voice_profile.voiceprint": process}
