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

## A long render ends with "ffprobe failed to inspect output artifact"

The film was fine. Before this fix, the app checked a finished film with one `ffprobe -count_frames`
call, which decodes every frame on a single thread, and gave up after 15 minutes. That is 2.25x
realtime on a Celeron J4125 whatever its core count, so a 75-minute album needs about 33 minutes.
The render that had just spent 11 hours encoding was marked failed and its upload skipped, while
`ffprobe -show_entries format=duration` on the `.assembling.mp4` beside the run answered at once.

The check now has two steps:

1. `ffprobe` reads container, codec, pixel format, colour and duration without decoding and
   compares them with the encoding plan. A wrong codec fails here, in under a second.
2. FFmpeg decodes the video on every core and fails the run on any decode error. It may take as
   long as the render's own encode (15 minutes at least), and logs
   `Checking the finished film: 12:34 of 1:14:46 decoded` once a minute. A film a
   [render worker](../deploy/running-modes.md#rendering-on-another-machine) made gets four times
   its duration instead.

Measured on a Celeron J4125 with two cores, the decode runs at 3.8x realtime for 1080p HEVC
(a 75-minute film in about 20 minutes) and 1.07x for 4K.

If it still runs out, the error reads `the decode check did not finish within the render's own
encode time` and starts with the path of the film. Nothing deletes that file. Check it yourself:

```bash
ffmpeg -v error -i memory.assembling.mp4 -map 0:v:0 -f null -
```

No output means every frame decoded. Rename it without `.assembling` and upload it by hand.

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
