import base64
import json
import subprocess
import wave
from pathlib import Path

from services_voice_profiles import VoicePacket, write_vpk1


ROOT = Path(__file__).resolve().parents[1]
DECODER_PROJECT = ROOT / "tools" / "VoiceProfileDecoder" / "VoiceProfileDecoder.csproj"


def test_concentus_helper_decodes_a_real_opus_packet(tmp_path):
    payload = base64.b64decode("aIVOha4df6KAarMF1UB0VIXQu2V1/dVBnd0x4YjEUgbm6/pGUz5E3w==")
    input_path = tmp_path / "speaker.vpk1"
    output_path = tmp_path / "speaker.wav"
    index_path = tmp_path / "speaker.json"
    write_vpk1(input_path, [VoicePacket(11790, 4079, payload, False)])

    result = subprocess.run(
        [
            "dotnet",
            "run",
            "--project",
            str(DECODER_PROJECT),
            "--",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--index",
            str(index_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    with wave.open(str(output_path), "rb") as audio:
        assert audio.getframerate() == 48_000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getnframes() > 0
        assert any(audio.readframes(audio.getnframes()))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index[0]["timestamp_ms"] == 11790
    assert index[0]["sample_count"] > 0
