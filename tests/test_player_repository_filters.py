import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import LabelCategory, PlayerAnomaly, PlayerData, PlayerLabel, PlayerName, PlayerNote, PlayerPunishment
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

        first, second, _ = self.session.query(PlayerData).order_by(PlayerData.player_id).all()
        category = LabelCategory(name="Проверка", icon="⚠️")
        self.session.add(category)
        self.session.flush()
        self.category_id = category.id
        self.session.add_all([
            PlayerLabel(player_id=first.player_id, category_id=category.id),
            PlayerNote(player_id=first.player_id, content="Есть заметка"),
            PlayerName(
                player_id=first.player_id,
                nickname="Старый ник",
                first_seen=datetime.utcnow(),
                last_seen=datetime.utcnow(),
            ),
            PlayerPunishment(
                player_id=first.player_id,
                player_steamid=first.player_steamid,
                type="warning",
                is_active=1,
                expires_at=datetime.utcnow() + timedelta(days=1),
            ),
            PlayerAnomaly(
                category="twink",
                subtype="ip_shared",
                player_id=first.player_id,
                related_player_id=second.player_id,
                score=50,
                status="new",
            ),
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

    def test_search_paginates_after_matching_all_identifiers(self):
        repository = PlayerRepository(self.session)

        page, total = repository.search_paginated("765611980000000", page=2, page_size=1)

        self.assertEqual(total, 3)
        self.assertEqual([player.player_last_time_name for player in page], ["Без привязки"])

    def test_player_flags_and_facets_use_moderation_tables(self):
        repository = PlayerRepository(self.session)
        players = self.session.query(PlayerData).order_by(PlayerData.player_id).all()
        flags = repository.get_player_flags([player.player_id for player in players])

        first_flags = flags[players[0].player_id]
        self.assertTrue(first_flags["has_labels"])
        self.assertEqual(first_flags["label_ids"], [self.category_id])
        self.assertTrue(all(first_flags[key] for key in (
            "has_notes", "has_active_punishments", "has_open_anomalies", "has_previous_names",
        )))
        self.assertTrue(flags[players[1].player_id]["has_open_anomalies"])
        self.assertFalse(flags[players[2].player_id]["has_active_punishments"])

        _, labels_total = repository.get_all(page=1, page_size=50, label_mode="with")
        _, notes_total = repository.get_all(page=1, page_size=50, notes="with")
        _, sanctions_total = repository.get_all(page=1, page_size=50, sanctions="with")
        _, anomalies_total = repository.get_all(page=1, page_size=50, anomalies="with")
        _, names_total = repository.get_all(page=1, page_size=50, name_history="with")

        self.assertEqual(labels_total, 1)
        self.assertEqual(notes_total, 1)
        self.assertEqual(sanctions_total, 1)
        self.assertEqual(anomalies_total, 2)
        self.assertEqual(names_total, 1)


if __name__ == "__main__":
    unittest.main()
