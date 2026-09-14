---
sidebar_position: 3
title: Demo assets
---

# Regenerating the demo assets

The demo workflow uses the CC0 stock library under `tests/e2e/fixtures/library/`. Remotion
renders a React recreation of the UI; VHS records the real CLI against test services; screenshots
and output clips come from the test application's browser flow.

`tests/e2e/fake_library.py` defines the synthetic June 2024 household, its 136 source pictures
and 18 scripted picks. Those picks demonstrate the UI; they do not measure model selection
quality. `CREDITS.md` beside the pictures records their provenance, and
`tests/test_fixture_library.py` checks credits, hashes and the 60 MB size ceiling.

Run `make dev` first. CLI recording also requires VHS; Remotion targets install their Node
dependencies with `npm ci`.

| Command (repo root) | Produces |
|---|---|
| `make demo-ui` | `docs-site/static/demo/demo.mp4`, the roughly 47-second composition, 1920×1080 H.264; it ends on the film the product made |
| `make demo-hero` | `docs-site/static/img/demo-hero.gif`, seconds 3.4 to 15.6 of `demo.mp4` (brief, cut, storyboard) then its last 3 s (the film), 720 px, 10 fps, about 15 s, the README hero (check the resulting file stays under 4 MB) |
| `make demo-cli` | `docs-site/remotion/public/cli-demo.mp4`, VHS recording the real CLI: `scripts/demo-cli-hermetic.py` runs `generate`, `runs story` and `runs why` against the hermetic fakes from `tests/e2e` (`make demo-cli-run` plays the same session without recording) |
| `make demo-output` | `docs-site/remotion/public/output-preview.mp4` and `output-frame.jpg`, cut on the hermetic launch |
| `make demo-output-trip` | `docs-site/static/demo/trip-preview.mp4` and `docs-site/static/img/trip-map-flyover.jpg`, the fixture's lake week cut as a trip memory and the still of its map fly-over, both played by the trip memory page. Needs the network: the satellite tiles come from ArcGIS World Imagery and the trip's name from Nominatim, and neither has an offline stand-in |
| `make demo-music` | ACE-Step music candidates; choose the demo track for `docs-site/remotion/public/demo-music.wav` |
| `make screenshots` | the light and dark screenshots under `docs-site/static/img/screenshots/` |
| `make demo-ui-dev` | Remotion Studio for a live preview |

Two entries in `docs-site/remotion/public/` are symlinks, so the demo shows the same pictures as
the tests and the docs: `library` points at `tests/e2e/fixtures/library` (credits in its
`CREDITS.md`) and `screenshots` at `docs-site/static/img/screenshots`.

The order that keeps everything consistent after a UI or a fixture change: `make screenshots`,
then `make demo-output` and `make demo-output-trip`, then `make demo-cli`, then `make demo-ui` (which runs `make demo-fixture`
first, exporting the fixture's cut and first pool page into `docs-site/remotion/src/fixture.ts`),
then `make demo-hero`. The hero GIF is served from the
docs site, so it reaches the README on the next docs deploy, not on the next push.

The scenes live in `docs-site/remotion/src/scenes/`. Update the scenes when app labels or layouts change.
`tests/e2e/test_demo_assets.py` exercises the real generation buttons, but the Remotion
recreation also needs visual review.
