# Immich Memories

[![CI](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/sam-dumont/immich-video-memory-generator/graph/badge.svg)](https://codecov.io/gh/sam-dumont/immich-video-memory-generator)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/sam-dumont/immich-video-memory-generator/badge)](https://scorecard.dev/viewer/?uri=github.com/sam-dumont/immich-video-memory-generator)
[![Release](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml)
[![Python](https://img.shields.io/pypi/pyversions/immich-memories)](https://pypi.org/project/immich-memories/)
[![License](https://img.shields.io/github/license/sam-dumont/immich-video-memory-generator)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-Docusaurus-blue)](https://sam-dumont.github.io/immich-video-memory-generator/)

> **Beta, under heavy rework, not stable.** The selection engine has just been replaced by a
> story-driven editor, and `main` and the `latest` Docker tag move with the work that follows.
> Expect runs that fail, photos that go missing, and options that move between releases. Try it
> on one small album first, not your whole library, and file what breaks.

**Turns your self-hosted [Immich](https://immich.app/) library into edited memory videos: a year in review, a trip with its map, one person across the years.** It reads the period as a story, keeps the pictures that carry it, always in chronological order, and writes down why every picture is in the cut.

<p align="center">
  <a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/welcome/overview">
    <img src="https://sam-dumont.github.io/immich-video-memory-generator/img/demo-hero.gif" alt="Immich Memories demo: the brief, the cut and the story it produced" width="800">
  </a>
  <br/>
  <sub><a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/welcome/overview">▶ Watch the 47-second demo</a> · <a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/create/first-memory">Make your first memory</a> · <a href="https://sam-dumont.github.io/immich-video-memory-generator/">Full documentation</a></sub>
</p>

The year-in-review videos your phone's cloud used to make, generated from your own Immich library, on your own hardware.

## What leaves your machine

Nothing, unless you point it somewhere. No telemetry, no cloud API; the app talks to your Immich server over your LAN. Two optional model endpoints can receive pictures: a caption server gets a 400 px tile of every picture in the period, once, and a reader gets 800 px tiles of a few dozen candidates plus their annotation lines, which carry people and place names. Both default to `localhost`. Trip detection geocodes GPS clusters at `nominatim.openstreetmap.org`, and satellite title screens fetch map tiles. The full list, with the switch for each, is on [Network & Privacy](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/network-and-privacy).

## Three ways to run it

The editor has two independent settings: who reads the period, and how much image analysis runs first.

| Setup | What you need | What you get |
|---|---|---|
| **Rules only** (`reader: rules`, `tier: metadata_only`) | The app alone. A 4-core NAS is enough | All ten memory types from dates, places, favourites and people. No model, $0 in API fees. Measured on a Celeron NAS: 279 s for a month cold, 11 s warm. Simpler cuts: it can skip an occasion or spend time on a mundane object, so look before you share |
| **Rules plus classifiers** (`tier: no_captions`) | Same box, plus about 500 MB of pinned ONNX models fetched with one command | The sensitive-content and document detectors, so the family-viewing gate has evidence. First pass over a library of about ten thousand pictures on a Celeron: 3 h 41 min, then banked |
| **Model reader** (`reader: model`) | A machine that holds a vision model with a 32k context. Graded on a 30B model at 4-bit, about 17 GB resident, on an Apple Silicon Mac with 32 GB | The full editor: it reads the period as a story, looks at the pictures it needs to, and argues for each one. Add the caption server (1 to 2 GB) for a description under every picture; that first pass costs 30 s a picture |

Title screens are GPU-rendered on Linux x86_64, Linux aarch64, macOS arm64 and Windows AMD64 (Python 3.11 to 3.13). On an Intel Mac or Python 3.14 there is no kernel wheel and titles fall back to the PIL renderer: same text and timing, static instead of animated. `immich-memories preflight` tells you which one you get.

The reader and the caption server can live on another machine than the app, which needs 2 to 4 GB and renders on CPU. Measured timings, what each degraded mode loses, and what a hosted reader costs are on [Running modes](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/running-modes).

## Run it

```bash
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
export IMMICH_URL="http://your-immich-server:2283"
export IMMICH_API_KEY="your-api-key"
docker compose up -d
docker compose exec immich-memories immich-memories models fetch   # skip on metadata_only
docker compose exec immich-memories immich-memories preflight      # Immich, models, reader
# then open http://localhost:8080
```

The compose file publishes port 8080 on localhost only, and authentication is disabled by default. The UI is single-user, single-replica: run one instance. The app holds an API key to your whole library, so turn on [authentication](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/authentication) before you expose the port.

### Supported Immich Versions

Immich v2 and v3, detected at runtime:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

Leave this on `auto`. The app detects the server major version and uses the matching API contract;
you do not choose a version for each run. The explicit `v2` and `v3` values are manual
troubleshooting overrides: escape hatches for proxies or unusual deployments that hide or rewrite
the version endpoint. They force that contract, so don't use them as upgrade flags.

Without Docker, on Python 3.11 or later:

```bash
uv tool install "immich-memories[editorial]"
immich-memories models fetch
immich-memories prepare --year 2024 --month 6      # prepare one month, print what each producer cost
immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
immich-memories ui                                 # the web UI on :8080
```

Start with one month, not a year: preparation scales with the width of the date range and is paid once, so the second cut over the same period is mostly the render. The whole stand-up, in order, is the [self-hosting guide](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/self-hosting).

## What it does

- Ten memory types: year in review, monthly, person, multiple people (with `AND` / `OR` between names), season, on this day, album, trip with an animated map, holiday, and a day the library itself flagged. The web UI adds a custom date range.
- Photos and videos in one pool, Live Photos included. Title screens, month dividers, map fly-overs.
- Music: your own file, 28 bundled tracks, or a generated track through ACE-Step or MusicGen. Ducking under the clips' own audio.
- A four-page web UI (Memory, Media pool, Generation Options, Preview & Export) behind basic auth, OIDC or a trusted-header proxy, or a headless CLI.
- Daily automation: one scheduled `auto run` generates a single eligible memory a day and can upload it back to Immich. In Docker set `IMMICH_MEMORIES_AUTOMATION__ENABLED=true`.
- Privacy mode blurs every frame and moves the map to a fake city, for demos and screenshots.

How the editor decides is written up in [The Curator](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/pipeline/the-curator).

## About the demo

The hero above is `make demo-hero`, an 18-second cut of the Remotion demo (`make demo-ui`), which recreates the UI in React over six CC0 photos. The CLI demo inside it is a VHS recording (`make demo-cli`). Docs screenshots come from a hermetic run over the same fixture library (`make screenshots`).

## Development

`make dev` installs everything, `make ci` runs what CI runs, `make help` lists the rest.
Guidelines in [CONTRIBUTING.md](CONTRIBUTING.md). The codebase was written with AI assistance as a deliberate experiment; [DISCLAIMER.md](DISCLAIMER.md) says how and where it fell short.

## License

MIT, see [LICENSE](LICENSE).
