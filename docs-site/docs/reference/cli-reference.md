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

Automation -- detect and generate memory candidates.

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
| `--force` | boolean | false | Schedule this install even when its checkout is a worktree or behind its upstream |

### `auto run`

Generate the chosen eligible candidate, or the highest-scoring one.

```bash
immich-memories auto run [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--candidate` | text | - | Exact memory_key from auto suggest --json |
| `--dry-run` | boolean | false | Show what would be generated |
| `--force` | boolean | false | Skip cooldown check |
| `--cooldown` | integer | - | Min hours since last auto-run |
| `--upload` | boolean | false | Upload to Immich |
| `--quiet` | boolean | false | One machine-readable line per decision, no progress display; -v adds log detail |

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
| `--catalogue` | file | ~/.immich-memories/special-days.json |  |

## `discover-days`

Find days something happened on, and remember them for later.

Meant to run occasionally rather than per generation: the point of a
catalogue is a memory nobody asked for (five years to the day since
the wedding), and that needs the days found in advance.

Days inside a trip are skipped, since a trip memory already tells that
story, and so are holidays, which have their own. With no model
configured, a day counts when one recorded fact stands out (away from
home, three favourites, mostly video, or a long day with close family)
and each year keeps its strongest few. With a model, every other day
is read a month at a time and the model says which were occasions.

Resumes by default: years already in the catalogue are not scanned
again, which matters for a command that runs for hours. --rescan
starts over.

A catalogue that accumulated over several releases holds rows judged by
questions this build no longer asks. --replace re-scans the years
between --since and --until and replaces what they hold, so a period
can be cleaned without editing JSON by hand. It says how many rows it
will replace before it starts, and it never touches a year outside the
period.

```bash
immich-memories discover-days [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--since` | integer | 2007 | First year to scan |
| `--until` | integer | 2026 | Last year to scan |
| `--also-skip` | text | - | A holiday name or MM-DD this library keeps that the defaults miss |
| `--out` | file | ~/.immich-memories/special-days.json | Where to write the catalogue |
| `--rescan` | boolean | false | Start over, ignoring and replacing the existing catalogue |
| `--replace` | boolean | false | Re-scan --since..--until and replace every row those years already hold, dropping days that no longer qualify. Rows outside the period are kept. |

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
  --memory-type person_spotlight --person "Riley" --year 2024
  --memory-type multi_person --person "Riley" --person "Bob" --year 2024
  --memory-type multi_person --person "Riley" --person "Bob"   (no dates: from
      the first day both could be in a picture, read off their birth dates)
  --memory-type monthly_highlights --month 7 --year 2024
  --memory-type on_this_day
```

```text
Manual time period options:
  --year 2024                    Calendar year
  --year 2024 --birthday 02-07   Birthday-based year
  --start 2024-01-01 --end 2024-06-30   Custom range
  --start 2024-01-01 --period 6m        Period from start
