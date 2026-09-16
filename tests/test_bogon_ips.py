import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from fastapi import HTTPException
from models import AppSetting, PlayerData, PlayerIP, PlayerStats
from repositories import PlayerIPRepository
from schemas import PlayerDataCreate
from services import EventDispatcherService, PlayerIPService, PlayerService, TwinkDetectionService, ip_subnet_fields
from utils import derive_subnet, is_bogon_ip


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

    def test_mass_player_ip_is_excluded_from_twink_detection(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[AppSetting.__table__, PlayerData.__table__, PlayerIP.__table__])
        db = sessionmaker(bind=engine)()
        try:
            now = datetime.utcnow()
            players = [PlayerData(player_id=index, player_steamid=str(index), player_last_time_name=f"p{index}") for index in range(1, 6)]
            db.add_all(players)
            db.flush()
            db.add_all([
                PlayerIP(player_id=player.player_id, ip="8.8.8.8", subnet="8.8.8.0/24", first_seen=now, last_seen=now)
                for player in players[:4]
            ])
            db.commit()

            self.assertTrue(TwinkDetectionService(db).get_all_twink_pairs())

            db.add(PlayerIP(player_id=players[4].player_id, ip="8.8.8.8", subnet="8.8.8.0/24", first_seen=now, last_seen=now))
            db.commit()

            self.assertEqual(TwinkDetectionService(db).get_all_twink_pairs(), [])
        finally:
            db.close()
            engine.dispose()


class DerivedSubnetTests(unittest.TestCase):
    def test_derive_subnet(self):
        self.assertEqual(derive_subnet("176.109.42.132"), "176.109.42.0/24")
        self.assertEqual(derive_subnet("8.8.8.8"), "8.8.8.0/24")
        self.assertIsNone(derive_subnet("not-an-ip"))
        self.assertIsNone(derive_subnet(None))
        self.assertIsNone(derive_subnet(""))
        v6 = derive_subnet("2001:db8::1")
        self.assertTrue(v6.endswith("/64"))

    def test_ip_subnet_fields(self):
        self.assertEqual(
            ip_subnet_fields("176.109.42.132", None),
            {"last_ip": "176.109.42.132", "last_subnet": "176.109.42.0/24"},
        )
        self.assertEqual(
            ip_subnet_fields("176.109.42.132", "176.109.0.0/16"),
            {"last_ip": "176.109.42.132", "last_subnet": "176.109.0.0/16"},
        )
        self.assertEqual(ip_subnet_fields("172.18.0.3", None), {})
        self.assertEqual(ip_subnet_fields("not-an-ip", None), {})
        self.assertEqual(ip_subnet_fields(None, None), {})

    def test_upsert_player_update_derives_subnet(self):
        # Regression: ip without subnet left last_subnet NULL, and the DB
        # triggers trg_players_ip_insert/trg_players_ip_update then aborted
        # the write with 1048 "Column 'subnet' cannot be null".
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerStats.__table__])
        db = sessionmaker(bind=engine)()
        try:
            db.add(PlayerData(player_last_time_name="Тест", player_steamid="76561198000000001"))
            db.commit()

            PlayerService(db).upsert_player(PlayerDataCreate(
                player_last_time_name="Тест",
                player_steamid="76561198000000001",
                last_ip="176.109.42.132",
            ))

            player = db.query(PlayerData).filter(
                PlayerData.player_steamid == "76561198000000001"
            ).one()
            self.assertEqual(player.last_ip, "176.109.42.132")
            self.assertEqual(player.last_subnet, "176.109.42.0/24")
        finally:
            db.close()
            engine.dispose()

    def test_upsert_player_create_derives_subnet(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerStats.__table__])
        db = sessionmaker(bind=engine)()
        try:
            PlayerService(db).upsert_player(PlayerDataCreate(
                player_last_time_name="Новичок",
                player_steamid="76561198000000002",
                last_ip="176.109.42.132",
            ))

            player = db.query(PlayerData).filter(
                PlayerData.player_steamid == "76561198000000002"
            ).one()
            self.assertEqual(player.last_ip, "176.109.42.132")
            self.assertEqual(player.last_subnet, "176.109.42.0/24")
        finally:
            db.close()
            engine.dispose()

    def test_ip_upsert_without_subnet_derives_instead_of_null(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerIP.__table__])
        db = sessionmaker(bind=engine)()
        try:
            player = PlayerData(player_last_time_name="Тест", player_steamid="76561198000000002")
            db.add(player)
            db.commit()

            record = PlayerIPRepository(db).create_or_update(player.player_id, "176.109.42.132", None)

            self.assertEqual(record.subnet, "176.109.42.0/24")
        finally:
            db.close()
            engine.dispose()

    def test_ip_upsert_without_subnet_or_ip_raises_422(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[PlayerData.__table__, PlayerIP.__table__])
        db = sessionmaker(bind=engine)()
        try:
            player = PlayerData(player_last_time_name="Тест", player_steamid="76561198000000003")
            db.add(player)
            db.commit()

            with self.assertRaises(HTTPException) as ctx:
                PlayerIPRepository(db).create_or_update(player.player_id, "not-an-ip", None)

            self.assertEqual(ctx.exception.status_code, 422)
        finally:
            db.close()
            engine.dispose()
