---
sidebar_label: "Upgrading"
---

# Upgrading

Reader: power user.

Read the [release notes](https://github.com/sam-dumont/immich-video-memory-generator/releases)
first (the repo's `CHANGELOG.md` points there). What bites: a config key renamed or removed, a
default that changes your output, a new system requirement such as an FFmpeg version.

## Docker

```bash
docker compose pull
docker compose up -d
docker compose exec immich-memories immich-memories models fetch
```

The code is in the image; the model files live on the config volume. `models fetch` checks each
file against the pins of the new release and downloads only what moved, so on most upgrades it
prints `already present at` for each file. Skip it after a release that moved a pin, and the next
run refuses to start, before any Immich call, because the file on the volume is not the pinned one.

## uv / pip

Keep your extras, or the upgrade comes back without the ONNX runtime:

```bash
uv tool upgrade immich-memories
# or
pip install --upgrade "immich-memories[editorial]"
immich-memories models fetch
```

## Upgrading Immich from v2 to v3

Both majors work ([Immich API compatibility](../config-file.md#immich-api-compatibility)). Leave
this alone through the server upgrade:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

On the next start, `auto` detects the server major and uses its API contract. Explicit `v2`
and `v3` are manual troubleshooting escape hatches for unusual proxies or deployments that prevent
correct detection; they force the selected contract. They are not an upgrade step.

The client handles the three v3 wire changes that affect generation:

- **Duration:** v2 duration strings and v3 integer milliseconds are normalized to seconds.
- **Upload:** v2 keeps the device identity fields; v3 sends `filename` and omits the removed
  `deviceAssetId` and `deviceId` fields. v3 assets report no device, so a re-render recognises its
  earlier upload by the `immich-memories/generated` tag on both versions. The tag goes on once
  Immich has finished reading the file, because Immich's own metadata read rewrites an asset's tags.
- **Search dates:** date bounds include a UTC offset, which v3 requires.

After upgrading Immich:

```bash
immich-memories config test
```

This is a read-only authentication and compatibility check. It does not search assets, generate a
video, create an album, or upload anything. It prints the `v2` or `v3` contract it resolved.

## Config compatibility

There is no automatic config migration. An unknown key inside a known section is ignored, so a
renamed field stops doing anything; an unknown top-level key or an invalid value fails at
startup. When a setting seems to have stopped working, look for its rename in the release notes.

Keys of the retired per-clip scorer (`content_analysis`, `audio_content`, `transcription`,
`analysis.max_refinement_passes`, `analysis.scene_threshold` and the other pacing dials,
`photos.max_ratio`, `photos.read_moments`, `hardware.gpu_analysis` and their family) load with one
warning listing each one. Delete them to silence it; nothing reads them.

## Data compatibility

`cache.db` and `annotations.sqlite` migrate forward when opened, so an upgrade never loses run
history or banked facts. The video cache is safe to delete at any time; it costs a re-download.
Finished MP4s depend on nothing.

## Rollback

**Docker:** pin the `image:` line to a release tag (no `v` prefix), then pull and recreate:

```yaml
image: ghcr.io/sam-dumont/immich-video-memory-generator:X.Y.Z
```

```bash
docker compose pull
docker compose up -d
```

**uv / pip:**

```bash
uv tool install --force "immich-memories[editorial]==X.Y.Z"
# or
pip install "immich-memories[editorial]==X.Y.Z"
```

Both databases migrate forward only, so rolling back across a schema change means old code reading
a newer file, which nobody tests. Copy `~/.immich-memories` before an upgrade you might undo.
