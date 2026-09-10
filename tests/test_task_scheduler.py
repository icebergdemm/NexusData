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


if __name__ == "__main__":
    unittest.main()
