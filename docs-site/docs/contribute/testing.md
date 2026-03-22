---
title: Testing Guide
---

# Testing Guide

Immich Memories uses a two-tier testing strategy: fast unit tests that run everywhere, and integration tests that need real services (FFmpeg, Immich).

## Testing Tiers

| Tier | Where it runs | Command | What it needs |
|------|--------------|---------|---------------|
| **Unit tests** | CI + local | `make test` | Nothing external |
| **Integration tests** | Local only | `make test-integration` | FFmpeg + Immich server |

### Unit tests

Cover pure logic: scoring math, config parsing, data models, assembly settings, helper functions. No FFmpeg, no Immich, no network.

```bash
make test          # Run all unit tests (~60s)
make test-fast     # Skip slow tests
```

### Integration tests

Cover the real pipeline: download from Immich, FFmpeg assembly, video output validation, music mixing. They **read** from Immich (no writes) and skip gracefully if services aren't available.

```bash
make test-integration   # Run all integration tests (~15 min)
```

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

CI runs unit tests and generates `coverage.xml`. Integration tests run locally and generate per-suite coverage XMLs (`tests/assembly-coverage.xml`, `tests/pipeline-coverage.xml`, etc.). All files are merged by CI's diff-cover check and uploaded to Codecov.

### Workflow when you change code

1. Write your code
2. Run `make test` (unit tests, always)
3. If you changed `src/immich_memories/processing/`, `analysis/`, `titles/`, or `generate.py`:
   ```bash
   make test-integration   # Runs real FFmpeg + Immich tests
   git add tests/*-coverage.xml tests/integration-junit.xml
   ```
4. Commit and push: CI merges all coverage files

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
├── test_*.py                      # Unit tests (CI + local)
├── integration/
│   ├── conftest.py                # FFmpeg fixtures, requires_ffmpeg marker
│   ├── test_assembly_real.py      # FFmpeg assembly tests
│   ├── test_generate_real.py      # generate_memory() with synthetic clips
│   ├── test_generate_scenarios.py # Music, trimming, error handling
│   └── test_cli_generate.py       # Full Immich pipeline + CLI tests
├── assembly-coverage.xml          # Committed: per-suite integration coverage
├── pipeline-coverage.xml          # Committed: per-suite integration coverage
├── photos-coverage.xml            # Committed: per-suite integration coverage
├── live-photos-coverage.xml       # Committed: per-suite integration coverage
└── integration-junit.xml          # Committed: Codecov test analytics
```
