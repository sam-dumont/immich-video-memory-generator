# Launch Hardening Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved P0 launch contracts before performing P1 performance and operational work.

**Architecture:** The program is split by stable subsystem boundary. Automation owns daily decisions and durable orchestration state; the Immich client owns major-version adaptation; the processing layer owns encoding and artifact validation; release gates prove the complete behavior; P1 then optimizes measured bottlenecks and exposes operational state.

**Tech Stack:** Python 3.11+, Click, Pydantic v2, httpx, SQLite, FFmpeg/ffprobe, NiceGUI, pytest, pytest-playwright, Hatch VCS, Docker, GitHub Actions.

## Global Constraints

- Complete every P0 plan before starting the P1 plan.
- `immich-memories auto run` is the only canonical daily automation entry point.
- One invocation attempts at most one generation candidate.
- Failed generation stops with a nonzero exit; healthy no-op exits zero.
- Hard variety rules cannot be relaxed when no candidate qualifies.
- Immich v2 and v3 are both supported; `api_version: auto` is the default.
- Automatic uploads select their schema before sending bytes.
- Requested output codec and hardware policy must match the probed artifact.
- Tests and benchmarks must never use the normal user database or output directory.
- Existing user data is not deleted or rewritten without a backup, manifest, and separate approval.
- The installed LaunchAgent remains unloaded until supervised rollout approval.
- All behavior changes use RED → GREEN → REFACTOR.

---

## Execution order

1. [P0 automation, variety, and state isolation](2026-08-11-p0-automation-variety.md)
2. [P0 Immich v2/v3 compatibility](2026-08-11-p0-immich-v2-v3.md)
3. [P0 encoding, validation, and delivery](2026-08-11-p0-encoding-delivery.md)
4. [P0 version, E2E, CI, and documentation](2026-08-11-p0-release-gates.md)
5. [P1 performance and operational flow](2026-08-11-p1-performance-operations.md)

Each plan ends in a green, independently reviewable checkpoint. Do not combine commits across
plan boundaries. If a task reveals a false assumption, stop that task, update the relevant
plan and design record, then restart its RED step.

## Program completion gate

Run after P0 plans 1–4:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run lint-imports
make e2e
make build-check
```

Expected: every command exits zero, required browser smoke produces and probes a real tiny
video, and no test writes outside pytest temporary directories.

The external scheduler remains unloaded after this gate. Reactivation follows the supervised
rollout in the approved design, not the test suite.
