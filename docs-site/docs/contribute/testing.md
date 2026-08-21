---
title: Testing Guide
---

# Testing Guide

Immich Memories has about 5,000 tests: 4,400+ fast unit tests that run everywhere, and 600+ integration and E2E tests that need real services (FFmpeg, Immich, a browser).

## Testing Tiers

| Tier | Where it runs | Command | What it needs |
|------|--------------|---------|---------------|
| **Unit tests** | CI (Linux + macOS) + local | `make test` | Nothing external |
| **Integration tests** | Local + self-hosted Linux GPU runner | `make test-integration` | FFmpeg + Immich server |
| **E2E (Playwright)** | CI launch check + local | `make e2e` (`make e2e-full` for the generation flow) | `make playwright-install`, no Immich (fake server) |

### Unit tests

Cover pure logic: scoring math, config parsing, data models, assembly settings, helper functions. No FFmpeg, no Immich, no network.

```bash
make test          # Run all unit tests (~60s)
make test-fast     # Skip slow tests
```

### Integration tests

Cover the real pipeline: download from Immich, FFmpeg assembly, video output validation, music mixing. They **read** from Immich (no writes) and skip gracefully if services aren't available.

```bash
make test-integration            # Every suite except cli (~15 min)
make test-integration-assembly   # One suite: assembly, audio, auth, cli, live-photos, photos, pipeline, processing, titles
```

Each suite is a folder under `tests/integration/` with its own `make test-integration-<suite>` target and rough runtime (see the table in `CLAUDE.md`). `cli` re-runs the full pipeline (~15 min) and is not part of `make test-integration`.

**What's tested:**
- Real FFmpeg assembly (single clip, crossfade, smart transitions)
- Real Immich API reads (asset fetching, video download)
- `generate_memory()` end-to-end pipeline
- Music file mixing into assembled video
- Clip segment trimming (custom start/end times)
- Upload-back to Immich (mocked write, real everything else)
- CLI `generate` command with real Immich
- Scoring engine with real video frames

**What's needed:**
- FFmpeg installed (`brew install ffmpeg` or `apt install ffmpeg`)
- Immich server reachable (configured in `~/.immich-memories/config.yaml`)
- At least 2 short video clips (under 30s) in your Immich library

Tests skip gracefully if services aren't available: you won't get failures, just skips.

## Coverage and diff-cover

### How coverage works

CI runs unit tests and uploads `coverage.xml` to Codecov under the `unittests` flag. The self-hosted GPU runner runs the integration suites and uploads its coverage under the `integration-linux` flag; Codecov merges the two. The per-suite XMLs that `make test-integration` writes locally (`tests/*-coverage.xml`, `tests/*-junit.xml`) are gitignored — they are for your own inspection, not for committing.

### Workflow when you change code

1. Write your code
2. Run `make test` (unit tests, always)
3. If you changed `src/immich_memories/processing/`, `analysis/`, `titles/`, or `generate.py`, run the matching integration suite locally (`make test-integration-processing`, `make test-integration-titles`, ...) so you catch FFmpeg regressions before the GPU runner does
4. Commit and push: CI runs unit tests + diff-cover, the GPU runner runs integration

### Check coverage locally before pushing

```bash
make diff-cover-local   # Runs unit tests + checks diff coverage at 80%
```

### Why 80% threshold?

We require 80% coverage on changed lines. Not 95% (forces testing trivial code) and not 50% (too lenient). The remaining 20% covers error handling, CLI glue, and code paths that need real external services.

## Writing integration tests

### Rules

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
    ├── auth/                # make test-integration-auth       (no external deps)
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

### Read the step, not the log

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id> \
  -q '.steps[] | select(.conclusion=="cancelled" or .conclusion=="failure") | "\(.name) -> \(.conclusion)"'
```

`Run tests with coverage -> cancelled`, with everything downstream `skipped`, is
the signature of a runner that died. No assertion ever ran.

`gh run view --log-failed` returns **nothing** in this case — precisely because
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

This settled a real case: a photo-caption test appeared to fail on Python 3.12
with `Error 137` (SIGKILL/OOM), and passed on 3.11 and 3.13 in the *same run* on
the *same image*. The test was correct. It was simply the slowest thing running
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
`conclusion=failure` on the *job* — `make` returns 137 — even though the step
reads `cancelled`. So an OOM still fails the gate, and only genuinely superseded
runs pass through. Check `gh run list --branch <branch>` to confirm a newer run
covered the cancelled one.

### Re-running

`gh run rerun <run-id> --failed` is rejected while any job in the run is still
in progress ("cannot be rerun; its workflow file may be broken" — the message is
misleading). Wait for the run to complete, then re-run.

If the same cell is reclaimed three times, stop re-running and treat it as a
resource problem rather than luck.
