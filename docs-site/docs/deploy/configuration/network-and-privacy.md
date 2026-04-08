---
sidebar_position: 10
title: Network & Privacy
---

# Data Leaving Your Network

Immich Memories runs locally and connects to your Immich server over your LAN. But some features make outbound requests to external services. Here's exactly what gets sent and when.

## Always-on (when features are used)

### Nominatim geocoding API

**When:** Trip detection, title screen location names.

**What's sent:** GPS latitude/longitude from your asset metadata.

**Destination:** `nominatim.openstreetmap.org` (OpenStreetMap's public geocoding service).

**Why:** Reverse-geocodes coordinates to city/country names for trip titles and location cards.

**Opt out:** Don't use trip memory type, or enable privacy mode (relocates coordinates to fake cities before geocoding).

### Map tile servers

**When:** Title screens with satellite map fly-over animations.

**What's sent:** Tile requests for a geographic bounding box (standard map tile URLs with x/y/z coordinates).

**Destination:** OpenStreetMap (`tile.openstreetmap.org`) and/or ArcGIS World Imagery (`server.arcgisonline.com`).

**Why:** Renders the animated satellite map that appears in trip title screens.

**Opt out:** Disable title screens (`title_screens.enabled: false`), or enable privacy mode (requests tiles for fake locations).

## Optional (user-configured)

### LLM vision API

**When:** Content analysis is enabled (`content_analysis.enabled: true`) or title generation uses an LLM.

**What's sent:** Video frame thumbnails (JPEG, downscaled to `frame_max_height`, default 480px) with a scoring prompt.

**Destination:** Whatever `llm.base_url` points to. If you run a local model (mlx-vlm, Ollama, vLLM), nothing leaves your network.

**Opt out:** Don't configure the `llm` section, or point it at a local model.

### Music generation API

**When:** AI music generation is used (`audio.music_source: "musicgen"` or `"ace_step"`).

**What's sent:** A text prompt describing the desired mood, tempo, and genre. No video frames or personal data.

**Destination:** Whatever `musicgen.base_url` or `ace_step.api_url` points to. If you run the music server on your own hardware, nothing leaves your network.

**Opt out:** Use `audio.music_source: "local"` for local music files, or `--no-music`.

## CI only

### PyPI (pip-audit)

**When:** CI runs `make pip-audit` to check for dependency vulnerabilities.

**What's sent:** Package name + version queries.

**Destination:** `pypi.org`.

**Not relevant at runtime** -- this only runs in the CI pipeline, not when you use the tool.

## Privacy mode

When privacy mode is on (`--privacy-mode` or `server.enable_demo_mode: true`), outbound requests for geocoding and map tiles still happen, but they use fake GPS coordinates. The real locations never leave your machine. See [Privacy Mode](../../create/pipeline/privacy-mode.md) for details.
