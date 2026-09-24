"""Small, authenticated HTTP API for the pinned voice classifier."""

from __future__ import annotations

import argparse
import base64
import hmac
import http.server
import json
import math
import ssl
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit

from tools.voice_synthetic_constants import (
    FAKE_SCORE_THRESHOLD,
    MAX_WINDOWS_PER_CALL,
    MODEL_REVISION,
)
from tools.voice_synthetic_runtime import _classify, _load

MAX_WAV_BYTES = 600_000
MAX_JSON_BYTES = 3_500_000
HEALTH_PATH = "/v1/voice-synthetic/health"
CLASSIFY_PATH = "/v1/voice-synthetic/classify"


def classify_window_batch(
    payload: object,
    classify: Callable[[bytes], float],
    *,
    revision: str = MODEL_REVISION,
    threshold: float = FAKE_SCORE_THRESHOLD,
) -> dict:
    if not isinstance(payload, dict) or payload.get("revision") != revision:
        raise ValueError("model revision mismatch")
    windows = payload.get("windows")
    if not isinstance(windows, list) or not 1 <= len(windows) <= MAX_WINDOWS_PER_CALL:
        raise ValueError(f"expected 1 to {MAX_WINDOWS_PER_CALL} WAV windows")

    scores = []
    for encoded in windows:
        try:
            if not isinstance(encoded, str):
                raise ValueError
            wav_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("invalid WAV base64") from exc
        if not 44 <= len(wav_bytes) <= MAX_WAV_BYTES or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
            raise ValueError("invalid WAV window")
        score = float(classify(wav_bytes))
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("classifier score is outside [0, 1]")
        scores.append(score)
        if score >= threshold:
            break
    return {"revision": revision, "fake_scores": scores}


def make_handler(api_keys: str | Sequence[str], revision: str, device: str, classify: Callable[[bytes], float]):
    keys = [api_keys] if isinstance(api_keys, str) else list(api_keys)
    keys = [key.strip() for key in keys if key.strip()]

    class VoiceSyntheticApiHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "VoiceClassifier"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(20.0)

        def log_message(self, _format: str, *_args) -> None:
            # Request bodies contain private player audio; keep them out of access logs.
            return

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            provided = self.headers.get("Authorization", "").encode("utf-8")
            if any(hmac.compare_digest(provided, f"Bearer {key}".encode("utf-8")) for key in keys):
                return True
            self._send(401, {"error": "unauthorized"})
            return False

        def do_GET(self) -> None:
            if urlsplit(self.path).path != HEALTH_PATH:
                self._send(404, {"error": "not found"})
                return
            if not self._authorized():
                return
            self._send(200, {"status": "ready", "revision": revision, "device": device})

        def do_POST(self) -> None:
            if urlsplit(self.path).path != CLASSIFY_PATH:
                self._send(404, {"error": "not found"})
                return
            if not self._authorized():
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, {"error": "application/json required"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid content length"})
                return
            if not 1 <= content_length <= MAX_JSON_BYTES:
                self._send(413, {"error": "request body too large"})
                return
            try:
                body = json.loads(self.rfile.read(content_length))
                result = classify_window_batch(body, classify, revision=revision)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
                return
            except Exception:
                self._send(500, {"error": "inference failed"})
                return
            self._send(200, result)

    return VoiceSyntheticApiHandler


def serve(args) -> None:
    api_keys = Path(args.api_key_file).read_text(encoding="utf-8").splitlines()
    if not any(key.strip() for key in api_keys):
        raise RuntimeError("API key file is empty")
    if args.device == "cuda" and not args.tls_cert:
        raise RuntimeError("TLS is required before serving authenticated audio requests")

    processor, model, torch, numpy, fake_index = _load(args.model_dir, args.device)

    def classify(wav_bytes: bytes) -> float:
        return _classify(wav_bytes, processor, model, torch, numpy, fake_index)

    handler = make_handler(api_keys, MODEL_REVISION, args.device, classify)
    server = http.server.HTTPServer((args.host, args.port), handler)
    server.request_queue_size = 2
    if not args.tls_cert or not args.tls_key:
        raise RuntimeError("TLS certificate and private key are required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.tls_cert, args.tls_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever(poll_interval=1.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--tls-cert", required=True)
    parser.add_argument("--tls-key", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    serve(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
