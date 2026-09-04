import asyncio
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from services_ticket_media import (
    TicketMediaQuotaExceeded,
    cleanup_orphaned_ticket_media,
    delete_ticket_media_file,
    find_archived_attachment,
    iter_file_range,
    new_storage_key,
    reserve_ticket_media,
    reset_ticket_media_quota_state,
    serialize_attachment,
    storage_path,
)


def _multiprocess_quota_attempt(
    root: str,
    ticket_id: int,
    ticket_quota_bytes: int,
    global_quota_bytes: int,
    result_queue,
    release_event,
) -> None:
    """Hold a reservation long enough for a sibling process to observe it."""
    reservation = None
    try:
        reservation = reserve_ticket_media(
            ticket_id,
            6,
            root=root,
            ticket_quota_bytes=ticket_quota_bytes,
            global_quota_bytes=global_quota_bytes,
        )
        result_queue.put(("reserved", None))
        release_event.wait(timeout=10)
    except TicketMediaQuotaExceeded as exc:
        result_queue.put(("rejected", exc.scope))
    except Exception as exc:  # pragma: no cover - reported to the parent assertion
        result_queue.put(("error", repr(exc)))
    finally:
        if reservation is not None:
            reservation.release()


def _multiprocess_crash_with_reservation(root: str, ready_event) -> None:
    """Simulate a killed worker that cannot explicitly release its lease."""
    reserve_ticket_media(
        73,
        6,
        root=root,
        ticket_quota_bytes=10,
        global_quota_bytes=100,
    )
    ready_event.set()
    os._exit(0)


@pytest.fixture(autouse=True)
def _reset_quota_cache():
    reset_ticket_media_quota_state()
    yield
    reset_ticket_media_quota_state()


def test_new_storage_key_is_opaque_and_keeps_only_a_safe_short_extension():
    first = new_storage_key("../../Evidence Final.PNG")
    second = new_storage_key("..\\..\\Evidence Final.PNG")

    assert first.endswith(".png")
    assert second.endswith(".png")
    assert len(first) == 36
    assert first != second
    assert "/" not in first and "\\" not in first and ".." not in first
    assert new_storage_key("payload.reallylongextension").isalnum()
    assert new_storage_key(".env").isalnum()


def test_orphan_cleanup_keeps_referenced_and_recent_files(tmp_path):
    referenced_key = new_storage_key("kept.png")
    orphan_key = new_storage_key("orphan.mp4")
    recent_key = new_storage_key("recent.log")
    partial_key = new_storage_key("partial.bin")
    referenced_path = storage_path(7, "msg-1", referenced_key, root=tmp_path)
    orphan_path = storage_path(7, "msg-2", orphan_key, root=tmp_path)
    recent_path = storage_path(7, "msg-3", recent_key, root=tmp_path)
    partial_path = storage_path(7, "msg-4", partial_key, root=tmp_path).with_name(
        partial_key + ".part"
    )
    for path in (referenced_path, orphan_path, recent_path, partial_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"evidence")
    old = 1_600_000_000
    os.utime(referenced_path, (old, old))
    os.utime(orphan_path, (old, old))
    os.utime(partial_path, (old, old))

    result = cleanup_orphaned_ticket_media(
        {(7, "msg-1", referenced_key)},
        root=tmp_path,
        min_age_seconds=60,
    )

    assert result == {
        "status": "ok",
        "deleted_files": 1,
        "deleted_partials": 1,
        "reclaimed_bytes": 16,
    }
    assert referenced_path.is_file()
    assert recent_path.is_file()
    assert not orphan_path.exists()
    assert not partial_path.exists()


