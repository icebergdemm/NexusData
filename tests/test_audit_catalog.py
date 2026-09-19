from security import (
    AUDIT_ACTION_CATEGORIES,
    AUDIT_MUTATION_EXCLUSIONS,
    SENSITIVE_AUDIT_ACTIONS,
    classify_audit_action,
)


def test_catalog_classifies_core_audit_families():
    assert classify_audit_action("login") == "auth"
    assert classify_audit_action("permission_policy_apply") == "permissions"
    assert classify_audit_action("moderation.kick") == "moderation"
    assert classify_audit_action("task_delete") == "content"
    assert classify_audit_action("view_player_profile") == "sensitive_view"
    assert classify_audit_action("web_lockdown_change") == "system"


def test_catalog_keeps_dynamic_moderation_actions_in_moderation_family():
    assert classify_audit_action("moderation.mute") == "moderation"
    assert classify_audit_action("moderation.warn") == "moderation"


def test_sensitive_views_are_explicit_and_polling_is_excluded():
    assert "view_player_profile" in SENSITIVE_AUDIT_ACTIONS
    assert "view_player_ip" in SENSITIVE_AUDIT_ACTIONS
    assert "view_login_keys" in SENSITIVE_AUDIT_ACTIONS
    assert "healthz" in AUDIT_MUTATION_EXCLUSIONS
    assert "polling" in AUDIT_MUTATION_EXCLUSIONS


def test_catalog_has_labels_for_existing_actions():
    expected = {
        "login", "logout", "csrf_failure", "login_key_create", "login_key_revoke",
        "revoke_session", "create_admin", "task_delete", "ticket_delete",
        "punishment_create", "web_lockdown_change",
    }
    assert expected.issubset(AUDIT_ACTION_CATEGORIES)
