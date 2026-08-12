# Performance baseline — 2026-08-12

## Scope

This is the pre-optimization, synthetic assembly baseline for P1 Task 1. Fixtures are generated
under pytest temporary roots. Each scenario uses one warm-up, then three measured repetitions with
separate output paths. The reported wall time and resource fields are medians; raw wall samples are
retained below. Reproduction metadata—including platform, CPU, and Git subprocess probes—is
collected before the timer, tracemalloc, and `RUSAGE_CHILDREN` snapshots start.

`RUSAGE_CHILDREN.ru_maxrss` is a process-lifetime high-water mark on this platform, not a reset per
repetition. It remains raw diagnostic context only and is not presented as a comparable median peak
measurement; Task 8 must change the measurement method before using it for comparison.

Command run:

```bash
uv run pytest tests/integration/assembly/test_perf_assembly.py -q -m integration
```

The command passed 4 tests in 73.34 seconds and wrote the reproduction schema in
`tests/perf-results.json`. That generated file is not baseline source and was restored to its
tracked pre-run content after the evidence below was transcribed.

## Environment and run identity

- Measured checkout revision reported by the harness: `4f8f280`. This is the committed parent of
  the review fixes, not a claim that the dirty measurement tree had that exact content.
- Worktree state during measurement: dirty with the Task 1 review fixes. `MagicMock/` was untracked
  and untouched.
- Final committed harness identity: the commit containing this document, with subject
  `test: harden performance baseline identity`. A self-referential commit hash is deliberately not
  fabricated.
- Platform: `macOS-26.5.1-arm64-arm-64bit`.
- Python: `3.12.11`.
- CPU: `Apple M5 Max` (parsed from the macOS hardware profile, with portable fallbacks).
- Media: synthetic H.264 MP4, 30 fps; all measured assembly scenarios are **warm** fixture-cache
  runs. Cold-cache assembly was not separately sampled in this task.

## Controlled assembly medians

| Scenario | Input | Warm-up | Measured wall seconds | Median wall seconds |
|---|---:|---:|---:|---:|
| `minimal` | 2 × 3s, 1280×720 | 0.834 | 0.790, 0.774, 0.776 | 0.776 |
| `typical` | 5 × 5s, 1920×1080 | 4.162 | 4.197, 4.214, 4.227 | 4.214 |
| `heavy` | 8 × 10s, 1920×1080 | 12.212 | 12.318, 12.474, 12.539 | 12.474 |

## Pipeline baseline

Not executed: existing benchmark requires a live personal Immich server and violates this plan's
isolation constraint. No pipeline median is fabricated. The harness now has the same one warm-up
plus three isolated-output repetition contract for a future non-personal integration environment.

## Existing audit observations

The launch-readiness audit recorded roughly 80–180 seconds of extraction and 190–350 seconds of
assembly for a real scheduled 4K memory. Those are historical observations, not comparable to this
controlled synthetic baseline. The same audit identified the old fixture-duration filename collision
that this task repairs.

## P1 Task 7 controlled profiles and native-code decision

Raw `cProfile` files plus top-50 cumulative and self-time tables are isolated under
`/tmp/immich-memories-profile-20260811`; they are deliberately not committed. Both commands used
fresh synthetic 2 × 3-second H.264 1280×720, 30 fps clips. Cold creates fixtures once per
repetition before both measured stages; warm creates them once before all repetitions. Neither mode uses Immich, user
configuration, cache, database, output directory, or network. Each repetition separately profiles
assembly and hermetic analysis (`SceneDetector.detect(..., extract_keyframes=False)` plus local
audio-boundary detection); LLM and network clients are not constructed.

```bash
uv run python scripts/profile_pipeline.py --scenario controlled-cold --repetitions 3 --output-dir /tmp/immich-memories-profile-20260811
uv run python scripts/profile_pipeline.py --scenario controlled-warm --repetitions 3 --output-dir /tmp/immich-memories-profile-20260811
```

