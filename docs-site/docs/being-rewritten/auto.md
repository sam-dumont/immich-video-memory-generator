---
sidebar_position: 8
title: auto
unlisted: true
---

:::note[Being rewritten]

This page is being split into the new docs. Its text moves to [Automated generation](../make/automate.md).

:::

# auto

`auto` looks at your library and decides which memory to make today: a trip that ended last week, a
birthday two days ago, last month's highlights, a year nobody has cut yet. One decision per run,
one video at most. Schedule it once a day and the memories arrive on their own.

The "score" below ranks candidate memories against each other. It never touches which pictures go
into a video: that is [the editor's job](./pipeline.md).

## How a candidate is chosen

Nine detectors propose candidates, hard rotation rules reject some, the rest are scored, the top
one is generated.

| Detector | Proposes | Score |
|---|---|---|
| Yearly | past years with content, after 15 January | 0.8, 10 % off per year of age, floor 0.3 |
| Birthday | a person whose birthday was 2 to 60 days ago | 0.75 |
| Monthly | the latest completed month, if not made yet | 0.7, and it never looks further back |
| Activity burst | a month with more than 2× the rolling 12-month average | 0.7 once over the threshold |
| Trip | GPS trips in the trailing year, 7 days after coming home | up to 0.75, by length (14 days) × pictures (200) |
| Person spotlight | the five most-pictured people | 0.6 × their share of the top person's count, floor 0.2 |
| Multi-person | pairs who appear together | 0.55, by estimated shared pictures up to 500, 50 minimum |
| On this day | dates with content in 5+ years | 0.35, by years the same month has content, up to 10 |
| Special day | a catalogued day whose anniversary is within 3 days | 0.8, ×1.0 for a decade, ×0.85 for a half-decade, ×0.6 otherwise |

The scorer then applies, in order: rotation, a 1.2× boost for a memory that does not exist yet,
recency (linear decay over 365 days from when the memory is timely, floor 0.5), content richness
(up to 30 % of the score, on a log scale) and a same-type cooldown (0.3× for 7 days, 0.7× for 30
days). Duplicates collapse by memory key before the sort; the per-type caps apply after it: 3 per
type, 1 for on-this-day and special day, 2 for multi-person.

The rotation rules are hard. If every candidate is rejected the run is skipped; nothing relaxes a
rule to get another video out:

- the previous category cannot repeat;
- a category cannot appear more than twice in the last six completed automatic runs;
- a monthly review cannot run twice in the same calendar month;
- a person cannot reappear if they were in either of the last two person-bearing runs.

Two timing rules wait for photos to reach Immich: birthdays fire 2 days after the date, and a
person whose birthday is within 7 days is skipped by the spotlight detector so the birthday version
wins later. Trips fire 7 days after coming home and need the homebase coordinates in the config.

Special days come from the catalogue `discover-days` writes. Automation passes the date only; the
title is read back from the catalogue at run time, because command lines are visible in `ps`.

## auto suggest

`immich-memories auto suggest` runs every detector and prints the ranked list with each candidate's
reason, the rule that rejected the others, and any candidate being held back. A candidate that
failed twice in a row waits before it is proposed again (24 hours, then 3 days, capped at 7 days);
a single failure never counts, and a success clears the streak. The GPS fetch is cached for 7 days.

## auto run

Exactly one action per run, one memory per invocation: retry the oldest pending upload if there is
one, otherwise generate the top candidate. `--cooldown` (config `automation.cooldown_hours`, 24) is
measured from the last run's start with 30 minutes of tolerance, so a daily timer fires every day.

The typed outcomes are `skipped`, `dry_run`, `completed` and `failed`; the first three exit 0.
Quiet output is a stable JSON object with `runtime` as its first key. Key a wrapper on `outcome`:
`action` is `generation` on every path.

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
    {"category": "person_spotlight", "memory_key": "...", "rule": "person_in_last_two_person_runs"}
  ]
}
```

An upload that keeps failing is abandoned after `automation.max_delivery_attempts` (5) tries, with
a notification carrying the original error; the video stays on disk.

`--candidate` takes an exact `memory_key` from `auto suggest --json` to run one suggestion rather
than the top one. The runner checks eligibility again, so cooldown, repetition rules and failure
backoff still apply, and `--force` only skips cooldown. A stale key fails with an explanation; it
never substitutes another memory.

Every attempt, including one killed by the two-hour timeout, writes the child's complete stdout and
stderr to `automation-output/<attempt-id>.private.log` under the cache: owner-readable only, with
configured credentials redacted, downloadable from the web UI's **Runs** page. Nothing evicts them,
so `runs storage` counts the directory.

## auto install

`immich-memories auto install` writes a launcher at
`~/.immich-memories/bin/immich-memories-auto` and schedules it: a launchd plist on macOS, a systemd
user timer on Linux, a crontab line to paste elsewhere. The launcher looks `immich-memories` up on
every fire, so an upgrade in place is picked up without reinstalling the schedule. `--uninstall`
removes both.

In Docker, or when the web UI process should do it, skip this command and set
`automation.enabled: true` with `automation.daily_at`; the UI runs `auto run` once a day
itself. See [Automated generation](../make/automate.md).

Three things about a scheduled job:

- it does not inherit your shell. `auto install` copies `PATH` and the ACE-Step and torch variables
  it knows about, nothing else; credentials belong in the config file;
- it refuses a git worktree or a checkout behind its upstream, because a scheduled job re-runs stale
  code every night while the logs look fine (`--force` overrides);
- `--config` goes before `auto`: the installed job keeps the resolved path.

On macOS a missed job runs when the Mac wakes; launchd does not wake it.

## auto status

Which code is running (version, commit, how far behind its upstream), the scheduler state, the last
attempt, the cooldown, the last six categories and the live suggestion. `auto history` and
`auto test-notification` are in the
[CLI reference](../reference/cli-reference.md#auto-history), along with every flag on this page.

## Configuration

The `automation:` and `notifications:` keys are in the
[config reference](../reference/config-reference.md#automation). `automation.enabled` and
`automation.daily_at` are the two a first setup needs.
