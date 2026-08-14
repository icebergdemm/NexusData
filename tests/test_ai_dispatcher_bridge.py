import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, call, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import RoundEvent, WorldRound
import routes.dispatch as dispatch_routes
from schemas import RoundStartIn
from routes.dispatch import (
    _AIDispatchJob,
    _ai_dispatch_job_queues,
    _ai_reply_delay_seconds,
    _cancel_ai_dispatch_action,
    dispatch_check_pending,
    _deliver_ai_dispatcher_replies,
    _enqueue_ai_dispatch_job,
    _extract_ai_dispatcher_replies,
    _post_ai_dispatcher_sync,
    _queue_ai_round_briefing,
    _queue_ai_world_briefing,
    _queue_ai_radio_action,
    _resolve_ai_conversation_round_id,
    _send_dispatcher_message,
    _split_ai_radio_reply,
    _wait_for_ai_dispatch_action_ack,
    post_round_start,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    response_payload = {}
    last_request = None

    def __init__(self, *, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, *, headers, json):
        type(self).last_request = (url, headers, json)
        return _FakeResponse(type(self).response_payload)


class AIDispatcherBridgeTests(unittest.TestCase):
    def setUp(self):
        _FakeClient.last_request = None

    @patch("routes.dispatch._queue_ai_round_briefing")
    @patch("routes.dispatch._queue_round_status_refresh")
    def test_round_start_normalizes_server_and_queues_briefing(
        self,
        refresh_status,
        queue_briefing,
    ):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[RoundEvent.__table__])
        db = Session(engine)
        try:
            result = post_round_start(
                RoundStartIn(
                    server_name=" asper1 ",
                    submarine_name="Сигнатура",
                    mission="Доставка",
                ),
                db,
            )

            event = db.query(RoundEvent).one()
            self.assertEqual(result["round_id"], event.id)
            self.assertEqual(event.server_name, "ASPER1")
            refresh_status.assert_called_once_with(db, "ASPER1")
            queue_briefing.assert_called_once_with(event, db)
        finally:
            db.close()
            engine.dispose()

    @patch("routes.dispatch._enqueue_ai_dispatch_job")
    def test_round_start_briefing_is_queued_with_context(self, enqueue_job):
        event = RoundEvent(
            id=42,
            event_type="start",
            server_name="ASPER1",
            submarine_name="Сигнатура",
            mission="Доставка",
            location="Европейский кряж",
        )

        with (
            patch.object(
                dispatch_routes.settings,
                "ai_dispatcher_url",
                "http://dispatcher/api/v1/dispatcher/messages",
            ),
            patch.object(
                dispatch_routes.settings,
                "ai_dispatcher_api_key",
                "dispatcher-secret",
            ),
        ):
            _queue_ai_round_briefing(event)

        job = enqueue_job.call_args.args[0]
        self.assertEqual(job.server_name, "ASPER1")
        self.assertEqual(job.max_attempts, 10)
        self.assertGreater(job.initial_delay_seconds, 0)
        self.assertGreater(job.retry_delay_seconds, 0)
        self.assertGreaterEqual(
            job.initial_delay_seconds
            + (job.max_attempts - 1) * job.retry_delay_seconds,
            80,
        )
        self.assertEqual(
            job.payload,
            {
                "event_id": "nexus-round-briefing-42",
                "round_id": "dispatch-ASPER1-round-42",
                "server_name": "ASPER1",
                "text": "Первичный доклад по сектору.",
                "sender_name": None,
                "message_type": "round_briefing",
                "briefing_context": {
                    "submarine_name": "Сигнатура",
                    "mission": "Доставка",
                    "location": "Европейский кряж",
                },
            },
        )

    @patch("routes.dispatch._enqueue_ai_dispatch_job")
    def test_world_telemetry_queues_one_briefing_without_round_start(self, enqueue_job):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(
            engine,
            tables=[RoundEvent.__table__, WorldRound.__table__],
        )
        db = Session(engine)
        try:
            world_round = WorldRound(
                round_id="world-round-42",
                server_name="ASPER1",
                sub_name="Сигнатура",
                level_name="Европейский кряж",
                last_update_at=datetime.utcnow(),
            )
            db.add(world_round)
            db.commit()
            db.refresh(world_round)

            with (
                patch.object(
                    dispatch_routes.settings,
                    "ai_dispatcher_url",
                    "http://dispatcher/api/v1/dispatcher/messages",
                ),
                patch.object(
                    dispatch_routes.settings,
                    "ai_dispatcher_api_key",
                    "dispatcher-secret",
                ),
                patch("routes.dispatch.secrets.randbelow", return_value=7),
            ):
                _queue_ai_world_briefing(world_round, db)
                _queue_ai_world_briefing(world_round, db)

            enqueue_job.assert_called_once()
            job = enqueue_job.call_args.args[0]
            self.assertEqual(job.initial_delay_seconds, 22.0)
            self.assertEqual(job.max_attempts, 10)
            self.assertEqual(
                job.payload,
                {
                    "event_id": f"nexus-world-briefing-{world_round.id}",
                    "round_id": "world-round-42",
                    "server_name": "ASPER1",
                    "text": "Первичный доклад по сектору.",
                    "sender_name": None,
                    "message_type": "round_briefing",
                    "briefing_context": {
                        "submarine_name": "Сигнатура",
                        "mission": None,
                        "location": "Европейский кряж",
                    },
                },
            )
            self.assertIsNotNone(world_round.ai_briefing_queued_at)
        finally:
            db.close()
            engine.dispose()

    @patch("routes.dispatch._enqueue_ai_dispatch_job")
    def test_world_telemetry_does_not_duplicate_recent_lifecycle_briefing(
        self,
        enqueue_job,
    ):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(
            engine,
            tables=[RoundEvent.__table__, WorldRound.__table__],
        )
        db = Session(engine)
        try:
            now = datetime.utcnow()
            world_round = WorldRound(
                round_id="world-round-43",
                server_name="ASPER1",
                created_at=now,
                last_update_at=now,
            )
            event = RoundEvent(
                event_type="start",
                server_name="ASPER1",
                created_at=now,
            )
            db.add_all([world_round, event])
            db.commit()
            db.refresh(world_round)

            with (
                patch.object(
                    dispatch_routes.settings,
                    "ai_dispatcher_url",
                    "http://dispatcher/api/v1/dispatcher/messages",
                ),
                patch.object(
                    dispatch_routes.settings,
                    "ai_dispatcher_api_key",
                    "dispatcher-secret",
                ),
            ):
                _queue_ai_world_briefing(world_round, db)

            enqueue_job.assert_not_called()
            self.assertIsNotNone(world_round.ai_briefing_queued_at)
        finally:
            db.close()
            engine.dispose()

    @patch("routes.dispatch._deliver_ai_dispatcher_replies")
    @patch("routes.dispatch.httpx.Client", _FakeClient)
    def test_answer_is_queued_for_the_source_server(self, deliver_replies):
        _FakeClient.response_payload = {
            "status": "answered",
            "reply": "Связь установлена.",
        }
        payload = {
            "event_id": "nexus-dispatch-42",
            "round_id": "round-1",
            "server_name": "ASPER2",
            "text": "Диспетчер, приём.",
            "sender_name": "Капитан",
        }

        _post_ai_dispatcher_sync(
            "http://dispatcher/api/v1/dispatcher/messages",
            "dispatcher-secret",
            payload,
            "ASPER2",
        )

        self.assertEqual(
            _FakeClient.last_request,
            (
                "http://dispatcher/api/v1/dispatcher/messages",
                {"X-API-Key": "dispatcher-secret"},
                payload,
            ),
        )
        deliver_replies.assert_called_once_with(
            ["Связь установлена."],
            server_name="ASPER2",
            event_id="nexus-dispatch-42",
            round_id="round-1",
            enqueued_at=None,
        )

    @patch("routes.dispatch._deliver_ai_dispatcher_replies")
    @patch("routes.dispatch.httpx.Client", _FakeClient)
    def test_multi_part_answer_is_queued_in_order(self, deliver_replies):
        _FakeClient.response_payload = {
            "status": "answered",
            "reply": "Корпус держится.",
            "replies": [
                "Корпус держится.",
                "Кормовой отсек принимает воду; направьте туда механика.",
            ],
        }
        payload = {
            "event_id": "nexus-dispatch-44",
            "round_id": "round-1",
            "server_name": "ASPER2",
            "text": "Диспетчер, состояние судна?",
            "sender_name": "Капитан",
        }

        _post_ai_dispatcher_sync(
            "http://dispatcher/api/v1/dispatcher/messages",
            "dispatcher-secret",
            payload,
            "ASPER2",
        )

        deliver_replies.assert_called_once_with(
            [
                "Корпус держится.",
                "Кормовой отсек принимает воду; направьте туда механика.",
            ],
            server_name="ASPER2",
            event_id="nexus-dispatch-44",
            round_id="round-1",
            enqueued_at=None,
        )

    @patch("routes.dispatch._deliver_ai_dispatcher_replies")
    @patch("routes.dispatch.httpx.Client", _FakeClient)
    def test_legacy_long_answer_is_split_without_cutting_words(self, deliver_replies):
        reply = (
            "Первый доклад содержит законченную сводку о состоянии корпуса и отсеков. "
            "Второй доклад содержит отдельную рекомендацию экипажу по дальнейшему курсу. "
            "Третий доклад подтверждает, что ближайшие навигационные контакты отмечены на карте."
        )
        _FakeClient.response_payload = {"status": "answered", "reply": reply}

        _post_ai_dispatcher_sync(
            "http://dispatcher/api/v1/dispatcher/messages",
            "dispatcher-secret",
            {
                "event_id": "nexus-dispatch-45",
                "round_id": "round-1",
                "server_name": "ASPER1",
                "text": "Диспетчер, дайте полную сводку.",
                "sender_name": "Капитан",
            },
            "ASPER1",
        )

        sent = deliver_replies.call_args.args[0]
        self.assertGreater(len(sent), 1)
        self.assertEqual(" ".join(sent), reply)
        self.assertTrue(all(len("[Диспетчер] " + part) <= 200 for part in sent))
        self.assertEqual(deliver_replies.call_args.kwargs["server_name"], "ASPER1")
        self.assertEqual(deliver_replies.call_args.kwargs["event_id"], "nexus-dispatch-45")

    @patch("routes.dispatch.GameSessionLocal")
    @patch("routes.dispatch.httpx.Client", _FakeClient)
    def test_ignored_message_does_not_open_database(self, session_factory):
        _FakeClient.response_payload = {
            "status": "ignored",
            "event_id": "nexus-dispatch-43",
            "reason": "not_addressed",
        }

        _post_ai_dispatcher_sync(
            "http://dispatcher/api/v1/dispatcher/messages",
            "dispatcher-secret",
            {
                "event_id": "nexus-dispatch-43",
                "round_id": "round-1",
                "server_name": "ASPER1",
                "text": "Обычное сообщение",
                "sender_name": "Капитан",
            },
            "ASPER1",
        )

        session_factory.assert_not_called()


