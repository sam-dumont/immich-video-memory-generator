---
sidebar_position: 1
title: Config File
---

# Config File

`~/.immich-memories/config.yaml`, written the first time you save the connection settings (from
Advanced on the Memory page, or `immich-memories config`), with permissions `600` because it holds
API keys. A complete annotated example is in the repository at
[`examples/config.example.yaml`](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/examples/config.example.yaml),
and every key with its default is in the [config reference](../../reference/config-reference.md).

## Quick start config

```yaml
immich:
  url: "https://photos.example.com"
  api_key: "${IMMICH_API_KEY}"
  api_version: auto  # auto | v2 | v3

output:
  directory: "~/Videos/Memories"
  resolution: "1080p"            # 720p, 1080p, 4k
  codec: h265                     # h264 is the default; h265 preserves HDR
  hdr_mode: auto                  # keep HLG/PQ when present, otherwise SDR

defaults:
  scale_mode: "blur"             # blur background, or fit for black bars
  transition: "smart"            # cut, crossfade, smart, none

# The reader (OpenAI-compatible, must take images). Leave it out for the rules reader.
llm:
  provider: "openai-compatible"
  base_url: "http://localhost:8000/v1"
  model: "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
```

Everything else has a default. With `codec: h265` and `hdr_mode: auto`, HLG or PQ material gives a
10-bit HDR video and SDR clips, photos and titles are converted to the same transfer; H.264 is
always SDR and tone-maps HDR sources.

## Tiers

Everyday sections stay at the top level (`immich`, `defaults`, `output`, `audio`, `title_screens`,
`title_llm`, `cache`, `upload`, `trips`, `photos`, `scheduler`). Tuning sections go under
`advanced:` (`analysis`, `speech`, `hardware`, `llm`, `musicgen`, `ace_step`, `server`, `auth`, `automation`,
`notifications`, `triage`, `editorial`, `inference`). The app writes them that way; on read both
placements work and merge setting by setting, and the top-level value wins a tie.

Unknown keys inside a section are ignored, and the keys of the removed per-clip scorer
(`content_analysis`, `audio_content`, `transcription`, `description_llm`,
`analysis.max_refinement_passes`, `photos.max_ratio` and their family) are dropped by name and
logged, so an old file does not keep loading while its settings silently do nothing. Unknown
top-level keys and invalid values (`codec: av1`) fail with a validation error.

## Paths in the config are host paths

Everything else in this file travels. These eleven keys do not: they name directories and files on
the machine that wrote them, so a config copied to a second host still points at the first one. The
way it shows up is a worker dying hours into a run, so `immich-memories preflight` checks them up
front and prints one `Config paths` row naming every path that is not here. It is a WARNING, not an
error: an unmounted music share should not stop a cut.

| Key | What it points at |
|---|---|
| `output.directory` | where finished videos are written |
| `cache.directory` | previews, thumbnails, downloaded clips |
| `cache.database` | run history and automation state |
| `advanced.editorial.annotation_database` | every banked fact and reading |
| `advanced.triage.encoder` | the pinned DINOv2 ONNX export |
| `advanced.triage.bundle` | a head bundle of your own |
| `advanced.editorial.preparation.head_bundle` | the same, for the six context heads |
| `advanced.editorial.preparation.marqo_onnx` | the pinned sensitive-content export |
| `advanced.editorial.preparation.detector_cache_dir` | the Hugging Face cache the detectors read |
| `advanced.editorial.preparation.detector_python` | an interpreter for the detector worker |
| `audio.local_music_dir` | your own music library, read by `immich-memories music` |

Blank is a real value for the last four, and the portable one: it means work it out here. A Mac venv
path carried into a NAS container is how `detector_python` produces `detectors: FileNotFoundError`
and no video at all.

Containers already have most of this right: the image pins `output.directory` to `/app/output`, and
the [Kubernetes manifests](../installation/kubernetes.md) pin the three model paths onto the
`/models` claim.

## Footage the camera roll did not shoot

Doorbells, screen recorders and messaging apps upload into the same timeline as your phone.
Files matching these patterns never reach selection:

```yaml
advanced:
  analysis:
    exclude_filename_patterns:
      - "RingVideo_*"
      - "RPReplay_Final*"
      - "Screen Recording *"
      - "Screenshot*"
      - "img-*-wa[0-9][0-9][0-9][0-9]*"
      - "vid-*-wa[0-9][0-9][0-9][0-9]*"
```

Case-insensitive globs on the original filename. Setting the key replaces the list, so include the
defaults you want to keep.

A still whose EXIF names no camera at all is also dropped (`exclude_stills_without_camera_exif:
true`, the default): on iOS a photo saved from a messaging app keeps its `IMG_` name and loses only
the camera make. Measured across four months of one library, 1,498 of 1,541 make-less stills had
arrived through a messaging app against 9 camera originals. Turn it off if your library is mostly
exported or edited originals, which lose their make the same way. Videos are exempt.

## Immich API compatibility

Immich v2 and v3 both work. `auto` is the default runtime policy: the app detects the server
major and selects the matching API contract. You do not choose a version for each run. Explicit
`v2` and `v3` values are manual troubleshooting escape hatches for proxies or unusual deployments
that break version detection. An override forces that contract; it is not a normal upgrade step.
The compatibility layer converts v2 duration strings and v3 millisecond durations to seconds, uses
version-specific upload fields, and sends timezone-aware search dates. An unknown major stops the
run with `UnsupportedImmichVersion` rather than sending requests of the wrong shape.

```bash
immich-memories config test
```

Read-only: it reports the connection and the resolved contract and does nothing else.

## Environment variable substitution

These fields expand `${VAR_NAME}` at load time:

| Section | Fields |
|---|---|
| `immich` | `url`, `api_key` |
| `llm` / `title_llm` | `api_key` |
| `musicgen` | `base_url`, `api_key` |
| `ace_step` | `api_url`, `api_key` |
| `auth` | `password`, `client_secret`, `issuer_url`, `client_id` |
| `editorial` | `annotation_database` |
| `editorial.preparation` | `head_bundle`, `detector_python`, `detector_cache_dir`, `marqo_onnx`, `caption_api_key` |

Only the braced form expands. A bare `$VAR` is left as written, because a `$` in a password is
ordinary, though a warning says so at load time if it matches a variable you have set. For any
other field, use `IMMICH_MEMORIES_<SECTION>__<FIELD>`
([Environment variables](environment-variables.md)).

## Trips and upload-back

```yaml
trips:
  homebase_latitude: 50.85
  homebase_longitude: 4.35
  min_distance_km: 50

upload:
  enabled: true
  album_name: "2024 Memories"
```

## Reader concurrency

`advanced.llm.reader_concurrency` is unset by default, and the number is then read from
`llm.base_url`: 1 for a loopback or private address or a bare service name, 4 for a public host. A
model on your own machine is one process in front of one accelerator, so four requests there queue
instead of overlapping; a hosted endpoint is a fleet. Set it yourself (1 to 16) for a local server
that does take concurrent requests, or a provider that wants a lower rate. What overlaps and what
cannot is [drawn on the pipeline overview](../../create/pipeline.md#what-overlaps-and-what-cannot).
