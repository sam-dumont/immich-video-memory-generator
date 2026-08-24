---
sidebar_position: 8
title: auto
---

# auto

Scans your Immich library, detects what's worth turning into a memory video, and generates it. Trips, birthdays, monthly highlights, person spotlights: it figures out what matters and picks the best one.

## How selection works

The system runs 9 detectors against your library, applies hard rotation rules, then scores the candidates that remain. The top eligible candidate gets generated.

A suggestion list can look like this:

```
 #  Type                 Period                  Score  Reason
 1  monthly_highlights   Jul 2026                0.776  683 assets, latest completed month
 2  person_spotlight     2025 (Lucas)            0.700  Completed birthday year, 16464 assets
 3  year_in_review       2025                    0.672  13151 assets, never generated
 4  multi_person         2025 (Lucas & Alex)     0.514  ~2564 shared moments
 5  trip                 Jul 26 - Aug 10 2025    0.449  16-day trip, 960 assets
 6  on_this_day          Aug 11                  0.349  Memories across 20 years
```

Monthly review is deliberately boring in one specific way: it proposes only the latest completed month. It does not dump six old reviews into the queue, and it does not fall back to an older month when the latest one is already generated or blocked by rotation.

Before scoring, automation rejects candidates that would make the output repetitive:

- The previous category cannot repeat.
- A category cannot appear more than twice in the last six completed automatic runs.
- A monthly review cannot run twice in the same calendar month.
- A person cannot reappear if they were in either of the last two person-bearing runs.

These are hard rules. If every candidate is rejected, the run is skipped. Automation does not quietly relax the rules just to produce another video.

### What happens over a week of daily runs

Say you set up `auto install` and it runs every morning at 9am:

**Monday**: July 2026 monthly highlights gets generated (score 0.776, top candidate).

**Tuesday**: Another monthly review is rejected because the category cannot repeat. Lucas's completed birthday-year spotlight is now #1.

**Wednesday**: A trip wins. The person rotation rule keeps Lucas from immediately appearing again in a spotlight or pair.

**Thursday**: Year-in-review 2025 takes over.

**Friday**: A multi-person memory wins, provided neither person was in the last two person-bearing runs.

For the rest of August, another monthly review remains blocked because one already completed that month. In September, the detector can propose August: exactly one current monthly candidate, not a backlog of increasingly stale reviews.

The exact order depends on your library. The guarantees are simpler: one generation subprocess at most, no category back-to-back, and no pile of monthly-review junk.

### Birthday timing

Birthdays get special treatment. Two rules make sure the timing is right:

1. **Sync buffer**: the detector only fires 2+ days after the birthday. Photos from the birthday party need time to sync to Immich before we pull clips.

2. **Lookahead suppression**: if someone's birthday is within the next 7 days, the PersonSpotlightDetector skips them entirely. This prevents generating a generic "most featured person" video for someone whose birthday video would be much better timed a few days later.

### Trip detection

Trips are detected from GPS data in the trailing year, including completed current-year trips and trips that cross New Year. A trip is a cluster of photos 50+ km from your homebase, spanning 2+ days, with no gap larger than 2 days. The detector only fires 7+ days after returning home (same sync buffer logic as birthdays).

You need homebase coordinates in your config:

```yaml
trips:
  homebase_latitude: 48.8566    # your home coordinates
  homebase_longitude: 2.3522
```

### Multi-person pairs

The system takes your top 10 people by asset count and generates all 45 possible pairs. For each pair, it estimates shared content as 30% of the smaller count (a rough co-occurrence proxy). Pairs with fewer than 50 estimated shared assets get filtered out.

Real example: if Person A has 16,464 assets and Person B has 8,549, the estimated shared content is `min(16464, 8549) * 0.3 = 2,564`. That's enough for a "together through the years" video.

## auto suggest

```bash
immich-memories auto suggest [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json` | flag | `false` | Machine-readable JSON output |
| `--limit` | int | `10` | Max candidates to show |
| `--type` | string | all | Filter by memory type |

Connects to Immich, fetches library stats + people + GPS assets, runs all detectors, scores and ranks. Takes about 30 seconds (GPS fetch for trip detection is the slow part).

### Uploads that keep failing

A pending Immich upload is retried before anything else on each wake, and that
retry ends the invocation. An upload that can never succeed — an API key without
upload scope, an album that no longer accepts writes — would therefore consume
every night and generate nothing.

After `max_delivery_attempts` failures (default 5) the upload is abandoned: the
run is marked `abandoned` rather than `pending`, a notification is sent with the
original error, and the next wake goes back to making memories. The video itself
is untouched and still on disk — only its delivery gave up.

### Candidates that keep failing

