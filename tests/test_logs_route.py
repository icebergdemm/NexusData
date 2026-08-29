import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from routes.logs import api_logs_events
from services_logs import LogEvent, LogSearchResult


class _FailingPlayerLookupDB:
    def __init__(self):
        self.rolled_back = False

    def query(self, *_args, **_kwargs):
        raise RuntimeError("main database temporarily unavailable")

    def rollback(self):
        self.rolled_back = True


async def _run_inline(function, *args, **kwargs):
    return function(*args, **kwargs)


class LogsRouteTests(unittest.TestCase):
    def _call_route(self, result, db=None):
        async def run():
            with (
                patch("routes.logs.check_permission", return_value=True),
                patch("routes.logs.search_logs", return_value=result),
                patch("routes.logs.run_in_threadpool", side_effect=_run_inline),
            ):
                return await api_logs_events(
                    request=None,
                    server=None,
                    player=None,
                    player_names=None,
                    player_scope="actor",
                    action_type=None,
                    category=None,
                    search=None,
                    time_from=None,
                    time_to=None,
                    pagination="cursor",
                    cursor=None,
                    page=1,
                    page_size=100,
                    db=db or object(),
                    current_admin=object(),
                )

        return asyncio.run(run())

    @staticmethod
    def _result():
        return LogSearchResult(
            events=[
                LogEvent(
                    timestamp=datetime(2026, 8, 29, 12, 0),
                    player_name="Игрок",
                    action_type="wire",
                    details="Игрок connected a wire",
                    raw_line="Игрок connected a wire",
                    server_name="ASPER1",
                    source_file="server.log",
                    row_id=42,
                )
            ],
            total=None,
            total_is_capped=False,
            has_more=True,
            next_cursor="MToxNzg4MDA0ODAwMDAwOjQy",
        )

    def test_cursor_response_serializes_pagination_metadata(self):
        with patch("routes.logs.get_steamids_for_names", return_value={}):
            response = self._call_route(self._result())

        body = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["events"][0]["event_id"], 42)
        self.assertEqual(body["next_cursor"], "MToxNzg4MDA0ODAwMDAwOjQy")
        self.assertEqual(body["pagination"], "cursor")
        self.assertIsNone(body["total"])
        self.assertIsNone(body["total_pages"])

    def test_profile_link_failure_does_not_hide_found_logs(self):
        db = _FailingPlayerLookupDB()
        with patch(
            "routes.logs.get_steamids_for_names",
            return_value={"Игрок": "76561198000000000"},
        ):
            response = self._call_route(self._result(), db=db)

        body = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(body["events"]), 1)
        self.assertIsNone(body["events"][0]["player_id"])
        self.assertTrue(db.rolled_back)


if __name__ == "__main__":
    unittest.main()
