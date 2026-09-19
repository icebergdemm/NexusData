from types import SimpleNamespace

from audit_service import format_audit_event, safe_audit_details


def _event(action, details=None):
    return SimpleNamespace(
        id=19,
        action=action,
        details=details or {},
        created_at=None,
        admin_id=7,
        actor_type="web",
        actor_discord_id="123",
        actor_discord_name="Петр Громов",
        actor_nexus_username="Petr Gromov",
        auth_type="cookie",
        session_id=41,
        request_id="req-19",
        source_ip="203.0.113.10",
        user_agent="Browser",
        outcome="success",
        reason=None,
        severity="info",
    )


def test_format_audit_event_turns_key_revocation_into_readable_structure():
    formatted = format_audit_event(_event("login_key_revoke", {"target_admin_id": 125, "key_id": 27}))
    assert formatted["summary"] == "Отозван ключ входа"
    assert formatted["target"] == {"type": "login_key", "id": 27, "label": "ключ входа #27"}
    assert formatted["context"]["source"] == "Панель"
    assert formatted["raw"] is None


def test_format_audit_event_preserves_changes_and_reason_without_json_dump():
    formatted = format_audit_event(_event("moderation.kick", {"reason": "Провокационное поведение", "player_id": 44}))
    assert formatted["summary"] == "Исключён игрок с сервера"
    assert formatted["target"]["id"] == 44
    assert formatted["reason"] == "Провокационное поведение"
    assert "{" not in formatted["summary"]


def test_format_audit_event_only_includes_redacted_raw_when_requested():
    event = _event("task_delete", {"task_id": 51, "token": "secret"})
    assert format_audit_event(event, include_raw=False)["raw"] is None
    assert format_audit_event(event, include_raw=True)["raw"] == {"task_id": 51}


def test_safe_audit_details_redacts_nested_credentials_and_keeps_shape():
    details = safe_audit_details({"changes": [{"field": "name", "from": "a", "to": "b"}], "credentials": {"password": "x"}})
    assert details == {"changes": [{"field": "name", "from": "a", "to": "b"}], "credentials": {}}
