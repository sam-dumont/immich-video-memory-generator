---
sidebar_position: 2
title: Environment variables
---

# Environment variables

Reader: power user.

Every config key has an environment variable, and an environment variable beats the config file.
On Docker, the ones you set live in `.env` beside `docker-compose.yml`.

## `.env` and `example.env`

The repo ships [`example.env`](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/example.env)
next to the compose file. Copy it to `.env` and fill it in. Docker Compose reads `.env` by itself,
and the compose file hands each of its variables to the container:

```bash
IMMICH_URL=http://192.168.1.10:2283
IMMICH_API_KEY=
IMMICH_MEMORIES_TRIPS__HOMEBASE_LATITUDE=
IMMICH_MEMORIES_TRIPS__HOMEBASE_LONGITUDE=
TZ=Etc/UTC
# IMMICH_MEMORIES_AUTH_USERNAME=
# IMMICH_MEMORIES_AUTH_PASSWORD=
```

:::caution An edit needs a recreate
The container reads its environment when it starts. After changing `.env` or the compose file,
run `docker compose up -d`, which recreates it. `docker compose restart` does not.
:::

A variable that is neither in `example.env` nor in the compose file's `environment:` block does
not reach the container: compose uses `.env` to fill in the compose file, not as the container's
environment. Add the line to `environment:` as well, like the commented ones already there.

## The variables you are most likely to set

"Tier" is where the key lives in `config.yaml`: top level (1) or under `advanced:`. "Compose" says
whether the shipped compose file already passes it.

### Immich and home

