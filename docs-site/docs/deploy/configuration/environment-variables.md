---
sidebar_position: 2
title: Environment Variables
---

# Environment Variables

Every config field can be set via environment variable. The pattern is:

```
IMMICH_MEMORIES_<SECTION>__<FIELD>
```

Note the **double underscore** between section and field, and that `<SECTION>` is always the flat
runtime name (`LLM`, `AUTH`, `EDITORIAL`…), never `ADVANCED__LLM`, even for sections that live under
`advanced:` in the YAML file. A nested field takes another double underscore. The one exception is
the top-level `preset`: `IMMICH_MEMORIES_PRESET`. Field names are in the
[config reference](../../reference/config-reference.md).

```bash
export IMMICH_MEMORIES_LLM__MODEL="gpt-4.1-nano"
export IMMICH_MEMORIES_OUTPUT__RESOLUTION="4k"
export IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL="http://localhost:8092/v1"
```

List- and dict-valued fields must be given as JSON: `auth.trusted_proxies`, `auth.allowed_emails`,
`auth.allowed_domains`, `notifications.urls`, `scheduler.schedules`,
`analysis.exclude_filename_patterns`, `llm.drop_params`, `llm.extra_params`, `llm.thinking_params`
and `editorial.head_versions`.

```bash
export IMMICH_MEMORIES_ANALYSIS__EXCLUDE_FILENAME_PATTERNS='["RingVideo_*", "Screenshot*"]'
```

### Immich connection

```bash
export IMMICH_MEMORIES_IMMICH__URL="https://photos.example.com"
export IMMICH_MEMORIES_IMMICH__API_KEY="your-api-key-here"
export IMMICH_MEMORIES_IMMICH__API_VERSION="auto"
```

