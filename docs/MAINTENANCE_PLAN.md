# GameVault Maintainer Plan

This document tracks technical delivery and operational reliability. Product feature
priority lives in [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md).

## Objectives

1. Keep large screenshot libraries responsive on Unraid and similar storage.
2. Treat one captured moment as one screenshot even when several file variants exist.
3. Preserve originals and avoid destructive automatic duplicate cleanup.
4. Make performance, migrations, and releases measurable and repeatable.

## Phase 1: Performance Foundation

Status: Complete in `b5a93f4`.

- Store new thumbnails in configurable fast cache storage.
- Preserve legacy thumbnail paths and provide an explicit migration action.
- Use responsive grid images, private media caching, and narrow media queries.
- Add composite gallery indexes.
- Decode once for both thumbnail sizes and move image work off the async loop.
- Recalculate game statistics once per import batch.
- Add CI for backend tests, frontend lint/build, and container builds.

Acceptance criteria:

- Existing libraries remain readable before and after cache migration.
- The gallery query does not use a temporary sort tree for date ordering.
- One thumbnail decode produces both generated sizes and a perceptual fingerprint.
- Backend tests, frontend lint, and frontend production build pass.

## Phase 2: Duplicate Review And Variant UX

Status: In progress. Logical capture groups, new-import fingerprints, resumable backfill,
and the duplicate-review workflow are implemented.

- [x] Backfill perceptual hashes for existing assets as a resumable background job.
- [x] Record duplicate candidates with confidence, reasons, and review status.
- Automatically group only deterministic source siblings and tightly constrained matches.
- [x] Add a duplicate-review view for ambiguous candidates.
- [x] Collapse grouped assets into one gallery item with explicit file variants.
- [x] Move gallery favorites and annotations to the logical capture; keep sharing on
  the representative asset until share links become variant-aware.
- Define explicit whole-capture versus single-variant deletion controls before adding
  screenshot deletion to the gallery UI.
- Never delete a physical variant automatically.

Acceptance criteria:

- JPG, PNG, and JXR variants of one capture contribute one to game totals.
- Every physical original remains downloadable.
- Ambiguous matches require confirmation.
- Negative fixtures such as loading screens, repeated HUDs, and near-identical bursts do not
  auto-group.

## Phase 3: Large-Library Scaling

Status: In progress. Repeatable 1k/10k benchmarks, bounded viewer previews, slim payloads,
revisioned browser caching, cursor pagination, virtualized galleries, and durable
filesystem imports are implemented.

- [x] Add a browser-compatible large preview so the viewer does not fetch 4K originals by default.
- [x] Virtualize long admin/public galleries and replace their deep offset pagination
  with opaque cursor pagination. Numbered pages remain compatible for older clients.
- [x] Return slim list payloads and fetch full metadata only when requested.
- [x] Version media URLs so warm authenticated navigation stays in the browser cache.
- [x] Replace Steam and Special K request-bound tasks with a durable SQLite-backed job
  worker. Browser upload-body staging remains separate work.
- [x] Add a reproducible gallery service benchmark at 1,000 and 10,000 captures.
- [x] Add bounded structured runtime timings for media delivery, gallery queries,
  image processing, and each import source, with threshold logs and a Settings view.

Acceptance criteria:

- [x] Benchmark libraries at 1,000 and 10,000 logical captures.
- [x] Warm gallery navigation reuses cached media without application database work.
- [ ] All imports survive an application restart and resume without duplicated rows.
  Steam and Special K satisfy this criterion; browser uploads do not yet.

## Phase 4: Release And Recovery

- Add migration tests for upgrades from every supported release.
- Exercise backup and restore against databases with active WAL files.
- Add dependency and container vulnerability checks to CI.
- Synchronize application, API, package, and documentation versions.
- Publish migration notes and rollback instructions with each schema release.

## Maintainer Rules

- Optimize from measurements and query plans, not database-fashion changes.
- Keep SQLite while the single-user deployment model remains dominant.
- Prefer additive, reversible migrations before API-breaking normalization.
- Keep generated previews disposable; originals and metadata are authoritative.
- Require tests for every migration, matching threshold, and destructive workflow.