def test_orphan_cleanup_skips_while_any_writer_holds_a_quota_lease(tmp_path):
    orphan_key = new_storage_key("orphan.bin")
    orphan_path = storage_path(9, "msg-1", orphan_key, root=tmp_path)
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_bytes(b"old")
    os.utime(orphan_path, (1_600_000_000, 1_600_000_000))
    reservation = reserve_ticket_media(
        10,
        1,
        root=tmp_path,
        ticket_quota_bytes=100,
        global_quota_bytes=100,
    )
    try:
        result = cleanup_orphaned_ticket_media(
            set(),
            root=tmp_path,
            min_age_seconds=0,
        )
    finally:
        reservation.release()

    assert result["status"] == "skipped"
    assert result["reason"] == "active_writers"
    assert orphan_path.is_file()


@pytest.mark.parametrize(
    ("ticket_id", "namespace", "storage_key"),
    [
        (0, "message", "abc.jpg"),
        (-1, "message", "abc.jpg"),
        (True, "message", "abc.jpg"),
        (1, "../outside", "abc.jpg"),
        (1, "message/nested", "abc.jpg"),
        (1, "message", "../abc.jpg"),
        (1, "message", "nested/abc.jpg"),
        (1, "message", "abc..jpg"),
        (1, "message", ".hidden"),
    ],
)
def test_storage_path_rejects_unsafe_components(tmp_path, ticket_id, namespace, storage_key):
    with pytest.raises(ValueError):
        storage_path(ticket_id, namespace, storage_key, root=tmp_path)


def test_storage_path_is_scoped_below_ticket_and_has_no_io_side_effect(tmp_path):
    result = storage_path(42, "message", "a1b2c3.png", root=tmp_path / "tickets")

    assert result == (tmp_path / "tickets" / "42" / "message" / "a1b2c3.png").resolve()
    assert not result.parent.exists()


def test_storage_path_uses_ticket_attachments_setting_when_root_is_omitted(tmp_path, monkeypatch):
    configured_root = tmp_path / "configured-ticket-media"
    monkeypatch.setattr(
        "config.get_settings",
        lambda: SimpleNamespace(ticket_attachments_dir=str(configured_root)),
    )

    result = storage_path(9, "forward", "snapshot.webp")

    assert result == (configured_root / "9" / "forward" / "snapshot.webp").resolve()


def test_storage_path_rejects_existing_symlink_escape(tmp_path):
    root = tmp_path / "tickets"
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "42").mkdir(parents=True)
    (root / "42" / "message").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes"):
        storage_path(42, "message", "a1b2c3.png", root=root)


def test_ticket_quota_counts_existing_files_and_active_reservations(tmp_path):
    existing = tmp_path / "42" / "old"
    existing.mkdir(parents=True)
    (existing / "proof.bin").write_bytes(b"1234")

    first = reserve_ticket_media(
        42,
        5,
        root=tmp_path,
        ticket_quota_bytes=10,
        global_quota_bytes=100,
    )
    with pytest.raises(TicketMediaQuotaExceeded) as caught:
        reserve_ticket_media(
            42,
            2,
            root=tmp_path,
            ticket_quota_bytes=10,
            global_quota_bytes=100,
        )
    assert caught.value.scope == "ticket"
    assert caught.value.used_bytes == 9

    first.release()
    second = reserve_ticket_media(
        42,
        6,
        root=tmp_path,
        ticket_quota_bytes=10,
        global_quota_bytes=100,
    )
    second.release()


def test_global_quota_is_shared_between_tickets(tmp_path):
    existing = tmp_path / "1" / "old"
    existing.mkdir(parents=True)
    (existing / "proof.bin").write_bytes(b"123456")

    with pytest.raises(TicketMediaQuotaExceeded) as caught:
        reserve_ticket_media(
            2,
            5,
            root=tmp_path,
            ticket_quota_bytes=100,
            global_quota_bytes=10,
        )
    assert caught.value.scope == "global"
    assert caught.value.used_bytes == 6


