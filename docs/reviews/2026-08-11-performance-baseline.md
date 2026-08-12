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
