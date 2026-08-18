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
