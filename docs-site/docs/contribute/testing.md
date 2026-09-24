---
title: Testing Guide
---

# Testing guide

Two suites: fast unit tests that run everywhere, and integration and E2E tests that need real
services. `uv run pytest tests/ --collect-only -q` prints the current split.

| Tier | Command | What it needs |
|------|---------|---------------|
| Unit | `make test` | FFmpeg on the `PATH`: a handful of unit tests encode real media |
| Extras | `make test-extras` | The torch-family extras (demucs/editorial). CI's extras job installs neither, so a green CI run does not prove the torch paths ran |
| Integration | `make test-integration` | FFmpeg, an Immich server in `~/.immich-memories/config.yaml`, and at least two clips under 30s in that library |
| Real-Immich gate | `make test-immich-gate IMMICH_GATE_VERSION=v2` (or `v3`) | Docker and FFmpeg. It starts its own Immich and fixture library, and fails when Immich does not come up |
| E2E | `make e2e` (`make e2e-full` for the generation flow) | `make playwright-install`; no Immich, it runs against a fake server |

`make test` takes about 3 minutes on an M-series Mac; `make test-fast` skips the slow ones.
Integration suites skip rather than fail when their services aren't there, unless `REQUIRE_IMMICH=1` (the gate below sets it). `make help` lists every
per-suite target with its runtime. Three suites are outside `make test-integration`: `cli`, which
re-runs the pipeline `pipeline` already covers and is the slowest in the tree; `audio`, which wants
the demucs and ACE-Step packages; and `automation`, which has no target at all (run
`pytest tests/integration/automation`).

## The real-Immich gate

Unit tests talk to a patched HTTP client and the integration suites skip when no Immich is
configured, so neither proves the product still speaks to a real server. The `Immich Gate` check
does, on every PR, for both majors:

1. `make immich-gate-up` starts Immich (v2.7.5 or v3.2.2), Postgres and Valkey from
   `tests/integration/immich_gate/docker-compose.yml`. Every image is pinned by digest, there is no
   machine-learning container, and Postgres and the uploads live on tmpfs, so `make immich-gate-down`
   leaves no volume behind.
2. `seed.py` signs up an admin, uploads the June 2024 CC0 fixture month (133 pictures, 13 of them
   videos, with EXIF camera and capture time), places them, tags three made-up people by hand,
   files the story albums, and adds 1,010 tiny pictures in one album so reads have to go past
   Immich's 1,000-item search page. Then it writes a rules-tier config (no model, no network
   beyond this Immich) under `.immich-gate/`.
3. The tests in `tests/integration/immich_gate/` run with `REQUIRE_IMMICH=1`, which turns
   `requires_immich` and `make_immich_client()` from a skip into a failure.

| Test | What it proves on each major |
|------|------------------------------|
| connect | the key works and the client resolves the API version the server runs |
| fixture month | every video and still of a date range comes back, stills with their camera, videos with their place |
| paging | a year and an album of 1,010 pictures read whole |
| people | hand-tagged faces scope the videos per person |
| albums | story albums list and resolve by name with their counts |
| upload | a re-rendered film lands in its album and trashes the earlier copy (v2 by device identity, v3 by the provenance tag) |
| generate | `generate --memory-type monthly_highlights --no-render` on the rules tier picks a cut from the fixture month |

The gate is deliberately small and stable. Wider real-Immich coverage stays in the other
integration folders. `IMMICH_GATE_KEEP=1` leaves the stack running after the tests; a failed run
writes the server logs to `.immich-gate/immich-<version>.log` (CI uploads them as an artifact).

In CI the pinned images come from the Actions cache, not the registries: one `docker save` tarball per
major, keyed on the exact refs. `make immich-gate-fetch` loads it, and pulls whatever it lacks with
three attempts of three minutes each; `make immich-gate-save` writes it back after a cold run. A
registry that stalls then costs one attempt instead of the whole job, and an image that never
arrives still fails the gate.

## Coverage and diff-cover

CI uploads unit coverage to Codecov under the `unittests` flag and the self-hosted GPU runner
uploads the integration suites under `integration-linux`; Codecov merges the two. The per-suite
XMLs that `make test-integration` writes locally (`tests/*-coverage.xml`, `tests/*-junit.xml`) are
gitignored.

A PR needs 80% coverage on the lines it changes. The gate skips itself with a warning when the diff
is under 10 source lines or over 1000, and `analysis/apple_vision*.py` is excluded outright.

Before checking, CI runs the FFmpeg-only integration suites covering the paths your diff touches,
and only those, then merges their coverage into the diff-cover run. So code reachable only through
FFmpeg is covered for you. To reproduce what CI will see:

