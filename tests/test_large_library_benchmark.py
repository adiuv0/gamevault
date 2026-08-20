"""Smoke tests for the reproducible large-library benchmark."""

import pytest


@pytest.mark.asyncio
async def test_large_library_benchmark_preserves_logical_counts():
    from benchmarks.large_library import run_case

    result = await run_case(120, iterations=2, variant_ratio=0.2)

    assert result["logical_captures"] == 120
    assert result["physical_assets"] > result["logical_captures"]
    assert result["first_page"]["items"] == 50
    assert result["first_page"]["total"] == 120
    assert result["first_page"]["payload_bytes"] > 0
    assert result["first_page"]["bytes_per_item"] > 0
    assert result["deep_page"]["page"] == 3
    assert result["deep_page"]["items"] == 20
    assert result["deep_page"]["total"] == 120
