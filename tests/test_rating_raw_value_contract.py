from datetime import datetime, timezone
import sys
import types

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl

from pydantic import ValidationError

from routes.rating_events import RatingEventIn


def _event(**overrides):
    value = {
        "event_id": "event-raw-1",
        "version": 1,
        "steam_id": "76561198000000001",
        "mode": "mission",
        "role": "medic",
        "event_type": "medical.revive",
        "raw_value": 1,
        "server_session_id": "server-session-1",
        "round_token": "round-1",
        "occurred_at": datetime.now(timezone.utc),
        "source": {"server": "ASPER1"},
        "round": {"round_token": "round-1"},
    }
    value.update(overrides)
    return value


def test_rating_event_accepts_raw_value_without_precomputed_points():
    event = RatingEventIn.model_validate(_event())
    assert event.raw_value == 1
    assert event.points_delta is None


def test_rating_event_accepts_zero_raw_value_for_non_scoring_lifecycle_events():
    event = RatingEventIn.model_validate(_event(event_type="round.finished", raw_value=0))
    assert event.raw_value == 0


def test_rating_event_rejects_non_finite_raw_value():
    try:
        RatingEventIn.model_validate(_event(raw_value=float("inf")))
    except ValidationError as exc:
        assert "finite" in str(exc)
    else:
        raise AssertionError("non-finite rating value was accepted")


def test_rating_event_accepts_flat_server_contract_context():
    payload = _event(
        source=None,
        round=None,
        server_name="ASPER1",
        server_session_id="server-session-1",
        round_token="round-2",
        plugin_version="rating-plugin/1",
        metadata={"mission": "cargo"},
    )
    event = RatingEventIn.model_validate(payload)
    assert event.source["server_name"] == "ASPER1"
    assert event.round["round_token"] == "round-2"
