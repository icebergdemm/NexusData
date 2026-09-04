from __future__ import annotations

import asyncio

import pytest
from starlette.datastructures import Headers

from services_request_limits import MultipartLimitExceeded, parse_bounded_multipart


def _multipart_body(payload: bytes, *, boundary: str = "ticket-boundary") -> bytes:
    return (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="content"\r\n\r\n'
        "proof\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="proof.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()


async def _chunks(body: bytes, size: int = 7):
    for offset in range(0, len(body), size):
        yield body[offset:offset + size]


def test_bounded_multipart_accepts_file_within_limits():
    body = _multipart_body(b"evidence")
    headers = Headers({
        "content-type": "multipart/form-data; boundary=ticket-boundary",
        "content-length": str(len(body)),
    })

    async def run():
        form = await parse_bounded_multipart(
            headers,
            _chunks(body),
            max_files=2,
            max_file_bytes=16,
            max_body_bytes=len(body) + 1,
        )
        upload = form.get("files")
        assert upload is not None
        assert await upload.read() == b"evidence"
        await upload.close()

    asyncio.run(run())


def test_bounded_multipart_rejects_file_during_streaming():
    body = _multipart_body(b"0123456789")
    headers = Headers({
        "content-type": "multipart/form-data; boundary=ticket-boundary",
    })

    async def run():
        with pytest.raises(MultipartLimitExceeded, match="File exceeds"):
            await parse_bounded_multipart(
                headers,
                _chunks(body, size=3),
                max_files=2,
                max_file_bytes=4,
                max_body_bytes=len(body) + 1,
            )

    asyncio.run(run())


def test_bounded_multipart_rejects_declared_body_before_reading_stream():
    consumed = False

    async def stream():
        nonlocal consumed
        consumed = True
        yield b"should-not-be-read"

    headers = Headers({
        "content-type": "multipart/form-data; boundary=ticket-boundary",
        "content-length": "500",
    })

    async def run():
        with pytest.raises(MultipartLimitExceeded, match="Request body exceeds"):
            await parse_bounded_multipart(
                headers,
                stream(),
                max_files=2,
                max_file_bytes=10,
                max_body_bytes=100,
            )

    asyncio.run(run())
    assert consumed is False