`auto` detects v2 or v3 at runtime; set `v2`/`v3` only to diagnose a proxy that breaks detection.
`immich-memories config test` is read-only and prints the resolved API version. Background:
[Immich API compatibility](./config-file.md#immich-api-compatibility).

## The ones that do not follow the pattern

**Retired keys are ignored in silence.** The scene-detection and segment-length knobs that went
with the clip scorer are dropped by name from a config file with one warning; the
environment-variable form gets no warning at all. Delete both.

**The captioner has its own credential.** `OPENAI_API_KEY` does not reach it: it is a second
endpoint, so give it `IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_API_KEY`, or leave it blank
for a server that needs none.

**The encoder backend.** `IMMICH_MEMORIES_HARDWARE__BACKEND` names one instead of probing them all:
`auto` (the default), `none`, `nvidia`, `apple`, `vaapi` or `qsv`. Naming one is for a benchmark,
where a silent fall to software would otherwise look like a GPU run.

**The output directory in Docker.** The image sets `IMMICH_MEMORIES_OUTPUT__DIRECTORY=/app/output`
in the Dockerfile, and an environment variable beats the config file: to write somewhere else in a
container, set that variable, not `output.directory`.

## Shorthand overrides

A few common variables are also supported without the full prefix:

| Variable | Overrides |
|----------|-----------|
| `IMMICH_URL` | `immich.url` |
| `IMMICH_API_KEY` | `immich.api_key` |
| `OPENAI_API_KEY` | `llm.api_key`, only when the config file states no key |
| `ANTHROPIC_API_KEY` | `llm.api_key` under the same rule, and it is the name read instead of `OPENAI_API_KEY` when `llm.provider` is `anthropic` or `zai` |
| `MUSICGEN_ENABLED` | `musicgen.enabled` |
| `MUSICGEN_BASE_URL` | `musicgen.base_url` |
| `MUSICGEN_API_KEY` | `musicgen.api_key` |
| `ACE_STEP_ENABLED` | `ace_step.enabled` |
| `ACE_STEP_MODE` | `ace_step.mode` (`api` or `lib`; other values ignored) |
| `ACE_STEP_API_URL` | `ace_step.api_url` |
| `ACE_STEP_API_KEY` | `ace_step.api_key` |
| `IMMICH_MEMORIES_AUTH_USERNAME` + `IMMICH_MEMORIES_AUTH_PASSWORD` | `auth.username` / `auth.password`, and sets `auth.enabled=true`, `auth.provider=basic`. **Both** must be set; either alone is ignored. |

:::caution An LLM key written in the file beats its shorthand
`OPENAI_API_KEY` is the name every OpenAI-SDK client reads, a local mlx or vLLM server included, so
on a machine that exports it for that server it says nothing about the endpoint the key will be
sent to. A key in `llm.api_key` therefore wins, and the variable fills the field only where the
file leaves it empty or holds a `${VAR}` nobody set. `ANTHROPIC_API_KEY` works the same way. To
replace a key that is in the file, use `IMMICH_MEMORIES_LLM__API_KEY`. Every other row in the table
still overrides whatever the file holds.
:::

The rows apply whatever file the process loaded: `--config PATH` installs PATH as its single
configuration source and reads the same variables the default `~/.immich-memories/config.yaml`
does. The `IMMICH_MEMORIES_<SECTION>__<FIELD>` form always works.

## Other environment variables

Not config fields, but read by the app:

| Variable | Effect |
|----------|--------|
| `IMMICH_MEMORIES_STORAGE_SECRET` | Secret for the web UI session store. Priority: this var > `~/.immich-memories/.storage_secret` file > generated on first start. Set it in Docker so sessions survive container recreation. |
| `IMMICH_MEMORIES_LOG_FORMAT` | `text` (default) or `json`. |
| `IMMICH_MEMORIES_LOG_LEVEL` | `INFO` (default), `DEBUG`, `WARNING` or `ERROR`. The CLI flags `-v` and `--log-level` win over it for one run. |
| `IMMICH_MEMORIES_LOG_FILE` | When set, logs are written to this file in addition to stdout. |
| `IMMICH_FORCE_CPU` | `1`/`true`/`yes` forces the GPU title renderer onto the CPU backend even when a GPU is available. |
| `ACESTEP_CHECKPOINTS_DIR` | ACE-Step `lib` mode: where model checkpoints are downloaded (default `~/.cache/ace-step/checkpoints`). |
| `ACESTEP_MLX_VAE_CHUNK` | ACE-Step `lib` mode on Apple Silicon: VAE decode chunk size in latent frames (minimum 192). Lower it if MLX runs out of memory. |
| `IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32` | ACE-Step `lib` mode on Apple Silicon: `1` keeps the MLX decoder in fp32 instead of casting to bf16 (roughly doubles decoder memory). |
| `FORWARDED_ALLOW_IPS` | uvicorn: proxies whose `X-Forwarded-*` headers are trusted. Wins over `auth.trusted_proxies` when set. See [Authentication](authentication.mdx). |

:::caution Scheduled jobs do not inherit your shell
A launchd or cron job starts from a login-less environment, so nothing you `export` interactively
reaches it. `auto install` copies `PATH`, `ACESTEP_CHECKPOINTS_DIR`, `ACESTEP_MLX_VAE_CHUNK`,
`IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32` and `PYTORCH_MPS_HIGH_WATERMARK_RATIO` into the plist or
unit, and nothing else, since `IMMICH_MEMORIES_*` also holds credentials. Change one of them and
re-run [`auto install`](../../create/cli/auto.md#auto-install).
:::

## Precedence

Highest wins:

1. CLI flags (`--duration`, `--output`, …) for the options they cover
2. Shorthand environment variables (the whole table above)
3. `IMMICH_MEMORIES_<SECTION>__<FIELD>` environment variables
4. Config file (`~/.immich-memories/config.yaml`)
5. Built-in defaults

So `IMMICH_URL=http://a` beats `IMMICH_MEMORIES_IMMICH__URL=http://b`, which beats `immich.url` in
the YAML file. `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are the exception noted above: they sit
below the config file rather than above it.