```

```bash
immich-memories generate [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Year to generate video for (calendar year by default) |
| `--start` | text | - | Start date (YYYY-MM-DD) |
| `--end` | text | - | End date (use with --start) |
| `--period` | text | - | Period from start date (e.g., 6m, 1y, 2w) |
| `--birthday`, `-b` | text | - | Run the year up to a birthday, plus earlier birthdays (reads Immich's birth date, or override with MM-DD, e.g. 03-15) |
| `--from-album` | text | - | Generate from an Immich album (name or ID) instead of a date range |
| `--person`, `-p` | text | - | Person name (repeatable) |
| `--people-expression` | text | - | Grouped people condition, e.g. ("Person A" OR "Person B") AND "Person C". Use exact library names; each asset must match. |
| `--person-match` | choice: `and` \| `or` | and | With several --person values, require everyone in each asset (and) or accept any named person (or) |
| `--memory-type` | choice: `year_in_review` \| `season` \| `person_spotlight` \| `multi_person` \| `monthly_highlights` \| `on_this_day` \| `album` \| `trip` \| `holiday` \| `special_day` | - | Memory type preset (album takes its pool from --from-album) |
| `--holiday` | text | - | Holiday name or MM-DD (use with --memory-type holiday) |
| `--season` | choice: `spring` \| `summer` \| `fall` \| `autumn` \| `winter` | - | Season (use with --memory-type season) |
| `--month` | integer | - | Month 1-12 (with --year, generates that month; selects trip by month) |
| `--hemisphere` | choice: `north` \| `south` | north | Hemisphere for season calculation |
| `--duration`, `-d` | integer | - | Target duration in seconds (default: fitted to the material the period holds) |
| `--short-form` | choice: `15` \| `30` \| `60` \| `90` | - | Short-form preset: sets the duration and makes the video vertical |
| `--orientation` | choice: `landscape` \| `portrait` \| `square` \| `auto` | auto | Output orientation (auto follows the final selected cut) |
| `--scale-mode`, `-s` | choice: `fit` \| `blur` | - | How to fill an aspect mismatch: blurred background or black bars (default: from config, else blur) |
| `--transition`, `-t` | choice: `smart` \| `cut` \| `crossfade` \| `none` | smart | Transition style (default: smart, a mix of fades and cuts) |
| `--resolution`, `-r` | choice: `auto` \| `4k` \| `1080p` \| `720p` | - | Output resolution (default: config value, 'auto' to match source clips) |
| `--music-volume` | float | 0.5 | Music volume 0.0-1.0 (default: 0.5) |
| `--format` | choice: `mp4` \| `h265` \| `prores` | - | Output format override (default: config value) |
| `--quality`, `-q` | choice: `high` \| `medium` \| `low` | - | Output quality (default: from config, typically high) |
| `--output`, `-o`, `-O` | path | - | Output file path. The run writes it inside its own directory and adds a recipe hash to the name, so an identical rerun replaces itself |
| `--music`, `-m` | text | - | Music: path to audio file, 'auto' to generate from config, or omit for default behavior |
| `--no-music` | boolean | false | Disable all music (skip both provided files and AI generation) |
| `--dry-run` | boolean | false | Discover inputs and show preparation needs without selection or generation |
| `--no-render` | boolean | false | Run story-first selection and its audience and media checks, then stop before encoding. Unlike --dry-run, this picks the clips it would actually ship |
| `--trace-selection` | file | - | Write a stage-by-stage report of how the clips were chosen |
| `--include` | text | - | Keep this picture in the cut even if the editor would drop it (repeatable) |
| `--exclude` | text | - | Leave this picture out of the cut (repeatable) |
| `--upload-to-immich` | boolean | false | Upload generated video back to Immich |
| `--album` | text | - | Immich album name for uploaded video |
| `--add-date` | boolean | false | Caption each clip with its date |
| `--add-place` | boolean | false | Caption each clip with its place |
| `--keep-intermediates` | boolean | false | Keep intermediate files for debugging |
| `--privacy-mode` | boolean | false | Demo mode: blur every clip frame, scramble the audio, fake the person names |
| `--title` | text | - | Override video title text |
| `--llm-title` | boolean | - | People and occasion memories are named by the model whenever a reader is configured. --llm-title adds trips, --no-llm-title pins the template everywhere (--title still wins) |
| `--subtitle` | text | - | Override video subtitle text |
| `--include-live-photos` | boolean | - | Include Live Photo video clips (3s iPhone clips, merged when burst-captured) |
| `--include-photos` | boolean | - | Include photos as animated Ken Burns clips (blur background, face-aware pan) |
| `--accept-any-provenance` | boolean | false | Keep forwarded and re-encoded media for this memory; date, person, privacy, and Live Photo boundaries still apply |
| `--photo-duration` | float | - | Duration per photo clip in seconds (default: 4.0) |
| `--trip-index` | integer | - | Select a specific trip by index (use with --memory-type trip) |
| `--all-trips` | boolean | false | Generate a video for every detected trip (use with --memory-type trip) |
| `--years-back` | integer | - | Years to look back for --birthday, on_this_day or holiday |
| `--near-date` | text | - | Select trip closest to this date (YYYY-MM-DD, use with --memory-type trip) |
| `--event-id` | text | - | Exact catalogue event ID (use with --memory-type special_day and --day) |
| `--day` | datetime | - | The day this memory is about (YYYY-MM-DD). With --memory-type special_day it names a catalogued day, whose title comes from the catalogue rather than from here (`immich-memories days-due` lists them). With --memory-type on_this_day it is the anniversary to look back from, so the cut is reproducible; without it, today |
| `--quiet` | boolean | false | Silence the live progress display and print log lines instead (cron, logs); -v sets the log level |

## `hardware`

Show hardware acceleration information.

```bash
immich-memories hardware [OPTIONS]
```

## `models`

Fetch the pinned model artifacts selection needs.

```bash
immich-memories models [OPTIONS]
```

### `models fetch`

Download every pinned model artifact a first cut needs, in one command.

```bash
immich-memories models fetch [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--force` | boolean | false | Re-download even when the file is already right |
| `--detectors` | boolean | true | Also fetch the pinned detector export and warm the pinned detector snapshot |
| `--laya` | boolean | false | Also fetch the optional Laya audience checkpoint (811 MB, Apple silicon) |

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
| `--analyze-frames` | boolean | false | Send video frames to the configured LLM for mood when --mood is absent |

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

Who is in this library, and who they are to each other.

Called on its own this still lists the people Immich knows, which is
what `immich-memories people` has always done.

```bash
immich-memories people [OPTIONS]
```

### `people scan`

Build or refresh the people file from Immich.

Reads every named person's count and month curve, then asks about each
remaining pair to find who appears with whom. Nothing here looks at a
pixel and nothing here asks you a question: the library's own
distribution is the whole input.

Safe to re-run: everything under `confirmed:` in the file is copied
through untouched, and preferred to this pass's reading forever after.

```bash
immich-memories people scan [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--min-assets` | integer | 25 | Pictures a named person needs before the graph has an opinion |
| `--owner` | text | - | The name of the person whose library this is, if the account does not say |
| `--out` | file | - | Where to write the people file |

### `people show`

Print what the last scan wrote down.

```bash
immich-memories people show [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--file` | file | - | The people file to read |
| `--tier` | choice: `inner` \| `recurring` \| `episodic` \| `event` | - | Show only one tier |

## `pictures`

Your own word on a picture: clear its hold, or never use it.

Every tier reads it, in every later cut. The asset id is the one `runs why`,
`runs story` and Immich show.

```bash
immich-memories pictures [OPTIONS]
```

### `pictures clear-hold`

Clear this one picture's hold, after you've looked at it yourself.

```bash
immich-memories pictures clear-hold [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--yes` | boolean | false | Clear it without asking |

**Arguments:**
- `asset_id` (text)

### `pictures list`

Every picture you cleared or will never use.

```bash
immich-memories pictures list [OPTIONS]
```

### `pictures never-use`

Keep this picture out of every film from now on.

```bash
immich-memories pictures never-use [OPTIONS]
```

**Arguments:**
- `asset_id` (text)

### `pictures show`

What holds this picture, and what you decided.

```bash
immich-memories pictures show [OPTIONS]
```

**Arguments:**
- `asset_id` (text)

### `pictures undo`

Forget what you decided about this picture: the app's own holds apply again.

```bash
immich-memories pictures undo [OPTIONS]
```

**Arguments:**
- `asset_id` (text)

## `preflight`

Run preflight checks to validate all provider connections.

Checks:
- Immich server connection and API key
- LLM availability (Ollama or OpenAI-compatible)
- Title rendering (GPU or PIL fallback)
- Pinned DINOv2 encoder export (presence and digest)
- Caption endpoint (advertises the accepted alias)
- Configured paths that are not on this host
- Notification delivery health
- Hardware acceleration

```bash
immich-memories preflight [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--verbose`, `-v` | boolean | false | Show detailed output |

## `prepare`

Prepare a scope's annotations, print what each producer cost, and stop.

```text
No selection and no render happen. Preparation is banked per picture, so
a scope prepared today is free for every later cut:
  immich-memories prepare --year 2024 --month 6
  immich-memories prepare --start 2024-01-01 --period 1y

--overviews goes one step further and banks what each month was about,
which a cut of that month then reads instead of working it out again:
  immich-memories prepare --year 2024 --month 6 --overviews
```

```bash
immich-memories prepare [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--year`, `-y` | integer | - | Calendar year to prepare |
| `--month` | integer | - | Month 1-12, with --year: one month at a time |
| `--start` | text | - | Start date (YYYY-MM-DD) |
| `--end` | text | - | End date (use with --start) |
| `--period` | text | - | Period from the start date (e.g. 6m, 1y, 2w) |
| `--overviews` | boolean | false | Also bank each month's episode readings and the account a cut reads as its thesis |
| `--library-size` | integer | 1000 | Project the measured rate onto a library of this many pictures |

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

### `runs story`

Print the cut of a run in the order it plays: day, kind, length, story, reason.

With no RUN_ID the most recent completed run is read. A run id prefix
works, and so does the path of an attempt directory.

```bash
immich-memories runs story [OPTIONS]
```

**Arguments:**
- `run_id` (text)

### `runs why`

Say what a run decided about one picture: where it passed, where it was dropped, and why.

```bash
immich-memories runs why [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--run` | text | - | Run id or prefix (default: latest) |

**Arguments:**
- `asset_id` (text)

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

Five OFL-1.1 families and Noto Sans (Latin, Greek, Cyrillic, Vietnamese)
ship inside the wheel. `--install` adds the Noto faces for every other
script a title can hold (Arabic, Hebrew, Indic, Thai, CJK and more) from
raw.githubusercontent.com, each file checked against a pinned SHA-256.
It is the only step that downloads a font; a render never does. The
Docker image runs it at build time.

```bash
immich-memories titles fonts [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--install` | boolean | false | Download the pinned Noto script fonts (about 43 MB, most of it CJK) |
| `--clear` | boolean | false | Clear ~/.immich-memories/fonts |
| `--list` | boolean | false | List title fonts (the default) |

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
| `--orientation` | choice: `landscape` \| `portrait` \| `square` | landscape | Output orientation |
| `--resolution`, `-r` | choice: `720p` \| `1080p` \| `4k` | 1080p | Output resolution |
| `--locale`, `-l` | choice: `en` \| `fr` \| `nl` \| `de` \| `es` \| `it` \| `pt-BR` \| `pt-PT` \| `pl` \| `sv` \| `ru` \| `ja` \| `zh-Hans` \| `ko` | en | Language |
| `--style`, `-s` | choice: `modern_warm` \| `elegant_minimal` \| `vintage_charm` \| `playful_bright` \| `soft_romantic` \| `random` | random | Visual style |
| `--output`, `-o`, `-O` | path | - | Output file path |
| `--type` | choice: `title` \| `month` \| `ending` | title | Screen type |
| `--no-animated-background` | boolean | false | Disable animated backgrounds (static gradient) |

## `ui`

Launch the interactive NiceGUI UI.

```bash
immich-memories ui [OPTIONS]
```

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--port`, `-p` | integer | - | Port to run the UI on (default: config or 8080) |
| `--host`, `-h` | text | - | Host to bind to (default: config or 127.0.0.1) |
| `--reload` | boolean | false | Enable hot reload (for development only) |

## `years`

List years with video content.

```bash
immich-memories years [OPTIONS]
```
