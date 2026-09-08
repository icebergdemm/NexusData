import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from services_server_restart import (
    PterodactylClient,
    PterodactylError,
    build_msg_box_command,
    conditions_satisfied,
    evaluate_schedule_tick,
    iso_utc,
    next_daily_run_utc,
    validate_daily_time,
)


class ServerRestartScheduleTests(unittest.TestCase):
    def test_next_daily_run_uses_moscow_time_and_rolls_to_next_day(self):
        now = datetime(2026, 9, 8, 2, 31, tzinfo=timezone.utc)

        result = next_daily_run_utc(now, "05:30")

        self.assertEqual(result, datetime(2026, 9, 9, 2, 30, tzinfo=timezone.utc))

    def test_next_daily_run_keeps_today_when_schedule_is_still_ahead(self):
        now = datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc)

        result = next_daily_run_utc(now, "05:30")

        self.assertEqual(result, datetime(2026, 9, 8, 2, 30, tzinfo=timezone.utc))

    def test_conditions_use_or_for_enabled_guards(self):
        self.assertTrue(conditions_satisfied(True, True, online_count=2, round_active=False))
        self.assertTrue(conditions_satisfied(True, True, online_count=0, round_active=True))
        self.assertFalse(conditions_satisfied(True, True, online_count=2, round_active=True))

    def test_no_enabled_conditions_allow_time_only_restart(self):
        self.assertTrue(conditions_satisfied(False, False, online_count=4, round_active=True))

    def test_msg_box_command_escapes_console_sensitive_text(self):
        result = build_msg_box_command('Сервер "ASPER1"\nперезапускается')

        self.assertEqual(result, r'msg_box "Сервер \"ASPER1\" перезапускается"')

    def test_msg_box_command_rejects_empty_or_oversized_text(self):
        with self.assertRaises(ValueError):
            build_msg_box_command("   ")
        with self.assertRaises(ValueError):
            build_msg_box_command("x" * 501)

    def test_schedule_waits_until_a_selected_condition_is_true(self):
        now = datetime(2026, 9, 8, 2, 31, tzinfo=timezone.utc)
        scheduled_for = datetime(2026, 9, 8, 2, 30, tzinfo=timezone.utc)

        decision = evaluate_schedule_tick(
            enabled=True,
            scheduled_for=scheduled_for,
            execution_due_at=None,
            warning_sent_at=None,
            no_online=True,
            round_finished=True,
            online_count=3,
            round_active=True,
            now=now,
        )

        self.assertEqual(decision.action, "wait_conditions")
        self.assertIsNone(decision.execution_due_at)

    def test_schedule_sends_warning_then_restarts_after_fifteen_seconds(self):
        scheduled_for = datetime(2026, 9, 8, 2, 30, tzinfo=timezone.utc)
        warning_time = datetime(2026, 9, 8, 2, 31, tzinfo=timezone.utc)

        warn = evaluate_schedule_tick(
            enabled=True,
            scheduled_for=scheduled_for,
            execution_due_at=None,
            warning_sent_at=None,
            no_online=True,
            round_finished=False,
            online_count=0,
            round_active=True,
            now=warning_time,
        )
        self.assertEqual(warn.action, "send_warning")
        self.assertEqual(warn.execution_due_at, datetime(2026, 9, 8, 2, 31, 15, tzinfo=timezone.utc))

        restart = evaluate_schedule_tick(
            enabled=True,
            scheduled_for=scheduled_for,
            execution_due_at=warn.execution_due_at,
            warning_sent_at=warning_time,
            no_online=True,
            round_finished=False,
            online_count=0,
            round_active=True,
            now=datetime(2026, 9, 8, 2, 31, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(restart.action, "restart")

    def test_disabled_schedule_does_not_produce_an_action(self):
        decision = evaluate_schedule_tick(
            enabled=False,
            scheduled_for=datetime(2026, 9, 8, 2, 30, tzinfo=timezone.utc),
            execution_due_at=None,
            warning_sent_at=None,
            no_online=False,
            round_finished=False,
            online_count=0,
            round_active=False,
            now=datetime(2026, 9, 8, 2, 31, tzinfo=timezone.utc),
        )

        self.assertEqual(decision.action, "noop")

    def test_api_timestamps_are_explicit_utc(self):
        value = datetime(2026, 9, 8, 2, 30, tzinfo=timezone.utc)

        self.assertEqual(iso_utc(value), "2026-09-08T02:30:00Z")

    def test_daily_time_validation_normalizes_and_rejects_invalid_values(self):
        self.assertEqual(validate_daily_time("5:30"), "05:30")
        with self.assertRaises(ValueError):
            validate_daily_time("25:00")

    def test_pterodactyl_target_uses_server_specific_uuid_without_exposing_key(self):
        settings = SimpleNamespace(
            pterodactyl_panel_url="https://panel.example",
            pterodactyl_api_key="secret-token",
            pterodactyl_server1_uuid="uuid-one",
            pterodactyl_server2_uuid="uuid-two",
        )
        client = PterodactylClient(settings)

        self.assertEqual(client._target("ASPER1"), ("https://panel.example", "secret-token", "uuid-one"))
        self.assertEqual(client._target("ASPER2"), ("https://panel.example", "secret-token", "uuid-two"))

        with self.assertRaises(PterodactylError):
            PterodactylClient(SimpleNamespace())._target("ASPER1")


if __name__ == "__main__":
    unittest.main()