class AIReplyDeliveryQueueTests(unittest.TestCase):
    def tearDown(self):
        # These tests replace Thread.start, so no live worker owns the queues.
        _ai_dispatch_job_queues.clear()

    def test_delay_is_monotonic_and_stays_between_four_and_twelve_seconds(self):
        delays = [
            _ai_reply_delay_seconds(""),
            _ai_reply_delay_seconds("Кратко."),
            _ai_reply_delay_seconds("С" * 94),
            _ai_reply_delay_seconds("Д" * 188),
            _ai_reply_delay_seconds("П" * 500),
        ]

        self.assertEqual(delays[0], 4.0)
        self.assertEqual(delays[-1], 12.0)
        self.assertTrue(all(4.0 <= delay <= 12.0 for delay in delays))
        self.assertEqual(delays, sorted(delays))

    @patch("routes.dispatch.threading.Thread")
    def test_one_fifo_is_created_per_server(self, thread_class):
        thread_class.return_value = Mock()
        first = _AIDispatchJob("http://ai", "key", {"event_id": "first"}, "ASPER1")
        second = _AIDispatchJob("http://ai", "key", {"event_id": "second"}, "ASPER1")
        other_server = _AIDispatchJob("http://ai", "key", {"event_id": "other"}, "ASPER2")

        _enqueue_ai_dispatch_job(first)
        _enqueue_ai_dispatch_job(second)
        _enqueue_ai_dispatch_job(other_server)

        self.assertEqual(thread_class.call_count, 2)
        self.assertEqual(
            [_ai_dispatch_job_queues["ASPER1"].get_nowait().payload["event_id"] for _ in range(2)],
            ["first", "second"],
        )
        self.assertEqual(
            _ai_dispatch_job_queues["ASPER2"].get_nowait().payload["event_id"],
            "other",
        )

    @patch("routes.dispatch._wait_for_ai_dispatch_action_ack", return_value=1)
    @patch("routes.dispatch._queue_ai_radio_action", side_effect=[101, 102])
    @patch("routes.dispatch.time.sleep")
    def test_parts_wait_for_delay_send_and_ack_strictly_in_order(
        self,
        sleep,
        queue_action,
        wait_for_ack,
    ):
        timeline = Mock()
        timeline.attach_mock(sleep, "sleep")
        timeline.attach_mock(queue_action, "send")
        timeline.attach_mock(wait_for_ack, "ack")
        replies = ["Принял.", "Держите курс к станции."]

        _deliver_ai_dispatcher_replies(
            replies,
            server_name="ASPER1",
            event_id="nexus-dispatch-50",
        )

        first_delay = _ai_reply_delay_seconds(replies[0])
        second_delay = _ai_reply_delay_seconds(replies[1])
        self.assertEqual(
            timeline.mock_calls,
            [
                call.sleep(first_delay),
                call.send(replies[0], "ASPER1", "nexus-dispatch-50", 1, 2),
                call.ack(101, "ASPER1", "nexus-dispatch-50"),
                call.sleep(second_delay),
                call.send(replies[1], "ASPER1", "nexus-dispatch-50", 2, 2),
                call.ack(102, "ASPER1", "nexus-dispatch-50"),
            ],
        )

    @patch("routes.dispatch._send_dispatcher_message_locked")
    @patch("routes.dispatch.time.sleep")
    @patch("routes.dispatch.GameSessionLocal")
    def test_action_is_not_inserted_until_existing_dispatch_is_done(
        self,
        session_factory,
        sleep,
        send_message,
    ):
        busy_db = Mock()
        busy_db.query.return_value.filter.return_value.first.return_value = (41,)
        idle_db = Mock()
        idle_db.query.return_value.filter.return_value.first.return_value = None
        session_factory.side_effect = [busy_db, idle_db]
        send_message.return_value = (Mock(), Mock(id=77))

        action_id = _queue_ai_radio_action(
            "Принял.",
            "ASPER2",
            "nexus-dispatch-51",
            1,
            1,
        )

        self.assertEqual(action_id, 77)
        sleep.assert_called_once_with(1.0)
        send_message.assert_called_once_with(
            idle_db,
            "Принял.",
            "ASPER2",
            admin_id=None,
            sender_label="ИИ-диспетчер",
        )
        busy_db.close.assert_called_once()
        idle_db.close.assert_called_once()

    @patch("routes.dispatch.time.sleep")
    @patch("routes.dispatch.GameSessionLocal")
    def test_ack_wait_stops_only_after_target_server_status_changes(
        self,
        session_factory,
        sleep,
    ):
        pending_db = Mock()
        pending_db.query.return_value.filter.return_value.first.return_value = Mock(
            status_asper1=0,
            status_asper2=1,
        )
        acknowledged_db = Mock()
        acknowledged_db.query.return_value.filter.return_value.first.return_value = Mock(
            status_asper1=1,
            status_asper2=1,
        )
        session_factory.side_effect = [pending_db, acknowledged_db]

        status = _wait_for_ai_dispatch_action_ack(77, "ASPER1", "nexus-dispatch-52")

        self.assertEqual(status, 1)
        sleep.assert_called_once_with(1.0)
        pending_db.close.assert_called_once()
        acknowledged_db.close.assert_called_once()

    @patch("routes.dispatch.time.sleep")
    @patch("routes.dispatch.GameSessionLocal")
    def test_failed_ack_is_terminal(self, session_factory, sleep):
        failed_db = Mock()
        failed_db.query.return_value.filter.return_value.first.return_value = Mock(
            status_asper1=1,
            status_asper2=2,
        )
        session_factory.return_value = failed_db

        status = _wait_for_ai_dispatch_action_ack(78, "ASPER2", "nexus-dispatch-53")

        self.assertEqual(status, 2)
        sleep.assert_not_called()
        failed_db.close.assert_called_once()

    @patch("routes.dispatch._wait_for_ai_dispatch_action_ack", return_value=2)
    @patch("routes.dispatch._queue_ai_radio_action", side_effect=[101, 102])
    @patch("routes.dispatch.time.sleep")
    def test_failed_ack_prevents_sending_continuation(
        self,
        sleep,
        queue_action,
        wait_for_ack,
    ):
        _deliver_ai_dispatcher_replies(
            ["Первая часть.", "Вторая часть."],
            server_name="ASPER1",
            event_id="nexus-dispatch-54",
        )

        sleep.assert_called_once_with(_ai_reply_delay_seconds("Первая часть."))
        queue_action.assert_called_once_with(
            "Первая часть.",
            "ASPER1",
            "nexus-dispatch-54",
            1,
            2,
        )
        wait_for_ack.assert_called_once_with(101, "ASPER1", "nexus-dispatch-54")

    @patch("routes.dispatch._queue_ai_radio_action")
    @patch("routes.dispatch.time.sleep")
    @patch("routes.dispatch._ai_dispatch_context_is_current", return_value=False)
    def test_stale_round_is_dropped_before_delay(
        self,
        context_is_current,
        sleep,
        queue_action,
    ):
        _deliver_ai_dispatcher_replies(
            ["Старая радиограмма."],
            server_name="ASPER1",
            event_id="nexus-dispatch-54-stale",
            round_id="old-round",
            enqueued_at=10.0,
        )

        context_is_current.assert_called_once_with("ASPER1", "old-round", 10.0)
        sleep.assert_not_called()
        queue_action.assert_not_called()

    @patch("routes.dispatch._send_dispatcher_message_locked")
    @patch("routes.dispatch._ai_dispatch_context_matches_db", return_value=False)
    @patch("routes.dispatch.GameSessionLocal")
    def test_round_is_checked_again_atomically_before_action_insert(
        self,
        session_factory,
        context_matches,
        send_message,
    ):
        callback_db = Mock()
        session_factory.return_value = callback_db

        action_id = _queue_ai_radio_action(
            "Запоздалая радиограмма.",
            "ASPER2",
            "nexus-dispatch-54-race",
            1,
            1,
            expected_round_id="old-round",
            enqueued_at=20.0,
        )

        self.assertIsNone(action_id)
        context_matches.assert_called_once_with(
            callback_db,
            "ASPER2",
            "old-round",
            20.0,
        )
        send_message.assert_not_called()
        callback_db.close.assert_called_once()

    @patch("routes.dispatch._cancel_ai_dispatch_action")
    @patch("routes.dispatch._AI_ACTION_ACK_TIMEOUT_SECONDS", 0)
    @patch("routes.dispatch.GameSessionLocal")
    def test_ack_wait_has_a_deadline(self, session_factory, cancel_action):
        self.assertIsNone(
            _wait_for_ai_dispatch_action_ack(79, "ASPER1", "nexus-dispatch-55")
        )
        session_factory.assert_not_called()
        cancel_action.assert_called_once_with(
            79,
            "ASPER1",
            "nexus-dispatch-55",
            reason="Истекло время ожидания подтверждения радиограммы",
        )

    @patch("routes.dispatch.GameSessionLocal")
    def test_cancelling_unacknowledged_action_marks_only_target_pending_status_failed(
        self,
        session_factory,
    ):
        callback_db = Mock()
        update_query = callback_db.query.return_value.filter.return_value
        update_query.update.return_value = 1
        session_factory.return_value = callback_db

        cancelled = _cancel_ai_dispatch_action(
            79,
            "ASPER2",
            "nexus-dispatch-55",
            reason="Тестовая отмена",
        )

        self.assertTrue(cancelled)
        update_query.update.assert_called_once()
        values = update_query.update.call_args.args[0]
        self.assertEqual(values[next(key for key in values if key.key == "status_asper2")], 2)
        self.assertEqual(
            values[next(key for key in values if key.key == "error_asper2")],
            "Тестовая отмена",
        )
        self.assertFalse(any(key.key == "status_asper1" for key in values))
        callback_db.commit.assert_called_once()
        callback_db.close.assert_called_once()

    @patch("routes.dispatch._AI_CHANNEL_WAIT_TIMEOUT_SECONDS", 0)
    @patch("routes.dispatch.GameSessionLocal")
    def test_channel_wait_has_a_deadline(self, session_factory):
        self.assertIsNone(
            _queue_ai_radio_action(
                "Доклад.",
                "ASPER1",
                "nexus-dispatch-56",
                1,
                1,
            )
        )
        session_factory.assert_not_called()

    @patch("routes.dispatch._AI_DISPATCH_QUEUE_MAXSIZE", 1)
    @patch("routes.dispatch.threading.Thread")
    def test_full_queue_drops_new_request_without_blocking(self, thread_class):
        thread_class.return_value = Mock()
        first = _AIDispatchJob("http://ai", "key", {"event_id": "first"}, "ASPER1")
        overflow = _AIDispatchJob(
            "http://ai",
            "key",
            {"event_id": "overflow"},
            "ASPER1",
        )

        _enqueue_ai_dispatch_job(first)
        _enqueue_ai_dispatch_job(overflow)

        self.assertEqual(_ai_dispatch_job_queues["ASPER1"].qsize(), 1)
        self.assertEqual(
            _ai_dispatch_job_queues["ASPER1"].get_nowait().payload["event_id"],
            "first",
        )

    def test_reply_extractor_keeps_four_model_authored_parts(self):
        replies = _extract_ai_dispatcher_replies(
            {
                "replies": [
                    "Первая законченная радиограмма.",
                    "Вторая законченная радиограмма.",
                    "Третья законченная радиограмма.",
                    "Четвёртая законченная радиограмма.",
                ]
            }
        )

        self.assertEqual(
            replies,
            [
                "Первая законченная радиограмма.",
                "Вторая законченная радиограмма.",
                "Третья законченная радиограмма.",
                "Четвёртая законченная радиограмма.",
            ],
        )

    def test_legacy_oversized_sentence_is_split_into_finished_clauses(self):
        reply = (
            "По карте поста впереди спокойный проход, держите текущий курс и малый ход, "
            "контролируйте глубину и не теряйте бортовой маяк из виду, "
            "при новом контакте доложите примерную дистанцию до цели."
        )

        parts = _split_ai_radio_reply(reply, 188)

        self.assertEqual(len(parts), 2)
        self.assertTrue(all(part.endswith((".", "!", "?", "…")) for part in parts))
        self.assertTrue(
            all(not part.endswith((",", ";", ":", "—", "-")) for part in parts)
        )
        self.assertTrue(parts[1][0].isupper())

    def test_unbreakable_legacy_sentence_is_rejected_without_synthetic_text(self):
        with self.assertRaises(ValueError):
            _split_ai_radio_reply(" ".join(["подробность"] * 80), 188)

    def test_oversized_terminal_clause_is_rejected_without_synthetic_text(self):
        with self.assertRaises(ValueError):
            _split_ai_radio_reply(f"{'А' * 186}, {'б' * 188}", 188)

    @patch("routes.dispatch._send_dispatcher_message_locked")
    @patch("routes.dispatch.GameSessionLocal")
    @patch("routes.dispatch._dispatch_process_locks")
    def test_manual_send_uses_short_channel_lock_during_ai_delay(
        self,
        process_locks,
        session_factory,
        send_locked,
    ):
        process_locks.return_value = MagicMock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )
        request_db = Mock()
        channel_db = Mock()
        channel_db.query.return_value.filter.return_value.first.return_value = None
        session_factory.return_value = channel_db
        send_locked.return_value = (Mock(id=10), Mock(id=11))

        result = _send_dispatcher_message(
            request_db,
            "Ручная радиограмма.",
            "ASPER1",
            admin_id=7,
            sender_label="Оператор",
        )

        self.assertEqual(result[1].id, 11)
        process_locks.assert_called_once_with(
            ("ASPER1",),
            blocking=True,
            lock_scope="channel",
        )
        send_locked.assert_called_once_with(
            channel_db,
            "Ручная радиограмма.",
            "ASPER1",
            7,
            "Оператор",
        )
        request_db.query.assert_not_called()
        channel_db.close.assert_called_once()

    @patch("routes.dispatch.check_permission", return_value=True)
    def test_pending_check_is_scoped_to_requested_server(self, _permission):
        db = MagicMock()
        action_query = MagicMock()
        scoped_query = MagicMock()
        db.query.return_value.filter.return_value = action_query
        action_query.filter.return_value = scoped_query
        scoped_query.first.return_value = Mock(id=91)

        result = dispatch_check_pending(
            server="asper2",
            db=db,
            current_admin=Mock(),
        )

        self.assertEqual(result, {"pending": True, "action_id": 91})
        status_expression = str(action_query.filter.call_args.args[0])
        self.assertIn("status_asper2", status_expression)
        self.assertNotIn("status_asper1", status_expression)

    @patch("routes.dispatch.check_permission", return_value=True)
    def test_pending_check_rejects_unknown_server(self, _permission):
        with self.assertRaises(HTTPException) as raised:
            dispatch_check_pending(
                server="ASPER3",
                db=MagicMock(),
                current_admin=Mock(),
            )

        self.assertEqual(raised.exception.status_code, 400)

    @patch("routes.dispatch.GameSessionLocal")
    @patch("routes.dispatch._dispatch_process_locks")
    def test_manual_send_is_rejected_when_dispatch_action_is_pending(
        self,
        process_locks,
        session_factory,
    ):
        process_locks.return_value = MagicMock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )
        request_db = Mock()
        channel_db = Mock()
        channel_db.query.return_value.filter.return_value.first.return_value = (91,)
        session_factory.return_value = channel_db

        with self.assertRaises(HTTPException) as raised:
            _send_dispatcher_message(
                request_db,
                "Ручная радиограмма.",
                "ASPER2",
                admin_id=7,
                sender_label="Оператор",
            )

        self.assertEqual(raised.exception.status_code, 409)
        request_db.query.assert_not_called()
        channel_db.add.assert_not_called()
        channel_db.close.assert_called_once()


class AIConversationRoundTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[RoundEvent.__table__, WorldRound.__table__],
        )
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_round_event_keeps_asper2_conversation_stable_without_asper2_world_state(self):
        self.db.add(
            WorldRound(
                round_id="world-asper1",
                server_name="ASPER1",
                last_update_at=datetime.utcnow(),
            )
        )
        start = RoundEvent(
            event_type="start",
            server_name="ASPER2",
            created_at=datetime.utcnow(),
        )
        self.db.add(start)
        self.db.commit()

        first = _resolve_ai_conversation_round_id(self.db, "ASPER2")
        second = _resolve_ai_conversation_round_id(self.db, "ASPER2")

        self.assertEqual(first, f"dispatch-ASPER2-round-{start.id}")
        self.assertEqual(second, first)

    def test_new_round_boundary_changes_conversation_id(self):
        self.assertEqual(
            _resolve_ai_conversation_round_id(self.db, "ASPER2"),
            "dispatch-ASPER2-idle-0",
        )
        end = RoundEvent(
            event_type="end",
            server_name="ASPER2",
            created_at=datetime.utcnow(),
        )
        self.db.add(end)
        self.db.commit()

        self.assertEqual(
            _resolve_ai_conversation_round_id(self.db, "ASPER2"),
            f"dispatch-ASPER2-idle-{end.id}",
        )

    def test_telemetry_freshness_cannot_flip_event_anchored_conversation(self):
        world_round = WorldRound(
            round_id="world-asper1",
            server_name="ASPER1",
            last_update_at=datetime(2026, 1, 1),
        )
        start = RoundEvent(
            event_type="start",
            server_name="ASPER1",
            created_at=datetime.utcnow(),
        )
        self.db.add_all([world_round, start])
        self.db.commit()

        expected = f"dispatch-ASPER1-round-{start.id}"
        self.assertEqual(_resolve_ai_conversation_round_id(self.db, "ASPER1"), expected)

        world_round.last_update_at = datetime.utcnow()
        self.db.commit()
        self.assertEqual(_resolve_ai_conversation_round_id(self.db, "ASPER1"), expected)

    def test_round_end_overrides_still_fresh_world_snapshot(self):
        self.db.add(
            WorldRound(
                round_id="ending-world",
                server_name="ASPER1",
                last_update_at=datetime.utcnow(),
            )
        )
        end = RoundEvent(
            event_type="end",
            server_name="ASPER1",
            created_at=datetime.utcnow(),
        )
        self.db.add(end)
        self.db.commit()

        self.assertEqual(
            _resolve_ai_conversation_round_id(self.db, "ASPER1"),
            f"dispatch-ASPER1-idle-{end.id}",
        )

    def test_world_round_is_stable_fallback_without_lifecycle_events(self):
        world_round = WorldRound(
            round_id="world-only-round",
            server_name="ASPER1",
            last_update_at=datetime(2026, 1, 1),
        )
        self.db.add(world_round)
        self.db.commit()

        self.assertEqual(
            _resolve_ai_conversation_round_id(self.db, "ASPER1"),
            "world-only-round",
        )

        world_round.last_update_at = datetime.utcnow()
        self.db.commit()
        self.assertEqual(
            _resolve_ai_conversation_round_id(self.db, "ASPER1"),
            "world-only-round",
        )

    def test_new_world_round_overrides_old_end_without_new_lifecycle_event(self):
        old_end = RoundEvent(
            event_type="end",
            server_name="ASPER1",
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        world_round = WorldRound(
            round_id="world-after-old-end",
            server_name="ASPER1",
            created_at=datetime.utcnow(),
            last_update_at=datetime.utcnow(),
        )
        self.db.add_all([old_end, world_round])
        self.db.commit()

        self.assertEqual(
            _resolve_ai_conversation_round_id(self.db, "ASPER1"),
            "world-after-old-end",
        )


if __name__ == "__main__":
    unittest.main()
