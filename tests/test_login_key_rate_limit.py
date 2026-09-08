from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware import RateLimitMiddleware


def test_valid_login_key_uses_four_times_global_bucket(monkeypatch):
    app = FastAPI()

    @app.get("/api/probe")
    async def probe():
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware, requests_per_window=1, window_seconds=60)
    monkeypatch.setattr(
        "middleware.resolve_api_login_key",
        lambda _raw: (42, 7),
    )

    with TestClient(app) as client:
        first = client.get("/api/probe", headers={"X-ND-Login-Key": "nd-test"})
        second = client.get("/api/probe", headers={"X-ND-Login-Key": "nd-test"})
        third = client.get("/api/probe", headers={"X-ND-Login-Key": "nd-test"})
        fourth = client.get("/api/probe", headers={"X-ND-Login-Key": "nd-test"})
        fifth = client.get("/api/probe", headers={"X-ND-Login-Key": "nd-test"})

    assert [response.status_code for response in (first, second, third, fourth)] == [200] * 4
    assert fifth.status_code == 429


def test_invalid_login_key_stays_ip_limited_even_with_service_key(monkeypatch):
    app = FastAPI()

    @app.get("/api/probe")
    async def probe():
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware, requests_per_window=1, window_seconds=60)
    monkeypatch.setattr("middleware.resolve_api_login_key", lambda _raw: None)
    monkeypatch.setattr("middleware.settings.api_key", "service-secret")

    with TestClient(app) as client:
        headers = {"X-ND-Login-Key": "nd-invalid", "X-API-Key": "service-secret"}
        first = client.get("/api/probe", headers=headers)
        second = client.get("/api/probe", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
