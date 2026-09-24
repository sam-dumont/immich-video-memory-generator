---
sidebar_position: 10
title: Network & Privacy
---

# Data leaving your network

The app runs locally and talks to your Immich server over your LAN. No telemetry, no update check,
no analytics.

**A default run makes no outside call at all.** It reaches your Immich server, the endpoints you
configured yourself, and localhost. Everything else is a switch you turn on, and every switch says
here what it sends and to whom. The list comes from a sweep of the source and is kept by hand: if
you find a call that is not here,
[open an issue](https://github.com/sam-dumont/immich-video-memory-generator/issues).

| Destination | When | What leaves your network | Opt out |
|---|---|---|---|
| Your Immich server | always | metadata, previews and originals down, and at preparation the index and three keyframes of each video's playback, by byte range; the finished video and an album up, only with upload-back | `upload.enabled: false` (default) |
| `nominatim.openstreetmap.org` | only with `network.geocoding: true`: trip detection, and the places on the cut | each trip cluster's centroid, and the rounded coordinates of the places the film shows | off by default |
| `server.arcgisonline.com` (World Imagery) | only with `network.map_tiles: true`: the trip fly-over, the static trip map, the background of location cards | tile requests covering the trip area and your home base | off by default |
| `raw.githubusercontent.com` (Noto fonts) | only when you run `titles fonts --install`, and while the Docker image is built; never during a render | nothing about your library: 42 font files, 43 MB, each checked against a pinned SHA-256 | don't run it: titles then draw what the wheel carries |
| `editorial.preparation.caption_base_url` | the first cut over a period, `full` tier | a 400 px JPEG of every eligible picture, once; a strip of three keyframes of every video, and of every Live Photo whose motion plays, once; a `/models` probe; and `caption_api_key` as a bearer token when one is set | `tier: no_captions`, or a server on your own network (default `localhost:8092`) |
| `llm.base_url` | the reader | 800 px tiles of a few dozen candidates and their annotation lines, which carry people and place names, plus the names of the Immich albums holding each episode's pictures | `reader: rules`, or a local model (default `localhost:8080`, the app's own port, so set it) |
| `llm.base_url` | the opening title of a people or occasion memory, by default whenever a reader is configured (both the wizard and the CLI); trips only with `--llm-title` | text, no images: first names, birth dates and ages, the relationships your people file records between the people in the film, the people condition, the span, place names, the catalogue's words, the name of the Immich album most of the cut sits in (never a catch-all out of proportion to the film), and the clip descriptions on the trip path | `--no-llm-title`, `--title` of your own, or no reader configured |
| `api.anthropic.com` | the reader, with `provider: anthropic` and no `base_url` of your own | the same tiles and lines, to Anthropic | name a host of your own in `llm.base_url` |
| `api.z.ai` | the reader, with `provider: zai` and no `base_url` of your own | the same tiles and lines, to z.ai | name a host of your own in `llm.base_url` |
| `advanced.inference.facts_base_url` | preparation, when set | each picture's preview, for the heads and detectors | leave it unset: the app runs them itself |
| `ace_step.api_url`, `musicgen.base_url` | AI music through a remote API | mood, tempo, genre text; MusicGen also uploads the generated track for stem separation | `ace_step.mode: lib`, your own file with `--music`, or `--no-music` |
| `llm.base_url` | automatic music selection and music preview, when cut text and a model are available | the saved cut's thesis, ordered story labels and prepared captions for kept pictures; no images | your own track, `--no-music`, or a local text model |
| `llm.base_url` | special-day scan | text only, never frames: prepared captions with capture times, places, coordinates and recognised names, and for a day the captions do not cover, what the library records about it (times, places, coordinates, recognised names, favourites, videos). A day with no written fact at all is left unjudged rather than asked about | a local model |
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
| reader | `llm.base_url` | 800 px tiles of stills, the annotation lines beside them with the names of people and places, and the album names your library gives those pictures. No video frames: what a video shows reaches it as the captioner's banked sentence |
| captioner | `editorial.preparation.caption_base_url` | 400 px tiles, a 960 × 320 strip of three keyframes per video, no metadata, and `caption_api_key` if set |

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

Both are off, and both are worth turning on if you are comfortable with what they send.

```yaml
network:
  geocoding: false
  map_tiles: false
```

**`geocoding`** sends each trip cluster's centroid to Nominatim, and the rounded coordinates
(2 decimals, about a kilometre) of the distinct places your cut actually shows. One request per
place, at Nominatim's one-per-second policy, cached under `cache.directory/place-names/` and keyed
on the locale as well as the coordinate. Only clips that already show a place are asked about, so
home and the neighbourhoods you see every week are never sent. The library is never geocoded: a cut
is tens of clips.

It buys two things. Trip names that read like places rather than like EXIF tags, and city names in
the film's language. Country names are translated offline whatever this switch says, so a French
film already says "Chypre" instead of "Cyprus"; what the switch adds is "Nicosie" instead of
"Nicosia". With it off, names come from the city and country Immich already stored, which are
always English.

The automation trip detector is a trigger too: a nightly `auto run` that finds trips geocodes them
the same way a `--memory-type trip` run does.

Neither privacy mode nor `title_screens.enabled: false` used to stop any of this, because detection
ran before anonymisation. Now the switch does.

**`map_tiles`** fetches satellite imagery from ArcGIS World Imagery: hundreds of tiles for one
fly-over, a handful for a static map or a location card, covering the trip area and your home base.
With it off, a trip opens on the ordinary title card carrying the trip title, and location cards
keep their text on the style's own background. In privacy mode the tiles are of the fake city, not
of yours.

## Fonts

A render never downloads a font. The title families and Noto Sans (Latin, Greek, Cyrillic,
Vietnamese) ship inside the wheel. The Noto faces for every other script (Arabic, Hebrew, the Indic
scripts, Thai, Chinese, Japanese, Korean and more) are 43 MB, so they come from one explicit step:

```bash
immich-memories titles fonts --install
```

It fetches 42 files from `raw.githubusercontent.com` (the Noto project's own repositories, pinned to
one commit and one tag) and refuses any file whose SHA-256 is not the one in the code. The Docker
image runs the same step at build time, so a container has every script without ever asking. A
title with letters no installed font has draws what it can and logs one line naming the step above.

`preflight` prints one row per switch you turned on, naming the host it will contact. A default
install gets no such row.

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
film shows, not what the app sends: with `network.geocoding: true`, trip detection has already
asked Nominatim about the real coordinates by the time the fake city is chosen.

## CI only

`make pip-audit` queries `pypi.org` for known vulnerabilities in the lock file. It runs in CI,
never at runtime.
