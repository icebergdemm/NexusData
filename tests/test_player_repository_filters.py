import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import PlayerData
from repositories import PlayerRepository


class PlayerRepositoryDiscordFilterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.session.add_all([
            PlayerData(player_last_time_name="Привязан", player_steamid="76561198000000000", discord_id="123"),
            PlayerData(player_last_time_name="Без привязки", player_steamid="76561198000000001", discord_id=None),
            PlayerData(player_last_time_name="Пустая привязка", player_steamid="76561198000000002", discord_id=""),
        ])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_discord_filter_is_applied_before_pagination(self):
        repository = PlayerRepository(self.session)

        linked, linked_total = repository.get_all(page=1, page_size=1, discord_link="linked")
        unlinked, unlinked_total = repository.get_all(page=1, page_size=1, discord_link="unlinked")

        self.assertEqual(linked_total, 1)
        self.assertEqual(len(linked), 1)
        self.assertEqual(unlinked_total, 2)
        self.assertEqual(len(unlinked), 1)

    def test_discord_filter_is_applied_before_search_limit(self):
        repository = PlayerRepository(self.session)

        linked = repository.search("765611980000000", limit=10, discord_link="linked")
        unlinked = repository.search("765611980000000", limit=10, discord_link="unlinked")

        self.assertEqual([player.player_last_time_name for player in linked], ["Привязан"])
        self.assertEqual([player.player_last_time_name for player in unlinked], ["Без привязки", "Пустая привязка"])


if __name__ == "__main__":
    unittest.main()
