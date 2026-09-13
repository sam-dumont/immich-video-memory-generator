---
sidebar_position: 3
title: Demo assets
---

# Regenerating the demo assets

Nothing on the docs site or in the README is a screenshot of a real library. The demo is a
React recreation of the UI rendered with Remotion over a CC0 fixture library, the CLI demo is a
VHS recording, and the screenshots come from a hermetic run over the same library. The library
is 136 stock pictures under `tests/e2e/fixtures/library/` that tell one household's June 2024:
ordinary days at home, a birthday in the garden, a Saturday in the woods, a week by a lake 590 km
away and the drive home. `tests/e2e/fake_library.py` is the script: which picture belongs to
which story, which ones the editor keeps (18) and the reason for every one it leaves out. Every
file is credited in `CREDITS.md` next to the pictures, and `tests/test_fixture_library.py` pins
the credits, the hashes and the 60 MB ceiling.

| Command (repo root) | Produces |
|---|---|
| `make demo-ui` | `docs-site/static/demo/demo.mp4`, the 47-second composition, 1920×1080 H.264; it ends on the film the product made |
| `make demo-hero` | `docs-site/static/img/demo-hero.gif`, seconds 2.6 to 16.6 of `demo.mp4` (brief, cut, storyboard) then its last 4 s (the film), 800 px, 10 fps, the README hero |
| `make demo-cli` | `docs-site/remotion/public/cli-demo.mp4`, VHS recording the real CLI: `scripts/demo-cli-hermetic.py` runs `generate`, `runs story` and `runs why` against the hermetic fakes from `tests/e2e` (`make demo-cli-run` plays the same session without recording) |
| `make demo-output` | `docs-site/remotion/public/output-preview.mp4` and `output-frame.jpg`, cut on the hermetic launch |
| `make demo-music` | ACE-Step candidates for `docs-site/remotion/public/demo-music.wav` |
| `make screenshots` | the light and dark screenshots under `docs-site/static/img/screenshots/` |
| `make demo-ui-dev` | Remotion Studio for a live preview |

Two entries in `docs-site/remotion/public/` are symlinks, so the demo shows the same pictures as
the tests and the docs: `library` points at `tests/e2e/fixtures/library` (credits in its
`CREDITS.md`) and `screenshots` at `docs-site/static/img/screenshots`.

The order that keeps everything consistent after a UI or a fixture change: `make screenshots`,
then `make demo-output`, then `make demo-cli`, then `make demo-ui` (which runs `make demo-fixture`
first, exporting the fixture's cut and first pool page into `docs-site/remotion/src/fixture.ts`),
then `make demo-hero`. The hero GIF is served from the
docs site, so it reaches the README on the next docs deploy, not on the next push.

The scenes live in `docs-site/remotion/src/scenes/`. When a page name or a button changes in the
app, the scene changes with it: `tests/e2e/test_demo_assets.py` pins the button labels the demo
shows against the real pages.
