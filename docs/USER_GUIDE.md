# User Guide

Immich Memories turns a slice of your Immich library into an edited video. It reads the period as
a story, keeps the pictures that carry it, adds title screens and music, and hands you a file.
Nothing is scored. Drive it from the web UI (`immich-memories ui`, then `http://localhost:8080`)
or headless from the CLI.

Authentication is off by default. If the UI binds beyond loopback, anyone who can reach the port
can use it, so turn auth on before exposing it. The UI is single-user and single-replica: run one
instance.

The walkthrough itself lives on the docs site, versioned with the code and gated against drift.

## The web UI

| Page | What it is |
|------|------------|
| Memory | [The brief, the cut and the story it produced](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/memory) |
| Media pool | Untick what should never be used, then cut again |
| Options | Title screens, music, resolution, encoder |
| Export | Preview, export, upload back to Immich |
| Config, People, Cache | [Settings](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/settings) |

## Everything else

| Topic | Page |
|-------|------|
| First run, end to end | [Quick start](https://sam-dumont.github.io/immich-video-memory-generator/docs/welcome/quick-start) |
| What a first run costs, per host and tier | [Running modes](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/running-modes) |
| Every command and flag | [CLI reference](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/cli-reference) |
| Every config key and default | [Config reference](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/config-reference) |
| Errors, slow runs, empty output | [Troubleshooting](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/troubleshooting) |
