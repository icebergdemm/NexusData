"""
Round replays service: handles .barodemo / .barodemo.zip uploads, lightweight
header parsing, retention cleanup, and runtime settings.

Design goal: MINIMAL server load.
- Files are stored on disk AS-IS (already compressed .barodemo.zip preferred).
- We do NOT decompress or fully parse demos on the server. The browser downloads
  the compressed archive and unpacks + parses it client-side.
- We only read the small binary header (magic, version, metadata) to populate
  DB columns for the listing — and only if the file is a raw .barodemo. For
  zipped files we read just the local file header to confirm it's a zip.
"""

import os
import io
import json
import re
import struct
import logging
import queue
import threading
import time
import zipfile
import shutil
from collections import Counter
from datetime import datetime, timedelta
from typing import Iterator, Optional, List

from sqlalchemy.orm import Session

from config import get_settings
from models import RoundReplay, AppSetting

logger = logging.getLogger(__name__)
settings = get_settings()

# Settings keys stored in app_settings table (override config defaults)
SETTING_RETENTION_DAYS = "replays_retention_days"
SETTING_UPLOAD_DIR = "replays_upload_dir"

_MAGIC_DEMO = b"BDMO"
_MAGIC_ZIP = b"PK\x03\x04"


def _queue_voice_index(replay: RoundReplay) -> None:
    """Hand indexing to the scheduler after the replay row is committed."""
    if not replay.has_voice:
        return
    try:
        from services_voice_index import queue_replay_voice_index

        queue_replay_voice_index(replay.id)
    except Exception:
        logger.exception("Failed to queue voice index for replay %s", replay.id)


# Action-specific extraction keeps item resolution deterministic.  Server
# logs are intentionally stored verbatim, so the replay UI needs a small
# adapter before it can show an inventory icon next to an event.  We extract
# only the object position of known Barotrauma log phrases; arbitrary words in
# chat/diagnostics are never guessed to be items.
_TIMELINE_ITEM_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "picked_up": (re.compile(r"\bpicked up (.+)$", re.IGNORECASE),),
    "placed": (re.compile(r"\bplaced (.+?) in the inventory of\b", re.IGNORECASE),),
    "removed": (re.compile(r"\bremoved (.+?) from the inventory of\b", re.IGNORECASE),),
    "equipped": (re.compile(r"\bequipped (.+)$", re.IGNORECASE),),
    "unequipped": (re.compile(r"\bunequipped (.+)$", re.IGNORECASE),),
    "dropped": (re.compile(r"\bdropped (.+)$", re.IGNORECASE),),
    "threw": (re.compile(r"\bthrew (.+)$", re.IGNORECASE),),
    "used": (
        re.compile(r"\bused (?:item )?(.+?) on\b", re.IGNORECASE),
        re.compile(r"\bused item (.+)$", re.IGNORECASE),
        re.compile(r"\bused (.+)$", re.IGNORECASE),
    ),
    "opened": (re.compile(r"\bopened (.+)$", re.IGNORECASE),),
    "closed": (re.compile(r"\bclosed (.+)$", re.IGNORECASE),),
    "activated": (re.compile(r"\bactivated (.+)$", re.IGNORECASE),),
    "deactivated": (re.compile(r"\bdeactivated (.+)$", re.IGNORECASE),),
    "repair": (re.compile(r"\bstarted repairing (.+)$", re.IGNORECASE),),
    "repair_failed": (re.compile(r"\bfailed to repair (.+)$", re.IGNORECASE),),
    "fabrication": (re.compile(r"\bstarted fabricating (.+)$", re.IGNORECASE),),
    "fabrication_cancel": (
        re.compile(r"\bcancelled the fabrication of (.+)$", re.IGNORECASE),
    ),
    "attack": (re.compile(r"\blaunched (.+)$", re.IGNORECASE),),
    "settings_change": (
        re.compile(r'\bof the item ["“](.+?)["”]\s+to\b', re.IGNORECASE),
    ),
}
_TIMELINE_TARGET_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "placed": (re.compile(r"\bin the inventory of (.+)$", re.IGNORECASE),),
    "removed": (re.compile(r"\bfrom the inventory of (.+)$", re.IGNORECASE),),
    "used": (re.compile(r"\bused .+? on (.+)$", re.IGNORECASE),),
}
_ITEM_ID_SUFFIX_RE = re.compile(r"\s*\(ID:\s*\d+\)\s*$", re.IGNORECASE)
_ITEM_PROJECTILE_SUFFIX_RE = re.compile(r"\s*\(projectile:.*\)\s*$", re.IGNORECASE)
_item_catalog_index_lock = threading.Lock()
_item_catalog_index: tuple[float, dict[str, tuple[str, str, bool]], list[str]] | None = None


