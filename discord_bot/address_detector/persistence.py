from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from database import SessionLocal
from models import AddressDetectorIncident, AddressDetectorSignal

from .analyzer import AnalysisResult, Verdict
from .context import AddressContext, MessageSnapshot


@dataclass(frozen=True)
class AnalysisRecord:
    incident_id: int | None
    should_ping_moderators: bool
    message_count: int


def _as_naive_utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _snapshot_json(context: AddressContext) -> dict:
    def row(snapshot: MessageSnapshot) -> dict:
        return {
            "message_id": snapshot.message_id,
            "author_id": snapshot.author_id,
            "author_display_name": snapshot.author_display_name,
            "content": snapshot.content,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
            "jump_url": snapshot.jump_url,
        }

    return {
        "reply": row(context.reply) if context.reply else None,
        "reply_author_id": context.reply_author_id,
        "mentions": list(context.mentions),
        "previous_messages": [row(item) for item in context.previous_messages],
        "jump_url": context.jump_url,
    }


class AddressDetectorStore:
    def __init__(self, session_factory: Callable = SessionLocal) -> None:
        self.session_factory = session_factory

    def upsert_candidate(self, context: AddressContext, tokens: Iterable[str]) -> bool:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(context.message_id)).one_or_none()
            if signal is not None:
                return False
            signal = AddressDetectorSignal(
                message_id=str(context.message_id),
                guild_id=str(context.guild_id),
                channel_id=str(context.channel_id),
                author_id=str(context.author_id),
                author_display_name=context.author_display_name,
                created_at=_as_naive_utc(context.timestamp),
                raw_content=context.raw_content,
                cleaned_content=context.cleaned_content,
                matched_tokens=list(tokens),
                context_snapshot=_snapshot_json(context),
                processing_status="queued",
            )
            db.add(signal)
            db.commit()
            return True
        finally:
            db.close()

    def update_candidate(self, context: AddressContext, tokens: Iterable[str]) -> None:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(context.message_id)).one_or_none()
            if signal is None:
                db.close()
                self.upsert_candidate(context, tokens)
                return
            signal.author_display_name = context.author_display_name
            signal.created_at = _as_naive_utc(context.timestamp)
            signal.raw_content = context.raw_content
            signal.cleaned_content = context.cleaned_content
            signal.matched_tokens = list(tokens)
            signal.context_snapshot = _snapshot_json(context)
            signal.deleted_at = None
            signal.status_note = None
            signal.processing_status = "queued"
            db.commit()
        finally:
            db.close()

    def record_analysis(self, context: AddressContext, result: AnalysisResult, window_seconds: int) -> AnalysisRecord:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(context.message_id)).one()
            signal.classification = result.verdict.value
            signal.confidence = result.confidence
            signal.classifier_version = result.classifier_version
            signal.processing_status = "classified"
            signal.status_note = result.reason
            if result.verdict is Verdict.CLEAN:
                db.commit()
                return AnalysisRecord(None, False, 0)

            timestamp = _as_naive_utc(context.timestamp)
            cutoff = timestamp - timedelta(seconds=max(1, window_seconds))
            incident = (
                db.query(AddressDetectorIncident)
                .filter(
                    AddressDetectorIncident.guild_id == str(context.guild_id),
                    AddressDetectorIncident.channel_id == str(context.channel_id),
                    AddressDetectorIncident.author_id == str(context.author_id),
                    AddressDetectorIncident.last_seen_at >= cutoff,
                )
                .order_by(AddressDetectorIncident.last_seen_at.desc())
                .first()
            )
            should_ping = False
            if incident is None:
                incident = AddressDetectorIncident(
                    guild_id=str(context.guild_id),
                    channel_id=str(context.channel_id),
                    author_id=str(context.author_id),
                    started_at=timestamp,
                    last_seen_at=timestamp,
                    message_ids=[str(context.message_id)],
                    message_count=1,
                    moderator_pinged=True,
                )
                db.add(incident)
                should_ping = True
            else:
                ids = list(incident.message_ids or [])
                if str(context.message_id) not in ids:
                    ids.append(str(context.message_id))
                    incident.message_count = int(incident.message_count or 0) + 1
                incident.message_ids = ids[-5:]
                incident.last_seen_at = timestamp
            db.flush()
            signal.incident_id = incident.id
            db.commit()
            return AnalysisRecord(int(incident.id), should_ping, int(incident.message_count or 0))
        finally:
            db.close()

    def set_admin_message_id(self, message_id: int, admin_message_id: int) -> None:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(message_id)).one_or_none()
            if signal is not None:
                signal.admin_message_id = str(admin_message_id)
                db.commit()
            if signal is not None and signal.incident_id is not None:
                incident = db.query(AddressDetectorIncident).get(signal.incident_id)
                if incident is not None:
                    incident.admin_message_id = str(admin_message_id)
                    db.commit()
        finally:
            db.close()

    def mark_deleted(self, message_id: int) -> None:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(message_id)).one_or_none()
            if signal is not None:
                signal.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
                signal.status_note = "Исходное сообщение удалено автором/недоступно"
                signal.processing_status = "deleted"
                db.commit()
        finally:
            db.close()

    def get_signal(self, message_id: int) -> AddressDetectorSignal | None:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(message_id)).one_or_none()
            if signal is None:
                return None
            db.expunge(signal)
            return signal
        finally:
            db.close()

