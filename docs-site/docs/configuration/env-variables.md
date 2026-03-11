---
sidebar_position: 2
title: Environment Variables
---

# Environment Variables

Every config field can be set via environment variable. The pattern is:

```
IMMICH_MEMORIES_<SECTION>__<FIELD>
```

Note the **double underscore** between section and field. All uppercase.

## Examples

### Immich connection

```bash
export IMMICH_MEMORIES_IMMICH__URL="https://photos.example.com"
export IMMICH_MEMORIES_IMMICH__API_KEY="your-api-key-here"
```

### Analysis settings

```bash
export IMMICH_MEMORIES_ANALYSIS__SCENE_THRESHOLD="30.0"
export IMMICH_MEMORIES_ANALYSIS__MIN_SCENE_DURATION="1.5"
export IMMICH_MEMORIES_ANALYSIS__ANALYSIS_RESOLUTION="720"
```

### LLM provider

```bash
export IMMICH_MEMORIES_LLM__PROVIDER="openai-compatible"
export IMMICH_MEMORIES_LLM__BASE_URL="https://api.openai.com/v1"
export IMMICH_MEMORIES_LLM__MODEL="gpt-4.1-nano"
export IMMICH_MEMORIES_LLM__API_KEY="sk-..."
```

### Hardware

```bash
export IMMICH_MEMORIES_HARDWARE__ENABLED="true"
export IMMICH_MEMORIES_HARDWARE__BACKEND="nvidia"
export IMMICH_MEMORIES_HARDWARE__ENCODER_PRESET="quality"
```

### Output

```bash
export IMMICH_MEMORIES_OUTPUT__DIRECTORY="/mnt/nas/memories"
export IMMICH_MEMORIES_OUTPUT__RESOLUTION="4k"
export IMMICH_MEMORIES_OUTPUT__CODEC="h265"
export IMMICH_MEMORIES_OUTPUT__CRF="20"
```

### Music generation

```bash
export IMMICH_MEMORIES_MUSICGEN__ENABLED="true"
export IMMICH_MEMORIES_MUSICGEN__BASE_URL="http://gpu-server:8000"
export IMMICH_MEMORIES_MUSICGEN__API_KEY="your-key"

export IMMICH_MEMORIES_ACE_STEP__ENABLED="true"
export IMMICH_MEMORIES_ACE_STEP__MODE="api"
export IMMICH_MEMORIES_ACE_STEP__API_URL="http://gpu-server:8000"
```

## Shorthand overrides

A few common variables are also supported without the full prefix, for convenience:

| Variable | Overrides |
|----------|-----------|
| `IMMICH_URL` | `immich.url` |
| `IMMICH_API_KEY` | `immich.api_key` |
| `OPENAI_API_KEY` | `llm.api_key` |
| `MUSICGEN_ENABLED` | `musicgen.enabled` |
| `MUSICGEN_BASE_URL` | `musicgen.base_url` |
| `MUSICGEN_API_KEY` | `musicgen.api_key` |
| `ACE_STEP_ENABLED` | `ace_step.enabled` |
| `ACE_STEP_MODE` | `ace_step.mode` |
| `ACE_STEP_API_URL` | `ace_step.api_url` |

## Precedence

Environment variables override config file values. The full precedence order:

1. CLI flags (highest priority)
2. Environment variables (`IMMICH_MEMORIES_*` prefix)
3. Shorthand environment variables (`IMMICH_URL`, etc.)
4. Config file (`~/.immich-memories/config.yaml`)
5. Built-in defaults (lowest priority)
