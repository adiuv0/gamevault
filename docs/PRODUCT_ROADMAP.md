# GameVault Product Roadmap

Updated: 2026-08-07

This document owns product direction and feature priority. The technical delivery
sequence lives in [MAINTENANCE_PLAN.md](MAINTENANCE_PLAN.md).

## Product Direction

GameVault should turn a large screenshot library into a curated personal game-memory
archive, not merely store image files.

Guiding principles:

- Large imports must remain fast and easy to triage.
- One captured moment is one logical capture, even when several file variants exist.
- Originals are preserved unless the user explicitly chooses a destructive action.
- Public visibility is intentional, inspectable, and easy to change in bulk.
- Search and organization should describe why an image matters, not only its filename.
- Expensive features must run locally, durably, and optionally.

## Current Foundation

The following product foundations are implemented:

- Steam, manual, and Special K imports with HDR-aware thumbnail generation.
- Logical capture groups for JPG, PNG, JXR, and other variants.
- Perceptual duplicate scanning, review decisions, and resumable backfill.
- One gallery card per logical capture with access to every original variant.
- Timeline, full-text search, annotations, favorites, sharing, and public gallery.
- Configurable thumbnail cache and the first large-library performance improvements.

The public gallery remaining unauthenticated and new games defaulting to public are
intentional product behavior. The roadmap adds visibility review tools, not an auth
requirement for that surface.

## P0: Large-Library Scaling

This is the current priority because performance degradation directly undermines every
later curation feature.

Deliverables:

- Reproducible benchmarks at 1,000 and 10,000 logical captures.
- Structured timings for list queries, media responses, imports, and image processing.
- Browser-friendly large previews so the viewer does not fetch 4K/HDR originals by
  default.
- Slim gallery payloads with full metadata fetched only when needed.
- Cursor pagination and gallery virtualization where measurements justify them.
- Durable import jobs that resume cleanly after restart.

Exit criteria:

- Warm gallery navigation reuses cached media without database work.
- Viewer bandwidth is bounded by preview size unless the original is requested.
- Imports survive restart without duplicate rows or lost progress.

## P1: Collections

Add user-defined collections containing logical captures from any game.

Examples include Best of 2026, Boss Kills, Architecture, Weird UI Moments, and
Screenshots to Share.

Initial scope:

- Create, rename, describe, and delete collections.
- Add or remove captures from the viewer, grid, search, timeline, and inbox.
- Collection detail page with optional cover capture.
- Optional public collection page with a separate slim response model.
- Preserve membership when capture variants are grouped or games are merged.

Reordering can follow after the core membership workflow proves useful.

## P1: Inbox And Bulk Triage

New imports and uploads should enter a review workflow without becoming a separate
storage location.

Initial scope:

- Nullable `reviewed_at` state on logical captures.
- Keyboard-friendly bulk selection.
- Mark reviewed, favorite, archive, change visibility, add to collection, and delete.
- Filter by game, source, date, duplicate status, and available variants.
- Explicit distinction between deleting one physical variant and deleting the complete
  logical capture.

Imports should default to unreviewed. A later “review later” state is only worth adding
if the simple reviewed/unreviewed model proves insufficient.

## P2: Tags And Saved Filters

Tags describe why a capture matters; saved filters make useful queries reusable.

Initial scope:

- Add and remove tags from logical captures.
- Tag management and bulk tagging.
- Filter library, search, timeline, collections, and inbox by tags.
- Save filters such as Untagged, Favorites from 2026, Public but Uncollected, Has
  Annotation, and Has HDR Variant.

The placeholder tag tables were removed in migration `m005`; this feature requires a
new schema designed around logical captures rather than restoring the old tables.

## P2: Public Surface Review

Add a calm, factual page showing exactly what unauthenticated visitors can see.

Initial scope:

- Public games, collections, active share links, and total public captures.
- Preview as an unauthenticated visitor.
- Review public items and make selected items private in bulk.
- Surface annotations or metadata that will appear publicly.
- No alarm banner merely because the intentionally public gallery is enabled.

## P2: Rich Sharing And Public Derivatives

Move sharing from a physical representative file to the logical capture and allow each
link to define its exposure.

Controls:

- Expiration, revocation, and optional passphrase.
- Select which variant is shown or allow viewer switching.
- Allow original download on or off.
- Show annotation, game metadata, dimensions, and date independently.
- Serve a metadata-stripped derivative when originals should remain authenticated.

Existing links should retain compatible, conservative defaults during migration.

## P3: OCR Search

Run optional local OCR and index dialogue, quest text, subtitles, signs, item names,
and other visible text.

Requirements:

- Durable background processing for new and existing captures.
- Configurable local engine and language.
- Search integration with confidence and engine metadata.
- OCR text visible in capture details with an option to hide it.
- CPU and storage costs clearly exposed before enabling the feature.

## P3: Play Sessions

Group captures from the same game into narrative play sessions using configurable time
gaps. Start with computed groups; persist sessions only when users can title, annotate,
or curate them as objects.

Potential actions include adding a session to a collection and viewing sessions in the
timeline.

## P3: Watch Folders

Periodically scan configured Steam and Special K folders and send new captures to the
inbox.

Start with scheduled scans because filesystem watchers are unreliable across Docker and
network mounts. Rescans must remain idempotent through source keys and content hashes.

## Duplicate Detection Follow-Up

Near-duplicate detection is implemented. Remaining work is safety and lifecycle polish:

- Negative fixtures for repeated HUDs, loading screens, and near-identical bursts.
- Threshold diagnostics and explainable candidate reasons.
- Variant-aware share links.
- Explicit single-variant versus whole-capture deletion controls.
- No automatic deletion under any matching confidence.

## Sequencing

1. Large-library benchmarks, diagnostics, and preview cache.
2. Slim/cursor gallery APIs and durable import jobs as measurements require.
3. Collections.
4. Inbox and bulk triage.
5. Tags, saved filters, and public surface review.
6. Rich sharing and metadata-stripped public derivatives.
7. OCR, play sessions, and watch folders.

Collections and inbox come before OCR or automation because they provide immediate
curation value and establish the capture-level actions those later systems need.

## Feature Standards

- New organization features operate on logical captures, not accidental file variants.
- Public APIs use explicit slim response models.
- Background work is restart-safe before it is enabled automatically.
- Destructive operations name their scope and require confirmation.
- Every migration, matching threshold, and destructive workflow receives focused tests.
- Performance claims include a repeatable measurement and library size.
