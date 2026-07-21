"""
Virtual dispatch resource warehouse — a shared stock of item quantities
managed from the Диспетчерская page (item identifiers come from the same
site-wide catalog as "Обзор мира"'s inventory display, see
static/item_catalog.json / tools/extract_barotrauma_items.py). Gated by the
existing site-wide "dispatch" permission, not a new one — this is just
another dispatch tool, not a separate feature.

Future work (not yet built here): actually sending some of this stock out
with the supply shuttle — see dispatch/index.tsx's "Отправить челнок
снабжения" stub action, the eventual integration point.

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

logger = logging.getLogger(__name__)

admin_router = APIRouter()
client_router = APIRouter(
    prefix="/api/v1/client",
    tags=["client-api"],
    dependencies=[Depends(verify_client_api_key_dependency)],
)


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get("admin_token")
    now = datetime.utcnow()
    if token:
        session = db.query(AdminSession).filter(AdminSession.token == token, AdminSession.revoked == 0).first()
        if session and (session.expires_at is None or session.expires_at > now):
            admin = (
                db.query(AdminUser)
                .options(joinedload(AdminUser.privilege_group))
                .filter(AdminUser.id == session.admin_id)
                .first()
            )
            if admin:
                from security import compute_department_site_permissions
                admin._department_site_permissions = compute_department_site_permissions(db, admin.id)
                return admin
    raise HTTPException(status_code=401, detail="Not authenticated")


def _require_dispatch(admin: AdminUser, action: str = "view"):
    if not check_permission(admin, "dispatch", action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _serialize(w: WarehouseItem) -> dict:
    return {
        "id": w.id,
        "identifier": w.identifier,
        "quantity": w.quantity,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
        "updated_by": w.updated_by.discord_username if w.updated_by else None,
    }


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
        "items": [_serialize(w) for w in rows],
        "can_edit": check_permission(current_admin, "dispatch", "edit"),
    })


@admin_router.post("/api/v1/admin/warehouse/items")
async def api_upsert_warehouse_item(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Add a new stock line, or set the absolute quantity of an existing
    one (same endpoint — upsert by identifier)."""
    _require_dispatch(current_admin, "edit")
    body = await request.json()
    identifier = (body.get("identifier") or "").strip()
    quantity = body.get("quantity")
    if not identifier:
        raise HTTPException(status_code=400, detail="Укажите идентификатор предмета")
    if not isinstance(quantity, int) or quantity < 0:
        raise HTTPException(status_code=400, detail="Количество должно быть целым числом ≥ 0")

    row = db.query(WarehouseItem).filter(WarehouseItem.identifier == identifier).first()
    if row:
        row.quantity = quantity
    else:
        row = WarehouseItem(identifier=identifier, quantity=quantity)
        db.add(row)
    row.updated_by_admin_id = current_admin.id
    db.commit()
    db.refresh(row)
    return JSONResponse(_serialize(row))


@admin_router.patch("/api/v1/admin/warehouse/items/{identifier}")
async def api_adjust_warehouse_item(
    identifier: str,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Either {"quantity": N} — set absolute — or {"delta": N} — relative
    adjust (clamped at 0), whichever the caller provides. The +/- stepper
    in the UI uses `delta`; a manual "set exact amount" field uses `quantity`."""
    _require_dispatch(current_admin, "edit")
    row = db.query(WarehouseItem).filter(WarehouseItem.identifier == identifier).first()
    if not row:
        raise HTTPException(status_code=404, detail="Предмет не найден на складе")

    body = await request.json()
    if "quantity" in body:
        quantity = body.get("quantity")
        if not isinstance(quantity, int) or quantity < 0:
            raise HTTPException(status_code=400, detail="Количество должно быть целым числом ≥ 0")
        row.quantity = quantity
    elif "delta" in body:
        delta = body.get("delta")
        if not isinstance(delta, int):
            raise HTTPException(status_code=400, detail="delta должно быть целым числом")
        row.quantity = max(0, row.quantity + delta)
    else:
        raise HTTPException(status_code=400, detail="Укажите quantity или delta")

    row.updated_by_admin_id = current_admin.id
    db.commit()
    db.refresh(row)
    return JSONResponse(_serialize(row))


@admin_router.delete("/api/v1/admin/warehouse/items/{identifier}")
async def api_delete_warehouse_item(
    identifier: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    _require_dispatch(current_admin, "edit")
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
async def api_client_warehouse(db: Session = Depends(get_db)):
    """Current warehouse stock for external client integrations — always
    the live table contents at request time, no caching layer."""
    rows = db.query(WarehouseItem).order_by(WarehouseItem.identifier).all()
    return JSONResponse({
        "items": [{"identifier": w.identifier, "quantity": w.quantity} for w in rows],
    })
