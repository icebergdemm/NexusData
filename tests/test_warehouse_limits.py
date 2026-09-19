"""Per-round availability rules for the dispatch warehouse."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import services_warehouse as warehouse_rules
from database import Base
from models import AdminUser, RoundEvent, ServerAction, WarehouseItem, WarehouseRoundUsage
from routes.dispatch import _queue_cargo_action


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        AdminUser.__table__,
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
    assert warehouse_rules.validate_limit_settings(True, 5) == (5, None, None)
    assert warehouse_rules.validate_limit_settings(False, None) == (None, None, None)


def test_random_round_limit_stays_fixed_inside_one_round(db):
    db.add(WarehouseItem(
        identifier="oxygentank", quantity=0, is_unlimited=True,
        per_round_limit=20, per_round_limit_min=0,
    ))
    db.commit()
    item = db.query(WarehouseItem).first()

    drawn = warehouse_rules.round_limit(db, item, "ASPER1")

    assert 0 <= drawn <= 20
    # Re-reading must never change it: the AI is shown this number, and a
    # caller could otherwise retry until the roll suited them.
    assert all(warehouse_rules.round_limit(db, item, "ASPER1") == drawn for _ in range(20))
    assert warehouse_rules.availability(db, item, "ASPER1") == drawn


def test_random_limit_is_drawn_per_server_and_per_round(db):
    db.add(WarehouseItem(
        identifier="oxygentank", quantity=0, is_unlimited=True,
        per_round_limit=1000, per_round_limit_min=0,
    ))
    db.commit()
    item = db.query(WarehouseItem).first()

    first_server = warehouse_rules.round_limit(db, item, "ASPER1")
    second_server = warehouse_rules.round_limit(db, item, "ASPER2")

    db.add(RoundEvent(
        id=3, event_type="start", server_name="ASPER1",
        created_at=datetime.utcnow() + timedelta(minutes=30),
    ))
    db.commit()
    next_round = warehouse_rules.round_limit(db, item, "ASPER1")

    # A wide range makes an accidental three-way match vanishingly unlikely.
    assert len({first_server, second_server, next_round}) == 3


def test_random_range_spreads_across_the_whole_span(db):
    """A degenerate hash would silently pin every round to the same number."""
    db.add(WarehouseItem(identifier="flare", quantity=0, is_unlimited=True,
                         per_round_limit=10, per_round_limit_min=0))
    db.commit()
    item = db.query(WarehouseItem).first()

    drawn = set()
    for round_id in range(200):
        drawn.add(warehouse_rules.round_limit(db, item, "ASPER1", round_ref=str(round_id)))

    assert min(drawn) == 0 and max(drawn) == 10
    assert len(drawn) == 11


def test_random_limit_is_enforced_like_a_fixed_one(db):
    db.add(WarehouseItem(identifier="flare", quantity=100,
                         per_round_limit=10, per_round_limit_min=0))
    db.commit()
    item = db.query(WarehouseItem).first()
    drawn = warehouse_rules.round_limit(db, item, "ASPER1")

    _order(db, [{"identifier": "flare", "quantity": drawn}])

    assert warehouse_rules.availability(db, item, "ASPER1") == 0
    with pytest.raises(HTTPException) as excinfo:
        _order(db, [{"identifier": "flare", "quantity": 1}])
    assert excinfo.value.status_code == 409
    assert f"из {drawn}" in excinfo.value.detail


def test_equal_bounds_are_stored_as_a_plain_fixed_limit():
    assert warehouse_rules.validate_limit_settings(False, 10, 10) == (10, None, None)
    assert warehouse_rules.validate_limit_settings(False, 10, 0) == (10, 0, None)
    with pytest.raises(ValueError):
        warehouse_rules.validate_limit_settings(False, 5, 9)
    with pytest.raises(ValueError):
        warehouse_rules.validate_limit_settings(False, None, 3)


def test_bulk_add_stores_limits_from_the_dialog(db, monkeypatch):
    """The add dialog sends quantity and limit settings in one request."""
    import asyncio
    from types import SimpleNamespace

    import routes.warehouse as warehouse_routes

    monkeypatch.setattr(warehouse_routes, "check_permission", lambda *_args: True)

    class _Request:
        async def json(self):
            return {"items": [
                {"identifier": "oxygentank", "quantity": 0,
                 "is_unlimited": True, "per_round_limit": 20, "per_round_limit_min": 0},
                {"identifier": "flare", "quantity": 40, "per_round_limit": 5},
            ]}

    asyncio.run(warehouse_routes.api_upsert_warehouse_item(
        _Request(), db=db, current_admin=SimpleNamespace(id=1),
    ))

    tank = db.query(WarehouseItem).filter(WarehouseItem.identifier == "oxygentank").one()
    assert tank.is_unlimited is True
    assert (tank.per_round_limit, tank.per_round_limit_min) == (20, 0)

    flare = db.query(WarehouseItem).filter(WarehouseItem.identifier == "flare").one()
    assert flare.is_unlimited is False
    assert (flare.quantity, flare.per_round_limit, flare.per_round_limit_min) == (40, 5, None)


def test_bulk_add_rejects_an_unlimited_item_without_a_ceiling(db, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import routes.warehouse as warehouse_routes

    monkeypatch.setattr(warehouse_routes, "check_permission", lambda *_args: True)

    class _Request:
        async def json(self):
            return {"items": [{"identifier": "oxygentank", "quantity": 0, "is_unlimited": True}]}

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(warehouse_routes.api_upsert_warehouse_item(
            _Request(), db=db, current_admin=SimpleNamespace(id=1),
        ))

    assert excinfo.value.status_code == 400
    assert db.query(WarehouseItem).count() == 0


def _spawn_rate(db, item, rounds=400):
    """Доля раундов, в которых предмет вообще появился."""
    hits = sum(
        1 for round_id in range(rounds)
        if warehouse_rules.spawned_this_round(db, item, "ASPER1", round_ref=f"r{round_id}")
    )
    return hits / rounds


def test_spawn_chance_roughly_matches_the_configured_percentage(db):
    db.add(WarehouseItem(identifier="flare", quantity=0, is_unlimited=True,
                         per_round_limit=10, round_spawn_chance=25))
    db.commit()
    item = db.query(WarehouseItem).first()

    assert 0.15 < _spawn_rate(db, item) < 0.35


def test_item_that_did_not_spawn_is_unavailable_this_round(db):
    db.add(WarehouseItem(identifier="flare", quantity=500, is_unlimited=True,
                         per_round_limit=10, round_spawn_chance=50))
    db.commit()
    item = db.query(WarehouseItem).first()

    missed = next(
        ref for ref in (f"r{i}" for i in range(400))
        if not warehouse_rules.spawned_this_round(db, item, "ASPER1", round_ref=ref)
    )

    assert warehouse_rules.round_limit(db, item, "ASPER1", round_ref=missed) == 0
    assert warehouse_rules.availability(db, item, "ASPER1", round_ref=missed) == 0


def test_zero_and_full_chance_are_absolute(db):
    db.add(WarehouseItem(identifier="never", quantity=0, is_unlimited=True,
                         per_round_limit=5, round_spawn_chance=0))
    db.add(WarehouseItem(identifier="always", quantity=0, is_unlimited=True,
                         per_round_limit=5))
    db.commit()
    never = db.query(WarehouseItem).filter_by(identifier="never").one()
    always = db.query(WarehouseItem).filter_by(identifier="always").one()

    assert _spawn_rate(db, never) == 0
    assert _spawn_rate(db, always) == 1


def test_raising_the_chance_never_removes_an_item_already_present(db):
    """Шанс не входит в сам бросок, поэтому правка настройки монотонна."""
    db.add(WarehouseItem(identifier="flare", quantity=0, is_unlimited=True,
                         per_round_limit=5, round_spawn_chance=30))
    db.commit()
    item = db.query(WarehouseItem).first()
    visible_at_30 = {
        ref for ref in (f"r{i}" for i in range(200))
        if warehouse_rules.spawned_this_round(db, item, "ASPER1", round_ref=ref)
    }

    item.round_spawn_chance = 80
    db.commit()
    visible_at_80 = {
        ref for ref in (f"r{i}" for i in range(200))
        if warehouse_rules.spawned_this_round(db, item, "ASPER1", round_ref=ref)
    }

    assert visible_at_30 < visible_at_80


def test_spawn_roll_is_independent_of_the_amount_roll(db):
    """Иначе редкий предмет выпадал бы всегда с одним и тем же количеством."""
    db.add(WarehouseItem(identifier="flare", quantity=0, is_unlimited=True,
                         per_round_limit=10, per_round_limit_min=1, round_spawn_chance=50))
    db.commit()
    item = db.query(WarehouseItem).first()

    amounts = {
        warehouse_rules.round_limit(db, item, "ASPER1", round_ref=f"r{i}")
        for i in range(200)
        if warehouse_rules.spawned_this_round(db, item, "ASPER1", round_ref=f"r{i}")
    }

    assert len(amounts) >= 8


def test_spawn_chance_requires_a_limit_and_is_bounded():
    assert warehouse_rules.validate_limit_settings(False, 10, None, 40) == (10, None, 40)
    # 100% ничем не отличается от отсутствия настройки.
    assert warehouse_rules.validate_limit_settings(False, 10, None, 100) == (10, None, None)
    with pytest.raises(ValueError):
        warehouse_rules.validate_limit_settings(False, None, None, 40)
    with pytest.raises(ValueError):
        warehouse_rules.validate_limit_settings(False, 10, None, 140)


def test_bulk_add_stores_the_spawn_chance(db, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import routes.warehouse as warehouse_routes

    monkeypatch.setattr(warehouse_routes, "check_permission", lambda *_args: True)

    class _Request:
        async def json(self):
            return {"items": [{
                "identifier": "flare", "quantity": 0, "is_unlimited": True,
                "per_round_limit": 12, "per_round_limit_min": 2,
                "round_spawn_chance": 35,
            }]}

    asyncio.run(warehouse_routes.api_upsert_warehouse_item(
        _Request(), db=db, current_admin=SimpleNamespace(id=1),
    ))

    row = db.query(WarehouseItem).one()
    assert (row.per_round_limit, row.per_round_limit_min, row.round_spawn_chance) == (12, 2, 35)
