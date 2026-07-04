"""
Personal admin notification system.

Designed for scale:
  - Polling via GET /api/v1/notifications (lightweight, just unread count + recent)
  - Full list via GET /api/v1/notifications/all (paginated)
  - Mark read via POST /api/v1/notifications/read
  - Auto-expiry: notifications expire after 7 days
  - No WebSockets needed — simple long-poll friendly endpoint

Notification sources:
  - Server action ACK (ban/kick executed or failed)
  - Rating recalculation complete
  - System alerts (task failures)
  - Manual (admin-to-admin messages)
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional, List

from database import get_db
from models import AdminNotification, AdminUser, AdminSession

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

_NOTIF_TTL_DAYS = 7


def _get_admin_id(request: Request, db: Session) -> int:
    """Fast admin_id lookup from cookie — no full ORM load."""
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=401)
    now = datetime.utcnow()
    session = db.query(AdminSession).filter(
        AdminSession.token == token, AdminSession.revoked == 0
    ).first()
    if not session or (session.expires_at and session.expires_at < now):
        raise HTTPException(status_code=401)
    return session.admin_id


# ── Public helpers (used by other routes to create notifications) ─────────────

def create_notification(
    db: Session,
    admin_id: int,
    title: str,
    body: Optional[str] = None,
    ntype: str = "info",
    category: str = "general",
    link: Optional[str] = None,
    icon: Optional[str] = None,
    ttl_days: int = _NOTIF_TTL_DAYS,
):
    """Create a notification for an admin. Call from any route."""
    expires = datetime.utcnow() + timedelta(days=ttl_days)
    notif = AdminNotification(
        admin_id=admin_id,
        type=ntype,
        category=category,
        title=title,
        body=body,
        link=link,
        icon=icon,
        expires_at=expires,
    )
    db.add(notif)
    db.commit()
    return notif


def cleanup_expired(db: Session):
    """Delete expired notifications. Call periodically."""
    db.query(AdminNotification).filter(
        AdminNotification.expires_at < datetime.utcnow()
    ).delete()
    db.commit()


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.get("/poll")
async def poll_notifications(request: Request, db: Session = Depends(get_db)):
    """
    Lightweight polling endpoint — returns unread count + up to 5 recent.
    Called every 15s by the frontend. Very cheap: indexed query on (admin_id, is_read).
    """
    admin_id = _get_admin_id(request, db)
    now = datetime.utcnow()

    unread_count = db.query(AdminNotification).filter(
        AdminNotification.admin_id == admin_id,
        AdminNotification.is_read == 0,
        (AdminNotification.expires_at == None) | (AdminNotification.expires_at > now),
    ).count()

    recent = db.query(AdminNotification).filter(
        AdminNotification.admin_id == admin_id,
        (AdminNotification.expires_at == None) | (AdminNotification.expires_at > now),
    ).order_by(AdminNotification.created_at.desc()).limit(5).all()

    return JSONResponse({
        "unread": unread_count,
        "recent": [_serialize(n) for n in recent],
    })


@router.get("/all")
async def list_notifications(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    unread_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Paginated full notification list for the bell panel."""
    admin_id = _get_admin_id(request, db)
    now = datetime.utcnow()

    q = db.query(AdminNotification).filter(
        AdminNotification.admin_id == admin_id,
        (AdminNotification.expires_at == None) | (AdminNotification.expires_at > now),
    )
    if unread_only:
        q = q.filter(AdminNotification.is_read == 0)

    total = q.count()
    items = q.order_by(AdminNotification.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return JSONResponse({
        "total": total,
        "page": page,
        "items": [_serialize(n) for n in items],
    })


@router.post("/read")
async def mark_read(request: Request, db: Session = Depends(get_db)):
    """Mark notification(s) as read. Body: {ids: [1,2,3]} or {all: true}."""
    admin_id = _get_admin_id(request, db)
    body = await request.json()

    if body.get("all"):
        db.query(AdminNotification).filter(
            AdminNotification.admin_id == admin_id,
            AdminNotification.is_read == 0,
        ).update({"is_read": 1})
    elif ids := body.get("ids"):
        db.query(AdminNotification).filter(
            AdminNotification.admin_id == admin_id,
            AdminNotification.id.in_(ids),
        ).update({"is_read": 1}, synchronize_session=False)

    db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/clear")
async def clear_read(request: Request, db: Session = Depends(get_db)):
    """Delete all read notifications for the current admin."""
    admin_id = _get_admin_id(request, db)
    db.query(AdminNotification).filter(
        AdminNotification.admin_id == admin_id,
        AdminNotification.is_read == 1,
    ).delete()
    db.commit()
    return JSONResponse({"status": "ok"})


def _serialize(n: AdminNotification) -> dict:
    return {
        "id":         n.id,
        "type":       n.type,
        "category":   n.category,
        "title":      n.title,
        "body":       n.body,
        "link":       n.link,
        "icon":       n.icon or _default_icon(n.type),
        "is_read":    bool(n.is_read),
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _default_icon(ntype: str) -> str:
    return {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(ntype, "🔔")
