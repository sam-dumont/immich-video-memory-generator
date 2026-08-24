---
sidebar_position: 3
title: Trigger from Immich or Anything Else
---

# Trigger from Immich or Anything Else

The server accepts one POST that starts a memory. It takes no parameters: it runs exactly the
decision `immich-memories auto run` would have made — same detectors, same variety rules, same
cooldown, same history. You are choosing *when*, not *what*.

That makes it the piece Immich Workflows was missing. A workflow that fires when an album fills
up, a cron on another box, a phone shortcut, a Home Assistant automation — anything that can make
an HTTP request can now start a memory.

## Turn it on

The endpoint is **not served at all** unless something can authenticate the caller. This process
holds your Immich API key, so an anonymous request that could spend it is not a thing that exists.

| `auth.enabled` | `server.trigger_token` | `POST /api/trigger` |
|---|---|---|
| off | unset | **404** — the route is not enabled |
| off | set | token required |
| on | unset | logged-in session required (browsers only) |
| on | set | token **or** session |

For headless callers, set a token:

```yaml
advanced:
  server:
    trigger_token: "${IMMICH_MEMORIES_TRIGGER_TOKEN}"
```

Generate something long and random (`openssl rand -hex 32`) and keep it in the environment rather
than in `config.yaml` — `IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN` works too. The value is compared in
constant time and redacted from logs, `/health`, and the config viewer like every other secret.

The token is a shared secret over whatever transport your server already uses. If the UI is
reachable from outside your LAN, put it behind the same HTTPS reverse proxy you use for the web
interface — a token sent over plain HTTP is a token you have published.

## Start a run

```bash
curl -X POST https://memories.example.com/api/trigger \
  -H "x-api-key: $IMMICH_MEMORIES_TRIGGER_TOKEN"
```

```json
{
  "status": "accepted",
  "attempt_id": "6f1c2a54-9d0e-4c31-9f6a-2c1d0b7e8a44",
  "status_url": "/api/trigger/6f1c2a54-9d0e-4c31-9f6a-2c1d0b7e8a44"
}
```

`202 Accepted`, not `200 OK` — a generation takes minutes to hours, so the call returns the moment
the run is booked. `Authorization: Bearer <token>` works in place of `x-api-key` if your caller
prefers it.

**409 Conflict** means a run is already going, and the body names it:

```json
{ "detail": "a run is already active", "attempt_id": "…" }
```

One automation decision runs at a time, enforced by the same lock the nightly timer and
`immich-memories auto run` use. A trigger cannot make two generations fight over your GPU.

## Poll for progress

```bash
curl -s https://memories.example.com/api/trigger/$ATTEMPT_ID \
  -H "x-api-key: $IMMICH_MEMORIES_TRIGGER_TOKEN"
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
`discovery` → `analysis` → `assembly` rather than a flat "running". When it finishes, `state`
becomes one of `completed`, `failed`, `skipped`, or `dry_run`, and `run` carries the record from
the run database — run id, status, output duration, and the Immich asset id if it was uploaded.

`skipped` is a normal answer, not a failure. The trigger runs what `auto run` decides, and `auto
run` declines when the cooldown is still active or nothing scored well enough. `reason` says which.

## From an Immich workflow

Immich Workflows can call an external URL when something happens in your library. Point one at
`/api/trigger` with the token header and you have "when this happens in Immich, make a memory".

Two things worth knowing before you wire it up:

- **The cooldown still applies.** A workflow that fires on every upload will mostly get `skipped`
  back, which is the system working. Pick a trigger that fires about as often as you want videos.
- **The server picks the memory.** A workflow that fires on a trip album does not generate *that*
  album — it asks for the best candidate right now, which may be something else entirely. If you
  want a specific memory, use the CLI or the web UI.

## Turning it off

Clear `server.trigger_token`. With authentication also off, the routes stop being served and go
back to answering 404.
