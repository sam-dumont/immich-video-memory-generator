---
sidebar_position: 1
title: Config File
---

# Config File

Location: `~/.immich-memories/config.yaml`

The config file is created automatically when you first run `immich-memories config`. File permissions are set to `600` (owner read/write only) since it contains API keys.

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
  codec: h265                     # Preserve HDR; use h264 for maximum compatibility
  hdr_mode: auto                  # Preserve HLG/PQ when present; otherwise output SDR

defaults:
  target_duration_seconds: 600   # 10-3600 seconds
  output_orientation: "auto"     # auto, landscape, portrait

# ── AI analysis (any OpenAI-compatible vision model) ──────
llm:
  provider: "openai-compatible"
  base_url: "http://localhost:8080/v1"
  model: "qwen2.5-vl"

# ── Background music (optional) ──────────────────────────
audio:
  auto_music: false
  music_source: "ace_step"       # ace_step, musicgen, local, or none

ace_step:
  enabled: false
  api_url: "http://localhost:8000"

# ── Clip scoring priorities ────────────────────
scoring_priority:
  people: high       # low, medium, high: prioritize clips with faces
  quality: medium    # low, medium, high: prioritize stable, well-shot clips
  moment: medium     # low, medium, high: prioritize clips with audio events
```

That's it. Everything else has sane defaults.

With `codec: h265` and `hdr_mode: auto`, detected HLG or PQ material produces a 10-bit HDR video;
SDR clips, photos, and titles are converted to the same HDR transfer during assembly. H.264 is
always SDR. If you select `codec: h264`, detected HDR is tone-mapped to SDR even when
`hdr_mode: auto` is set. Use H.264 when broad playback compatibility matters more than HDR.

`target_duration_seconds` applies to the complete result, including titles and the time removed by
overlapping fades. When eligible material exists, the optimizer backfills unused clips before
accepting a short result. The encoded duration may differ by less than one transition because cuts
land on video frame boundaries.

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
| `fast-cuts` | Energetic, music-video style | 3-6s | 30% |
| `balanced` | Default, natural pacing | 5-10s | 40% |
| `long-cuts` | Cinematic, slow | 8-15s | 50% |

You can override individual duration parameters if needed. See the [Config Reference](../../reference/config-reference.md) for all options.

## Environment variable substitution

Any string value supports `${VAR_NAME}` syntax. The variable is expanded at load time:

```yaml
immich:
  api_key: ${IMMICH_API_KEY}

llm:
  api_key: ${OPENAI_API_KEY}
```

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

For the full list of 100+ options (scoring weights, hardware acceleration, audio ducking, title screen styling, scheduler, etc.), see the [Config Reference](../../reference/config-reference.md).
