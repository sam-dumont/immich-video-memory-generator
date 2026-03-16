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

# ── Output ────────────────────────────────────────────────
output:
  directory: "~/Videos/Memories"
  resolution: "1080p"            # 720p, 1080p, 4k

defaults:
  target_duration_minutes: 10    # 1-60 minutes
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
```

That's it. Everything else has sane defaults.

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

You can override individual duration parameters if needed. See [Advanced Configuration](./advanced.md) for all options.

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

For the full list of 100+ options (scoring weights, hardware acceleration, audio ducking, title screen styling, scheduler, etc.), see the [Advanced Configuration](./advanced.md) reference.
