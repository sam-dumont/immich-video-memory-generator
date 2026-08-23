---
sidebar_position: 1
title: Config File
---

# Config File

Location: `~/.immich-memories/config.yaml`

The file is written the first time you save the connection settings — from Step 1 of the web UI or
with `immich-memories config`. Permissions are set to `600` (owner read/write only) since it
contains API keys. Sections are grouped in two tiers: everyday options at the top level, and the
rest under `advanced:` (see [Tiers](#tiers) below).

## Quick start config

Most users only need these options:

```yaml
# ── Required ──────────────────────────────────────────────
immich:
  url: "https://photos.example.com"
  api_key: "${IMMICH_API_KEY}"
  api_version: auto  # auto | v2 | v3

# ── Output ────────────────────────────────────────────────
output:
  directory: "~/Videos/Memories"
  resolution: "1080p"            # 720p, 1080p, 4k
  codec: h265                     # h264 is the default; h265 preserves HDR
  hdr_mode: auto                  # Preserve HLG/PQ when present; otherwise output SDR

defaults:
  scale_mode: "blur"             # blur background, or fit for black bars
  transition: "smart"            # cut, crossfade, smart, none

# ── AI analysis (any OpenAI-compatible vision model) ──────
# Both sections are needed: `llm` says where the model is,
# `content_analysis.enabled` turns scoring with it on.
llm:
  provider: "openai-compatible"
  base_url: "http://localhost:8080/v1"
  model: "qwen2.5-vl"

content_analysis:
  enabled: true

# ── AI background music (optional) ───────────────────────
# Music is on when one generator is enabled. Per run you can
# still pass `--music PATH` / `--no-music` (CLI) or pick
# None / Upload file / AI Generated in the UI.
ace_step:
  enabled: false
  api_url: "http://localhost:8000"
```

That's it. Everything else has sane defaults.

With `codec: h265` and `hdr_mode: auto`, detected HLG or PQ material produces a 10-bit HDR video;
SDR clips, photos, and titles are converted to the same HDR transfer during assembly. H.264 is
always SDR. If you select `codec: h264`, detected HDR is tone-mapped to SDR even when
`hdr_mode: auto` is set. Use H.264 when broad playback compatibility matters more than HDR.

The target duration you pick per run (UI slider or `--duration`) applies to the complete result,
including titles and the time removed by overlapping fades. When eligible material exists, the
optimizer backfills unused clips before accepting a short result. The encoded duration may differ by
less than one transition because cuts land on video frame boundaries. There is no config default for
it: the memory type preset supplies one.

## Tiers

`llm`, `content_analysis`, `ace_step` and the other tuning sections are Tier 2. When the app writes
the file it groups them under `advanced:`; when reading, both placements work, and if the same key
appears in both the top-level value wins.

```yaml
advanced:
  llm:
    base_url: "http://localhost:8080/v1"
  content_analysis:
    enabled: true
```

Tier 2 sections: `analysis`, `hardware`, `llm`, `musicgen`, `ace_step`, `content_analysis`,
`audio_content`, `speech`, `transcription`, `server`, `auth`, `automation`, `notifications`.
Everything else (`immich`, `defaults`, `output`, `audio`, `title_screens`, `cache`, `upload`,
`trips`, `photos`, `scheduler`) stays at the top level.

Unknown keys inside a section are silently ignored — a typo does not fail the load, it just does
nothing. Unknown top-level keys and invalid values (`codec: av1`, `llm.provider: openai`) do fail
with a validation error at startup.

## Footage the camera roll did not shoot

Doorbells, security cameras, screen recorders and messaging apps all upload into the same
timeline as your phone. None of it was shot to be remembered, and some of it scores well —
a doorbell is a perfectly stable camera pointed at a place people walk through.

Source files matching these patterns never reach selection:

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

Case-insensitive globs against the original filename. Setting the key replaces the list
rather than adding to it, so include the defaults you want to keep. An empty list turns the
filter off.

A name does not always give it away. On iOS a photo saved from a messaging app keeps an
`IMG_` name and loses only its EXIF camera. So a still whose EXIF names no camera at all is
also dropped:

```yaml
advanced:
  analysis:
    exclude_stills_without_camera_exif: true   # the default
```

Measured across four months of a real library: of 1541 stills with no EXIF make, 1498
arrived through a messaging app and 34 were downloads, against 9 camera originals that had
lost their make somewhere. Turn it off if your library is mostly exported or edited
originals, which lose their make the same way. Videos are exempt either way — 25 of 224
make-less videos in that library were genuine phone clips, so the rule cannot judge them.

Both rules run before anything is analysed, which is also what keeps this material out of
the analysis budget, and `discover-days` applies the same rules before it counts a day's
photographs — a day should not clear the bar on pictures nobody in the library took.

This runs before analysis, so an excluded file costs nothing to skip, and it works with no
LLM configured at all. With one configured, the holistic review is a second line of defence:
it drops footage nobody chose to shoot whatever the picture quality, which covers the
cameras whose filenames give nothing away.

## Immich API compatibility

Immich Memories supports **Immich v2 and v3**. `auto` is the default runtime policy: the app
detects the server major and selects the matching API contract. You do not choose a version for
each run.

Explicit `v2` and `v3` values are manual troubleshooting escape hatches for proxies or unusual
deployments that break version detection. An override forces that contract; it is not a normal
upgrade step.

The compatibility layer converts v2 duration strings and v3 millisecond durations to seconds,
uses version-specific upload fields, and sends timezone-aware search dates accepted by v3. Check
the configured connection and resolved API contract without generating or uploading anything:

```bash
immich-memories config test
```

This check is read-only.

`auto` is a runtime detection policy, not a switch you set before each generation. Use `v2` or
`v3` only to diagnose a deployment that prevents detection, then return to `auto`.

## Clip pacing

Control how clips are cut with a single option:

```yaml
analysis:
  clip_style: "balanced"    # fast-cuts | balanced | long-cuts
```

| Style | Feel | Clip duration | Extraction ratio |
|-------|------|---------------|-----------------|
| *(unset)* | Default: natural pacing, conservative extraction | 5-10s | 15% |
| `fast-cuts` | Energetic, music-video style | 3-6s | 30% |
| `balanced` | Same durations as the default, pulls more footage per source | 5-10s | 40% |
| `long-cuts` | Cinematic, slow | 8-15s | 50% |

A preset only fills in the five duration parameters you have not set yourself; explicit values win.
See the [Config Reference](../../reference/config-reference.md#video-analysis) for the individual knobs.

## Environment variable substitution

A handful of secret-bearing fields expand `${VAR_NAME}` at load time:

| Section | Fields |
|---------|--------|
| `immich` | `url`, `api_key` |
| `llm` / `title_llm` | `api_key` |
| `musicgen` | `base_url`, `api_key` |
| `ace_step` | `api_url` (not `api_key`) |
| `auth` | `password`, `client_secret`, `issuer_url`, `client_id` |

Only the braced form expands. A bare `$VAR_NAME` is left exactly as written,
because these fields hold passwords and API keys and a `$` in a secret is
ordinary — `S3cret$USER!` would otherwise pick up your login name and the only
symptom would be a rejected password. If a value contains a bare `$NAME` that
matches a variable you have set, a warning says so at load time.

```yaml
immich:
  api_key: ${IMMICH_API_KEY}

llm:
  api_key: ${OPENAI_API_KEY}
```

Every other string is stored literally — `output.directory: ${HOME}/x` is not expanded. To set any
other field from the environment, use the `IMMICH_MEMORIES_<SECTION>__<FIELD>` form described in
[Environment Variables](environment-variables.md).

## Trip memories

For trip detection, set your home coordinates:

```yaml
trips:
  homebase_latitude: 50.85
  homebase_longitude: 4.35
  min_distance_km: 50
```

## Upload back to Immich

Generated videos can be auto-uploaded as Immich albums:

```yaml
upload:
  enabled: true
  album_name: "2024 Memories"
```

## All options

For the full list of options (analysis tuning, hardware acceleration, speech and transcription, title screens, scheduler, notifications, etc.), see the [Config Reference](../../reference/config-reference.md).
