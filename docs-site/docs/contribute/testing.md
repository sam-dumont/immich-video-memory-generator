---
title: Testing Guide
---

# Testing Guide

Two suites: fast unit tests that run everywhere, and integration and E2E tests that need real
services (FFmpeg, Immich, a browser). `uv run pytest tests/ --collect-only -q` prints the current
split. No count is written down here, because it moves with every PR.

## Testing tiers

| Tier | Where it runs | Command | What it needs |
|------|--------------|---------|---------------|
| **Unit tests** | CI (Linux + macOS) + local | `make test` | Mostly nothing. A handful do encode real media, so FFmpeg has to be on the `PATH` |
| **Extras** | CI + local | `make test-extras` | The torch-family extras (demucs/editorial). CI's extras job installs `dev,audio` on Linux and `dev,mac,audio` on macOS, neither of which pulls torch, so a green CI run does not prove the torch paths ran. They are effectively a local tier |
| **Integration tests** | Local + self-hosted Linux GPU runner | `make test-integration` | FFmpeg + Immich server |
| **E2E (Playwright)** | CI launch check + local | `make e2e` (`make e2e-full` for the generation flow) | `make playwright-install`, no Immich (fake server) |

### Unit tests

Cover pure logic: selection rules, config parsing, data models, assembly settings, helper functions. No Immich and no network. Not quite no FFmpeg: a few render real media through it, so the binary has to be there.

```bash
make test          # Run all unit tests (~3 min on an M-series Mac, slower on CI)
make test-fast     # Skip slow tests
```

### Integration tests

Cover the real pipeline: download from Immich, FFmpeg assembly, video output validation, music mixing. They read from Immich and never write to it: upload-back is the one mutation and it is mocked. A suite skips rather than fails when its services aren't there.

```bash
make test-integration            # Every suite except cli, audio and automation
make test-integration-assembly   # One suite: assembly, audio, audio-mixing, auth, cli, live-photos, photos, pipeline, processing, titles
```

Each suite is a folder under `tests/integration/`, and most have their own `make test-integration-<suite>` target with a rough runtime (see the table in `CLAUDE.md`). Three are not in the aggregate target: `cli`, because it re-runs the full pipeline that `pipeline` already covers and is the slowest suite in the tree (`make help` prints its estimate); `audio`, because it wants the demucs and ACE-Step packages; and `automation`, which has no target at all. Run it with `pytest tests/integration/automation` until one exists.

What they need: FFmpeg on the `PATH` (`brew install ffmpeg` or `apt install ffmpeg`), an Immich server reachable from `~/.immich-memories/config.yaml`, and at least two clips under 30s in that library.

## Coverage and diff-cover

CI runs unit tests and uploads `coverage.xml` to Codecov under the `unittests` flag. The self-hosted GPU runner runs the integration suites and uploads its coverage under the `integration-linux` flag; Codecov merges the two. The per-suite XMLs that `make test-integration` writes locally (`tests/*-coverage.xml`, `tests/*-junit.xml`) are gitignored: they are for your own inspection, not for committing.

A PR needs 80% coverage on the lines it changes. Not 95%, which forces tests for trivial code, and not 50%, which is too lenient. The gate skips itself with a warning when the diff is under 10 source lines or over 1000, rather than pretend a threshold means anything there, and `analysis/apple_vision*.py` is excluded outright.

Before checking, CI runs the FFmpeg-only integration suites covering the paths your diff touches, and only those, then merges their coverage into the diff-cover run. So code reachable only through FFmpeg is covered for you: you do not need to write unit tests for it. To reproduce locally exactly what CI will see:

```bash
make integration-coverage-for-diff   # runs only the suites your diff touches
make diff-cover-local                # merges them with unit coverage, same as CI
```

If diff-cover still fails after that, the uncovered lines are not reachable from an integration suite and do need unit tests. Subprocess boundaries can be stubbed rather than run for real: `tests/test_ffmpeg_pipe.py` shows the pattern.

If you changed `src/immich_memories/processing/`, `analysis/`, `titles/`, or `generate.py`, run the matching integration suite locally (`make test-integration-processing`, `make test-integration-titles`, ...) before pushing, so you catch FFmpeg regressions before the GPU runner does.

## Writing integration tests

1. **Mock WRITES, not READS**: use real Immich for fetching assets, real FFmpeg for encoding. Only mock upload/mutation operations.
2. **Use short clips**: filter to clips under 30s, limit to 2-3 per test. Full pipeline tests should complete in under 2 minutes.
3. **Skip gracefully**: use `requires_ffmpeg` and `requires_immich` markers. Tests skip (not fail) when services are unavailable.
4. **Assert properties, not content**: verify "valid video exists" and "duration > 0", not specific pixel values or exact durations. Content is non-deterministic.
5. **Log during tests**: `make test-integration` shows live logs (`--log-cli-level=INFO`). Use this to debug slow or failing tests.

### Example

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

## Test files overview

