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

**Make memory videos from your [Immich](https://immich.app/) library: a year in review, a trip with its map, or one person across the years.**

<p align="center">
  <a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/">
    <img src="https://sam-dumont.github.io/immich-video-memory-generator/img/demo-hero.gif" alt="Immich Memories demo: the brief, the cut and the story it produced" width="720">
  </a>
  <br/>
  <sub><a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/">▶ Watch the 47-second demo</a> · <a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/create/first-memory">Make your first memory</a> · <a href="https://sam-dumont.github.io/immich-video-memory-generator/">Full documentation</a></sub>
</p>

The editor reads the period as a story, keeps the pictures that carry it, and shows the storyboard before rendering: every shot in the order it was taken, each with the line that put it there. Untick what you disagree with and cut again; `immich-memories runs why` says which pass dropped a missing one.

## What leaves your machine

The app connects to your configured Immich server. Remote model endpoints can receive pictures
and annotations, including names and places. Trip detection uses Nominatim for place names;
satellite titles fetch map tiles; model installation downloads artifacts. There is no app
telemetry. See [Network & Privacy](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/network-and-privacy) for each destination and its switch.

## Three ways to run it

The editor has two independent settings: who reads the period, and how much image analysis runs first.

| Setup | What you need | What you get |
|---|---|---|
| **Rules only** (`reader: rules`, `tier: metadata_only`) | The app and FFmpeg | Ten standard memory types from metadata and pixel measurements. Simpler selection, no custom free-text subjects, limited audience evidence |
| **Rules plus classifiers** (`reader: rules`, `tier: no_captions`) | The `editorial` extra and pinned model files | Image classifiers and detectors, without a caption server |
| **Model reader** (`reader: model`) | A configured vision model server | Story-based selection and custom subjects. The `full` preparation tier also needs a caption server |

The reader, captioner and image classifiers can run on another machine. [Running modes](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/running-modes) covers hardware requirements and measured timings. `immich-memories preflight` checks your setup.

Title screens use kernels on a supported GPU or CPU backend, with a PIL fallback. Both can animate;
available effects and speed differ. HDR export requires H.265; the default H.264 export is SDR.

## Run it

```bash
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
export IMMICH_URL="http://your-immich-server:2283"
export IMMICH_API_KEY="your-api-key"
mkdir -p output
docker compose up -d
docker compose exec immich-memories immich-memories models fetch   # skip on metadata_only
docker compose exec immich-memories immich-memories preflight      # Immich, models, reader
# then open http://localhost:8080
```

On Linux, `./output` must be writable by UID/GID 1000; see [Docker permissions](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/installation/docker). The compose file publishes port 8080 on localhost only, and authentication is disabled by default. The UI is single-user, single-replica: run one instance. The app holds an API key to your whole library, so turn on [authentication](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/authentication) before you expose the port.

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

Without Docker, install FFmpeg and use Python 3.11 or later. With the same Immich environment variables:

```bash
uv tool install "immich-memories[editorial]"
export IMMICH_MEMORIES_EDITORIAL__READER=rules
export IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER=no_captions
immich-memories models fetch
immich-memories preflight
immich-memories prepare --year 2024 --month 6      # prepare one month, print what each producer cost
immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
immich-memories ui                                 # the web UI on :8080
```

Start with one month. Preparation scales with the date range; later runs reuse compatible saved facts and readings. The whole stand-up, in order, is the [self-hosting guide](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/self-hosting).

## What it does

- Ten memory types: year in review, monthly, person, multiple people (with `AND` / `OR` between names), season, on this day, album, trip with an animated map, holiday, and a day the library itself flagged. The web UI adds a custom date range.
- Photos and videos in one pool, Live Photos included. Title screens, month dividers, map fly-overs.
- Music: your own file, 28 bundled tracks, or a generated track through ACE-Step or MusicGen. Ducking under the clips' own audio.
- A four-page web UI (Memory, Media pool, Generation Options, Preview & Export) behind basic auth, OIDC or a trusted-header proxy, or a headless CLI.
- Daily automation: a scheduled `auto run` retries pending delivery or generates one eligible memory, subject to cooldown. Upload-back is optional. In Docker set `IMMICH_MEMORIES_AUTOMATION__ENABLED=true`.
- Privacy mode blurs every frame and moves the map to a fake city, for showing the app over your own library.

How the editor decides is written up in [The Curator](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/pipeline/the-curator).

## About the demo

The hero above is `make demo-hero`, a 15-second cut of the Remotion demo (`make demo-ui`), which recreates the UI in React over a CC0 fixture library: 136 stock pictures that tell one household's June, a birthday, a Saturday in the woods and a week by a lake. The CLI demo inside it is a VHS recording (`make demo-cli`). Docs screenshots come from a hermetic run over the same fixture library (`make screenshots`).

## Development

`make dev` installs development dependencies, `make ci` runs the local quality gates and unit suite, and `make help` lists the rest.
Guidelines in [CONTRIBUTING.md](CONTRIBUTING.md). The codebase was written with AI assistance as a deliberate experiment; [DISCLAIMER.md](DISCLAIMER.md) says how and where it fell short.

## License

MIT, see [LICENSE](LICENSE).
