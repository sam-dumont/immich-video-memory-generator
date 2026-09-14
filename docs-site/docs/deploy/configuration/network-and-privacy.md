---
sidebar_position: 10
title: Network & Privacy
---

# Data leaving your network

The app has no runtime telemetry, analytics or update check. It contacts the services below;
whether requests leave your network depends on where those services run.

| Destination | When | What leaves your network | Opt out |
|---|---|---|---|
| Your Immich server | always | metadata, previews and originals down; the finished video and an album up, only with upload-back | `upload.enabled: false` (default) |
| `nominatim.openstreetmap.org` | named trip detection, including automation | real GPS of each trip cluster's centroid, for a place name | avoid trip generation/detection and set `automation.detect_trips: false` |
| `server.arcgisonline.com` (World Imagery) | the map fly-in of a trip title | tile requests covering the trip area and your home base | `title_screens.enabled: false` |
| `cdn.jsdelivr.net` (Fontsource) | a supported font family is missing from the bundled and local font directories | a font file, unpinned (`@latest`) | keep a bundled family (Josefin Sans, Montserrat, Outfit, Quicksand, Raleway) or drop TTFs in that folder |
| `editorial.preparation.caption_base_url` | the first cut over a period, `full` tier | a 400 px JPEG of every eligible picture, once, a `/models` probe, and `caption_api_key` as a bearer token when one is set | `tier: no_captions`, or a server on your own network (default `localhost:8092`) |
| `llm.base_url` | model reader and optional title generation | 800 px candidate tiles and annotation lines, including people and place names; title prompts carry names, places, dates and descriptions | `reader: rules` stops reader calls; also disable LLM titles to avoid title calls |
| `title_llm.base_url` | optional title generation when `title_llm.model` is set | names, places, dates and descriptions | leave its model blank and turn off LLM title generation, or use a local endpoint |
| `advanced.inference.facts_base_url` | preparation, when set | each picture's preview, for the heads and detectors | leave it unset: the app runs them itself |
| `ace_step.api_url`, `musicgen.base_url` | AI music through a remote API | mood, tempo, genre text; MusicGen also uploads the generated track for stem separation | `ace_step.mode: lib`, your own file with `--music`, or `--no-music` |
| Hugging Face, torch hub, `github.com` | `models fetch`; first use of ACE-Step or Demucs | nothing about your library; weights are downloaded once | pre-seed the caches for an air-gapped box |
| Your Apprise or ntfy targets | notifications | memory type, outcome, duration, output path, a redacted error tail; a JPEG frame if `attach_thumbnail: true` | `notifications.enabled: false` (default) |
| Your OIDC provider | login | the standard OIDC flow with PKCE | basic auth or the trusted-header provider |

Two provider names fill in a vendor URL when `llm.base_url` is left at its default: `openai`
(`https://api.openai.com/v1`) and `zai` (`https://api.z.ai/api/paas/v4`). `preflight` may send a small test completion to the configured reader; the Ollama-specific check
uses its model inventory instead. Rules mode skips the reader check.

For z.ai, the `base_url` selects the API dialect: `/api/anthropic` uses the Anthropic adapter;
`/api/paas/v4` uses the OpenAI-compatible adapter. Provider errors appear in logs with a bounded
code and message. Reasoning support varies by model; use the provider's supported settings.

## The two picture seats

| Seat | Setting | What it is shown |
|---|---|---|
| reader | `llm.base_url` | 800 px tiles, and the annotation lines beside them with the names of people and places |
| captioner | `editorial.preparation.caption_base_url` | 400 px tiles, no metadata, and `caption_api_key` if set |

Both default to this machine. Pointing either at another host (a box on your LAN, a container, a
hosted endpoint) sends that host the picture bytes and prompts. The app does not ask again; the receiving
service decides what it retains or logs.

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

`--privacy-mode` is a demo and screenshot feature. In the UI, `server.enable_demo_mode: true`
shows the privacy toggle; it does not activate it. When enabled, privacy mode blurs
every frame of every clip, makes clip audio unintelligible, replaces person names, and moves home
base and destination onto a fake city while preserving relative directions and compressing wide trips into the fake city's area.
It does not reach the geocoding, which already ran, nor the output file name, which is built
first. See [Privacy mode](../../create/pipeline/privacy-mode.md).

Unknown font families are not downloaded automatically; the downloader accepts only its built-in
font list.

## CI only

`make pip-audit` queries `pypi.org` for known vulnerabilities in the lock file. It runs in CI,
never at runtime.
