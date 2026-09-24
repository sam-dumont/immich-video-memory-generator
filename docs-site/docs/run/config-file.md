---
sidebar_position: 1
title: Config File
---

# Config file

Reader: power user.

`~/.immich-memories/config.yaml`, written the first time you save the connection (Advanced on the
Memory page, or `immich-memories config`), with permissions `600` because it holds API keys. The
annotated example is
[`examples/config.example.yaml`](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/examples/config.example.yaml),
and every key with its default is in the [config reference](../reference/config-reference.md). In
Docker you can skip the file entirely and use [environment variables](./environment-variables.md).

## Quick start config

This is a full plain-NAS setup, every default kept except the two values that make a cut good
(where home is, and where films go).

```yaml
immich:
  url: "https://photos.example.com"
  api_key: "${IMMICH_API_KEY}"
  api_version: auto  # auto | v2 | v3

trips:
  homebase_latitude: 50.85      # without these, no trip is ever a trip
  homebase_longitude: 4.35

output:
  directory: "~/Videos/Memories"
  resolution: "1080p"            # 720p, 1080p, 4k
  codec: h264                     # the default; h265 keeps HDR
  hdr_mode: auto                  # keep HLG/PQ when present, otherwise SDR
```

Everything else has a default. With `codec: h265` and `hdr_mode: auto`, HLG or PQ footage gives a
10-bit HDR film and SDR clips, photos and titles are converted to the same transfer. H.264 is
always SDR and tone-maps HDR sources.

Trip detection needs both home coordinates. Preflight warns when either is missing or left at
`(0, 0)`, and trips stay off until you set them.

### Make it better (optional)

A reader is one block. Leave it out and the app edits on a plain NAS, which is the default and the
tier most installs run; a model makes the cut better. What a model adds and costs is on [the overview](../better/overview.md).

```yaml
llm:
  provider: "openai-compatible"
  base_url: "http://localhost:8000/v1"
  model: "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
```

## Everyday keys and advanced keys

Everyday sections sit at the top level: `immich`, `defaults`, `output`, `audio`, `title_screens`,
`cache`, `upload`, `trips`, `network`, `photos`, `render`, `scheduler`, `title_llm`. Tuning sections
go under `advanced:`: `analysis`, `speech`, `hardware`, `llm`, `musicgen`, `ace_step`, `server`, `auth`, `automation`,
`notifications`, `triage`, `editorial`, `inference`. The app writes them that way. On read both
placements work and merge key by key at every depth, and the top-level value wins a tie, so a
hand-written `editorial: {preparation: {tier: no_captions}}` changes the tier and keeps the rest of
the app-written block.

Unknown keys inside a section are ignored. The keys of the retired per-clip scorer
(`content_analysis`, `audio_content`, `transcription`, `description_llm`,
`analysis.max_refinement_passes`, `photos.max_ratio` and their family) are dropped by name with a
warning, so an old file loads and tells you what it ignored. Unknown top-level keys and invalid
values (`codec: av1`) fail with a validation error.

## Paths in the config are host paths

Everything else in this file travels to another machine. These keys don't: they name paths on the
machine that wrote them. `immich-memories preflight` prints one `Config paths` warning naming every
path that is missing here, so a copied config fails up front instead of hours into a run.

| Key | What it points at |
|---|---|
| `output.directory` | where finished films are written |
| `cache.directory` | previews, thumbnails, downloaded clips |
| `cache.database` | run history and automation state |
| `advanced.editorial.annotation_database` | every banked fact and reading |
| `advanced.triage.encoder` | the pinned DINOv2 ONNX export |
| `advanced.triage.bundle` | a head bundle of your own |
| `advanced.editorial.preparation.head_bundle` | the same, for the eight context heads |
| `advanced.editorial.preparation.marqo_onnx` | the pinned sensitive-content export |
| `advanced.editorial.preparation.detector_cache_dir` | the Hugging Face cache the detectors read |
| `advanced.editorial.preparation.detector_python` | an interpreter for the detector worker |
| `audio.local_music_dir` | your own music, read by `immich-memories music` |

Blank is the default for `head_bundle`, `detector_python` and `detector_cache_dir`, and the portable
value: it means "work it out here". A Mac venv path carried into a NAS container is how
`detector_python` ends in `detectors: FileNotFoundError` and no film. Containers already pin most of these: the image sets
`output.directory` to `/app/output`, and the [Kubernetes manifests](./kubernetes.md) put the model
paths on the `/models` claim.

## Footage the camera roll did not shoot

Doorbells, screen recorders and messaging apps upload into the same timeline as your phone. Files
matching these patterns never reach selection:

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

Case-insensitive globs on the original filename. Setting the key replaces the list, so copy the
defaults you want to keep.

A still whose EXIF names no camera is dropped too (`exclude_stills_without_camera_exif: true`, the
default): on iOS a photo saved from a messaging app keeps its `IMG_` name and loses only the camera
make. Turn it off if your library is mostly exported or edited originals, which lose the make the
same way. Videos are exempt.

## Immich API compatibility

Immich v2 and v3 both work. `auto` is the default runtime policy: the app detects the server
major and selects the matching API contract. You do not choose a version for each run. Explicit
`v2` and `v3` values are manual troubleshooting escape hatches for proxies or unusual deployments
that break version detection. An override forces that contract; it is not a normal upgrade step.
Durations, upload fields and search dates are converted for each version, and an unknown major
stops the run with `UnsupportedImmichVersion` rather than sending requests of the wrong shape.

```bash
immich-memories config test
```

Read-only: it reports the connection and the resolved contract, and does nothing else.

## Environment variable substitution

These fields expand `${VAR_NAME}` at load time:

| Section | Fields |
|---|---|
| `immich` | `url`, `api_key` |
| `llm` / `title_llm` | `api_key` |
| `musicgen` | `base_url`, `api_key` |
| `ace_step` | `api_url`, `api_key` |
| `auth` | `password`, `client_secret`, `issuer_url`, `client_id` |
| `render` | `worker_base_url`, `worker_token` |
| `editorial` | `annotation_database` |
| `editorial.preparation` | `head_bundle`, `detector_python`, `detector_cache_dir`, `marqo_onnx`, `caption_api_key` |

Only the braced form expands. A bare `$VAR` stays as written, because a `$` in a password is
ordinary (a warning says so if it matches a variable you have set). For any other field, use
`IMMICH_MEMORIES_<SECTION>__<FIELD>` ([environment variables](./environment-variables.md)).

## Upload back to Immich

```yaml
upload:
  enabled: true
  album_name: "2024 Memories"
```

Off by default. [What Immich sees](./privacy.md#what-immich-sees) lists every write it makes.

## Outside calls

```yaml
network:
  geocoding: false        # nominatim.openstreetmap.org
  map_tiles: false        # server.arcgisonline.com
```

Both off, so a default run reaches your Immich server, the endpoints named elsewhere in this file,
and nothing else. `geocoding` buys place names in the film's language; `map_tiles` buys the trip
fly-over and the map behind location cards. Fonts are never fetched at run time (see
[fonts](./privacy.md#fonts)). [Privacy](./privacy.md) says exactly what each host receives.

## Reader concurrency

Only matters with a reader. `advanced.llm.reader_concurrency` is unset by default and then read
from `llm.base_url`: 1 for a loopback or private address or a bare service name, 4 for a public
host. A model on your own machine is one process in front of one accelerator, so four requests
queue there instead of overlapping; a hosted endpoint is a fleet. Set it yourself (1 to 16) for a
local server that does take concurrent requests, or a provider that wants a lower rate.
