import pytest

from routes.tasks import system_board_column_for_status


@pytest.mark.parametrize(
    ("mode", "status", "expected"),
    [
        ("requests", "submitted", "Рассмотрение"),
        ("requests", "in_dev_queue", "Передано в разработку"),
        ("requests", "in_progress", "Передано в разработку"),
        ("requests", "testing", "Передано в разработку"),
        ("requests", "done", "Готово"),
        ("requests", "rejected", "Архив"),
        ("requests", "archived", "Архив"),
        ("development", "in_dev_queue", "Новые"),
        ("development", "in_progress", "В работе"),
        ("development", "testing", "Тестирование"),
        ("development", "done", "Готово"),
        ("development", "submitted", None),
        ("development", "rejected", None),
        ("development", "archived", None),
    ],
)
def test_system_board_status_is_projected_by_view(mode, status, expected):
    assert system_board_column_for_status(status, mode) == expected


def test_system_board_rejects_unknown_view():
    with pytest.raises(ValueError, match="Некорректный режим доски"):
        system_board_column_for_status("done", "secret")


def test_blocked_status_is_not_part_of_system_board():
    from routes.tasks import SYSTEM_TASK_STATUSES

    assert "blocked" not in SYSTEM_TASK_STATUSES
    assert system_board_column_for_status("blocked", "requests") is None
    assert system_board_column_for_status("blocked", "development") is None