A candidate that fails twice in a row is held back for a while instead of being
proposed again the next night, so one memory that cannot render stops consuming
every nightly run. The wait grows with the streak — 24 hours after two failures,
3 days after three, capped at 7 days — and always expires, so a memory broken by
something temporary (a server that was down, an asset that gets re-uploaded)
comes back on its own.

A single failure never counts against a candidate, and a successful run clears
the streak immediately.

`auto suggest` says when this is happening, so a held-back candidate is not
mistaken for an empty library:

```
Backing off monthly_highlights:2026-06 — failed 3x, retrying after 3d
```

### Detectors

Each detector has a base score and scales it by how strong the case is. The ranges
below are derived from those formulas, so the ceiling is what a perfect case gets and
the floor is what the detector's own admission rules allow through.

| Detector | What it finds | Score | Scaled by |
|----------|---------------|-------|-----------|
| **YearlyDetector** | Past years with content (only after Jan 15) | 0.24-0.72 | recency, 10% per year, floored at 0.3 |
| **BirthdayDetector** | People whose birthday was 2-60 days ago | 0.75 | nothing — fixed |
| **MonthlyDetector** | Latest completed month, if not already generated | 0.21-0.7 | recency, 10% per month back, floored at 0.3 |
| **ActivityBurstDetector** | Months with >2x the rolling average (last 12 months) | 0.7 | nothing — a month that clears the threshold gets the full score |
| **TripDetector** | GPS-detected trips from the past year | up to 0.75 | trip length up to 14 days × asset count up to 200 |
| **PersonSpotlightDetector** | Top 5 people by asset count | 0.12-0.6 | that person's share of the top person's asset count, floored at 0.2 |
| **MultiPersonDetector** | Pairs who appear together frequently | 0.06-0.55 | estimated shared assets up to 500 (50 minimum to qualify) |
| **OnThisDayDetector** | Dates with content across 5+ years | 0.18-0.35 | how many years the date has content in, up to 10 |
| **SpecialDayDetector** | Catalogued days whose anniversary is within 3 days | 0.48-0.8 | roundness of the anniversary (decade / half-decade / other) |

### The anniversary that would otherwise score lowest

`SpecialDayDetector` reads the catalogue `discover-days` writes and proposes a day when its anniversary comes round. Base score 0.8, multiplied by how round the anniversary is: 1.0 for a decade, 0.85 for a half-decade, 0.6 for anything else.

The scoring adjustments then land it where the ladder wants it. For a 200-photo day, never generated, with no same-type cooldown: never-generated ×1.2, recency ×1.0 (the anniversary is within 3 days by construction), richness ×(0.7 + 0.3 × log(200)/log(1000)) = ×0.930.

| Anniversary | Roundness | Raw | Final |
|---|---|---|---|
| 10, 20, 30 years | 1.00 | 0.80 | **0.893** (`0.80 × 1.2 × 1.0 × 0.930`) |
| 5, 15, 25 years | 0.85 | 0.68 | **0.759** (`0.68 × 1.2 × 1.0 × 0.930`) |
| anything else | 0.60 | 0.48 | **0.536** (`0.48 × 1.2 × 1.0 × 0.930`) |

Against the rest of the ladder — monthly 0.776, birthday 0.700, yearly 0.672, multi-person 0.514, trip 0.449, on-this-day 0.349 — a decade goes first, a half-decade sits between monthly and birthday, and a seventh anniversary competes rather than pre-empts.

Recency is the reason this detector needs a rule of its own. The scorer decays a candidate from the end of what it covers, so a ten-year-old day would take the 0.5 floor: the memory most worth arriving would be punished hardest by a rule meant to prefer fresh content. `OnThisDayDetector` avoids that by reporting today as its date range and putting the real years in its reason, which makes `auto suggest` print a period the memory does not cover. A special day instead reports its real date and tells the scorer separately when it is timely, so what you see in the table is what gets generated.

One per run, and the title never travels: automation passes `--day 2016-06-12` and the generate command re-reads the catalogue for the name, because argv is logged in full and readable in `ps`.

### Scoring adjustments

After detectors assign raw scores, the scorer applies:

- **Hard rotation first**: no consecutive category, max 2 of the last 6, monthly cadence, and recent-person rotation
- **Never-generated boost**: 1.2x for memories that don't exist yet
- **Recency**: recent content scores higher (linear decay over 365 days, floor 0.5x)
- **Content richness**: more assets = higher score (log scale)
- **Same-type cooldown**: 0.3x for 7 days, 0.7x for 30 days after generating the same type
- **Per-type caps**: max 3 per type, except on_this_day (1), special_day (1), and multi_person (2)
- **Dedup by memory key**: if two detectors propose the same memory, the higher-scoring one wins

## auto run

```bash
immich-memories auto run [OPTIONS]
```

