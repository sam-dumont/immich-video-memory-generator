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
| `llm.base_url` | special-day scan | prepared captions with capture times, places and recognised names; sampled frames only when the day has no prepared captions | a local model |
| Hugging Face, torch hub, `github.com` | `models fetch`; first use of ACE-Step or Demucs | nothing about your library; weights are downloaded once | pre-seed the caches for an air-gapped box |
| Your Apprise or ntfy targets | notifications | memory type, outcome, duration, output path, a redacted error tail; a JPEG frame if `attach_thumbnail: true` | `notifications.enabled: false` (default) |
| Your OIDC provider | login | the standard OIDC flow with PKCE | basic auth or the trusted-header provider |

Three provider names fill in a vendor URL when `llm.base_url` is left at its default: `openai`
(`https://api.openai.com/v1`), `anthropic` (`https://api.anthropic.com`) and `zai`
(`https://api.z.ai/api/anthropic`). `preflight` asks whatever the reader URL is for its model list,
and sends one small test call when the host does not publish one.

`provider: anthropic` is the Messages API and reaches any host that serves it, Claude included:
`POST {base_url}/v1/messages` with `x-api-key`, `anthropic-version: 2023-06-01`, the prompt and the
reader's 800 px tiles as base64 `image` blocks. The key comes from `ANTHROPIC_API_KEY` as well as
`OPENAI_API_KEY`, and the provider you configured decides which of the two wins when both are set.

z.ai serves two dialects on one host, so `provider: zai` routes on the path of the `base_url` you
set: `.../api/anthropic` takes the Anthropic adapter and its `/v1/messages`, and `.../api/paas/v4`
takes the OpenAI-compatible one. Send the OpenAI path to the Anthropic base and the reply is an
HTTP 200 carrying `{"code":500,"msg":"404 NOT_FOUND"}`, which the reader reports by its code and
message instead of a bare `KeyError`.

A named provider's own reasoning switch fills in beside whatever else you put in
`thinking_params` or `no_thinking_params`: replacing the block with a Qwen-shaped one used to drop
that switch, and GLM then reasoned through the whole bulk pass. A `thinking` key you write
yourself wins over the preset's, because z.ai's switch is a level rather than an on and off:
`disabled`, `low`, `high` or `max`. The GLM-5 line reasons unconditionally and answers `disabled`
with HTTP 400 code 1210, so the preset sends `low` there and `disabled` on the lines that take it.
A model neither list has heard of that refuses the same way is retried once at `low`, and the log
says what changed.

That refusal only ever comes from `/api/paas/v4`. The `.../api/anthropic` route answers HTTP 200
to every setting, including `disabled` and levels it has never heard of, and then reasons or does
not on its own terms. Measured on 2026-09-14 with `glm-5.3-flash`, a caption-shaped ask at the
140-token cap the readers use spent all 140 tokens inside a `thinking` block and came back with no
answer in it at all. So on that route the reader reads the first `text` block and skips the
reasoning in front of it, asks for 1024 tokens on top of the cap the caller set so the cap keeps
meaning the length of the answer, and turns a reply with no `text` block into an error naming the
`stop_reason` rather than an empty string. The level still goes out with the request, and `low`
measured clean where `disabled` did not.

Every refusal an LLM provider sends now carries that provider's own `code` and `message`, bounded
to 300 characters, into the line the reader logs. Before, a 400 or a 429 reached the operator as
the bare status and a link to MDN, so `1210` and `1113 Insufficient balance` both read as "Client
error".

## The two picture seats

Music selection reads the cut's text by default. An unavailable text model or an unusable answer
falls back to defaults; it does not send pictures instead. The answer is banked separately from
selection, and `runs why <asset-id> --run <run-id>` reports the music route. Standalone
`music add --analyze-frames` and `music analyze` explicitly send sampled frames to the configured
vision provider.

Special-day scans prefer the configured producer's prepared captions. A day with no such captions
keeps the frame fallback. Once a day takes the caption route, a failed text call never switches
it to vision. Both new text calls have separate bank keys; existing reader questions are unchanged.

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
