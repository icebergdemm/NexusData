# Voice synthetic classifier service

This service is separate from the NexusData web process. Keep model files in
`/opt/models/voice-synthetic-model`; keep the service checkout in
`/opt/voice-synthetic-service`. Neither path is part of the NexusData auto-deploy.

## Install

1. Copy `tools/voice_synthetic_api.py`, `tools/voice_synthetic_runtime.py`, and
   `tools/voice_synthetic_constants.py` into `/opt/voice-synthetic-service/tools/`.
2. The host has Python 3.13, while the pinned PyTorch CPU wheel needs Python 3.12.
   Install Python 3.12 into `/opt` with `uv` (without replacing the system Python),
   create `/opt/voice-synthetic-service/.venv` from it, then install
   `requirements-cpu.txt`. Install the system `ffmpeg` package; the pinned
   Transformers audio reader uses it to resample 48 kHz WAV input.
3. Run `install-model.sh`; it downloads the pinned Hugging Face revision and
   verifies every SHA-256 before replacing the installed file.
4. Install `voice-synthetic.service` as
   `/etc/systemd/system/voice-synthetic.service`, then reload systemd and start it.
5. The persistent ingress rules are managed by `qwen-firewall.service`, which
   runs `/usr/local/sbin/qwen-firewall`. Add an idempotent allow rule for
   `144.31.195.171` on TCP 8443 before a final drop rule for all other sources
   on that port. Keep the existing port 8000 rules and do not flush the nftables
   ruleset. The service presents `player-dossier.crt`, whose SAN covers the AI
   host IP, and reads the existing Qwen API key credential without printing it.
6. NexusData defaults to `https://213.108.170.48:8443`; set
   `VOICE_SYNTHETIC_API_URL` only to override it. The client reuses the
   configured AI-dossier key and CA unless voice-classifier overrides are set.

The service starts CPU-only, with one inference request at a time, a 1 CPU
quota, and a 2 GiB memory ceiling. To consider CUDA, install
`requirements-cuda.txt`, change `VOICE_SYNTHETIC_DEVICE` to `cuda`, and measure
while Qwen is loaded. Keep CUDA only if peak use leaves at least 1 GiB free VRAM,
Qwen health remains stable, and request latency stays within the measured
baseline. Otherwise return to CPU.

The protected health endpoint reports the pinned model revision. The current
22 WAV sample set produced 10/11 fake detections and 11/11 original
classifications; `voice-profile-148521.wav` was a false negative (`0.000018`).
The API accepts at most four 48 kHz mono PCM16 windows per request and stops
evaluating after a score reaches `0.9995`. Request bodies are never logged.
