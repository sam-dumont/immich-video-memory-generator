---
sidebar_position: 2
title: Environment Variables
---

# Environment Variables

Every config field can be set via environment variable. The pattern is:

```
IMMICH_MEMORIES_<SECTION>__<FIELD>
```

Note the **double underscore** between section and field. Case does not matter, but uppercase is
the convention. `<SECTION>` is always the flat runtime name (`LLM`, `AUTH`, `SPEECH`…) — never
`ADVANCED__LLM`, even for sections that live under `advanced:` in the YAML file.

List-valued fields (`auth.trusted_proxies`, `notifications.urls`, `transcription.languages`,
`scheduler.schedules`) must be given as JSON:

```bash
export IMMICH_MEMORIES_AUTH__TRUSTED_PROXIES='["10.0.0.0/8"]'
export IMMICH_MEMORIES_TRANSCRIPTION__LANGUAGES='["fr", "en"]'
```

## Examples

### Immich connection

```bash
export IMMICH_MEMORIES_IMMICH__URL="https://photos.example.com"
export IMMICH_MEMORIES_IMMICH__API_KEY="your-api-key-here"
export IMMICH_MEMORIES_IMMICH__API_VERSION="auto"
```

`auto` detects v2 or v3 at runtime; set `v2`/`v3` only to diagnose a proxy that breaks detection.
See [Immich API compatibility](./config-file.md#immich-api-compatibility) for what is supported and
what is tested. `immich-memories config test` is read-only and prints the resolved API version.

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
export IMMICH_MEMORIES_CONTENT_ANALYSIS__ENABLED="true"   # without this the LLM is never called for scoring
```

### Hardware

```bash
export IMMICH_MEMORIES_HARDWARE__ENABLED="true"       # false = CPU-only encoding
export IMMICH_MEMORIES_HARDWARE__ENCODER_PRESET="quality"
export IMMICH_MEMORIES_HARDWARE__GPU_DECODE="true"
```

The backend (NVENC, VideoToolbox, QSV, VAAPI) is auto-detected; there is no working override.

### Output

```bash
export IMMICH_MEMORIES_OUTPUT__DIRECTORY="/mnt/nas/memories"
export IMMICH_MEMORIES_OUTPUT__RESOLUTION="4k"
export IMMICH_MEMORIES_OUTPUT__CODEC="h265"
export IMMICH_MEMORIES_OUTPUT__CRF="20"
```

`DIRECTORY` defaults to `~/Videos/Memories`. The Docker image overrides it to `/app/output` in the
Dockerfile, so you only set it in a container to write somewhere else — and because it is an
environment variable it beats `output.directory` in `config.yaml`.

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
| `ACE_STEP_MODE` | `ace_step.mode` (`api` or `lib`; other values ignored) |
| `ACE_STEP_API_URL` | `ace_step.api_url` |
| `ACE_STEP_API_KEY` | `ace_step.api_key` |
| `IMMICH_MEMORIES_AUTH_USERNAME` + `IMMICH_MEMORIES_AUTH_PASSWORD` | `auth.username` / `auth.password`, and sets `auth.enabled=true`, `auth.provider=basic`. **Both** must be set; either alone is ignored. |

:::caution Shorthand vars are skipped with an explicit config path
The shorthand table is applied only when the app loads its default config path
(`~/.immich-memories/config.yaml`). `immich-memories --config PATH …` and a scheduler daemon
started with an explicit config file ignore every row above — including the basic-auth shortcut.
The `IMMICH_MEMORIES_<SECTION>__<FIELD>` form always works.
:::

## Other environment variables

Not config fields, but read by the app:

| Variable | Effect |
|----------|--------|
| `IMMICH_MEMORIES_STORAGE_SECRET` | Secret for the web UI session store. Priority: this var > `~/.immich-memories/.storage_secret` file > generated on first start. Set it in Docker so sessions survive container recreation. |
| `IMMICH_MEMORIES_LOG_FORMAT` | `text` (default) or `json`. |
| `IMMICH_MEMORIES_LOG_FILE` | When set, logs are written to this file in addition to stdout. |
| `IMMICH_FORCE_CPU` | `1`/`true`/`yes` forces the Taichi title renderer onto CPU even when a GPU is available. |
| `ACESTEP_CHECKPOINTS_DIR` | ACE-Step `lib` mode: where model checkpoints are downloaded (default `~/.cache/ace-step/checkpoints`). |
| `ACESTEP_MLX_VAE_CHUNK` | ACE-Step `lib` mode on Apple Silicon: VAE decode chunk size in latent frames (minimum 192). Lower it if MLX runs out of memory. |
| `IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32` | ACE-Step `lib` mode on Apple Silicon: `1` keeps the MLX decoder in fp32 instead of casting to bf16 (roughly doubles decoder memory). |
| `FORWARDED_ALLOW_IPS` | uvicorn: proxies whose `X-Forwarded-*` headers are trusted. Wins over `auth.trusted_proxies` when set — see [Authentication](authentication.mdx). |

There is no environment variable for the log *level*.

:::caution Scheduled jobs do not inherit your shell
A launchd or cron job starts from a login-less environment, so nothing you `export` interactively
reaches it. `auto install` copies `PATH`, `ACESTEP_CHECKPOINTS_DIR`, `ACESTEP_MLX_VAE_CHUNK`,
`IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32`, and `PYTORCH_MPS_HIGH_WATERMARK_RATIO` from the shell you
install from into the plist or unit — and nothing else, since `IMMICH_MEMORIES_*` also holds
credentials. Change one of them and re-run `auto install`. See
[`auto install`](../../create/cli/auto.md#what-environment-the-scheduled-job-sees).
:::

## Precedence

Highest wins:

1. CLI flags (`--duration`, `--output`, …) for the options they cover
2. Shorthand environment variables (`IMMICH_URL`, `OPENAI_API_KEY`, `MUSICGEN_*`, `ACE_STEP_*`, the auth pair) — applied last, on top of everything below
3. `IMMICH_MEMORIES_<SECTION>__<FIELD>` environment variables
4. Config file (`~/.immich-memories/config.yaml`)
5. Built-in defaults

So `IMMICH_URL=http://a` beats `IMMICH_MEMORIES_IMMICH__URL=http://b`, which beats `immich.url` in
the YAML file.
