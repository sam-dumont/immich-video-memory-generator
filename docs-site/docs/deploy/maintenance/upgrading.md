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

## Upgrading Immich from v2 to v3

Immich Memories supports Immich v2 and v3. Keep the default automatic runtime policy during the
server upgrade:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

You do not need to switch this setting for each run. On the next client start, `auto` detects the
server major and uses its API contract. Explicit `v2` and `v3` are manual troubleshooting escape
hatches for unusual proxies or deployments that prevent correct detection; they force the selected
contract. They are the escape hatch if detection is wrong, not an upgrade ritual.

The client handles the three v3 wire changes that affect generation:

- **Duration:** v2 duration strings and v3 integer milliseconds are normalized to seconds.
- **Upload:** v2 keeps the device identity fields; v3 sends `filename` and omits the removed
  `deviceAssetId` and `deviceId` fields. The schema is selected before bytes are uploaded.
- **Search dates:** date bounds include a UTC offset, which v3 requires.

After upgrading Immich, run:

```bash
immich-memories config test
```

This is a read-only authentication and compatibility check. It does not search assets, generate
a video, create an album, or upload anything. A successful result includes the resolved `v2` or
`v3` contract.

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