def test_unknown_stream_grows_before_write_and_committed_bytes_remain_counted(tmp_path):
    reservation = reserve_ticket_media(
        7,
        0,
        root=tmp_path,
        ticket_quota_bytes=8,
        global_quota_bytes=100,
    )
    reservation.grow_to(8)
    with pytest.raises(TicketMediaQuotaExceeded):
        reservation.grow_to(9)
    archived = tmp_path / "7" / "web" / "proof.bin"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"12345678")
    reservation.commit(8)

    with pytest.raises(TicketMediaQuotaExceeded):
        reserve_ticket_media(
            7,
            1,
            root=tmp_path,
            ticket_quota_bytes=8,
            global_quota_bytes=100,
        )

    assert delete_ticket_media_file(7, archived, root=tmp_path) is True
    retry = reserve_ticket_media(
        7,
        8,
        root=tmp_path,
        ticket_quota_bytes=8,
        global_quota_bytes=100,
    )
    retry.release()


def test_concurrent_reservations_cannot_both_cross_same_ticket_quota(tmp_path):
    start = threading.Barrier(2)
    attempted = threading.Barrier(2)

    def attempt_reservation():
        start.wait(timeout=2)
        reservation = None
        try:
            reservation = reserve_ticket_media(
                5,
                6,
                root=tmp_path,
                ticket_quota_bytes=10,
                global_quota_bytes=100,
            )
            outcome = "reserved"
        except TicketMediaQuotaExceeded:
            outcome = "rejected"
        attempted.wait(timeout=2)
        if reservation is not None:
            reservation.release()
        return outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: attempt_reservation(), range(2)))

    assert sorted(outcomes) == ["rejected", "reserved"]


@pytest.mark.parametrize(
    ("ticket_ids", "ticket_quota_bytes", "global_quota_bytes", "rejected_scope"),
    [
        ((71, 71), 10, 100, "ticket"),
        ((71, 72), 100, 10, "global"),
    ],
)
def test_multiprocess_reservations_share_ticket_and_global_quotas(
    tmp_path,
    ticket_ids,
    ticket_quota_bytes,
    global_quota_bytes,
    rejected_scope,
):
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    release_event = context.Event()
    processes = [
        context.Process(
            target=_multiprocess_quota_attempt,
            args=(
                str(tmp_path),
                ticket_id,
                ticket_quota_bytes,
                global_quota_bytes,
                result_queue,
                release_event,
            ),
        )
        for ticket_id in ticket_ids
    ]

    try:
        for process in processes:
            process.start()
        outcomes = [result_queue.get(timeout=15) for _process in processes]
    finally:
        release_event.set()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(outcome[0] for outcome in outcomes) == ["rejected", "reserved"]
    assert next(outcome[1] for outcome in outcomes if outcome[0] == "rejected") == rejected_scope


