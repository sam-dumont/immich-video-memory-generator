---
sidebar_position: 2
title: Automate it
---

# Automate it

Reader: newcomer for the first two sections, power user for the rest.

`immich-memories auto run` is the single daily entry point. It looks at your library, decides which one memory
is worth making today (a trip that ended last week, a birthday two days ago, last month's highlights, a year
nobody cut yet), makes it, and exits. Schedule it once a day and the films arrive on their own.

```bash
immich-memories auto suggest            # what would it make today, and why
immich-memories auto run --dry-run      # the decision, without the render
immich-memories auto run                # decide and do it
```

It runs on every tier. On a NAS with no model, the films it makes are the same NAS cuts you get by hand.

## Docker: switch on the built-in timer

In Docker the container's only process is the web UI, so the timer lives there. One setting:

```yaml
advanced:
  automation:
    enabled: true        # default false
    daily_at: "09:00"    # the container's local time (set TZ=)
```

or `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and `IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00` in your
`.env`. That is the whole setup.

The UI process then runs the same `auto run` decision once a day, with the same lock, history, upload retry
and notifications as the CLI. A container that was down at `daily_at` catches up when it starts; if the day's
run already happened (a manual `docker compose exec immich-memories immich-memories auto run` counts) it waits
for tomorrow. A manual run in progress holds the same lock, so the timer reports `skipped` instead of fighting
it. `/health/ready` shows the timer under `in_process_scheduler`.

## Bare metal: auto install

```bash
immich-memories auto install --hour 9
```

This writes a launcher at `~/.immich-memories/bin/immich-memories-auto` and schedules it: a launchd plist on
macOS, a systemd user timer on Linux, a crontab line to paste anywhere else. The launcher looks
`immich-memories` up on every fire, so an upgrade in place needs no reinstall. `--uninstall` removes both.

Three things a scheduled job does differently from your shell:

- it does not inherit your environment. `auto install` copies `PATH` and the ACE-Step and torch variables it
  knows, nothing else; credentials belong in the config file;
- it refuses a git worktree or a checkout behind its upstream, because a nightly job on stale code looks fine
  in the logs (`--force` overrides);
- `--config` goes before `auto`, and the installed job keeps the resolved path.

On macOS a missed job runs when the Mac wakes; launchd does not wake it.

## How it picks one memory

Nine detectors propose candidates, hard rotation rules reject some, the rest are scored, and the top one is
made. The score ranks memories against each other; it never touches which pictures go in a film. That is the
editor's job, see [How it chooses](../how-it-chooses/overview.md).

| Detector | Proposes | Score |
|---|---|---|
| Yearly | past years with content, after 15 January | 0.8, 10 % off per year of age, floor 0.3 |
| Birthday | a person whose birthday was 2 to 60 days ago | 0.75 |
| Monthly | the latest completed month, if not made yet | 0.7, and it never looks further back |
| Activity burst | a month with more than 2× the rolling 12-month average | 0.7 once over the threshold |
| Trip | trips in the trailing year, 7 days after coming home | up to 0.75, by length (14 days) × pictures (200) |
| Person spotlight | the five most-pictured people | 0.6 × their share of the top person's count, floor 0.2 |
| Multi-person | pairs who appear together | 0.55, by estimated shared pictures up to 500, 50 minimum |
| On this day | dates with content in 5+ years | 0.35, by years the same month has content, up to 10 |
| Special day | a catalogued day whose anniversary is within 3 days | 0.8, ×1.0 for a decade, ×0.85 for a half-decade, ×0.6 otherwise |

Then, in order: a 1.2× boost for a memory that does not exist yet, recency (linear decay over 365 days from
when the memory is timely, floor 0.5), content richness (up to 30 % of the score, log scale), and a same-type
cooldown (0.3× for 7 days, 0.7× for 30 days). Per-type caps: 3 per type, 1 for on-this-day and special day, 2
for multi-person.

The rotation rules are hard. If every candidate is rejected the run is skipped; nothing relaxes a rule to get
another video out:

- only the latest completed month is eligible, and a monthly review cannot run twice in the same calendar month;
- the previous category cannot repeat;
- a category cannot appear more than twice in the last six completed automatic runs;
- a person cannot come back if they were in either of the last two person runs.

Timing: birthdays fire 2 days after the date and trips 7 days after coming home, so the phone has uploaded.
Trips need `trips.homebase_latitude` and `trips.homebase_longitude` (see
[Teach it your family](../get-started/who-is-who.md)). Special days come from the catalogue `discover-days`
writes.

`auto suggest` prints the ranked list, each candidate's reason, the rule that rejected the others and anything
held back. A candidate that failed twice in a row waits before it comes back (24 hours, then 3 days, then 7);
one failure never counts, and a success clears it.

## What auto run does, exactly

One action per run, one memory per invocation: retry the oldest pending upload if there is one, otherwise make
the top candidate. `--cooldown` (`automation.cooldown_hours`, 24) is measured from the last run's start with
30 minutes of slack, so a daily timer fires every day. `--candidate KEY` runs one exact `memory_key` from
`auto suggest --json`; the rules still apply, `--force` skips only the cooldown, and a stale key fails rather
than making something else.

The outcomes are `skipped`, `dry_run`, `completed` and `failed`; the first three exit 0.
Quiet output is a stable JSON object with `runtime` as its first key. Key a wrapper on `outcome`: `action` is
`generation` on every path.

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

An upload that keeps failing is dropped after `automation.max_delivery_attempts` (5) tries, with a notification
carrying the error; the video stays on disk. Every attempt writes its full output to
`automation-output/<attempt-id>.private.log` under the cache (owner-readable, credentials redacted,
downloadable from the **Runs** page). `auto status` shows the running code's version and commit, the timer, the
last attempt, the cooldown and the live suggestion.

## Trigger it over HTTP

One POST starts the decision `auto run` would have made, on the same detectors, rules, cooldown and history.
You choose *when*, not *what*: an Immich workflow when an album fills up, a cron on another box, a phone
shortcut, Home Assistant.

The route is not served at all unless something can authenticate the caller, because this process holds your
Immich API key.

| `auth.enabled` | `server.trigger_token` | `POST /api/trigger` |
|---|---|---|
| off | unset | **404**: the route is not enabled |
| off | set | token required |
| on | unset | logged-in session required (browsers only) |
| on | set | token **or** session |

```bash
export IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN="$(openssl rand -hex 32)"

