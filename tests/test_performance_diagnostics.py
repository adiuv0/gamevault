"""Runtime performance diagnostics tests."""

import asyncio
import logging

import pytest

import backend.services.performance_diagnostics as diagnostics_module
from backend.services.performance_diagnostics import (
    SAMPLE_LIMIT,
    MediaTimingMiddleware,
    PerformanceRecorder,
    measure_async,
    measure_sync,
    recorder,
)


def test_recorder_aggregates_percentiles_errors_and_bytes(caplog):
    diagnostics = PerformanceRecorder()

    with caplog.at_level(logging.WARNING):
        diagnostics.record(
            "media",
            "private.preview",
            100,
            threshold_ms=250,
            byte_count=10,
        )
        diagnostics.record(
            "media",
            "private.preview",
            300,
            threshold_ms=250,
            error=True,
            byte_count=20,
        )

    snapshot = diagnostics.snapshot()
    assert snapshot["total_measurements"] == 2
    assert snapshot["sample_limit"] == SAMPLE_LIMIT

    metric = snapshot["metrics"][0]
    assert metric["average_ms"] == 200
    assert metric["p50_ms"] == 100
    assert metric["p95_ms"] == 300
    assert metric["max_ms"] == 300
    assert metric["slow_count"] == 1
    assert metric["error_count"] == 1
    assert metric["recent_error_count"] == 1
    assert metric["total_bytes"] == 30
    assert "slow_operation stage=media operation=private.preview" in caplog.text


def test_recorder_keeps_a_bounded_recent_percentile_window():
    diagnostics = PerformanceRecorder()
    for duration in range(SAMPLE_LIMIT + 10):
        diagnostics.record(
            "database",
            "gallery_cursor",
            duration,
            threshold_ms=1000,
        )

    metric = diagnostics.snapshot()["metrics"][0]
    assert metric["count"] == SAMPLE_LIMIT + 10
    assert metric["p50_ms"] == 137
    assert metric["p95_ms"] == 253


def test_recent_errors_age_out_without_losing_lifetime_total():
    diagnostics = PerformanceRecorder()
    diagnostics.record("media", "private.preview", 10, threshold_ms=750, error=True)
    for _ in range(SAMPLE_LIMIT):
        diagnostics.record("media", "private.preview", 10, threshold_ms=750)

    metric = diagnostics.snapshot()["metrics"][0]
    assert metric["error_count"] == 1
    assert metric["recent_error_count"] == 0


def test_slow_warnings_are_rate_limited(monkeypatch, caplog):
    diagnostics = PerformanceRecorder()
    ticks = iter((0, 10, 60))
    monkeypatch.setattr(diagnostics_module, "perf_counter", lambda: next(ticks))

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            diagnostics.record("media", "private.original", 800, threshold_ms=750)

    assert caplog.text.count("slow_operation stage=media operation=private.original") == 2


def test_measure_decorators_preserve_results_and_record_failures():
    recorder.reset()

    @measure_sync("image", "test_sync", threshold_ms=1000)
    def sync_operation() -> str:
        return "ok"

    @measure_async("database", "test_async", threshold_ms=1000)
    async def async_operation() -> None:
        raise RuntimeError("expected")

    assert sync_operation() == "ok"
    with pytest.raises(RuntimeError, match="expected"):
        asyncio.run(async_operation())

    metrics = {
        metric["operation"]: metric for metric in recorder.snapshot()["metrics"]
    }
    assert metrics["test_sync"]["error_count"] == 0
    assert metrics["test_async"]["error_count"] == 1


@pytest.mark.asyncio
async def test_media_middleware_records_stream_completion_and_bytes():
    recorder.reset()
    sent: list[dict] = []

    async def media_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"abc", "more_body": True})
        await send({"type": "http.response.body", "body": b"def"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = MediaTimingMiddleware(media_app)
    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/gallery/screenshots/1/thumb/md",
        },
        receive,
        send,
    )

    assert len(sent) == 3
    metric = recorder.snapshot()["metrics"][0]
    assert metric["stage"] == "media"
    assert metric["operation"] == "public.thumbnail"
    assert metric["count"] == 1
    assert metric["total_bytes"] == 6


@pytest.mark.asyncio
async def test_media_middleware_ignores_non_media_requests():
    recorder.reset()

    async def api_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    await MediaTimingMiddleware(api_app)(
        {"type": "http", "method": "GET", "path": "/api/settings"},
        receive,
        send,
    )

    assert recorder.snapshot()["metrics"] == []


@pytest.mark.asyncio
async def test_media_middleware_counts_pathsend_files(tmp_path):
    recorder.reset()
    media_file = tmp_path / "preview.jpg"
    media_file.write_bytes(b"12345678")

    async def media_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.pathsend", "path": str(media_file)})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    await MediaTimingMiddleware(media_app)(
        {"type": "http", "method": "GET", "path": "/api/screenshots/1/preview"},
        receive,
        send,
    )

    metric = recorder.snapshot()["metrics"][0]
    assert metric["count"] == 1
    assert metric["total_bytes"] == 8


def test_reset_drops_measurements():
    diagnostics = PerformanceRecorder()
    diagnostics.record("import", "steam_file", 50, threshold_ms=5000)

    snapshot = diagnostics.reset()

    assert snapshot["total_measurements"] == 0
    assert snapshot["metrics"] == []


@pytest.mark.asyncio
async def test_settings_routes_return_and_reset_diagnostics():
    from backend.routers.settings import (
        get_performance_diagnostics,
        reset_performance_diagnostics,
    )

    recorder.reset()
    recorder.record("database", "gallery_cursor", 25, threshold_ms=250)

    snapshot = await get_performance_diagnostics()
    assert snapshot["total_measurements"] == 1

    reset = await reset_performance_diagnostics()
    assert reset["total_measurements"] == 0