def _normalise_item_label(value: str) -> str:
    """Canonical comparison key for localized prefab display names."""
    value = value.casefold().replace("ё", "е")
    value = value.replace("«", '"').replace("»", '"')
    return " ".join(value.split()).strip(" \t\r\n.,:;!?'\"")


def _load_timeline_item_index() -> tuple[dict[str, tuple[str, str, bool]], list[str]]:
    """Return localized-name/identifier lookup for replay log enrichment.

    The catalog is generated from the game content and already powers every
    other item icon in NexusData.  Keeping the reverse index here avoids a
    second source of truth and makes modded items work after the catalog is
    regenerated.
    """
    global _item_catalog_index
    # The catalog is deployment-time data. Avoid a stat + lock for every one
    # of tens of thousands of timeline rows; a process restart is what makes
    # a newly deployed catalog authoritative anyway.
    if _item_catalog_index is not None:
        return _item_catalog_index[1], _item_catalog_index[2]
    catalog_path = os.path.join(os.path.dirname(__file__), "static", "item_catalog.json")
    try:
        mtime = os.path.getmtime(catalog_path)
    except OSError:
        return {}, []

    with _item_catalog_index_lock:
        if _item_catalog_index is not None and _item_catalog_index[0] == mtime:
            return _item_catalog_index[1], _item_catalog_index[2]
        try:
            with open(catalog_path, "r", encoding="utf-8") as fh:
                catalog = json.load(fh)
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to load item catalog for replay timeline")
            return {}, []

        index: dict[str, tuple[str, str, bool]] = {}
        for identifier, payload in catalog.items():
            if not isinstance(payload, dict):
                continue
            name = str(payload.get("name") or identifier).strip()
            has_icon = bool(payload.get("has_icon"))
            entry = (str(identifier), name, has_icon)
            for alias in (identifier, name):
                key = _normalise_item_label(str(alias))
                if not key:
                    continue
                # Prefer the prefab that has actual artwork when localized
                # names collide (blueprints/variants occasionally do).
                existing = index.get(key)
                if existing is None or (has_icon and not existing[2]):
                    index[key] = entry
        aliases = sorted(index, key=len, reverse=True)
        _item_catalog_index = (mtime, index, aliases)
        return index, aliases


def extract_replay_timeline_item(
    action_type: str,
    details: str | None,
) -> Optional[dict[str, object]]:
    """Best-effort structured item for one server-log event.

    Unknown/modded labels remain useful as ``name`` while ``identifier`` is
    null, allowing the frontend to render a truthful SVG fallback instead of
    a blank or unrelated icon.
    """
    text = (details or "").strip()
    if not text:
        return None
    candidate = None
    for pattern in _TIMELINE_ITEM_PATTERNS.get(action_type, ()):
        match = pattern.search(text)
        if match:
            candidate = match.group(1).strip()
            break
    if not candidate:
        return None

    candidate = _ITEM_ID_SUFFIX_RE.sub("", candidate)
    candidate = _ITEM_PROJECTILE_SUFFIX_RE.sub("", candidate).strip(" \t\r\n.,")
    if not candidate:
        return None

    index, _aliases = _load_timeline_item_index()
    key = _normalise_item_label(candidate)
    resolved = index.get(key)
    if resolved is None:
        return {"identifier": None, "name": candidate}
    identifier, name, has_icon = resolved
    return {"identifier": identifier, "name": name, "has_icon": has_icon}


def extract_replay_timeline_target(
    action_type: str,
    details: str | None,
) -> Optional[str]:
    """Extract the truthful container/recipient from a known log phrase."""
    text = (details or "").strip()
    if not text:
        return None
    for pattern in _TIMELINE_TARGET_PATTERNS.get(action_type, ()):
        match = pattern.search(text)
        if not match:
            continue
        target = _ITEM_ID_SUFFIX_RE.sub("", match.group(1)).strip(" \t\r\n.,\"“”")
        return target or None
    return None


