---
title: Troubleshooting
---

# Troubleshooting

`immich-memories -v <command>` logs at DEBUG for one run. `immich-memories preflight` checks
Immich, the model files and every configured server in one go.

## Cannot connect to Immich

The read-only check comes first: authentication and the resolved API contract, nothing searched,
generated or uploaded.

```bash
immich-memories config test
```

It prints one line and exits 1 on failure. `URL not configured` and `API key not configured` mean
the setting never reached the process.

- The URL needs its protocol (`https://`). A trailing slash is tidied up, not refused.
- A `403 Forbidden` means the key exists but lacks rights: recreate it with **All**, or the read
  plus upload plus album scopes the quick start lists.
- Immich must be v2 or v3. Immich 1.x is refused at connect time.
- In Docker, `localhost` is the container. Use the host's IP or the Docker network name.

## Immich v2/v3 version mismatch

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

`auto` detects the server major at runtime; you do not pick one for each run. So a v2-to-v3
upgrade needs no change here. If a reverse proxy hides or rewrites `/api/server/version`, use `v2`
or `v3` as a manual troubleshooting escape hatch. The override forces that contract, so go back to
`auto` once detection works.

The read-only
`immich-memories config test` reports the server version and authentication errors; it does not
test uploads. If a v3 upload fails, keep the error shown by the command doing the upload and check
the relevant Immich server logs. API keys are redacted.

## The cut stops with "Waiting for the reader at host:port"

The model server is not answering. Start it (or fix `llm.base_url`), then **Cut again** or rerun
the command; everything already read is banked. Without a model, set `reader: rules` to cut anyway.

## A picture I expected is not in the cut

```bash
immich-memories runs why <asset id> --run <run id>
```

says where it passed and where it was dropped, with the reason. On the Memory page, tick it on the
pool page and **Cut again**: a tick outranks the editor. On the CLI, `--include <asset id>`.
Neither overrides the audience gate: a picture the gate holds at family-only, or one whose file
Immich cannot serve, stays out however you ask for it.

A source whose preview Immich answers HTTP 404 for is the second kind. The run logs one line
naming the count and the reason, `preview unavailable at Immich (HTTP 404)`, lists those ids under
`unservable_sources` in the attempt's `preparation.private.json`, and cuts the rest. Regenerate
that asset's thumbnails in Immich, then cut again.

## A clip fails with "Could not write header (incorrect codec parameters ?)"

The log names the source and shows a stream copy into a `.mov` refused by FFmpeg, usually
`vp9 only supported in MP4.`. The source is VP9 or AV1 inside a QuickTime `.MOV`, which Android
phones and some editors write. A lossless camera cut keeps the source container so ProRes and PCM
audio survive, and QuickTime will not carry those two codecs.

Nothing to do: the cut re-encodes that clip through the normal encode path with the same in and out
points, so it costs one encode instead of a copy. If a source still cannot be cut, that clip leaves
the film by name, the log says why, and the rest is assembled. Only a film whose every source
failed stops with `No clips could be processed`.

## No videos found

- The person name must match Immich's, case-insensitive, nothing else fuzzy.
- Photos are in the pool by default (`photos.enabled: true`); with `--no-photos` the period needs
  at least one video.
- A `--person` filter needs tagged assets in the period.

## The first cut is slow

The first cut over a period prepares every eligible picture once (previews, pixel facts, heads,
detectors, and on the `full` tier one caption each), then the reader reads the period. The levers,
in order: put the caption server and the reader on the fastest box you have, prepare a month at a
time with [`prepare`](../create/cli/prepare.md), pick a lower
[tier](../deploy/running-modes.md), and keep the cache. Measured numbers per host are on Running
modes.

## Out of memory

Almost always the reader: a 30B model at 4-bit holds about 17 GB for as long as its server is up.
If the same box also renders or generates music, that is the collision: stop the model servers
before a music-heavy run, or move them to their own machine. With ACE-Step's language model on
(`ace_step.use_lm`, off by default), set `lm_model_size: "0.6B"` or switch it off.

If audio mixing dies at the end of a long album on a small container, that is the memory limit.
The mixer runs one FFmpeg process per clip and merges in bounded groups; the failure names the clip
and the exit reason.

## FFmpeg not found

FFmpeg is called by name off `PATH`; a missing binary surfaces as
`FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'` the first time a clip is
encoded. `brew install ffmpeg`, `apt install ffmpeg`, or use the Docker image, which has it.

## GPU not detected

The log says `No hardware acceleration detected, using software encoding` and `preflight` reports
no GPU. `immich-memories hardware` shows what it sees. For NVIDIA, `nvidia-smi` must work; in
Docker you need `--gpus all` and the NVIDIA Container Toolkit. Hardware encoders only speed up the
encode, never inference.

## Music generation fails

- Both backends default to `http://localhost:8000`. ACE-Step is treated as up only when
  `/health` returns `{"data": {"status": "ok"}}`; MusicGen only needs HTTP 200.
- A timeout: raise `ace_step.timeout_seconds` (3600) or `musicgen.timeout_seconds` (10800), both
  capped at 18000.
- A failed generator falls back to the next one, then to a bundled track, and the run says so.
