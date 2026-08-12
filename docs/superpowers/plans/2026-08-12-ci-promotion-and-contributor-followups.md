# CI Promotion and Contributor Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PR #277 pass every required launch gate, publish the reviewed branch, close superseded reports accurately, and preserve the useful independent ideas from contributor PR #273.

**Architecture:** The existing `codex/encoding-delivery-final` branch remains the launch release candidate. CI contract repairs stay on that branch and are proven locally before it updates PR #277. Contributor-derived Taichi, FFmpeg-pipe, and disconnected-UI work starts only after #277 is green and is kept in a separate follow-up branch or issues so the launch PR cannot become a moving target.

**Tech Stack:** Python 3.11–3.13, pytest, Make, Git LFS, GitHub Actions, uv, pip-audit, complexipy, jscpd, FFmpeg, Taichi, NiceGUI, GitHub CLI.

## Global Constraints

- Execute Tasks 1–5 in order; do not begin a later task until the preceding task's gate passes.
- Preserve the user's untracked `MagicMock/` directory and all unrelated worktree changes.
- Use RED → GREEN for behavior and contract changes. Existing failing GitHub checks count as the initial RED only when their exact failure is reproduced locally or by a focused contract test.
- Do not weaken a required gate merely to make CI green.
- Do not regenerate a complexity baseline until every newly introduced over-threshold function has been reviewed and either refactored or explicitly accepted as legacy debt.
- Do not suppress fixable dependency advisories. Upgrade within compatible ranges and run the complete application gate.
- Do not close issues or PR #273 until PR #277 is green on the updated head.
- Credit Sanji78 when retaining ideas reported in #271, #272, or PR #273.
- Keep the installed LaunchAgent unloaded throughout this work.

---

### Task 1: Repair every CI-only P0 failure

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `Makefile`
- Modify: `tests/test_version_contract.py`
- Modify: `tests/test_ci_contract.py` or the closest existing CI contract test
- Modify: `complexipy-snapshot.json` only after reviewing actual deltas
- Modify: `pyproject.toml` and `uv.lock` for compatible security upgrades
- Modify: `docs/reviews/2026-08-11-launch-readiness-audit.md`

**Interfaces:**
- Produces: a launch-check job with materialized Git LFS fixtures.
- Produces: a Make target contract parser that ignores target-specific variable assignments.
- Produces: a pinned, supported jscpd invocation.
- Produces: a cognitive-complexity gate that distinguishes reviewed debt from new violations.
- Produces: a locked dependency environment with zero fixable advisories under the repository audit policy.

- [ ] **Step 1: Add focused failing CI contract tests**

