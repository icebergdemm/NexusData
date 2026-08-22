import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import AdminUser, ServerAction, WarehouseItem
from routes.dispatch import _queue_cargo_action
from security import check_permission
from services import ServerModerationService


class SupplyDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[
                AdminUser.__table__,
                WarehouseItem.__table__,
                ServerAction.__table__,
            ],
        )
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _stock(self, identifier: str = "oxygentank") -> WarehouseItem:
        return (
            self.db.query(WarehouseItem)
            .filter(WarehouseItem.identifier == identifier)
            .one()
        )

    def test_manual_and_ai_shared_queue_reserves_stock_and_normalizes_server(self):
        self.db.add(WarehouseItem(identifier="oxygentank", quantity=10))
        self.db.commit()

        action = _queue_cargo_action(
            self.db,
            "asper1",
            [
                {"identifier": "oxygentank", "quantity": 2},
                {"identifier": "oxygentank", "quantity": 3},
            ],
            admin_id=None,
        )

        self.assertEqual(self._stock().quantity, 5)
        self.assertEqual(action.target_server, "ASPER1")
        self.assertEqual(action.status_asper1, 0)
        self.assertEqual(action.status_asper2, 1)
        self.assertEqual(
            action.payload,
            {"items": [{"identifier": "oxygentank", "quantity": 5}]},
        )

    def test_duplicate_lines_are_checked_as_one_reservation(self):
        self.db.add(WarehouseItem(identifier="oxygentank", quantity=10))
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            _queue_cargo_action(
                self.db,
                "ASPER1",
                [
                    {"identifier": "oxygentank", "quantity": 6},
                    {"identifier": "oxygentank", "quantity": 6},
                ],
                admin_id=None,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self._stock().quantity, 10)
        self.assertEqual(self.db.query(ServerAction).count(), 0)

    def test_failed_server_ack_refunds_reserved_stock_exactly_once(self):
        self.db.add(WarehouseItem(identifier="oxygentank", quantity=10))
        self.db.commit()
        action = _queue_cargo_action(
            self.db,
            "ASPER1",
            [{"identifier": "oxygentank", "quantity": 4}],
            admin_id=None,
        )
        service = ServerModerationService(self.db)

        self.assertTrue(
            service.acknowledge_action(
                action.id,
                False,
                "spawn failed",
                server_name="ASPER1",
            )
        )
        self.assertEqual(self._stock().quantity, 10)
        self.db.refresh(action)
        self.assertEqual(action.status_asper1, 2)
        self.assertTrue(action.payload["warehouse_refunded"])

        # The game plugin may retry an ACK if the HTTP response was lost.
        self.assertTrue(
            service.acknowledge_action(
                action.id,
                False,
                "spawn failed",
                server_name="ASPER1",
            )
        )
        self.assertEqual(self._stock().quantity, 10)

    def test_successful_server_ack_keeps_stock_deducted(self):
        self.db.add(WarehouseItem(identifier="oxygentank", quantity=10))
        self.db.commit()
        action = _queue_cargo_action(
            self.db,
            "ASPER2",
            [{"identifier": "oxygentank", "quantity": 4}],
            admin_id=None,
        )

        self.assertTrue(
            ServerModerationService(self.db).acknowledge_action(
                action.id,
                True,
                server_name="ASPER2",
            )
        )
        self.assertEqual(self._stock().quantity, 6)
        self.db.refresh(action)
        self.assertEqual(action.status_asper2, 1)
        self.assertNotIn("warehouse_refunded", action.payload)


class AIDispatcherPermissionTests(unittest.TestCase):
    @staticmethod
    def _admin(*, role="Смотритель", group_name=None, individual=None):
        group = (
            SimpleNamespace(name=group_name, permissions={})
            if group_name is not None
            else None
        )
        return SimpleNamespace(
            is_superuser=False,
            role=role,
            privilege_group=group,
            individual_permissions=individual or {},
            _department_site_permissions={},
        )

    def test_curator_and_higher_project_ranks_can_manage_ai(self):
        for rank in ("Куратор", "Старший куратор", "Управляющий", "Администратор"):
            with self.subTest(rank=rank):
                admin = self._admin(group_name=rank)
                self.assertTrue(check_permission(admin, "dispatch", "manage_ai"))

    def test_explicit_permission_can_grant_ai_management_to_any_rank(self):
        admin = self._admin(
            individual={"pages": {"dispatch": {"manage_ai": True}}},
        )
        self.assertTrue(check_permission(admin, "dispatch", "manage_ai"))

    def test_lower_rank_without_explicit_permission_cannot_manage_ai(self):
        admin = self._admin(role="Смотритель", group_name="Оператор")
        self.assertFalse(check_permission(admin, "dispatch", "manage_ai"))


if __name__ == "__main__":
    unittest.main()
