---
title: CLI Reference
sidebar_label: CLI Reference
---

# CLI Reference

This page is auto-generated from the Click command definitions.
Run `make docs-cli` to regenerate.

## `cache`

Manage the analysis cache (LLM scores, video metadata).

```bash
immich-memories cache [COMMAND]
```

### `cache stats`

Show cache statistics: total scored assets, breakdown by type, oldest/newest entries.

```bash
immich-memories cache stats
```

### `cache export`

Export asset scores to JSON. Safe to run while the pipeline is active.

```bash
immich-memories cache export scores.json
```

### `cache import`

Import asset scores from a JSON backup. Useful for migrating between hosts.

```bash
immich-memories cache import scores.json
```

### `cache backup`

Full SQLite backup using the safe backup API (no corruption risk, even during writes).

```bash
immich-memories cache backup cache-backup.db
```

## `analyze`

Analyze videos and cache metadata.

```bash
immich-memories analyze [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Year to analyze |
| `--force`, `-f` | boolean | false | Force re-analysis of cached videos |

## `config`

Configure Immich connection settings.

```bash
immich-memories config [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--url`, `-u` | text | - | Immich server URL |
| `--api-key`, `-k` | text | - | Immich API key |
| `--show`, `-s` | boolean | false | Show current configuration |

## `export-project`

Export project state for later editing.

```bash
immich-memories export-project [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Year |
| `--person`, `-p` | text | - | Person name |
| `--output`, `-o` | path | - | Output JSON file |

## `generate`

Generate a video compilation.

```bash
immich-memories generate [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Year to generate video for (calendar year by default) |
| `--start` | text | - | Start date (YYYY-MM-DD or DD/MM/YYYY) |
| `--end` | text | - | End date (use with --start) |
| `--period` | text | - | Period from start date (e.g., 6m, 1y, 2w) |
| `--birthday`, `-b` | text | - | Birthday date for year calculation (use with --year) |
| `--person`, `-p` | text | - | Person name to filter by |
| `--duration`, `-d` | integer | - | Target duration in seconds |
| `--orientation`, `-o` | choice | landscape | Output orientation |
| `--scale-mode`, `-s` | choice | blur | Scaling mode |
| `--transition`, `-t` | choice | smart | Transition style |
| `--quality` | choice | high | Output quality (high, medium, low) |
| `--output`, `-O` | path | - | Output file path |
| `--music`, `-m` | path | - | Background music file |
| `--analysis-depth` | choice | fast | Analysis depth: `fast` (LLM for favorites only) or `thorough` (LLM for top candidates) |
| `--include-photos` | boolean | false | Include photos alongside videos |
| `--photo-duration` | float | 4.0 | Seconds per photo clip |
| `--dry-run` | boolean | false | Show what would be done without generating |

## `hardware`

Show hardware acceleration information.

```bash
immich-memories hardware [OPTIONS]
```

## `music`

Music and audio commands.

```bash
immich-memories music [OPTIONS]
```

### `music add`

Add background music to a video with automatic ducking.

```bash
immich-memories music add [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--music`, `-m` | path | - | Music file (auto-select if not provided) |
| `--mood` | text | - | Override mood for music selection |
| `--genre`, `-g` | text | - | Override genre for music selection |
| `--volume`, `-v` | float | -6.0 | Music volume in dB |
| `--fade-in` | float | 2.0 | Fade in duration in seconds |
| `--fade-out` | float | 3.0 | Fade out duration in seconds |

**Arguments:**
- `video_path` (path)
- `output_path` (path)

### `music analyze`

Analyze a video to determine its mood for music selection.

```bash
immich-memories music analyze [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--ollama-url` | text | - | Ollama API URL (default: from config) |
| `--ollama-model` | text | - | Ollama vision model (default: from config) |

**Arguments:**
- `video_path` (path)

### `music search`

Search for music in local library.

```bash
immich-memories music search [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--mood`, `-m` | text | - | Mood (happy, calm, energetic, etc.) |
| `--genre`, `-g` | text | - | Genre (acoustic, electronic, cinematic, etc.) |
| `--tempo`, `-t` | choice | - | Tempo |
| `--min-duration` | float | 60 | Minimum duration in seconds |
| `--limit`, `-n` | integer | 10 | Number of results |

## `people`

List all people in Immich.

```bash
immich-memories people [OPTIONS]
```

## `preflight`

Run preflight checks to validate all provider connections.

```bash
immich-memories preflight [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--verbose`, `-v` | boolean | false | Show detailed output |

## `runs`

Browse and manage pipeline run history.

See [runs CLI docs](../create/cli/runs.md) for detailed usage.

## `scheduler`

Manage the background scheduler.

See [scheduler CLI docs](../create/cli/scheduler.md) for detailed usage.

## `titles`

Title screen generation and testing commands.

See [titles CLI docs](../create/cli/titles.md) for detailed usage.

## `ui`

Launch the interactive NiceGUI UI.

```bash
immich-memories ui [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--port`, `-p` | integer | 8080 | Port to run the UI on |
| `--host`, `-h` | text | 0.0.0.0 | Host to bind to |
| `--reload` | boolean | false | Enable hot reload (for development only) |

## `years`

List years with video content.

```bash
immich-memories years [OPTIONS]
```
