import unittest
from unittest.mock import Mock, patch

from routes.dispatch import _post_ai_dispatcher_sync


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


if __name__ == "__main__":
    unittest.main()
