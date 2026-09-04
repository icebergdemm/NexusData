"""Streaming request parsers with application-level size limits.

Starlette 0.27 limits the number of multipart fields, but it does not reject an
oversized file until application code reads the already-spooled UploadFile.
Ticket evidence can contain videos, so the boundary must be enforced while the
HTTP body is still being parsed rather than after temporary disk is consumed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from starlette.datastructures import FormData, Headers
from starlette.formparsers import MultiPartException, MultiPartParser


class MultipartLimitExceeded(ValueError):
    """Raised when a multipart request exceeds a configured hard limit."""


class _BoundedMultiPartParser(MultiPartParser):
    def __init__(
        self,
        headers: Headers,
        stream: AsyncIterator[bytes],
        *,
        max_files: int,
        max_fields: int,
        max_file_bytes: int,
        max_field_bytes: int,
    ) -> None:
        super().__init__(
            headers,
            stream,
            max_files=max_files,
            max_fields=max_fields,
        )
        self._max_file_bytes = max_file_bytes
        self._max_field_bytes = max_field_bytes
        self._current_part_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_part_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self._current_part_bytes += end - start
        limit = (
            self._max_file_bytes
            if self._current_part.file is not None
            else self._max_field_bytes
        )
        if self._current_part_bytes > limit:
            kind = "File" if self._current_part.file is not None else "Form field"
            raise MultiPartException(f"{kind} exceeds the {limit}-byte limit")
        super().on_part_data(data, start, end)


async def parse_bounded_multipart(
    headers: Headers,
    stream: AsyncIterator[bytes],
    *,
    max_files: int,
    max_fields: int = 10,
    max_file_bytes: int,
    max_body_bytes: int,
    max_field_bytes: int = 16 * 1024,
) -> FormData:
    """Parse multipart data while bounding raw body and every individual part."""

    content_length = headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError) as exc:
            raise MultipartLimitExceeded("Invalid Content-Length header") from exc
        if declared_length < 0:
            raise MultipartLimitExceeded("Invalid Content-Length header")
        if declared_length > max_body_bytes:
            raise MultipartLimitExceeded(
                f"Request body exceeds the {max_body_bytes}-byte limit"
            )

    async def bounded_stream() -> AsyncIterator[bytes]:
        received = 0
        async for chunk in stream:
            received += len(chunk)
            if received > max_body_bytes:
                raise MultiPartException(
                    f"Request body exceeds the {max_body_bytes}-byte limit"
                )
            yield chunk

    parser = _BoundedMultiPartParser(
        headers,
        bounded_stream(),
        max_files=max_files,
        max_fields=max_fields,
        max_file_bytes=max_file_bytes,
        max_field_bytes=max_field_bytes,
    )
    try:
        return await parser.parse()
    except MultiPartException as exc:
        raise MultipartLimitExceeded(str(exc)) from exc
