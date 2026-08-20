"""Benchmark logical-capture gallery queries at realistic library sizes.

Run from the repository root:

    python -m benchmarks.large_library --captures 1000 10000 --iterations 20

The benchmark uses a temporary database and metadata-only screenshot rows. It measures
the API service query/assembly path without mixing filesystem or network performance into
the result.
"""

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "median_ms": round(statistics.median(values) * 1000, 3),
        "p95_ms": round(_percentile(values, 0.95) * 1000, 3),
        "max_ms": round(max(values) * 1000, 3),
    }


async def _seed_library(db, capture_count: int, variant_ratio: float) -> tuple[int, int]:
    cursor = await db.execute(
        "INSERT INTO games (name, folder_name) VALUES ('Benchmark', 'Benchmark')"
    )
    game_id = cursor.lastrowid
    start = datetime(2020, 1, 1, tzinfo=UTC)
    rows = []
    screenshot_id = 1

    for capture_index in range(capture_count):
        group_id = screenshot_id
        taken_at = (start + timedelta(minutes=capture_index)).isoformat()
        rows.append(
            (
                screenshot_id,
                game_id,
                f"capture-{capture_index:06d}.png",
                f"Benchmark/screenshots/capture-{capture_index:06d}.png",
                3840,
                2160,
                "png",
                taken_at,
                group_id,
            )
        )
        screenshot_id += 1

        interval = round(1 / variant_ratio) if variant_ratio > 0 else 0
        if interval and capture_index % interval == 0:
            rows.append(
                (
                    screenshot_id,
                    game_id,
                    f"capture-{capture_index:06d}.jxr",
                    f"Benchmark/screenshots/capture-{capture_index:06d}.jxr",
                    3840,
                    2160,
                    "jxr",
                    taken_at,
                    group_id,
                )
            )
            screenshot_id += 1

    await db.executemany(
        """INSERT INTO screenshots
           (id, game_id, filename, file_path, width, height, format, taken_at,
            capture_group_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.execute(
        "UPDATE games SET screenshot_count = ? WHERE id = ?",
        (capture_count, game_id),
    )
    await db.commit()
    return game_id, len(rows)


async def run_case(
    capture_count: int,
    iterations: int = 20,
    variant_ratio: float = 0.1,
) -> dict:
    from backend.config import settings
    from backend.database import close_db, get_db, init_db
    from backend.services.screenshot_service import list_screenshots

    await close_db()
    with tempfile.TemporaryDirectory(prefix="gamevault-benchmark-") as temp_dir:
        original_db_path = settings.db_path
        settings.db_path = Path(temp_dir) / "benchmark.db"
        try:
            started = time.perf_counter()
            await init_db()
            db = await get_db()
            game_id, physical_count = await _seed_library(
                db, capture_count, variant_ratio
            )
            seed_seconds = time.perf_counter() - started

            deep_page = max(1, (capture_count + 49) // 50)
            await list_screenshots(game_id, 1, 50)
            await list_screenshots(game_id, deep_page, 50)

            first_page_times = []
            deep_page_times = []
            for _ in range(iterations):
                started = time.perf_counter()
                first_items, total = await list_screenshots(game_id, 1, 50)
                first_page_times.append(time.perf_counter() - started)

                started = time.perf_counter()
                deep_items, deep_total = await list_screenshots(
                    game_id, deep_page, 50
                )
                deep_page_times.append(time.perf_counter() - started)

            first_page_payload = json.dumps(
                first_items, separators=(",", ":")
            ).encode()

            return {
                "logical_captures": capture_count,
                "physical_assets": physical_count,
                "variant_ratio": variant_ratio,
                "iterations": iterations,
                "seed_seconds": round(seed_seconds, 3),
                "first_page": {
                    **_summary(first_page_times),
                    "items": len(first_items),
                    "total": total,
                    "payload_bytes": len(first_page_payload),
                    "bytes_per_item": round(
                        len(first_page_payload) / max(1, len(first_items)), 1
                    ),
                },
                "deep_page": {
                    **_summary(deep_page_times),
                    "page": deep_page,
                    "items": len(deep_items),
                    "total": deep_total,
                },
            }
        finally:
            await close_db()
            settings.db_path = original_db_path


async def _run(args: argparse.Namespace) -> None:
    results = []
    for capture_count in args.captures:
        results.append(
            await run_case(capture_count, args.iterations, args.variant_ratio)
        )
    print(json.dumps({"benchmark": "large_library", "results": results}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--captures",
        nargs="+",
        type=int,
        default=[1000, 10000],
        help="Logical capture counts to seed",
    )
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--variant-ratio", type=float, default=0.1)
    args = parser.parse_args()
    if any(value < 1 for value in args.captures):
        parser.error("capture counts must be positive")
    if args.iterations < 1:
        parser.error("iterations must be positive")
    if not 0 <= args.variant_ratio <= 1:
        parser.error("variant ratio must be between 0 and 1")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
