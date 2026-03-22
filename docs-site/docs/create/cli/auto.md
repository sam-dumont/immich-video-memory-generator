---
sidebar_position: 8
title: auto
---

# auto

Smart automation that figures out what memory to generate next. Scans your Immich library, detects trips, birthdays, activity bursts, and ranks everything by priority. Run it once, it picks the best candidate and generates it.

## auto suggest

```bash
immich-memories auto suggest [OPTIONS]
```

Shows what the system thinks you should generate, ranked by score.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json` | flag | `false` | Machine-readable JSON output |
| `--limit` | int | `10` | Max candidates to show |
| `--type` | string | all | Filter by memory type |

The system connects to Immich, fetches your library stats and people, runs 7 detectors, scores everything, and shows a ranked table. Takes about 30 seconds (most of it is fetching GPS data for trip detection).

### What gets detected

| Detector | What it finds | Score range |
|----------|---------------|-------------|
| **MonthlyDetector** | Last 6 un-generated months | 0.5-0.8 |
| **YearlyDetector** | Past years with content (only after Jan 15) | 0.5-0.7 |
| **PersonSpotlightDetector** | Top 5 people by asset count | 0.1-0.6 |
| **BirthdayDetector** | People whose birthday was 2-60 days ago | 0.75 |
| **TripDetector** | GPS-detected trips from the past year | 0.1-0.5 |
| **ActivityBurstDetector** | Months with >2x the rolling average (last 12 months) | 0.4-0.7 |
| **OnThisDayDetector** | Dates with content across 5+ years | 0.2-0.35 |
| **MultiPersonDetector** | Pairs of people who appear together frequently | 0.3-0.55 |

### Scoring and balancing

Each detector assigns a raw score. The scorer then applies:

- **Never-generated boost**: memories that haven't been created yet score 1.2x higher
- **Recency**: recent content scores higher than old content (linear decay over 365 days)
- **Content richness**: more assets = higher score (log scale, so 10K assets isn't 10x better than 1K)
- **Same-type cooldown**: generated a monthly_highlights yesterday? All other monthlies get penalized for a week
- **Per-type caps**: max 3 per type (1 for on_this_day, 2 for multi_person) so the list stays diverse
- **Dedup**: if both BirthdayDetector and PersonSpotlightDetector propose the same person, the higher-scoring one wins

### Birthday lookahead

If someone's birthday is coming up in the next 7 days, the PersonSpotlightDetector skips them. This way, the BirthdayDetector fires at the right time (2+ days after the birthday, once photos have synced) instead of wasting a generic spotlight early.

## auto run

```bash
immich-memories auto run [OPTIONS]
```

Picks the #1 candidate from `suggest` and generates it. One memory per invocation, then exits. This is what launchd/systemd/cron calls.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dry-run` | flag | `false` | Show what would be generated, don't do it |
| `--force` | flag | `false` | Skip cooldown check |
| `--cooldown` | int | `24` | Min hours since last auto-run |
| `--upload` | flag | `false` | Upload result to Immich |
| `--quiet` | flag | `false` | Machine-friendly output (just the path) |

With daily runs, the balancing works like this: Day 1 generates a monthly_highlights, Day 2 the same-type cooldown pushes monthly down so a birthday or trip takes over, Day 3 another type surfaces. After 7 days the monthly cooldown lifts but the previously generated one is deduped, so the next monthly appears.

## auto install

```bash
immich-memories auto install [OPTIONS]
```

Sets up your OS scheduler so `auto run` fires automatically. Detects your platform and generates the right config.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--hour` | int | `9` | Hour to run (0-23) |
| `--minute` | int | `0` | Minute to run (0-59) |
| `--cooldown` | int | `24` | Cooldown hours between runs |
| `--uninstall` | flag | `false` | Remove installed scheduler |
| `--show` | flag | `false` | Print config without installing |

### Platform support

| Platform | What gets created | Activate command |
|----------|-------------------|-----------------|
| **macOS** | `~/Library/LaunchAgents/com.immich-memories.auto.plist` | `launchctl load <path>` |
| **Linux** | systemd user service + timer in `~/.config/systemd/user/` | `systemctl --user enable --now immich-memories-auto.timer` |
| **Other** | Prints a crontab entry you can add manually | `crontab -e` |

On macOS, launchd wakes the machine if it's sleeping, runs the command, and goes back to sleep. No daemon needed.

## auto history

```bash
immich-memories auto history [--limit N]
```

Shows recent auto-generated memories with date, type, and output path.

## auto test-notification

```bash
immich-memories auto test-notification
```

Sends a test notification through your configured Apprise URLs. Requires `notifications.enabled: true` and at least one URL in `notifications.urls` in your config.

## Configuration

These go under `advanced:` in your `config.yaml`:

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
    urls: []                        # Apprise URLs: ntfy://ntfy.sh/my-topic, discord:///id/token
    on_success: true
    on_failure: true
```

Trip detection requires your homebase coordinates:

```yaml
trips:
  homebase_latitude: 50.8468
  homebase_longitude: 4.3525
  min_distance_km: 50
  min_duration_days: 2
```
