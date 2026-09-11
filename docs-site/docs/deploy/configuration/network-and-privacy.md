---
sidebar_position: 10
title: Network & Privacy
---

# Data Leaving Your Network

Immich Memories runs locally and talks to your Immich server over your LAN. There is no
telemetry, no update check and no analytics. Some features do make outbound requests, though.
This page lists every one of them, what is sent, and how to turn it off. It is written from a sweep
of the source code and maintained by hand; no gate checks it, so if you find an outbound call that
is not listed here, [open an issue](https://github.com/sam-dumont/immich-video-memory-generator/issues).

## Summary table

| Destination | When | What leaves your network | Off by default? | Opt out |
|---|---|---|---|---|
| Your Immich server | always | asset metadata, thumbnails/originals **down**; finished video + album create **up** only if upload-back is on | reads: no · upload: **yes** | `upload.enabled: false` (default) |
| `nominatim.openstreetmap.org` | trip detection and trip titles | real GPS of trip clusters (lat/lon → place name) | no (runs when trips are detected) | don't use the Trip type; see below |
| `server.arcgisonline.com` (World Imagery) | satellite map title screens | tile x/y/z requests for the trip area and your home base | no | `title_screens.enabled: false` |
| `cdn.jsdelivr.net` (Fontsource) | first map / GPU title render **only if** the configured font is not bundled or cached | nothing personal (a font file is downloaded) | n/a | keep the default bundled font (Montserrat); pre-place TTFs in `~/.immich-memories/fonts/` |
| `editorial.preparation.caption_base_url` | the first cut over a period | **a 400 px JPEG of every eligible picture in the period**, plus a `/models` probe | no: selection requires it | point it at a server on your own network (the default is `localhost:8092`) |
| `llm.base_url` | the editor's readings, mood detection, LLM titles | frame thumbnails, photos, and for titles: **person names, place names, dates, clip descriptions** | **yes** | leave `llm` unconfigured, or point it at a local model |
| `ace_step.api_url` / `musicgen.base_url` | AI music via a remote API | mood/genre/tempo text; a generated WAV for stem separation (MusicGen path) | **yes** | in-process ACE-Step (`ace_step.mode: lib`), your own file with `--music`, or `--no-music` |
| Hugging Face / torch hub, and `github.com` for the pinned encoder | `models fetch`, and first use of ACE-Step or Demucs | nothing personal (model weights are downloaded once) | features are opt-in | pre-download models; air-gapped installs should disable those features |
| Your Apprise / ntfy targets | notifications | memory type, status, duration, output path, error tail; a JPEG frame if `attach_thumbnail: true` | **yes** | `notifications.enabled: false` (default) |
| Your OIDC provider | login | standard OIDC flow (client id, PKCE, tokens) | **yes** | basic auth or trusted-header auth |

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

**When:** a map or GPU-rendered title needs a font family that is neither bundled in the wheel nor
already present under `~/.immich-memories/fonts/`. Five families ship in the wheel (Josefin Sans,
Montserrat, Outfit, Quicksand and Raleway), which covers every built-in theme, so this only fires
if you configure a family of your own. Then a `latin-<weight>` TTF is fetched from
`cdn.jsdelivr.net/fontsource/fonts/<family>@latest`.

**What's sent:** nothing about your library. Note the file is unpinned (`@latest`).

**Opt out:** keep the default font, or drop the TTFs you want into `~/.immich-memories/fonts/<Family>/`.

### LLM vision API

**When:** `llm` is configured: the editor's readings of a period, mood detection, or
LLM-written titles.

**What's sent:**
- For the editor: the period's picture captions and metadata with the editing prompts; contact
  sheets of the pictures where a reading asks to see them. See
  [Editorial annotation setup](./editorial-preparation.md).
- For mood detection: video keyframes.
- For titles: the **person names, city/place names, dates and clip descriptions** the title is
  written from.
- `immich-memories preflight` sends one small test completion to verify the endpoint.

**Destination:** whatever `llm.base_url` points to. With a local model (mlx-vlm/oMLX, Ollama,
vLLM) nothing leaves your network. `openai-compatible` defaults to `http://localhost:8080/v1`,
which is the app's own port. Set it. Two provider names fill in a vendor's URL instead when you
leave `base_url` at that default: `openai` → `https://api.openai.com/v1`, `zai` →
`https://api.z.ai/api/paas/v4`.

**Opt out:** don't configure `llm`, or point it at a local server.

### Music generation

**When:** `ace_step.enabled: true` in API mode (`ace_step.mode: api`), or `musicgen.enabled: true`.

**What's sent:** a text prompt (mood, tempo, genre, optional lyrics). The MusicGen path also
uploads the *generated* track for stem separation. No frames, no personal data.

**Opt out:** run ACE-Step in-process (`ace_step.mode: lib`), pass your own track with
`--music path.mp3` (or the Upload option in the UI), or `--no-music`.

### Model downloads

First use of an optional ML feature downloads its weights once: ACE-Step (Hugging Face),
Demucs (torch hub), the `editorial` extra's detector weights (Hugging Face). Nothing about your
library is sent;
weights are cached under the respective library's cache directory. Air-gapped installs should
pre-seed those caches or leave the features off.

### Notifications (Apprise / ntfy)

**When:** `notifications.enabled: true`.

**What's sent:** memory type, outcome, duration, the absolute output path and a redacted error
tail. With `notifications.attach_thumbnail: true`, a JPEG frame from the finished video is
attached. Think about who runs your notification service (ntfy.sh, Discord, Telegram…) before
turning that on.

## Privacy mode

Privacy mode (`--privacy-mode` / `server.enable_demo_mode: true`) is a **demo/screenshot**
feature: it blurs every frame of every clip (not faces, the whole picture), makes all clip audio
unintelligible, replaces person names, and shifts your *home base* to a fake city so the map
fly-in does not start at your house. It does **not** fake the destination coordinates: trip
detection and titles still geocode and render the real place, because that is the point of a
trip memory. See [Privacy Mode](../../create/pipeline/privacy-mode.md).

## CI only

`make pip-audit` queries `pypi.org` for known vulnerabilities in the dependency lockfile. It
runs in CI, never at runtime.
