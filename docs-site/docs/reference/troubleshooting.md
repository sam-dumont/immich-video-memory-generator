---
title: Troubleshooting
---

# Troubleshooting

Reader: anyone whose run stopped.

**Help, in four steps:** read the table below and the [FAQ](./faq.md); check the
[release notes](https://github.com/sam-dumont/immich-video-memory-generator/releases) for your version;
[search the issues](https://github.com/sam-dumont/immich-video-memory-generator/issues?q=is%3Aissue); then open
one with the command, the version (`immich-memories --version`) and the last 50 lines of `-v` output. API keys
are redacted from logs, but check for names before you paste.

Two commands answer most questions: `immich-memories -v <command>` logs at DEBUG for one run, and
`immich-memories preflight` checks Immich, the model files, the output directory and every configured server in
one go. In Docker, prefix both with `docker compose exec immich-memories`.

## When it stops

| What you see | What to do |
|---|---|
| `public heads need the pinned DINOv2 ONNX export at …` | Run `immich-memories models fetch` once. It puts the encoder and detectors on the models volume |
| `nsfw_marqo has no model: …` or `doc_docling has no model: …` | Same: `models fetch` |
| `Output directory is not writable` | In Docker the container runs as uid 1000: `mkdir output` before `up`, or `sudo chown 1000:1000 output` |
| `Story-first selection needs prepared annotations at …` | The annotation store moved. Point `editorial.annotation_database` at it |
| `editorial runtime needs a nonblank LLM model` | `reader: model` with an empty `llm.model`. Set the model, or go back to `reader: auto` |
| `Waiting for the reader at host:port` | A configured model server stopped answering. See [below](#waiting-for-a-model-server) |
| `caption endpoint must advertise smolvlm2-500m-base-public` | Right weights, wrong name: alias it. See [Add captions](../better/captions.md) |
| `caption endpoint failed the compact-v3 schema control` | The server ignores the JSON schema, or it is the wrong model |

## Cannot connect to Immich

The read-only check comes first: authentication and the resolved API contract, nothing searched, generated or
uploaded.

```bash
immich-memories config test
```

It prints one line and exits 1 on failure. `URL not configured` and `API key not configured` mean the setting
never reached the process.

- The URL needs its protocol (`https://`). A trailing slash is tidied up.
- A `403 Forbidden` means the key lacks rights. The scopes are on the [Docker page](../run/docker.md).
- Immich must be v2 or v3. Immich 1.x is refused at connect time.
- In Docker, `localhost` is the container. Use the host's IP or the Docker network name.

## Immich v2/v3 version mismatch

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

`auto` detects the server major at runtime; you do not pick one for each run. So a v2-to-v3 upgrade needs no
change here. If a reverse proxy hides or rewrites `/api/server/version`, use `v2` or `v3` as a manual
troubleshooting escape hatch. The override forces that contract, so go back to `auto` once detection works.

The read-only `immich-memories config test` reports the server version and authentication errors; it does not
test uploads. If a v3 upload fails, keep the error shown by the command doing the upload and check the relevant
Immich server logs. API keys are redacted.

## No videos found

- The person name must match Immich's exactly, case aside. `immich-memories people` lists them.
- Photos are in the pool by default (`photos.enabled: true`); with photos off, the period needs at least one
  video.
- A `--person` filter needs pictures where Immich recognised that face in the period.

## A picture I expected is not in the cut

```bash
immich-memories runs why <asset id> --run <run id>
```

says where it passed and where it was dropped, and why. To overrule it, tick it on the web UI's pool and
**Cut again**, or pass `--include <asset id>`: a tick outranks the editor. Neither overrides the family-viewing
gate, and neither brings back a picture whose preview Immich answers HTTP 404 for. The run logs those as
`preview unavailable at Immich (HTTP 404)` and cuts the rest; regenerate that asset's thumbnails in Immich and
cut again. Every lever is on [Overrule it](../how-it-chooses/overrule-it.md).

## The first cut is slow

A cut prepares the pictures it can reach once (previews, pixel facts, heads, detectors, and on the `full` tier a
caption each) and banks them. The second cut over the same period is mostly the render. The levers, in order:
keep the cache volume, prepare ahead with [`prepare`](../make/cli/prepare.md) overnight, and move the heads to a
faster box with [the inference service](../better/inference.md). Numbers per host are on
[Measured](../better/measured.md).

## Waiting for a model server

Only with a reader or caption server configured. The run names the endpoint and retries three times, two then
four seconds apart, then fails. Start the server or fix `llm.base_url`, then **Cut again** or rerun: everything
already read is banked. To cut without it, clear `llm.model` with `reader: auto` (the NAS path).

## A clip fails with "Could not write header (incorrect codec parameters ?)"

The source is VP9 or AV1 inside a QuickTime `.MOV` (Android phones and some editors write those), and a lossless
stream copy into `.mov` is refused. Nothing to do: the cut re-encodes that clip with the same in and out points.
A clip that still cannot be cut leaves the film by name and the rest is assembled. Only a film whose every
source failed stops with `No clips could be processed`.

## A long render spends a while "Checking the finished film" {#a-long-render-ends-with-ffprobe-failed-to-inspect-output-artifact}

Before a film gets its final name, FFmpeg decodes every frame once, on every core, and fails the run on any
decode error. It can take as long as the encode did, and logs
`Checking the finished film: 12:34 of 1:14:46 decoded` once a minute. That is normal on a NAS with a long film.

If it runs out of time the error reads `the decode check did not finish within the render's own encode time`
and names the file. Nothing deletes it. Check it yourself:

```bash
ffmpeg -v error -i memory.assembling.mp4 -map 0:v:0 -f null -
```

No output means every frame decoded: rename it without `.assembling` and upload it by hand (the music is
already in it).

## Out of memory

On a NAS, it is almost always a long film's audio mix on a small container: the mixer runs one FFmpeg per clip
and the failure names the clip. Raise the container's memory limit.

With a model on the same box, it is the model: the default Gemma 4 E4B holds about 7 GB and the optional 30B about 17 GB for as long as its server
is up, and ACE-Step in `lib` mode refuses a render it cannot hold. Stop the model servers before a music-heavy
run, or give them their own machine.

## FFmpeg not found

The app calls FFmpeg by name off `PATH`, so a missing binary surfaces as
`FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'` at the first encode. `brew install ffmpeg`,
`apt install ffmpeg`, or use the Docker image.

## GPU not detected

The log says `No hardware acceleration detected, using software encoding`, and `immich-memories hardware` shows
what it sees. For NVIDIA, `nvidia-smi` must work, and Docker needs the NVIDIA Container Toolkit and the GPU
overlay. A hardware encoder only speeds up the encode. See [Hardware encoding](../run/hardware.md).

## Music generation fails

A failed generator falls back to the next one, then to a bundled track, and the finished run says so. ACE-Step
counts as up only when `/health` returns `{"data": {"status": "ok"}}`; MusicGen needs HTTP 200. For timeouts,
raise `ace_step.timeout_seconds` (3600) or `musicgen.timeout_seconds` (10800), both capped at 18000. Setup is on
[Generated music](../better/music.md).
