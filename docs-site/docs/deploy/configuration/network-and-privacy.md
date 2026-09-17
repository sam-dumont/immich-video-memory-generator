---
sidebar_position: 10
title: Network & Privacy
---

# Data leaving your network

The app runs locally and talks to your Immich server over your LAN. No telemetry, no update check,
no analytics. Some features make outbound requests; every one is below, with what is sent and how
to turn it off. The list comes from a sweep of the source and is kept by hand: if you find a call
that is not here, [open an issue](https://github.com/sam-dumont/immich-video-memory-generator/issues).

| Destination | When | What leaves your network | Opt out |
|---|---|---|---|
| Your Immich server | always | metadata, previews and originals down; the finished video and an album up, only with upload-back | `upload.enabled: false` (default) |
| `nominatim.openstreetmap.org` | trip detection | the real GPS of each trip cluster's centroid, for a place name | do not use the Trip type |
| `server.arcgisonline.com` (World Imagery) | the map fly-in of a trip title | tile requests covering the trip area and your home base | `title_screens.enabled: false` |
| `cdn.jsdelivr.net` (Fontsource) | a title needs a font that is neither bundled nor in `~/.immich-memories/fonts/` | a font file, unpinned (`@latest`) | keep a bundled family (Josefin Sans, Montserrat, Outfit, Quicksand, Raleway) or drop TTFs in that folder |
| `editorial.preparation.caption_base_url` | the first cut over a period, `full` tier | a 400 px JPEG of every eligible picture, once, a `/models` probe, and `caption_api_key` as a bearer token when one is set | `tier: no_captions`, or a server on your own network (default `localhost:8092`) |
| `llm.base_url` | the reader | 800 px tiles of a few dozen candidates and their annotation lines, which carry people and place names | `reader: rules`, or a local model (default `localhost:8080`, the app's own port, so set it) |
| `llm.base_url` | the opening title of a people or occasion memory, by default whenever a reader is configured (both the wizard and the CLI); trips only with `--llm-title` | text, no images: first names, birth dates and ages, the relationships your people file records between the people in the film, the people condition, the span, place names, the catalogue's or the album's own words, and the clip descriptions on the trip path | `--no-llm-title`, `--title` of your own, or no reader configured |
| `api.anthropic.com` | the reader, with `provider: anthropic` and no `base_url` of your own | the same tiles and lines, to Anthropic | name a host of your own in `llm.base_url` |
| `api.z.ai` | the reader, with `provider: zai` and no `base_url` of your own | the same tiles and lines, to z.ai | name a host of your own in `llm.base_url` |
| `advanced.inference.facts_base_url` | preparation, when set | each picture's preview, for the heads and detectors | leave it unset: the app runs them itself |
| `ace_step.api_url`, `musicgen.base_url` | AI music through a remote API | mood, tempo, genre text; MusicGen also uploads the generated track for stem separation | `ace_step.mode: lib`, your own file with `--music`, or `--no-music` |
| `llm.base_url` | automatic music selection and music preview, when cut text and a model are available | the saved cut's thesis, ordered story labels and prepared captions for kept pictures; no images | your own track, `--no-music`, or a local text model |
| `llm.base_url` | special-day scan | prepared captions with capture times, places and recognised names; sampled frames for any day the captions do not cover | a local model |
| Hugging Face, torch hub, `github.com` | `models fetch`; first use of ACE-Step or Demucs | nothing about your library; weights are downloaded once | pre-seed the caches for an air-gapped box |
| Your Apprise or ntfy targets | notifications | memory type, outcome, duration, output path, a redacted error tail; a JPEG frame if `attach_thumbnail: true` | `notifications.enabled: false` (default) |
| Your OIDC provider | login | the standard OIDC flow with PKCE | basic auth or the trusted-header provider |

Three provider names fill in a vendor URL when `llm.base_url` is left at its default: `openai`,
`anthropic` and `zai`. Set `base_url` yourself and the request goes where you point it, whichever
provider is named. `preflight` asks that URL for its model list, and sends one small test call when
the host does not publish one.

## The two picture seats

| Seat | Setting | What it is shown |
|---|---|---|
| reader | `llm.base_url` | 800 px tiles, and the annotation lines beside them with the names of people and places |
| captioner | `editorial.preparation.caption_base_url` | 400 px tiles, no metadata, and `caption_api_key` if set |

Both default to this machine. Pointing either at another host (a box on your LAN, a container, a
hosted endpoint) is the consent step: those bytes go onto its disk and into its logs, and nothing
asks a second time.

Two features can reach a vision seat without being the editor, and both prefer text:

- **Music selection** reads the cut's text. A missing text model or an unusable answer falls back
  to defaults rather than sending pictures, and `runs why <asset-id> --run <run-id>` reports the
  route taken. Standalone `music add --analyze-frames` and `music analyze` do send sampled frames.
- **Special-day scans** use prepared captions, and only for a day they cover: 20 described pictures
  across 6 hours of the clock. Below that bar the day falls back to sampled frames; above it the
  day's tiles are never downloaded. Once a day takes the caption route, a failed text call never
  switches it to vision.

## Geocoding and maps

Trip detection reverse-geocodes each cluster's centroid so trips get names; home-base coordinates
feed the map animation only and are never geocoded. Disabling title screens does not stop the
geocoding, and neither does privacy mode: detection runs before anonymisation. The map fly-in
requests hundreds of World Imagery tiles per animated title, of the fake city in privacy mode.

## Thumbnails inside the web UI

Every thumbnail is an `<img>` the browser fetches from the app at `/media/thumb/<asset id>` on the
same port, out of the cache the analysis already filled. The route answers only for assets the
current session prepared, sits behind the same login as every page, and honours privacy-mode blur.

## Privacy mode

Privacy mode blurs every frame of every clip, makes the audio unintelligible, and replaces person
names with fake ones. What survives is the timing, the transitions, the music and the structure:
enough to show someone how the app edits, with none of your pictures in it.

```bash
immich-memories generate --privacy-mode --year 2024
```

For the UI, set `server.enable_demo_mode: true` (off by default) and the sidebar shows a **Demo
mode** switch. Toggling it on blurs every image and video the UI renders, on every page, not just
the clip review screen.

| Data | How it is handled |
|------|-----------------|
| Video content | Whole-frame Gaussian blur plus a noise texture (frosted glass, not pixelation), added to the encode filter chain rather than run as a pass before it. Not face detection: every pixel of every clip goes |
| Audio | Segment reversal (200 ms) and a 300 Hz lowpass on all clip audio, not just detected speech: you hear people talking but cannot make out words |
| GPS coordinates | The whole memory moves onto one fake city: its centre lands on the city, every clip keeps its bearing and distance from that centre, and a memory spread wider than about 25 km is scaled down to fit. Home base moves with it |
| Place names | Replaced with the fake city's name wherever the memory carried one. A clip with no place name does not gain one |
| Person names | Replaced with one of twelve fake names, picked by SHA-256 of the real one, so the same person is the same alias every run |
| Title screen text | Uses the fake person name and the fake city |
| Map animation | Flies to the fake destination, same visual style |

The move is the same every run, so repeated renders of the same trip give away nothing that could
be averaged back to the real location. Title *graphics* are rendered clean (the title text, the map
fly-over, the location cards, the ending screen), but an opening card backed by a frame from your
own clips is a clip and gets the same blur.

Two things it does not cover. The output file name is built before anonymization, so it can still
carry the real place or person names: rename the file before sharing it. And it changes what the
film shows, not what the app sends: trip detection and its geocoding have already run by then.

## CI only

`make pip-audit` queries `pypi.org` for known vulnerabilities in the lock file. It runs in CI,
never at runtime.
