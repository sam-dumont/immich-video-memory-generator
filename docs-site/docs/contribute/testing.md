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
| E2E | `make e2e` (`make e2e-full` for the generation flow) | `make playwright-install`; no Immich, it runs against a fake server |

`make test` takes about 3 minutes on an M-series Mac; `make test-fast` skips the slow ones.
Integration suites skip rather than fail when their services aren't there. `make help` lists every
per-suite target with its runtime. Three suites are outside `make test-integration`: `cli`, which
re-runs the pipeline `pipeline` already covers and is the slowest in the tree; `audio`, which wants
the demucs and ACE-Step packages; and `automation`, which has no target at all (run
`pytest tests/integration/automation`).

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

`_render_single_photo` picks its encoder from `check_zscale_available()`: with zscale it uses
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