def test_multiprocess_crash_releases_stale_quota_lease(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready_event = context.Event()
    process = context.Process(
        target=_multiprocess_crash_with_reservation,
        args=(str(tmp_path), ready_event),
    )
    process.start()
    assert ready_event.wait(timeout=15)
    process.join(timeout=15)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    assert process.exitcode == 0

    # The OS drops the flock on process exit. The next authoritative pass
    # removes the abandoned record instead of leaking quota forever.
    replacement = reserve_ticket_media(
        73,
        6,
        root=tmp_path,
        ticket_quota_bytes=10,
        global_quota_bytes=100,
    )
    replacement.release()


def test_discord_archiver_marks_quota_failure_without_downloading(tmp_path, monkeypatch):
    import config
    from discord_bot import bot as bot_module

    existing = tmp_path / "11" / "old"
    existing.mkdir(parents=True)
    (existing / "already.bin").write_bytes(b"12345")
    quota_settings = SimpleNamespace(
        ticket_attachments_dir=str(tmp_path),
        ticket_attachment_ticket_quota_bytes=5,
        ticket_attachment_global_quota_bytes=100,
    )
    monkeypatch.setattr(config, "get_settings", lambda: quota_settings)
    monkeypatch.setattr(
        bot_module,
        "settings",
        SimpleNamespace(
            ticket_attachment_max_bytes=100,
            ticket_attachment_message_max_bytes=100,
            ticket_attachment_download_timeout_seconds=5,
        ),
    )

    result = asyncio.run(bot_module._archive_ticket_attachments(
        11,
        "msg-99",
        [SimpleNamespace(
            id="55",
            filename="proof.png",
            content_type="image/png",
            size=1,
            url="https://example.invalid/must-not-be-requested",
            proxy_url="",
        )],
        {"used": 0},
    ))

    assert result[0]["archive_status"] == "quota_exceeded"
    assert result[0]["archive_error"] == "ticket_quota"
    assert result[0]["storage_key"] is None


def test_discord_archiver_does_not_trust_declared_cdn_size(tmp_path, monkeypatch):
    from discord_bot import bot as bot_module

    requested_sizes = []

    class Reservation:
        def grow_to(self, _target):
            return None

        def commit(self, _actual):
            return None

        def release(self):
            return None

    def reserve(_ticket_id, requested_bytes):
        requested_sizes.append(requested_bytes)
        return Reservation()

    async def download(_session, _attachment, destination, _limit, _reservation):
        destination.write_bytes(b"archived body is larger than Discord metadata")
        return 48, "digest"

    monkeypatch.setattr(bot_module, "reserve_ticket_media", reserve)
    monkeypatch.setattr(bot_module, "_download_ticket_attachment", download)
    monkeypatch.setattr(
        bot_module,
        "settings",
        SimpleNamespace(
            ticket_attachment_max_bytes=100,
            ticket_attachment_message_max_bytes=100,
            ticket_attachment_download_timeout_seconds=5,
        ),
    )
    monkeypatch.setattr(
        bot_module,
        "storage_path",
        lambda _ticket_id, namespace, key: tmp_path / namespace / key,
    )

    result = asyncio.run(bot_module._archive_ticket_attachments(
        11,
        "msg-100",
        [SimpleNamespace(
            id="56",
            filename="proof.png",
            content_type="image/png",
            size=10,
            url="https://example.invalid/proof.png",
            proxy_url="",
        )],
        {"used": 0},
    ))

    assert requested_sizes == [0]
    assert result[0]["archive_status"] == "stored"
    assert result[0]["file_size"] == 48


def test_reference_snapshot_supports_discord_without_reference_type_enum(monkeypatch):
    from discord_bot import bot as bot_module

    monkeypatch.delattr(bot_module.discord, "MessageReferenceType", raising=False)
    reference = SimpleNamespace(
        type=SimpleNamespace(name="default", value=0),
        message_id=123,
        channel_id=None,
        guild_id=456,
    )
    message = SimpleNamespace(id=789, reference=reference, message_snapshots=[])

    result = asyncio.run(bot_module._build_ticket_reference_snapshot(
        11,
        message,
        {"used": 0},
        None,
    ))

    assert result["kind"] == "reply"
    assert result["source"]["message_id"] == "123"


def test_web_upload_returns_413_when_ticket_quota_is_full(tmp_path, monkeypatch):
    import config
    from fastapi import HTTPException
    from routes import tickets_api

    existing = tmp_path / "12" / "old"
    existing.mkdir(parents=True)
    (existing / "already.bin").write_bytes(b"12345")
    quota_settings = SimpleNamespace(
        ticket_attachments_dir=str(tmp_path),
        ticket_attachment_ticket_quota_bytes=5,
        ticket_attachment_global_quota_bytes=100,
    )
    monkeypatch.setattr(config, "get_settings", lambda: quota_settings)
    monkeypatch.setattr(
        tickets_api,
        "settings",
        SimpleNamespace(ticket_web_upload_max_bytes=100),
    )

    class Upload:
        filename = "proof.png"
        content_type = "image/png"
        size = 1

        async def read(self, _size):
            return b"x"

    with pytest.raises(HTTPException) as caught:
        asyncio.run(tickets_api._store_web_upload(12, "web-test", Upload()))

    assert caught.value.status_code == 413
    assert "квоте тикета" in str(caught.value.detail)
    assert not (tmp_path / "12" / "web-test").exists()


def test_cancelled_web_upload_removes_partial_file(tmp_path, monkeypatch):
    import config
    from routes import tickets_api

    quota_settings = SimpleNamespace(
        ticket_attachments_dir=str(tmp_path),
        ticket_attachment_ticket_quota_bytes=1_000,
        ticket_attachment_global_quota_bytes=1_000,
        ticket_attachment_max_bytes=100,
        ticket_web_upload_max_bytes=100,
    )
    monkeypatch.setattr(config, "get_settings", lambda: quota_settings)
    monkeypatch.setattr(
        tickets_api,
        "settings",
        SimpleNamespace(ticket_web_upload_max_bytes=100),
    )

    class Upload:
        filename = "proof.png"
        content_type = "image/png"
        size = 0
        reads = 0

        async def read(self, _size):
            self.reads += 1
            if self.reads == 1:
                return b"partial"
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tickets_api._store_web_upload(13, "web-test", Upload()))

    namespace = tmp_path / "13" / "web-test"
    assert not list(namespace.glob("*.part"))
    assert not [path for path in namespace.glob("*") if path.is_file()]


