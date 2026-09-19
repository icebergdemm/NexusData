"""Per-round availability rules for the dispatch warehouse."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import services_warehouse as warehouse_rules
from database import Base
from models import RoundEvent, ServerAction, WarehouseItem, WarehouseRoundUsage
from routes.dispatch import _queue_cargo_action


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RoundEvent.__table__,
        ServerAction.__table__,
        WarehouseItem.__table__,
        WarehouseRoundUsage.__table__,
    ])
    session = Session(engine)
    session.add(RoundEvent(
        id=1, event_type="start", server_name="ASPER1", created_at=datetime.utcnow(),
    ))
    session.add(RoundEvent(
        id=2, event_type="start", server_name="ASPER2", created_at=datetime.utcnow(),
    ))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _order(db, items, server="ASPER1", **kwargs):
    return _queue_cargo_action(db, server, items, admin_id=1, **kwargs)


def test_unlimited_item_is_capped_only_by_its_round_limit(db):
    db.add(WarehouseItem(identifier="oxygentank", quantity=0, is_unlimited=True, per_round_limit=10))
    db.commit()
    item = db.query(WarehouseItem).first()

    # Stock of 0 is irrelevant for an unlimited line.
    assert warehouse_rules.availability(db, item, "ASPER1") == 10

    _order(db, [{"identifier": "oxygentank", "quantity": 4}])
    db.refresh(item)

    assert item.quantity == 0  # never goes negative
    assert warehouse_rules.availability(db, item, "ASPER1") == 6


def test_round_limit_rejects_the_ai_order_that_exceeds_it(db):
    db.add(WarehouseItem(identifier="railgunshell", quantity=500, per_round_limit=5))
    db.commit()

    _order(db, [{"identifier": "railgunshell", "quantity": 5}])

    with pytest.raises(HTTPException) as excinfo:
        _order(db, [{"identifier": "railgunshell", "quantity": 1}])

    assert excinfo.value.status_code == 409
    assert "Лимит на раунд" in excinfo.value.detail


def test_operator_may_exceed_the_ceiling_deliberately(db):
    db.add(WarehouseItem(identifier="railgunshell", quantity=500, per_round_limit=5))
    db.commit()

    action = _order(
        db,
        [{"identifier": "railgunshell", "quantity": 9}],
        allow_limit_override=True,
    )

    assert action.payload["limit_overrides"][0]["identifier"] == "railgunshell"
    item = db.query(WarehouseItem).first()
    assert item.quantity == 491  # stock still follows the order


def test_each_server_gets_its_own_round_budget(db):
    db.add(WarehouseItem(identifier="weldingfuel", quantity=100, per_round_limit=3))
    db.commit()
    item = db.query(WarehouseItem).first()

    _order(db, [{"identifier": "weldingfuel", "quantity": 3}], server="ASPER1")

    assert warehouse_rules.availability(db, item, "ASPER1") == 0
    assert warehouse_rules.availability(db, item, "ASPER2") == 3


def test_a_new_round_starts_the_budget_over(db):
    db.add(WarehouseItem(identifier="weldingfuel", quantity=100, per_round_limit=3))
    db.commit()
    item = db.query(WarehouseItem).first()
    _order(db, [{"identifier": "weldingfuel", "quantity": 3}])
    assert warehouse_rules.availability(db, item, "ASPER1") == 0

    db.add(RoundEvent(
        id=3, event_type="start", server_name="ASPER1",
        created_at=datetime.utcnow() + timedelta(minutes=30),
    ))
    db.commit()

    assert warehouse_rules.availability(db, item, "ASPER1") == 3


def test_finite_item_is_capped_by_whichever_runs_out_first(db):
    db.add(WarehouseItem(identifier="flare", quantity=2, per_round_limit=10))
    db.commit()
    item = db.query(WarehouseItem).first()

    assert warehouse_rules.availability(db, item, "ASPER1") == 2


def test_unlimited_without_a_ceiling_is_rejected():
    with pytest.raises(ValueError):
        warehouse_rules.validate_limit_settings(True, None)
    assert warehouse_rules.validate_limit_settings(True, 5) == 5
    assert warehouse_rules.validate_limit_settings(False, None) is None
