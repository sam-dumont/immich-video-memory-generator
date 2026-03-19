---
title: CLI Reference (Auto-Generated)
sidebar_label: Reference
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

Time period options:


Calendar year:
  --year 2024                    (Jan 1, 2024 - Dec 31, 2024)


Birthday-based year:
  --year 2024 --birthday 02/07   (Feb 7, 2024 - Feb 6, 2025)


Custom date range:
  --start 2024-01-01 --end 2024-06-30


Period from start date:
  --start 2024-01-01 --period 6m   (6 months)
  --start 2024-01-01 --period 1y   (1 year)

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
| `--duration`, `-d` | integer | 10 | Target duration in minutes |
| `--orientation`, `-o` | choice | landscape | Output orientation |
| `--scale-mode`, `-s` | choice | smart_crop | Scaling mode |
| `--transition`, `-t` | choice | crossfade | Transition style |
| `--output`, `-O` | path | - | Output file path |
| `--music`, `-m` | path | - | Background music file |
| `--analysis-depth` | choice | fast | Analysis depth: `fast` (LLM for favorites only) or `thorough` (LLM for top candidates) |
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

If no music file is provided, automatically selects music based on video mood.
Music volume is automatically lowered when speech/sounds are detected.

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

Checks:
- Immich server connection and API key
- Ollama availability (for mood/content analysis)
- OpenAI API key (fallback for analysis)
- Pixabay API key (for music search)
- Hardware acceleration

```bash
immich-memories preflight [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--verbose`, `-v` | boolean | false | Show detailed output |

## `runs`

Browse and manage pipeline run history.

```bash
immich-memories runs [OPTIONS]
```

### `runs delete`

Delete a run and optionally its output files.

Examples:


# Delete run and its output
immich-memories runs delete 20260105_143052_a7b3


# Delete run but keep the video
immich-memories runs delete 20260105_143052_a7b3 --keep-output

```bash
immich-memories runs delete [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--keep-output` | boolean | false | Keep the output video file |
| `--yes` | boolean | false | Confirm the action without prompting. |

**Arguments:**
- `run_id` (text)

### `runs list`

List recent pipeline runs.

Examples:


# List recent runs
immich-memories runs list


# Filter by person
immich-memories runs list --person "John"


# Show only failed runs
immich-memories runs list --status failed

```bash
immich-memories runs list [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--limit`, `-n` | integer | 20 | Number of runs to show |
| `--person`, `-p` | text | - | Filter by person name |
| `--status`, `-s` | choice | - | Filter by status |

### `runs show`

Show detailed information about a specific run.

Example:
    immich-memories runs show 20260105_143052_a7b3

```bash
immich-memories runs show [OPTIONS]
```

**Arguments:**
- `run_id` (text)

### `runs stats`

Show aggregate statistics across all runs.

```bash
immich-memories runs stats [OPTIONS]
```

## `titles`

Title screen generation and testing commands.

```bash
immich-memories titles [OPTIONS]
```

### `titles fonts`

Manage title screen fonts.

Downloads OFL-licensed fonts from Google Fonts and caches
them locally in ~/.immich-memories/fonts/.

```bash
immich-memories titles fonts [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--download`, `-d` | boolean | false | Download all fonts |
| `--clear` | boolean | false | Clear font cache |
| `--list` | boolean | false | List cached fonts |

### `titles test`

Generate a test title screen to preview styles.

Examples:


# Simple year title
immich-memories titles test --year 2024


# Birthday title with person name
immich-memories titles test --birthday-age 1 --person "Emma"


# Month divider
immich-memories titles test --month 6 --year 2024 --type month


# Portrait orientation (for social media)
immich-memories titles test --year 2024 --orientation portrait


# French locale with specific style
immich-memories titles test --year 2024 --locale fr --style vintage_charm

```bash
immich-memories titles test [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Year for title screen (e.g., 2024) |
| `--birthday-age` | integer | - | Age for birthday title (e.g., 1 for '1st Year') |
| `--person`, `-p` | text | - | Person name for subtitle |
| `--month`, `-m` | integer | - | Month for month divider (1-12) |
| `--orientation`, `-o` | choice | landscape | Output orientation |
| `--resolution`, `-r` | choice | 1080p | Output resolution |
| `--locale`, `-l` | choice | en | Language |
| `--style`, `-s` | choice | random | Visual style |
| `--output`, `-O` | path | - | Output file path |
| `--type` | choice | title | Screen type |
| `--download-fonts` | boolean | false | Download fonts before generating |
| `--no-animated-background` | boolean | false | Disable animated backgrounds (static gradient) |

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
