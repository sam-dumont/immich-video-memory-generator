---
sidebar_position: 8
title: auto
---

# auto

Scans your Immich library, detects what's worth turning into a memory video, and generates it. Trips, birthdays, monthly highlights, person spotlights: it figures out what matters and picks the best one.

## How selection works

The system runs 8 detectors against your library, applies hard rotation rules, then scores the candidates that remain. The top eligible candidate gets generated.

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

### Detectors

| Detector | What it finds | Score range |
|----------|---------------|-------------|
| **MonthlyDetector** | Latest completed month, if not already generated | 0.5-0.8 |
| **YearlyDetector** | Past years with content (only after Jan 15) | 0.5-0.7 |
| **PersonSpotlightDetector** | Top 5 people by asset count | 0.1-0.6 |
| **BirthdayDetector** | People whose birthday was 2-60 days ago | 0.75 |
| **TripDetector** | GPS-detected trips from the past year | 0.1-0.5 |
| **ActivityBurstDetector** | Months with >2x the rolling average (last 12 months) | 0.4-0.7 |
| **OnThisDayDetector** | Dates with content across 5+ years | 0.2-0.35 |
| **MultiPersonDetector** | Pairs who appear together frequently | 0.3-0.55 |

### Scoring adjustments

After detectors assign raw scores, the scorer applies:

- **Hard rotation first**: no consecutive category, max 2 of the last 6, monthly cadence, and recent-person rotation
- **Never-generated boost**: 1.2x for memories that don't exist yet
- **Recency**: recent content scores higher (linear decay over 365 days, floor 0.5x)
- **Content richness**: more assets = higher score (log scale)
- **Same-type cooldown**: 0.3x for 7 days, 0.7x for 30 days after generating the same type
- **Per-type caps**: max 3 per type, except on_this_day (1) and multi_person (2)
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
| `--cooldown` | int | `24` | Min hours since last auto-run |
| `--upload` | flag | `false` | Upload result to Immich |
| `--quiet` | flag | `false` | Emit exactly one JSON result object on stdout |

The typed terminal outcomes are `skipped`, `dry_run`, `completed`, and `failed`. `skipped`,
`dry_run`, and `completed` exit 0; `failed` exits 1. Quiet output is a stable JSON object, not a
bare path. Its `action` is `generation` or `delivery_retry` when work was selected:

```json
{
  "outcome": "dry_run",
  "action": "generation",
  "reason": "dry run",
  "candidate_key": "trip:2026-07-02:2026-07-09:",
  "category": "trip",
  "run_id": null,
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

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--hour` | int | `9` | Hour to run (0-23) |
| `--minute` | int | `0` | Minute to run (0-59) |
| `--cooldown` | int | `24` | Cooldown hours between runs |
| `--uninstall` | flag | `false` | Remove installed scheduler |
| `--show` | flag | `false` | Print config without installing |

| Platform | What gets created | How to activate |
|----------|-------------------|-----------------|
| **macOS** | `~/Library/LaunchAgents/com.immich-memories.auto.plist` | `launchctl load <path>` |
| **Linux** | systemd user service + timer in `~/.config/systemd/user/` | `systemctl --user enable --now immich-memories-auto.timer` |
| **Other** | Prints a crontab entry | `crontab -e` |

On macOS, launchd wakes the machine from sleep, runs the command, and goes back to sleep.

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

Shows the external scheduler state, last automation attempt, last completed automatic run, cooldown, the last six categories, current variety rejection rules, and the live suggestion status. It is diagnostic: it does not generate a video or install, load, unload, or rewrite a scheduler.

Use `--json` for the full machine-readable object. If Immich discovery is temporarily unavailable, durable attempt and run history still appears and `suggestion.outcome` explains the discovery failure.

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
    cooldown_hours: 24              # min hours between auto-generated memories
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
```
