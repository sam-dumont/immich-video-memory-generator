---
title: Testing Guide
---

# Testing Guide

Use the Makefile targets so local checks use the repository's test selection and coverage
settings. Start with [Development Setup](./development-setup.md).

## Test suites

| Suite | Command | Requirements |
|---|---|---|
| Default tests | `make test` | Python dependencies and FFmpeg for the tests that render media; no live Immich required |
| One file or node | `make test-one T=tests/test_config.py` | Depends on the selected tests |
| Optional backend tests | `make test-extras` | Tests marked `extras`; dependencies vary by test |
| Integration | `make test-integration` | FFmpeg; some suites also read from a configured Immich server |
| Browser launch | `make e2e` | Playwright Chromium and FFmpeg; uses a fake Immich server |
| Full browser suite | `make e2e-full` | Broader browser and optional visual flows |

`make test` excludes tests marked `integration` and `e2e`. Most of the remaining tests
exercise logic or replace external boundaries, but some encode real media. For example,
`TestPhotoPlaceCaption` in `tests/test_processing_coverage.py` renders a photograph before
checking its location caption. “Unit test” does not mean FFmpeg is never called.

`make test-extras` selects the `extras` marker. CI currently installs `dev,audio` on Linux
and `dev,mac,audio` on macOS for this job. That does not include Demucs or the editorial
extra, so a green job does not establish that every optional backend ran. Read the skips.

Install the browser once with `make playwright-install`. `make launch-check` combines
local checks, package validation, the docs build and the browser launch test.
`make launch-check-ci` runs the browser portion because CI has separate jobs for the rest.

## Integration tests

The aggregate `make test-integration` runs these suites:

| Target suffix | Exercises | External requirements |
|---|---|---|
| `auth` | Authentication boundaries | No external service |
| `assembly` | Assembly and transitions | FFmpeg |
| `audio-mixing` | Soundtrack mixing and loop seams | FFmpeg |
| `processing` | Probing, filters and subprocess handling | FFmpeg |
| `titles` | Title rendering and pixel checks | FFmpeg; backend-specific tests may skip |
| `photos` | Photo decoding and animation | FFmpeg |
| `pipeline` | Generation from library assets | FFmpeg and Immich; test-specific backends |
| `live-photos` | Live Photo material and merging | FFmpeg and Immich |

Run one with, for example, `make test-integration-photos`.

Two additional targets are outside the aggregate: `make test-integration-cli` runs CLI
pipeline tests, and `make test-integration-audio` needs its audio backend packages. The
`automation` folder has no dedicated target; use the existing focused-test entry point:

```bash
make test-one T="tests/integration/automation -m integration"
```

Immich fixtures load the default configuration and look for suitable short clips. Availability
markers and fixtures skip tests when their requirements are absent. A skip means that path
was not checked. Once prerequisites are present, authentication, rendering or assertion
errors can still fail a test.

Integration tests should read real media and replace upload or mutation boundaries. Keep
fixtures small, write outputs under the test's temporary directory and verify the behavior
the test names. Use pixel or duration assertions when those properties are the contract;
allow the tolerance required by frame quantization or encoding.

For software-only tests, select a software encoder at the relevant boundary. Do not assume
FFmpeg listing a hardware encoder means the CI runner can use it. The photo-caption test
forces its SDR software path; HDR tests need their own explicit encoding setup.

## CI and local checks

`.github/workflows/ci.yml` defines the current matrix:

- Pull requests: Ubuntu on Python 3.11, 3.12 and 3.13; macOS on Python 3.13.
- Main: Ubuntu and macOS on all three Python versions.
- Separate jobs cover optional extras, package and Docker builds, documentation and the
  browser launch test. Quality and security jobs gate the main test jobs.

The private GPU mirror runs integration suites and reports their coverage separately.
The public CI workflow also runs selected FFmpeg integration suites for diff coverage.

`make check` runs lint, formatting, types, file length, cyclomatic complexity and the default
tests. `make ci` adds cognitive complexity, dead code, Bandit, Semgrep, modernization,
dependency/import checks, duplication, critique, reference drift, docs voice, notices and
Compose validation. It does not reproduce every GitHub Actions job or platform.

## Coverage and diff-cover

CI uploads default-suite coverage under the `unittests` flag. The private GPU runner uses
`integration-linux`. Per-suite coverage and JUnit XML files under `tests/` are generated
artifacts and are gitignored.

The CI diff gate requires 80% coverage on changed lines. It skips diffs with fewer than
10 or more than 1000 changed Python source lines, excluding tests from that count.
`analysis/apple_vision*.py` is excluded from the coverage comparison.

For supported paths, `make integration-coverage-for-diff` runs the matching FFmpeg suites
before the coverage check. The mapping is in the Makefile: titles, processing, photos,
audio and top-level generation modules. This selects suites; it does not guarantee those
suites exercise every changed line.

```bash
make integration-coverage-for-diff
make diff-cover-local
```

Both compare against `origin/main`. The integration selector uses `origin/main...HEAD`,
so uncommitted edits do not change its suite selection. Run the relevant suite explicitly
while working. `diff-cover-local` reruns default tests and merges available integration
reports; unlike `diff-cover-ci`, it does not apply the small/large-diff skips or Apple Vision
exclusion. Use fresh reports so old results do not hide missing coverage.

When coverage is missing, inspect the lines and choose a test at the boundary that can
exercise them. FFmpeg behavior often needs an integration test; subprocess error handling
can be checked with a fake process. `tests/test_ffmpeg_pipe.py` shows the latter pattern.
Do not add tests merely to repeat constants or force a percentage.

## Interrupted CI jobs

Read the job annotations and full log before blaming an assertion or dismissing a failure.
`cancelled` can mean a superseded run, a manual cancellation or an interrupted runner.
Exit 137 indicates SIGKILL, which can come from resource limits or a terminated runner.
Neither tells you whether the code is correct.

```bash
gh run view <run-id>
gh run view <run-id> --log
gh run list --branch <branch>
```

An empty `--log-failed` result is inconclusive. Compare the matrix cells for clues, then
check the actual failing step. A single failing Python version can still expose a real
version-specific bug.

The current `CI Success` aggregator accepts `success`, `skipped` and `cancelled` job
results. Check that required jobs completed for the commit being reviewed; the aggregate
alone does not prove that a cancelled job has a successful replacement.

After the run completes, `gh run rerun <run-id> --failed` retries failed jobs. Repeated
kills need investigation: reduce an unnecessarily large fixture or find the resource
limit instead of treating repeated reruns as validation.