Runs one daily decision. It retries the oldest retryable pending delivery first; if there is none,
it picks the #1 candidate from `suggest` and generates it. That generation is one memory per
invocation. Exactly one action per invocation, then it exits.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dry-run` | flag | `false` | Show what would be generated, don't do it |
| `--force` | flag | `false` | Skip cooldown check |
| `--cooldown` | int | config `automation.cooldown_hours` (24) | Min hours since the last auto-run *started* (30 min tolerance, so a daily timer with `24` fires every day) |
| `--upload` | flag | `false` | Upload result to Immich |
| `--quiet` | flag | `false` | Emit exactly one JSON result object on stdout |

The typed terminal outcomes are `skipped`, `dry_run`, `completed`, and `failed`. `skipped`,
`dry_run`, and `completed` exit 0; `failed` exits 1. Quiet output is a stable JSON object, not a
bare path. Its `action` is `generation` or `delivery_retry` when work was selected:

`error` carries the cause of a failure and is always present, so a wrapper
script never has to tell "no error" from "field missing".

```json
{
  "outcome": "dry_run",
  "action": "generation",
  "reason": "dry run",
  "candidate_key": "trip:2026-07-02:2026-07-09:",
  "category": "trip",
  "run_id": null,
  "error": null,
  "output_path": null,
  "recent_categories": ["monthly_review", "birthday"],
  "rejections": [
    {
      "category": "person_spotlight",
      "memory_key": "person_spotlight:2025-01-01:2025-12-31:lucas",
      "rule": "person_in_last_two_person_runs"
    }
  ]
}
```

When every candidate is rejected, `outcome` is `skipped`, `category` is `null`, and `rejections` explains why. Human `--dry-run` output prints the same rotation and rejection details.

If upload delivery failed after generation, the durable output stays pending. The next `auto run`
attempts the oldest retryable pending delivery before it considers a fresh candidate. A failed retry
is still a `failed` result; it does not quietly begin another generation.

## auto install

```bash
immich-memories auto install [OPTIONS]
```

Sets up your OS scheduler. Detects the platform and generates the right config file.

Running in Docker (or wanting the web UI process to do it)? Skip this command and set
`automation.enabled: true` + `automation.daily_at` instead — the UI process then runs `auto run`
once a day itself. See [automated generation](../recipes/automated-generation.md#docker-and-the-web-ui-built-in-daily-timer).

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--hour` | int | `9` | Hour to run (0-23) |
| `--minute` | int | `0` | Minute to run (0-59) |
| `--cooldown` | int | `24` | Cooldown hours between runs |
| `--uninstall` | flag | `false` | Remove installed scheduler |
| `--show` | flag | `false` | Print config without installing |
| `--force` | flag | `false` | Install even when the checkout is a worktree or behind its upstream |

| Platform | What gets created | How to activate |
|----------|-------------------|-----------------|
| **macOS** | launcher in `~/.immich-memories/bin/` + `~/Library/LaunchAgents/com.immich-memories.auto.plist` | `launchctl load <path>` |
| **Linux** | launcher in `~/.immich-memories/bin/` + systemd user service and timer in `~/.config/systemd/user/` | `systemctl --user enable --now immich-memories-auto.timer` |
| **Other** | launcher in `~/.immich-memories/bin/` + prints a crontab entry | `crontab -e` |

On macOS the plist uses `StartCalendarInterval`; launchd runs a missed job the next time the Mac wakes rather than waking it from sleep. If the Mac is asleep at the scheduled time, expect the run once it wakes.

### Which binary gets scheduled

The OS never re-resolves the command it was given, so `auto install` does not hand it a path that
can go stale. It writes a small launcher at `~/.immich-memories/bin/immich-memories-auto` and
schedules *that*. The launcher re-runs the `immich-memories` lookup on every fire, with the
directory you installed from first on its `PATH`. Upgrade or reinstall the package in place and the
nightly job picks it up; no reinstall of the scheduler is needed.

`auto install --uninstall` removes the launcher along with the plist or unit files.

### Refusing to schedule stale code

Nothing updates a checkout on your behalf, and a scheduled job re-runs whatever that checkout holds
every night while the logs look completely normal. `auto install` refuses two cases:

- **A linked git worktree** (`git worktree add`) — its root holds a `.git` *file* rather than a
  directory. A worktree stays frozen on the commit it was left at, or gets pruned.
- **A checkout already behind its tracking branch** — measured with `git rev-list HEAD..@{upstream}`
  against refs you have already fetched. `auto install` never fetches, so this only sees drift your
  own `git pull` or `git fetch` recorded.

Both name the offending path and the drift. Update the checkout and re-run, or pass `--force` to
schedule it as it is.