```
tests/
├── test_*.py                # Unit tests (CI + local)
├── benchmarks/, performance/ # Timing benchmarks (make benchmark*)
├── e2e/                     # Playwright E2E against a fake Immich server (make e2e, make screenshots)
└── integration/
    ├── conftest.py          # FFmpeg fixtures, requires_ffmpeg marker
    ├── immich_fixtures.py   # requires_immich, short-clip fixtures
    ├── assembly/            # make test-integration-assembly   (FFmpeg only)
    ├── audio/               # make test-integration-audio      (demucs/acestep packages)
    ├── audio_mixing/        # make test-integration-audio-mixing (FFmpeg only, loop seams)
    ├── auth/                # make test-integration-auth       (no external deps)
    ├── automation/          # auto suggest/run (no dedicated target yet)
    ├── cli/                 # make test-integration-cli        (full pipeline, slow)
    ├── live_photos/         # make test-integration-live-photos (FFmpeg + Immich)
    ├── photos/              # make test-integration-photos     (FFmpeg only)
    ├── pipeline/            # make test-integration-pipeline   (FFmpeg + Immich)
    ├── processing/          # make test-integration-processing (FFmpeg only)
    └── titles/              # make test-integration-titles     (FFmpeg only, pixel tests)
```

## When CI fails but nothing failed

A red `Test (Python 3.12, ubuntu-latest)` usually reads as *your code broke on
Linux*. Often it means the runner was taken away mid-suite. The two look
identical on the PR page and are easy to separate one API call down.

### A reclaimed job is an unverified job

A cancelled job is a scheduling artefact, and it is tempting to treat it as
noise to re-run at leisure. It is not noise. **It is a job that did not run**, so
merging while one is outstanding means merging on the strength of whichever jobs
happened to survive.

That is not hypothetical. `TestPhotoPlaceCaption` reached `main` broken and
stayed there through two PRs:

| PR | macOS job | merged |
|---|---|---|
| introduced the test | **failure** | yes |
| shortened the test | **all three cancelled, never ran** | yes |
| next merge | n/a | failure finally surfaced on main |

The test had never once passed on a macOS runner. Nothing reported it, because
the job was either red-and-ignored or reclaimed, and every branch cut from main
afterwards inherited a red macOS job that was nobody's own change.

Before merging, check that each job **ran**, not just that nothing is red.

### Read the step, not the log

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id> \
  -q '.steps[] | select(.conclusion=="cancelled" or .conclusion=="failure") | "\(.name) -> \(.conclusion)"'
```

`Run tests with coverage -> cancelled`, with everything downstream `skipped`, is
the signature of a runner that died. No assertion ever ran.

`gh run view --log-failed` returns **nothing** in this case: precisely because
nothing failed. An empty failure log is evidence, not a broken tool.

### Check how far it got

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id> \
  -q '.steps[] | select(.name=="Run tests with coverage") | "\(.started_at) -> \(.completed_at)"'
```

Four minutes against a suite that takes eleven means it never finished. A real
failure stops at the assertion; a reclaimed runner stops at an arbitrary point.

### Use the matrix as a control group

The test matrix runs identical code on several Python versions and two operating
systems. That is a built-in control:

- **one cell red, siblings green on the same OS** → the runner died. The test in
  flight gets the blame it does not deserve.
- **every Linux cell red, macOS green** → a real platform difference.

The matrix is a hint, not the verdict. Two cells can be reclaimed at once when
the host is under memory pressure, which looks like a platform difference and is
not. The log decides: `FAILED` lines mean a real failure, while `Error 137`
after a run of `PASSED` lines means the runner was killed. Check the log before
concluding from the pattern.

This settled a real case: a photo-caption test appeared to fail on Python 3.12
with `Error 137` (SIGKILL/OOM), and passed on 3.11 and 3.13 in the *same run* on
the *same image*. The test was correct. It was the slowest thing running
when the runner was killed.

### Heavy tests attract the blame

The OOM lands on whatever is running, which skews toward the slow tests. Two
have been trimmed for this reason rather than because they were wrong: the
loudnorm fixtures (thirty FFmpeg calls to one) and the photo-caption test (120
encoded frames to 30, to assert one string).

If a unit test renders video to check metadata, shrink the render. Weight is
what makes a test the victim.

### `cancelled` is not always ignorable

The `CI Success` gate tolerates `cancelled` because the concurrency group
cancels superseded runs. That is safe: a runner death produces
`conclusion=failure` on the *job* (`make` returns 137) even though the step
reads `cancelled`. So an OOM still fails the gate, and only genuinely superseded
runs pass through. Check `gh run list --branch <branch>` to confirm a newer run
covered the cancelled one.

### Hardware encoders are absent on CI

`_render_single_photo` picks its encoder from `check_zscale_available()`: with
zscale it uses `hevc_videotoolbox`, without it `libx264`. VideoToolbox writes no
file inside CI's macOS VM, and the function returns `None` when encoding
produces nothing, so the failure surfaces as whatever the test asserted next,
not as an encoder error.

Any unit test that reaches the photo encoder needs the software path forced:

```python
monkeypatch.setattr(
    "immich_memories.processing.hdr_utilities.check_zscale_available", lambda: False
)
```

It passes on a real Mac either way, which is what makes this one easy to merge
and hard to notice.

### Re-running

`gh run rerun <run-id> --failed` is rejected while any job in the run is still
in progress ("cannot be rerun; its workflow file may be broken": the message is
misleading). Wait for the run to complete, then re-run.

If the same cell is reclaimed three times, stop re-running and treat it as a
resource problem rather than luck.
