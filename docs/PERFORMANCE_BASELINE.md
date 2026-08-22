# GameVault Large-Library Baseline

Measured: 2026-08-21

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
| 1,000 | 1,100 | 1 | 1.493 ms | 1.631 ms | 1.724 ms | 11,351 B |
| 1,000 | 1,100 | 20 offset | 1.656 ms | 1.893 ms | 2.032 ms | n/a |
| 1,000 | 1,100 | 20 cursor | 1.267 ms | 1.377 ms | 1.492 ms | n/a |
| 10,000 | 11,000 | 1 | 12.308 ms | 12.712 ms | 13.382 ms | 11,401 B |
| 10,000 | 11,000 | 200 offset | 14.850 ms | 15.360 ms | 15.994 ms | n/a |
| 10,000 | 11,000 | 200 cursor | 9.758 ms | 10.501 ms | 10.622 ms | n/a |

These results were collected on the maintainer's local Windows development machine and
are comparison data, not universal service-level targets.

## Interpretation

- SQLite list and grouping latency remains modest at 10,000 captures.
- Cursor traversal avoids the growing deep-page offset cost while numbered-page callers
  remain compatible.
- Gallery cards use bounded thumbnails. Opening a card requests its full-resolution
  original, while browser-incompatible formats use a bounded 1920px fallback preview.
- List endpoints now return card-sized records and fetch physical-variant metadata only
  when a viewer opens. On the deterministic 1,000-capture fixture, the first 50 service
  records fell from 65,581 bytes to 11,351 bytes, an 82.7% reduction including the opaque
  media revision used for browser cache invalidation.
- Matching revisioned authenticated media URLs are privately cacheable and immutable for
  one year. Warm navigation is therefore handled by the browser without another route or
  database lookup; public media retains a one-day revocation window.
- Runtime diagnostic recording is active during this benchmark. Its bounded aggregate
  bookkeeping leaves the 10,000-capture cursor median below 10 ms on this machine.

## Reproducing Changes

Run the same command before and after a query or schema change on the same machine. Keep
the capture counts, variant ratio, and iteration count unchanged. Timing assertions are
deliberately not part of CI because shared-runner variance would make them unreliable.
