import asyncio
import gzip
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import WorldRound
from routes.world_state import _resolve_ingest_server_name, api_client_world_state


class WorldStateClientTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[WorldRound.__table__])
        self.db = Session(self.engine)
        self.db.add_all(
            [
                WorldRound(
                    round_id="round-asper1",
                    server_name="ASPER1",
                    level_name="Первая карта",
                    last_update_at=datetime.utcnow(),
                ),
                WorldRound(
                    round_id="round-asper2",
                    server_name="ASPER2",
                    level_name="Вторая карта",
                    last_update_at=datetime.utcnow(),
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_server_name_filters_active_rounds(self):
        response = asyncio.run(
            api_client_world_state(
                round_id=None,
                server_name="ASPER2",
                db=self.db,
            )
        )
        payload = json.loads(gzip.decompress(response.body))

        self.assertEqual(
            [round_["round_id"] for round_ in payload["rounds"]],
            ["round-asper2"],
        )

    def test_round_id_cannot_cross_server_filter(self):
        response = asyncio.run(
            api_client_world_state(
                round_id="round-asper1",
                server_name="ASPER1",
                db=self.db,
            )
        )
        payload = json.loads(gzip.decompress(response.body))

        self.assertEqual(payload["rounds"][0]["server_name"], "ASPER1")

    def test_one_missed_production_snapshot_keeps_round_active(self):
        self.db.query(WorldRound).delete()
        self.db.add(
            WorldRound(
                round_id="round-delayed-snapshot",
                server_name="ASPER1",
                last_update_at=datetime.utcnow() - timedelta(seconds=20),
            )
        )
        self.db.commit()

        response = asyncio.run(
            api_client_world_state(
                round_id=None,
                server_name=None,
                db=self.db,
            )
        )
        payload = json.loads(gzip.decompress(response.body))

        self.assertEqual(
            [round_["round_id"] for round_ in payload["rounds"]],
            ["round-delayed-snapshot"],
        )


class WorldStateIngestServerNameTests(unittest.TestCase):
    def test_explicit_server_name_is_normalized_and_wins(self):
        with patch("routes.world_state.settings.world_default_server_name", "ASPER1"):
            self.assertEqual(
                _resolve_ingest_server_name({"server_name": " asper2 "}, "ASPER1"),
                "ASPER2",
            )

    def test_existing_server_name_survives_omitted_payload_field(self):
        with patch("routes.world_state.settings.world_default_server_name", "ASPER1"):
            self.assertEqual(_resolve_ingest_server_name({}, "asper2"), "ASPER2")

    def test_default_repairs_snapshot_without_init(self):
        with patch("routes.world_state.settings.world_default_server_name", "ASPER1"):
            self.assertEqual(_resolve_ingest_server_name({}, None), "ASPER1")


if __name__ == "__main__":
    unittest.main()