def classify_replay_timeline_lane(action_type: str) -> str:
    """Stable visual lane used by the V2 canvas timeline."""
    if action_type in {"chat", "radio", "terminal"}:
        return "communication"
    if action_type in {"attack", "damage", "shot", "death", "structure"}:
        return "combat"
    if action_type in {
        "picked_up", "placed", "removed", "opened", "closed", "equipped",
        "unequipped", "dropped", "threw", "used", "fabrication",
        "fabrication_cancel", "activated", "deactivated", "attached", "looted_body",
    }:
        return "inventory"
    if action_type in {
        "reactor", "repair", "repair_failed", "wire", "pump",
        "settings_change",
    }:
        return "systems"
    return "system"


def build_replay_timeline_overview_rows(
    buckets: list[dict],
    duration_ms: int,
) -> tuple[list[dict], Counter[str]]:
    """Convert SQL overview buckets without losing less frequent actions.

    ``get_log_timeline_overview`` already groups by both time bucket and
    action type.  A second grouping by visual lane used to collapse, for
    example, ``attack`` and ``death`` into one dominant marker.  Keep one
    lightweight row per action type here; the canvas performs its own pixel
    clustering, so dense views stay compact without making data disappear.
    """
    effective_duration_ms = max(0, int(duration_ms))
    rows: list[dict] = []
    action_counts: Counter[str] = Counter()
    for bucket in sorted(
        buckets,
        key=lambda item: (int(item["offset_ms"]), str(item["action_type"])),
    ):
        action_name = str(bucket["action_type"])
        amount = max(0, int(bucket["count"]))
        if amount <= 0:
            continue
        offset = min(effective_duration_ms, max(0, int(bucket["offset_ms"])))
        lane = classify_replay_timeline_lane(action_name)
        action_counts[action_name] += amount
        rows.append({
            "event_id": f"overview:{offset}:{lane}:{action_name}",
            "offset_ms": offset,
            "player_name": bucket.get("player_name"),
            "player_count": max(0, int(bucket.get("player_count") or 0)),
            "action_type": action_name,
            "details": f"{amount} событий этого типа в участке",
            "lane": lane,
            "item": None,
            "aggregate_count": amount,
            "precision_ms": None,
        })
    return rows, action_counts


# ══════════════════════════════════════════════════════════════════════════════
# App settings helpers (runtime, DB-backed with config fallback)
# ══════════════════════════════════════════════════════════════════════════════

