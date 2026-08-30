import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from models import RoundEvent, RoundReplay
from routes.replays import _round_for_replay


class _Query:
    def __init__(self, *, first=None, rows=None):
        self._first = first
        self._rows = list(rows or [])

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._rows


class _DB:
    def __init__(self, *, manual=None, rounds=None):
        self.manual = manual
        self.rounds = list(rounds or [])

    def query(self, model):
        if model is RoundEvent:
            return _Query(first=self.manual, rows=self.rounds)
        if model is RoundReplay:
            return _Query()
        raise AssertionError(f"unexpected model: {model}")


class ReplayRoundMatchingTests(unittest.TestCase):
    def test_recording_clock_links_a_delayed_upload_to_closest_round_start(self):
        recorded = datetime(2026, 8, 30, 19, 18, 25)
        farther_start = SimpleNamespace(created_at=recorded - timedelta(minutes=7), submarine_name="Другой борт")
        closest_start = SimpleNamespace(created_at=recorded + timedelta(seconds=4), submarine_name="Клоп [ASPER]")
        farther = SimpleNamespace(id=10, created_at=recorded + timedelta(minutes=20), paired_start=farther_start)
        closest = SimpleNamespace(id=11, created_at=recorded + timedelta(minutes=26), paired_start=closest_start)
        replay = SimpleNamespace(
            id=1126,
            manual_round_event_id=None,
            server_name="ASPER1",
            submarine_name="Клоп [ASPER]",
            recorded_at=recorded,
            # Deliberately far outside the old end+60 minute upload window.
            uploaded_at=recorded + timedelta(hours=5),
        )

        matched = _round_for_replay(_DB(rounds=[farther, closest]), replay)

        self.assertIs(matched, closest)

    def test_manual_round_link_still_has_priority(self):
        manual = SimpleNamespace(id=77)
        replay = SimpleNamespace(
            manual_round_event_id=77,
            server_name="ASPER1",
            submarine_name="Клоп [ASPER]",
            recorded_at=datetime(2026, 8, 30, 19, 18, 25),
            uploaded_at=datetime(2026, 8, 31, 0, 18, 25),
        )

        self.assertIs(_round_for_replay(_DB(manual=manual), replay), manual)

    def test_legacy_local_clock_header_uses_server_timezone_offset(self):
        real_utc_start = datetime(2026, 8, 30, 19, 18, 25)
        start = SimpleNamespace(
            created_at=real_utc_start,
            submarine_name="Клоп [ASPER]",
        )
        end = SimpleNamespace(
            id=12,
            created_at=real_utc_start + timedelta(minutes=26),
            paired_start=start,
        )
        replay = SimpleNamespace(
            id=1126,
            manual_round_event_id=None,
            server_name="ASPER1",
            submarine_name="Клоп [ASPER]",
            # Legacy header stored Moscow wall time as though it were UTC.
            recorded_at=real_utc_start + timedelta(hours=3),
            uploaded_at=real_utc_start + timedelta(hours=5),
        )

        self.assertIs(_round_for_replay(_DB(rounds=[end]), replay), end)


if __name__ == "__main__":
    unittest.main()
