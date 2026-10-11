"""Authenticate and bound multipart uploads before FastAPI spools their bodies."""

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from starlette.formparsers import MultiPartException, MultiPartParser

from backend.auth import require_auth
from backend.config import settings


class LimitedMultiPartParser(MultiPartParser):
    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._part_bytes = 0
        self._header_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self._part_bytes += end - start
        if self._current_part.file and self._part_bytes > settings.max_upload_size_bytes:
            raise HTTPException(413, f"File exceeds {settings.max_upload_size_mb} MiB limit")
        super().on_part_data(data, start, end)

    def _check_header_size(self, size: int) -> None:
        self._header_bytes += size
        if self._header_bytes > 16384:
            raise MultiPartException("Multipart headers are too large")

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._check_header_size(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._check_header_size(end - start)
        super().on_header_value(data, start, end)


class UploadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded_upload(request: Request):
            if request.method != "POST":
                return await handler(request)
            await require_auth(request)
            limit = settings.max_upload_batch_size_mb * 1024 * 1024
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                raise HTTPException(400, "Invalid Content-Length")
            if length > limit:
                raise HTTPException(413, f"Batch exceeds {settings.max_upload_batch_size_mb} MiB limit")
            if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
                raise HTTPException(415, "Expected multipart/form-data")

            async def bounded_stream():
                received = 0
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > limit:
                        raise HTTPException(413, f"Batch exceeds {settings.max_upload_batch_size_mb} MiB limit")
                    yield chunk

            parser = LimitedMultiPartParser(
                request.headers, bounded_stream(),
                max_files=settings.max_upload_files, max_fields=4, max_part_size=16384,
            )
            try:
                form = await parser.parse()
            except MultiPartException as error:
                raise HTTPException(400, error.message) from error
            # FastAPI reuses the parsed form instead of invoking its unbounded parser.
            request._form = form
            try:
                return await handler(request)
            finally:
                await form.close()

        return bounded_upload
