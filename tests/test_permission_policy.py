from types import SimpleNamespace

from security import check_department_permission, check_permission, grant_only_permissions


def admin(*, mode="deny_v2", individual=None, group=None, department_pages=None):
    value = SimpleNamespace(
        id=1,
        is_superuser=False,
        individual_permissions=individual,
        privilege_group=group,
        _permission_policy_mode=mode,
        _department_site_permissions=department_pages or {},
    )
    return value


def test_explicit_deny_beats_individual_group_and_department_allow():
    subject = admin(
        individual={"pages": {"ratings": {"view": False}}},
        group=SimpleNamespace(permissions={"pages": {"ratings": {"view": True}}}),
        department_pages={"pages": {"ratings": {"view": True}}},
    )

    assert check_permission(subject, "ratings", "view") is False


def test_missing_permission_is_neutral_and_allows_department_grant():
    subject = admin(
        individual={"pages": {}},
        group=SimpleNamespace(permissions={"pages": {}}),
        department_pages={"pages": {"ratings": {"view": True}}},
    )

    assert check_permission(subject, "ratings", "view") is True


def test_department_deny_is_ignored_because_department_permissions_only_grant():
    subject = admin(
        individual={"pages": {}},
        group=SimpleNamespace(permissions={"pages": {"ratings": {"view": True}}}),
        department_pages={"pages": {"ratings": {"view": False}}},
    )

    assert check_permission(subject, "ratings", "view") is True


def test_legacy_mode_keeps_false_additive_behavior():
    subject = admin(
        mode="legacy_additive",
        individual={"pages": {"ratings": {"view": False}}},
        group=SimpleNamespace(permissions={"pages": {}}),
        department_pages={"pages": {"ratings": {"view": True}}},
    )

    assert check_permission(subject, "ratings", "view") is True


def test_department_module_deny_is_ignored_and_head_fallback_still_grants():
    level = SimpleNamespace(
        key="examiner",
        permissions={"pages": {"test_server": {"view": False}}},
    )
    department = SimpleNamespace(access_levels=[level])
    member = SimpleNamespace(is_head=True, held_level_keys=["examiner"])
    subject = admin()

    assert check_department_permission(subject, department, member, "test_server", "view") is True


def test_department_permission_writer_drops_denies():
    assert grant_only_permissions({"pages": {"ratings": {"view": True, "edit": False}}}) == {
        "pages": {"ratings": {"view": True}},
    }
