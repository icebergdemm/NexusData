"""Pure storage and serialization helpers for archived ticket media.

The Discord ingest and HTTP route layers deliberately live elsewhere.  This
module only defines the small, deterministic pieces they share: opaque storage
keys, traversal-safe paths, attachment lookup/serialization, and bounded file
range iteration.
"""

from __future__ import annotations

from platform_locks import LOCK_EX, LOCK_NB, LOCK_UN, flock
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any


DEFAULT_RANGE_CHUNK_SIZE = 64 * 1024

_SAFE_NAMESPACE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SAFE_STORAGE_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_SAFE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,10}\Z")
_GENERATED_STORAGE_KEY_RE = re.compile(
    r"[0-9a-f]{32}(?:\.[A-Za-z0-9]{1,10})?\Z"
)
_PRIVATE_ATTACHMENT_FIELDS = {
    "local_path",
    "path",
    "stored_path",
    "storage_path",
    "storage_namespace",
}


class TicketMediaQuotaExceeded(RuntimeError):
    """Raised before a ticket-media write would exceed a disk budget."""

    def __init__(
        self,
        scope: str,
        *,
        limit_bytes: int,
        used_bytes: int,
        requested_bytes: int,
    ) -> None:
        self.scope = scope
        self.limit_bytes = int(limit_bytes)
        self.used_bytes = int(used_bytes)
        self.requested_bytes = int(requested_bytes)
        super().__init__(
            f"{scope} quota exceeded: used={self.used_bytes}, "
            f"requested={self.requested_bytes}, limit={self.limit_bytes}"
        )


_QUOTA_LOCK_NAME = ".ticket-media-quota.lock"
_QUOTA_RESERVATION_DIR = ".ticket-media-quota-reservations"
_QUOTA_RECORD_LIMIT = 4096
_DEFAULT_UNKNOWN_STREAM_RESERVATION = 200 * 1024 * 1024
_quota_process_lock = threading.RLock()


def _scan_ticket_media_usage(root: Path) -> tuple[int, dict[int, int]]:
    """Return authoritative durable/partial bytes, excluding quota metadata."""
    total = 0
    by_ticket: dict[int, int] = {}
    if not root.is_dir():
        return total, by_ticket

    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if name != _QUOTA_RESERVATION_DIR
            and not (current_path / name).is_symlink()
        ]
        try:
            relative = current_path.relative_to(root)
            first = relative.parts[0] if relative.parts else ""
            ticket_id = int(first) if first.isdigit() and int(first) > 0 else None
        except (ValueError, OSError):
            ticket_id = None

        for filename in filenames:
            if current_path == root and filename == _QUOTA_LOCK_NAME:
                continue
            path = current_path / filename
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                size = path.stat().st_size
            except OSError:
                # Files may be atomically renamed/removed by a completed writer
                # while this authoritative pass is walking the tree.
                continue
            total += size
            if ticket_id is not None:
                by_ticket[ticket_id] = by_ticket.get(ticket_id, 0) + size
    return total, by_ticket


