"""
Virtual dispatch resource warehouse — a shared stock of item quantities
managed from the Диспетчерская page (item identifiers come from the same
site-wide catalog as "Обзор мира"'s inventory display, see
static/item_catalog.json / tools/extract_barotrauma_items.py). Gated by the
existing site-wide "dispatch" permission, not a new one — this is just
another dispatch tool, not a separate feature.

Поставка оформляется через общий `cargo_spawn` в routes/dispatch.py. Остатки
резервируются атомарно при постановке действия; если игровой сервер вернёт
неуспешный ACK, services.py один раз возвращает резерв на склад.

Two routers:
- `admin_router` (session-gated, "dispatch" permission) — the CRUD the
  Диспетчерская page itself uses.
- `client_router` (dedicated client_api_key, see security.py) — read-only
  export for external integrations, same key as routes/world_state.py's
  client export.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload

from database import get_db
from security import check_permission, verify_client_api_key_dependency
from models import AdminUser, AdminSession, WarehouseItem
from services_warehouse import (
    availability,
    availability_across_servers,
    current_round_ref,
    issued_this_round,
    remaining_this_round,
    round_limit,
    validate_limit_settings,
)

# Both live servers. Kept here rather than derived from the round table so an
# idle server still reports its own budget instead of disappearing.
DISPATCH_SERVERS = ("ASPER1", "ASPER2")

logger = logging.getLogger(__name__)

admin_router = APIRouter()
client_router = APIRouter(
    prefix="/api/v1/client",
    tags=["client-api"],
    dependencies=[Depends(verify_client_api_key_dependency)],
)


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    from routes.admin import load_current_admin
    return load_current_admin(request, db)


def _require_dispatch(admin: AdminUser, action: str = "view"):
    if not check_permission(admin, "dispatch", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _item_name(identifier: str) -> str:
    """Русское название предмета по идентификатору — тот же справочник, что
    и у «Обзор мира»/страницы складов (static/item_catalog.json). Отдаём его
    прямо в ответе склада: раньше внешняя интеграция и человек, читающий JSON
    руками, видели только identifier вроде "railgunshell" и должны были сами
    держать под рукой каталог, чтобы понять, что это «Снаряд рельсотрона»."""
    from routes.world_state import _load_item_catalog
    entry = _load_item_catalog().get(identifier)
    return (entry or {}).get("name") or identifier


def _serialize(w: WarehouseItem, db: Session | None = None) -> dict:
    payload = {
        "id": w.id,
        "identifier": w.identifier,
        "name": _item_name(w.identifier),
        "quantity": w.quantity,
        "is_unlimited": bool(w.is_unlimited),
        "per_round_limit": w.per_round_limit,
        "per_round_limit_min": w.per_round_limit_min,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
        "updated_by": w.updated_by.discord_username if w.updated_by else None,
    }
    if db is not None:
        # The operator needs to see how much of the ceiling this round already
        # consumed on each server, not just the configured number.
        payload["rounds"] = {
            server: {
                "issued": issued_this_round(db, w.identifier, server),
                "remaining": remaining_this_round(db, w, server),
                "available": availability(db, w, server),
                # For a randomised range this is the ceiling actually drawn for
                # the current round, not the configured maximum.
                "limit": round_limit(db, w, server),
            }
            for server in DISPATCH_SERVERS
        }
    return payload


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN — Диспетчерская → Склад
# ══════════════════════════════════════════════════════════════════════════════

@admin_router.get("/api/v1/admin/warehouse")
async def api_list_warehouse(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_dispatch(current_admin, "view")
    rows = (
        db.query(WarehouseItem)
        .options(joinedload(WarehouseItem.updated_by))
        .order_by(WarehouseItem.identifier)
        .all()
    )
    return JSONResponse({
        "items": [_serialize(w, db) for w in rows],
        "servers": list(DISPATCH_SERVERS),
        "can_edit": check_permission(current_admin, "dispatch", "manage_warehouse"),
    })


@admin_router.post("/api/v1/admin/warehouse/items")
async def api_upsert_warehouse_item(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Add stock lines, or set the absolute quantity of existing ones.

    Accepts either one item or ``{"items": [...]}``: picking twenty things out
    of a 900-entry catalog one dialog at a time was the slowest part of setting
    up a warehouse.
    """
    _require_dispatch(current_admin, "manage_warehouse")
    body = await request.json()
    entries = body.get("items") if isinstance(body.get("items"), list) else [body]
    if not entries:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один предмет")

    saved = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=400, detail="Некорректный предмет в списке")
        identifier = (entry.get("identifier") or "").strip()
        quantity = entry.get("quantity", 0)
        if not identifier:
            raise HTTPException(status_code=400, detail="Укажите идентификатор предмета")
        if len(identifier) > 100:
            raise HTTPException(status_code=400, detail="Идентификатор длиннее 100 символов")
        if identifier in seen:
            raise HTTPException(status_code=400, detail=f"Предмет «{identifier}» указан дважды")
        seen.add(identifier)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise HTTPException(status_code=400, detail="Количество должно быть целым числом ≥ 0")
        try:
            per_round_limit, per_round_limit_min = validate_limit_settings(
                bool(entry.get("is_unlimited")),
                entry.get("per_round_limit"),
                entry.get("per_round_limit_min"),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

        row = db.query(WarehouseItem).filter(WarehouseItem.identifier == identifier).first()
        if not row:
            row = WarehouseItem(identifier=identifier)
            db.add(row)
        row.quantity = quantity
        row.is_unlimited = bool(entry.get("is_unlimited"))
        row.per_round_limit = per_round_limit
        row.per_round_limit_min = per_round_limit_min
        row.updated_by_admin_id = current_admin.id
        saved.append(row)

    db.commit()
    for row in saved:
        db.refresh(row)
    items = [_serialize(row, db) for row in saved]
    return JSONResponse(items[0] if len(items) == 1 and "items" not in body else {"items": items})


@admin_router.patch("/api/v1/admin/warehouse/items/{identifier}")
async def api_adjust_warehouse_item(
    identifier: str,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Update one stock line.

    ``{"quantity": N}`` sets the absolute amount (what the UI sends once the
    operator stops clicking), ``{"delta": N}`` adjusts relatively, and
    ``is_unlimited``/``per_round_limit`` change the availability rules. All of
    them may arrive in the same request.
    """
    _require_dispatch(current_admin, "manage_warehouse")
    row = db.query(WarehouseItem).filter(WarehouseItem.identifier == identifier).first()
    if not row:
        raise HTTPException(status_code=404, detail="Предмет не найден на складе")

    body = await request.json()
    touches_limits = (
        "is_unlimited" in body
        or "per_round_limit" in body
        or "per_round_limit_min" in body
    )
    if not touches_limits and "quantity" not in body and "delta" not in body:
        raise HTTPException(status_code=400, detail="Укажите quantity, delta или настройки лимита")

    if "quantity" in body:
        quantity = body.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise HTTPException(status_code=400, detail="Количество должно быть целым числом ≥ 0")
        row.quantity = quantity
    elif "delta" in body:
        delta = body.get("delta")
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise HTTPException(status_code=400, detail="delta должно быть целым числом")
        row.quantity = max(0, row.quantity + delta)

    if touches_limits:
        is_unlimited = bool(body.get("is_unlimited", row.is_unlimited))
        raw_limit = body.get("per_round_limit", row.per_round_limit)
        raw_min = body.get("per_round_limit_min", row.per_round_limit_min)
        try:
            limit, limit_min = validate_limit_settings(is_unlimited, raw_limit, raw_min)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        row.per_round_limit = limit
        row.per_round_limit_min = limit_min
        row.is_unlimited = is_unlimited

    row.updated_by_admin_id = current_admin.id
    db.commit()
    db.refresh(row)
    return JSONResponse(_serialize(row, db))


@admin_router.delete("/api/v1/admin/warehouse/items/{identifier}")
async def api_delete_warehouse_item(
    identifier: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_dispatch(current_admin, "manage_warehouse")
    row = db.query(WarehouseItem).filter(WarehouseItem.identifier == identifier).first()
    if not row:
        raise HTTPException(status_code=404, detail="Предмет не найден на складе")
    db.delete(row)
    db.commit()
    return JSONResponse({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT — read-only export (dedicated client_api_key, see security.py)
# ══════════════════════════════════════════════════════════════════════════════

@client_router.get("/warehouse")
async def api_client_warehouse(
    server_name: str | None = None,
    server: str | None = None,
    db: Session = Depends(get_db),
):
    """Warehouse availability for external client integrations (the AI).

    ``quantity`` is deliberately not the raw stock: it is how much can still be
    delivered to ``server_name`` this round, once the per-round ceiling is
    taken into account. A model shown "500 in stock" offers 500 and then has
    its order rejected, so the single number it reads has to be the real one.
    Without a server the smallest per-server figure is returned, because the
    caller has not yet said where the delivery would go.
    """
    requested = (server_name or server or "").strip().upper() or None
    if requested and requested not in DISPATCH_SERVERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервер")

    rows = db.query(WarehouseItem).order_by(WarehouseItem.identifier).all()
    items = []
    for w in rows:
        spread = availability_across_servers(db, w, DISPATCH_SERVERS)
        effective = (
            availability(db, w, requested) if requested else spread["safe"]
        )
        items.append({
            "identifier": w.identifier,
            "name": _item_name(w.identifier),
            # Effective availability — what may actually be ordered.
            "quantity": effective,
            "available": effective,
            "stock": w.quantity,
            "is_unlimited": bool(w.is_unlimited),
            # The ceiling in force right now. For a randomised range the
            # configured maximum would over-promise, so it is not sent.
            "per_round_limit": round_limit(db, w, requested) if requested else w.per_round_limit,
            "available_by_server": spread["per_server"],
        })
    return JSONResponse({"server_name": requested, "items": items})