def test_cancelled_discord_download_removes_partial_file(tmp_path):
    from discord_bot import bot as bot_module

    destination = storage_path(14, "msg-1", new_storage_key("proof.png"), root=tmp_path)
    destination.parent.mkdir(parents=True)
    reservation = reserve_ticket_media(
        14,
        20,
        root=tmp_path,
        ticket_quota_bytes=100,
        global_quota_bytes=100,
    )

    class Content:
        async def iter_chunked(self, _size):
            yield b"partial"
            raise asyncio.CancelledError()

    class Response:
        status = 200
        content_length = None
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bot_module._download_ticket_attachment(
                Session(),
                {"url": "https://cdn.example.invalid/proof.png"},
                destination,
                20,
                reservation,
            ))
    finally:
        reservation.release()

    assert not destination.exists()
    assert not destination.with_name(f".{destination.name}.part").exists()


def test_ticket_history_backfill_pages_open_channel_and_continues_after_message_error(monkeypatch):
    from discord_bot import bot as bot_module

    class Author:
        bot = False

    class Message:
        def __init__(self, message_id):
            self.id = message_id
            self.author = Author()

    class Channel:
        async def history(self, *, limit, oldest_first, after=None):
            assert oldest_first is True
            after_id = int(after.id) if after else 0
            matching = [item for item in (Message(1), Message(2), Message(3)) if item.id > after_id]
            for item in matching[:limit]:
                yield item

    channel = Channel()  # Deliberately has no edit/delete operation.
    guild = SimpleNamespace(get_channel=lambda channel_id: channel if channel_id == 99 else None)
    monkeypatch.setattr(bot_module.bot, "get_guild", lambda _guild_id: guild)
    monkeypatch.setattr(
        bot_module.bot_data,
        "get_open_tickets",
        lambda: [{"id": 17, "discord_channel_id": "99"}],
    )
    monkeypatch.setattr(
        bot_module.bot_data,
        "get_ticket_message_by_discord_id",
        lambda *_args: {"attachments": [], "reference_snapshot": None},
    )
    monkeypatch.setattr(
        bot_module,
        "settings",
        SimpleNamespace(
            ticket_history_backfill_channels_per_run=1,
            ticket_history_backfill_messages_per_channel=2,
            ticket_history_backfill_pause_seconds=0,
        ),
    )
    attempted = []

    async def capture(ticket_id, message, *, strict):
        attempted.append((ticket_id, message.id, strict))
        if message.id == 1:
            raise RuntimeError("transient test failure")

    monkeypatch.setattr(bot_module, "_capture_ticket_message", capture)
    bot_module._ticket_history_backfill_offset = 0
    bot_module._ticket_history_backfill_after.clear()
    bot_module._ticket_history_backfill_retry.clear()

    asyncio.run(bot_module.ticket_history_backfill_task.coro())
    assert bot_module._ticket_history_backfill_after == {17: 2}
    asyncio.run(bot_module.ticket_history_backfill_task.coro())

    assert attempted == [
        (17, 1, False),
        (17, 2, False),
        (17, 3, False),
    ]
    assert bot_module._ticket_history_backfill_after == {}


