from pathlib import Path
from types import SimpleNamespace

from discord_bot.data import _aggregate_rating_records


ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "discord_bot" / "data.py").read_text(encoding="utf-8")
BOT = (ROOT / "discord_bot" / "bot.py").read_text(encoding="utf-8")


def test_discord_data_supports_one_aggregate_pvp_leaderboard():
    assert 'if role == "all"' in DATA
    assert 'def _aggregate_rating_records(records, limit: int)' in DATA
    assert '_aggregate_rating_records(records, limit)' in DATA


def test_discord_pvp_board_does_not_publish_role_sections():
    pvp_block = BOT[BOT.index("# Mission keeps its existing per-role messages above."):BOT.index("# Department rating message IDs")]
    assert 'get_ratings_leaderboard, "all", 10, "pvp"' in pvp_block
    assert "pvp_fields" not in pvp_block
    assert "add_field" not in pvp_block


def test_aggregate_pvp_rows_sum_role_totals_per_player():
    rows = _aggregate_rating_records([
        SimpleNamespace(steam_id="a", current_rating=10.25, cumulative_points=20),
        SimpleNamespace(steam_id="a", current_rating=4.75, cumulative_points=8),
        SimpleNamespace(steam_id="b", current_rating=12, cumulative_points=25),
    ], 10)

    assert [(row.steam_id, row.current_rating, row.cumulative_points) for row in rows] == [
        ("a", 15.0, 28.0), ("b", 12.0, 25.0)
    ]