| Variable | Config key | Default | Tier | Compose | What it does |
|---|---|---|---|---|---|
| `IMMICH_URL` | `immich.url` | none | 1 | yes, `.env` | Immich, as the app reaches it. Required |
| `IMMICH_API_KEY` | `immich.api_key` | none | 1 | yes, `.env` | Required. Permissions: [The API key](./docker.md#the-api-key) |
| `IMMICH_MEMORIES_TRIPS__HOMEBASE_LATITUDE` | `trips.homebase_latitude` | `0` (unset) | 1 | yes, `.env` | Your home. Without it no day is away from home and no story is a trip |
| `IMMICH_MEMORIES_TRIPS__HOMEBASE_LONGITUDE` | `trips.homebase_longitude` | `0` (unset) | 1 | yes, `.env` | Same, the longitude |
| `TZ` | none | `Etc/UTC` | none | yes, `.env` | The zone `automation.daily_at` and the logs use |
| `IMMICH_MEMORIES_IMMICH__API_VERSION` | `immich.api_version` | `auto` | 1 | no | `v2` or `v3` only to diagnose a proxy that breaks detection |

### The web UI

| Variable | Config key | Default | Tier | Compose | What it does |
|---|---|---|---|---|---|
| `IMMICH_MEMORIES_AUTH_USERNAME`, `IMMICH_MEMORIES_AUTH_PASSWORD` | `auth.username`, `auth.password` | empty | advanced | yes, `.env` | Set both to turn on basic auth. Either alone is ignored |
| `IMMICH_MEMORIES_STORAGE_SECRET` | none | generated | none | commented | Session secret. Generated once onto the config volume, so sessions survive a recreate without it |
| `IMMICH_MEMORIES_AUTOMATION__ENABLED` | `automation.enabled` | `false` | advanced | commented | The daily memory, inside the UI process |
| `IMMICH_MEMORIES_AUTOMATION__DAILY_AT` | `automation.daily_at` | `09:00` | advanced | commented | When, in the `TZ` zone |

### Preparation and output

| Variable | Config key | Default | Tier | Compose | What it does |
|---|---|---|---|---|---|
| `IMMICH_MEMORIES_TIER` | `tier` | `auto` | 1 | `auto` | Resolves NAS, GPU or Full from inference capability and the configured LLM; preparation follows it |
| `IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR` | `editorial.preparation.detector_cache_dir` | the Hugging Face cache | advanced | the config volume | Where the document classifier lives. Keep it on a volume |
| `IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL` | `editorial.preparation.caption_base_url` | `http://localhost:8092/v1` | advanced | commented | The caption server, for `tier: full` |
| `IMMICH_MEMORIES_OUTPUT__DIRECTORY` | `output.directory` | `~/Videos/Memories`; `/app/output` in the image | 1 | set by the image | Where films land. In a container, set this, not `output.directory` |
| `IMMICH_MEMORIES_PRESET` | `preset` | none | 1 | no | `fast`: 1080p H.264, fast encoder preset, static titles. Explicit settings win |
| `IMMICH_MEMORIES_HARDWARE__BACKEND` | `hardware.backend` | `auto` | advanced | no | `none`, `nvidia`, `apple`, `vaapi` or `qsv` to name one encoder instead of probing |

### Optional add-ons

| Variable | Config key | Default | Tier | Compose | What it does |
|---|---|---|---|---|---|
| `IMMICH_MEMORIES_LLM__BASE_URL` | `llm.base_url` | `http://localhost:8080/v1` | advanced | commented | The reader's endpoint. The default is the app's own port: set it |
| `IMMICH_MEMORIES_LLM__MODEL` | `llm.model` | empty | advanced | commented | The reader. Empty means the rules editor works alone. Must match `GET /v1/models`; a text model is enough |
| `IMMICH_MEMORIES_LLM__API_KEY` | `llm.api_key` | empty | advanced | no | The reader's token, for a server that answers `401` |
| `IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL` | `inference.facts_base_url` | empty | advanced | commented | Send the heads and detectors to the [inference service](../better/inference.md) |

## The pattern

Every other key follows `IMMICH_MEMORIES_<SECTION>__<FIELD>`, with a double underscore between
levels. `<SECTION>` is always the flat runtime name (`LLM`, `AUTH`, `EDITORIAL`), never
`ADVANCED__LLM`, even for sections that live under `advanced:` in the file. A nested field takes
another double underscore. Field names are in the [config reference](../reference/config-reference.md).

```bash
IMMICH_MEMORIES_OUTPUT__RESOLUTION=4k
IMMICH_MEMORIES_EDITORIAL__PEOPLE__SEAT_MIN_PICTURES=30
```

List- and dict-valued fields take JSON: `auth.trusted_proxies`, `auth.allowed_emails`,
`auth.allowed_domains`, `notifications.urls`, `scheduler.schedules`,
`analysis.exclude_filename_patterns`, `llm.drop_params`, `llm.extra_params`, `llm.thinking_params`
and `editorial.head_versions`.

```bash
IMMICH_MEMORIES_ANALYSIS__EXCLUDE_FILENAME_PATTERNS='["RingVideo_*", "Screenshot*"]'
```

### Immich connection

```bash
IMMICH_MEMORIES_IMMICH__URL="https://photos.example.com"
IMMICH_MEMORIES_IMMICH__API_KEY="your-api-key-here"
IMMICH_MEMORIES_IMMICH__API_VERSION="auto"
```

`auto` detects v2 or v3 at runtime; set `v2`/`v3` only to diagnose a proxy that breaks detection.
`immich-memories config test` is read-only and prints the API version it found:
[Immich API compatibility](./config-file.md#immich-api-compatibility).

## The ones that do not follow the pattern

**Retired keys are dropped.** The scene-detection and segment-length knobs that went with the old
clip scorer are dropped by name from a config file with one warning; the environment-variable form
gets no warning at all. Delete both.

**The captioner has its own credential.** `OPENAI_API_KEY` does not reach it: give it
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_API_KEY`, or leave it blank for a server that
needs none.

### Shorthands

| Variable | Overrides |
|----------|-----------|
| `IMMICH_URL` | `immich.url` |
| `IMMICH_API_KEY` | `immich.api_key` |
| `OPENAI_API_KEY` | `llm.api_key`, only when the config file states no key |
| `ANTHROPIC_API_KEY` | `llm.api_key` under the same rule, read instead of `OPENAI_API_KEY` when `llm.provider` is `anthropic` or `zai` |
| `MUSICGEN_ENABLED`, `MUSICGEN_BASE_URL`, `MUSICGEN_API_KEY` | `musicgen.enabled`, `.base_url`, `.api_key` |
| `ACE_STEP_ENABLED`, `ACE_STEP_API_URL`, `ACE_STEP_API_KEY` | `ace_step.enabled`, `.api_url`, `.api_key` |
| `ACE_STEP_MODE` | `ace_step.mode` (`api` or `lib`; other values ignored) |
| `IMMICH_MEMORIES_AUTH_USERNAME` + `IMMICH_MEMORIES_AUTH_PASSWORD` | `auth.username` and `auth.password`, and sets `auth.enabled=true`, `auth.provider=basic`. Both must be set |

An empty shorthand counts as unset.

:::caution An LLM key written in the file beats its shorthand
`OPENAI_API_KEY` is the name every OpenAI-SDK client reads, a local mlx or vLLM server included, so
on a machine that exports it for that server it says nothing about the endpoint the key will be
sent to. So a key in `llm.api_key` wins, and the variable fills the field only where the
file leaves it empty or holds a `${VAR}` nobody set. `ANTHROPIC_API_KEY` works the same way. To
replace a key that is in the file, use `IMMICH_MEMORIES_LLM__API_KEY`.
:::

`--config PATH` makes PATH the single config file, and the same variables apply to it.

### Not config keys

| Variable | Effect |
|----------|--------|
| `IMMICH_MEMORIES_STORAGE_SECRET` | Secret for the web UI session store. Priority: this variable, then `~/.immich-memories/.storage_secret`, then generated on first start |
| `IMMICH_MEMORIES_LOG_FORMAT` | `text` (default) or `json` |
| `IMMICH_MEMORIES_LOG_LEVEL` | `INFO` (default), `DEBUG`, `WARNING` or `ERROR`. The CLI's `-v` and `--log-level` win for one run |
| `IMMICH_MEMORIES_LOG_FILE` | Also write logs to this file |
| `IMMICH_FORCE_CPU` | `1`, `true` or `yes` puts the title renderer on the CPU even with a GPU |
| `ACESTEP_CHECKPOINTS_DIR` | ACE-Step `lib` mode: where checkpoints go (default `~/.cache/ace-step/checkpoints`) |
| `ACESTEP_MLX_VAE_CHUNK` | ACE-Step `lib` mode on Apple Silicon: VAE decode chunk in latent frames (minimum 192). Lower it if MLX runs out of memory |
| `IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32` | ACE-Step `lib` mode on Apple Silicon: `1` keeps the decoder in fp32 (about twice the memory) |
| `FORWARDED_ALLOW_IPS` | uvicorn: proxies whose `X-Forwarded-*` headers are trusted. Wins over `auth.trusted_proxies`. See [Authentication](./authentication.mdx) |

:::caution Scheduled jobs do not inherit your shell
A launchd or cron job starts from a login-less environment, so nothing you `export` interactively
reaches it. `auto install` copies `PATH`, `ACESTEP_CHECKPOINTS_DIR`, `ACESTEP_MLX_VAE_CHUNK`,
`IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32` and `PYTORCH_MPS_HIGH_WATERMARK_RATIO` into the plist or
unit, and nothing else, since `IMMICH_MEMORIES_*` also holds credentials. Change one of them and
run [`auto install`](../make/automate.md) again.
:::

## Precedence

Highest wins:

1. CLI flags (`--duration`, `--output`, ...) for the options they cover
2. Shorthand variables (the table above)
3. `IMMICH_MEMORIES_<SECTION>__<FIELD>` variables
4. The config file (`~/.immich-memories/config.yaml`)
5. Built-in defaults

So `IMMICH_URL=http://a` beats `IMMICH_MEMORIES_IMMICH__URL=http://b`, which beats `immich.url` in
the file. `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are the exception above: they sit below the
config file.

## Compute tier

`IMMICH_MEMORIES_TIER=auto|nas|gpu|full` selects the same tier as `tier:` in the config file.
The default is `auto`: no GPU inference capability means NAS; GPU capability means GPU;
GPU capability plus a configured LLM means Full. An LLM alone remains available for text features.
See the [tier reference](../reference/config-reference.md#tier).
