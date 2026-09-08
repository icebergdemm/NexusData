from types import SimpleNamespace

from permissions_registry import PERMISSION_REGISTRY
from permission_audit import build_permission_snapshot, effective_permission


def test_effective_permission_reports_value_and_sources():
    subject = SimpleNamespace(
        id=106,
        is_superuser=False,
        individual_permissions={"pages": {"ratings": {"view": None}}},
        privilege_group=SimpleNamespace(
            id=3,
            name="Модераторы",
            permissions={"pages": {"ratings": {"view": True}}},
        ),
        _permission_policy_mode="deny_v2",
        _department_site_permissions={"pages": {"ratings": {"view": True}}},
    )
    department_sources = [{
        "department_id": 7,
        "department_name": "Экзаменационная служба",
        "level_id": 48,
        "level_key": "dept-examiner",
        "level_name": "Экзаменатор",
        "module": "site",
        "page": "ratings",
        "action": "view",
        "value": True,
    }]

    decision = effective_permission(subject, "ratings", "view", department_sources)

    assert decision["value"] is True
    assert {source["kind"] for source in decision["sources"]} == {"group", "department_access_level"}


def test_snapshot_contains_every_registry_action_as_tri_state():
    subject = SimpleNamespace(
        id=1,
        is_superuser=False,
        individual_permissions=None,
        privilege_group=None,
        _permission_policy_mode="deny_v2",
        _department_site_permissions={},
    )

    snapshot = build_permission_snapshot(subject, PERMISSION_REGISTRY, [])

    assert set(snapshot["pages"]) == set(PERMISSION_REGISTRY)
    assert snapshot["pages"]["ratings"]["view"]["value"] is False
    assert snapshot["pages"]["ratings"]["view"]["state"] == "unset"


def test_department_module_source_is_kept_out_of_global_page_matrix():
    subject = SimpleNamespace(
        id=1,
        is_superuser=False,
        individual_permissions=None,
        privilege_group=None,
        _permission_policy_mode="deny_v2",
        _department_site_permissions={},
    )
    decision = effective_permission(subject, "ratings", "view", [{
        "module": "module",
        "page": "ratings",
        "action": "view",
        "value": True,
    }])

    assert decision["value"] is False
    assert decision["state"] == "unset"
    assert decision["sources"] == []


def test_department_deny_is_reported_as_ignored_and_cannot_create_conflict():
    subject = SimpleNamespace(
        id=1,
        is_superuser=False,
        individual_permissions=None,
        privilege_group=SimpleNamespace(
            id=3,
            name="Модераторы",
            permissions={"pages": {"ratings": {"view": True}}},
        ),
        _permission_policy_mode="deny_v2",
        _department_site_permissions={"pages": {"ratings": {"view": True}}},
    )
    decision = effective_permission(subject, "ratings", "view", [{
        "module": "site",
        "department_name": "Экзаменационная служба",
        "level_name": "Экзаменатор",
        "value": False,
        "page": "ratings",
        "action": "view",
    }])

    assert decision["value"] is True
    assert decision["state"] == "allow"
    ignored = next(source for source in decision["sources"] if source["kind"] == "department_access_level")
    assert ignored["applies"] is False