def get_setting(db: Session, key: str, default: str = None) -> Optional[str]:
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        return row.value if row and row.value is not None else default
    except Exception as e:
        logger.warning(f"get_setting failed (table missing?): {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return default


def set_setting(db: Session, key: str, value: str):
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = value
        else:
            row = AppSetting(key=key, value=value)
            db.add(row)
        db.commit()
    except Exception as e:
        logger.warning(f"set_setting failed: {e}")
        db.rollback()


def get_retention_days(db: Session) -> int:
    raw = get_setting(db, SETTING_RETENTION_DAYS)
    if raw is not None:
        try:
            return max(1, int(raw))
        except (ValueError, TypeError):
            pass
    return settings.replays_retention_days


def get_upload_dir(db: Session) -> str:
    raw = get_setting(db, SETTING_UPLOAD_DIR)
    return raw.strip() if raw and raw.strip() else settings.replays_upload_dir


# ══════════════════════════════════════════════════════════════════════════════
# Replay service
# ══════════════════════════════════════════════════════════════════════════════

class ReplayService:
    def __init__(self, db: Session):
        self.db = db
        self.upload_dir = get_upload_dir(db)
        os.makedirs(self.upload_dir, exist_ok=True)

    # ── Listing ──────────────────────────────────────────────────────────────

    def get_all(self, limit: int = 200, offset: int = 0, server_name: Optional[str] = None):
        from sqlalchemy import desc
        q = self.db.query(RoundReplay)
        if server_name:
            q = q.filter(RoundReplay.server_name == server_name)
        total = q.count()
        rows = q.order_by(desc(RoundReplay.uploaded_at)).offset(offset).limit(limit).all()
        return rows, total

    def get_by_id(self, replay_id: int) -> Optional[RoundReplay]:
        return self.db.query(RoundReplay).filter(RoundReplay.id == replay_id).first()

    # ── Header parsing (lightweight) ───────────────────────────────────────────

    @staticmethod
    def _read_str(buf: bytes, off: int):
        """Read u16-length-prefixed UTF-8 string. Returns (value, new_offset)."""
        if off + 2 > len(buf):
            return "", off
        (n,) = struct.unpack_from("<H", buf, off)
        off += 2
        s = buf[off:off + n].decode("utf-8", errors="replace")
        return s, off + n

    @classmethod
    def _parse_demo_header(cls, raw: bytes) -> dict:
        """Parse just the .barodemo header (raw, uncompressed bytes)."""
        meta = {"server_name": None, "level_name": None, "sub_name": None,
                "has_voice": 0, "recorded_at": None}
        try:
            if raw[:4] != _MAGIC_DEMO:
                return meta
            off = 4
            (version, flags, tick_rate) = struct.unpack_from("<HHB", raw, off)
            off += 5
            (start_unix_ms,) = struct.unpack_from("<q", raw, off)
            off += 8
            server_name, off = cls._read_str(raw, off)
            level_seed, off = cls._read_str(raw, off)
            level_name, off = cls._read_str(raw, off)
            sub_name, off = cls._read_str(raw, off)
            meta["server_name"] = server_name or None
            meta["level_name"] = level_name or None
            meta["sub_name"] = sub_name or None
            meta["has_voice"] = 1 if (flags & 0x1) else 0
            if start_unix_ms and start_unix_ms > 0:
                meta["recorded_at"] = datetime.utcfromtimestamp(start_unix_ms / 1000.0)
        except Exception as e:
            logger.warning(f"Failed to parse demo header: {e}")
        return meta

    @classmethod
    def _extract_metadata(cls, file_path: str) -> dict:
        """Read minimal metadata without fully decompressing large files.

        For a zip: read only the first entry's first ~4KB to parse the header.
        For raw .barodemo: read first ~4KB.
        For gzip: decompress first ~4KB and parse header.
        """
        try:
            with open(file_path, "rb") as f:
                sig = f.read(4)
            if sig == _MAGIC_ZIP:
                with zipfile.ZipFile(file_path, "r") as zf:
                    names = zf.namelist()
                    if not names:
                        return {}
                    with zf.open(names[0], "r") as entry:
                        head = entry.read(4096)  # only header region
                    return cls._parse_demo_header(head)
            elif sig[:2] == b"\x1f\x8b":
                # gzip compressed
                import gzip
                with gzip.open(file_path, "rb") as gz:
                    head = gz.read(4096)
                return cls._parse_demo_header(head)
            elif sig == _MAGIC_DEMO:
                with open(file_path, "rb") as f:
                    head = f.read(4096)
                return cls._parse_demo_header(head)
        except Exception as e:
            logger.warning(f"Metadata extraction failed for {file_path}: {e}")
        return {}

    # ── Upload ─────────────────────────────────────────────────────────────────

    def save_upload(self, filename: str, content: bytes,
                    server_name: Optional[str] = None,
                    notes: Optional[str] = None) -> RoundReplay:
        """Store an uploaded demo file. Accepts raw .barodemo or .barodemo.zip.

        To keep server load minimal we store the bytes as-is. If the upload is a
        raw (uncompressed) demo we zip it once to save disk + bandwidth.
        """
        os.makedirs(self.upload_dir, exist_ok=True)

        import uuid
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        is_zip = content[:4] == _MAGIC_ZIP
        is_raw = content[:4] == _MAGIC_DEMO

        if not is_zip and not is_raw:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Файл не является .barodemo или .barodemo.zip")

        if is_zip:
            stored_name = f"replay_{ts}.barodemo.zip"
            stored_path = os.path.join(self.upload_dir, stored_name)
            with open(stored_path, "wb") as f:
                f.write(content)
        else:
            # Compress raw demo once (server-side, one-time cost) to save space.
            stored_name = f"replay_{ts}.barodemo.zip"
            stored_path = os.path.join(self.upload_dir, stored_name)
            inner_name = filename if filename.endswith(".barodemo") else "round.barodemo"
            with zipfile.ZipFile(stored_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                zf.writestr(inner_name, content)

        file_size = os.path.getsize(stored_path)
        meta = self._extract_metadata(stored_path)

        replay = RoundReplay(
            server_name=server_name or meta.get("server_name"),
            submarine_name=meta.get("sub_name"),
            level_name=meta.get("level_name"),
            filename=filename or stored_name,
            stored_filename=stored_name,
            file_size=file_size,
            has_voice=meta.get("has_voice", 0),
            recorded_at=meta.get("recorded_at"),
            notes=notes,
        )
        self.db.add(replay)
        self.db.commit()
        self.db.refresh(replay)
        _queue_voice_index(replay)
        logger.info(f"Saved replay {replay.id}: {stored_name} ({file_size} bytes)")
        return replay

    # ── Delete ───────────────────────────────────────────────────────────────

    def delete(self, replay_id: int) -> bool:
        replay = self.get_by_id(replay_id)
        if not replay:
            return False
        path = os.path.join(self.upload_dir, replay.stored_filename)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"Failed to delete replay file {path}: {e}")
        self.db.delete(replay)
        self.db.commit()
        return True

    def get_file_path(self, replay: RoundReplay) -> str:
        return os.path.join(self.upload_dir, replay.stored_filename)

    # ── Streaming save (low memory) ─────────────────────────────────────────────

    async def save_upload_stream(self, upload, server_name=None, notes=None,
                                 max_bytes: int = 500*1024*1024) -> "RoundReplay":
        """Save an UploadFile to disk in chunks WITHOUT loading it fully into RAM.

        Reads the first 4 bytes to detect format, then streams the rest to disk.
        Raw .barodemo is stored as-is (NOT recompressed in memory — recompression
        of a 500MB file in RAM is what triggered OOM). Browser handles raw too.
        """
        import uuid
        os.makedirs(self.upload_dir, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

        # Peek first 4 bytes for signature
        head = await upload.read(4)

        if not head or len(head) < 4:
            logger.warning(
                "Replay upload: file too small or empty (got %d bytes, filename=%s)",
                len(head) if head else 0, getattr(upload, "filename", None),
            )
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail=f"Файл пустой или слишком маленький ({len(head) if head else 0} байт)"
            )

        if head[:4] == _MAGIC_ZIP:
            stored_name = f"replay_{ts}.barodemo.zip"
        elif head[:4] == _MAGIC_DEMO:
            stored_name = f"replay_{ts}.barodemo"
        elif head[:2] == b"\x1f\x8b":
            # gzip-compressed — accept and store as .gz, will decompress on read
            stored_name = f"replay_{ts}.barodemo.gz"
        else:
            logger.warning(
                "Replay upload: unknown signature %s (filename=%s, size_hint=%s)",
                head[:4].hex(), getattr(upload, "filename", None),
                getattr(upload, "size", "?"),
            )
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail=f"Файл не является .barodemo или .barodemo.zip (сигнатура: {head[:4].hex()})"
            )

        stored_path = os.path.join(self.upload_dir, stored_name)
        total = 0
        CHUNK = 1024 * 1024  # 1 MB
        try:
            with open(stored_path, "wb") as f:
                f.write(head)
                total += len(head)
                while True:
                    chunk = await upload.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        f.close()
                        os.remove(stored_path)
                        from fastapi import HTTPException
                        raise HTTPException(status_code=413, detail="Файл слишком большой (>500MB)")
                    f.write(chunk)
        except Exception:
            if os.path.exists(stored_path):
                try: os.remove(stored_path)
                except OSError: pass
            raise

        file_size = os.path.getsize(stored_path)
        meta = self._extract_metadata(stored_path)  # reads only ~4KB

        replay = RoundReplay(
            server_name=server_name or meta.get("server_name"),
            submarine_name=meta.get("sub_name"),
            level_name=meta.get("level_name"),
            filename=getattr(upload, "filename", None) or stored_name,
            stored_filename=stored_name,
            file_size=file_size,
            has_voice=meta.get("has_voice", 0),
            recorded_at=meta.get("recorded_at"),
            notes=notes,
        )
        self.db.add(replay)
        self.db.commit()
        self.db.refresh(replay)
        _queue_voice_index(replay)
        logger.info(f"Saved replay {replay.id} (streamed): {stored_name} ({file_size} bytes)")
        return replay

    def save_staged_demo(self, demo_path: str, filename: str,
                         server_name: Optional[str] = None,
                         notes: Optional[str] = None,
                         stream_id: Optional[str] = None,
                         is_broken: bool = False) -> RoundReplay:
        """Register a raw demo assembled by streaming ingest without RAM copies."""
        import uuid
        from fastapi import HTTPException
        if not os.path.isfile(demo_path):
            raise HTTPException(status_code=404, detail="Поток демки не найден")
        with open(demo_path, "rb") as f:
            if f.read(4) != _MAGIC_DEMO:
                raise HTTPException(status_code=400, detail="Поток не является .barodemo")

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        stored_name = f"replay_{ts}.barodemo.zip"
        stored_path = os.path.join(self.upload_dir, stored_name)
        inner_name = filename if filename.endswith(".barodemo") else "round.barodemo"
        # Demo frames are already compact binary data. ZIP_STORED avoids a
        # long CPU-bound compression phase while the stream finish request is
        # holding an HTTP worker.
        with zipfile.ZipFile(stored_path, "w", zipfile.ZIP_STORED) as zf:
            with zf.open(inner_name, "w") as dst, open(demo_path, "rb") as src:
                shutil.copyfileobj(src, dst, length=1024 * 1024)

        meta = self._extract_metadata(stored_path)
        replay = self.db.query(RoundReplay).filter(
            RoundReplay.stream_id == stream_id
        ).first() if stream_id else None
        if replay is not None:
            old_path = os.path.join(self.upload_dir, replay.stored_filename)
            if old_path != stored_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
            replay.server_name = server_name or meta.get("server_name") or replay.server_name
            replay.submarine_name = meta.get("sub_name") or replay.submarine_name
            replay.level_name = meta.get("level_name") or replay.level_name
            replay.filename = filename or replay.filename
            replay.stored_filename = stored_name
            replay.file_size = os.path.getsize(stored_path)
            replay.has_voice = meta.get("has_voice", 0)
            replay.recorded_at = meta.get("recorded_at") or replay.recorded_at
            replay.notes = notes or replay.notes
            replay.is_broken = is_broken
            replay.broken_at = datetime.utcnow() if is_broken else None
        else:
            replay = RoundReplay(
                server_name=server_name or meta.get("server_name"),
                submarine_name=meta.get("sub_name"),
                level_name=meta.get("level_name"),
                filename=filename or stored_name,
                stored_filename=stored_name,
                file_size=os.path.getsize(stored_path),
                has_voice=meta.get("has_voice", 0),
                recorded_at=meta.get("recorded_at"),
                notes=notes,
                stream_id=stream_id,
                is_broken=is_broken,
                broken_at=datetime.utcnow() if is_broken else None,
            )
            self.db.add(replay)
        self.db.commit()
        self.db.refresh(replay)
        _queue_voice_index(replay)
        return replay

    # ── Retention cleanup ──────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """Delete replays older than the configured retention window."""
        retention_days = get_retention_days(self.db)
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        expired = self.db.query(RoundReplay).filter(RoundReplay.uploaded_at < cutoff).all()
        count = 0
        for r in expired:
            # Materialize any representative clips that still need this replay
            # while the source bytes exist. The archive tables intentionally do
            # not cascade from RoundReplay, so selected clips survive below.
            archive_ready = True
            try:
                from models import ReplayVoiceSegment
                from services_voice_archive import VoiceArchiveService

                steam_ids = {
                    steam_id
                    for (steam_id,) in self.db.query(ReplayVoiceSegment.steam_id)
                    .filter(ReplayVoiceSegment.replay_id == r.id)
                    .all()
                    if steam_id
                }
                if steam_ids:
                    with self.db.begin_nested():
                        VoiceArchiveService(self.db).reconcile_steam_ids(steam_ids)
                        self.db.flush()
            except Exception:
                # Do not delete the only remaining source while the archive
                # rebuild failed. The next scheduler pass can retry safely.
                archive_ready = False
                logger.exception("Voice archive reconciliation failed before deleting replay %s", r.id)
            if not archive_ready:
                continue
            path = os.path.join(self.upload_dir, r.stored_filename)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            self.db.delete(r)
            count += 1
        if count:
            self.db.commit()
            logger.info(f"Replay retention: deleted {count} expired replays (>{retention_days}d)")
        return count


