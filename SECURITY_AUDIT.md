# Security & Quality Audit Report

**Project:** Immich Memories
**Date:** 2026-03-08
**Auditor:** Claude Opus 4.6
**Methodology:** Full pre-release audit — Layer 1 (deterministic tools) + Layer 2 (AI deep analysis per `references/ai-security-prompts.md`)

---

## Executive Summary

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 8 | 5 Fixed, 3 Documented (design/architecture) |
| Low | 9 | Backlog |

**Overall Risk Level:** LOW (for intended use as single-user desktop/Docker tool)

The codebase has matured significantly since the December 2025 audit. All three HIGH findings from the prior audit have been resolved: `tempfile.mktemp()` replaced, path validation via `security.py` deployed, and LLM output validated with whitelists. The remaining findings are primarily architectural considerations for multi-user deployment scenarios, which is not the current design target.

---

## Layer 1: Deterministic Tool Results

### Ruff (Linting & Formatting)
- **31 findings** — All `S108` (temp file paths in tests). False positives in test fixtures.
- **Formatting:** 78 files checked, all clean.

### Bandit (Security SAST)
- **0 HIGH severity**
- **2 MEDIUM severity** — Both acknowledged with `noqa` comments:
  - `B104` (`0.0.0.0` binding) — Intentional for Docker container use
  - `B608` (f-string SQL) — Column names are hardcoded; values are parameterized
- **178 LOW severity** — B603/B607 (subprocess with list args — safe pattern), B311 (random — not used for crypto), B404 (subprocess import — expected)

### Vulture (Dead Code)
- **0 findings** — Clean.

### pip-audit (Dependency Vulnerabilities)
- **12 vulnerabilities in 4 build/infra packages** (not runtime dependencies):
  - `cryptography==41.0.7` (6 CVEs, fix: upgrade to 46.0.5+)
  - `pip==24.0` (2 CVEs, fix: upgrade to 26.0+)
  - `setuptools==68.1.2` (3 CVEs, fix: upgrade to 78.1.1+)
  - `wheel==0.42.0` (1 CVE, fix: upgrade to 0.46.2+)
- **Note:** These are system-level packages, not application dependencies in `uv.lock`.

### Radon / Xenon (Complexity)
- Could not run due to `pyproject.toml` config conflict with radon's parser. Filed as known issue.

### Tests
- **340 passed, 4 skipped, 0 failed** — Full test suite clean.

---

## Layer 2: AI Deep Analysis Results

### Prompt 1 — Attack Surface Mapping

**Architecture:** CLI (Click) + local web UI (NiceGUI) + Immich API client + FFmpeg video processing.

| Entry Point | Auth | Risk | Notes |
|---|---|---|---|
| CLI `main` | None | LOW | Local process, user-invoked |
| CLI `ui` | None | MEDIUM | Default `0.0.0.0` binding, no auth on web UI |
| Web `/` (Config) | None | MEDIUM | Accepts Immich URL + API key |
| Web `/step2` (Review) | None | LOW | Triggers FFmpeg for previews |
| Web `/step3` (Options) | None | LOW | Music upload accepted |
| Web `/step4` (Export) | None | MEDIUM | Output filename user-controlled (now sanitized) |
| Immich API calls | API key | LOW | Key in header, not query param |
| LLM calls (Ollama/OpenAI) | API key | LOW | Keys in Bearer header |
| FFmpeg subprocess | N/A | LOW | All list-based, no shell=True |

### Prompt 2 — Auth & Authorization

**Not applicable for v1.** This is a single-user desktop tool. No user authentication, no RBAC, no session management. The web UI is intended for localhost access. The `0.0.0.0` default binding is documented as intentional for Docker.

**Recommendation for future multi-user deployment:** Add auth middleware before exposing on a network.

### Prompt 3 — Injection & Data Flow

| Category | Status | Details |
|---|---|---|
| SQL Injection | **SAFE** | All SQLite queries use parameterized `?` placeholders. Single f-string at `run_database.py:403` uses hardcoded column names. |
| Command Injection | **SAFE** | Zero `shell=True`, zero `os.system()`. All subprocess uses list args. |
| SSRF | **LOW** | Config-driven API URLs send credentials. By design (user configures their own servers). |
| Path Traversal | **FIXED** | `sanitize_filename()` now applied to output filename and person slug. API suffix sanitized. |
| Template Injection | **N/A** | No server-side template rendering. |
| Deserialization | **SAFE** | `yaml.safe_load()` only. No pickle, no unsafe YAML. |

### Prompt 4 — Race Conditions & Concurrency

| Finding | Severity | Notes |
|---|---|---|
| `AppState` global shared between UI + background threads | MEDIUM | Single-user tool; thread-safety is cosmetic, not security-critical |
| Config singleton race on first access | LOW | Write-once pattern, idempotent |
| Progress dict shared without atomic multi-field update | LOW | Cosmetic (progress display) |
| Non-atomic config file write | LOW | Crash mid-write could corrupt; rare |
| No `asyncio.Lock` usage | LOW | Architectural; blocking I/O properly offloaded to threads |

### Prompt 5 — Business Logic

