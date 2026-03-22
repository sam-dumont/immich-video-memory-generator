---
sidebar_position: 8
title: auto
---

# auto

Scans your Immich library, detects what's worth turning into a memory video, and generates it. Trips, birthdays, monthly highlights, person spotlights: it figures out what matters and picks the best one.

## How selection works

The system runs 8 detectors against your library, each producing candidates with a score between 0 and 1. The top candidate gets generated.

Here's what a real library (50K+ assets, 20 years, 500 tagged people) produces:

```
 #  Type                 Period                  Score  Reason
 1  monthly_highlights   Feb 2026                0.776  683 assets, most recent month
 2  person_spotlight     2025 (Lucas)            0.700  Birthday (2 years old), 16464 assets
 3  year_in_review       2025                    0.672  13151 assets, never generated
 4  monthly_highlights   Jan 2026                0.642  684 assets, never generated
 5  monthly_highlights   Dec 2025                0.523  1384 assets, never generated
 6  multi_person         2025 (Lucas & Alex)      0.514  ~2564 shared moments
 7  multi_person         2025 (Alice & Lucas)    0.514  ~2007 shared moments
 8  trip                 Jul 26 - Aug 10 2025    0.449  16-day trip to Charente-Maritime, 960 assets
 9  year_in_review       2024                    0.384  18562 assets, never generated
10  on_this_day          Mar 22                  0.349  Memories across 20 years (2005-2025)
11  person_spotlight     2025 (Alex)              0.291  2nd most featured, 8549 assets
12  person_spotlight     2025 (Alice)            0.228  3rd most featured, 6690 assets
13  trip                 May 1-4 2025            0.123  4-day trip to Pas-de-Calais, 339 assets
14  trip                 May 29 - Jun 1 2025     0.121  4-day trip to Ostend, Belgium, 258 assets
```

Notice the variety: 3 monthly max, 3 person spotlights, 3 trips, 2 multi-person pairs, 1 on-this-day. The per-type caps prevent any single detector from flooding the list.

### What happens over a week of daily runs

Say you set up `auto install` and it runs every morning at 9am:

**Monday**: Feb 2026 monthly highlights gets generated (score 0.776, top candidate).

**Tuesday**: All `monthly_highlights` candidates get a 0.3x penalty for 7 days. Lucas's birthday spotlight (0.700) is now #1. That gets generated.

**Wednesday**: `person_spotlight` also gets a 7-day penalty. Year-in-review 2025 (0.672) takes over.

**Thursday**: `year_in_review` penalized. The Charente-Maritime trip rises to #1. Multi-person pairs are close behind.

**Friday**: Trip penalized. Lucas & Alex together (multi_person) gets generated.

**Next Monday** (day 8): The 7-day penalty on monthlies lifts. But Feb 2026 was already generated (deduped), so Jan 2026 takes the monthly slot.

After a few weeks, the system has generated: 3 monthlies, a birthday video, a year-in-review, a 16-day trip, a couple together video, and a few person spotlights. Each run automatically picks whatever is most valuable at that moment.

### Birthday timing

Birthdays get special treatment. Two rules make sure the timing is right:

1. **Sync buffer**: the detector only fires 2+ days after the birthday. Photos from the birthday party need time to sync to Immich before we pull clips.

2. **Lookahead suppression**: if someone's birthday is within the next 7 days, the PersonSpotlightDetector skips them entirely. This prevents generating a generic "most featured person" video for someone whose birthday video would be much better timed a few days later.

### Trip detection

Trips are detected from GPS data: any cluster of photos 50+ km from your homebase, spanning 2+ days, with no gap larger than 2 days. The detector only fires 7+ days after returning home (same sync buffer logic as birthdays).

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
| **MonthlyDetector** | Last 6 un-generated months | 0.5-0.8 |
| **YearlyDetector** | Past years with content (only after Jan 15) | 0.5-0.7 |
| **PersonSpotlightDetector** | Top 5 people by asset count | 0.1-0.6 |
| **BirthdayDetector** | People whose birthday was 2-60 days ago | 0.75 |
| **TripDetector** | GPS-detected trips from the past year | 0.1-0.5 |
| **ActivityBurstDetector** | Months with >2x the rolling average (last 12 months) | 0.4-0.7 |
| **OnThisDayDetector** | Dates with content across 5+ years | 0.2-0.35 |
| **MultiPersonDetector** | Pairs who appear together frequently | 0.3-0.55 |

### Scoring adjustments

After detectors assign raw scores, the scorer applies:

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

Picks the #1 candidate from `suggest` and generates it. One memory per invocation, then exits.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dry-run` | flag | `false` | Show what would be generated, don't do it |
| `--force` | flag | `false` | Skip cooldown check |
| `--cooldown` | int | `24` | Min hours since last auto-run |
| `--upload` | flag | `false` | Upload result to Immich |
| `--quiet` | flag | `false` | Machine-friendly output (just the path) |

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

## auto history

```bash
immich-memories auto history [--limit N]
```

Shows recent auto-generated memories: date, type, date range, output file.

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