Add assertions that the hermetic launch job pulls Git LFS before tests, that the launch target prerequisite helper ignores `TARGET: VARIABLE = value` declarations, and that the duplication command pins jscpd and uses only supported arguments.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/test_version_contract.py tests/test_ci_contract.py -q`

Expected: failures identify missing LFS materialization and the unpinned/unsupported duplication command. The existing launch-target test reproduces the target-specific-variable parser failure when run against the PR head.

- [ ] **Step 3: Implement the minimal LFS, Make-parser, and jscpd fixes**

Copy the repository's existing LFS cache/pull pattern into the launch job. Make the test helper select real prerequisite declarations rather than target-specific assignments. Pin jscpd to a known version and pass its supported ignore form.

- [ ] **Step 4: Verify the three focused fixes**

Run the focused pytest command, `make duplication`, and the Ultra HDR tests from an LFS-materialized checkout.

- [ ] **Step 5: Audit cognitive-complexity deltas**

Run complexipy against the final branch, compare every over-15 function with `complexipy-snapshot.json`, refactor newly introduced violations with focused tests, and update the snapshot only for unchanged or explicitly reviewed legacy functions.

- [ ] **Step 6: Upgrade vulnerable dependencies deliberately**

Use `uv tree --invert` to identify direct owners, upgrade the smallest compatible package set, regenerate `uv.lock`, then run `make pip-audit`. Do not jump Starlette or other transitive framework packages across an incompatible major without verifying NiceGUI/FastAPI constraints.

- [ ] **Step 7: Verify the benchmark fix on the final branch**

Run: `make benchmark-assembly`

Expected: three scenarios complete and both `tests/perf-results.json` and `tests/benchmark-assembly.json` are valid. Restore tracked result fixtures after inspection.

- [ ] **Step 8: Record the CI addendum and commit coherent fixes**

Append exact commands/results to the launch audit. Commit independent gate repairs separately so remote failures remain bisectable.

### Task 2: Run the actual CI-equivalent local gate

**Files:**
- Modify only files required by failures discovered during this gate.

**Interfaces:**
- Consumes: the repaired CI commands from Task 1.
- Produces: fresh local evidence for every required GitHub job, not only `make launch-check`.

- [ ] **Step 1: Run source and policy gates**

Run the Make targets used by CI: lint/format, mypy, file length, Xenon, complexipy, Vulture, refurb, deptry, import-linter, duplication, AI-smell audit, and pip-audit.

- [ ] **Step 2: Run the hermetic launch gate from scratch**

Run: `make launch-check`

- [ ] **Step 3: Run package, benchmark, and container contracts**

Run package build/Twine validation, the Linux-equivalent benchmark contract, and Docker/static workflow contract tests. Build locally only where the repository's existing launch plan requires it.

- [ ] **Step 4: Inspect git state and update evidence**

Confirm the index is clean, only the user's `MagicMock/` remains untracked, and the audit contains exact fresh counts.

### Task 3: Update and babysit PR #277 to green

**Files:**
- No new source files unless a remote-only failure exposes a real missing contract.

**Interfaces:**
- Publishes: `codex/encoding-delivery-final` as the head of PR #277.
- Produces: all required GitHub Actions checks green on the exact reviewed SHA.

- [ ] **Step 1: Verify branch ancestry and remote target**

Confirm the PR head is an ancestor of the final branch and that updating `codex/launch-hardening` is a fast-forward, never a force-push.

- [ ] **Step 2: Push the reviewed branch to PR #277**

Push the final SHA to `origin/codex/launch-hardening` using a non-force refspec.

- [ ] **Step 3: Monitor every required check**

Use `gh pr checks 277 --watch` and inspect any failure log at its root cause. Apply focused RED/GREEN fixes, rerun local affected gates, commit, and push until all required checks pass.

- [ ] **Step 4: Confirm the PR head and evidence SHA match**

Record the green remote SHA and check list in the launch audit.

### Task 4: Close fixed reports and resolve contributor PR #273 respectfully

**Files:**
- Modify: `docs/reviews/2026-08-11-launch-readiness-audit.md` with final issue disposition.

**Interfaces:**
- Closes: #275 as duplicate/superseded, #276 and #271 as fixed by #277, and #272 as fixed by tested same-codec software fallback, but only after the green PR proves those fixes.
- Resolves: PR #273 without merging its unsafe bundle.

- [ ] **Step 1: Recheck issue behavior against the green PR SHA**

Run the v2/v3 duration/date tests, upload-schema tests, and hardware fallback tests on the exact pushed commit.

- [ ] **Step 2: Comment and close fixed issues**

Post direct comments with the fixing PR, exact behavior, and release caveat. Mark #275 as covered by the broader #276 report.

- [ ] **Step 3: Thank and close PR #273**

Credit Sanji78 for the real-world diagnosis. Explain which changes #277 supersedes and why the generic VAAPI/QSV probe cannot be merged. Link the focused follow-ups for the remaining ideas.

- [ ] **Step 4: Triage older issues now satisfied by the final branch**

Close only issues whose full acceptance criteria are met. For partially met issues such as scheduler `next_scheduled_run`, leave them open with an accurate status comment.

### Task 5: Preserve the three useful #273 ideas as focused P1 follow-ups

**Files:**
- Create issues or a separate follow-up plan/branch for Taichi dispatch probing, FFmpeg stderr draining, and NiceGUI disconnect tolerance.
- Modify production/tests only in a post-#277 follow-up branch.

**Interfaces:**
- Produces: one independently testable unit per risk boundary.
- Keeps: PR #277 frozen after green.

- [ ] **Step 1: Open three scoped follow-up issues with contributor credit**

Each issue includes the observed failure, safe design boundary, acceptance tests, and a link to PR #273.

- [ ] **Step 2: Implement Taichi probing test-first in isolation**

The probe runs a real kernel in a spawned subprocess, has a hard timeout, terminates a hung child, never probes CPU in a child, and falls back to the next backend without initializing Taichi twice in the parent.

- [ ] **Step 3: Implement FFmpeg stderr draining test-first in isolation**

Read stderr concurrently while FFmpeg is running; never swap `wait()` and `read()` into a different deadlock. Preserve the bounded diagnostic tail.

- [ ] **Step 4: Implement NiceGUI disconnect tolerance test-first in isolation**

Only UI writes caused by a disconnected client are suppressed. Generation, artifact persistence, delivery state, and non-disconnect exceptions retain their existing semantics.

- [ ] **Step 5: Run the full follow-up gate and publish separately**

Run focused tests, the complete source gate, `make launch-check`, and then publish a separate PR that credits Sanji78 and links PR #273.
