# Pre-optimization performance baseline

Captured on 2026-08-12. The filename stays dated 2026-08-11 because it belongs to that
performance review and implementation plan.

This is the baseline before any P1 production optimization. The working tree contained only
the benchmark-harness changes needed to make these measurements honest; application source was
the exact `b1e6f088437a0c872d3fe8dff8361537e2525491` revision.

## Machine and toolchain

- Machine: MacBook Pro `Mac17,7`
- CPU: Apple M5 Max, arm64
- Memory: 128 GiB (`137438953472` bytes)
- OS: macOS 26.5.1, Darwin 25.5.0
- Python: CPython 3.12.11, Clang 20.1.4
- FFmpeg: 8.1, Homebrew build with libx264, libx265, libzimg, and VideoToolbox
- Git HEAD: `b1e6f088437a0c872d3fe8dff8361537e2525491`
- Git describe during capture: `v0.37.1-108-gb1e6f08-dirty`

The dirty suffix is expected: it names the uncommitted benchmark test/harness changes in this
task. No production code had changed.

## Method

Each scenario runs once as a cold/priming warm-up, then three measured warm repetitions. The
reported number is the median of those three repetitions. Raw values stay in the report; no
fastest-run nonsense.

Synthetic fixture names encode resolution, duration, frame rate, codec, and index. A SHA-256
fingerprint covers the complete FFmpeg argument list. Reuse happens only after ffprobe confirms
codec, dimensions, frame rate, and duration.

All fixtures, caches, databases, generated media, and JSON summaries lived under pytest or
explicit operating-system temporary roots. The tracked `tests/perf-results*.json` files were not
used or updated.

The exact required gates were:

```bash
uv run pytest tests/integration/assembly/test_perf_assembly.py -q -m integration
uv run pytest tests/integration/pipeline/test_perf_pipeline.py -q -m integration
```

Both passed: assembly 4/4 in 75.44 seconds; pipeline 4/4 in 59.45 seconds with one third-party
Taichi deprecation warning. Raw JSON was retained long enough to transcribe by adding a unique
`--basetemp=/private/tmp/...` directory to otherwise identical runs.

“Cold” is scoped to the application benchmark, not an OS page-cache purge. Assembly's warm-up is
the first assembly after fixture generation. Each pipeline scenario gets a new application video
cache and SQLite database; its warm-up pays download, migrations, and hardware discovery. The
three measured repetitions reuse that scenario cache. Source identity probing happens before the
timer and writes to a separate temporary input directory.

## Assembly baseline

Direct assembly uses the explicit software H.264 plan (`libx264`). Inputs are synthetic H.264 at
30 fps.

| Scenario | Inputs | Warm-up (s) | Measured repetitions (s) | Median (s) |
| --- | --- | ---: | --- | ---: |
| minimal | 2 × 1280×720, 3 s | 0.949 | 0.826, 0.834, 0.835 | 0.834 |
| typical | 5 × 1920×1080, 5 s | 4.325 | 4.313, 4.377, 4.437 | 4.377 |
| heavy | 8 × 1920×1080, 10 s | 12.739 | 12.925, 13.127, 13.244 | 13.127 |

The child-process RSS high-water readings were roughly 415 MiB for minimal and 962–965 MiB for
the two 1080p cases. `ru_maxrss` is cumulative within the pytest process, so treat it as a process
ceiling, not clean per-scenario allocation accounting.

## Pipeline baseline

The selected real Immich inputs totaled 3.393333 seconds. Both were H.265, at 18.182 and 25.882
fps. Output was 1280×720 H.264 through `h264_videotoolbox`. Title runs pin the
`elegant_minimal` style. The full-pipeline case uses the test suite's generated five-second music
fixture, so it performs a real mix instead of depending on ACE-Step availability.

| Scenario | Warm-up (s) | Measured repetitions (s) | Median (s) |
| --- | ---: | --- | ---: |
| Immich assembly only | 6.094 | 1.278, 1.375, 1.343 | 1.343 |
| Immich with titles | 6.123 | 5.312, 5.330, 5.373 | 5.330 |
| Immich full pipeline, explicit music | 6.640 | 5.990, 6.077, 6.015 | 6.015 |

The warm assembly-only path is cheap on this machine. Titles add about 3.99 seconds to the median;
the controlled music mix adds another 0.69 seconds. Cold startup is still expensive: the
assembly-only warm-up is about 4.5 times its warm median because it includes first download,
database migration, and capability detection. Those are the obvious P1 targets. Native
compilation is not justified by this baseline; profile evidence still has to meet Task 7's
threshold.

## Historical 4K observations — not this baseline

The earlier launch review saw real 4K extraction around **80–180 seconds** and 4K assembly around
**190–350 seconds**. Those figures came from ad hoc runs with different media, cache state, and
hardware paths. They are useful as an order-of-magnitude warning and nothing more. Do not compare
them as regressions against the controlled 720p/1080p numbers above.

## Harness defects fixed before capture

- Fixture filenames used to contain only resolution and index. A five-second clip could be reused
  for a ten-second test.
- Existing fixtures were trusted because a path existed. Codec, dimensions, frame rate, duration,
  and FFmpeg arguments were never checked.
- Results omitted input duration, codec, frame rate, cache mode, Python, platform, CPU, and Git
  revision.
- Scenarios ran once. A lucky outlier could become the published number.
- The assembly and pipeline tests wrote their live output into tracked repository JSON files.

The new contract rejects all five failure modes and keeps raw warm-up/repetition data beside the
median.
