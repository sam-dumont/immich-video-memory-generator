# User Guide

Immich Memories turns a slice of your Immich library into an edited video. It scores every clip,
keeps the few seconds worth keeping, adds title screens and music, and hands you a file. Drive it
through the 4-step web wizard (`immich-memories ui`, then `http://localhost:8080`) or headless
from the CLI.

Authentication is off by default. If the UI binds beyond loopback, anyone who can reach the port
can use it, so turn auth on before exposing it. The UI is single-user and single-replica: run one
instance.

The walkthrough itself lives on the docs site, where it is versioned with the code and gated
against drift. This page is the index.

## The web wizard

| Step | Page |
|------|------|
| 1. Memory type, date range, people | [Step 1: Configuration](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/step1-configuration) |
| 2. Review, trim and reorder the clips | [Step 2: Clip Review](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/step2-clip-review) |
| 3. Title screens, music, resolution, encoder | [Step 3: Generation Options](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/step3-generation-options) |
| 4. Preview, export, upload back to Immich | [Step 4: Preview & Export](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/step4-preview-export) |
| Server, LLM and auth settings | [Settings](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/settings) |

## Everything else

| Topic | Page |
|-------|------|
| First run, end to end | [Quick start](https://sam-dumont.github.io/immich-video-memory-generator/docs/welcome/quick-start) |
| Every command and flag | [CLI reference](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/cli-reference) |
| Every config key and default | [Config reference](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/config-reference) |
| Errors, slow runs, empty output | [Troubleshooting](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/troubleshooting) |
