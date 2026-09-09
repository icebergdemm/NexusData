import threading
import time

from task_scheduler import TaskScheduler, raise_if_task_cancelled


def test_running_task_can_be_cancelled_without_killing_scheduler():
    scheduler = TaskScheduler(worker_count=1, result_ttl=60)
    started = threading.Event()

    def work():
        started.set()
        while True:
            raise_if_task_cancelled()
            time.sleep(0.001)

    scheduler.start()
    try:
        task_id = scheduler.submit(work, task_id="replay_voice_index_1052_v5", timeout=None)
        assert started.wait(1)
        assert scheduler.cancel(task_id)
        deadline = time.monotonic() + 1
        while scheduler.status(task_id) == "running" and time.monotonic() < deadline:
            time.sleep(0.005)
        assert scheduler.status(task_id) == "cancelled"
        assert scheduler._workers[0].is_alive()
    finally:
        scheduler.stop()


def test_cancelled_queued_task_is_not_started():
    scheduler = TaskScheduler(worker_count=1, result_ttl=60)
    blocker = threading.Event()
    first_started = threading.Event()
    started = threading.Event()

    def first():
        first_started.set()
        blocker.wait(1)

    def second():
        started.set()

    scheduler.start()
    try:
        scheduler.submit(first, task_id="first", timeout=None)
        assert first_started.wait(1)
        scheduler.submit(second, task_id="second", timeout=None)
        assert scheduler.cancel("second")
        blocker.set()
        time.sleep(0.05)
        assert not started.is_set()
        assert scheduler.status("second") == "cancelled"
    finally:
        scheduler.stop()
