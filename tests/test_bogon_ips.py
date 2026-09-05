import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import PlayerData, PlayerIP
from services import EventDispatcherService, PlayerIPService
from utils import is_bogon_ip


class BogonIPTests(unittest.TestCase):
    def test_private_and_non_global_addresses_are_bogon(self):
        for value in ("172.18.0.3", "172.18.0.0/16", "10.0.0.1", "192.168.1.20", "127.0.0.1", "100.64.0.1", "not-an-ip"):
            with self.subTest(value=value):
                self.assertTrue(is_bogon_ip(value))

        self.assertFalse(is_bogon_ip("8.8.8.8"))

    def test_profile_ip_history_hides_bogon_rows(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerIP.__table__])
        db = sessionmaker(bind=engine)()
        try:
            player = PlayerData(player_last_time_name="Тест", player_steamid="76561198000000000")
            db.add(player)
            db.flush()
            now = datetime.utcnow()
            db.add_all([
                PlayerIP(player_id=player.player_id, ip="172.18.0.3", subnet="172.18.0.0/16", first_seen=now, last_seen=now),
                PlayerIP(player_id=player.player_id, ip="8.8.8.8", subnet="8.8.8.0/24", first_seen=now, last_seen=now),
            ])
            db.commit()

            rows = PlayerIPService(db).get_player_ips(player.player_id)

            self.assertEqual([row.ip for row in rows], ["8.8.8.8"])
        finally:
            db.close()
            engine.dispose()

    def test_ingest_drops_bogon_ip_before_it_reaches_profile_history(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerIP.__table__])
        db = sessionmaker(bind=engine)()
        try:
            player = PlayerData(player_last_time_name="Тест", player_steamid="76561198000000000")
            db.add(player)
            db.commit()

            response = EventDispatcherService(db)._handle_player_ips_upsert({
                "player_id": player.player_id,
                "ip": "172.18.0.3",
                "subnet": "172.18.0.0/16",
            })

            self.assertEqual(response["ignored"], "bogon_ip")
            self.assertEqual(db.query(PlayerIP).count(), 0)
        finally:
            db.close()
            engine.dispose()