Environment: revision `4b4cdb8` (dirty Task 7 review-fix worktree); macOS-26.5.1-arm64-arm-64bit; Python
3.12.11; CPU fingerprint `Apple M5 Max`; FFmpeg/ffprobe 8.1. `cProfile` sees Python execution and wait time,
not child-process internals. The subprocess values below are non-overlapping `subprocess.run` plus
direct `Popen.wait` cumulative-time estimates, not FFmpeg CPU time.

| Scenario | Stage | Three wall seconds | Median wall seconds | Subprocess-wait estimate, seconds |
|---|---|---:|---:|---:|
| cold | assembly | 0.749, 0.745, 0.735 | 0.745 | 0.220, 0.217, 0.215 |
| cold | analysis | 0.090, 0.091, 0.090 | 0.090 | 0.028, 0.028, 0.029 |
| warm | assembly | 0.721, 0.741, 0.698 | 0.721 | 0.176, 0.176, 0.174 |
| warm | analysis | 0.091, 0.092, 0.091 | 0.091 | 0.030, 0.030, 0.029 |

An unprofiled warm-up preceded the three measurements: cold assembly/analysis 0.843/0.163 seconds;
warm assembly/analysis 0.732/0.167 seconds. End-to-end medians (assembly plus analysis for each
matching repetition) are 0.837 seconds cold and 0.812 seconds warm. The stable assembly bottleneck
is native I/O: buffered-reader self time is 0.342–0.371 seconds (49.1–50.1% of warm assembly samples),
followed by poll wait at 0.159–0.198 seconds and buffered writes at
0.123–0.138 seconds. `blend_crossfade` is a Python wrapper around per-frame NumPy operations, not a
standalone pure-Python candidate; its 0.038-second self time is 4.5% of the cold 0.837-second
assembly-plus-analysis median and 4.7% of the warm 0.812-second median. `_mean_pixel_distance`
likewise delegates per-frame work to NumPy/OpenCV; its cold cumulative time is 0.012 seconds in all
three samples (about 1.4% end-to-end) and warm is 0.012–0.014 seconds (about 1.5–1.7%). The next
bottleneck is therefore FFmpeg/pipe I/O, not a Python loop.

### Decision: no Cython

All five required conditions must pass; they do not.

1. **No.** No pure-Python function consumes 15% of end-to-end wall time in both controlled modes:
   `blend_crossfade` is a 4.5% cold and 4.7% warm end-to-end wrapper, while
   `_mean_pixel_distance` is below 2% of either assembly-plus-analysis path. No representative
   profile was explicitly configured.
2. **Not reached.** No candidate clears condition 1, so Python/native boundary crossings were not
   accepted as a Cython candidate.
3. **No.** A Cython experiment would bypass the measured result: the profile points first to
   FFmpeg/pipe I/O and existing OpenCV/PySceneDetect native work, so an algorithmic, cache,
   NumPy/OpenCV, or concurrency alternative has not been measured and rejected.
4. **No.** No prototype was justified; consequently there is no measured 20% end-to-end median
   improvement with identical outputs.
5. **No.** There is no Cython build or supported-platform wheel matrix in CI.

No representative real-library run was configured, so none was attempted. It cannot turn this into
an approval because controlled condition 1 already fails. No Cython dependency or build configuration
was added.

## P1 Task 8 final comparison

The controlled assembly benchmark was rerun after the structural changes with the same Apple M5
Max, macOS 26.5.1, Python 3.12.11, warm-cache fixture identities, codecs, frame rates, resolutions,
durations, warm-up, and three-repetition contract as the baseline.

```bash
uv run pytest tests/integration/assembly/test_perf_assembly.py -q -m integration
```

Result: 4 passed in 75.07 seconds. The generated JSON was inspected for comparison and restored to
its tracked content; it is not committed benchmark evidence.

