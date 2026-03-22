---
sidebar_label: "Upgrading"
---

# Upgrading

## Docker

```bash
docker compose pull
docker compose up -d
```

That's it. The container image includes all dependencies.

## uv (recommended for native install)

```bash
uv tool upgrade immich-memories
```

## pip

```bash
pip install --upgrade immich-memories
```

## Before upgrading

Check the [CHANGELOG](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/CHANGELOG.md) before upgrading. Look for:

- **Breaking changes**: config fields that were renamed or removed
- **New defaults**: behavior changes that might affect your output
- **New dependencies**: system-level requirements (FFmpeg version, etc.)

## Config compatibility

There is no automatic config migration. If a release renames or removes a config field, you'll see a validation error on startup. The fix is always documented in the release notes: update your `config.yaml` to use the new field name.

In practice, most config fields have been stable since v0.1. Breaking config changes are rare and always called out in the CHANGELOG.

## Data compatibility

**Analysis cache** (`cache.db`): forward-compatible. The SQLite database has schema migrations that run automatically on startup. Upgrading never loses your analysis scores.

**Video cache** (downloaded clips): can be cleared safely at any time. If a new version changes the download format or caching structure, the old cache files are still valid but you can clear them without loss: `immich-memories cache clear-videos`.

**Generated videos**: output MP4 files are standalone. They don't depend on any version of Immich Memories.

## Rollback

If something goes wrong:

**Docker:**
```bash
# Pin to a specific version
docker compose pull ghcr.io/sam-dumont/immich-video-memory-generator:v0.1.0
docker compose up -d
```

**uv/pip:**
```bash
uv tool install immich-memories==0.1.0
# or
pip install immich-memories==0.1.0
```

Your analysis cache and config are preserved across version changes. The only thing that might need attention is config field names if the version you're rolling back to used different names.
