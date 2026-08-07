# GameVault Large-Library Baseline

Measured: 2026-08-07

Command:

```powershell
python -m benchmarks.large_library --captures 1000 10000 --iterations 20
```

The benchmark creates a temporary SQLite database with deterministic metadata-only
rows. Ten percent of logical captures have a second physical variant. It measures
`screenshot_service.list_screenshots`, including logical grouping and variant assembly,
without mixing filesystem, image decode, browser rendering, or network latency into the
result.

## Results

| Logical captures | Physical assets | Page | Median | p95 | Maximum |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 1,100 | 1 | 1.515 ms | 1.718 ms | 1.731 ms |
| 1,000 | 1,100 | 20 | 1.722 ms | 1.840 ms | 1.851 ms |
| 10,000 | 11,000 | 1 | 13.020 ms | 13.892 ms | 14.218 ms |
| 10,000 | 11,000 | 200 | 15.337 ms | 17.215 ms | 18.532 ms |

These results were collected on the maintainer's local Windows development machine and
are comparison data, not universal service-level targets.

## Interpretation

- SQLite list and grouping latency remains modest at 10,000 captures.
- Deep offset pagination adds measurable but currently small cost at this scale.
- Full-resolution viewer media was the higher-leverage bottleneck, so GameVault now uses
  bounded 1920px previews while preserving originals for download.
- Cursor pagination and virtualization should follow payload/browser measurements rather
  than being introduced solely from the database timings above.

## Reproducing Changes

Run the same command before and after a query or schema change on the same machine. Keep
the capture counts, variant ratio, and iteration count unchanged. Timing assertions are
deliberately not part of CI because shared-runner variance would make them unreliable.