def build_replay_download_archive(
    source_path: str,
    destination_path: str,
    member_name: str,
) -> None:
    """Wrap one stored replay in a ZIP without loading it into memory.

    Streamed uploads may intentionally be kept as raw ``.barodemo`` or gzip
    files to avoid a large recompression step during ingestion.  The admin UI
    nevertheless promises a real ZIP export, so build it only on explicit
    request. ``ZIP_STORED`` keeps the operation bounded and fast even for
    several-hundred-megabyte voice replays; gzip inputs are already compressed.
    """
    safe_member_name = os.path.basename(member_name).strip() or "round.barodemo"
    with zipfile.ZipFile(
        destination_path,
        "w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as archive:
        archive.write(source_path, arcname=safe_member_name)


class _StreamingZipSink:
    """Non-seekable file object that forwards ZIP bytes to a bounded queue."""

    def __init__(self, messages: queue.Queue, cancelled: threading.Event):
        self._messages = messages
        self._cancelled = cancelled
        self._position = 0

    def _put(self, value) -> None:
        while not self._cancelled.is_set():
            try:
                self._messages.put(value, timeout=0.25)
                return
            except queue.Full:
                continue
        raise BrokenPipeError("Replay archive download was cancelled")

    def write(self, data) -> int:
        payload = bytes(data)
        if payload:
            self._put(payload)
            self._position += len(payload)
        return len(payload)

    def tell(self) -> int:
        return self._position

    def seekable(self) -> bool:
        return False

    def flush(self) -> None:
        return None


def iter_replay_download_archive(
    source_path: str,
    member_name: str,
    *,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    """Stream one stored replay as a standards-compliant ZIP archive.

    ``ZipFile`` supports non-seekable outputs by writing data descriptors. A
    small producer thread performs disk I/O and feeds a bounded queue, so the
    HTTP response can send its headers and first ZIP header immediately rather
    than waiting for a multi-gigabyte temporary copy to finish.  The queue also
    caps memory use and the cancellation event stops the producer when a client
    aborts the download.
    """
    safe_member_name = os.path.basename(member_name).strip() or "round.barodemo"
    source_size = os.path.getsize(source_path)
    messages: queue.Queue = queue.Queue(maxsize=4)
    cancelled = threading.Event()
    finished = object()

    def enqueue_terminal(value) -> None:
        while not cancelled.is_set():
            try:
                messages.put(value, timeout=0.25)
                return
            except queue.Full:
                continue

    def produce() -> None:
        sink = _StreamingZipSink(messages, cancelled)
        try:
            with zipfile.ZipFile(
                sink,
                "w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                info = zipfile.ZipInfo(safe_member_name)
                info.compress_type = zipfile.ZIP_STORED
                info.file_size = source_size
                with open(source_path, "rb") as source, archive.open(
                    info,
                    "w",
                    force_zip64=source_size >= zipfile.ZIP64_LIMIT,
                ) as member:
                    while not cancelled.is_set():
                        chunk = source.read(chunk_size)
                        if not chunk:
                            break
                        member.write(chunk)
        except BrokenPipeError:
            return
        except BaseException as exc:
            if not cancelled.is_set():
                enqueue_terminal(exc)
        finally:
            if not cancelled.is_set():
                enqueue_terminal(finished)

    producer = threading.Thread(
        target=produce,
        name="replay-zip-stream",
        daemon=True,
    )
    producer.start()
    try:
        while True:
            message = messages.get()
            if message is finished:
                break
            if isinstance(message, BaseException):
                raise message
            yield message
    finally:
        cancelled.set()
        producer.join(timeout=1.0)


# ══════════════════════════════════════════════════════════════════════════════
# Background retention worker
# ══════════════════════════════════════════════════════════════════════════════

_replay_worker_running = False
_REPLAY_CLEANUP_INTERVAL = 6 * 3600  # every 6 hours


def _replay_worker_loop():
    global _replay_worker_running
    _replay_worker_running = True
    logger.info("Replay retention worker started")
    from database import SessionLocal
    while _replay_worker_running:
        try:
            db = SessionLocal()
            try:
                ReplayService(db).cleanup_expired()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Replay retention worker error: {e}")
        for _ in range(_REPLAY_CLEANUP_INTERVAL):
            if not _replay_worker_running:
                break
            time.sleep(1)


def start_replay_worker():
    global _replay_worker_running
    if _replay_worker_running:
        return
    t = threading.Thread(target=_replay_worker_loop, daemon=True, name="ReplayWorker")
    t.start()


def stop_replay_worker():
    global _replay_worker_running
    _replay_worker_running = False
