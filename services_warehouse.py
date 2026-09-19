"""Availability rules for the virtual dispatch warehouse.

Two independent constraints decide how much of an item can be handed out:

* **stock** — ``WarehouseItem.quantity``, ignored for an "unlimited" line that
  the station can always produce;
* **per-round ceiling** — ``WarehouseItem.per_round_limit``, counted per server
  and per round in ``WarehouseRoundUsage``. With ``per_round_limit_min`` set,
  the ceiling is drawn anew each round from that range.

Rounds run independently on each server, so a delivery to ASPER1 must not eat
ASPER2's budget. The counter is keyed by that server's last round-start event
instead of being reset by a job: a new round simply starts counting under a new
key, which also means nothing is lost if the site was down when a round began.

The AI dispatcher never sees these two rules separately — it is told a single
"available" number (see ``availability``), because a model that is shown a
stock of 500 will offer 500 no matter what the ceiling says.
"""

import hashlib
import logging
from typing import Optional

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import RoundEvent, WarehouseItem, WarehouseRoundUsage

logger = logging.getLogger(__name__)

# Used when a server has no recorded round start yet. Deliveries made in that
# state still count, so an operator cannot reset a ceiling by restarting the
# site before the first round of the day.
NO_ROUND_REF = "no-round"


def normalize_server(server_name: Optional[str]) -> str:
    return (server_name or "").strip().upper()


def current_round_ref(db: Session, server_name: Optional[str]) -> str:
    """Identify the round a delivery to this server belongs to."""
    normalized = normalize_server(server_name)
    if not normalized:
        return NO_ROUND_REF
    last_start = (
        db.query(RoundEvent.id)
        .filter(RoundEvent.event_type == "start", RoundEvent.server_name == normalized)
        .order_by(desc(RoundEvent.created_at))
        .first()
    )
    return str(last_start.id) if last_start else NO_ROUND_REF


def issued_this_round(
    db: Session,
    identifier: str,
    server_name: Optional[str],
    round_ref: Optional[str] = None,
) -> int:
    normalized = normalize_server(server_name)
    if not normalized:
        return 0
    ref = round_ref or current_round_ref(db, normalized)
    row = (
        db.query(WarehouseRoundUsage.issued)
        .filter(
            WarehouseRoundUsage.identifier == identifier,
            WarehouseRoundUsage.server_name == normalized,
            WarehouseRoundUsage.round_ref == ref,
        )
        .first()
    )
    return int(row.issued) if row else 0


def round_limit(
    db: Session,
    item: WarehouseItem,
    server_name: Optional[str],
    round_ref: Optional[str] = None,
) -> Optional[int]:
    """The ceiling that applies to this item, on this server, this round.

    A line configured with a range draws its own ceiling every round. The draw
    is derived from (item, server, round) instead of being stored, which keeps
    it identical for every worker process and every request within the round —
    a number that changed between two reads would let a caller retry until it
    liked the answer, and would make the figure shown to the AI a lie.
    """
    ceiling = item.per_round_limit
    if ceiling is None:
        return None
    ceiling = int(ceiling)
    floor = item.per_round_limit_min
    if floor is None or int(floor) >= ceiling:
        return ceiling
    floor = max(0, int(floor))
    ref = round_ref or current_round_ref(db, server_name)
    seed = f"{item.identifier}|{normalize_server(server_name)}|{ref}|{floor}-{ceiling}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return floor + int(digest, 16) % (ceiling - floor + 1)


def remaining_this_round(
    db: Session,
    item: WarehouseItem,
    server_name: Optional[str],
    round_ref: Optional[str] = None,
) -> Optional[int]:
    """Units left under the per-round ceiling, or None when there is none."""
    ref = round_ref or current_round_ref(db, server_name)
    limit = round_limit(db, item, server_name, ref)
    if limit is None:
        return None
    used = issued_this_round(db, item.identifier, server_name, ref)
    return max(0, int(limit) - used)


def availability(
    db: Session,
    item: WarehouseItem,
    server_name: Optional[str],
    round_ref: Optional[str] = None,
) -> int:
    """How many units can actually be delivered to this server right now."""
    remaining = remaining_this_round(db, item, server_name, round_ref)
    if item.is_unlimited:
        # Stock is meaningless here; the ceiling is the only real constraint.
        # It is mandatory for unlimited lines, so `remaining` is never None,
        # but stay defensive about rows written before that rule existed.
        return remaining if remaining is not None else 0
    stock = max(0, int(item.quantity or 0))
    return stock if remaining is None else min(stock, remaining)


