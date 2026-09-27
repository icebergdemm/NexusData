"""CPU-bound extraction stage for NewWorldStreamer voice-profile archives."""
import hashlib
import json
import os
import shutil
import struct
import tempfile
import wave
import zipfile
from pathlib import Path

from services_voice_profiles import (
    SPEECH_GATE_VERSION, VoicePacket, analyze_pcm16_speech, decode_vpk1,
    gzip_file, make_nwdemo_archive_key, read_ndvc, stream_nwdemo_segments,
    write_pcm16_wav, write_vpk1,
)


def available():
    decoder = os.environ.get("VOICE_PROFILE_DECODER_PATH", "")
    return bool((Path(decoder).is_file() or shutil.which(decoder)) and
                (not decoder.lower().endswith((".dll", ".csproj")) or shutil.which("dotnet")))


def _read_wav(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 48_000):
            raise ValueError("Voice decoder returned an unexpected WAV format")
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
    segments_dir = output_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    speech_dir = output_dir / "speech"
    speech_dir.mkdir(parents=True, exist_ok=True)

    def on_packet(count, _packet):
        if cancelled.is_set():
            raise RuntimeError("Job cancelled")
        if count % 5000 == 0:
            progress({"packets_read": count})

    segments = stream_nwdemo_segments(
        input_path, segments_dir,
        allow_empty_world=bool(job["payload"].get("allow_empty_world")),
        progress_callback=on_packet,
    )
    decoder = os.environ.get("VOICE_PROFILE_DECODER_PATH")
    rows = []
    for segment_index, segment in enumerate(segments, start=1):
        if cancelled.is_set():
            raise RuntimeError("Job cancelled")
        key = make_nwdemo_archive_key(
            digest, segment["steam_id"], segment["radio"], segment["start_ms"], segment["end_ms"],
        )
        filename = f"segments/{key}.ndvc"
        ndvc_path = output_dir / filename
        os.replace(segment["packet_file"], ndvc_path)
        with tempfile.TemporaryDirectory(prefix="nd-voice-gate-") as temporary:
            temp = Path(temporary)
            packets = read_ndvc(ndvc_path)
            vpk_path = temp / f"{key}.vpk1"
            wav_path = temp / f"{key}.wav"
            index_path = temp / f"{key}.index.json"
            write_vpk1(vpk_path, [VoicePacket(packet.time_ms, 1, packet.data, packet.radio) for packet in packets])
            decode_vpk1(decoder, vpk_path, wav_path, index_path)
            speech = analyze_pcm16_speech(_read_wav(wav_path))
            wav_member = None
            wav_size = 0
            if speech.accepted:
                wav_member = f"speech/{key}.wav.gz"
                voiced = temp / f"{key}.voice.wav"
                write_pcm16_wav(voiced, speech.voiced_samples)
                wav_size = gzip_file(voiced, output_dir / wav_member)
        rows.append({
            "archive_key": key,
            "steam_id": segment["steam_id"],
            "is_radio": bool(segment["radio"]),
            "start_ms": int(segment["start_ms"]),
            "end_ms": int(segment["end_ms"]),
            "packet_count": int(segment["packet_count"]),
            "radio_packet_count": int(segment["radio_packet_count"]),
            "ndvc_filename": filename,
            "storage_size": (output_dir / filename).stat().st_size,
            "speech_status": "accepted" if speech.accepted else "rejected",
            "speech_ms": speech.speech_ms,
            "speech_ratio": speech.speech_ratio,
            "speech_gate_version": SPEECH_GATE_VERSION,
            "wav_member": wav_member,
            "wav_size": wav_size,
        })
        if segment_index % 10 == 0:
            progress({"segments_completed": segment_index, "segments_seen": len(segments)})
    manifest = {"schema": "nexusdata.voice-profile-extraction.v1", "replay_id": replay_id,
                "replay_sha256": digest, "segments": rows}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    archive_path = output_dir / "result.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(output_dir / "manifest.json", "manifest.json")
        for row in rows:
            archive.write(output_dir / row["ndvc_filename"], row["ndvc_filename"])
            if row["wav_member"]:
                archive.write(output_dir / row["wav_member"], row["wav_member"])
    return {"schema": manifest["schema"], "replay_id": replay_id, "replay_sha256": digest,
            "segment_count": len(rows), "steam_ids": sorted({row["steam_id"] for row in rows})}, archive_path


HANDLERS = {"voice_profile.extract": process}
