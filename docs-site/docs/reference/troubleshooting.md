---
title: Troubleshooting
---

# Troubleshooting

`immich-memories -v <command>` logs at DEBUG for one run. `immich-memories preflight` checks
Immich, the model files and every configured server in one go.

## Cannot connect to Immich

Run the read-only check first. It checks authentication and reports the resolved API contract
without searching, generating or uploading:

```bash
immich-memories config test
```

It prints one line and exits 1 on failure. `URL not configured` and `API key not configured` mean
the setting never reached the process.

- The URL needs its protocol (`http://` or `https://`).
- A `403 Forbidden` means the key exists but lacks rights: recreate it with **All**, or the read
  plus upload plus album scopes the quick start lists.
- Immich must be v2 or v3. Immich 1.x is refused at connect time.
- In Docker, `localhost` is the container. Use the host's IP or the Docker network name.

## Immich v2/v3 Version Mismatch

Immich Memories supports Immich v2 and v3. The normal configuration is:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

`auto` detects the server major at runtime; you do not pick one for each run. If a reverse proxy
hides or rewrites `/api/server/version`, use `v2` or `v3` as a manual troubleshooting escape hatch.
The override forces that contract, so match the real server major (an explicit `v3` override on a
v3 server) and return to `auto` once detection works.

Do not flip the override as part of a routine v2-to-v3 upgrade. `auto` is runtime detection; the
manual values exist to diagnose broken version discovery.

The compatibility layer handles the known v2-to-v3 differences: duration strings versus integer
milliseconds, version-specific upload fields, and the UTC offset on search dates. The read-only
`immich-memories config test` reports the server version and authentication errors; it does not test
uploads. If a v3 upload
fails, keep the error shown by the command doing the upload and check the relevant Immich server
logs. API keys are redacted.

## The cut stops with "Waiting for the reader at host:port"

The model server is not answering. The run retries three times, two then four seconds apart,
and fails naming the endpoint. Start the server (or fix `llm.base_url`), then **Cut again** or
rerun the command; compatible saved readings can be reused. To use rules, set `editorial.reader: rules`.
Preparation still follows its own tier; `full` also needs a caption server.

## A picture I expected is not in the cut

```bash
immich-memories runs why <asset id> --run <run id>
```

says where it passed and where it was dropped, with the reason. On the Memory page, tick it on
the pool page and **Cut again**. On the CLI, use `--include <asset id>`. Inclusion
requests do not bypass missing media or audience restrictions; read the resulting decision.

## No Videos Found

- The person name must match Immich's, case-insensitive, nothing else fuzzy.
- Photos are in the pool by default (`photos.enabled: true`); with `--no-photos` the period needs
  at least one video.
- A `--person` filter needs tagged assets in the period.

## The first cut is slow

The first cut over a period prepares every eligible picture once (previews, pixel facts, heads,
detectors, and on the `full` tier one caption each), then the reader reads the period. The levers,
in order: put the caption server and the reader on the fastest box you have, prepare a library a
month at a time with [`prepare`](../create/cli/prepare.md) and read its cost table, pick a lower
[tier](../deploy/running-modes.md), and keep the cache (facts and readings are banked; clearing
the directory makes the next cut cold again). Measured numbers per host are on Running modes.

## Out of memory

Check which process was killed and its memory limit: the app, reader, caption server and
music generator have different budgets. A large model can hold most of a host's RAM while the
app also needs space to decode frames and render titles.

Try a smaller source period or lower output resolution. Move model services to another machine,
or use rules with `no_captions` if the model workload is too large. In containers, inspect the
exit reason and resource usage before raising limits. See [hardware requirements](../deploy/hardware/overview.md).

## FFmpeg not found

FFmpeg is called by name off `PATH`; a missing binary surfaces as
`FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'` the first time a clip is
encoded. `brew install ffmpeg`, `apt install ffmpeg`, or use the Docker image, which has it.

## GPU not detected

The log says `No hardware acceleration detected, using software encoding` and `preflight` reports
no GPU. `immich-memories hardware` shows what it sees. For NVIDIA, `nvidia-smi` must work; in
Docker you need `--gpus all` and the NVIDIA Container Toolkit. Hardware encoders only speed up the
encode; nothing in the app runs inference on them.

## Music generation fails

- Both backends default to `http://localhost:8000`. ACE-Step is treated as up only when
  `/health` returns `{"data": {"status": "ok"}}`; MusicGen only needs HTTP 200.
- A timeout: raise `ace_step.timeout_seconds` (3600) or `musicgen.timeout_seconds` (10800), both
  capped at 18000.
- A failed generator falls back to the next one, then to a bundled track, and the run says so.