def availability_across_servers(
    db: Session,
    item: WarehouseItem,
    servers: tuple,
) -> dict:
    """Per-server availability plus the number safe to promise anywhere.

    A caller that does not name a server (an external integration listing the
    warehouse) gets the smallest figure: promising ASPER2's remaining budget
    for a delivery that turns out to target ASPER1 is worse than promising
    less than the station could actually ship.
    """
    per_server = {
        server: availability(db, item, server)
        for server in servers
    }
    return {
        "per_server": per_server,
        "safe": min(per_server.values()) if per_server else 0,
    }


def record_issue(
    db: Session,
    identifier: str,
    server_name: Optional[str],
    quantity: int,
    round_ref: Optional[str] = None,
) -> str:
    """Count issued units against the round ceiling. Returns the round ref.

    The row is locked (or created) so two simultaneous orders cannot both pass
    the ceiling check and then overshoot it.
    """
    normalized = normalize_server(server_name)
    ref = round_ref or current_round_ref(db, normalized)
    row = (
        db.query(WarehouseRoundUsage)
        .filter(
            WarehouseRoundUsage.identifier == identifier,
            WarehouseRoundUsage.server_name == normalized,
            WarehouseRoundUsage.round_ref == ref,
        )
        .with_for_update()
        .first()
    )
    if row is None:
        row = WarehouseRoundUsage(
            identifier=identifier,
            server_name=normalized,
            round_ref=ref,
            issued=0,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # Another worker inserted the same key first; take its row.
            db.rollback()
            row = (
                db.query(WarehouseRoundUsage)
                .filter(
                    WarehouseRoundUsage.identifier == identifier,
                    WarehouseRoundUsage.server_name == normalized,
                    WarehouseRoundUsage.round_ref == ref,
                )
                .with_for_update()
                .first()
            )
            if row is None:
                raise
    row.issued = int(row.issued or 0) + int(quantity)
    return ref


def release_issue(
    db: Session,
    identifier: str,
    server_name: Optional[str],
    quantity: int,
    round_ref: Optional[str],
) -> None:
    """Give back budget for a delivery the game server never accepted."""
    normalized = normalize_server(server_name)
    if not round_ref:
        return
    row = (
        db.query(WarehouseRoundUsage)
        .filter(
            WarehouseRoundUsage.identifier == identifier,
            WarehouseRoundUsage.server_name == normalized,
            WarehouseRoundUsage.round_ref == round_ref,
        )
        .with_for_update()
        .first()
    )
    if row is None:
        return
    row.issued = max(0, int(row.issued or 0) - int(quantity))


def validate_limit_settings(
    is_unlimited: bool,
    per_round_limit,
    per_round_limit_min=None,
) -> tuple:
    """Normalize the settings, or raise ValueError describing what is wrong.

    Returns ``(per_round_limit, per_round_limit_min)``; the lower bound is None
    whenever the ceiling is the same every round.
    """
    if per_round_limit in (None, ""):
        normalized_limit = None
    else:
        if isinstance(per_round_limit, bool) or not isinstance(per_round_limit, int):
            raise ValueError("Лимит на раунд должен быть целым числом")
        if per_round_limit < 1:
            raise ValueError("Лимит на раунд должен быть больше нуля")
        normalized_limit = int(per_round_limit)
    if is_unlimited and normalized_limit is None:
        raise ValueError("Для бесконечного предмета лимит на раунд обязателен")

    if per_round_limit_min in (None, ""):
        normalized_min = None
    else:
        if isinstance(per_round_limit_min, bool) or not isinstance(per_round_limit_min, int):
            raise ValueError("Нижняя граница лимита должна быть целым числом")
        if per_round_limit_min < 0:
            raise ValueError("Нижняя граница лимита не может быть отрицательной")
        normalized_min = int(per_round_limit_min)

    if normalized_min is not None:
        if normalized_limit is None:
            raise ValueError("Случайный лимит требует верхнюю границу")
        if normalized_min > normalized_limit:
            raise ValueError("Нижняя граница лимита больше верхней")
        if normalized_min == normalized_limit:
            # Not a range at all — store it as a plain fixed ceiling.
            normalized_min = None
    return normalized_limit, normalized_min
