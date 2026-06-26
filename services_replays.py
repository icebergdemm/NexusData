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
import struct
import logging
import threading
import time
import zipfile
from datetime import datetime, timedelta
from typing import Optional, List

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
        logger.info(f"Saved replay {replay.id} (streamed): {stored_name} ({file_size} bytes)")
        return replay

    # ── Retention cleanup ──────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """Delete replays older than the configured retention window."""
        retention_days = get_retention_days(self.db)
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        expired = self.db.query(RoundReplay).filter(RoundReplay.uploaded_at < cutoff).all()
        count = 0
        for r in expired:
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
