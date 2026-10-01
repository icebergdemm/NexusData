from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Callable, Iterable

from database import SessionLocal
from models import AddressDetectorChannelState, AddressDetectorIncident, AddressDetectorSignal
from sqlalchemy.exc import IntegrityError

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


def _compact_context(context: AddressContext) -> dict:
    speakers: dict[int | None, str] = {context.author_id: "s1"}

    def speaker(author_id: int | None) -> str:
        if author_id not in speakers:
            speakers[author_id] = f"s{len(speakers) + 1}"
        return speakers[author_id]

    ordered: list[MessageSnapshot] = []
    if context.reply is not None:
        ordered.append(context.reply)
    ordered.extend(context.previous_messages)
    # Keep one copy when the referenced message also appears in channel history.
    unique: list[MessageSnapshot] = []
    seen: set[int] = set()
    for item in ordered:
        if item.message_id not in seen and item.message_id != context.message_id:
            seen.add(item.message_id)
            unique.append(item)
    unique = unique[:4]
    for item in unique:
        speaker(item.author_id)
    for value in context.mentions:
        if str(value).isdigit():
            speaker(int(value))
    index_by_id = {item.message_id: i + 1 for i, item in enumerate(unique)}

    discord_user_mention = re.compile(r"<@!?([0-9]+)>")
    discord_role_mention = re.compile(r"<@&[0-9]+>")
    discord_channel_mention = re.compile(r"<#([0-9]+)>")
    discord_message_url = re.compile(r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/[0-9]+/[0-9]+/[0-9]+", re.IGNORECASE)

    def safe_text(value: str, *, current: bool = False) -> str:
        value = discord_message_url.sub("[discord link]", value)
        value = discord_user_mention.sub(
            lambda match: f"<@{speaker(int(match.group(1))) if current else speakers.get(int(match.group(1)), 'participant')}>",
            value,
        )
        value = discord_role_mention.sub("<@role>", value)
        return discord_channel_mention.sub("<#channel>", value)

    context_rows = [
        {"index": i + 1, "speaker": speaker(item.author_id), "text": safe_text(item.content)}
        for i, item in enumerate(unique)
    ]
    mentions = [speaker(int(value)) for value in context.mentions if str(value).isdigit()]
    reply_to = index_by_id.get(context.reply.message_id) if context.reply else None
    return {
        "current": {
            "speaker": "s1",
            "text": safe_text(context.raw_content, current=True),
            "reply_to": reply_to,
            "mentions": list(dict.fromkeys(mentions)),
        },
        "context": context_rows,
    }


def _snapshot_json(context: AddressContext, revision: int = 1) -> dict:
    return {
        "current_revision": revision,
        "jump_url": context.jump_url,
        "revisions": {str(revision): {"classifier_context": None}},
    }


def _revision(snapshot: dict | None) -> int:
    try:
        return max(1, int((snapshot or {}).get("current_revision", 1)))
    except (TypeError, ValueError):
        return 1


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
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return False
            return True
        finally:
            db.close()

    def update_candidate(self, context: AddressContext, tokens: Iterable[str]) -> int:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(context.message_id)).with_for_update().one_or_none()
            if signal is None:
                self.upsert_candidate(context, tokens)
                return 1
            revision = _revision(signal.context_snapshot) + 1
            signal.author_display_name = context.author_display_name
            signal.created_at = _as_naive_utc(context.timestamp)
            signal.raw_content = context.raw_content
            signal.cleaned_content = context.cleaned_content
            signal.matched_tokens = list(tokens)
            snapshot = dict(signal.context_snapshot or {})
            revisions = dict(snapshot.get("revisions") or {})
            revisions[str(revision)] = {"classifier_context": None}
            signal.context_snapshot = {**snapshot, "current_revision": revision, "jump_url": context.jump_url, "revisions": revisions}
            signal.deleted_at = None
            signal.status_note = None
            signal.classification = None
            signal.confidence = None
            signal.classifier_version = None
            signal.incident_id = None
            signal.processing_status = "queued"
            db.commit()
            return revision
        finally:
            db.close()

    def set_classifier_context(self, context: AddressContext, revision: int) -> bool:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(context.message_id)).with_for_update().one_or_none()
            if signal is None or signal.deleted_at is not None or _revision(signal.context_snapshot) != revision:
                return False
            snapshot = dict(signal.context_snapshot or {})
            revisions = dict(snapshot.get("revisions") or {})
            entry = dict(revisions.get(str(revision)) or {})
            if entry.get("classifier_context") is not None:
                return True
            entry["classifier_context"] = _compact_context(context)
            revisions[str(revision)] = entry
            signal.context_snapshot = {**snapshot, "revisions": revisions}
            db.commit()
            return True
        finally:
            db.close()

    def set_classifier_request(self, context: AddressContext, revision: int, payload: dict) -> bool:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(context.message_id)).with_for_update().one_or_none()
            if signal is None or signal.deleted_at is not None or _revision(signal.context_snapshot) != revision:
                return False
            snapshot = dict(signal.context_snapshot or {})
            revisions = dict(snapshot.get("revisions") or {})
            entry = dict(revisions.get(str(revision)) or {})
            entry["classifier_context"] = payload
            from .qwen_shadow import idempotency_key
            entry["semantic_request"] = {
                "message_id": context.message_id,
                "revision": revision,
                "idempotency_key": idempotency_key(context.message_id, revision),
            }
            revisions[str(revision)] = entry
            signal.context_snapshot = {**snapshot, "revisions": revisions}
            db.commit()
            return True
        finally:
            db.close()

    def mark_terminal(self, message_id: int, revision: int, status: str, note: str) -> bool:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(message_id)).with_for_update().one_or_none()
            if signal is None or signal.deleted_at is not None or _revision(signal.context_snapshot) != revision:
                return False
            signal.processing_status = status[:32]
            signal.status_note = note[:500]
            snapshot = dict(signal.context_snapshot or {})
            revisions = dict(snapshot.get("revisions") or {})
            entry = dict(revisions.get(str(revision)) or {})
            if status in {"classification_failed", "queue_overflow", "stopping"}:
                entry["semantic_analysis"] = {"status": status, "error": note[:120]}
                revisions[str(revision)] = entry
                signal.context_snapshot = {**snapshot, "revisions": revisions}
            db.commit()
            return True
        finally:
            db.close()

    def record_analysis(
        self, context: AddressContext, result: AnalysisResult, window_seconds: int, revision: int = 1
    ) -> AnalysisRecord | None:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(context.message_id)).with_for_update().one_or_none()
            if signal is None or signal.deleted_at is not None or _revision(signal.context_snapshot) != revision:
                return None
            signal.classification = result.verdict.value
            signal.confidence = result.confidence
            signal.classifier_version = result.classifier_version
            signal.processing_status = "classified"
            signal.status_note = result.reason
            snapshot = dict(signal.context_snapshot or {})
            revisions = dict(snapshot.get("revisions") or {})
            entry = dict(revisions.get(str(revision)) or {})
            entry["semantic_analysis"] = {
                "status": "completed",
                "message_id": context.message_id,
                "revision": revision,
                "classification": result.classification,
                "reason_code": result.reason_code,
                "verdict": result.verdict.value,
                "ticket_id": result.ticket_id,
                "idempotency_key": result.idempotency_key,
                "queue_wait_ms": result.queue_wait_ms,
                "backend_ms": result.backend_ms,
                "total_ms": result.total_ms,
                "request_chars": result.request_chars,
            }
            revisions[str(revision)] = entry
            signal.context_snapshot = {**snapshot, "revisions": revisions}
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

    def mark_deleted(self, message_id: int) -> bool:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(message_id)).with_for_update().one_or_none()
            if signal is not None:
                snapshot = dict(signal.context_snapshot or {})
                snapshot["current_revision"] = _revision(snapshot) + 1
                signal.context_snapshot = snapshot
                signal.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
                signal.status_note = "Исходное сообщение удалено автором/недоступно"
                signal.processing_status = "deleted"
                db.commit()
                return True
            return False
        finally:
            db.close()

    def mark_edit_clean(self, message_id: int, context: AddressContext | None = None) -> bool:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(message_id)).with_for_update().one_or_none()
            if signal is not None:
                snapshot = dict(signal.context_snapshot or {})
                revision = _revision(snapshot) + 1
                revisions = dict(snapshot.get("revisions") or {})
                revisions[str(revision)] = {"classifier_context": None}
                signal.context_snapshot = {**snapshot, "current_revision": revision, "revisions": revisions}
                if context is not None:
                    signal.author_display_name = context.author_display_name
                    signal.raw_content = context.raw_content
                    signal.cleaned_content = context.cleaned_content
                    signal.matched_tokens = list(context.trigger_tokens)
                signal.classification = Verdict.CLEAN.value
                signal.processing_status = "clean"
                signal.status_note = "Исходное сообщение изменено; текущая версия больше не срабатывает"
                db.commit()
                return True
            return False
        finally:
            db.close()

    def set_shadow(self, message_id: int, revision: int, value: dict) -> bool:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(message_id)).with_for_update().one_or_none()
            if signal is None or signal.deleted_at is not None or _revision(signal.context_snapshot) != revision:
                return False
            snapshot = dict(signal.context_snapshot or {})
            revisions = dict(snapshot.get("revisions") or {})
            entry = dict(revisions.get(str(revision)) or {})
            entry["shadow"] = dict(value)
            revisions[str(revision)] = entry
            signal.context_snapshot = {**snapshot, "revisions": revisions}
            db.commit()
            return True
        finally:
            db.close()

    def is_current(self, message_id: int, revision: int) -> bool:
        db = self.session_factory()
        try:
            signal = db.query(AddressDetectorSignal).filter_by(message_id=str(message_id)).one_or_none()
            return bool(signal is not None and signal.deleted_at is None and _revision(signal.context_snapshot) == revision)
        finally:
            db.close()

    def pending_shadow(self) -> list[dict]:
        db = self.session_factory()
        try:
            rows = db.query(AddressDetectorSignal).filter(AddressDetectorSignal.deleted_at.is_(None)).all()
            pending = []
            for signal in rows:
                snapshot = signal.context_snapshot or {}
                for revision_text, entry in (snapshot.get("revisions") or {}).items():
                    shadow = entry.get("shadow") or {}
                    if shadow.get("status") in {"submitting", "queued", "running"}:
                        pending.append({
                            "message_id": int(signal.message_id),
                            "revision": int(revision_text),
                            "context": entry.get("classifier_context"),
                            **shadow,
                        })
            return pending
        finally:
            db.close()

    def background_states(self) -> list[dict]:
        db = self.session_factory()
        try:
            return [{
                "channel_id": row.channel_id,
                "last_message_id": row.last_message_id,
                "pending_batch": row.pending_batch,
            } for row in db.query(AddressDetectorChannelState).all()]
        finally:
            db.close()

    def pending_candidates(self) -> list[dict]:
        db = self.session_factory()
        try:
            return [{
                "message_id": int(row.message_id),
                "channel_id": int(row.channel_id),
                "revision": _revision(row.context_snapshot),
                "content": row.raw_content,
                "tokens": list(row.matched_tokens or []),
            } for row in db.query(AddressDetectorSignal).filter(
                AddressDetectorSignal.deleted_at.is_(None),
                AddressDetectorSignal.processing_status == "queued",
            ).all()]
        finally:
            db.close()

    def save_background_pending(self, channel_id: int, pending_batch: dict) -> None:
        db = self.session_factory()
        try:
            row = db.query(AddressDetectorChannelState).filter_by(channel_id=str(channel_id)).with_for_update().one_or_none()
            if row is None:
                row = AddressDetectorChannelState(channel_id=str(channel_id))
                db.add(row)
            row.pending_batch = pending_batch
            db.commit()
        finally:
            db.close()

    def complete_background_batch(self, channel_id: int, last_message_id: int) -> None:
        db = self.session_factory()
        try:
            row = db.query(AddressDetectorChannelState).filter_by(channel_id=str(channel_id)).with_for_update().one_or_none()
            if row is None:
                row = AddressDetectorChannelState(channel_id=str(channel_id))
                db.add(row)
            row.last_message_id = str(last_message_id)
            row.pending_batch = None
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

    def get_incident(self, incident_id: int) -> dict | None:
        db = self.session_factory()
        try:
            incident = db.query(AddressDetectorIncident).filter_by(id=incident_id).one_or_none()
            if incident is None:
                return None
            signals = (
                db.query(AddressDetectorSignal)
                .filter_by(incident_id=incident_id)
                .order_by(AddressDetectorSignal.created_at.desc())
                .limit(3)
                .all()
            )
            return {
                "id": int(incident.id),
                "guild_id": incident.guild_id,
                "channel_id": incident.channel_id,
                "author_id": incident.author_id,
                "admin_message_id": incident.admin_message_id,
                "message_count": int(incident.message_count or 0),
                "signals": [
                    {
                        "message_id": int(signal.message_id),
                        "raw_content": signal.raw_content,
                        "classification": signal.classification,
                        "confidence": signal.confidence,
                        "matched_tokens": list(signal.matched_tokens or []),
                        "jump_url": (signal.context_snapshot or {}).get("jump_url"),
                        "status_note": signal.status_note,
                    }
                    for signal in signals
                ],
            }
        finally:
            db.close()
