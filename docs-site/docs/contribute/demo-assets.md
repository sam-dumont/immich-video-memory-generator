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
file is credited in `CREDITS.md` next to the pictures. `captions.json` describes each displayed
photograph separately; alternative pictures do not inherit the first picture's description.
The clips are pans over those stills, so their captions describe the visible scene.
`tests/test_fixture_library.py` pins
the credits, the hashes and the 60 MB ceiling.

The demo unticks the first picture and cuts again before export: 17 pictures in the
finished 61.57-second film. The generated fixture carries both cuts' timeline positions;
the duration cards include titles and transition overlap.

136 files on disk; the setup matrix reports 133 pictures for the same month, because the
visibility and metadata rules drop a few before selection ever sees them. Both numbers are right
about different things, so do not reconcile them by editing one.

| Command (repo root) | Produces |
|---|---|
| `make demo-ui` | `docs-site/static/demo/demo.mp4`, 1,436 video frames at 30 fps (about 48 s), 1920×1080 H.264; it ends on the film the product made |
| `make demo-hero` | `docs-site/static/img/demo-hero.gif`, seconds 3.4 to 15.6 of `demo.mp4` (brief, cut, storyboard) then its last 3 s (the film), 720 px, 10 fps, 15.1 s, under 4 MB, the README hero |
| `make demo-cli` | `docs-site/remotion/public/cli-demo.mp4`, VHS recording the real CLI: `scripts/demo-cli-hermetic.py` runs `generate`, `runs story` and `runs why` against the hermetic fakes from `tests/e2e` (`make demo-cli-run` plays the same session without recording) |
| `make demo-output` | `docs-site/remotion/public/output-preview.mp4` and `output-frame.jpg`, cut on the hermetic launch |
| `make demo-output-trip` | `docs-site/static/demo/trip-preview.mp4` and `docs-site/static/img/trip-map-flyover.jpg`, the fixture's lake week cut as a trip memory and the still of its map fly-over, both played by the trip memory page. Needs the network: the satellite tiles come from ArcGIS World Imagery and the trip's name from Nominatim, and neither has an offline stand-in |
| `make demo-soundtrack` | `docs-site/remotion/public/demo-music.wav`, crossfaded and normalised from a bundled acoustic track; also runs with `make demo-ui` |
| `make demo-music` | Optional ACE-Step candidates under `docs-site/static/demo/music-candidates/` |
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

The soundtrack uses `happy_acoustic_s411.opus` from the project's bundled music package, under
the same MIT licence (see `packages/immich-memories-music/LICENSE-MUSIC`). The recipe crossfades
two copies over three seconds, normalises to -18 LUFS with a -2 dB true-peak ceiling, and leaves
the fade-in and fade-out to Remotion. It needs no model, API key or media download.

The homepage and README show the hero GIF and link to the full demo with sound. The homepage
uses a still screenshot when the browser requests reduced motion. The finished trip film remains
available beside the demo.

The scenes live in `docs-site/remotion/src/scenes/`. When a page name or a button changes in the
app, the scene changes with it: `tests/e2e/test_demo_assets.py` pins the button labels the demo
shows against the real pages.

Keep the UI workflow together: brief, cut, storyboard, pool correction, export, generation,
finished file. Runs and Suggestions follow, then the CLI goes straight into the rendered film.
The storyboard scene shows the contact sheet and picture inspector. Its selected picture and
reason come from the same fixture as the browser tests; it must not invent model proposals that
the fixture did not record. Rebuild the frontend with `make frontend-check` before capturing UI
screenshots, then rebuild the Remotion demo and hero after changing the review layout.
The demo shows the product working: an error card, even a helpful one, reads as the product
failing, so refusals live in the install docs, not in a scene.

The last scene plays the closing seconds of `output-preview.mp4` and must stop before the film's
blurred ending card, because the hero GIF's last three seconds are the demo's last three. It does
not hold that moment in a constant: `make demo-fixture` measures the film with `edgedetect`, which
reads a flat zero on that card and 8 to 12 on a photograph, and writes `FILM_PICTURES_END` into
`fixture.ts`. Re-cutting the film moves the window with it.