curl -X POST https://memories.example.com/api/trigger \
  -H "x-api-key: $IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN"
```

Keep the token in the environment: `server` is not a section that expands `${VAR}`, so `"${SOMETHING}"` in
`config.yaml` is those literal characters. It is compared in constant time and redacted from logs, `/health`
and the config viewer. Send it only over HTTPS, behind the same reverse proxy as the web UI.

The answer is `202 Accepted` with an `attempt_id` and a `status_url`; `Authorization: Bearer <token>` works too.
**409** means a run is already going, and the body names it. GET the `status_url` for the live `phase`, then a
final `state` (`completed`, `failed`, `skipped`, `dry_run`) with `run_id`, `output_duration_seconds`,
`delivery_status` and `immich_asset_id`.

`skipped` is a normal answer: a workflow that fires on every upload mostly gets it back, and one that fires on a
trip album asks for the best candidate right now, not for that album. For a specific film, use the CLI or the
web UI.

## Kubernetes

`deploy/kubernetes/base/job.yaml` holds a one-off `generate` Job plus monthly and `auto run` CronJobs. It reads
the `immich-memories-secrets` Secret and shares the volumes of the [Kubernetes deployment](../run/kubernetes.md):

```bash
kubectl apply -f deploy/kubernetes/base/job.yaml
```

## A named memory on a named date: the scheduler daemon

The one thing `auto` cannot say is "a year in review every 15 January". The `scheduler` command group, the
advanced/legacy cron daemon, still does that, and nothing else in the app reads its `schedules:` section. It
knows none of the rotation rules, back-off or upload retries above and needs `--foreground`, so prefer `auto`.

```yaml
scheduler:
  enabled: true
  timezone: "America/New_York"
  schedules:
    - name: "yearly-recap"
      memory_type: "year_in_review"
      cron: "0 9 15 1 *"          # 15 January, 9:00
      upload_to_immich: true
      album_name: "{year} Memories"
```

```bash
immich-memories scheduler start --foreground
```

Every `automation:`, `notifications:` and `scheduler:` key is in the
[config reference](../reference/config-reference.md#automation), every flag in the
[CLI reference](../reference/cli-reference.md#auto).
