import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import RoundEvent, WorldRound
from routes.dispatch import _post_ai_dispatcher_sync, _resolve_ai_conversation_round_id


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

    @patch("routes.dispatch._send_dispatcher_message")
    @patch("routes.dispatch.GameSessionLocal")
    @patch("routes.dispatch.httpx.Client", _FakeClient)
    def test_answer_is_queued_for_the_source_server(self, session_factory, send_message):
        _FakeClient.response_payload = {
            "status": "answered",
            "reply": "Связь установлена.",
        }
        db = Mock()
        session_factory.return_value = db
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
        send_message.assert_called_once_with(
            db,
            "Связь установлена.",
            "ASPER2",
            admin_id=None,
            sender_label="ИИ-диспетчер",
        )
        db.close.assert_called_once()

    @patch("routes.dispatch._send_dispatcher_message")
    @patch("routes.dispatch.GameSessionLocal")
    @patch("routes.dispatch.httpx.Client", _FakeClient)
    def test_multi_part_answer_is_queued_in_order(self, session_factory, send_message):
        _FakeClient.response_payload = {
            "status": "answered",
            "reply": "Корпус держится.",
            "replies": [
                "Корпус держится.",
                "Кормовой отсек принимает воду; направьте туда механика.",
            ],
        }
        db = Mock()
        session_factory.return_value = db
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

        self.assertEqual(send_message.call_count, 2)
        self.assertEqual(
            [call.args[1] for call in send_message.call_args_list],
            [
                "Корпус держится.",
                "Кормовой отсек принимает воду; направьте туда механика.",
            ],
        )
        for call in send_message.call_args_list:
            self.assertEqual(call.args[0], db)
            self.assertEqual(call.args[2], "ASPER2")
            self.assertEqual(call.kwargs["admin_id"], None)
            self.assertEqual(call.kwargs["sender_label"], "ИИ-диспетчер")
        db.close.assert_called_once()

    @patch("routes.dispatch._send_dispatcher_message")
    @patch("routes.dispatch.GameSessionLocal")
    @patch("routes.dispatch.httpx.Client", _FakeClient)
    def test_legacy_long_answer_is_split_without_cutting_words(self, session_factory, send_message):
        reply = (
            "Первый доклад содержит законченную сводку о состоянии корпуса и отсеков. "
            "Второй доклад содержит отдельную рекомендацию экипажу по дальнейшему курсу. "
            "Третий доклад подтверждает, что ближайшие навигационные контакты отмечены на карте."
        )
        _FakeClient.response_payload = {"status": "answered", "reply": reply}
        session_factory.return_value = Mock()

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

        sent = [call.args[1] for call in send_message.call_args_list]
        self.assertGreater(len(sent), 1)
        self.assertEqual(" ".join(sent), reply)
        self.assertTrue(all(len("[Диспетчер] " + part) <= 200 for part in sent))

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


if __name__ == "__main__":
    unittest.main()
