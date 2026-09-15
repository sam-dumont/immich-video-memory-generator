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
the convention. `<SECTION>` is always the flat runtime name (`LLM`, `AUTH`, `EDITORIAL`…), never
`ADVANCED__LLM`, even for sections that live under `advanced:` in the YAML file. A nested field
takes another double underscore. The one exception to the pattern is the top-level `preset`, which
has no section and is `IMMICH_MEMORIES_PRESET`. Field names are in the
[config reference](../../reference/config-reference.md).

```bash
export IMMICH_MEMORIES_IMMICH__URL="https://photos.example.com"
export IMMICH_MEMORIES_IMMICH__API_KEY="your-api-key-here"
export IMMICH_MEMORIES_LLM__BASE_URL="https://api.openai.com/v1"
export IMMICH_MEMORIES_LLM__MODEL="gpt-4.1-nano"
export IMMICH_MEMORIES_OUTPUT__RESOLUTION="4k"
export IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL="http://localhost:8092/v1"
```

List- and dict-valued fields must be given as JSON. That includes `auth.trusted_proxies`,
`auth.allowed_emails`, `auth.allowed_domains`, `notifications.urls`, `scheduler.schedules`,
`analysis.exclude_filename_patterns`, `llm.drop_params`, `llm.extra_params`,
`llm.thinking_params` and `editorial.head_versions`:

```bash
export IMMICH_MEMORIES_AUTH__TRUSTED_PROXIES='["10.0.0.0/8"]'
export IMMICH_MEMORIES_ANALYSIS__EXCLUDE_FILENAME_PATTERNS='["RingVideo_*", "Screenshot*"]'
```

### Immich connection

```bash
export IMMICH_MEMORIES_IMMICH__URL="https://photos.example.com"
export IMMICH_MEMORIES_IMMICH__API_KEY="your-api-key-here"
export IMMICH_MEMORIES_IMMICH__API_VERSION="auto"
```

`auto` detects v2 or v3 at runtime; set `v2`/`v3` only to diagnose a proxy that breaks detection.
See [Immich API compatibility](./config-file.md#immich-api-compatibility) for what is tested.
`immich-memories config test` is read-only and prints the resolved API version.

## The ones that do not follow the pattern

**Keys that no longer exist.** The scene-detection and segment-length knobs that used to live under
`analysis` went with the clip scorer. A config file that still names one starts normally and logs
one warning naming every key it dropped; the environment-variable form is ignored in silence.
Delete both.

**The captioner has its own credential.** `OPENAI_API_KEY` does not reach it: it is a second
endpoint, so give it `IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_API_KEY` of its own, or leave
it blank for a server that needs none. There is no editorial opt-in variable, and new runs use the
FAMILY audience; see [Editorial annotation setup](editorial-preparation.md) before the first
uncached run.

**The encoder backend.** `IMMICH_MEMORIES_HARDWARE__BACKEND` names one instead of probing them all:
`auto` (the default), `none`, `nvidia`, `apple`, `vaapi` or `qsv`. Leave it on `auto` for normal
use. Naming one is for a benchmark, where a silent fall to software would otherwise look like a GPU
run: a host where the named backend cannot encode logs a warning and encodes in software anyway.

**The output directory in Docker.** It defaults to `~/Videos/Memories`, and the image overrides it
to `/app/output` in the Dockerfile. That override is an environment variable, so it beats
`output.directory` in `config.yaml`: to write somewhere else in a container, set
`IMMICH_MEMORIES_OUTPUT__DIRECTORY`, not the config file.

## Shorthand overrides

A few common variables are also supported without the full prefix:

| Variable | Overrides |
|----------|-----------|
| `IMMICH_URL` | `immich.url` |
| `IMMICH_API_KEY` | `immich.api_key` |
| `OPENAI_API_KEY` | `llm.api_key` |
| `ANTHROPIC_API_KEY` | `llm.api_key`, and wins over `OPENAI_API_KEY` when `llm.provider` is `anthropic` or `zai` |
| `MUSICGEN_ENABLED` | `musicgen.enabled` |
| `MUSICGEN_BASE_URL` | `musicgen.base_url` |
| `MUSICGEN_API_KEY` | `musicgen.api_key` |
| `ACE_STEP_ENABLED` | `ace_step.enabled` |
| `ACE_STEP_MODE` | `ace_step.mode` (`api` or `lib`; other values ignored) |
| `ACE_STEP_API_URL` | `ace_step.api_url` |
| `ACE_STEP_API_KEY` | `ace_step.api_key` |
| `IMMICH_MEMORIES_AUTH_USERNAME` + `IMMICH_MEMORIES_AUTH_PASSWORD` | `auth.username` / `auth.password`, and sets `auth.enabled=true`, `auth.provider=basic`. **Both** must be set; either alone is ignored. |

:::caution Shorthand vars are skipped with an explicit config path, except in the UI
The shorthand table is applied only when the app loads its default config path
(`~/.immich-memories/config.yaml`). `immich-memories --config PATH generate …` and a scheduler
daemon started with an explicit config file ignore every row above, including the basic-auth
shortcut.

`immich-memories --config PATH ui` is the exception, and it cuts the other way: the server reloads
the default config path for everything except host and port, so the shorthand *does* apply there
and the `auth:` block in `PATH` does not. The `IMMICH_MEMORIES_<SECTION>__<FIELD>` form always
works.
:::

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
`IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32`, and `PYTORCH_MPS_HIGH_WATERMARK_RATIO` from the shell you
install from into the plist or unit, and nothing else, since `IMMICH_MEMORIES_*` also holds
credentials. Change one of them and re-run `auto install`. See
[`auto install`](../../create/cli/auto.md#auto-install).
:::

## Precedence

Highest wins:

1. CLI flags (`--duration`, `--output`, …) for the options they cover
2. Shorthand environment variables (`IMMICH_URL`, `OPENAI_API_KEY`, `MUSICGEN_*`, `ACE_STEP_*`, the auth pair), applied last, on top of everything below
3. `IMMICH_MEMORIES_<SECTION>__<FIELD>` environment variables
4. Config file (`~/.immich-memories/config.yaml`)
5. Built-in defaults

So `IMMICH_URL=http://a` beats `IMMICH_MEMORIES_IMMICH__URL=http://b`, which beats `immich.url` in
the YAML file.