| Scenario | Baseline median | Final warm-up | Final measured wall seconds | Final median | Change |
|---|---:|---:|---:|---:|---:|
| `minimal` | 0.776s | 0.744s | 0.688, 0.685, 0.699 | 0.688s | 11.3% faster |
| `typical` | 4.214s | 4.160s | 4.075, 4.222, 4.151 | 4.151s | 1.5% faster |
| `heavy` | 12.474s | 12.699s | 12.756, 13.317, 13.002 | 13.002s | 4.2% slower |

The minimal and typical changes are improvements. The heavy result is a regression watch item,
not a win: one three-sample local run is insufficient to distinguish a stable 4.2% regression from
host noise. The CI comparison therefore remains advisory until it has ten histories with matching
workload and environment identity. The benchmark name includes that identity, so the comparison
action cannot silently select a run from different hardware.

No before/after peak-memory claim is made. The baseline's child-process RSS field is a
process-lifetime high-water mark on macOS and cannot be compared per repetition without a different
measurement method.

The deterministic structural gate passed 70 tests:

```bash
uv run pytest tests/test_pipeline_efficiency.py tests/test_download_coordinator.py \
  tests/test_probe_cache.py tests/test_video_cache.py -q
```

| Contract | Final evidence |
|---|---|
| Cache maintenance | A batch of 20 downloads performs one manifest scan; legacy per-download global eviction is not called. |
| Download overlap | Six 100ms downloads use at most three workers, finish under 350ms, retain input order, and close each worker-owned client. |
| Probe reuse | Duration, resolution, codec, HDR, audio, rotation, and frame rate share one ffprobe process for an unchanged file; changed identity triggers a new probe. |
| Analyzer lifecycle | Reusable analysis services close once per batch, including failure paths; full GC is no longer forced per clip. |

The live pipeline benchmark remains unrun because its module imports and probes the configured
personal Immich server. Running it would violate the isolation rule. No personal library was read,
generated from, or uploaded to for this comparison.

### Daily trip-discovery input cache

The final P1 pass also closed the repeated full-year trip query. `auto run` now fingerprints the
Immich server, API-key scope, and monthly bucket counts, stores a seven-day rolling-coverage
snapshot with an atomic replacement, and hashes rather than stores the server URL and credential.
Each read validates the serialized `Asset` models and both coverage boundaries, then filters them
to the current day's one-year window. Before reuse, a one-result `updatedAfter` search checks for
same-count replacements and edits to GPS, city, timestamp, or other asset metadata. A changed
fingerprint, newer metadata, expired or undersized coverage, malformed JSON, a failed freshness
probe, or a failed write falls back to the normal Immich query.

The eleven cache-specific tests and the 293-test automation/API/status/storage neighborhood passed.
They include credential separation, same-count metadata edits, rolling coverage, corrupt input,
freshness failure, failed atomic replacement, and concurrent writers. Independent re-review found
no Critical or Important issues. This change does not affect the controlled assembly timings above;
it removes redundant Immich API work from the daily suggestion path.

## Final launch verification

After fixing the regressions found during the repeated full-suite and independent-review passes,
the complete command was rerun from the start:

```bash
make launch-check
uv run pytest tests/test_operational_phases.py tests/test_storage_report.py -q
```

The final result is 4,232 passed, 7 skipped, and 658 deselected in the full suite; 13 additional
operational/storage tests passed. Ruff, formatting across 510 Python files, mypy across 244 source
files, complexity, and the 1,000-line hard limit passed. The `0.37.2.dev138` wheel and sdist built
and passed Twine validation. The production documentation build passed. The required hermetic E2E
gate passed 24 tests, including a real Chromium render and output probe against the local fake
Immich v3 service.

The preserved `com.immich-memories.auto` LaunchAgent remains unloaded. The gate did not read from,
generate from, or upload to the personal Immich library, and it performed no storage cleanup.
