from types import SimpleNamespace
from unittest.mock import patch

import services_ai_client


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _Response()

    def close(self):
        return None


def _settings():
    return SimpleNamespace(
        ai_dossier_url="https://ai.test/v1/chat/completions",
        ai_dossier_api_key="secret",
        ai_dossier_tls_ca_file="",
        ai_dossier_timeout_seconds=30,
        ai_dossier_model="qwen",
    )


def test_ai_client_reuses_http_connection_and_builds_split_timeouts():
    services_ai_client.close_ai_client()
    with patch.object(services_ai_client, "get_settings", return_value=_settings()), \
         patch.object(services_ai_client.httpx, "Client", side_effect=_Client) as factory:
        services_ai_client.post_chat_completions({"n": 1}, timeout=30)
        services_ai_client.post_chat_completions({"n": 2}, timeout=30)

    assert factory.call_count == 1
    timeout = factory.call_args.kwargs["timeout"]
    assert timeout.connect == 10
    assert timeout.pool == 5
    assert timeout.read == 30
    services_ai_client.close_ai_client()
