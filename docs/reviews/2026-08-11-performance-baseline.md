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
