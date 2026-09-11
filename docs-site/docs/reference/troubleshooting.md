---
title: Troubleshooting
---

# Troubleshooting

## Cannot Connect to Immich

Run the read-only compatibility check first. It checks authentication and reports the resolved
Immich API contract. It does not search, generate, create an album, or upload a video:

```bash
immich-memories config test
```

It prints one line and exits 1 on failure:

```
Error: Connection failed: <what the server or the socket returned>
```

`URL not configured` and `API key not configured` instead mean the setting never reached the
process at all.

- Double-check your URL. Include the protocol (`https://`). Don't include a trailing slash.
- Verify your API key is correct: **Immich > Account Settings > API Keys**. A `403 Forbidden` means the key exists but lacks permissions: recreate it with **All** permissions (or the read + upload + album scopes described in the quick start).
- Immich must be **v2 or newer**; Immich 1.x is rejected at connect time (`Unsupported Immich major version 1`).
- Make sure Immich is actually reachable from wherever you're running this tool. If you're in Docker, `localhost` means the container, not your host machine: use the host's IP or Docker network hostname.

## Immich v2/v3 Version Mismatch

Immich Memories supports Immich v2 and v3. The normal configuration is:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

`auto` detects the server major at runtime; you do not pick one for each run. If a reverse proxy
hides or rewrites `/api/server/version`, use `v2` or `v3` as a manual troubleshooting escape hatch.
The override forces that contract, so match the real server major and return to `auto` once
detection works.

Do not flip the override as part of a routine v2-to-v3 upgrade. `auto` is runtime detection; the
manual values exist to diagnose broken version discovery.

The compatibility layer handles the known v2-to-v3 differences: duration strings versus integer
milliseconds, version-specific upload fields, and the UTC offset on search dates. The read-only
`immich-memories config test` reports the server version and authentication errors; it does not test
uploads. If a v3 upload fails, keep the error shown by the command doing the upload and check the
relevant Immich server logs. API keys are redacted.

## No Videos Found

- Check the person name matches what Immich has. The lookup is case-insensitive, but nothing else about it is fuzzy: no partial or first-name match, no accent folding.
- Photos are included by default (`photos.enabled: true`, or `--include-photos` / `--no-photos` per run), and videos and photos compete in one selection pool. A time period with only photos is valid and will produce a memory.
- If you turned photos off, the selected time range must contain at least one video.
- If you're filtering by `--person`, make sure that person has tagged assets in the time range.

## Slow Analysis

The first cut over a period is the slow one: every eligible picture gets its caption, context heads and detector facts prepared once, then the text model reads the period. The levers:

- **Put the caption server on the fast box**: captions are one HTTP request per picture (`editorial.preparation.caption_base_url`, `caption_concurrency`), and the encoder and detectors run where the app runs. A missing producer does not slow the run down: it stops it with a count. See [Editorial annotation setup](../deploy/configuration/editorial-preparation.md).
- **Narrow the period**: the cost is linear in eligible pictures, so a month or a person filter is a fraction of a year. Nobody has timed the cold pass on this route, so start with one month and watch what yours does.
- **Let the store work**: facts are stored per picture and producer in `annotations.sqlite` inside the cache directory, and every reading is banked by its exact request, so the second cut over the same period skips both. Do not clear the cache between runs.
- **Check the text model**: every reading waits on it, so a slow model server dominates the run. The run summary prints the model's call count and time.

## Out of Memory (OOM)

Almost always the reader. It holds roughly 17 GB of weights for as long as its server is up, and
nothing else in this stack is in that class. If the box is also rendering, or also generating
music, that is where the collision is: stop the model servers before a music-heavy run, or move
them to their own machine.

```
CUDA out of memory
```

- If you turned the ACE-Step language model on (`ace_step.use_lm`, off by default), set
  `ace_step.lm_model_size: "0.6B"` or switch it back off.

## FFmpeg Not Found

FFmpeg is called by name off `PATH` and nothing pre-checks it, so a missing binary surfaces as
Python's own error the first time a clip is encoded:

```
FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'
```

Install it:

- **macOS**: `brew install ffmpeg`
- **Ubuntu/Debian**: `sudo apt install ffmpeg`
- **Docker**: It's already included in the Docker image.

## GPU Not Detected

The run log says:

```
No hardware acceleration detected, using software encoding
```

and `immich-memories preflight` reports `Hardware: No GPU acceleration`.

- Check your GPU drivers are installed and up to date.
- Run `immich-memories hardware` to see what the tool detects.
- For NVIDIA: make sure `nvidia-smi` works. If not, your drivers aren't set up correctly.
- For Docker: you need `--gpus all` in your `docker run` command and the NVIDIA Container Toolkit installed.

## Music Generation Fails

- Check the music API server is running and reachable. Both backends default to
  `http://localhost:8000`.
- For ACE-Step: hit `http://your-server:8000/health` in a browser. The backend treats the server as
  up only when the body is `{"data": {"status": "ok"}}`: anything else is logged as unhealthy.
- For MusicGen: the same `/health` route, but it only has to return HTTP 200. The body is read for
  device and status, not gated on.
- If generation times out, raise `timeout_seconds` in the backend's config section
  (`ace_step.timeout_seconds` defaults to 3600, `musicgen.timeout_seconds` to 10800; both cap at
  18000). Some tracks take a while on slower GPUs.