Every `auto run` also states which code it is executing, and `auto status` shows the same thing —
see [auto status](#auto-status).

### What environment the scheduled job sees

A scheduled job does **not** inherit your interactive shell. launchd and cron start it from a
login-less environment, so anything you `export` in `.zshrc` is absent at 03:00 — which is how a
scheduled ACE-Step render ends up tuned differently from the one you tested by hand.

`auto install` captures these variables from the shell you install from and writes them into the
plist or unit alongside `PATH`:

| Variable | Why it is carried |
|----------|-------------------|
| `PATH` | So FFmpeg and the launcher's own lookup work |
| `ACESTEP_CHECKPOINTS_DIR` | Model cache location |
| `ACESTEP_MLX_VAE_CHUNK` | ACE-Step MLX VAE decode chunk |
| `IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32` | ACE-Step MLX decoder precision |
| `PYTORCH_MPS_HIGH_WATERMARK_RATIO` | torch MPS allocator ceiling |

Nothing else is copied. `IMMICH_MEMORIES_*` also holds the Immich API key and the UI password, and
a plist in `~/Library` is not a secret store — put credentials in your config file or a `.env` the
app reads, not in the scheduler. Change any of these and re-run `auto install` to update the job.

Crontab entries carry no environment; set what you need in the crontab itself.

### Installing with a custom config

`--config` is a root option, so it goes before `auto`:

```bash
# Inspect the scheduler definition first
immich-memories --config "/srv/Immich Memories/family.yaml" auto install --show

# Install it
immich-memories --config "/srv/Immich Memories/family.yaml" auto install --hour 9

# Run the same configuration manually
immich-memories --config "/srv/Immich Memories/family.yaml" auto run --dry-run
```

The generated launchd, systemd, or crontab command retains the resolved config path. Spaces and platform-specific special characters are encoded by the installer. Without `--config`, the normal default config behavior is unchanged.

## auto history

```bash
immich-memories auto history [--limit N]
```

Shows recent auto-generated memories: date, type, date range, output file.

## auto status

```bash
immich-memories auto status [--json]
```

Shows which code is running, the external scheduler state, last automation attempt, last completed automatic run, cooldown, the last six categories, current variety rejection rules, and the live suggestion status. It is diagnostic: it does not generate a video or install, load, unload, or rewrite a scheduler.

Use `--json` for the full machine-readable object. If Immich discovery is temporarily unavailable, durable attempt and run history still appears and `suggestion.outcome` explains the discovery failure.

### Knowing which code is running

The `Running code:` line names the version, the short commit, the checkout it came from, and how
many commits that checkout is behind its tracking branch. When it is behind, the line is printed as
an error rather than as info.

The same object is in `--json` under `runtime`:

```json
{
  "runtime": {
    "version": "0.54.0",
    "checkout": "/home/me/immich-video-memory-generator",
    "commit": "08e4cd3",
    "upstream": "origin/main",
    "commits_behind": 32,
    "stale": true
  }
}
```

`auto run` reports the same object. Under `--quiet` it is the `runtime` key of the single JSON line
it prints, so a scheduled run records which code produced it in `auto.log`; if the checkout is
behind, `auto run` additionally writes a `warning: scheduled code is stale` line to stderr, which
`--quiet` cannot suppress. `commit`, `upstream`, and `commits_behind` are `null` for an install that
is not a git checkout (pip, pipx, Docker); `version` is always present.

## auto test-notification

```bash
immich-memories auto test-notification
```

Sends a test notification through your Apprise URLs. Requires `notifications.enabled: true` and at least one URL configured.

## Configuration

Under `advanced:` in `config.yaml`:

```yaml
advanced:
  automation:
    cooldown_hours: 24              # min hours between auto-run starts (daily timer + 24 = once a day)
    max_delivery_attempts: 5        # give up on an upload after this many failures
    upload_to_immich: false         # auto-upload generated videos
    album_name: null                # album for uploads
    detect_monthly: true
    detect_yearly: true
    detect_trips: true              # needs trips.homebase_latitude/longitude
    detect_person_spotlight: true
    detect_activity_burst: true
    burst_threshold: 2.0            # how many x above average triggers a burst

  notifications:
    enabled: false
    urls: []                        # ntfy://ntfy.sh/my-topic, discord:///id/token, etc.
    on_success: true
    on_failure: true
    attach_thumbnail: false         # opt in to FFmpeg extraction + attachment upload
    cooldown_hours: 24              # pause after provider/auth/quota failures
```

Notification health is durable and visible in `auto status`, `preflight`, and `/health`.
A failed delivery pauses normal notification attempts for the configured cooldown; it
does not stop memory generation or make `/health/ready` fail. The explicit
`auto test-notification` command bypasses the cooldown so you can verify a fix.
