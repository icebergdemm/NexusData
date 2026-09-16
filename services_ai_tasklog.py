"""Stage-by-stage progress log for AI tasks (AI Centre "Журнал задач").

Every AI worker (player dossiers, health
checks) appends bounded entries as its task moves through stages: claimed,
packet built, request sent, response received, validated, applied, failed.
The AI Centre renders these grouped per task so an operator can trace
progress of a specific task without tailing application logs.

Writing must never break the task it logs: all failures are swallowed.
Entries are written with their own short transaction (or the session the
caller hand in) so one task's log survives another task's rollback.
"""

import logging

logger = logging.getLogger(__name__)

TASK_KIND_DOSSIER = "dossier"
TASK_KIND_HEALTH = "health"
# Gateway clients use their client_id directly as task kind.
TASK_STAGES = {"queued", "claimed", "request_sent", "response_received", "validated", "failed", "cancelled"}
TASK_KIND_DISPATCHER = "dispatcher"


def ai_task_log(db, kind: str, ref: str, stage: str,
                message: str = "", level: str = "info") -> None:
    """Append one entry and commit it in the caller's session.

    db: SQLAlchemy session bound to the main database. Never raises."""
    from models import AITaskLog

    try:
        db.add(AITaskLog(
            task_kind=(kind or "?")[:30],
            task_ref=(ref or "?")[:120],
            stage=(stage or "?")[:40],
            level=(level or "info")[:10],
            message=(message or "")[:500],
        ))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — logging must never break tasks
        try:
            db.rollback()
        except Exception:
            pass
        logger.debug("ai_task_log write failed (kind=%s ref=%s): %s", kind, ref, exc)


def recent_ai_task_entries(db, kinds=None, limit: int = 200) -> list[dict]:
    """Newest entries, optionally filtered by task kind. Ordered newest first."""
    from models import AITaskLog

    query = db.query(AITaskLog)
    if kinds:
        query = query.filter(AITaskLog.task_kind.in_(kinds))
    rows = query.order_by(AITaskLog.id.desc()).limit(min(max(limit, 1), 500)).all()
    return [
        {
            "id": row.id,
            "kind": row.task_kind,
            "ref": row.task_ref,
            "stage": row.stage,
            "level": row.level,
            "message": row.message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
