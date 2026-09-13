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
| `advanced.inference.facts_base_url` | preparation, when set | each picture's preview, for the heads and detectors | leave it unset: the app runs them itself |
| `ace_step.api_url`, `musicgen.base_url` | AI music through a remote API | mood, tempo, genre text; MusicGen also uploads the generated track for stem separation | `ace_step.mode: lib`, your own file with `--music`, or `--no-music` |
| Hugging Face, torch hub, `github.com` | `models fetch`; first use of ACE-Step or Demucs | nothing about your library; weights are downloaded once | pre-seed the caches for an air-gapped box |
| Your Apprise or ntfy targets | notifications | memory type, outcome, duration, output path, a redacted error tail; a JPEG frame if `attach_thumbnail: true` | `notifications.enabled: false` (default) |
| Your OIDC provider | login | the standard OIDC flow with PKCE | basic auth or the trusted-header provider |

Two provider names fill in a vendor URL when `llm.base_url` is left at its default: `openai`
(`https://api.openai.com/v1`) and `zai` (`https://api.z.ai/api/paas/v4`). `preflight` sends one
small test completion to whatever the reader URL is.

z.ai serves two dialects on one host, so `provider: zai` routes on the path of the `base_url` you
set: `.../api/anthropic` takes the Anthropic adapter and its `/v1/messages`, and anything else
(including the preset `.../api/paas/v4`) takes the OpenAI-compatible one. Send the OpenAI path to
the Anthropic base and the reply is an HTTP 200 carrying `{"code":500,"msg":"404 NOT_FOUND"}`,
which the reader now reports by its code and message instead of a bare `KeyError`.

A named provider's own reasoning switch is sent whatever else you put in `thinking_params` or
`no_thinking_params`. z.ai wants `thinking: {"type": "disabled"}` on a fast call; replacing the
block with a Qwen-shaped one used to drop that switch, and GLM then reasoned through the whole
bulk pass.

## The two picture seats

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

`--privacy-mode` (or `server.enable_demo_mode: true`) is a demo and screenshot feature: it blurs
every frame of every clip, makes clip audio unintelligible, replaces person names, and moves home
base and destination onto a fake city while keeping the spacing so the map still reads as a trip.
It does not reach the geocoding, which already ran, nor the output file name, which is built
first. See [Privacy mode](../../create/pipeline/privacy-mode.md).

## CI only

`make pip-audit` queries `pypi.org` for known vulnerabilities in the lock file. It runs in CI,
never at runtime.
