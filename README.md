# Immich Memories

[![CI](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/sam-dumont/immich-video-memory-generator/graph/badge.svg)](https://codecov.io/gh/sam-dumont/immich-video-memory-generator)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/sam-dumont/immich-video-memory-generator/badge)](https://scorecard.dev/viewer/?uri=github.com/sam-dumont/immich-video-memory-generator)
[![Release](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml)
[![Python](https://img.shields.io/pypi/pyversions/immich-memories)](https://pypi.org/project/immich-memories/)
[![License](https://img.shields.io/github/license/sam-dumont/immich-video-memory-generator)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-Docusaurus-blue)](https://sam-dumont.github.io/immich-video-memory-generator/)

**Your self-hosted [Immich](https://immich.app/) library, cut into films worth keeping: a month, a year in review, a trip with its map, one person across the years.**

<p align="center">
  <a href="https://sam-dumont.github.io/immich-video-memory-generator/demo/trip-preview.mp4">
    <img src="https://sam-dumont.github.io/immich-video-memory-generator/img/trip-map-flyover.jpg" alt="A finished trip film, opening on the satellite map of the route" width="720">
  </a>
  <br/>
  <sub><a href="https://sam-dumont.github.io/immich-video-memory-generator/demo/trip-preview.mp4">▶ Play a finished film (32 s)</a> · CC0 stock pictures, <a href="tests/e2e/fixtures/library/CREDITS.md">credited here</a> · <a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/">Documentation</a></sub>
</p>

It reads a period of your library, picks the pictures and videos that tell it, keeps them in the order they were taken, and renders the film with titles, maps and music.

**It runs on your NAS. No GPU, no AI service.** One container next to Immich, cutting from dates, places, favourites, the people Immich recognised and a few small classifiers on the CPU. That cut is the product. A GPU makes it faster, and a reader model can polish the draft; both are [optional](https://sam-dumont.github.io/immich-video-memory-generator/docs/better/overview).

You see the storyboard before anything renders. Untick what you disagree with and cut again; `immich-memories runs why <asset-id>` says which rule kept a picture or left it out. [How it chooses](https://sam-dumont.github.io/immich-video-memory-generator/docs/how-it-chooses/overview) writes every rule down.

## Run it

```bash
mkdir -p immich-memories/output && cd immich-memories
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
export IMMICH_URL="http://your-immich-server:2283"
export IMMICH_API_KEY="your-api-key"
docker compose up -d
docker compose exec immich-memories immich-memories models fetch   # the small CPU classifiers, once
docker compose exec immich-memories immich-memories preflight      # checks Immich, models, output dir
# then open http://localhost:8080 and cut one month
```

Port 8080 is published on localhost only and authentication is off by default: the app holds an API key to your whole library, so turn on [authentication](https://sam-dumont.github.io/immich-video-memory-generator/docs/run/authentication) before you expose it. Immich v2 and v3 are both detected at runtime.

The [Quick start](https://sam-dumont.github.io/immich-video-memory-generator/docs/get-started/quick-start) walks it step by step, and [Teach it your family](https://sam-dumont.github.io/immich-video-memory-generator/docs/get-started/who-is-who) covers the two settings that make a NAS cut good: where home is, and who is who. Without Docker: [pip / uv](https://sam-dumont.github.io/immich-video-memory-generator/docs/run/uv-pip).

## What leaves your network

A default run talks to your Immich server and nothing else. No telemetry, no account. Immich stays read-only unless you ask for the film to be uploaded back. The two outside hosts, Nominatim for place names and ArcGIS for the satellite map, sit behind `network:` switches that are off in a fresh install, and a reader or caption server only receives pictures if you configure one. Everything is on [Privacy](https://sam-dumont.github.io/immich-video-memory-generator/docs/run/privacy).

## Why it's being reworked so much

Stable: the install, the read-only use of Immich, and the render (titles, maps, music, HDR, encoding). Still moving: selection, which pictures make the cut. It improves most weeks, and every change is checked against films cut from real libraries before it merges. A release can pick a slightly different set for the same month; pin a version tag instead of `latest` if you want it to hold still, and watch a film before you share it.

## Development

`make dev` installs everything, `make ci` runs what CI runs, `make help` lists the rest.
Guidelines in [CONTRIBUTING.md](CONTRIBUTING.md). The codebase was written with AI assistance as a deliberate experiment; [DISCLAIMER.md](DISCLAIMER.md) says how and where it fell short.

## License

MIT, see [LICENSE](LICENSE).