```bash
make integration-coverage-for-diff   # runs only the suites your diff touches
make diff-cover-local                # merges them with unit coverage, same as CI
```

If diff-cover still fails after that, the uncovered lines are not reachable from an integration
suite and do need unit tests. Subprocess boundaries can be stubbed rather than run for real:
`tests/test_ffmpeg_pipe.py` shows the pattern.

If you changed `processing/`, `analysis/`, `titles/` or `generate.py`, run the matching integration
suite locally before pushing, so you catch FFmpeg regressions before the GPU runner does.

## Writing integration tests

1. **Mock WRITES, not READS**: real Immich for fetching assets, real FFmpeg for encoding. Only mock upload and mutation.
2. **Use short clips**: under 30s, 2-3 per test. Full pipeline tests should finish in under 2 minutes.
3. **Skip gracefully**: use the `requires_ffmpeg` and `requires_immich` markers.
4. **Assert properties, not content**: "valid video exists" and "duration > 0", not exact pixels or durations. Content is non-deterministic.
5. **Log during tests**: `make test-integration` shows live logs (`--log-cli-level=INFO`).

```python
@requires_immich
class TestMyFeature:
    def test_real_pipeline(self, immich_short_clips, tmp_path):
        clips, config, client = immich_short_clips
        config.title_screens.enabled = False  # Skip for speed

        params = GenerationParams(
            clips=clips[:2],
            output_path=tmp_path / "test.mp4",
            config=config,
            client=client,
            upload_enabled=False,  # NO WRITES
        )

        result = generate_memory(params)
        assert result.exists()
        assert get_duration(ffprobe_json(result)) > 0
```

## When CI fails but nothing failed

A red `Test (Python 3.12, ubuntu-latest)` often means the runner was reclaimed mid-suite, not that
your code broke on Linux. A cancelled job is a job that did not run, so merging while one is
outstanding means merging on the strength of whichever jobs happened to survive.
`TestPhotoPlaceCaption` reached `main` broken and stayed there through two PRs that way: red and
ignored once, reclaimed and never run the second time.

Read the step, not the log:

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id> \
  -q '.steps[] | select(.conclusion=="cancelled" or .conclusion=="failure") | "\(.name) -> \(.conclusion)"'
```

`Run tests with coverage -> cancelled` with everything downstream `skipped` is a runner that died.
No assertion ever ran, which is why `gh run view --log-failed` returns nothing: an empty failure
log is evidence, not a broken tool. Swap the query for `.started_at` and `.completed_at` to see how
far it got. Four minutes against a suite that takes eleven means it never finished.

The matrix is a control group: one cell red with its siblings green on the same OS points at a dead
runner, every Linux cell red with macOS green at a real platform difference. It is a hint, not the
verdict, because two cells can be reclaimed at once under memory pressure. The log decides:
`FAILED` lines mean a real failure, `Error 137` after a run of `PASSED` lines means the runner was
killed. One photo-caption test failed with `Error 137` on Python 3.12 and passed on 3.11 and 3.13
in the same run on the same image. The test was correct: it was the slowest thing running when the
runner was killed.

The OOM lands on whatever is running, which skews toward the slow tests. Two have been trimmed for
that reason rather than because they were wrong: the loudnorm fixtures (thirty FFmpeg calls to one)
and the photo-caption test (120 encoded frames to 30, to assert one string). If a unit test renders
video to check metadata, shrink the render.

The `CI Success` gate tolerates `cancelled`, because the concurrency group cancels superseded runs
and a runner death still produces `conclusion=failure` on the job (`make` returns 137). Check
`gh run list --branch <branch>` to confirm a newer run covered the cancelled one.

`gh run rerun <run-id> --failed` is rejected while any job in the run is still in progress; the
error message about a broken workflow file is misleading. Wait for the run to complete. If the same
cell is reclaimed three times, treat it as a resource problem rather than luck.

## Hardware encoders are absent on CI

`render_single_photo` picks its encoder from `check_zscale_available()`: with zscale it uses
`hevc_videotoolbox`, without it `libx264`. VideoToolbox writes no file inside CI's macOS VM, and
the function returns `None` when encoding produces nothing, so the failure surfaces as whatever the
test asserted next, not as an encoder error. Any unit test that reaches the photo encoder needs the
software path forced:

```python
monkeypatch.setattr(
    "immich_memories.processing.hdr_utilities.check_zscale_available", lambda: False
)
```

It passes on a real Mac either way, which is what makes this one easy to merge and hard to notice.
