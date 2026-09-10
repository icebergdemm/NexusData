import logging
import time
import unittest
from unittest.mock import patch

import task_scheduler


class TaskSchedulerStatsTests(unittest.TestCase):
    @staticmethod
    def _wait_for_status(scheduler, task_id):
        deadline = time.monotonic() + 2
        while scheduler.status(task_id) in {"pending", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
        return scheduler.status(task_id)

    def test_successful_task_has_stats_without_routine_completion_log(self):
        scheduler = task_scheduler.TaskScheduler(worker_count=1, result_ttl=60)
        try:
            with patch.object(task_scheduler.logger, "info") as info:
                scheduler.start()
                task_id = scheduler.submit(lambda: {"status": "ok"}, task_id="quiet-task")

                deadline = time.monotonic() + 2
                while scheduler.status(task_id) == "pending" and time.monotonic() < deadline:
                    time.sleep(0.01)

                self.assertEqual(scheduler.status(task_id), "done")
                self.assertFalse(any("→ done" in str(call) for call in info.call_args_list))

            stats = scheduler.task_stats()
            self.assertEqual(len(stats), 1)
            self.assertEqual(stats[0]["task_id"], "quiet-task")
            self.assertEqual(stats[0]["runs"], 1)
            self.assertEqual(stats[0]["errors"], 0)
            self.assertEqual(stats[0]["timeouts"], 0)
            self.assertEqual(stats[0]["last_status"], "done")
            self.assertIsNotNone(stats[0]["queue_wait_ms"])
        finally:
            scheduler.stop()

    def test_slow_success_is_retained_as_a_warning(self):
        scheduler = task_scheduler.TaskScheduler(worker_count=1, result_ttl=60)
        try:
            with patch.object(task_scheduler, "SLOW_TASK_WARNING_SECONDS", 0.0), \
                 patch.object(task_scheduler.logger, "warning") as warning:
                scheduler.start()
                task_id = scheduler.submit(lambda: None, task_id="slow-task")
                deadline = time.monotonic() + 2
                while scheduler.status(task_id) == "pending" and time.monotonic() < deadline:
                    time.sleep(0.01)

                self.assertEqual(scheduler.status(task_id), "done")
                self.assertTrue(any("slow-task" in str(call) for call in warning.call_args_list))
        finally:
            scheduler.stop()

    def test_errors_and_timeouts_stay_visible_and_are_counted(self):
        scheduler = task_scheduler.TaskScheduler(worker_count=1, result_ttl=60)
        try:
            with patch.object(task_scheduler.logger, "warning") as warning, \
                 patch.object(task_scheduler.logger, "error") as error:
                scheduler.start()
                failed_id = scheduler.submit(lambda: 1 / 0, task_id="failed-task")
                timeout_id = scheduler.submit(lambda: time.sleep(0.2), task_id="timeout-task", timeout=0.01)

                self.assertEqual(self._wait_for_status(scheduler, failed_id), "error")
                self.assertEqual(self._wait_for_status(scheduler, timeout_id), "timeout")

                self.assertTrue(error.called)
                self.assertTrue(any("timed out" in str(call) for call in warning.call_args_list))

            stats = {item["task_id"]: item for item in scheduler.task_stats()}
            self.assertEqual(stats["failed-task"]["errors"], 1)
            self.assertEqual(stats["timeout-task"]["timeouts"], 1)
        finally:
            scheduler.stop()

    def test_external_run_is_added_to_task_stats(self):
        scheduler = task_scheduler.TaskScheduler(worker_count=1, result_ttl=60)

        scheduler.record_external_run(
            "ai_dossier_worker",
            status="succeeded",
            duration_ms=8123,
            queue_wait_ms=30,
        )

        stats = scheduler.task_stats()
        assert stats[0]["task_id"] == "ai_dossier_worker"
        assert stats[0]["runs"] == 1
        assert stats[0]["errors"] == 0
        assert stats[0]["timeouts"] == 0
        assert stats[0]["last_duration_ms"] == 8123
        assert stats[0]["avg_duration_ms"] == 8123.0
        assert stats[0]["max_duration_ms"] == 8123
        assert stats[0]["last_status"] == "succeeded"
        assert stats[0]["queue_wait_ms"] == 30

    def test_external_errors_and_timeouts_are_counted(self):
        scheduler = task_scheduler.TaskScheduler(worker_count=1, result_ttl=60)

        scheduler.record_external_run("ai_dossier_worker", status="failed", duration_ms=10)
        scheduler.record_external_run("ai_dossier_worker", status="timeout", duration_ms=20)

        stats = scheduler.task_stats()[0]
        assert stats["runs"] == 2
        assert stats["errors"] == 1
        assert stats["timeouts"] == 1
        assert stats["last_status"] == "timeout"

    def test_stats_include_rolling_duration_and_queue_wait_percentiles(self):
        scheduler = task_scheduler.TaskScheduler(worker_count=1, result_ttl=60)

        for duration_ms, queue_wait_ms in ((10, 1), (20, 2), (30, 3), (40, 4)):
            scheduler.record_external_run(
                "metrics-task",
                status="done",
                duration_ms=duration_ms,
                queue_wait_ms=queue_wait_ms,
            )

        stats = scheduler.task_stats()[0]
        assert stats["duration_p95_ms"] == 40
        assert stats["duration_p99_ms"] == 40
        assert stats["queue_wait_p95_ms"] == 4
        assert stats["queue_wait_p99_ms"] == 4
        assert stats["sample_count"] == 4

    def test_active_snapshot_reports_running_tasks_and_overlap(self):
        scheduler = task_scheduler.TaskScheduler(worker_count=2, result_ttl=60)
        gate = task_scheduler.threading.Event()
        try:
            scheduler.start()
            first = scheduler.submit(lambda: gate.wait(2), task_id="active-one", timeout=None)
            second = scheduler.submit(lambda: gate.wait(2), task_id="active-two", timeout=None)

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                active = scheduler.active_tasks()
                if len(active) == 2 and all(item["status"] == "running" for item in active):
                    break
                time.sleep(0.01)

            active = scheduler.active_tasks()
            assert {item["task_id"] for item in active} == {first, second}
            assert all(item["status"] == "running" for item in active)
            overlap = scheduler.overlap_snapshot()
            assert overlap["active_count"] == 2
            assert overlap["overlap"] is True
        finally:
            gate.set()
            scheduler.stop()

    def test_periodic_task_coalesces_missed_intervals(self):
        scheduler = task_scheduler.TaskScheduler(worker_count=1, result_ttl=60)
        scheduler.register_periodic(lambda: None, interval_secs=10, task_id="periodic-task")
        periodic = scheduler._periodic[0]
        periodic["last_run"] = time.monotonic() - 35

        with patch.object(scheduler, "submit") as submit:
            scheduler._periodic_loop_once(time.monotonic())

        assert submit.call_count == 1
        assert periodic["coalesced_runs"] == 2

if __name__ == "__main__":
    unittest.main()
