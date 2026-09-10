import logging
import queue
import time
import unittest
from unittest.mock import patch

import services_error_log
import services_perf_monitor
from logging_config import _FailSafeFormatter


class NonBlockingDiagnosticsTests(unittest.TestCase):
    def test_database_error_handler_only_enqueues_and_preserves_record(self):
        pending = queue.Queue(maxsize=2)
        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failure: %s",
            args=("boom",),
            exc_info=None,
        )

        with (
            patch.object(services_error_log, "_error_queue", pending),
            patch.object(services_error_log, "_ensure_worker") as start_worker,
        ):
            services_error_log.DatabaseErrorHandler().emit(record)

        payload = pending.get_nowait()
        self.assertEqual(payload.message, "failure: boom")
        self.assertEqual(record.msg, "failure: %s")
        self.assertEqual(record.args, ("boom",))
        start_worker.assert_called_once_with()

    def test_database_error_handler_drops_immediately_when_queue_is_full(self):
        pending = queue.Queue(maxsize=1)
        pending.put_nowait(object())
        record = logging.LogRecord(
            "test.logger", logging.ERROR, __file__, 1, "failure", (), None,
        )

        started = time.monotonic()
        with (
            patch.object(services_error_log, "_error_queue", pending),
            patch.object(services_error_log, "_ensure_worker") as start_worker,
        ):
            services_error_log.DatabaseErrorHandler().emit(record)

        self.assertLess(time.monotonic() - started, 0.1)
        start_worker.assert_not_called()

    def test_perf_anomaly_only_enqueues(self):
        pending = queue.Queue(maxsize=2)
        with (
            patch.object(services_perf_monitor, "_anomaly_queue", pending),
            patch.object(services_perf_monitor, "_ensure_anomaly_worker") as start_worker,
        ):
            services_perf_monitor.record_anomaly(
                "event_loop_lag", 12.5, path="/admin", method="GET",
            )

        payload = pending.get_nowait()
        self.assertEqual(payload.kind, "event_loop_lag")
        self.assertEqual(payload.value, 12.5)
        self.assertEqual(payload.path, "/admin")
        start_worker.assert_called_once_with()

    def test_fail_safe_formatter_handles_malformed_percent_record(self):
        record = logging.LogRecord(
            "discord.gateway",
            logging.WARNING,
            __file__,
            1,
            "heartbeat %s; captured source contains %s",
            (None,),
            None,
        )
        formatter = _FailSafeFormatter("%(levelname)s | %(message)s")

        rendered = formatter.format(record)

        self.assertIn("captured source contains %s", rendered)
        self.assertIn("args=(None,)", rendered)
        self.assertEqual(record.args, (None,))

    def test_active_request_registry_is_bounded_to_live_requests(self):
        request_id = services_perf_monitor.begin_request("GET", "/api/v1/admin/users")
        active = services_perf_monitor.get_active_requests()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["path"], "/api/v1/admin/users")
        self.assertNotIn("?", active[0]["path"])

        services_perf_monitor.end_request(request_id)
        self.assertEqual(services_perf_monitor.get_active_requests(), [])


if __name__ == "__main__":
    unittest.main()