| Finding | Severity | Notes |
|---|---|---|
| No rate limiting on FFmpeg/LLM | MEDIUM | Design: single-user tool. If network-exposed, add `slowapi`. |
| Error messages exposed to UI without sanitization | **FIXED** | `sanitize_error_message()` now applied in step2 and step4 |
| API keys in plaintext config file | LOW | Config dir permissions `0o700`; file now `0o600` on save |
| No concurrency guard on generate button | LOW | Single-user; worst case = wasted resources |
| Wizard steps not enforced server-side | LOW | Single-user; no security implication |

### Prompt 6 — FFmpeg / Subprocess Hardening

| Finding | Severity | Notes |
|---|---|---|
| No `shell=True` anywhere | **EXCELLENT** | All 88+ subprocess calls use list args |
| `validate_video_path()` applied at entry points | **GOOD** | Extension + magic bytes + null byte + control char checks |
| Missing timeout on `_extract_copy()` | MEDIUM | `subprocess.run()` at `clips.py:360` has no `timeout=` |
| 5 `Popen` calls without timeout mechanism | MEDIUM | `clips.py:451`, `assembly.py:840`, title renderers |
| No `-threads` limit on most FFmpeg calls | LOW | Can consume all CPU; acceptable for single-user |
| No `-fs` limit on FFmpeg output | LOW | Could produce large files; user controls input |
| Full FFmpeg stderr in some exception messages | LOW | Now sanitized before reaching UI |

### Prompt 7 — WebSocket / NiceGUI

**Not a significant attack surface for v1.** NiceGUI manages WebSocket lifecycle internally. The app is single-user, localhost-targeted. No custom WS handlers, no message validation needed.

---

## Fixes Applied in This Audit

### FIX-001: Output filename sanitization (`step4_export.py`)
- Applied `sanitize_filename()` to user-provided output filename
- Applied `sanitize_filename()` to `person_slug` for directory creation
- **Prevents:** Path traversal via `../../` in filename or person name

### FIX-002: Error message sanitization (`step4_export.py`, `step2_review.py`)
- Applied `sanitize_error_message()` to all `ui.notify()` and `ui.label()` calls displaying exceptions
- **Prevents:** API key or internal path leakage in error displays

### FIX-003: API suffix sanitization (`pipeline.py`)
- Validated suffix from `original_file_name` against known video extensions
- Falls back to `.mp4` for unrecognized suffixes
- **Prevents:** Path injection via malicious Immich API filenames

### FIX-004: Pixabay redirect domain validation (`music_sources.py`)
- Added post-redirect domain check on `response.url.host`
- **Prevents:** Open redirect abuse bypassing the pre-request domain whitelist

### FIX-005: Config file permissions (`config.py`)
- `save_yaml()` now sets `0o600` on the config file after writing
- **Prevents:** Other users reading API keys from config file

---

## Positive Security Observations

The codebase demonstrates strong security awareness:

1. **Dedicated `security.py` module** — Centralized path validation, magic bytes checking, filename sanitization, error message scrubbing
2. **Zero `shell=True`** — All 88+ subprocess calls use list-based arguments
3. **`yaml.safe_load()` exclusively** — No unsafe YAML deserialization anywhere
4. **Parameterized SQL throughout** — SQLite queries use `?` placeholders consistently
5. **Pydantic validation** — Config and API models validated with type constraints and ranges
6. **Download size limits** — 25 GB per Immich asset, 100 MB per music file, 10 GB video cache
7. **LLM output whitelists** — Mood, genre, and energy values validated against allowed sets
8. **Error sanitization** — API keys stripped from error messages before display
9. **Config directory permissions** — `0o700` enforced on `~/.immich-memories/`
10. **Domain whitelisting** — Pixabay downloads restricted to trusted domains

---

## Remaining Recommendations

### Before v1 Release
- [x] Fix output filename sanitization (FIX-001)
- [x] Fix error message leakage (FIX-002)
- [x] Fix API suffix sanitization (FIX-003)
- [x] Fix redirect domain validation (FIX-004)
- [x] Fix config file permissions (FIX-005)

### Short-term (Post-v1)
- Add `timeout=` to `_extract_copy()` subprocess call in `clips.py`
- Add timeout mechanism to `Popen` calls in assembly and title renderers
- Upgrade system `cryptography`, `pip`, `setuptools`, `wheel` packages
- Fix radon/xenon compatibility with pyproject.toml

### If Deploying Multi-User / Network-Exposed
- Add authentication middleware to NiceGUI routes
- Add rate limiting (`slowapi`) to expensive operations
- Add `threading.Lock` to `AppState` for concurrent access
- Use atomic file writes (write-to-temp-then-rename) for config and metadata
- Add CSRF protection and security headers
- Consider per-session `AppState` isolation

---

## Tool Versions

| Tool | Version | Result |
|------|---------|--------|
| Ruff | latest (CI) | 31 findings (all false positives) |
| Bandit | 1.9.4 | 2 MEDIUM (both justified), 178 LOW |
| Vulture | 2.15 | 0 findings |
| pip-audit | 2.10.0 | 12 vulns (build deps only) |
| pytest | 8.x | 340 passed, 4 skipped |
| Python | 3.13 | — |

---

*Report generated using the python-security-audit skill methodology: Layer 1 deterministic scanning + Layer 2 AI deep analysis with structured security prompts.*
