import unittest
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from config import Settings, snapshot_env_file
from models import AIPlayerDossier, AIPlayerDossierRun, AppSetting, LabelCategory, PlayerData, PlayerIP, PlayerLabel, PlayerRoleAction, PlayerSession
from services_ai_dossier import (
    DOSSIER_RULES_VERSION,
    build_kobold_request,
    build_dossier_analytics,
    build_moderation_packet,
    build_negative_rating_actions,
    build_rules_result,
    dossier_evidence_link,
    dossier_public_dict,
    dossier_run_public_dict,
    dossier_response_with_run,
    get_dossier_config,
    get_dossier_prompt,
    validate_dossier_ca_pem,
    normalize_dossier_result,
    normalize_ai_summary,
    parse_kobold_response,
    should_queue_dossier,
    select_active_player_ids,
    dossier_input_hash,
    sanitize_dossier_result,
    get_dossier_triage_map,
    select_active_player_ids,
    run_active_dossier_batch,
)
import services_ai_dossier
import services_ai_client


class AIDossierContractTests(unittest.TestCase):
    @staticmethod
    def _valid_response_content():
        return '{"summary":"Есть основания проверить сигнал."}'

    def test_kobold_response_accepts_json_fence(self):
        payload = {"choices": [{"message": {"content": f"```json\n{self._valid_response_content()}\n```"}}]}

        result = parse_kobold_response(payload)

        self.assertEqual(result["summary"], "Есть основания проверить сигнал.")

    def test_kobold_response_accepts_thinking_or_prose_before_json(self):
        payload = {"choices": [{"message": {"content": f"<think>Проверяю входные данные.</think>\n{self._valid_response_content()}"}}]}

        result = parse_kobold_response(payload)

        self.assertEqual(result["summary"], "Есть основания проверить сигнал.")

    def test_kobold_response_skips_braces_inside_reasoning(self):
        payload = {"choices": [{"message": {"content": f"<think>Пример структуры: {{\"not\": \"the answer\"}}</think>\n{self._valid_response_content()}"}}]}

        result = parse_kobold_response(payload)

        self.assertEqual(result["summary"], "Есть основания проверить сигнал.")

    def test_kobold_response_parse_error_contains_only_safe_size_diagnostic(self):
        secret_like_text = "Player private detail that must never be persisted"
        payload = {"choices": [{"message": {"content": secret_like_text}}]}

        with self.assertRaisesRegex(ValueError, r"KoboldCpp returned non-JSON content \(response chars: \d+\)") as context:
            parse_kobold_response(payload)

        self.assertNotIn(secret_like_text, str(context.exception))

    def test_normalize_result_requires_schema_and_does_not_invent_fields(self):
        result = normalize_dossier_result({
            "summary": "Опытный игрок; подтверждённых нарушений нет.",
            "facts": [{"text": "Сыграл 12 раундов", "source": "stats"}],
            "signals": [],
            "caveats": ["Логи доступны не полностью"],
            "confidence": "medium",
            "evidence": [{"kind": "stats", "id": "player:7"}],
            "unexpected": "drop me",
        })

        self.assertEqual(result["confidence"], "medium")
        self.assertNotIn("unexpected", result)
        self.assertEqual(result["facts"][0]["text"], "Сыграл 12 раундов")

    def test_normalize_result_rejects_missing_or_invalid_confidence(self):
        with self.assertRaises(ValueError):
            normalize_dossier_result({"summary": "x"})
        with self.assertRaises(ValueError):
            normalize_dossier_result({
                "summary": "x", "facts": [], "signals": [], "caveats": [],
                "confidence": "certain", "evidence": [],
            })

    def test_kobold_request_has_bearer_auth_and_json_only_contract(self):
        request = build_kobold_request(
            {"player": {"player_id": 7, "name": "Тест"}, "findings": [], "missing": ["logs"]},
            api_key="secret",
            model="qwen2.5-3b-instruct",
        )

        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertFalse(request["json"]["stream"])
        self.assertEqual(request["json"]["max_tokens"], 260)
        response_format = request["json"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(response_format["json_schema"]["name"], "player_dossier")
        self.assertEqual(response_format["json_schema"]["schema"]["type"], "object")
        self.assertEqual(response_format["json_schema"]["schema"]["required"], ["summary"])
        self.assertIn("player_id", request["json"]["messages"][1]["content"])
        self.assertIn("moderation packet", request["json"]["messages"][1]["content"])
        self.assertIn("NON-NEGOTIABLE OUTPUT CONTRACT", request["json"]["messages"][0]["content"])
        self.assertIn("Не добавляй новые факты", request["json"]["messages"][0]["content"])

    def test_dossier_analytics_is_a_moderator_focused_concentrate(self):
        analytics = build_dossier_analytics({
            "stats": {"killed_players": 2, "killed_monsters": 41},
            "sessions": [
                {"id": 10, "login_time": "2026-09-04T10:00:00", "logout_time": "2026-09-04T11:00:00", "duration": 3600, "avg_ping": 80},
                {"id": 11, "login_time": "2026-09-03T10:00:00", "logout_time": None, "duration": 0, "avg_ping": None},
            ],
            "punishments": [{"id": 5, "active": True}, {"id": 4, "active": False}],
            "notes": [{"id": 8}],
            "labels": [{"id": 9}],
            "traps": [{"id": 7, "active": True, "triggered_at": None}],
            "anticheat_events": [{"id": 6}],
            "logs": [{"id": 12}, {"id": 13}],
            "missing": ["server_logs"],
            "window": {"days": 30},
        })

        self.assertEqual(analytics["activity"]["sessions"], 2)
        self.assertEqual(analytics["activity"]["completed_sessions"], 1)
        self.assertEqual(analytics["activity"]["total_duration_seconds"], 3600)
        self.assertEqual(analytics["activity"]["average_duration_seconds"], 3600)
        self.assertEqual(analytics["moderation"]["punishments_total"], 2)
        self.assertEqual(analytics["moderation"]["active_punishments"], 1)
        self.assertEqual(analytics["moderation"]["notes"], 1)
        self.assertEqual(analytics["moderation"]["anticheat_events"], 1)
        self.assertEqual(analytics["moderation"]["logs"], 2)
        self.assertEqual(analytics["missing"], ["server_logs"])

    def test_moderation_packet_keeps_only_actionable_sources(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "stats": {"killed_monsters": 624},
            "sessions": [{"id": 1}],
            "anomalies": [
                {"id": 51, "category": "behavior", "subtype": "sauron_deviant", "score": 6,
                 "status": "new", "incidents": [{"label": "Убийств союзников: 4"}]},
                {"id": 52, "category": "twink", "subtype": "sauron_pair", "score": 3,
                 "status": "dismissed", "related_player": {"id": 8, "name": "Другой"}},
            ],
            "anticheat_events": [{"id": 61, "event_type": "speed", "reviewed": False}],
            "logs": [
                {"id": 71, "action": "command_denied", "details": "команда запрещена"},
                {"id": 72, "action": "picked_up", "details": "обычная игра"},
            ],
            "punishments": [{"id": 81, "type": "warning", "active": False, "reason": "старое"}],
            "labels": [{"id": 91, "name": "Наблюдение", "note": "проверить"}],
            "notes": [{"id": 101, "content": "служебная заметка"}],
            "shared_accounts": [],
            "missing": [],
        })

        self.assertEqual({finding["category"] for finding in packet["findings"]}, {"deviant_behavior", "anticheat"})
        self.assertNotIn("killed_monsters", json.dumps(packet, ensure_ascii=False))
        self.assertNotIn("picked_up", json.dumps(packet, ensure_ascii=False))
        self.assertEqual([item["kind"] for item in packet["context"]], ["punishment", "label", "note", "log_security"])

    def test_moderation_packet_detects_ip_ban_evasion_and_second_side_anomaly(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест", "known_ips": ["10.0.0.1"]},
            "anomalies": [{
                "id": 52, "category": "twink", "subtype": "sauron_pair", "score": 4,
                "status": "new", "related_player": {"id": 8, "name": "Связанный"},
                "signals": [{"type": "shared_ip", "label": "IP: 8.8.8.8"}],
            }],
            "shared_accounts": [{"player_id": 9, "name": "Забаненный", "active_ban": True}],
            "punishments": [], "labels": [], "notes": [], "anticheat_events": [], "logs": [], "missing": [],
        })

        self.assertEqual({finding["category"] for finding in packet["findings"]}, {"twink_link", "ban_evasion"})
        self.assertTrue(any(item["kind"] == "player" and item["id"] == 9 for item in packet["findings"][0]["evidence"] + packet["findings"][1]["evidence"]))
        self.assertNotIn("10.0.0.1", json.dumps(packet, ensure_ascii=False))

    def test_qwen_request_receives_untrusted_context_without_source_coverage(self):
        request = build_kobold_request({
            "player": {"player_id": 7, "name": "Тест"},
            "findings": [{"category": "twink_link", "severity": "medium", "title": "Связь", "details": "Основание", "reason": "Проверить", "next_step": "Сверить", "evidence": []}],
            "context": [{"kind": "label", "text": "Метка Дитриха"}],
            "coverage": {"tickets": {"checked": True, "matches": 1}},
            "missing": [],
        }, api_key="secret")

        model_input = request["json"]["messages"][1]["content"]

        self.assertIn("Связь", model_input)
        self.assertIn("Метка Дитриха", model_input)
        self.assertNotIn('"coverage"', model_input)

    def test_twink_finding_exposes_sauron_basis_without_raw_ip(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест", "known_ips": ["10.0.0.1"]},
            "anomalies": [{
                "id": 52, "category": "twink", "subtype": "sauron_pair", "score": 7,
                "status": "new", "related_player": {"id": 8, "name": "Связанный"},
                "signals": [{"type": "shared_ip"}, {"type": "similar_name"}],
            }],
            "shared_accounts": [], "punishments": [], "labels": [], "notes": [],
            "anticheat_events": [], "logs": [], "missing": [],
        })

        finding = packet["findings"][0]

        self.assertEqual(finding["basis"], ["точное совпадение IP", "похожий префикс ника"])
        self.assertNotIn("10.0.0.1", json.dumps(packet, ensure_ascii=False))

    def test_packet_reports_source_coverage_and_keeps_tickets_as_context(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [], "shared_accounts": [], "anticheat_events": [],
            "logs": [
                {"id": 401, "action": "picked_up", "details": "обычная игра"},
                {"id": 402, "action": "command_denied", "details": "команда запрещена"},
                {"id": 403, "action": "kick", "details": "Игрок исключен"},
            ],
            "tickets": [{"id": 301, "type": "player_report", "title": "Проверить жалобу", "status": "open"}],
            "punishments": [], "labels": [], "notes": [], "missing": [],
        })

        self.assertEqual(packet["coverage"]["tickets"], {"checked": True, "matches": 1, "status": "matched", "messages": 0})
        self.assertEqual(packet["coverage"]["logs"], {"checked": True, "matches": 3, "status": "matched"})
        self.assertEqual(packet["coverage"]["security_logs"], {"checked": True, "matches": 1, "status": "matched"})
        self.assertEqual(packet["coverage"]["sauron"], {"checked": True, "matches": 0, "status": "checked_empty"})
        self.assertEqual(packet["findings"], [])
        self.assertTrue(any(item["kind"] == "ticket" and item["source_id"] == 301 for item in packet["context"]))
        self.assertTrue(any(item["kind"] == "log" and item["source_id"] == 403 for item in packet["context"]))

    def test_teamkill_only_behavior_stays_review_and_never_urgent(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [{
                "id": 501, "category": "behavior", "subtype": "sauron_deviant", "score": 12,
                "status": "new", "incidents": [
                    {"type": "teamkill", "label": "Убийств союзников: 145", "score": 10},
                    {"type": "team_damage", "label": "Урон по союзникам: 5481 HP", "score": 8},
                    {"type": "self_death", "label": "Самоубийств: 7", "score": 6},
                ],
            }],
            "shared_accounts": [], "anticheat_events": [], "logs": [], "tickets": [],
            "punishments": [], "labels": [], "notes": [], "missing": [],
        })

        self.assertEqual(len(packet["findings"]), 1)
        self.assertEqual(packet["findings"][0]["severity"], "low")
        self.assertEqual(build_rules_result(packet)["triage"]["level"], "review")

    def test_nickname_only_sauron_signal_is_context_not_twink_finding(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [{
                "id": 502, "category": "twink", "subtype": "sauron_pair", "score": 2,
                "status": "new", "related_player": {"id": 8, "name": "Похожий"},
                "signals": [{"type": "similar_name", "label": "Похожее имя: Test..."}],
            }],
            "shared_accounts": [], "anticheat_events": [], "logs": [], "tickets": [],
            "punishments": [], "labels": [], "notes": [], "missing": [],
        })

        self.assertEqual(packet["findings"], [])
        self.assertTrue(any(item["kind"] == "sauron" for item in packet["context"]))

    def test_anomaly_is_used_when_player_is_related_side(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [{
                "id": 503, "category": "twink", "subtype": "ip_ban_evasion", "score": 12,
                "status": "new", "related_player": {"id": 8, "name": "Забаненный"},
                "signals": [{"type": "ip_exact_match_active_ban"}],
            }],
            "shared_accounts": [], "anticheat_events": [], "logs": [], "tickets": [],
            "punishments": [], "labels": [], "notes": [], "missing": [],
        })

        self.assertEqual([item["category"] for item in packet["findings"]], ["ban_evasion"])

    def test_repeated_command_denied_logs_are_a_finding_with_evidence(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [], "shared_accounts": [], "anticheat_events": [], "tickets": [],
            "punishments": [], "labels": [], "notes": [], "missing": [],
            "logs": [
                {"id": 601, "action": "command_denied", "details": "ban"},
                {"id": 602, "action": "command_denied", "details": "ban"},
            ],
        })

        finding = next(item for item in packet["findings"] if item["category"] == "log_security")
        self.assertEqual(finding["severity"], "medium")
        self.assertEqual({item["id"] for item in finding["evidence"]}, {601, 602})

    def test_stale_log_cache_is_marked_in_coverage(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [], "shared_accounts": [], "anticheat_events": [], "logs": [], "tickets": [],
            "punishments": [], "labels": [], "notes": [], "missing": [],
            "log_cache": {"ASPER1": {"last_refresh": "2020-01-01T00:00:00", "event_count": 1}},
        })

        self.assertEqual(packet["coverage"]["logs"]["status"], "stale")

    def test_shared_ip_rows_for_one_account_are_aggregated_with_public_details(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [], "anticheat_events": [], "logs": [], "tickets": [],
            "punishments": [], "labels": [], "notes": [], "missing": [],
            "shared_accounts": [
                {"player_id": 8, "name": "Связанный", "active_ban": False,
                 "shared_ip_count": 1, "shared_ips": [{
                     "ip": "8.8.8.8", "first_seen": "2026-08-01T10:00:00",
                     "last_seen": "2026-08-02T10:00:00", "overlap_count": 1,
                 }]},
                {"player_id": 8, "name": "Связанный", "active_ban": False,
                 "shared_ip_count": 1, "shared_ips": [{
                     "ip": "1.1.1.1", "first_seen": "2026-08-03T10:00:00",
                     "last_seen": "2026-08-04T10:00:00", "overlap_count": 1,
                 }]},
            ],
        })

        links = [item for item in packet["context"] if item["kind"] == "ip_link"]
        self.assertEqual(len(links), 1)
        self.assertIn("8.8.8.8", links[0]["text"])
        self.assertIn("1.1.1.1", links[0]["text"])

    def test_ticket_messages_are_context_and_explicitly_unverified(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [], "shared_accounts": [], "anticheat_events": [], "logs": [],
            "tickets": [{
                "id": 301, "type": "player_report", "title": "Проверить жалобу", "status": "open",
                "messages": [{"author": "Жалобщик", "created_at": "2026-09-04T10:00:00", "content": "Игрок ломал реактор"}],
            }],
            "punishments": [], "labels": [], "notes": [], "missing": [],
        })

        ticket_context = [item for item in packet["context"] if item["kind"] == "ticket"]
        self.assertEqual(len(ticket_context), 1)
        self.assertIn("Игрок ломал реактор", ticket_context[0]["text"])
        self.assertIn("неподтвержд", ticket_context[0]["text"].casefold())

    def test_qwen_packet_redacts_public_ip_even_when_moderator_packet_shows_it(self):
        request = build_kobold_request({
            "player": {"player_id": 7, "name": "Тест"},
            "findings": [{"category": "ban_evasion", "severity": "high", "title": "Связь",
                          "details": "Общий публичный IP 8.8.8.8 с забаненным аккаунтом.",
                          "reason": "Проверить", "next_step": "Сверить", "evidence": []}],
            "missing": [],
        }, api_key="secret")

        model_input = request["json"]["messages"][1]["content"]

        self.assertNotIn("8.8.8.8", model_input)
        self.assertIn("ban_evasion", model_input)

    def test_qwen_packet_contains_untrusted_moderation_context_without_exact_ip(self):
        packet = {
            "player": {"player_id": 7, "name": "Тест"},
            "findings": [{"category": "twink_link", "severity": "medium", "title": "Связь", "details": "Проверить", "reason": "Технический сигнал", "next_step": "Сверить", "basis": ["точное совпадение IP"]}],
            "context": [
                {"kind": "punishment", "text": "warning: оскорбления в чате"},
                {"kind": "label", "text": "Метка (не доказательство): токсичность"},
                {"kind": "note", "text": "Недоверенный текст заметки: проверить повторные жалобы"},
                {"kind": "ticket", "text": "Тикет: open — сообщения (неподтверждённые сведения): жалоба на спам"},
                {"kind": "ip_history", "text": "Глобальные IP: 8.8.8.8"},
            ],
            "missing": [],
        }

        model_packet = services_ai_dossier.build_qwen_packet(packet)
        model_context = json.dumps(model_packet["model_context"], ensure_ascii=False)

        self.assertIn("оскорбления", model_context)
        self.assertIn("токсичность", model_context)
        self.assertIn("полное содержимое доступно модератору", model_context)
        self.assertNotIn("жалоба на спам", model_context)
        self.assertNotIn("8.8.8.8", model_context)
        self.assertNotIn("source_id", model_context)

    def test_qwen_context_keeps_punishment_labels_notes_and_tickets_after_log_noise(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [], "shared_accounts": [], "anticheat_events": [],
            "logs": [{"id": index, "action": "kick", "details": "шум"} for index in range(20)],
            "tickets": [{"id": 301, "type": "player_report", "status": "open", "title": "Жалоба", "messages": [{"content": "сообщение жалобы"}]}],
            "punishments": [{"id": 401, "type": "warning", "status": "expired", "reason": "оскорбления", "recent": False}],
            "labels": [{"id": 501, "name": "токсичность", "note": "контекст метки"}],
            "notes": [{"id": 601, "content": "контекст заметки"}],
            "missing": [],
        })

        model_context = json.dumps(services_ai_dossier.build_qwen_packet(packet)["model_context"], ensure_ascii=False)

        self.assertIn("оскорбления", model_context)
        self.assertIn("токсичность", model_context)
        self.assertIn("контекст заметки", model_context)
        self.assertIn("полное содержимое доступно модератору", model_context)
        self.assertNotIn("сообщение жалобы", model_context)

    def test_old_rules_version_is_stale_even_when_schema_is_v2(self):
        self.assertFalse(services_ai_dossier.is_current_dossier_result({
            "schema_version": 2, "triage": {}, "rules_version": DOSSIER_RULES_VERSION - 1,
        }))
        self.assertFalse(services_ai_dossier.is_current_dossier_result({
            "schema_version": 2, "triage": {},
        }))

    def test_moderation_triage_map_exposes_only_current_actionable_reports(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossier.__table__])
        db = Session(engine)
        try:
            db.add_all([
                AIPlayerDossier(player_id=1, status="succeeded", result_json={"schema_version": 2, "rules_version": DOSSIER_RULES_VERSION, "triage": {"level": "urgent", "title": "Срочно"}, "summary": "Проверить", "findings": [], "context": [], "coverage": {}, "caveats": [], "confidence": "high"}),
                AIPlayerDossier(player_id=2, status="failed", result_json={"schema_version": 2, "rules_version": DOSSIER_RULES_VERSION, "triage": {"level": "review", "title": "Проверить"}, "summary": "Rules-only", "findings": [], "context": [], "coverage": {}, "caveats": [], "confidence": "high"}),
                AIPlayerDossier(player_id=3, status="succeeded", result_json={"schema_version": 2, "rules_version": DOSSIER_RULES_VERSION, "triage": {"level": "none", "title": "Нет"}, "summary": "Нет", "findings": [], "context": [], "coverage": {}, "caveats": [], "confidence": "high"}),
                AIPlayerDossier(player_id=4, status="succeeded", result_json={"schema_version": 2, "rules_version": DOSSIER_RULES_VERSION - 1, "triage": {"level": "urgent", "title": "Старое"}, "summary": "Старое", "findings": [], "context": [], "coverage": {}, "caveats": [], "confidence": "high"}),
            ])
            db.commit()

            markers = get_dossier_triage_map(db, [1, 2, 3, 4, 5])

            self.assertEqual(markers[1]["level"], "urgent")
            self.assertEqual(markers[2]["level"], "review")
            self.assertNotIn(3, markers)
            self.assertNotIn(4, markers)
            self.assertNotIn(5, markers)
        finally:
            db.close()
            engine.dispose()

    def test_non_superuser_cannot_request_manual_dossier_generation(self):
        from fastapi import HTTPException
        from services_ai_dossier import require_dossier_generation

        with self.assertRaises(HTTPException) as error:
            require_dossier_generation(SimpleNamespace(is_superuser=False))

        self.assertEqual(error.exception.status_code, 403)

    def test_profile_ip_context_excludes_bogon_and_keeps_global_ip(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест", "last_ip": "172.18.0.3", "known_ips": ["172.18.0.3", "8.8.8.8"]},
            "anomalies": [], "shared_accounts": [], "anticheat_events": [], "logs": [], "tickets": [],
            "punishments": [], "labels": [], "notes": [], "missing": [],
        })

        text = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("172.18.0.3", text)
        self.assertIn("8.8.8.8", text)

    def test_payload_loads_ticket_messages_and_log_cache_metadata(self):
        from models import Ticket, TicketMessage

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            player = PlayerData(player_id=7, player_steamid="76561198000000007", player_last_time_name="Тест")
            ticket = Ticket(id=301, ticket_type="player_report", title="Проверить", status="open", creator_discord_id="123", player_id=7, created_at=datetime(2026, 9, 4, 10, 0))
            message = TicketMessage(id=901, ticket_id=301, author_display_name="Жалобщик", author_discord_id="123", content="Игрок ломал реактор", created_at=datetime(2026, 9, 4, 10, 1))
            db.add_all([
                player, ticket, message,
                PlayerRoleAction(player_id=7, role="security", action_type="player_killed", value=2),
                PlayerRoleAction(player_id=7, role="security", action_type="shoot_weapon", value=100),
            ])
            db.commit()
            fake_event = SimpleNamespace(row_id=902, timestamp=datetime(2026, 9, 4, 10, 2), player_name="Тест", action_type="kick", details="кик", server_name="ASPER1", source_file="server.log")
            with (
                patch("services_logs.search_logs", return_value=SimpleNamespace(events=[fake_event])),
                patch("services_logs.get_log_stats", return_value={"ASPER1": {"last_refresh": "2026-09-04T10:05:00", "event_count": 10}}),
            ):
                payload = services_ai_dossier.build_player_dossier_payload(db, 7, datetime(2026, 9, 4, 12, 0))

            self.assertEqual(payload["tickets"][0]["messages"][0]["content"], "Игрок ломал реактор")
            self.assertEqual(payload["log_cache"]["ASPER1"]["event_count"], 10)
            self.assertEqual(payload["logs"][0]["action"], "kick")
            self.assertEqual(payload["rating_negative_actions"][0]["action_type"], "player_killed")
            self.assertEqual(payload["rating_negative_actions"][0]["count"], 1)
        finally:
            db.close()
            engine.dispose()

    def test_shared_ai_payload_excludes_secret_labels(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            player = PlayerData(player_id=7, player_steamid="76561198000000007", player_last_time_name="Тест")
            public = LabelCategory(name="Публичная")
            secret = LabelCategory(name="Закрытая", is_secret=True)
            db.add_all([player, public, secret])
            db.flush()
            db.add_all([
                PlayerLabel(player_id=7, category_id=public.id),
                PlayerLabel(player_id=7, category_id=secret.id),
            ])
            db.commit()
            with (
                patch("services_logs.search_logs", return_value=SimpleNamespace(events=[])),
                patch("services_logs.get_log_stats", return_value={}),
            ):
                payload = services_ai_dossier.build_player_dossier_payload(db, 7, datetime(2026, 9, 4, 12, 0))

            self.assertEqual([row["name"] for row in payload["labels"]], ["Публичная"])
            self.assertNotIn("Закрытая", json.dumps(payload, ensure_ascii=False))
        finally:
            db.close()
            engine.dispose()

    def test_unavailable_log_cache_is_explicit_in_coverage_and_caveats(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "anomalies": [], "shared_accounts": [], "anticheat_events": [], "logs": [], "tickets": [],
            "punishments": [], "labels": [], "notes": [], "missing": ["server_logs"],
        })

        result = build_rules_result(packet)

        self.assertEqual(packet["coverage"]["logs"]["status"], "unavailable")
        self.assertEqual(packet["coverage"]["security_logs"]["status"], "unavailable")
        self.assertTrue(any("Серверные логи недоступны" in item for item in result["caveats"]))

    def test_dismissed_anomalies_and_ordinary_gameplay_create_no_findings(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "stats": {"killed_monsters": 624}, "sessions": [{"id": 1}],
            "anomalies": [{"id": 1, "category": "behavior", "status": "dismissed"}],
            "anticheat_events": [], "logs": [{"id": 1, "action": "picked_up"}],
            "punishments": [{"id": 2, "type": "warning", "status": "expired", "recent": False}],
            "labels": [], "notes": [], "shared_accounts": [], "missing": [],
        })

        self.assertEqual(packet["findings"], [])
        self.assertFalse(build_rules_result(packet)["triage"]["level"] != "none")

    def test_rules_result_uses_aggressive_independent_category_priority(self):
        packet = {
            "player": {"player_id": 7, "name": "Тест"},
            "findings": [
                {"category": "anticheat", "severity": "medium", "title": "Античит", "details": "A", "reason": "B", "next_step": "C", "evidence": [{"kind": "anticheat", "id": 1}]},
                {"category": "log_security", "severity": "medium", "title": "Security-лог", "details": "A", "reason": "B", "next_step": "C", "evidence": [{"kind": "log", "id": 2}]},
            ],
            "context": [],
            "missing": [],
        }

        result = build_rules_result(packet)

        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["triage"]["level"], "urgent")
        self.assertEqual(len(result["findings"]), 2)
        self.assertEqual(result["summary_source"], "rules")

    def test_qwen_summary_cannot_add_findings_or_evidence(self):
        packet = {
            "player": {"player_id": 7, "name": "Тест"},
            "findings": [{"category": "anticheat", "severity": "medium", "title": "Античит", "details": "A", "reason": "B", "next_step": "C", "evidence": [{"kind": "anticheat", "id": 1}]}],
            "context": [],
            "missing": [],
        }
        rules = build_rules_result(packet)
        summary = normalize_ai_summary({
            "summary": "Найдено событие античита; проверить запись.",
            "caveats": ["Ответ модели ограничен готовыми находками."],
            "findings": [{"category": "invented", "details": "не добавлять"}],
            "evidence": [{"kind": "log", "id": 999}],
        })

        merged = services_ai_dossier.merge_ai_summary(rules, summary)

        self.assertEqual(len(merged["findings"]), 1)
        self.assertEqual(merged["findings"][0]["category"], "anticheat")
        self.assertEqual(merged["summary_source"], "qwen")

    def test_generic_or_english_qwen_summary_is_replaced_by_rules_summary(self):
        rules = build_rules_result({
            "player": {"player_id": 7, "name": "Тест"},
            "findings": [{"category": "anticheat", "severity": "medium", "title": "Непроверенное античит-событие", "details": "A", "reason": "B", "next_step": "C", "evidence": []}],
            "context": [], "missing": [],
        })

        merged = services_ai_dossier.merge_ai_summary(rules, {"summary": "This JSON object contains player data.", "caveats": []})

        self.assertEqual(merged["summary_source"], "rules")
        self.assertEqual(merged["summary"], rules["summary"])

    def test_old_result_is_not_current_dossier(self):
        self.assertFalse(services_ai_dossier.is_current_dossier_result({"summary": "старый", "facts": []}))
        self.assertTrue(services_ai_dossier.is_current_dossier_result({"schema_version": 2, "rules_version": DOSSIER_RULES_VERSION, "triage": {}}))

    def test_stale_result_is_hidden_but_not_deleted_from_report(self):
        report = SimpleNamespace(
            status="succeeded", result_json={"summary": "v1", "facts": []}, summary_text="v1",
            input_hash="old", model="qwen", requested_at=None, started_at=None,
            generated_at=None, finished_at=None, error_message=None, requested_by_admin_id=None,
            player_id=7,
        )

        public = dossier_public_dict(report)

        self.assertTrue(public["stale"])
        self.assertIsNone(public["result"])
        self.assertEqual(report.result_json["summary"], "v1")

    def test_should_queue_only_when_changed_or_stale_unless_forced(self):
        now = datetime(2026, 9, 4, 12, 0)
        successful = AIPlayerDossier(
            status="succeeded",
            input_hash="same",
            result_json={"schema_version": 2, "rules_version": DOSSIER_RULES_VERSION},
            generated_at=now - timedelta(days=2),
        )
        self.assertFalse(should_queue_dossier(successful, "same", False, now, 7))
        self.assertTrue(should_queue_dossier(successful, "new", False, now, 7))
        self.assertTrue(should_queue_dossier(successful, "same", False, now, 1))
        self.assertTrue(should_queue_dossier(successful, "same", True, now, 365))

    def test_input_hash_ignores_only_the_moving_window_end(self):
        first = {"profile": {"player_id": 7}, "window": {"from": "2026-08-01", "to": "2026-09-04T12:00:00"}}
        second = {"profile": {"player_id": 7}, "window": {"from": "2026-08-01", "to": "2026-09-04T13:00:00"}}
        self.assertEqual(dossier_input_hash(first), dossier_input_hash(second))

    def test_sanitize_hides_identifiers_and_drops_unknown_evidence(self):
        payload = {"profile": {"player_id": 7, "steamid": "7656119", "discord_id": "123456789", "last_ip": "10.0.0.1", "known_ips": []}, "logs": [{"id": 42}]}
        result = sanitize_dossier_result({
            "summary": "IP 10.0.0.1 найден",
            "facts": [{"text": "Steam 7656119", "source": "profile"}],
            "signals": [], "caveats": [], "confidence": "low",
            "evidence": [{"kind": "log", "id": 42}, {"kind": "log", "id": 999}],
        }, payload)
        self.assertNotIn("10.0.0.1", result["summary"])
        self.assertEqual(result["evidence"], [{"kind": "log", "id": 42}])

    def test_sanitize_removes_duplicate_facts_and_signals(self):
        result = sanitize_dossier_result({
            "summary": "Игрок активен.",
            "facts": [
                {"text": "Игрок активен.", "source": "session"},
                {"text": "Игрок активен.", "source": "session"},
            ],
            "signals": [
                {"text": "Проверить наказание.", "reason": "Есть активное наказание."},
                {"text": "Проверить наказание.", "reason": "Есть активное наказание."},
            ],
            "caveats": [],
            "confidence": "low",
            "evidence": [],
        }, {"profile": {"player_id": 7}, "sessions": [{"id": 9}], "punishments": [{"id": 1}]})

        self.assertEqual(sum(item["text"] == "Игрок активен." for item in result["facts"]), 1)
        self.assertEqual(len(result["signals"]), 1)

    def test_sanitize_prefers_concrete_aggregates_and_drops_activity_only_signals(self):
        payload = {
            "profile": {"player_id": 7, "name": "Тест"},
            "stats": {"killed_players": 0, "killed_monsters": 12},
            "sessions": [
                {"login_time": "2026-09-04T10:00:00", "logout_time": "2026-09-04T11:00:00", "duration": 3600},
                {"login_time": "2026-09-03T10:00:00", "logout_time": "2026-09-03T10:30:00", "duration": 1800},
            ],
            "punishments": [], "notes": [], "labels": [], "traps": [],
            "anticheat_events": [], "logs": [], "missing": [],
            "window": {"days": 30},
        }
        result = sanitize_dossier_result({
            "summary": "Вот краткий обзор данных.",
            "facts": [{"text": "Данные содержат список сессий игрока.", "source": "profile"}],
            "signals": [{"text": "Проверить активность игрока.", "reason": "Есть сессии."}],
            "caveats": [],
            "confidence": "high",
            "evidence": [],
        }, payload)

        self.assertTrue(any("2 сесс" in item["text"] for item in result["facts"]))
        self.assertTrue(any("12" in item["text"] for item in result["facts"]))
        self.assertIn("2 сесс", result["summary"])
        self.assertEqual(result["signals"], [])

    def test_sanitize_drops_model_fact_when_declared_source_has_no_records(self):
        payload = {
            "profile": {"player_id": 7, "name": "Тест"},
            "stats": None,
            "sessions": [], "punishments": [], "notes": [], "labels": [],
            "traps": [], "anticheat_events": [], "logs": [], "missing": [],
            "window": {"days": 30},
        }
        result = sanitize_dossier_result({
            "summary": "Нарушений не зафиксировано.",
            "facts": [{"text": "Игрок получил бан за нарушение правил.", "source": "punishment"}],
            "signals": [], "caveats": [], "confidence": "low", "evidence": [],
        }, payload)

        self.assertFalse(any("получил бан" in item["text"] for item in result["facts"]))

    def test_sanitize_drops_signal_without_moderation_reason(self):
        payload = {
            "profile": {"player_id": 7, "name": "Тест"},
            "stats": None,
            "sessions": [], "punishments": [], "notes": [], "labels": [],
            "traps": [], "anticheat_events": [], "logs": [{"id": 42}], "missing": [],
            "window": {"days": 30},
        }
        result = sanitize_dossier_result({
            "summary": "Есть данные для проверки.", "facts": [],
            "signals": [{"text": "Игрок подозрительный.", "reason": "В профиле есть логи."}],
            "caveats": [], "confidence": "low", "evidence": [],
        }, payload)

        self.assertEqual(result["signals"], [])

    def test_baseline_duration_is_readable_for_moderators(self):
        payload = {
            "profile": {"player_id": 7, "name": "Тест"},
            "stats": None,
            "sessions": [{"login_time": "2026-09-04T10:00:00", "duration": 5400}],
            "punishments": [], "notes": [], "labels": [], "traps": [],
            "anticheat_events": [], "logs": [], "missing": [],
            "window": {"days": 30},
        }

        result = sanitize_dossier_result({
            "summary": "Короткая сводка.", "facts": [], "signals": [],
            "caveats": [], "confidence": "high", "evidence": [],
        }, payload)

        self.assertTrue(any("1 ч 30 мин" in item["text"] for item in result["facts"]))

    def test_sanitize_replaces_vague_summary_with_concrete_facts(self):
        payload = {
            "profile": {"player_id": 7, "name": "Тест"},
            "stats": {"killed_players": 2},
            "sessions": [{"login_time": "2026-09-04T10:00:00", "duration": 3600}],
            "punishments": [], "notes": [], "labels": [], "traps": [],
            "anticheat_events": [], "logs": [], "missing": [],
            "window": {"days": 30},
        }

        result = sanitize_dossier_result({
            "summary": "Игрок активен.", "facts": [], "signals": [],
            "caveats": [], "confidence": "medium", "evidence": [],
        }, payload)

        self.assertIn("сесс", result["summary"])
        self.assertIn("убийств игроков", result["summary"].casefold())

    def test_active_player_selection_is_recent_login_desc_then_id(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerSession.__table__])
        db = Session(engine)
        try:
            db.add_all([
                PlayerData(player_id=2, player_steamid="2", player_last_time_name="two", last_login_time=datetime(2026, 9, 3)),
                PlayerData(player_id=1, player_steamid="1", player_last_time_name="one", last_login_time=datetime(2026, 9, 3)),
                PlayerData(player_id=3, player_steamid="3", player_last_time_name="old", last_login_time=datetime(2026, 7, 1)),
            ])
            db.commit()
            self.assertEqual(select_active_player_ids(db, datetime(2026, 9, 4), 10), [1, 2])
        finally:
            db.close()
            engine.dispose()

    def test_active_player_selection_without_limit_returns_every_recent_player(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerSession.__table__])
        db = Session(engine)
        try:
            db.add_all([
                PlayerData(player_id=1, player_steamid="1", player_last_time_name="one", last_login_time=datetime(2026, 9, 3)),
                PlayerData(player_id=2, player_steamid="2", player_last_time_name="two", last_login_time=datetime(2026, 9, 3)),
                PlayerData(player_id=3, player_steamid="3", player_last_time_name="old", last_login_time=datetime(2026, 7, 1)),
                PlayerData(player_id=4, player_steamid="4", player_last_time_name="four", last_login_time=datetime(2026, 9, 2)),
            ])
            db.commit()

            self.assertEqual(select_active_player_ids(db, datetime(2026, 9, 4), None), [1, 2, 4])
        finally:
            db.close()
            engine.dispose()

    def test_analysis_ip_policy_combines_manual_and_mass_player_exclusions(self):
        from services_ip_filter import build_analysis_ip_policy

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AppSetting.__table__, PlayerData.__table__, PlayerIP.__table__])
        db = Session(engine)
        try:
            now = datetime(2026, 9, 4, 12, 0)
            players = [PlayerData(player_id=index, player_steamid=str(index), player_last_time_name=f"p{index}") for index in range(1, 6)]
            db.add_all(players)
            db.flush()
            db.add(AppSetting(key="ai_dossier_ignored_ips", value='["9.9.9.0/24"]'))
            db.add_all([
                PlayerIP(player_id=player.player_id, ip="8.8.8.8", subnet="8.8.8.0/24", first_seen=now, last_seen=now)
                for player in players
            ])
            db.commit()

            policy = build_analysis_ip_policy(db, now)

            self.assertTrue(policy.is_ignored("9.9.9.7"))
            self.assertTrue(policy.is_ignored("8.8.8.8"))
            self.assertFalse(policy.is_ignored("1.1.1.1"))
            self.assertEqual(policy.auto_ignored_ips, {"8.8.8.8"})
        finally:
            db.close()
            engine.dispose()

    def test_payload_excludes_mass_ip_from_dossier_links(self):
        from models import PlayerIP

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            now = datetime(2026, 9, 4, 12, 0)
            target = PlayerData(player_id=7, player_steamid="7", player_last_time_name="target")
            others = [PlayerData(player_id=index, player_steamid=str(index), player_last_time_name=f"p{index}") for index in range(8, 12)]
            db.add_all([target, *others])
            db.flush()
            db.add_all([
                PlayerIP(player_id=player.player_id, ip="8.8.8.8", subnet="8.8.8.0/24", first_seen=now, last_seen=now)
                for player in [target, *others]
            ])
            db.commit()

            with patch("services_logs.search_logs", return_value=SimpleNamespace(events=[])), patch("services_logs.get_log_stats", return_value={}):
                payload = services_ai_dossier.build_player_dossier_payload(db, 7, now)

            self.assertEqual(payload["shared_accounts"], [])
            self.assertEqual(payload["profile"]["known_ips"], [])
            self.assertEqual(payload["ip_filter"]["auto_count"], 1)
        finally:
            db.close()
            engine.dispose()

    def test_manual_active_window_batch_uses_all_eligible_players(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AppSetting.__table__, PlayerData.__table__, PlayerSession.__table__])
        db = Session(engine)
        try:
            now = datetime(2026, 9, 4, 12, 0)
            db.add_all([
                PlayerData(player_id=1, player_steamid="1", player_last_time_name="one", last_login_time=datetime(2026, 9, 3)),
                PlayerData(player_id=2, player_steamid="2", player_last_time_name="two", last_login_time=datetime(2026, 9, 2)),
                PlayerData(player_id=3, player_steamid="3", player_last_time_name="old", last_login_time=datetime(2026, 7, 1)),
            ])
            db.commit()

            calls = []
            with patch("database.SessionLocal", return_value=db), patch.object(
                services_ai_dossier, "enqueue_dossier_if_needed",
                side_effect=lambda _db, player_id, admin_id, requested_at, source: calls.append((player_id, admin_id, requested_at, source)) or (None, True),
            ):
                result = run_active_dossier_batch(99)

            self.assertEqual(result, {"selected": 2, "queued": 2, "skipped": 0})
            self.assertEqual([call[0] for call in calls], [1, 2])
            self.assertTrue(all(call[1] == 99 and call[3] == "manual_batch" for call in calls))
        finally:
            db.close()
            engine.dispose()

    def test_enqueue_is_idempotent_for_running_player(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossier.__table__, AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            first, created = services_ai_dossier.enqueue_dossier(db, 7, 11, force=True)
            second, created_again = services_ai_dossier.enqueue_dossier(db, 7, 12, force=True)
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.requested_by_admin_id, 11)
            self.assertEqual(db.query(AIPlayerDossierRun).count(), 1)
            self.assertEqual(db.query(AIPlayerDossierRun).one().source, "manual")
        finally:
            db.close()
            engine.dispose()

    def test_successful_generation_keeps_bounded_raw_response_for_superuser_debug(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossier.__table__, AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            db.add(AIPlayerDossier(player_id=7, status="queued"))
            db.commit()
            raw_response = '{"summary":"Готово: активных сигналов не найдено.","caveats":[]}'
            fake_response = SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "choices": [{"message": {"content": raw_response}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                },
            )
            fake_settings = SimpleNamespace(
                ai_dossier_url="https://qwen.test/v1/chat/completions",
                ai_dossier_api_key="secret",
                ai_dossier_model="qwen",
                ai_dossier_tls_ca_file="",
                ai_dossier_timeout_seconds=1,
                ai_dossier_active_days=30,
                ai_dossier_max_log_events=10,
                ai_dossier_max_input_chars=30000,
            )
            with (
                patch("database.SessionLocal", return_value=db),
                patch.object(services_ai_dossier, "build_player_dossier_payload", return_value={"player_id": 7, "profile": {"player_id": 7}}),
                patch.object(services_ai_dossier, "get_settings", return_value=fake_settings),
                patch.object(services_ai_client, "get_settings", return_value=fake_settings),
                patch("httpx.Client.post", return_value=fake_response),
            ):
                result = services_ai_dossier.process_one_dossier()

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(db.query(AIPlayerDossierRun).one().raw_response, raw_response)
        finally:
            db.close()
            engine.dispose()

    def test_generation_failure_keeps_previous_successful_result(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossier.__table__, AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            report = AIPlayerDossier(
                player_id=7,
                status="queued",
                result_json={"schema_version": 2, "rules_version": DOSSIER_RULES_VERSION, "triage": {"level": "none", "title": "Старый", "reason": ""}, "summary": "старый", "findings": [], "context": [], "caveats": [], "confidence": "high"},
                summary_text="старый",
                input_hash="old",
                generated_at=datetime(2026, 9, 1),
            )
            db.add(report)
            db.commit()

            fake_settings = SimpleNamespace(
                ai_dossier_url="https://qwen.test/v1/chat/completions",
                ai_dossier_api_key="secret",
                ai_dossier_model="qwen",
                ai_dossier_tls_ca_file="",
                ai_dossier_timeout_seconds=1,
                ai_dossier_active_days=30,
                ai_dossier_max_log_events=10,
                ai_dossier_max_input_chars=30000,
            )
            with (
                patch("database.SessionLocal", return_value=db),
                patch.object(services_ai_dossier, "build_player_dossier_payload", return_value={"player_id": 7}),
                patch.object(services_ai_dossier, "get_settings", return_value=fake_settings),
                patch.object(services_ai_client, "get_settings", return_value=fake_settings),
                patch("httpx.Client.post", side_effect=RuntimeError("qwen offline")),
            ):
                result = services_ai_dossier.process_one_dossier()

            self.assertEqual(result["status"], "failed")
            db.expire_all()
            stored = db.query(AIPlayerDossier).one()
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.summary_text, "старый")
            self.assertEqual(stored.result_json["summary"], "старый")
            self.assertIn("qwen offline", stored.error_message)
        finally:
            db.close()
            engine.dispose()

    def test_evidence_links_are_derived_from_whitelisted_source_ids(self):
        self.assertEqual(dossier_evidence_link("log", 42, 7), "/logs?event_id=42")
        self.assertEqual(dossier_evidence_link("anomaly", 52, 7), "/moderation?tab=anomalies")
        self.assertEqual(dossier_evidence_link("note", 3, 7), "/players/7?tab=notes&source_id=3")
        self.assertEqual(dossier_evidence_link("ticket", 301, 7), "/tickets/301")
        self.assertIsNone(dossier_evidence_link("note", 999, None))

    def test_run_public_dict_contains_metadata_but_no_response_body(self):
        run = AIPlayerDossierRun(
            id=4, player_id=7, source="manual", status="succeeded", model="qwen",
            latency_ms=1200, prompt_tokens=100, completion_tokens=20,
            raw_response="private model output",
        )
        public = dossier_run_public_dict(run, player_name="Alice")
        self.assertEqual(public["player_name"], "Alice")
        self.assertEqual(public["latency_ms"], 1200)
        self.assertNotIn("result", public)
        self.assertNotIn("raw_response", public)
        privileged = dossier_run_public_dict(run, player_name="Alice", include_raw=True)
        self.assertEqual(privileged["raw_response"], "private model output")

    def test_failed_generation_keeps_bounded_raw_response_for_superuser_debug(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossier.__table__, AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            report = AIPlayerDossier(player_id=7, status="queued")
            db.add(report)
            db.commit()

            raw_response = "not-json model output" + ("x" * 15000)
            fake_response = SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "choices": [{"message": {"content": raw_response}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 812, "completion_tokens": 650},
                },
            )
            fake_settings = SimpleNamespace(
                ai_dossier_url="https://qwen.test/v1/chat/completions",
                ai_dossier_api_key="secret",
                ai_dossier_model="qwen",
                ai_dossier_tls_ca_file="",
                ai_dossier_timeout_seconds=1,
                ai_dossier_active_days=30,
                ai_dossier_max_log_events=10,
                ai_dossier_max_input_chars=30000,
            )
            with (
                patch("database.SessionLocal", return_value=db),
                patch.object(services_ai_dossier, "build_player_dossier_payload", return_value={"player_id": 7}),
                patch.object(services_ai_dossier, "get_settings", return_value=fake_settings),
                patch.object(services_ai_client, "get_settings", return_value=fake_settings),
                patch("httpx.Client.post", return_value=fake_response),
            ):
                result = services_ai_dossier.process_one_dossier()

            self.assertEqual(result["status"], "failed")
            stored = db.query(AIPlayerDossierRun).one()
            self.assertEqual(stored.raw_response, raw_response[:12000])
            self.assertEqual(stored.prompt_tokens, 812)
            self.assertEqual(stored.completion_tokens, 650)
            self.assertIn("finish_reason: length", stored.error_message)
        finally:
            db.close()
            engine.dispose()

    def test_generation_failure_replaces_legacy_result_with_rules_only_v2(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossier.__table__, AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            db.add(AIPlayerDossier(
                player_id=7, status="queued",
                result_json={"summary": "legacy", "facts": [], "signals": []}, summary_text="legacy",
            ))
            db.commit()
            fake_settings = SimpleNamespace(
                ai_dossier_url="https://qwen.test/v1/chat/completions", ai_dossier_api_key="secret",
                ai_dossier_model="qwen", ai_dossier_tls_ca_file="", ai_dossier_timeout_seconds=1,
                ai_dossier_active_days=30, ai_dossier_max_log_events=10, ai_dossier_max_input_chars=30000,
            )
            with (
                patch("database.SessionLocal", return_value=db),
                patch.object(services_ai_dossier, "build_player_dossier_payload", return_value={"player_id": 7}),
                patch.object(services_ai_dossier, "get_settings", return_value=fake_settings),
                patch.object(services_ai_client, "get_settings", return_value=fake_settings),
                patch("httpx.Client.post", side_effect=RuntimeError("qwen offline")),
            ):
                services_ai_dossier.process_one_dossier()

            stored = db.query(AIPlayerDossier).one()
            self.assertEqual(stored.status, "failed")
            self.assertTrue(services_ai_dossier.is_current_dossier_result(stored.result_json))
            self.assertEqual(stored.result_json["summary_source"], "rules")
            self.assertNotEqual(stored.summary_text, "legacy")
        finally:
            db.close()
            engine.dispose()

    def test_empty_dossier_response_does_not_require_a_report_row(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AIPlayerDossierRun.__table__])
        db = Session(engine)
        try:
            response = dossier_response_with_run(db, None)
            self.assertEqual(response, {"status": "none", "result": None, "stale": False, "run": None})
        finally:
            db.close()
            engine.dispose()

    def test_runtime_config_reads_safe_operational_overrides(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AppSetting.__table__])
        db = Session(engine)
        try:
            db.add_all([
                AppSetting(key="ai_dossier_schedule_hour", value="6"),
                AppSetting(key="ai_dossier_active_days", value="14"),
                AppSetting(key="ai_dossier_max_log_events", value="40"),
            ])
            db.commit()
            with patch.object(services_ai_dossier, "get_settings", return_value=SimpleNamespace(
                ai_dossier_url="", ai_dossier_api_key="", ai_dossier_model="qwen",
                ai_dossier_daily_limit=10, ai_dossier_schedule_hour=4,
                ai_dossier_active_days=30, ai_dossier_max_log_events=120,
                ai_dossier_timeout_seconds=180, ai_dossier_tls_ca_file="",
                ai_dossier_max_input_chars=30000,
            )):
                config = get_dossier_config(db)
            self.assertEqual(config["schedule_hour"], 6)
            self.assertEqual(config["active_days"], 14)
            self.assertEqual(config["max_log_events"], 40)
        finally:
            db.close()
            engine.dispose()

    def test_prompt_can_be_overridden_by_superuser_setting(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AppSetting.__table__])
        db = Session(engine)
        try:
            db.add(AppSetting(key="ai_dossier_prompt", value="custom prompt"))
            db.commit()
            self.assertEqual(get_dossier_prompt(db), "custom prompt")
        finally:
            db.close()
            engine.dispose()

    def test_ca_upload_accepts_certificate_and_rejects_private_key(self):
        self.assertTrue(validate_dossier_ca_pem(
            b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
        ))
        with self.assertRaises(ValueError):
            validate_dossier_ca_pem(b"-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n")

    def test_empty_numeric_env_values_fall_back_to_defaults(self):
        settings = Settings(
            rate_limit_requests="", rate_limit_window="",
            sftp_server_1_port="", sftp_server_2_port="",
        )
        self.assertEqual(settings.rate_limit_requests, 100)
        self.assertEqual(settings.rate_limit_window, 60)
        self.assertEqual(settings.sftp_server_1_port, 22)
        self.assertEqual(settings.sftp_server_2_port, 22)

    def test_env_snapshot_is_private_and_contains_previous_content(self):
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("SECRET_VALUE=do-not-print\n", encoding="utf-8")
            snapshot = snapshot_env_file(env_path)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.read_text(encoding="utf-8"), "SECRET_VALUE=do-not-print\n")
            if os.name != "nt":
                self.assertEqual(snapshot.stat().st_mode & 0o077, 0)

    def test_negative_rating_actions_are_aggregated_with_all_time_scope(self):
        import rating_constants

        with patch.object(rating_constants, "WEIGHT_MATRIX", {
            "security": {"player_killed": -100.0, "self_death": -50.0, "shoot_weapon": 0.5},
        }):
            actions = build_negative_rating_actions([
                {"role": "security", "action_type": "player_killed", "value": 2},
                {"role": "security", "action_type": "player_killed", "value": 3},
                {"role": "security", "action_type": "self_death", "value": 1},
                {"role": "security", "action_type": "shoot_weapon", "value": 999},
                {"role": "security", "action_type": "player_killed", "value": "bad"},
                {"role": "security", "action_type": "player_killed", "value": -4},
            ])

        self.assertEqual(actions, [
            {"role": "security", "action_type": "player_killed", "count": 2,
             "total_value": 5.0, "penalty_points": 500.0, "all_time": True},
            {"role": "security", "action_type": "self_death", "count": 1,
             "total_value": 1.0, "penalty_points": 50.0, "all_time": True},
        ])

    def test_negative_rating_context_is_not_an_urgent_finding(self):
        packet = build_moderation_packet({
            "profile": {"player_id": 7, "name": "Тест"},
            "rating_negative_actions": [{
                "role": "security", "action_type": "player_killed", "count": 2,
                "total_value": 5.0, "penalty_points": 500.0, "all_time": True,
            }],
            "anomalies": [], "shared_accounts": [], "anticheat_events": [], "logs": [],
            "tickets": [], "punishments": [], "labels": [], "notes": [], "missing": [],
        })

        self.assertEqual(packet["findings"], [])
        self.assertEqual(packet["coverage"]["rating"], {
            "checked": True, "matches": 1, "status": "matched", "all_time": True,
        })
        rating_context = next(item for item in packet["context"] if item["kind"] == "rating_negative")
        self.assertIn("за всю историю", rating_context["text"])
        self.assertIn("убийство игрока/союзника", rating_context["text"])

    def test_qwen_packet_contains_rating_context_without_rules_owned_fields(self):
        packet = {
            "player": {"player_id": 7, "name": "Тест"},
            "findings": [],
            "context": [{"kind": "rating_negative", "text": "Отрицательные действия рейтинга за всю историю: security/player_killed ×2, штраф 500."}],
            "coverage": {"rating": {"checked": True, "matches": 1, "status": "matched", "all_time": True}},
            "missing": [],
        }

        model_packet = services_ai_dossier.build_qwen_packet(packet)

        self.assertIn("rating_negative", json.dumps(model_packet, ensure_ascii=False))
        self.assertNotIn("coverage", model_packet)
        self.assertNotIn("all_time", json.dumps(model_packet, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
