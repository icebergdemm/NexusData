"""Domain helpers for the NexusData ticket lifecycle."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Query, Session

from models import DiscordMember, Ticket, TicketMessage


DELETED_TICKET_RETENTION_DAYS = 30


class TicketStateError(ValueError):
    """Raised when a ticket action does not match the ticket lifecycle."""


def apply_ticket_status_filter(query: Query, status: str) -> Query:
    """Apply the public ticket-list status without leaking deleted tickets."""
    if status == "deleted":
        return query.filter(Ticket.deleted_at.is_not(None))

    query = query.filter(Ticket.deleted_at.is_(None))
    if status == "open":
        return query.filter(Ticket.status != "closed")
    if status == "closed":
        return query.filter(Ticket.status == "closed")
    return query


def soft_delete_ticket(
    db: Session,
    ticket_id: int,
    deleted_by: str,
    *,
    now: datetime | None = None,
) -> Ticket:
    """Mark one closed ticket deleted, without touching Discord or messages."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).with_for_update().first()
    if ticket is None:
        raise TicketStateError("Тикет не найден")
    if ticket.deleted_at is not None:
        raise TicketStateError("Тикет уже удалён")
    if ticket.status != "closed":
        raise TicketStateError("Удалять можно только закрытые тикеты")

    ticket.deleted_at = now or datetime.utcnow()
    ticket.deleted_by = deleted_by
    db.flush()
    return ticket


def restore_ticket(db: Session, ticket_id: int) -> Ticket:
    """Restore one soft-deleted ticket without touching Discord."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).with_for_update().first()
    if ticket is None:
        raise TicketStateError("Тикет не найден")
    if ticket.deleted_at is None:
        raise TicketStateError("Тикет не удалён")

    ticket.deleted_at = None
    ticket.deleted_by = None
    db.flush()
    return ticket


def purge_deleted_tickets(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> dict:
    """Permanently remove expired soft-deleted tickets and their messages."""
    cutoff = (now or datetime.utcnow()) - timedelta(days=DELETED_TICKET_RETENTION_DAYS)
    tickets = (
        db.query(Ticket)
        .filter(Ticket.deleted_at.is_not(None), Ticket.deleted_at <= cutoff)
        .order_by(Ticket.deleted_at, Ticket.id)
        .limit(max(1, min(limit, 500)))
        .all()
    )

    deleted_messages = 0
    try:
        for ticket in tickets:
            deleted_messages += (
                db.query(TicketMessage)
                .filter(TicketMessage.ticket_id == ticket.id)
                .delete(synchronize_session=False)
            )
            db.delete(ticket)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "status": "ok",
        "deleted_tickets": len(tickets),
        "deleted_messages": deleted_messages,
    }


def search_ticket_members(
    db: Session,
    search: str = "",
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[DiscordMember]]:
    """Return the cached server members used by the ticket picker."""
    query = db.query(DiscordMember)
    value = (search or "").strip()
    if value:
        like = f"%{value}%"
        query = query.filter(
            (DiscordMember.username.ilike(like))
            | (DiscordMember.display_name.ilike(like))
        )
    total = query.count()
    rows = (
        query.order_by(DiscordMember.display_name, DiscordMember.username)
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return total, rows
