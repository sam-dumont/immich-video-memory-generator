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
  <a href="https://sam-dumont.github.io/immich-video-memory-generator/demo/demo.mp4">
    <img src="https://sam-dumont.github.io/immich-video-memory-generator/img/demo-hero.gif" alt="Choose a memory, review its storyboard, and watch the finished film" width="720" height="405">
  </a>
  <br/>
  <sub><a href="https://sam-dumont.github.io/immich-video-memory-generator/demo/demo.mp4">▶ Play the demo with music</a> · <a href="https://sam-dumont.github.io/immich-video-memory-generator/demo/trip-preview.mp4">Watch a finished trip film</a> · CC0 stock pictures, <a href="tests/e2e/fixtures/library/CREDITS.md">credited here</a> · <a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/">Documentation</a></sub>
</p>

It reads a period of your library, picks the pictures and videos that tell it, keeps them in the order they were taken, and renders the film with titles, maps and music.

**Works on a plain NAS. A GPU or a model makes it better.** The base install is one container next to Immich, cutting from dates, places, favourites, the people Immich recognised and a few small classifiers on the CPU, and that already gives you a film worth sharing. A GPU makes it faster, and a reader model polishes the cut: [what each one adds](https://sam-dumont.github.io/immich-video-memory-generator/docs/better/overview).

The CLI and web UI run the same editor. Automate with the CLI, or make and refine a cut in the browser. The storyboard is a contact sheet: open a picture to read why it stayed, inspect recorded model suggestions, exclude it from export, or find alternatives in the pool. `immich-memories runs why <asset-id>` reads the same saved evidence. [How it chooses](https://sam-dumont.github.io/immich-video-memory-generator/docs/how-it-chooses/overview) writes every rule down.

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

The compose file publishes port 8080 on localhost only, and authentication is disabled by default. The UI is single-user, single-replica: run one instance. The app holds an API key to your whole library, so turn on [authentication](https://sam-dumont.github.io/immich-video-memory-generator/docs/run/authentication) before you expose the port.

### Immich v2 and v3

Both majors are supported, and Immich v2 and v3 are detected at runtime:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

Leave this on `auto`. The app detects the server major version and uses the matching API contract; you do not choose a version for each run. The explicit `v2` and `v3` values are manual troubleshooting overrides: escape hatches for proxies or unusual deployments that hide or rewrite the version endpoint. They force that contract, so don't use them as upgrade flags.

### Next

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