def _read_active_quota_reservations(
    reservation_dir: Path,
) -> tuple[int, dict[int, int]]:
    """Count live flock-held leases and discard unlocked crash leftovers.

    This function runs while the global quota lock is held. Reservation
    records are immutable after creation; their separate flock is released
    only after the writer has atomically installed its target (or abandoned
    and cleaned its partial file).
    """
    reserved_global = 0
    reserved_by_ticket: dict[int, int] = {}
    open_flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)

    try:
        entries = list(os.scandir(reservation_dir))
    except FileNotFoundError:
        return reserved_global, reserved_by_ticket

    for entry in entries:
        if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
            continue
        entry_path = Path(entry.path)
        lock_path = entry_path.with_suffix(".lock") if os.name == "nt" else entry_path
        try:
            descriptor = os.open(lock_path, open_flags)
        except (FileNotFoundError, OSError):
            continue
        try:
            try:
                flock(descriptor, LOCK_EX | LOCK_NB)
            except BlockingIOError:
                if os.name == "nt":
                    raw = entry_path.read_bytes()[:_QUOTA_RECORD_LIMIT]
                else:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    raw = os.read(descriptor, _QUOTA_RECORD_LIMIT)
                try:
                    record = json.loads(raw.decode("utf-8"))
                    ticket_id = int(record["ticket_id"])
                    reserved_bytes = int(record["reserved_bytes"])
                    if ticket_id <= 0 or reserved_bytes < 0:
                        raise ValueError
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    UnicodeDecodeError,
                ) as exc:
                    # Never ignore a locked but unreadable record: doing so
                    # could permit both workers to exceed the disk budget.
                    raise RuntimeError("active ticket-media quota lease is corrupt") from exc
                reserved_global += reserved_bytes
                reserved_by_ticket[ticket_id] = (
                    reserved_by_ticket.get(ticket_id, 0) + reserved_bytes
                )
            else:
                # No process owns this record anymore. The durable/partial
                # scan that follows accounts for anything left by a crash.
                try:
                    flock(descriptor, LOCK_UN)
                    os.close(descriptor)
                    descriptor = -1
                    os.unlink(entry_path)
                    if os.name == "nt":
                        try:
                            os.unlink(lock_path)
                        except FileNotFoundError:
                            pass
                except FileNotFoundError:
                    pass
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return reserved_global, reserved_by_ticket


def _unknown_stream_reservation_limit(settings: Any | None) -> int:
    if settings is None:
        return _DEFAULT_UNKNOWN_STREAM_RESERVATION
    return max(
        1,
        int(
            getattr(
                settings,
                "ticket_attachment_max_bytes",
                _DEFAULT_UNKNOWN_STREAM_RESERVATION,
            )
            or _DEFAULT_UNKNOWN_STREAM_RESERVATION
        ),
        int(
            getattr(
                settings,
                "ticket_web_upload_max_bytes",
                _DEFAULT_UNKNOWN_STREAM_RESERVATION,
            )
            or _DEFAULT_UNKNOWN_STREAM_RESERVATION
        ),
    )


