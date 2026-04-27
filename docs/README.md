# GameVault Documentation

Reference documentation for GameVault, organized by audience.

## For users of the web UI

[**USER_GUIDE.md**](USER_GUIDE.md) — feature-by-feature walkthrough. Start
here if you've just deployed GameVault and want to know what each page
does. Covers authentication, library, uploads, both importers (Steam +
Special K), timeline, search, annotations, sharing, public gallery,
settings, and the desktop sync CLI.

## For deployers and operators

[**CONFIGURATION.md**](CONFIGURATION.md) — every environment variable, every
DB-stored preference, library layout on disk, backup procedure, migration
mechanism, reverse-proxy notes, sample `.env`. Read this when you're
setting up GameVault or troubleshooting a config issue.

## For developers and integrators

[**API_REFERENCE.md**](API_REFERENCE.md) — every REST endpoint with method,
path, parameters, request/response shapes, and SSE event vocabularies.
Covers admin and public endpoints.

[**ARCHITECTURE.md**](ARCHITECTURE.md) — how the code is organized, the
services/routers split, the image pipeline (with HDR specifics), Steam
scraping mechanics, SSE patterns, frontend structure, Docker build, and
known constraints. Read this before forking or contributing.

[**RELEASING.md**](RELEASING.md) — cheat sheet for cutting a release:
version bump, git tag, Docker build, Docker Hub push (single-arch and
multi-arch with buildx), and rollback procedure.

## For Special K users specifically

The relevant sections across the docs:

- **Workflow** → [USER_GUIDE.md → Special K Import](USER_GUIDE.md#special-k-import-hdr--sdr)
- **Tone-map settings** → [USER_GUIDE.md → HDR & Tone Mapping](USER_GUIDE.md#hdr--tone-mapping)
  and [CONFIGURATION.md → Tone Mapping in Practice](CONFIGURATION.md#tone-mapping-in-practice)
- **API contract** → [API_REFERENCE.md → Special K Import](API_REFERENCE.md#special-k-import)
- **Internals** → [ARCHITECTURE.md → Special K import](ARCHITECTURE.md#special-k-import)
  and [HDR specifics](ARCHITECTURE.md#hdr-specifics)

## Other resources

- [Main README](../README.md) — installation, quick start, Docker / Unraid
  setup
- [`cli/README.md`](../cli/README.md) — GameVault Sync desktop CLI
