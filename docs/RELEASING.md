# Releasing GameVault

Cheat sheet for cutting a new release: bump version, tag git, build the
Docker image, push to Docker Hub.

Docker Hub repo: [adiuv0/gamevault](https://hub.docker.com/r/adiuv0/gamevault)
GitHub repo: [adiuv0/gamevault](https://github.com/adiuv0/gamevault)

## TL;DR (one-shot)

For a routine release where you just want to push a new version of the
existing image to Docker Hub:

```bash
# From the repo root, after committing all changes:
VERSION=0.2.0

docker build -t adiuv0/gamevault:$VERSION -t adiuv0/gamevault:latest .
docker login                                       # one-time per shell
docker push adiuv0/gamevault:$VERSION
docker push adiuv0/gamevault:latest
```

Don't forget to also push the matching git tag (see [Tagging git](#tagging-git)
below) so the Docker tag and a git ref line up.

---

## Pre-release checklist

Before building the image, verify the repo is in a release-able state:

- [ ] All changes committed and pushed to `main`
- [ ] `python -m pytest tests/ -v` passes
- [ ] Frontend builds cleanly: `cd frontend && npm run build`
- [ ] `pyproject.toml` `version` bumped if this is a new version
- [ ] `frontend/src/components/layout/Sidebar.tsx` footer version bumped
  (the `GameVault v0.1.0` string)
- [ ] `backend/main.py` version in the `FastAPI(..., version="0.1.0")`
  call bumped
- [ ] README.md / docs updated for any user-visible changes
- [ ] CHANGELOG (if you start one) updated

A simple bash helper for the version bump:

```bash
OLD=0.1.0
NEW=0.2.0

# Update each known location
sed -i "s/version = \"$OLD\"/version = \"$NEW\"/" pyproject.toml
sed -i "s/version=\"$OLD\"/version=\"$NEW\"/" backend/main.py
sed -i "s/GameVault v$OLD/GameVault v$NEW/" frontend/src/components/layout/Sidebar.tsx

git diff   # eyeball it
git commit -am "Bump version to $NEW"
git push origin main
```

---

## Tagging git

Tag the commit so you can find it later:

```bash
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

Use the same version string for the git tag and the Docker tag so they
correspond.

---

## Building the Docker image

The repo has a multi-stage Dockerfile (`node:20-alpine` builder →
`python:3.12-slim` runtime). A simple `docker build` does the right
thing.

### Single-arch (amd64 — what Unraid uses)

```bash
VERSION=0.2.0

docker build \
    -t adiuv0/gamevault:$VERSION \
    -t adiuv0/gamevault:latest \
    .
```

The `-t` flag can be repeated to apply multiple tags at once. The build
takes a few minutes — most of it is the `imagecodecs` wheel install
(needed for HDR JXR decoding).

### Multi-arch (amd64 + arm64) with buildx

If you want the image to work on ARM hardware (Raspberry Pi, Apple
Silicon servers, ARM cloud nodes), use buildx:

```bash
VERSION=0.2.0

# One-time setup of a builder that supports multi-arch
docker buildx create --name gamevault-builder --use
docker buildx inspect --bootstrap

# Build + push in one step (buildx requires --push for multi-arch)
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -t adiuv0/gamevault:$VERSION \
    -t adiuv0/gamevault:latest \
    --push \
    .
```

Note that buildx's `--push` flag *replaces* the normal
`docker build` + `docker push` flow — there's no local intermediate
image to test. Run the single-arch build first to smoke-test, then run
buildx for the real release.

---

## Logging into Docker Hub

```bash
docker login
```

This prompts for your Docker Hub username (`adiuv0`) and either your
password or — preferred — a Personal Access Token from
[hub.docker.com/settings/security](https://hub.docker.com/settings/security).

Credentials are cached in your OS keychain so you only need to do this
once per machine. To log out: `docker logout`.

---

## Pushing to Docker Hub

If you used `docker build` (not buildx with `--push`), you have a local
image that needs pushing:

```bash
VERSION=0.2.0
docker push adiuv0/gamevault:$VERSION
docker push adiuv0/gamevault:latest
```

Each tag is a separate push command. Skipping `:latest` means folks
running `docker pull adiuv0/gamevault` (no tag) will keep getting the
old version — usually you want both.

After the push, verify on Docker Hub:

- [hub.docker.com/r/adiuv0/gamevault/tags](https://hub.docker.com/r/adiuv0/gamevault/tags)

The new tag should appear within seconds. The "latest" tag's digest
should match your version tag's digest if both pushed correctly.

---

## Updating Unraid users' deployments

Once `:latest` is pushed, Unraid users can update via the Docker tab
("Check for Updates" button on the GameVault container) — Unraid
checks digests against `:latest` and offers an in-place upgrade.

Users who pinned a specific version need to bump their tag manually
(edit the container, change `adiuv0/gamevault:0.1.0` to
`adiuv0/gamevault:0.2.0`).

---

## Troubleshooting

### "denied: requested access to the resource is denied"

You're not logged in, or the credentials in your keychain are stale.
Run `docker login` again. Make sure you're pushing to `adiuv0/gamevault`
and not `gamevault` (which would require an org with that name).

### Build is huge / slow

The `imagecodecs` wheel is the culprit (~150 MB on Linux x86_64,
needed for HDR JXR decode). It's a runtime requirement — not removable.
Cached layers should make subsequent builds fast unless `requirements.txt`
changes.

### "Cannot connect to the Docker daemon"

Docker Desktop / dockerd isn't running. Start it.

### Multi-arch build fails on a single-arch host

You need buildx (which uses QEMU under the hood). The `docker buildx
create` step in the [multi-arch section](#multi-arch-amd64--arm64-with-buildx)
is required once per machine.

### Want to test the image locally before pushing

```bash
docker build -t gamevault-test .
docker run --rm -p 8080:8080 \
    -e GAMEVAULT_SECRET_KEY=test-key \
    -e GAMEVAULT_BASE_URL=http://localhost:8080 \
    -v $(pwd)/_test_data:/data \
    gamevault-test
```

Then visit `http://localhost:8080`. Stop with Ctrl+C; remove the
`_test_data` directory when done.

---

## Rolling back a bad release

If you pushed a broken image:

```bash
# Re-tag the previous good version as :latest and push it
docker pull adiuv0/gamevault:0.1.0
docker tag adiuv0/gamevault:0.1.0 adiuv0/gamevault:latest
docker push adiuv0/gamevault:latest
```

This reverts what `:latest` points to without touching the broken
versioned tag (so anyone who pinned to `:0.2.0` is unaffected, and
anyone tracking `:latest` gets the rollback on their next pull).
