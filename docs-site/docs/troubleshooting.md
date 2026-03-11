---
sidebar_position: 98
title: Troubleshooting
---

# Troubleshooting

## Connection Refused

```
ConnectionError: Cannot connect to Immich at https://photos.example.com
```

- Double-check your URL. Include the protocol (`https://`). Don't include a trailing slash.
- Verify your API key is correct: **Immich > Account Settings > API Keys**.
- Make sure Immich is actually reachable from wherever you're running this tool. If you're in Docker, `localhost` means the container, not your host machine — use the host's IP or Docker network hostname.

## No Videos Found

- Check the person name matches exactly what Immich has. Face recognition names are case-sensitive.
- Check the time period actually contains videos. A year with only photos won't produce anything.
- If you're not using `--person`, make sure there are videos (not just photos) in the selected time range.

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
- For ACE-Step: hit `http://your-server:8000/health` in a browser. You should see `{"data": {"status": "ok"}}`.
- For MusicGen: same thing, check the health endpoint.
- If generation times out, increase `timeout_seconds` in your config. Some tracks take a while on slower GPUs.
