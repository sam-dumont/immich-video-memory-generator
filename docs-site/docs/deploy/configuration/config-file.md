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
  format: "mp4"                  # mp4 or mov container
  resolution: "1080p"            # 720p, 1080p, 4k
  codec: "h264"                  # h264, h265, prores
  hdr_mode: "auto"               # auto, sdr, hdr

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

H.264 and ProRes are SDR-only; ProRes requires MOV; H.265 is the only HDR output. The full
compatibility matrix is in the [Config Reference](../../reference/config-reference.md#output).

That's it. Everything else has sane defaults.

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
