---
sidebar_position: 8
title: auto
---

# auto

`auto` looks at your library and decides which memory to make today: a trip that ended last
week, a birthday two days ago, last month's highlights, a year nobody has cut yet. One decision
per run, one video at most, then it exits. Schedule it once a day and the memories arrive on
their own.

The "score" on this page ranks candidate memories against each other. It never touches which
pictures go into a video: that is the editor's job, and it works the same for a scheduled memory
as for one you asked for. See [The Curator](../pipeline/the-curator.md).

## How a candidate is chosen

Nine detectors propose candidates, hard rotation rules reject some, the rest are scored, the
top one is generated.

| Detector | Proposes | Base score | Scaled by |
|---|---|---|---|
| Yearly | past years with content, after 15 January | 0.8 | 10 % off per year of age, floor 0.3 |
| Birthday | a person whose birthday was 2 to 60 days ago | 0.75 | fixed |
| Monthly | the latest completed month, if not made yet | 0.7 | fixed (it never looks further back) |
| Activity burst | a month with more than 2× the rolling 12-month average | 0.7 | fixed once over the threshold |
| Trip | GPS trips in the trailing year, 7 days after coming home | up to 0.75 | length up to 14 days × pictures up to 200 |
| Person spotlight | the five most-pictured people | 0.6 | that person's share of the top person's count, floor 0.2 |
| Multi-person | pairs who appear together | 0.55 | estimated shared pictures up to 500, 50 minimum |
| On this day | dates with content in 5+ years | 0.35 | how many years the same month has content, up to 10 |
| Special day | a catalogued day whose anniversary is within 3 days | 0.8 | 1.0 for a decade, 0.85 for a half-decade, 0.6 otherwise |

Then the scorer applies, in this order: rotation, a 1.2× boost for a memory that does not exist
yet, recency (linear decay over 365 days from when the memory is timely, floor 0.5), content
richness (up to 30 % of the score, on a log scale) and a same-type cooldown (0.3× for 7 days, 0.7×
for 30 days after the same type ran). Duplicates are collapsed by memory key first, before the
sort, and the per-type caps apply after it: 3 per type, 1 for on-this-day and special day, 2 for
multi-person.

The rotation rules are hard. If every candidate is rejected the run is skipped, and nothing
relaxes a rule to get another video out:

- the previous category cannot repeat;
- a category cannot appear more than twice in the last six completed automatic runs;
- a monthly review cannot run twice in the same calendar month;
- a person cannot reappear if they were in either of the last two person-bearing runs.

Two timing rules protect the two memories that depend on sync: birthdays fire 2 days after the
date (party photos take time to reach Immich) and a person whose birthday is within 7 days is
skipped by the spotlight detector, so the birthday version wins later. Trips fire 7 days after
coming home. Trips need `trips.homebase_latitude` and `trips.homebase_longitude` in the config.

Special days come from the catalogue `discover-days` writes. Automation passes the date only
(`--day 2016-06-12`); the title is read back from the catalogue at run time, because command
lines are logged and visible in `ps`.

## auto suggest

```bash
immich-memories auto suggest [--json] [--limit 10] [--type TYPE]
```

Runs every detector and prints the ranked list with each candidate's reason, the rule that
rejected the others, and any candidate that is being held back. A candidate that failed twice in a
row waits before it is proposed again (24 hours, then 3 days, capped at 7 days); a single failure
never counts, and a success clears the streak. The GPS fetch is cached for 7 days.

## auto run

```bash
immich-memories auto run [--dry-run] [--force] [--cooldown HOURS] [--upload] [--quiet]
```

Exactly one action per run, one memory per invocation: retry the oldest pending upload if there is
one, otherwise generate the top candidate. `--cooldown` (config `automation.cooldown_hours`, 24) is measured from the last
run's start with 30 minutes of tolerance, so a daily timer at 24 fires every day.

The typed outcomes are `skipped`, `dry_run`, `completed` and `failed`; the first three exit 0.
Quiet output is a stable JSON object with `runtime` as its first key. Key a wrapper on `outcome`,
not on `action`, which is `generation` on every path:

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
    {"category": "person_spotlight", "memory_key": "person_spotlight:2025-01-01:2025-12-31:lucas", "rule": "person_in_last_two_person_runs"}
  ]
}
```

An upload that keeps failing is abandoned after `automation.max_delivery_attempts` (5) tries,
with a notification carrying the original error; the video stays on disk.

## auto install

```bash
immich-memories auto install [--hour 9] [--minute 0] [--cooldown 24] [--show] [--uninstall] [--force]
```

Writes a launcher at `~/.immich-memories/bin/immich-memories-auto` and schedules it: a launchd
plist on macOS, a systemd user timer on Linux, a crontab line to paste elsewhere. The launcher
looks `immich-memories` up on every fire, so an upgrade in place is picked up without
reinstalling the schedule. `--uninstall` removes both.

In Docker, or when the web UI process should do it, skip this command and set
`automation.enabled: true` with `automation.daily_at`; the UI runs `auto run` once a day
itself. See [Automated generation](../recipes/automated-generation.md).

Three things about a scheduled job:

- it does not inherit your shell. `auto install` copies `PATH` and the ACE-Step and torch
  variables it knows about into the plist or unit, nothing else; credentials belong in the config
  file, not in a plist under `~/Library`;
- it refuses a git worktree or a checkout behind its upstream, because a scheduled job re-runs
  stale code every night while the logs look fine (`--force` overrides);
- `--config` is a root option and goes before `auto`: the installed job keeps the resolved path.

On macOS a missed job runs when the Mac wakes; launchd does not wake it.

## auto status, history, test-notification

`auto status [--json]` shows which code is running (version, commit, how far behind its
upstream), the scheduler state, the last attempt, the cooldown, the last six categories and the
live suggestion. `auto history [--limit N]` lists what automation made. `auto test-notification`
sends one message through the configured Apprise URLs, bypassing the notification cooldown.

## Configuration

The `automation:` and `notifications:` keys, with their defaults, are in the
[config reference](../../reference/config-reference.md#automation). `automation.enabled` and
`automation.daily_at` are the two a first setup needs; everything else tunes the rotation rules
described above.

## Run a specific suggestion

`immich-memories auto suggest --json` includes each suggestion's `memory_key`.
Pass that exact key to `immich-memories auto run --candidate 'KEY' --dry-run`
to check it, then omit `--dry-run` to generate it. The runner checks eligibility
again. A stale key fails with an explanation; it never substitutes another memory.
Cooldown, repetition rules and failure backoff still apply. `--force` only skips
cooldown.

The complete child stdout and stderr are retained under the configured cache at
`automation-output/<attempt-id>.private.log`. Successful runs, failed exits and
runs killed by the two-hour timeout all get one. Files are readable only by their
owner, with configured credentials redacted. Older runs may have no log. Nothing
evicts them yet: one log per attempt stays until you delete it, and `runs storage`
counts the directory.
