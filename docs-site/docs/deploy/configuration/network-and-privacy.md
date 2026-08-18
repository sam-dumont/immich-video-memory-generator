---
sidebar_position: 10
title: Network & Privacy
---

# Data Leaving Your Network

Immich Memories runs locally and talks to your Immich server over your LAN. There is no
telemetry, no update check and no analytics. Some features do make outbound requests, though.
This page lists every one of them, what is sent, and how to turn it off. It is generated from a
sweep of the source code and is kept in sync with releases; if you find an outbound call that is
not listed here, [open an issue](https://github.com/sam-dumont/immich-video-memory-generator/issues).

## Summary table

| Destination | When | What leaves your network | Off by default? | Opt out |
|---|---|---|---|---|
| Your Immich server | always | asset metadata, thumbnails/originals **down**; finished video + album create **up** only if upload-back is on | reads: no · upload: **yes** | `upload.enabled: false` (default) |
| `nominatim.openstreetmap.org` | trip detection and trip titles | real GPS of trip clusters (lat/lon → place name) | no (runs when trips are detected) | don't use the Trip type; see below |
| `server.arcgisonline.com` (World Imagery) | satellite map title screens | tile x/y/z requests for the trip area and your home base | no | `title_screens.enabled: false` |
| `cdn.jsdelivr.net` (Fontsource) | first map / GPU title render **only if** the configured font is not bundled or cached | nothing personal (a font file is downloaded) | n/a | keep the default bundled font (Montserrat); pre-place TTFs in `~/.immich-memories/fonts/` |
| `llm.base_url` | content analysis, mood detection, LLM titles | frame thumbnails, photos, and for titles: **person names, place names, dates, clip descriptions** | **yes** | leave `llm` unconfigured, or point it at a local model |
| `ace_step.api_url` / `musicgen.base_url` | AI music via a remote API | mood/genre/tempo text; a generated WAV for stem separation (MusicGen path) | **yes** | in-process ACE-Step (`ace_step.mode: lib`), your own file with `--music`, or `--no-music` |
| Hugging Face / torch hub / Zenodo | first use of ACE-Step, Demucs, whisper.cpp, PANNs, smart-turn | nothing personal (model weights are downloaded once) | features are opt-in | pre-download models; air-gapped installs should disable those features |
| Your Apprise / ntfy targets | notifications | memory type, status, duration, output path, error tail; a JPEG frame if `attach_thumbnail: true` | **yes** | `notifications.enabled: false` (default) |
| Your OIDC provider | login | standard OIDC flow (client id, PKCE, tokens) | **yes** | basic auth or trusted-header auth |

Everything below `LLM vision API` is optional and off unless you configure it.

## Details

### Nominatim geocoding

**When:** trip detection (`analysis/trip_detection.py`) reverse-geocodes each detected trip
cluster so trips get names, and trip title screens use those names.

**What's sent:** the real latitude/longitude of the trip's centroid(s). Home-base coordinates
are only used for the map animation, not geocoded.

**Opt out:** don't use the Trip memory type. Disabling title screens does **not** stop trip
detection from geocoding. Privacy mode does **not** change these coordinates (see below).

### Map tiles (satellite)

**When:** the animated fly-in of a trip title screen (`title_screens.enabled: true`).

**What's sent:** standard tile URLs (`{z}/{y}/{x}`) covering the trip area and the route from
your home base. Hundreds of tile requests per animated title. Only ArcGIS World Imagery is used
today; the OSM/OpenTopo styles in the renderer are not reachable from the config.

**Opt out:** `title_screens.enabled: false`.

### Fonts (jsdelivr / Fontsource)

**When:** a map or GPU-rendered title needs a font family that is neither bundled in the wheel
(Montserrat is) nor already present under `~/.immich-memories/fonts/`. Then a `latin-<weight>`
TTF is fetched from `cdn.jsdelivr.net/fontsource/fonts/<family>@latest`.

**What's sent:** nothing about your library. Note the file is unpinned (`@latest`).

**Opt out:** keep the default font, or drop the TTFs you want into `~/.immich-memories/fonts/<Family>/`.

### LLM vision API

**When:** `content_analysis.enabled: true` (clip scoring), mood detection, or LLM-written
titles.

**What's sent:**
- Video frame thumbnails (JPEG, downscaled to `frame_max_height`, default 480px) and whole
  photos, with a scoring prompt.
- For titles: the **person names, city/place names, dates and clip descriptions** the title is
  written from.
- `immich-memories preflight` sends one small test completion to verify the endpoint.

**Destination:** whatever `llm.base_url` points to. With a local model (mlx-vlm/omlx, Ollama,
vLLM) nothing leaves your network. The `openai-compatible` provider defaults to
`https://api.openai.com/v1` if you set a key but no `base_url` — set `base_url` explicitly.

**Opt out:** don't configure `llm`, or point it at a local server.

### Music generation

**When:** `ace_step.enabled: true` in API mode (`ace_step.mode: api`), or `musicgen.enabled: true`.

**What's sent:** a text prompt (mood, tempo, genre, optional lyrics). The MusicGen path also
uploads the *generated* track for stem separation. No frames, no personal data.

**Opt out:** run ACE-Step in-process (`ace_step.mode: lib`), pass your own track with
`--music path.mp3` (or the Upload option in the UI), or `--no-music`.

### Model downloads

First use of an optional ML feature downloads its weights once: ACE-Step (Hugging Face),
Demucs (torch hub), whisper.cpp models for transcription (Hugging Face), PANNs audio tagging
(Zenodo), smart-turn (Hugging Face). FireRedVAD is bundled. Nothing about your library is sent;
weights are cached under the respective library's cache directory. Air-gapped installs should
pre-seed those caches or leave the features off.

### Notifications (Apprise / ntfy)

**When:** `notifications.enabled: true`.

**What's sent:** memory type, outcome, duration, the absolute output path and a redacted error
tail. With `notifications.attach_thumbnail: true`, a JPEG frame from the finished video is
attached — think about who runs your notification service (ntfy.sh, Discord, Telegram…) before
turning that on.

## Privacy mode

Privacy mode (`--privacy-mode` / `server.enable_demo_mode: true`) is a **demo/screenshot**
feature: it blurs faces, muffles speech and shifts your *home base* to a fake city so the map
fly-in does not start at your house. It does **not** fake the destination coordinates — trip
detection and titles still geocode and render the real place, because that is the point of a
trip memory. See [Privacy Mode](../../create/pipeline/privacy-mode.md).

## CI only

`make pip-audit` queries `pypi.org` for known vulnerabilities in the dependency lockfile. It
runs in CI, never at runtime.