def _create_quota_reservation(
    *,
    root: Path,
    ticket_id: int,
    requested_bytes: int,
    ticket_limit: int,
    global_limit: int,
    unknown_stream_limit: int,
) -> "TicketMediaReservation":
    """Atomically scan usage and publish one cross-process capacity lease."""
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("ticket media root cannot be a symlink")
    reservation_dir = root / _QUOTA_RESERVATION_DIR
    if reservation_dir.is_symlink():
        raise ValueError("ticket media quota reservation directory cannot be a symlink")
    reservation_dir.mkdir(mode=0o700, exist_ok=True)

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_descriptor = os.open(root / _QUOTA_LOCK_NAME, lock_flags, 0o600)
    with _quota_process_lock:
        try:
            flock(lock_descriptor, LOCK_EX)

            # Read leases first, then scan files. This ordering is essential:
            # if a writer commits while this pass runs, we see either its still
            # locked lease or its already-installed file, never neither.
            reserved_global, reserved_by_ticket = _read_active_quota_reservations(
                reservation_dir
            )
            actual_global, actual_by_ticket = _scan_ticket_media_usage(root)
            ticket_used = (
                actual_by_ticket.get(ticket_id, 0)
                + reserved_by_ticket.get(ticket_id, 0)
            )
            global_used = actual_global + reserved_global

            capacity = requested_bytes
            overflow_scope = "reservation"
            overflow_limit = requested_bytes
            overflow_used = requested_bytes
            if requested_bytes == 0:
                capacity = unknown_stream_limit
                overflow_limit = capacity
                overflow_used = 0
                if ticket_limit:
                    ticket_available = max(0, ticket_limit - ticket_used)
                    if ticket_available < capacity:
                        capacity = ticket_available
                        overflow_scope = "ticket"
                        overflow_limit = ticket_limit
                        overflow_used = ticket_used
                if global_limit:
                    global_available = max(0, global_limit - global_used)
                    if global_available < capacity:
                        capacity = global_available
                        overflow_scope = "global"
                        overflow_limit = global_limit
                        overflow_used = global_used

            if ticket_limit and ticket_used + capacity > ticket_limit:
                raise TicketMediaQuotaExceeded(
                    "ticket",
                    limit_bytes=ticket_limit,
                    used_bytes=ticket_used,
                    requested_bytes=capacity,
                )
            if global_limit and global_used + capacity > global_limit:
                raise TicketMediaQuotaExceeded(
                    "global",
                    limit_bytes=global_limit,
                    used_bytes=global_used,
                    requested_bytes=capacity,
                )
            if requested_bytes == 0 and capacity <= 0:
                raise TicketMediaQuotaExceeded(
                    overflow_scope,
                    limit_bytes=overflow_limit,
                    used_bytes=overflow_used,
                    requested_bytes=1,
                )

            token = uuid.uuid4().hex
            reservation_path = reservation_dir / f"{token}.json"
            record_flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(reservation_path, record_flags, 0o600)
            lock_path = reservation_path
            try:
                payload = json.dumps(
                    {
                        "version": 1,
                        "ticket_id": ticket_id,
                        "reserved_bytes": capacity,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                os.write(descriptor, payload)
                os.fsync(descriptor)
                if os.name == "nt":
                    os.close(descriptor)
                    descriptor = -1
                    lock_path = reservation_path.with_suffix(".lock")
                    descriptor = os.open(lock_path, record_flags, 0o600)
                flock(descriptor, LOCK_EX)
            except Exception:
                try:
                    os.unlink(reservation_path)
                except OSError:
                    pass
                if os.name == "nt":
                    try:
                        os.unlink(lock_path)
                    except OSError:
                        pass
                if descriptor >= 0:
                    os.close(descriptor)
                raise
            return TicketMediaReservation(
                ticket_id=ticket_id,
                reserved_bytes=capacity,
                overflow_scope=overflow_scope,
                overflow_limit=overflow_limit,
                overflow_used=overflow_used,
                descriptor=descriptor,
                reservation_path=reservation_path,
            )
        finally:
            try:
                flock(lock_descriptor, LOCK_UN)
            finally:
                os.close(lock_descriptor)


class TicketMediaReservation:
    """A fixed-capacity cross-process lease held until a file write ends.

    The potentially slow flock wait and authoritative directory scan happen in
    ``reserve_ticket_media`` (its async callers dispatch it to a worker
    thread). ``grow_to`` is deliberately in-memory only, and commit/release do
    nothing slower than releasing/closing an already-owned descriptor.
    """

    def __init__(
        self,
        *,
        ticket_id: int,
        reserved_bytes: int,
        overflow_scope: str,
        overflow_limit: int,
        overflow_used: int,
        descriptor: int,
        reservation_path: Path,
    ) -> None:
        self.ticket_id = ticket_id
        self.reserved_bytes = reserved_bytes
        self._overflow_scope = overflow_scope
        self._overflow_limit = overflow_limit
        self._overflow_used = overflow_used
        self._descriptor = descriptor
        self._reservation_path = reservation_path
        self._lock = threading.Lock()
        self._closed = False

    def grow_to(self, target_bytes: int) -> None:
        if isinstance(target_bytes, bool) or int(target_bytes) < 0:
            raise ValueError("target_bytes must be a non-negative integer")
        target_bytes = int(target_bytes)
        with self._lock:
            if self._closed:
                raise RuntimeError("ticket media reservation is already closed")
            if target_bytes > self.reserved_bytes:
                raise TicketMediaQuotaExceeded(
                    self._overflow_scope,
                    limit_bytes=self._overflow_limit,
                    used_bytes=self._overflow_used,
                    requested_bytes=target_bytes,
                )

    def commit(self, actual_bytes: int) -> None:
        if isinstance(actual_bytes, bool) or int(actual_bytes) < 0:
            raise ValueError("actual_bytes must be a non-negative integer")
        self.grow_to(int(actual_bytes))
        self._finish()

    def release(self) -> None:
        self._finish()

    def _finish(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            descriptor = self._descriptor
            self._descriptor = -1
        # Unlocking an already-owned flock never waits. Keep the tiny stale
        # record: the next authoritative reservation pass removes it while
        # holding the global lock, entirely outside the asyncio event loop.
        try:
            flock(descriptor, LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "TicketMediaReservation":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._closed:
            self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


def reserve_ticket_media(
    ticket_id: int,
    requested_bytes: int = 0,
    *,
    root: str | Path | None = None,
    ticket_quota_bytes: int | None = None,
    global_quota_bytes: int | None = None,
) -> TicketMediaReservation:
    """Reserve disk capacity for one ticket-media write.

    A zero configured quota disables only that scope. Unknown-length streams
    receive a conservative fixed-capacity lease (bounded by the per-file cap
    and remaining quota), allowing ``grow_to`` to stay free of filesystem I/O.
    """

    normalized_ticket_id = _positive_int(ticket_id, "ticket_id")
    if isinstance(requested_bytes, bool):
        raise ValueError("requested_bytes must be a non-negative integer")
    try:
        normalized_requested = int(requested_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError("requested_bytes must be a non-negative integer") from exc
    if normalized_requested < 0:
        raise ValueError("requested_bytes must be a non-negative integer")

    settings = None
    if (
        root is None
        or ticket_quota_bytes is None
        or global_quota_bytes is None
        or normalized_requested == 0
    ):
        from config import get_settings

        settings = get_settings()
        root = root if root is not None else settings.ticket_attachments_dir
        ticket_quota_bytes = (
            ticket_quota_bytes
            if ticket_quota_bytes is not None
            else settings.ticket_attachment_ticket_quota_bytes
        )
        global_quota_bytes = (
            global_quota_bytes
            if global_quota_bytes is not None
            else settings.ticket_attachment_global_quota_bytes
        )

    normalized_root = Path(root).expanduser().resolve(strict=False)
    normalized_ticket_limit = max(0, int(ticket_quota_bytes))
    normalized_global_limit = max(0, int(global_quota_bytes))
    return _create_quota_reservation(
        root=normalized_root,
        ticket_id=normalized_ticket_id,
        requested_bytes=normalized_requested,
        ticket_limit=normalized_ticket_limit,
        global_limit=normalized_global_limit,
        unknown_stream_limit=_unknown_stream_reservation_limit(settings),
    )


def note_ticket_media_deleted(ticket_id: int, removed_bytes: int) -> None:
    """Compatibility hook; authoritative filesystem scans need no cache edit."""

    _positive_int(ticket_id, "ticket_id")
    if isinstance(removed_bytes, bool) or int(removed_bytes) < 0:
        raise ValueError("removed_bytes must be a non-negative integer")


def delete_ticket_media_file(
    ticket_id: int,
    path: str | Path,
    *,
    root: str | Path | None = None,
) -> bool:
    """Delete one committed ticket file; the next scan observes exact usage."""

    normalized_ticket_id = _positive_int(ticket_id, "ticket_id")
    if root is None:
        from config import get_settings

        root = get_settings().ticket_attachments_dir
    configured_root = Path(root).expanduser().resolve(strict=False)
    ticket_root = (configured_root / str(normalized_ticket_id)).resolve(strict=False)
    if not ticket_root.is_relative_to(configured_root):
        raise ValueError("ticket media ticket directory escapes configured root")
    raw_path = Path(path).expanduser()
    if raw_path.is_symlink():
        raise ValueError("ticket media delete target cannot be a symlink")
    candidate = raw_path.resolve(strict=False)
    if not candidate.is_relative_to(ticket_root):
        raise ValueError("ticket media delete target escapes its ticket root")

    try:
        if not candidate.is_file():
            return False
        candidate.unlink()
    except FileNotFoundError:
        return False
    return True


def reset_ticket_media_quota_state() -> None:
    """Compatibility no-op: quota usage is authoritative on every reserve."""


def cleanup_orphaned_ticket_media(
    referenced: set[tuple[int, str, str]],
    *,
    root: str | Path | None = None,
    min_age_seconds: float = 24 * 60 * 60,
) -> dict:
    """Remove old generated files which no TicketMessage references.

    A process can die after the atomic file rename but before its SQL commit.
    The grace window protects in-flight DB work, and the global quota lock plus
    active-lease check prevents this reconciler from racing a live writer.
    Only application-generated opaque names inside the dedicated ticket tree
    are eligible; unrelated/manual files are never touched.
    """

    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must be non-negative")
    if root is None:
        from config import get_settings

        root = get_settings().ticket_attachments_dir
    configured_root = Path(root).expanduser().resolve(strict=False)
    if not configured_root.is_dir():
        return {
            "status": "ok",
            "deleted_files": 0,
            "deleted_partials": 0,
            "reclaimed_bytes": 0,
        }
    if configured_root.is_symlink():
        raise ValueError("ticket media root cannot be a symlink")

    referenced_paths: set[Path] = set()
    for ticket_id, namespace, storage_key in referenced:
        try:
            referenced_paths.add(
                storage_path(
                    ticket_id,
                    namespace,
                    storage_key,
                    root=configured_root,
                )
            )
        except (TypeError, ValueError):
            # Malformed DB metadata cannot authorize an arbitrary filesystem
            # path. It remains visible as incomplete evidence in the UI.
            continue

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_descriptor = os.open(configured_root / _QUOTA_LOCK_NAME, lock_flags, 0o600)
    deleted_files = 0
    deleted_partials = 0
    reclaimed_bytes = 0
    with _quota_process_lock:
        try:
            flock(lock_descriptor, LOCK_EX)
            reservation_dir = configured_root / _QUOTA_RESERVATION_DIR
            active_bytes, _active_by_ticket = _read_active_quota_reservations(
                reservation_dir
            )
            if active_bytes:
                return {
                    "status": "skipped",
                    "reason": "active_writers",
                    "deleted_files": 0,
                    "deleted_partials": 0,
                    "reclaimed_bytes": 0,
                }

            cutoff = time.time() - float(min_age_seconds)
            for current, directories, filenames in os.walk(
                configured_root,
                topdown=False,
                followlinks=False,
            ):
                current_path = Path(current)
                directories[:] = [
                    name
                    for name in directories
                    if name != _QUOTA_RESERVATION_DIR
                    and not (current_path / name).is_symlink()
                ]
                try:
                    relative_dir = current_path.relative_to(configured_root)
                except ValueError:
                    continue
                if len(relative_dir.parts) != 2 or not relative_dir.parts[0].isdigit():
                    continue

                for filename in filenames:
                    path = current_path / filename
                    if path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                    except (FileNotFoundError, OSError):
                        continue
                    if stat.st_mtime > cutoff:
                        continue

                    is_partial = filename.endswith(".part")
                    generated_name = filename
                    if is_partial:
                        generated_name = generated_name[:-5]
                        if generated_name.startswith("."):
                            generated_name = generated_name[1:]
                    if not _GENERATED_STORAGE_KEY_RE.fullmatch(generated_name):
                        continue
                    if not is_partial and path.resolve(strict=False) in referenced_paths:
                        continue
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        continue
                    reclaimed_bytes += max(0, int(stat.st_size))
                    if is_partial:
                        deleted_partials += 1
                    else:
                        deleted_files += 1

            # Leave the ticket root and quota metadata in place, but remove
            # empty per-message namespaces left after orphan reclamation.
            for ticket_dir in configured_root.iterdir():
                if not ticket_dir.is_dir() or not ticket_dir.name.isdigit():
                    continue
                for namespace_dir in ticket_dir.iterdir():
                    if namespace_dir.is_dir() and not namespace_dir.is_symlink():
                        try:
                            namespace_dir.rmdir()
                        except OSError:
                            pass
            return {
                "status": "ok",
                "deleted_files": deleted_files,
                "deleted_partials": deleted_partials,
                "reclaimed_bytes": reclaimed_bytes,
            }
        finally:
            try:
                flock(lock_descriptor, LOCK_UN)
            finally:
                os.close(lock_descriptor)


def new_storage_key(filename: str | None) -> str:
    """Return an opaque collision-resistant key with a conservative suffix.

    The original filename is never used as a path component.  Keeping a short
    alphanumeric extension is useful for diagnostics and media tooling, while
    unusual or attacker-controlled suffixes are simply omitted.
    """

    raw_name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(raw_name).suffix.lower()
    if not _SAFE_EXTENSION_RE.fullmatch(suffix):
        suffix = ""
    return f"{uuid.uuid4().hex}{suffix}"


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if normalized <= 0 or str(normalized) != str(value):
        raise ValueError(f"{field_name} must be a positive integer")
    return normalized


def _safe_namespace(namespace: str) -> str:
    normalized = str(namespace or "")
    if not _SAFE_NAMESPACE_RE.fullmatch(normalized):
        raise ValueError("namespace must be a single safe path component")
    return normalized


def _safe_storage_key(storage_key: str) -> str:
    normalized = str(storage_key or "")
    if (
        not _SAFE_STORAGE_KEY_RE.fullmatch(normalized)
        or normalized in {".", ".."}
        or ".." in normalized
    ):
        raise ValueError("storage_key must be a single safe path component")
    return normalized


def storage_path(
    ticket_id: int,
    namespace: str,
    storage_key: str,
    root: str | Path | None = None,
) -> Path:
    """Resolve an archived media path while rejecting every traversal form.

    No directory is created.  Callers remain responsible for I/O and can use
    the returned path for an atomic ``.part`` write/rename workflow.
    """

    normalized_ticket_id = _positive_int(ticket_id, "ticket_id")
    normalized_namespace = _safe_namespace(namespace)
    normalized_storage_key = _safe_storage_key(storage_key)

    if root is None:
        from config import get_settings

        configured_root = get_settings().ticket_attachments_dir
    else:
        configured_root = root

    base = Path(configured_root).expanduser().resolve(strict=False)
    expected_parent = (
        base / str(normalized_ticket_id) / normalized_namespace
    ).resolve(strict=False)
    candidate = (expected_parent / normalized_storage_key).resolve(strict=False)

    # Segment validation above blocks lexical traversal.  The resolved-parent
    # equality additionally blocks a pre-existing symlink below the storage
    # root from redirecting an otherwise innocent-looking key elsewhere.
    if candidate.parent != expected_parent or not candidate.is_relative_to(base):
        raise ValueError("resolved storage path escapes ticket attachment root")
    return candidate


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def find_archived_attachment(message: Any, storage_key: str) -> Any | None:
    """Find an archived attachment on a message or nested reference snapshot.

    Ticket messages are SQLAlchemy objects in route code and plain mappings in
    serialized snapshots/tests, so both shapes are supported.  Only attachment
    collections and known snapshot wrappers are traversed; unrelated mapping
    fields containing a coincidental ``storage_key`` are ignored.
    """

    if not storage_key:
        return None

    seen: set[int] = set()

    def visit(container: Any) -> Any | None:
        if container is None or isinstance(container, (str, bytes, bytearray)):
            return None

        identity = id(container)
        if identity in seen:
            return None
        seen.add(identity)

        attachments = _field(container, "attachments", []) or []
        if isinstance(attachments, (list, tuple)):
            for attachment in attachments:
                if str(_field(attachment, "storage_key", "") or "") == str(storage_key):
                    return attachment

        snapshot = _field(container, "reference_snapshot")
        found = visit(snapshot)
        if found is not None:
            return found

        # Accept both the normalized application shape and Discord's raw
        # ``message_snapshots: [{message: {...}}]`` envelope.  This also makes
        # the helper tolerant of the one nested snapshot level Discord allows.
        wrapped_message = _field(container, "message")
        found = visit(wrapped_message)
        if found is not None:
            return found

        snapshots = _field(container, "message_snapshots", []) or []
        if isinstance(snapshots, (list, tuple)):
            for item in snapshots:
                found = visit(item)
                if found is not None:
                    return found
        return None

    return visit(message)


def _raw_attachment_dict(raw: Any) -> dict[str, Any]:
    # Older ticket rows stored attachments as bare Discord CDN URL strings.
    # Keep those records readable while newer messages use structured,
    # durably archived metadata.
    if isinstance(raw, str):
        return {"url": raw}

    if isinstance(raw, Mapping):
        return {
            str(key): value
            for key, value in raw.items()
            if str(key) not in _PRIVATE_ATTACHMENT_FIELDS
        }

    result: dict[str, Any] = {}
    for field in (
        "id",
        "discord_attachment_id",
        "filename",
        "content_type",
        "size",
        "file_size",
        "width",
        "height",
        "duration",
        "duration_secs",
        "description",
        "spoiler",
        "sha256",
        "archive_status",
        "storage_key",
        "url",
        "legacy_url",
        "source_url",
        "proxy_url",
    ):
        value = getattr(raw, field, None)
        if value is not None:
            result[field] = value
    return result


def serialize_attachment(ticket_id: int, message_id: int, raw: Any) -> dict[str, Any]:
    """Serialize attachment metadata without exposing a local filesystem path.

    Archived media always receives a stable, same-origin authenticated URL.
    Legacy records without a valid storage key retain their historical remote
    URL so existing ticket data remains usable while it is being backfilled.
    """

    normalized_ticket_id = _positive_int(ticket_id, "ticket_id")
    normalized_message_id = _positive_int(message_id, "message_id")
    result = _raw_attachment_dict(raw)

    storage_key = result.get("storage_key")
    try:
        normalized_storage_key = _safe_storage_key(storage_key) if storage_key else None
    except ValueError:
        normalized_storage_key = None

    legacy_url = next(
        (
            result.get(field)
            for field in ("legacy_url", "source_url", "url", "proxy_url")
            if result.get(field)
        ),
        None,
    )

    if normalized_storage_key:
        result["storage_key"] = normalized_storage_key
        result["url"] = (
            f"/api/v1/admin/tickets/{normalized_ticket_id}/messages/"
            f"{normalized_message_id}/media/{normalized_storage_key}"
        )
        result["download_url"] = f'{result["url"]}?download=true'
        result["archived"] = True
        result.setdefault("archive_status", "stored")
        # Once a durable local copy exists, do not leak Discord's expiring
        # signed CDN/proxy URLs back to browser clients.
        for field in ("legacy_url", "source_url", "proxy_url"):
            result.pop(field, None)
    else:
        # Do not echo a malformed key into either a path or the API response.
        result.pop("storage_key", None)
        result["url"] = legacy_url
        result["download_url"] = legacy_url
        result["archived"] = False
        result.setdefault("archive_status", "legacy_remote" if legacy_url else "failed")

    return result


def iter_file_range(
    path: str | Path,
    start: int,
    end: int,
    chunk_size: int = DEFAULT_RANGE_CHUNK_SIZE,
) -> Iterator[bytes]:
    """Yield the inclusive byte range ``start..end`` without reading past it."""

    if isinstance(start, bool) or isinstance(end, bool):
        raise ValueError("range offsets must be integers")
    if not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("range offsets must be integers")
    if start < 0 or end < start:
        raise ValueError("invalid inclusive file range")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    remaining = end - start + 1
    with Path(path).open("rb") as source:
        source.seek(start)
        while remaining:
            chunk = source.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
