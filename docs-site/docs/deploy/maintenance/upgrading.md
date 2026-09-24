---
sidebar_label: "Upgrading"
---

# Upgrading

## Docker

```bash
docker compose pull
docker compose up -d
```

Everything is in the image, so that is the whole upgrade.

## uv

```bash
uv tool upgrade immich-memories
```

## pip

```bash
pip install --upgrade immich-memories
```

## Before upgrading

Read the [GitHub release notes](https://github.com/sam-dumont/immich-video-memory-generator/releases)
first; the repo's `CHANGELOG.md` is a stub that points there. What bites: config fields renamed or
removed, changed defaults that move your output, and new system requirements such as an FFmpeg
version.

## Upgrading Immich from v2 to v3

Both majors work. [Immich API compatibility](../configuration/config-file.md#immich-api-compatibility)
says what is actually tested. Leave this alone through the server upgrade:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

On the next client start, `auto` detects the server major and uses its API contract. Explicit `v2`
and `v3` are manual troubleshooting escape hatches for unusual proxies or deployments that prevent
correct detection; they force the selected contract. They are the escape hatch when detection is
wrong, not an upgrade step.

The client handles the three v3 wire changes that affect generation:

- **Duration:** v2 duration strings and v3 integer milliseconds are normalized to seconds.
- **Upload:** v2 keeps the device identity fields; v3 sends `filename` and omits the removed
  `deviceAssetId` and `deviceId` fields. The schema is selected before bytes are uploaded. v3
  assets no longer report a device either, so a re-render recognises its earlier upload by the
  `immich-memories/generated` tag on both versions. A v3 render uploaded before the tag existed
  is left in place.
- **Search dates:** date bounds include a UTC offset, which v3 requires.

After upgrading Immich:

```bash
immich-memories config test
```

This is a read-only authentication and compatibility check. It does not search assets, generate a
video, create an album, or upload anything. It prints the `v2` or `v3` contract it resolved.

## Config compatibility

There is no automatic config migration. Unknown keys **inside** a known section are silently
ignored, so a renamed field simply stops doing anything; unknown *top-level* keys and invalid
values fail at startup. Renames are in the release notes: check them when a setting seems to have
stopped taking effect.

The removed clip scorer's keys are named rather than merely ignored: `content_analysis`,
`audio_content`, `transcription`, `analysis.max_refinement_passes`,
`analysis.scene_threshold` and the other pacing and detection dials, `photos.max_ratio`,
`photos.read_moments`, `photos.moment_gap_seconds`, `photos.moment_hash_threshold` and
`hardware.gpu_analysis`. A file that still names one starts normally and logs a warning listing
every one it found, with what each used to do. Delete them to silence it; leaving them changes
nothing, because the code that read them is gone. The `audio-ml`, `speech` and `transcribe` extras
went with it.

Speech-aware cuts are available again in the story-first editor. The existing
`advanced.speech` settings apply to selected videos and Live Photo stitches. The local
detector ships with the `editorial` and `editorial-cuda` extras; it needs no model server.

## Data compatibility

`cache.db` and `annotations.sqlite` migrate forward when they are opened, so upgrading never loses
the run history or the editor's banks.

The video cache is safe to delete at any time (`~/.immich-memories/cache/video-cache`, or the UI's
Cache page); the only cost is downloading the clips again. Generated MP4s stand alone and depend on
no version of anything.

## Rollback

**Docker:** edit the `image:` line in your compose file to a specific tag (all
tags: [GitHub releases](https://github.com/sam-dumont/immich-video-memory-generator/releases)),
then pull and recreate:

```yaml
image: ghcr.io/sam-dumont/immich-video-memory-generator:X.Y.Z   # image tags have no `v` prefix
```

```bash
docker compose pull
docker compose up -d
```

**uv/pip:** keep the extras, or the install comes back without the ONNX runtime:

```bash
uv tool install --force "immich-memories[editorial]==X.Y.Z"
# or
pip install "immich-memories[editorial]==X.Y.Z"
```

Config and caches are kept across version changes, and both databases migrate forward only: a
schema an older build does not know about is not migrated back. So a rollback across a schema
change reads the new file with the old code, which is not a case anyone has tested. Copy
`~/.immich-memories` before you upgrade if you expect to go back. Config field names are the other
thing to check: a key renamed in the meantime is the version you are rolling back to not knowing
the new one.
