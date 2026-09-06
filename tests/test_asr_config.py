"""The game plugin reads one authenticated config, never a second .env."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from config import Settings
from routes import communications


def test_streaming_config_uses_nexus_settings(monkeypatch):
    settings = Settings(
        _env_file=None, deepgram_api_key="test-only",
        deepgram_model="nova-3", deepgram_language="ru",
        deepgram_segment_gap_ms=2400, deepgram_audio_queue_frames=768,
    )
    monkeypatch.setattr(communications, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(communications.api_router)
    app.dependency_overrides[communications.verify_external_api_key_dependency] = lambda: None
    with TestClient(app) as client:
        response = client.get("/api/v1/server/communications/config")
    assert response.status_code == 200
    data = response.json()
    assert data["deepgram_model"] == "nova-3"
    assert data["deepgram_language"] == "ru"
    assert data["deepgram_streaming"] == {
        "segment_gap_ms": 2400, "max_segment_seconds": 60,
        "finalize_timeout_seconds": 8, "io_timeout_seconds": 10,
        "audio_queue_frames": 768, "endpointing_ms": 600,
    }


def test_streaming_config_retains_auth_dependency():
    route = next(r for r in communications.api_router.routes if r.path.endswith("/communications/config"))
    assert communications.verify_external_api_key_dependency in [
        dependency.call for dependency in route.dependant.dependencies
    ]


@pytest.mark.parametrize(("field", "bad"), [
    ("deepgram_segment_gap_ms", 0),
    ("deepgram_max_segment_seconds", 1000),
    ("deepgram_finalize_timeout_seconds", -1),
    ("deepgram_io_timeout_seconds", 999),
    ("deepgram_audio_queue_frames", 1000000),
    ("deepgram_endpointing_ms", 0),
])
def test_streaming_settings_reject_unsafe_values(field, bad):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: bad})


def test_streaming_settings_read_same_environment(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_SEGMENT_GAP_MS", "2200")
    monkeypatch.setenv("DEEPGRAM_FINALIZE_TIMEOUT_SECONDS", "12")
    settings = Settings(_env_file=None)
    assert settings.deepgram_segment_gap_ms == 2200
    assert settings.deepgram_finalize_timeout_seconds == 12
