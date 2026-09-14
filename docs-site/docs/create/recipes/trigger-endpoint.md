---
sidebar_position: 3
title: Trigger from Immich or Anything Else
---

# Trigger from Immich or Anything Else

Send `POST /api/trigger` to request the same decision as `immich-memories auto run`.
It accepts no memory parameters and keeps the normal cooldown and variety rules. Use it from
a workflow, phone shortcut or another service that can send an HTTP request.

## Turn it on

The endpoint requires authentication or a trigger token. Without either, it returns **404**.

| `auth.enabled` | `server.trigger_token` | `POST /api/trigger` |
|---|---|---|
| off | unset | **404**: the route is not enabled |
| off | set | token required |
| on | unset | logged-in session required (browsers only) |
| on | set | token **or** session |

For headless callers, set a token. Generate something long and random and keep it in the
environment rather than in `config.yaml`:

```bash
export IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN="$(openssl rand -hex 32)"
```

Set the same variable on the running server and keep its value for callers. Writing a token
into the config file also works:

```yaml
advanced:
  server:
    trigger_token: "replace-with-your-generated-token"
```

but `server` is not one of the sections that expand a `${VAR}` reference: put `"${SOMETHING}"`
there and the token is those literal characters. Either way the value is compared in constant
time and redacted from logs, `/health`, and the config viewer like every other secret.

The token is a shared secret over whatever transport your server already uses. If the UI is
reachable from outside your LAN, put it behind the same HTTPS reverse proxy you use for the web
interface so the token is encrypted in transit.

## Start a run

```bash
curl -X POST https://memories.example.com/api/trigger \
  -H "x-api-key: $IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN"
```

```json
{
  "status": "accepted",
  "attempt_id": "6f1c2a54-9d0e-4c31-9f6a-2c1d0b7e8a44",
  "status_url": "/api/trigger/6f1c2a54-9d0e-4c31-9f6a-2c1d0b7e8a44"
}
```

The response is **202 Accepted**. Generation runs in the background; acceptance is not a
guarantee that a video will be generated. `Authorization: Bearer <token>` works in place of `x-api-key` if your caller
prefers it.

**409 Conflict** means a run is already going, and the body names it:

```json
{ "detail": "a run is already active", "attempt_id": "…" }
```

One automation decision runs at a time, enforced by the same lock the nightly timer and
`immich-memories auto run` use. A busy installation rejects another trigger instead of starting a second automatic run.

## Poll for progress

```bash
curl -s https://memories.example.com/api/trigger/$ATTEMPT_ID \
  -H "x-api-key: $IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN"
```

```json
{
  "attempt_id": "6f1c2a54-9d0e-4c31-9f6a-2c1d0b7e8a44",
  "state": "running",
  "reason": "http trigger",
  "started_at": "2026-08-24T09:15:02+00:00",
  "finished_at": null,
  "phase": "analysis",
  "memory_type": "monthly_highlights",
  "error": null,
  "run": null
}
```

`phase` is live: the generation reports each pipeline stage back as it goes, so you see
`discovery` → `download` → `analysis` → `selection` → `render` → `music` → `delivery` →
`complete` rather than a flat "running". When it finishes, `state` becomes one of `completed`,
`failed`, `skipped`, or `dry_run`, and `run` carries eight fields from the run database:
`run_id`, `status`, `created_at`, `completed_at`, `last_phase`, `output_duration_seconds`,
`delivery_status`, `immich_asset_id`. The rest of the record holds host paths and person names,
which this contract has no reason to promise.

`skipped` is a normal answer, not a failure. The trigger runs what `auto run` decides, and `auto
run` declines when the cooldown is still active or no candidate cleared its bar. `reason` says
which.

## From an Immich workflow

Immich Workflows can call an external URL when something happens in your library. Point one at
`/api/trigger` with the token header and you have "when this happens in Immich, make a memory".

Two things worth knowing before you wire it up:

- **The cooldown still applies.** A workflow that fires on every upload will mostly get `skipped`
  back, which is the system working. Pick a trigger that fires about as often as you want videos.
- **The server picks the memory.** A workflow that fires on a trip album does not generate *that*
  album: it asks for the best candidate right now, which may be something else entirely. If you
  want a specific memory, use the CLI or the web UI.

## Turning it off

Clear `server.trigger_token`. With authentication also off, the routes stop being served and go
back to answering 404.
