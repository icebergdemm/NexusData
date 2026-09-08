import unittest
from types import SimpleNamespace
from unittest.mock import patch

import services_ai_client
import services_ai_dossier
import services_llm_monster
from services_ai_client import AIClientError


def _settings(**overrides):
    values = {
        "ai_dossier_url": "https://ai.example/v1/chat/completions",
        "ai_dossier_api_key": "test-key",
        "ai_dossier_tls_ca_file": "",
        "ai_dossier_timeout_seconds": 180.0,
        "monster_llm_model": "qwen3-4b-instruct",
        "monster_llm_timeout_seconds": 90.0,
        "monster_llm_temperature": 0.15,
        "monster_llm_top_p": 0.8,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class SharedAIClientTests(unittest.TestCase):
    def test_monster_and_dossier_share_the_same_request_lock(self):
        self.assertIs(services_ai_dossier.AI_REQUEST_LOCK, services_ai_client.AI_REQUEST_LOCK)

    def test_post_chat_completions_requires_aggregator_config(self):
        with patch.object(services_ai_client, "get_settings", return_value=_settings(ai_dossier_url="", ai_dossier_api_key="")):
            with self.assertRaisesRegex(AIClientError, "AI-агрегатор не настроен"):
                services_ai_client.post_chat_completions({"model": "m", "messages": []}, timeout=5.0)

    def test_monster_chat_goes_through_aggregator_client(self):
        payload = {"choices": [{"message": {"content": "hello"}}]}
        with patch.object(services_llm_monster, "post_chat_completions", return_value=(payload, 7)) as post:
            content = services_llm_monster._chat_completions(
                messages=[{"role": "user", "content": "hi"}],
                model="qwen3-4b-instruct",
                timeout=90.0,
                temperature=0.15,
                top_p=0.8,
            )
        self.assertEqual(content, "hello")
        body, = post.call_args.args
        self.assertEqual(body["model"], "qwen3-4b-instruct")
        self.assertEqual(body["temperature"], 0.15)
        self.assertEqual(body["top_p"], 0.8)
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(post.call_args.kwargs["timeout"], 90.0)

    def test_monster_chat_maps_aggregator_errors_to_llm_error(self):
        with patch.object(services_llm_monster, "post_chat_completions", side_effect=AIClientError("AI-агрегатор недоступен")):
            with self.assertRaisesRegex(services_llm_monster.LLMError, "AI-агрегатор недоступен"):
                services_llm_monster._chat_completions(
                    messages=[{"role": "user", "content": "hi"}],
                    model="m", timeout=1.0, temperature=0.0, top_p=1.0,
                )

    def test_monster_cycle_refuses_to_run_without_aggregator(self):
        with patch.object(services_llm_monster, "get_settings", return_value=_settings(ai_dossier_url="", ai_dossier_api_key="")):
            with self.assertRaisesRegex(services_llm_monster.LLMError, "AI-агрегатор не настроен"):
                services_llm_monster.run_llm_cycle(None, "ASPER1", {}, mode="analyze")

    def test_monster_deployment_info_points_at_aggregator(self):
        with patch.object(services_llm_monster, "get_settings", return_value=_settings()):
            info = services_llm_monster.get_llm_deployment_info()
        self.assertEqual(info["base_url"], "https://ai.example/v1/chat/completions")
        self.assertEqual(info["model"], "qwen3-4b-instruct")
        self.assertTrue(info["api_key_set"])
        self.assertTrue(info["has_base_url"])
        self.assertEqual(info["via"], "ai-aggregator")
        self.assertNotIn("monster_llm_api_key", info)


if __name__ == "__main__":
    unittest.main()