def test_find_archived_attachment_searches_message_and_reference_snapshot():
    direct = {"storage_key": "direct.png", "filename": "direct.png"}
    forwarded = {"storage_key": "forwarded.mp4", "filename": "forwarded.mp4"}
    message = SimpleNamespace(
        attachments=[direct],
        reference_snapshot={"attachments": [forwarded]},
    )

    assert find_archived_attachment(message, "direct.png") is direct
    assert find_archived_attachment(message, "forwarded.mp4") is forwarded
    assert find_archived_attachment(message, "missing") is None


def test_find_archived_attachment_supports_raw_discord_snapshot_envelope_and_cycles():
    forwarded = {"storage_key": "snapshot.webp"}
    message = {
        "attachments": [],
        "message_snapshots": [{"message": {"attachments": [forwarded]}}],
    }
    message["reference_snapshot"] = message

    assert find_archived_attachment(message, "snapshot.webp") is forwarded


def test_serialize_attachment_uses_private_stable_url_and_drops_local_path():
    result = serialize_attachment(
        7,
        19,
        {
            "storage_key": "abc123.mp4",
            "filename": "proof.mp4",
            "content_type": "video/mp4",
            "file_size": 1234,
            "stored_path": "/private/uploads/tickets/7/abc123.mp4",
            "url": "https://cdn.discordapp.com/expired",
        },
    )

    assert result["url"] == "/api/v1/admin/tickets/7/messages/19/media/abc123.mp4"
    assert result["archived"] is True
    assert result["filename"] == "proof.mp4"
    assert "stored_path" not in result
    assert "/private/" not in repr(result)


def test_serialize_attachment_falls_back_to_legacy_url_without_storage_key():
    result = serialize_attachment(
        7,
        19,
        {"filename": "old.png", "url": "https://cdn.discordapp.com/legacy"},
    )

    assert result["url"] == "https://cdn.discordapp.com/legacy"
    assert result["archived"] is False


def test_serialize_attachment_never_builds_route_from_malformed_key():
    result = serialize_attachment(
        7,
        19,
        {
            "storage_key": "../secret.env",
            "source_url": "https://cdn.discordapp.com/fallback",
        },
    )

    assert result["url"] == "https://cdn.discordapp.com/fallback"
    assert result["archived"] is False
    assert "storage_key" not in result


def test_serialize_attachment_accepts_model_like_objects():
    raw = SimpleNamespace(
        storage_key="object-key.jpg",
        filename="object.jpg",
        content_type="image/jpeg",
        size=17,
        stored_path="/must/not/leak",
    )

    result = serialize_attachment(2, 3, raw)

    assert result["url"].endswith("/2/messages/3/media/object-key.jpg")
    assert result["size"] == 17
    assert "stored_path" not in result


def test_iter_file_range_is_inclusive_chunked_and_never_reads_past_end(tmp_path):
    path = tmp_path / "video.bin"
    path.write_bytes(bytes(range(32)))

    chunks = list(iter_file_range(path, 5, 17, chunk_size=4))

    assert [len(chunk) for chunk in chunks] == [4, 4, 4, 1]
    assert b"".join(chunks) == bytes(range(5, 18))


def test_iter_file_range_stops_cleanly_at_eof(tmp_path):
    path = tmp_path / "short.bin"
    path.write_bytes(b"abcdef")

    assert b"".join(iter_file_range(path, 4, 100, chunk_size=3)) == b"ef"


@pytest.mark.parametrize(
    ("start", "end", "chunk_size"),
    [(-1, 2, 1), (3, 2, 1), (True, 2, 1), (0, 2, 0), (0, 2, True)],
)
def test_iter_file_range_rejects_invalid_bounds(tmp_path, start, end, chunk_size):
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")

    with pytest.raises(ValueError):
        list(iter_file_range(path, start, end, chunk_size))
