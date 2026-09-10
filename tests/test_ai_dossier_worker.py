import time
import threading

from unittest.mock import patch

import services_ai_dossier_worker


def test_worker_recovery_wait_runs_one_idle_check():
    calls = []
    worker = services_ai_dossier_worker.AIDossierWorker(
        process=lambda: calls.append(time.monotonic()) or {"status": "idle"},
        recovery_interval=30.0,
        cooldown=0.01,
    )

    with patch.object(worker, "_wait", return_value=False):
        worker._run_once()

    assert len(calls) == 1


def test_worker_wake_processes_one_dossier_and_records_stats():
    result = {"status": "succeeded", "player_id": 7}
    worker = services_ai_dossier_worker.AIDossierWorker(
        process=lambda: result,
        recovery_interval=30.0,
        cooldown=0.01,
    )

    with patch.object(worker, "_wait", return_value=True), \
         patch.object(worker, "_sleep_cooldown") as sleep:
        assert worker._run_once() == result

    assert worker.last_status == "succeeded"
    sleep.assert_called_once()


def test_worker_warns_for_slow_success_without_routine_success_log():
    worker = services_ai_dossier_worker.AIDossierWorker(
        process=lambda: {"status": "succeeded"},
        recovery_interval=30.0,
        cooldown=0.01,
    )

    with patch.object(worker, "_wait", return_value=True), \
         patch.object(worker, "_sleep_cooldown"), \
         patch.object(worker, "_record_stats"), \
         patch.object(services_ai_dossier_worker.time, "monotonic", side_effect=[1.0, 7.0]), \
         patch.object(services_ai_dossier_worker.logger, "warning") as warning:
        worker._run_once()

    assert any("completed slowly" in str(call) for call in warning.call_args_list)


def test_worker_does_not_run_process_concurrently():
    worker = services_ai_dossier_worker.AIDossierWorker(
        process=lambda: {"status": "idle"},
        recovery_interval=30.0,
        cooldown=0.01,
    )
    worker._processing = True

    assert worker._run_once() is None


def test_worker_drains_backlog_after_cooldown_without_recovery_wait():
    calls = []
    worker = services_ai_dossier_worker.AIDossierWorker(
        process=lambda: calls.append(1) or {"status": "succeeded"},
        recovery_interval=30.0,
        cooldown=0.01,
    )

    with patch.object(worker, "_wait") as wait, \
         patch.object(worker, "_sleep_cooldown"):
        worker._run_once(wait_for_signal=True)
        worker._run_once(wait_for_signal=False)

    wait.assert_called_once()
    assert len(calls) == 2


def test_worker_failure_uses_backoff_and_records_error():
    worker = services_ai_dossier_worker.AIDossierWorker(
        process=lambda: {"status": "failed", "error": "AI unavailable"},
        recovery_interval=30.0,
        error_backoff=30.0,
    )

    with patch.object(worker, "_wait", return_value=True), \
         patch.object(worker, "_sleep_cooldown") as sleep, \
         patch.object(worker, "_record_stats") as record:
        result = worker._run_once()

    assert result["status"] == "failed"
    record.assert_called_once()
    assert record.call_args.args[0] == "failed"
    sleep.assert_called_once_with(30.0)


def test_worker_start_is_idempotent_and_stop_wakes_waiter():
    worker = services_ai_dossier_worker.AIDossierWorker(
        process=lambda: {"status": "idle"},
        recovery_interval=30.0,
    )

    worker.start()
    thread = worker._thread
    worker.start()
    assert worker._thread is thread

    worker.stop(timeout=1.0)
    assert worker._thread is None


def test_worker_does_not_start_a_second_thread_while_old_run_is_in_flight():
    started = threading.Event()
    release = threading.Event()

    def process():
        started.set()
        release.wait(1.0)
        return {"status": "succeeded"}

    worker = services_ai_dossier_worker.AIDossierWorker(
        process=process,
        recovery_interval=30.0,
        cooldown=0.01,
    )
    worker.start()
    worker.wake()
    assert started.wait(1.0)
    original_thread = worker._thread

    worker.stop(timeout=0.01)
    worker.start()
    assert worker._thread is original_thread

    release.set()
    deadline = time.monotonic() + 1.0
    while worker._thread is original_thread and time.monotonic() < deadline:
        time.sleep(0.01)
    assert worker._thread is not None
    assert worker._thread is not original_thread
    worker.stop(timeout=1.0)
    assert worker._thread is None
