---
title: CLI Reference (Auto-Generated)
sidebar_label: Reference
---

# CLI Reference

This page is auto-generated from the Click command definitions.
Run `make docs-cli` to regenerate.

## `analyze`

Analyze videos and cache metadata.

```bash
immich-memories analyze [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Year to analyze |
| `--force`, `-f` | boolean | false | Force re-analysis of cached videos |

## `auto`

Smart automation -- detect and generate memory candidates.

```bash
immich-memories auto [OPTIONS]
```

### `auto history`

Show recent auto-generated memories.

```bash
immich-memories auto history [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--limit` | integer | 10 | Number of entries to show |

### `auto install`

Install system-level scheduler (launchd/systemd/cron).

```bash
immich-memories auto install [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--hour` | integer range | 9 | Hour to run (0-23) |
| `--minute` | integer range | 0 | Minute to run (0-59) |
| `--cooldown` | integer | 24 | Cooldown hours between runs |
| `--uninstall` | boolean | false | Remove installed scheduler |
| `--show` | boolean | false | Show config without installing |

### `auto run`

Generate the top-scoring memory candidate.

```bash
immich-memories auto run [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--dry-run` | boolean | false | Show what would be generated |
| `--force` | boolean | false | Skip cooldown check |
| `--cooldown` | integer | - | Min hours since last auto-run |
| `--upload` | boolean | false | Upload to Immich |
| `--quiet` | boolean | false | Machine-friendly output |

### `auto status`

Show durable automation and external scheduler state.

```bash
immich-memories auto status [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--json` | boolean | false | Machine-readable output |

### `auto suggest`

Show prioritized memory candidates.

```bash
immich-memories auto suggest [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--json` | boolean | false | Machine-readable output |
| `--limit` | integer | 10 | Max candidates to show |
| `--type` | text | - | Filter by memory type |

### `auto test-notification`

Send a test notification to verify Apprise URL configuration.

```bash
immich-memories auto test-notification [OPTIONS]
```

## `cache`

Manage the analysis cache (LLM scores, video metadata).

```bash
immich-memories cache [OPTIONS]
```

### `cache backup`

Backup the entire cache DB (safe SQLite backup API).

```bash
immich-memories cache backup [OPTIONS]
```

**Arguments:**
- `output_path` (path)

### `cache export`

Export asset scores to JSON (safe, lock-aware).

```bash
immich-memories cache export [OPTIONS]
```

**Arguments:**
- `output_path` (path)

### `cache import`

Import asset scores from JSON backup.

```bash
immich-memories cache import [OPTIONS]
```

**Arguments:**
- `input_path` (path)

### `cache stats`

Show cache statistics.

```bash
immich-memories cache stats [OPTIONS]
```

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

**Arguments:**
- `action` (choice)

## `days-due`

Show which discovered days have an anniversary about now.

```bash
immich-memories days-due [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--on` | datetime | - | The date to look around (default today) |
| `--catalogue` | file | special-days.json |  |

## `discover-days`

Find days something happened on, and remember them for later.

Meant to run occasionally rather than per generation: the point of a
catalogue is a memory nobody asked for — five years to the day since
the wedding — and that needs the days found in advance.

Days inside a trip are skipped, since a trip memory already tells that
story, and so are holidays, which have their own.

Resumes by default: years already in the catalogue are not scanned
again, which matters for a command that runs for hours. --rescan
starts over.

```bash
immich-memories discover-days [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--since` | integer | 2007 | First year to scan |
| `--until` | integer | 2026 | Last year to scan |
| `--per-year` | integer | 6 | Busiest candidates to ask about |
| `--also-skip` | text | - | A holiday name or MM-DD this library keeps that the defaults miss |
| `--out` | file | special-days.json | Where to write the catalogue |
| `--rescan` | boolean | false | Start over, ignoring and replacing the existing catalogue |

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

```text
Memory type presets:
  --memory-type season --season summer --year 2024
  --memory-type person_spotlight --person "Alice" --year 2024
  --memory-type multi_person --person "Alice" --person "Bob" --year 2024
  --memory-type monthly_highlights --month 7 --year 2024
  --memory-type on_this_day
```

```text
Manual time period options:
  --year 2024                    Calendar year
  --year 2024 --birthday 02/07   Birthday-based year
  --start 2024-01-01 --end 2024-06-30   Custom range
  --start 2024-01-01 --period 6m        Period from start
```

```bash
immich-memories generate [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Year to generate video for (calendar year by default) |
| `--start` | text | - | Start date (YYYY-MM-DD or DD/MM/YYYY) |
| `--end` | text | - | End date (use with --start) |
| `--period` | text | - | Period from start date (e.g., 6m, 1y, 2w) |
| `--birthday`, `-b` | text | - | Use birthday-based year (auto-detects from Immich, or specify MM/DD) |
| `--from-album` | text | - | Generate from an Immich album (name or ID) instead of a date range |
| `--person`, `-p` | text | - | Person name (repeatable) |
| `--memory-type` | choice: `year_in_review` \| `season` \| `person_spotlight` \| `multi_person` \| `monthly_highlights` \| `on_this_day` \| `trip` \| `holiday` \| `then_and_now` | - | Memory type preset |
| `--holiday` | text | - | Holiday name or MM-DD (use with --memory-type holiday) |
| `--season` | choice: `spring` \| `summer` \| `fall` \| `autumn` \| `winter` | - | Season (use with --memory-type season) |
| `--month` | integer | - | Month 1-12 (with --year, generates that month; selects trip by month) |
| `--hemisphere` | choice: `north` \| `south` | north | Hemisphere for season calculation |
| `--duration`, `-d` | integer | - | Target duration in seconds (default: from memory type preset) |
| `--short-form` | choice: `15` \| `30` \| `60` \| `90` | - | Short-form preset: sets the duration and makes the video vertical |
| `--orientation`, `-o` | choice: `landscape` \| `portrait` \| `square` | landscape | Output orientation |
| `--scale-mode`, `-s` | choice: `fit` \| `blur` | - | How to fill an aspect mismatch: blurred background or black bars (default: from config, else blur) |
| `--transition`, `-t` | choice: `smart` \| `cut` \| `crossfade` \| `none` | smart | Transition style (default: smart — mix of fades & cuts) |
| `--resolution`, `-r` | choice: `auto` \| `4k` \| `1080p` \| `720p` | - | Output resolution (default: config value, 'auto' to match source clips) |
| `--music-volume` | float | 0.5 | Music volume 0.0-1.0 (default: 0.5) |
| `--format` | choice: `mp4` \| `h265` \| `prores` | - | Output format override (default: config value) |
| `--quality`, `-q` | choice: `high` \| `medium` \| `low` | - | Output quality (default: from config, typically high) |
| `--output`, `-O` | path | - | Output file path |
| `--music`, `-m` | text | - | Music: path to audio file, 'auto' to generate from config, or omit for default behavior |
| `--no-music` | boolean | false | Disable all music (skip both provided files and AI generation) |
| `--dry-run` | boolean | false | Show what would be done without generating |
| `--trace-selection` | file | - | Write a stage-by-stage report of how the clips were chosen |
| `--upload-to-immich` | boolean | false | Upload generated video back to Immich |
| `--album` | text | - | Immich album name for uploaded video |
| `--add-date` | boolean | false | Caption each clip with its date |
| `--add-place` | boolean | false | Caption each clip with its place |
| `--keep-intermediates` | boolean | false | Keep intermediate files for debugging |
| `--privacy-mode` | boolean | false | Blur faces and mute speech |
| `--title` | text | - | Override video title text |
| `--subtitle` | text | - | Override video subtitle text |
| `--include-live-photos` | boolean | - | Include Live Photo video clips (3s iPhone clips, merged when burst-captured) |
| `--include-photos` | boolean | - | Include photos as animated Ken Burns clips (blur background, face-aware pan) |
| `--photo-duration` | float | - | Duration per photo clip in seconds (default: 4.0) |
| `--analysis-depth` | choice: `auto` \| `fast` \| `thorough` | - | Analysis depth: auto (full analysis for manageable pools), fast (favorites first), or thorough (every eligible clip) |
| `--trip-index` | integer | - | Select a specific trip by index (use with --memory-type trip) |
| `--all-trips` | boolean | false | Generate a video for every detected trip (use with --memory-type trip) |
| `--years-back` | integer | - | Years to look back for on_this_day, holiday or then_and_now |
| `--near-date` | text | - | Select trip closest to this date (YYYY-MM-DD, use with --memory-type trip) |
| `--quiet` | boolean | false | Suppress interactive progress, emit log lines |

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
| `--tempo`, `-t` | choice: `slow` \| `medium` \| `fast` | - | Tempo |
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
- LLM availability (Ollama or OpenAI-compatible)
- Semantic audio analysis (PANNs or energy fallback)
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

```text
# Delete run and its output
immich-memories runs delete 20260105_143052_a7b3
```

```text
# Delete run but keep the video
immich-memories runs delete 20260105_143052_a7b3 --keep-output
```

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

```text
# List recent runs
immich-memories runs list
```

```text
# Filter by person
immich-memories runs list --person "John"
```

```text
# Show only failed runs
immich-memories runs list --status failed
```

```bash
immich-memories runs list [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--limit`, `-n` | integer | 20 | Number of runs to show |
| `--person`, `-p` | text | - | Filter by person name |
| `--status`, `-s` | choice: `completed` \| `failed` \| `running` \| `cancelled` \| `interrupted` | - | Filter by status |

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

### `runs storage`

Report configured output and cache storage without changing it.

```bash
immich-memories runs storage [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--json` | boolean | false | Machine-readable output |

## `scheduler`

Manage scheduled automatic memory generation.

```bash
immich-memories scheduler [OPTIONS]
```

### `scheduler list`

List all configured schedules.

```bash
immich-memories scheduler list [OPTIONS]
```

### `scheduler start`

Start the scheduler daemon.

```bash
immich-memories scheduler start [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--foreground` | boolean | false | Run in foreground (don't daemonize) |

### `scheduler status`

Show scheduler status.

```bash
immich-memories scheduler status [OPTIONS]
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

```text
# Simple year title
immich-memories titles test --year 2024
```

```text
# Birthday title with person name
immich-memories titles test --birthday-age 1 --person "Emma"
```

```text
# Month divider
immich-memories titles test --month 6 --year 2024 --type month
```

```text
# Portrait orientation (for social media)
immich-memories titles test --year 2024 --orientation portrait
```

```text
# French locale with specific style
immich-memories titles test --year 2024 --locale fr --style vintage_charm
```

```bash
immich-memories titles test [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Year for title screen (e.g., 2024) |
| `--birthday-age` | integer | - | Age for birthday title (e.g., 1 for '1st Year') |
| `--person`, `-p` | text | - | Person name for subtitle |
| `--month`, `-m` | integer | - | Month for month divider (1-12) |
| `--orientation`, `-o` | choice: `landscape` \| `portrait` \| `square` | landscape | Output orientation |
| `--resolution`, `-r` | choice: `720p` \| `1080p` \| `4k` | 1080p | Output resolution |
| `--locale`, `-l` | choice: `en` \| `fr` | en | Language |
| `--style`, `-s` | choice: `modern_warm` \| `elegant_minimal` \| `vintage_charm` \| `playful_bright` \| `soft_romantic` \| `random` | random | Visual style |
| `--output`, `-O` | path | - | Output file path |
| `--type` | choice: `title` \| `month` \| `ending` | title | Screen type |
| `--download-fonts` | boolean | false | Download fonts before generating |
| `--no-animated-background` | boolean | false | Disable animated backgrounds (static gradient) |

## `ui`

Launch the interactive NiceGUI UI.

```bash
immich-memories ui [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--port`, `-p` | integer | - | Port to run the UI on (default: config or 8080) |
| `--host`, `-h` | text | - | Host to bind to (default: config or 0.0.0.0) |
| `--reload` | boolean | false | Enable hot reload (for development only) |

## `years`

List years with video content.

```bash
immich-memories years [OPTIONS]
```
