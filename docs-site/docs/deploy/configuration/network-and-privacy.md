---
sidebar_position: 10
title: Network & Privacy
---

# Data leaving your network

The app runs locally and talks to your Immich server over your LAN. No telemetry, no update check,
no analytics. Some features make outbound requests; this page lists every one, what is sent and
how to turn it off. It comes from a sweep of the source and is kept by hand: if you find a call
that is not here, [open an issue](https://github.com/sam-dumont/immich-video-memory-generator/issues).

| Destination | When | What leaves your network | Opt out |
|---|---|---|---|
| Your Immich server | always | metadata, previews and originals down; the finished video and an album up, only with upload-back | `upload.enabled: false` (default) |
| `nominatim.openstreetmap.org` | trip detection | the real GPS of each trip cluster's centroid, for a place name | do not use the Trip type |
| `server.arcgisonline.com` (World Imagery) | the map fly-in of a trip title | tile requests covering the trip area and your home base | `title_screens.enabled: false` |
| `cdn.jsdelivr.net` (Fontsource) | a title needs a font that is neither bundled nor in `~/.immich-memories/fonts/` | a font file, unpinned (`@latest`) | keep a bundled family (Josefin Sans, Montserrat, Outfit, Quicksand, Raleway) or drop TTFs in that folder |
| `editorial.preparation.caption_base_url` | the first cut over a period, `full` tier | a 400 px JPEG of every eligible picture, once, a `/models` probe, and `caption_api_key` as a bearer token when one is set | `tier: no_captions`, or a server on your own network (default `localhost:8092`) |
| `llm.base_url` | the reader | 800 px tiles of a few dozen candidates and their annotation lines, which carry people and place names; for titles, names, places, dates and descriptions | `reader: rules`, or a local model (default `localhost:8080`, the app's own port, so set it) |
| `api.anthropic.com` | the reader, with `provider: anthropic` and no `base_url` of your own | the same tiles and lines, to Anthropic | name a host of your own in `llm.base_url` |
| `api.z.ai` | the reader, with `provider: zai` and no `base_url` of your own | the same tiles and lines, to z.ai | name a host of your own in `llm.base_url` |
| `advanced.inference.facts_base_url` | preparation, when set | each picture's preview, for the heads and detectors | leave it unset: the app runs them itself |
| `ace_step.api_url`, `musicgen.base_url` | AI music through a remote API | mood, tempo, genre text; MusicGen also uploads the generated track for stem separation | `ace_step.mode: lib`, your own file with `--music`, or `--no-music` |
| `llm.base_url` | automatic music selection and music preview, when cut text and a model are available | the saved cut's thesis, ordered story labels and prepared captions for kept pictures; no images | your own track, `--no-music`, or a local text model |
| `llm.base_url` | special-day scan | prepared captions with capture times, places and recognised names; sampled frames for any day the captions do not cover | a local model |
| Hugging Face, torch hub, `github.com` | `models fetch`; first use of ACE-Step or Demucs | nothing about your library; weights are downloaded once | pre-seed the caches for an air-gapped box |
| Your Apprise or ntfy targets | notifications | memory type, outcome, duration, output path, a redacted error tail; a JPEG frame if `attach_thumbnail: true` | `notifications.enabled: false` (default) |
| Your OIDC provider | login | the standard OIDC flow with PKCE | basic auth or the trusted-header provider |

Three provider names fill in a vendor URL when `llm.base_url` is left at its default: `openai`
(`https://api.openai.com/v1`), `anthropic` (`https://api.anthropic.com`) and `zai`
(`https://api.z.ai/api/anthropic`). Set `base_url` yourself and the request goes where you point it,
whichever provider is named; which dialect each one speaks is on
[LLM Titles and Mood](../../create/pipeline/llm-content-analysis.md). `preflight` asks whatever the
reader URL is for its model list, and sends one small test call when the host does not publish one.

## The two picture seats

Music selection reads the cut's text by default. An unavailable text model or an unusable answer
falls back to defaults; it does not send pictures instead. The answer is banked separately from
selection, and `runs why <asset-id> --run <run-id>` reports the music route. Standalone
`music add --analyze-frames` and `music analyze` explicitly send sampled frames to the configured
vision provider.

Special-day scans prefer the configured producer's prepared captions, and only for a day the
captions actually cover. The described pictures have to clear the same bar the day itself had to
clear to be worth asking about: 20 of them across 6 hours of the clock. Below that the day keeps
the frame fallback. Above it the day's tiles are never downloaded at all. Once a day takes the
caption route, a failed text call never switches it to vision.

| Seat | Setting | What it is shown |
|---|---|---|
| reader | `llm.base_url` | 800 px tiles, and the annotation lines beside them with the names of people and places |
| captioner | `editorial.preparation.caption_base_url` | 400 px tiles, no metadata, and `caption_api_key` if set |

Both default to this machine. Pointing either at another host (a box on your LAN, a container, a
hosted endpoint) is the consent step: those bytes go onto its disk and into its logs, and nothing
asks a second time.

## Geocoding and maps

Trip detection reverse-geocodes each cluster's centroid so trips get names; home-base coordinates
are used for the map animation only, never geocoded. Disabling title screens does not stop the
geocoding, and privacy mode does not change it either: detection runs before anonymisation. The
map fly-in requests hundreds of World Imagery tiles per animated title; in privacy mode the tiles
cover the fake city.

## Thumbnails inside the web UI

Every thumbnail is an `<img>` the browser fetches from the app at `/media/thumb/<asset id>` on the
same port, served from the cache the analysis already filled. The route answers only for assets the
current session prepared, sits behind the same login as every page, and derives a 320 px grid
thumbnail from the cached preview on first request. Privacy-mode blur applies to it.

## Privacy mode

`--privacy-mode` (or `server.enable_demo_mode: true`) changes what the film shows, not what the app
sends. Trip detection and its geocoding have already run by then, and the map still fetches its
tiles. See [Privacy mode](../../create/pipeline/privacy-mode.md).

## CI only

`make pip-audit` queries `pypi.org` for known vulnerabilities in the lock file. It runs in CI,
never at runtime.
