from types import SimpleNamespace

from services import (
    LabelCategoryService,
    can_manage_secret_labels,
    filter_visible_label_categories,
    filter_visible_label_folders,
    filter_visible_label_pairs,
    merge_visible_label_category_order,
)
from database import Base
from models import LabelFolder
from models import LabelCategory, PlayerData, PlayerLabel
from repositories import PlayerRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from permissions_registry import PERMISSION_ACTION_SPLITS, PERMISSION_REGISTRY


def _admin(*, superuser=False, secret=False):
    permissions = {"players": {"manage_secret_labels": secret}}
    return SimpleNamespace(
        is_superuser=superuser,
        individual_permissions=permissions,
        privilege_group=None,
    )


def _category(category_id, name, *, secret=False, folder_id=None):
    return SimpleNamespace(
        id=category_id,
        name=name,
        is_secret=secret,
        folder_id=folder_id,
    )


def test_secret_label_management_requires_superuser_or_explicit_permission():
    assert can_manage_secret_labels(_admin()) is False
    assert can_manage_secret_labels(_admin(secret=True)) is True
    assert can_manage_secret_labels(_admin(superuser=True)) is True


def test_secret_label_permission_is_explicit_and_not_backfilled_from_edit():
    actions = {action["key"] for action in PERMISSION_REGISTRY["players"]["actions"]}

    assert "manage_secret_labels" in actions
    assert "manage_secret_labels" not in PERMISSION_ACTION_SPLITS["players"]["edit"]


def test_secret_categories_and_folders_are_removed_from_unauthorized_view():
    public = _category(1, "Публичная", folder_id=10)
    secret = _category(2, "Секретная", secret=True, folder_id=20)
    folders = [
        SimpleNamespace(id=10, name="Обычные"),
        SimpleNamespace(id=20, name="Секретные"),
    ]

    visible_categories = filter_visible_label_categories([public, secret], _admin())
    visible_folders = filter_visible_label_folders(folders, [public, secret], _admin())

    assert visible_categories == [public]
    assert visible_folders == [folders[0]]


def test_secret_label_pairs_are_hidden_without_permission():
    public = _category(1, "Публичная")
    secret = _category(2, "Секретная", secret=True)
    public_pair = (SimpleNamespace(category_id=1), public)
    secret_pair = (SimpleNamespace(category_id=2), secret)

    assert filter_visible_label_pairs([public_pair, secret_pair], _admin()) == [public_pair]
    assert filter_visible_label_pairs([public_pair, secret_pair], _admin(secret=True)) == [public_pair, secret_pair]


def test_label_category_service_persists_secret_flag_and_folder():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        folder = LabelFolder(name="Закрытые")
        db.add(folder)
        db.flush()

        category = LabelCategoryService(db).create(
            "Секретная метка", "#111111", "🔒", 7,
            folder_id=folder.id, description="Для ограниченного круга",
            is_secret=True,
        )

        assert category.is_secret is True
        assert category.folder_id == folder.id
    finally:
        db.close()
        engine.dispose()


def test_player_flags_only_include_label_ids_visible_to_current_admin():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        player = PlayerData(player_id=1, player_last_time_name="Игрок", player_steamid="steam-1")
        public = LabelCategory(name="Публичная")
        secret = LabelCategory(name="Секретная", is_secret=True)
        db.add_all([player, public, secret])
        db.flush()
        db.add_all([
            PlayerLabel(player_id=1, category_id=public.id),
            PlayerLabel(player_id=1, category_id=secret.id),
        ])
        db.commit()

        flags = PlayerRepository(db).get_player_flags([1], visible_label_ids={public.id})

        assert flags[1]["label_ids"] == [public.id]
        assert flags[1]["has_labels"] is True
    finally:
        db.close()
        engine.dispose()


def test_reorder_keeps_hidden_categories_at_their_existing_positions():
    public_one = _category(1, "Одна")
    secret = _category(2, "Секретная", secret=True)
    public_two = _category(3, "Две")

    ordered = merge_visible_label_category_order(
        [public_one, secret, public_two],
        visible_ids={public_one.id, public_two.id},
        requested_ids=[public_two.id, public_one.id],
    )

    assert ordered == [public_two.id, secret.id, public_one.id]


def test_reorder_rejects_missing_or_duplicate_visible_categories():
    public_one = _category(1, "Одна")
    public_two = _category(2, "Две")

    try:
        merge_visible_label_category_order(
            [public_one, public_two],
            visible_ids={public_one.id, public_two.id},
            requested_ids=[public_one.id, public_one.id],
        )
    except ValueError as exc:
        assert "ровно по одному" in str(exc)
    else:
        raise AssertionError("duplicate reorder payload must be rejected")


def test_label_reorder_route_precedes_dynamic_category_route():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "routes" / "admin.py").read_text(encoding="utf-8")
    assert source.index('@router.post("/api/v1/admin/label-categories/reorder")') < source.index('@router.post("/api/v1/admin/label-categories/{cat_id}")')
