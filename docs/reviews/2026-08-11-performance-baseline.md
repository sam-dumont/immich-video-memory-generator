# Performance baseline — 2026-08-12

## Scope

This is the pre-optimization, synthetic assembly baseline for P1 Task 1. Fixtures are generated
under pytest temporary roots. Each scenario uses one warm-up, then three measured repetitions with
separate output paths. The reported wall time and resource fields are medians; raw wall samples are
retained below. `RUSAGE_CHILDREN.ru_maxrss` is a process-lifetime high-water mark on this platform,
not a reset per repetition. It remains raw diagnostic context only and is not presented as a
comparable median peak measurement; Task 8 must change the measurement method before using it for
comparison.

Command run:

```bash
uv run pytest tests/integration/assembly/test_perf_assembly.py -q -m integration
```

The command completed and wrote the new reproduction schema in `tests/perf-results.json`. That
generated file is not baseline source and is not committed.

## Environment and run identity

- Git revision: `02372b8`.
- Worktree state: dirty with the uncommitted P1 Task 1 harness changes listed in the command's
  benchmark metadata; `MagicMock/` was untracked and untouched.
- Platform: `macOS-26.5.1-arm64-arm-64bit`.
- Python: `3.12.11`.
- CPU: `arm` (the portable platform report available to Python).
- Media: synthetic H.264 MP4, 30 fps; all measured assembly scenarios are **warm** fixture-cache
  runs. Cold-cache assembly was not separately sampled in this task.

## Controlled assembly medians

| Scenario | Input | Warm-up | Measured wall seconds | Median wall seconds |
|---|---:|---:|---:|---:|
| `minimal` | 2 × 3s, 720p | 0.802 | 0.801, 0.783, 0.784 | 0.784 |
| `typical` | 5 × 5s, 1080p | 4.197 | 4.224, 4.253, 4.256 | 4.253 |
| `heavy` | 8 × 10s, 1080p | 12.783 | 12.642, 12.797, 12.820 | 12.797 |

## Pipeline baseline

Not executed: existing benchmark requires a live personal Immich server and violates this plan's
isolation constraint. No pipeline median is fabricated. The harness now has the same one warm-up
plus three isolated-output repetition contract for a future non-personal integration environment.

## Existing audit observations

The launch-readiness audit recorded roughly 80–180 seconds of extraction and 190–350 seconds of
assembly for a real scheduled 4K memory. Those are historical observations, not comparable to this
controlled synthetic baseline. The same audit identified the old fixture-duration filename collision
that this task repairs.
