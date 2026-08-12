---
title: Troubleshooting
---

# Troubleshooting

## Connection Refused

```
ConnectionError: Cannot connect to Immich at https://photos.example.com
```

- Double-check your URL. Include the protocol (`https://`). Don't include a trailing slash.
- Verify your API key is correct: **Immich > Account Settings > API Keys**.
- Make sure Immich is actually reachable from wherever you're running this tool. If you're in Docker, `localhost` means the container, not your host machine: use the host's IP or Docker network hostname.

Run the read-only compatibility check before changing anything:

```bash
immich-memories config test
```

It checks authentication and reports the resolved Immich API contract. It does not search,
generate, create an album, or upload a video.

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

The compatibility layer handles the known v2-to-v3 differences: duration strings versus integer
milliseconds, version-specific upload fields, and the UTC offset on search dates. The read-only
`immich-memories config test` reports the server version and authentication errors; it does not test
uploads. If a v3 upload fails, keep the error shown by the command doing the upload and check the
relevant Immich server logs. API keys are redacted.

## Pending Immich Delivery

Run `immich-memories auto status --json` to inspect `pending_delivery_count` and
`oldest_pending_delivery`. The next `auto run` retries the existing artifact and its original album
before cooldown or discovery.

A failed retry exits 1, keeps the run completed and pending, and does not rerender the video. A
missing artifact remains in the pending count but is skipped until the file is restored.

## No Videos Found

- Check the person name matches exactly what Immich has. Face recognition names are case-sensitive.
- If photo support is enabled (`photos.enabled: true` or `--include-photos`), videos and photos compete in a unified selection pool. A time period with only photos is valid and will produce a memory.
- If photo support is **not** enabled, the selected time range must contain at least one video.
- If you're filtering by `--person`, make sure that person has tagged assets in the time range.

## Slow Analysis

Analysis runs at roughly 1-2 minutes per video on CPU. Speed it up:

- **Enable GPU analysis**: Set `hardware.gpu_analysis: true` in your config.
- **Enable downscaling**: Set `analysis.enable_downscaling: true` and `analysis.analysis_resolution: 480`.
- **Reduce keyframe interval**: Lower `analysis.keyframe_interval` to analyze fewer frames per second.

## Out of Memory (OOM)

```
RuntimeError: CUDA out of memory
```

- Reduce `analysis.analysis_resolution` to `360` or `240`.
- If using ACE-Step, switch to `lm_model_size: "0.6B"`.
- If using LLM content analysis, set `content_analysis.frame_max_height: 240`.

## FFmpeg Not Found

```
FileNotFoundError: ffmpeg not found
```

Install it:

- **macOS**: `brew install ffmpeg`
- **Ubuntu/Debian**: `sudo apt install ffmpeg`
- **Docker**: It's already included in the Docker image.

## GPU Not Detected

```
No hardware acceleration available, falling back to CPU
```

- Check your GPU drivers are installed and up to date.
- Run `immich-memories hardware` to see what the tool detects.
- For NVIDIA: make sure `nvidia-smi` works. If not, your drivers aren't set up correctly.
- For Docker: you need `--gpus all` in your `docker run` command and the NVIDIA Container Toolkit installed.

## Music Generation Fails

- Check the music API server is running and reachable.
- For ACE-Step: hit `http://your-server:8000/health` in a browser. Expect `{"data": {"status": "ok"}}`.
- For MusicGen: same thing, check the health endpoint.
- If generation times out, increase `timeout_seconds` in your config. Some tracks take a while on slower GPUs.
