from routes.admin import build_task_status_payload


class _FakeScheduler:
    def queue_size(self):
        return 3

    def list_tasks(self):
        return [{"task_id": "daily", "status": "running"}]

    def active_tasks(self):
        return [{"task_id": "daily", "status": "running", "priority": 4}]

    def overlap_snapshot(self):
        return {"active_count": 1, "task_ids": ["daily"], "overlap": False}

    def task_stats(self):
        return [{
            "task_id": "daily",
            "duration_p95_ms": 120,
            "duration_p99_ms": 140,
            "queue_wait_p95_ms": 2,
            "queue_wait_p99_ms": 3,
            "sample_count": 8,
            "last_status": "done",
        }]


def test_task_status_payload_exposes_scheduler_metrics_in_task_rows():
    payload = build_task_status_payload(_FakeScheduler())

    assert payload["queue_size"] == 3
    assert payload["tasks"][0]["task_id"] == "daily"
    assert payload["tasks"][0]["duration_p95_ms"] == 120
    assert payload["tasks"][0]["queue_wait_p99_ms"] == 3
    assert payload["active_tasks"][0]["task_id"] == "daily"
    assert payload["overlap"]["active_count"] == 1
    assert payload["stats"][0]["duration_p99_ms"] == 140
