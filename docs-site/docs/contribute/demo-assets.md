---
sidebar_position: 3
title: Demo assets
---

# Regenerating the demo assets

Nothing on the docs site or in the README is a screenshot of a real library. The demo is a
React recreation of the UI rendered with Remotion over six CC0 photographs, the CLI demo is a
VHS recording, and the screenshots come from a hermetic run over the same six photographs.

| Command (repo root) | Produces |
|---|---|
| `make demo-ui` | `docs-site/static/demo/demo.mp4`, the 45-second composition, 1920×1080 H.264 |
| `make demo-hero` | `docs-site/static/img/demo-hero.gif`, seconds 2.6 to 20.6 of `demo.mp4` at 800 px and 12 fps, the README hero |
| `make demo-cli` | `docs-site/remotion/public/cli-demo.mp4`, the terminal recording the CLI scene plays, via VHS |
| `make demo-output` | `docs-site/remotion/public/output-preview.mp4` and `output-frame.jpg`, cut on the hermetic launch |
| `make demo-music` | ACE-Step candidates for `docs-site/remotion/public/demo-music.wav` |
| `make screenshots` | the light and dark screenshots under `docs-site/static/img/screenshots/` |
| `make demo-ui-dev` | Remotion Studio for a live preview |

Two entries in `docs-site/remotion/public/` are symlinks, so the demo shows the same pictures as
the tests and the docs: `library` points at `tests/e2e/fixtures/library` (credits in its
`CREDITS.md`) and `screenshots` at `docs-site/static/img/screenshots`.

The order that keeps everything consistent after a UI change: `make screenshots`, then
`make demo-output`, then `make demo-ui`, then `make demo-hero`. The hero GIF is served from the
docs site, so it reaches the README on the next docs deploy, not on the next push.

The scenes live in `docs-site/remotion/src/scenes/`. When a page name or a button changes in the
app, the scene changes with it: `tests/e2e/test_demo_assets.py` pins the button labels the demo
shows against the real pages.
