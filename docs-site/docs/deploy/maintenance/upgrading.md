---
sidebar_label: "Upgrading"
---

# Upgrading

## Docker

```bash
docker compose pull
docker compose up -d
```

That's it for the app's own dependencies. The editor's model files are not in the image: if the
release notes move the pinned encoder digest or a detector revision, re-run
`immich-memories models fetch` afterwards.

## uv (recommended for native install)

```bash
uv tool upgrade immich-memories
```

## pip

```bash
pip install --upgrade immich-memories
```

## Before upgrading

Read the [GitHub release notes](https://github.com/sam-dumont/immich-video-memory-generator/releases) before upgrading (the `CHANGELOG.md` in the repo is a stub that points there). Look for:

- **Breaking changes**: config fields that were renamed or removed
- **New defaults**: behavior changes that might affect your output
- **New dependencies**: system-level requirements (FFmpeg version, etc.)

## Upgrading Immich from v2 to v3

Immich Memories supports Immich v2 and v3: see
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

The one family of keys that is refused rather than ignored is the removed clip scorer's: the whole `content_analysis`, `audio_content`, `speech` and `transcription` sections, `description_llm`, twenty `analysis.*` pacing and detection dials (`max_refinement_passes`, `scene_threshold`, `duplicate_hash_threshold`, `subject_policy_enabled` and the rest), `photos.max_ratio`, `photos.read_moments`, `photos.moment_gap_seconds`, `photos.moment_hash_threshold` and `hardware.gpu_analysis`. Thirty keys in all. A file that still names one stops the app at startup with a message listing them; delete them and start again. The `audio-ml`, `speech` and `transcribe` extras went with the code.


## Data compatibility

**Analysis database** (`cache.db`, `cache/annotations.sqlite`): forward-compatible. `cache.db` has a versioned migrator; `annotations.sqlite` creates missing tables and adds columns additively. Both run when the store is first opened, not at startup. Upgrading never loses the run history or the editor's banks.

**Video cache** (downloaded clips): can be cleared safely at any time. If a new version changes the download format or caching structure, the old cache files are still valid but you can clear them without loss by deleting `~/.immich-memories/cache/video-cache` (or via the UI Cache page).

**Generated videos**: output MP4 files are standalone. They don't depend on any version of Immich Memories.

## Rollback

If something goes wrong:

**Docker:** edit the `image:` line in your compose file to a specific tag (all
tags: [GitHub releases](https://github.com/sam-dumont/immich-video-memory-generator/releases)),
then pull and recreate:

```yaml
image: ghcr.io/sam-dumont/immich-video-memory-generator:0.59.2   # image tags have no `v` prefix
```

```bash
docker compose pull
docker compose up -d
```

**uv/pip:**
```bash
uv tool install immich-memories==0.59.2
# or
pip install immich-memories==0.59.2
```

Your caches and config are preserved across version changes. The only thing that might need attention is config field names if the version you're rolling back to used different names.
