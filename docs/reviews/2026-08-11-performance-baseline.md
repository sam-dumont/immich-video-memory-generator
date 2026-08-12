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

## Task 7 profile after structural fixes

Profiles were captured on 2026-08-12 at git HEAD `d9052ba46d9c` with dirty describe state recorded
alongside each run. The raw artifacts remain only under
`/tmp/immich-memories-profile-20260811`; this review keeps the summarized values and environment.

| Field | Value |
| --- | --- |
| Machine | MacBook Pro `Mac17,7`, Apple M5 Max / arm64, 128 GiB |
| OS | macOS 26.5.1, Darwin 25.5.0 |
| Python | CPython 3.12.11 |
| FFmpeg | 8.1 Homebrew build |
| Profile command | `uv run python scripts/profile_pipeline.py --scenario <scenario> --repetitions 3 --output-dir /tmp/immich-memories-profile-20260811` |

The controlled cases assemble two isolated, synthetic 1280×720 H.264/AAC, one-second clips at 24
fps through production analysis and `VideoAssembler`, with a 0.1-second crossfade and CRF 28. Cold
uses a fresh caller-owned probe cache per repetition; warm reuses one caller-owned cache. No
representative profile was configured, so no personal Immich library was accessed.

| Scenario | Warm-up (s) | Measured repetitions (s) | Median (s) |
| --- | ---: | --- | ---: |
| controlled-cold | 0.643805 | 0.303688, 0.302126, 0.304088 | 0.303688 |
| controlled-warm | 0.346721 | 0.266905, 0.264373, 0.271724 | 0.266905 |

Tasks 2–6 structural counts: cache construction sites fell 3→2 with zero per-download global
evictions; download workers are bounded 1–8 (default 3); analyzer GC counts fell from 4/1/2/3 to
1/0/0/0; one unchanged source now needs exactly one comprehensive `ffprobe`; and eight ordered
operational phases are persisted through schema v14.

## Native-code threshold

1. **One pure-Python function is at least 15% in controlled and representative profiles:** false.
   `streaming_assembler.blend_crossfade` is 4.2% cold and 4.8% warm; no representative profile is
   configured.
2. **Inputs cross the Python/native boundary no more than once per batch:** false/not evaluated;
   there is no qualifying function.
3. **An algorithmic, cache, NumPy/OpenCV, or concurrency alternative was measured and rejected:**
   false; Tasks 2–6 made structural changes but did not reject an alternative for a qualifying
   native prototype.
4. **A prototype improves end-to-end median by at least 20% without output changes:** false; no
   prototype was justified or run.
5. **CI builds wheels for supported Python/platform targets:** false; no Cython build or wheel
   matrix exists.

Decision: no Cython

I/O and process waiting dominate the controlled profiles, and no pure-Python candidate crosses the
first threshold. No dependency or build configuration is added.

## Task 8 final P1 verification

Re-measured on 2026-08-12 at git HEAD `0f3c5724b8c6`. The machine, Python, FFmpeg, codecs,
fixture identities, and repetition method match the Task 1 baseline above. The runs used separate
roots under `/private/tmp`; no tracked benchmark JSON was read or changed. Negative percentages
below mean the final run was faster.

### Assembly before and after

| Scenario | Task 1 median (s) | Final warm-up (s) | Final measured repetitions (s) | Final median (s) | Median change | Final child peak RSS (MiB) |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| minimal | 0.834 | 0.763931 | 0.749263, 0.754121, 0.746069 | 0.749263 | −10.2% | 415.3 |
| typical | 4.377 | 4.105397 | 4.085556, 4.146204, 4.120570 | 4.120570 | −5.9% | 962.5 |
| heavy | 13.127 | 11.850263 | 12.051343, 12.239880, 12.201445 | 12.201445 | −7.1% | 964.6 |

The Task 1 child-process high-water marks were roughly 415 MiB for minimal and 962–965 MiB for
typical/heavy, so the final RSS ceilings did not materially move. Median Python heap peaks in the
final warm repetitions were 26.4, 59.4, and 59.4 MiB respectively.

### Configured Immich pipeline before and after

The source identity still matches Task 1 exactly: two H.265 clips totaling 3.393333 seconds at
18.182 and 25.882 fps, with 1280×720 H.264 output. The full case again applied the explicit music
fixture in the warm-up and all three repetitions.

| Scenario | Task 1 median (s) | Final warm-up (s) | Final measured repetitions (s) | Final median (s) | Median change | Final child peak RSS (MiB) |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| Immich assembly only | 1.343 | 6.761957 | 1.145041, 1.143512, 1.243138 | 1.145041 | −14.7% | 312.3 |
| Immich with titles | 5.330 | 5.910481 | 4.929936, 4.919682, 4.928772 | 4.928772 | −7.5% | 312.4 |
| Immich full pipeline, explicit music | 6.015 | 6.248759 | 5.543774, 5.569385, 5.536920 | 5.543774 | −7.8% | 312.4 |

Task 1 did not retain a comparable pipeline RSS table. The final warm-repetition Python heap
medians were 26.5 MiB for assembly only, 236.6 MiB with titles, and 234.0 MiB for the full case.
The child RSS value is still a cumulative subprocess high-water mark, not isolated allocation per
scenario.

### Deterministic structural evidence

- Twenty cache downloads perform one recursive cache scan at batch start and no end rescan when
  the manifest is unchanged. A genuine external mutation is the only covered path that adds the
  fallback scan, for two scans total.
- Eight duration/resolution/frame-rate/codec/HDR/primaries/audio-bitrate consumers for one
  unchanged file share one comprehensive JSON `ffprobe` subprocess.
- Six simulated 100 ms downloads use exactly three active workers, finish under 350 ms, retain
  input order, and close one client per worker.
- Analyzer teardown performs at most one `gc.collect()` for a batch, including a loaded audio
  model, and repeated close calls do not collect again.

The required structural command passed 90 tests in 2.93 seconds. The final full suite after the
read-only CLI fix passed 4,332 tests with 7 skipped and 661 deselected in 110.97 seconds.

### Launch and operational gates

`make launch-check` passed on the final diff: Ruff, formatting, mypy, Xenon, the 1,000-line hard
limit, 4,332 unit
tests, sdist/wheel build and Twine validation, docs build, and 24 hermetic E2E tests including the
real Chromium render. The focused operational-phase and storage-report gate passed 26 tests.

The first real `runs storage --json` audit found that generic CLI initialization unconditionally
called `chmod(0700)` on directories already at mode 0700, changing their APFS ctime. The fix now
checks the existing mode before normalizing it. Its RED test observed three unwanted chmod calls;
the GREEN tests prove secure directories keep their ctime while insecure directories still become
0700. The affected config/storage/CLI suite passed 200 tests.

After that fix, exact before/after manifests matched for the config/cache/projects directory
metadata, the database metadata and SHA-256, every entry below the cache root, and every entry
below the output root. The read-only report found 6,700,755,517 bytes across 3,359 files:

- 6,700,679,737 bytes across 3,358 cache files classified `unknown`;
- 75,780 bytes in one output-root file classified `orphaned`;
- zero completed, failed, running, or pending-delivery entries.

The largest directory was `preview-cache` at 5,455,153,242 bytes. Nothing was deleted, no run row
or database byte changed, and the LaunchAgent remained untouched and unloaded.
