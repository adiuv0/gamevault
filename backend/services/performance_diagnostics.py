"""Bounded, privacy-preserving runtime performance diagnostics."""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import Any

SAMPLE_LIMIT = 256
SLOW_LOG_INTERVAL_SECONDS = 60

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return ordered[index]


@dataclass
class _Metric:
    threshold_ms: float
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLE_LIMIT))
    error_samples: deque[int] = field(default_factory=lambda: deque(maxlen=SAMPLE_LIMIT))
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    slow_count: int = 0
    error_count: int = 0
    total_bytes: int = 0
    last_seen_at: str | None = None
    last_slow_log_at: float | None = None

    def add(self, duration_ms: float, *, error: bool, byte_count: int) -> None:
        self.samples.append(duration_ms)
        self.error_samples.append(int(error))
        self.count += 1
        self.total_ms += duration_ms
        self.max_ms = max(self.max_ms, duration_ms)
        self.slow_count += int(duration_ms >= self.threshold_ms)
        self.error_count += int(error)
        self.total_bytes += max(0, byte_count)
        self.last_seen_at = _utc_now()

    def snapshot(self, stage: str, operation: str) -> dict[str, Any]:
        samples = list(self.samples)
        return {
            "stage": stage,
            "operation": operation,
            "count": self.count,
            "average_ms": round(self.total_ms / self.count, 2),
            "p50_ms": round(_percentile(samples, 50), 2),
            "p95_ms": round(_percentile(samples, 95), 2),
            "max_ms": round(self.max_ms, 2),
            "threshold_ms": self.threshold_ms,
            "slow_count": self.slow_count,
            "error_count": self.error_count,
            "recent_error_count": sum(self.error_samples),
            "total_bytes": self.total_bytes,
            "last_seen_at": self.last_seen_at,
        }


class PerformanceRecorder:
    """Keep aggregate timings without retaining paths, filenames, or request data."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[tuple[str, str], _Metric] = {}
        self._started_at = _utc_now()

    def record(
        self,
        stage: str,
        operation: str,
        duration_ms: float,
        *,
        threshold_ms: float,
        error: bool = False,
        byte_count: int = 0,
    ) -> None:
        duration_ms = max(0.0, duration_ms)
        now = perf_counter()
        with self._lock:
            metric = self._metrics.setdefault(
                (stage, operation),
                _Metric(threshold_ms=threshold_ms),
            )
            metric.add(duration_ms, error=error, byte_count=byte_count)
            should_log = duration_ms >= metric.threshold_ms and (
                metric.last_slow_log_at is None
                or now - metric.last_slow_log_at >= SLOW_LOG_INTERVAL_SECONDS
            )
            if should_log:
                metric.last_slow_log_at = now

        if should_log:
            logger.warning(
                "slow_operation stage=%s operation=%s duration_ms=%.2f threshold_ms=%.2f",
                stage,
                operation,
                duration_ms,
                metric.threshold_ms,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            metrics = [
                metric.snapshot(stage, operation)
                for (stage, operation), metric in sorted(self._metrics.items())
            ]
            started_at = self._started_at

        return {
            "started_at": started_at,
            "generated_at": _utc_now(),
            "sample_limit": SAMPLE_LIMIT,
            "total_measurements": sum(metric["count"] for metric in metrics),
            "metrics": metrics,
        }

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._metrics.clear()
            self._started_at = _utc_now()
        return self.snapshot()


recorder = PerformanceRecorder()


@contextmanager
def measure(
    stage: str,
    operation: str,
    *,
    threshold_ms: float,
) -> Iterator[None]:
    """Measure a block and record failures without suppressing them."""
    started = perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        recorder.record(
            stage,
            operation,
            (perf_counter() - started) * 1000,
            threshold_ms=threshold_ms,
            error=failed,
        )


def measure_sync(
    stage: str,
    operation: str,
    *,
    threshold_ms: float,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            with measure(stage, operation, threshold_ms=threshold_ms):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def measure_async(
    stage: str,
    operation: str,
    *,
    threshold_ms: float,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            with measure(stage, operation, threshold_ms=threshold_ms):
                return await function(*args, **kwargs)

        return wrapped

    return decorate


def get_snapshot() -> dict[str, Any]:
    return recorder.snapshot()


def reset_snapshot() -> dict[str, Any]:
    return recorder.reset()


def _media_operation(path: str) -> str:
    visibility = "public" if path.startswith(("/api/gallery/", "/share/")) else "private"
    if "/thumb/" in path:
        kind = "thumbnail"
    elif path.endswith("/preview"):
        kind = "preview"
    elif path.endswith("/cover") or "/cover" in path:
        kind = "cover"
    else:
        kind = "original"
    return f"{visibility}.{kind}"


def _is_media_path(path: str) -> bool:
    return (
        "/thumb/" in path
        or path.endswith(("/preview", "/image", "/cover"))
    )


class MediaTimingMiddleware:
    """Measure media responses through their final streamed body chunk."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope.get("type") != "http" or not _is_media_path(path):
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500
        byte_count = 0
        recorded = False

        async def send_timed(message: dict[str, Any]) -> None:
            nonlocal status_code, byte_count, recorded
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                byte_count += len(message.get("body", b""))
            elif message["type"] == "http.response.pathsend":
                with suppress(KeyError, OSError):
                    byte_count += Path(message["path"]).stat().st_size
            await send(message)
            if (
                message["type"] == "http.response.pathsend"
                or message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                record(error=status_code >= 400)

        def record(*, error: bool) -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            recorder.record(
                "media",
                _media_operation(path),
                (perf_counter() - started) * 1000,
                threshold_ms=750,
                error=error,
                byte_count=byte_count,
            )

        try:
            await self.app(scope, receive, send_timed)
        except BaseException:
            record(error=True)
            raise
        finally:
            if not recorded:
                record(error=status_code >= 400)
