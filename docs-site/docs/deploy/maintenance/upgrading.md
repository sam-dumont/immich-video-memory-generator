---
sidebar_label: "Upgrading"
---

# Upgrading

## Docker

```bash
docker compose pull
docker compose up -d
```

This replaces the app image. Separately deployed reader, caption and inference services have
their own versions and model files.

## uv (recommended for native install)

```bash
uv tool upgrade immich-memories
```

## pip

```bash
pip install --upgrade immich-memories
```

## Before upgrading

Stop generation and scheduled writers. Back up the whole `~/.immich-memories` directory (Docker:
the config volume; Kubernetes: the state PVC), plus generated outputs and any separately placed
annotation database. A stopped copy includes both SQLite databases and their sidecars.
`cache backup` alone only covers `cache.db`; the editor's facts are in `cache/annotations.sqlite`.
Keep the old image tag or package version alongside that backup.


Read the [GitHub release notes](https://github.com/sam-dumont/immich-video-memory-generator/releases) before upgrading (the `CHANGELOG.md` in the repo is a stub that points there). Look for:

- **Breaking changes**: config fields that were renamed or removed
- **New defaults**: behavior changes that might affect your output
- **New dependencies**: system-level requirements (FFmpeg version, etc.)

## Upgrading Immich from v2 to v3

Immich Memories supports Immich v2 and v3, see
[Immich API compatibility](../configuration/config-file.md#immich-api-compatibility) for what that
covers and what is actually tested. Keep the default automatic runtime policy during the server
upgrade:

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

There is no automatic config migration. Unknown keys **inside** a known section are silently ignored, so a renamed field simply stops doing anything; unknown *top-level* keys and invalid values fail at startup. Renames are documented in the release notes: check them when a setting seems to have stopped taking effect.

Retired sections (`content_analysis`, `audio_content`, `speech`, `transcription`) and their
associated analysis/photo dials log a warning and are ignored. Remove the named keys. The
`audio-ml`, `speech` and `transcribe` installation extras were removed with those features.

The superseded `scheduler`, `analyze` and `export-project` commands are removed, along with
scorer-only `cache stats`, `cache export` and `cache import`. Use `auto` for automation and
`runs storage` for current storage usage. The old scheduler's per-type schedules have no exact
`auto` equivalent: `auto` chooses eligible memories.

Remove `scheduler:`, `cache.max_age_days`, `triage.enabled`, `triage.bundle` and `title_screens.show_decorative_lines`, plus the removed
analysis keys such as `use_scene_detection` and `use_unified_analysis`. They no longer control
anything. The active head bundle setting is `editorial.preparation.head_bundle`.

Kubernetes and Terraform now keep scratch and library caches on PVC storage, with a 50Gi state
default. Terraform also creates a model PVC; remove the old `tmp_size` argument. Existing claims
need sufficient space and a StorageClass that supports expansion. Inspect the plan and resulting
PVC capacity before starting another run; changing a default is not proof that an existing volume
expanded.

## Data compatibility

**Databases:** `cache.db` migrates when opened; the annotation store creates missing tables and
adds columns when opened. Keep a backup: these are upgrade paths, not a guarantee that an older
version can read a newer database.

**Source caches:** downloaded videos, thumbnails and previews can be rebuilt from Immich. Clear
them only while the app is idle. Do not delete the whole cache directory: it also contains the
annotation banks and editorial attempts.

**Generated videos**: output MP4 files are standalone. They don't depend on any version of Immich Memories.

## Rollback

If something goes wrong:

**Docker:** edit the `image:` line in your compose file to a specific tag (all
tags: [GitHub releases](https://github.com/sam-dumont/immich-video-memory-generator/releases)),
then pull and recreate:

```yaml
image: ghcr.io/sam-dumont/immich-video-memory-generator:X.Y.Z   # the version you backed up, no `v` prefix
```

```bash
docker compose pull
docker compose up -d
```

**uv/pip:**
```bash
uv tool install --force "immich-memories[editorial]==X.Y.Z"
# or
pip install "immich-memories[editorial]==X.Y.Z"
```

Replace `X.Y.Z` with your saved version and keep the extras your native install used. Restore the
matching state backup with writers stopped. A package rollback alone does not undo schema or
model-format changes. Keep newer outputs separately if you want to retain them.
