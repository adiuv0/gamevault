# GameVault Large-Library Baseline

Measured: 2026-08-19

Command:

```powershell
python -m benchmarks.large_library --captures 1000 10000 --iterations 20
```

The benchmark creates a temporary SQLite database with deterministic metadata-only
rows. Ten percent of logical captures have a second physical variant. It measures
`screenshot_service.list_screenshots`, including logical grouping and card projection,
without mixing filesystem, image decode, browser rendering, or network latency into the
result.

## Results

| Logical captures | Physical assets | Page | Median | p95 | Maximum | 50-card payload |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 1,100 | 1 | 1.369 ms | 1.470 ms | 1.487 ms | 11,351 B |
| 1,000 | 1,100 | 20 | 1.549 ms | 1.663 ms | 1.703 ms | n/a |
| 10,000 | 11,000 | 1 | 11.932 ms | 12.561 ms | 13.073 ms | 11,401 B |
| 10,000 | 11,000 | 200 | 14.391 ms | 14.858 ms | 15.794 ms | n/a |

These results were collected on the maintainer's local Windows development machine and
are comparison data, not universal service-level targets.

## Interpretation

- SQLite list and grouping latency remains modest at 10,000 captures.
- Deep offset pagination adds measurable but currently small cost at this scale.
- Full-resolution viewer media was the higher-leverage bottleneck, so GameVault now uses
  bounded 1920px previews while preserving originals for download.
- List endpoints now return card-sized records and fetch physical-variant metadata only
  when a viewer opens. On the deterministic 1,000-capture fixture, the first 50 service
  records fell from 65,581 bytes to 11,351 bytes, an 82.7% reduction including the opaque
  media revision used for browser cache invalidation.
- Matching revisioned authenticated media URLs are privately cacheable and immutable for
  one year. Warm navigation is therefore handled by the browser without another route or
  database lookup; public media retains a one-day revocation window.
- Cursor pagination and virtualization should follow payload/browser measurements rather
  than being introduced solely from the database timings above.

## Reproducing Changes

Run the same command before and after a query or schema change on the same machine. Keep
the capture counts, variant ratio, and iteration count unchanged. Timing assertions are
deliberately not part of CI because shared-runner variance would make them unreliable.
