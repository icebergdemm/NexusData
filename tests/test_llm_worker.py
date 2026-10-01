import threading
from copy import deepcopy

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services_ai_client
import services_llm_gateway as gateway
import services_llm_worker as worker_module
import database
from models import AILlmRequest, AITaskLog


def test_submit_wakes_idle_worker_and_worker_lifecycle_clears_callback(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AILlmRequest.__table__.create(engine)
    AITaskLog.__table__.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(gateway, "SessionLocal", sessions)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(gateway, "LLM_CLIENTS", deepcopy(gateway.LLM_CLIENTS))
    monkeypatch.setattr(gateway, "_wake", None)
    monkeypatch.setattr(services_ai_client, "close_ai_client", lambda: None)

    idle_wait_started = threading.Event()
    backend_started = threading.Event()
    ticket_finished = threading.Event()

    class ObservedEvent(threading.Event):
        def wait(self, timeout=None):
            if timeout == 30:
                idle_wait_started.set()
            return super().wait(timeout)

    worker = worker_module.AILlmWorker()
    worker._wake = ObservedEvent()
    def fake_backend(*args, **kwargs):
        backend_started.set()
        return {"choices": []}, 1

    monkeypatch.setattr(worker_module, "post_backend", fake_backend)
    finish = worker_module.finish

    def record_finish(*args, **kwargs):
        result = finish(*args, **kwargs)
        ticket_finished.set()
        return result

    monkeypatch.setattr(worker_module, "finish", record_finish)
    assert worker._loop.__func__.__globals__["post_backend"] is worker_module.post_backend
    worker.start()
    try:
        assert idle_wait_started.wait(1)
        assert gateway._wake.__self__ is worker
        ticket_id = gateway.submit(
            "address_detector_fast",
            [{"role": "user", "content": "test"}],
            {"model": "test", "max_tokens": 64},
            idempotency_key="address_detector_fast:123:1",
        )
        assert backend_started.wait(1), "gateway submit left the worker in its 30-second idle wait"
        assert ticket_finished.wait(1)
        assert gateway.poll(ticket_id)["status"] == "succeeded"
    finally:
        worker.stop()

    assert gateway._wake is None
    worker.start()
    assert gateway._wake.__self__ is worker
    worker.stop()
    assert gateway._wake is None
