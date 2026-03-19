---
date: 2026-03-19
supersedes: 2026-03-16-stage3-revised-design.md
branch: main (after feat/adaptive-pipeline merges)
origin: 5-prism roast (2026-03-18) + adaptive pipeline session (2026-03-19)
grade_target: A- → A → A+
---

# Post-Roast Plan: From B- to A+

## Context

The 5-prism codebase roast (2026-03-18) rated the project **B-** overall. Five expert
agents (hostile reviewer, self-hoster, AI maximalist, SRE, CISO) converged on specific
gaps. This plan addresses every RED and CRITICAL finding, organized into the roast's
own A-/A/A+ grade ladder.

### What already shipped (feat/adaptive-pipeline PR, 2026-03-19)

These roast findings are **resolved** and should not appear in the plan:

| Roast finding | Resolution |
|---------------|-----------|
| #69 temp dir cleanup (SRE/Self-Hoster RED) | Commit `d354701` |
| #67 prefiltering discards before LLM (Self-Hoster RED) | Density budget replaces rigid filter |
| #68 live photo unification | Density budget includes live photos, favorite inheritance |
| No cache backup guidance (Self-Hoster) | `cache stats/export/import/backup` CLI commands |
| Live photo Pydantic crash on null dimensions | `coerce_null_dimensions` validator on Asset |
| Live photo burst orientation mismatch | Rotation-aware `filter_valid_clips` |

### What was dropped from Stage 3

| Item | Reason |
|------|--------|
| PR H (pixel assertions) | Roast consensus: "only the plan author wants this" |

### What was reworked

| Item | Old | New |
|------|-----|-----|
| PR I (coverage) | "Hit 60%" | "80% on critical core paths, fix bad tests" |
| PR P (UI parity) | Full parity PR | 5 small gaps remaining, folded into Phase 9 |
| PR L (launch polish) | Manual screenshots + README | Playwright E2E auto-generates screenshots |

---

## Priority Order: A → C → B → D

- **A = Ship quality** — Scoring calibration, video encoding quality
- **C = Security gate** — Basic auth now, OIDC soon
- **B = Operational safety** — SRE RED fixes, reliability
- **D = Launch polish** — E2E tests + auto-screenshots (comes last, builds on everything)

---

## Tier A-: "Ship It"

Everything here blocks public promotion. Each phase is one PR (max ~300 lines).

### Phase 1 — Scoring Calibration (#73) ✅ SHIPPED

**Shipped in PR #77 + PR #78.** All deliverables complete plus extras found during implementation:

- [x] Log per-factor breakdown (`log_top_segments`) for top-N clips
- [x] Normalize scores: SceneScorer weights auto-normalize to 1.0 (were 1.15)
- [x] LLM-as-bonus: content analysis only boosts, never dilutes (additive model)
- [x] 42 new tests including parametrized LLM-never-lowers-score matrix
- [x] `SCORING_VERSION` in cache (migration v8) — old scores auto-invalidated
- [x] Quality gate for density budget: non-camera + low-res clips filtered before selection
- [x] Extracted `AssetScoreCache` from `database.py` (cohesion split)
- [x] Duration in seconds (SI units) — `target_duration_minutes` → `target_duration_seconds`
- [x] Memory type duration defaults (monthly=60s, season=135s, trip=35s/day)
- [x] Integration test perf: 60s cap, temp file cleanup, smart fixtures
- [x] Codecov fix: only upload fresh CI-generated coverage.xml

**Discovered during implementation (new issues filed):**
- #75: Duration scaling per memory type (closed by PR #78)
- #76: Integration test temp file leak (closed by PR #78)
- HDR/SDR detection bug: Apple Shared Album videos have partial HDR metadata causing red cast (tracked in memory, Phase 2 scope)

### Phase 2 — Video Quality (#72)

**Problem:** Double-encoding (segment encode + final concat) compounds compression artifacts.
CRF 18 × 2 passes ≠ CRF 18 quality. Also: Apple Shared Album videos carry partial HDR
metadata (bt2020nc color space) but are actually SDR, causing red color cast when tone-mapped.

**Deliverables:**
- [ ] Make `concat_with_inline_trim` the default assembly path (reads source files directly, single encode)
- [ ] Remove the pre-encode step for non-xfade transitions
- [ ] Review CRF defaults (18 may be too aggressive for single-pass)
- [ ] Integration test: SSIM or VMAF threshold comparing output vs source
- [ ] Verify analysis downscaler files never leak into final assembly
- [ ] HDR/SDR detection: check actual transfer function (PQ/HLG), not just color space tag.
      Videos tagged bt2020 but without PQ/HLG transfer should be treated as SDR.

**Files:** `processing/video_assembler.py`, `processing/clip_encoder.py`, `processing/ffmpeg_filter_graph.py`, `processing/hdr_utilities.py`

### Phase 3 — Basic Auth (new issue, covers CISO CRITICAL)

**Problem:** Web UI at `0.0.0.0:8080` has zero authentication. Anyone on LAN can browse
photos, see API keys, trigger generation.

**Deliverables:**
- [ ] Env vars: `IMMICH_MEMORIES_AUTH_USERNAME`, `IMMICH_MEMORIES_AUTH_PASSWORD`
- [ ] When set: NiceGUI Starlette middleware redirects unauthenticated requests to `/login`
- [ ] Nice login form page (matches app visual style)
- [ ] When unset: no auth (backwards compatible, current behavior)
- [ ] `/health` and `/api/*` endpoints bypass auth (monitoring, API clients)
- [ ] Session persistence via `app.storage.user`
- [ ] Update Docker docs with auth env vars

**Files:** `ui/app.py` (middleware), new `ui/pages/login.py`, `config_models.py`

### Phase 4 — Reliability Quick Fixes (SRE REDs + YELLOWs)

**Problem:** Multiple trivial-to-fix operational issues flagged RED across two review passes.

**Deliverables (S-sized, ~3 lines each):**
- [ ] Atomic `_apply_music_file`: replace `unlink()+rename()` with `os.replace()` (#new)
- [ ] SQLite `busy_timeout = 5000` on both DB connections (#new)
- [ ] Photo FFmpeg stderr: replace `DEVNULL` with `PIPE`, log on failure (#new)
- [ ] Configurable scheduler timeout (env var, default 60min instead of 30) (#new)

**Deliverables (small PRs):**
- [ ] #50: Skip bad clips instead of aborting assembly
- [ ] #52: Disk space preflight check before assembly
- [ ] #53: File lock for single-instance prevention
- [ ] #51: Add `run_id` to all log lines (structured logging context)

**Files:** `generate.py`, `cache/database.py`, `photos/photo_pipeline.py`,
`cli/scheduler_cmd.py`, `processing/video_assembler.py`

### Phase 5 — Config Singleton Kill (PR G, reworked)

**Problem:** PR #60 claimed to kill `get_config()` but 50 calls remain. The "optional
parameter with global fallback" pattern hides the dependency instead of removing it.
Hostile reviewer upgraded to RED: "declared victory without achieving it."

**Deliverables:**
- [ ] Eliminate ALL `get_config()` calls from core code (analysis, processing, cache, API)
- [ ] Constructor injection everywhere — no hidden fallbacks
- [ ] `get_config()` may remain in CLI/UI entry points only (top-level wiring)
- [ ] Update conftest.py — no more double-patching
- [ ] Delete `src/immich_memories/config.py` singleton module if possible

**Files:** ~30 files across `analysis/`, `processing/`, `cache/`, `ui/`

### Phase 6 — Docs, Hygiene & Security Posture

**Problem:** Multiple small gaps flagged by CISO, Self-Hoster, and Hostile Reviewer.

**Deliverables:**
- [ ] New docs page: "Network Requests" — document ALL outbound calls with what data is sent:
      - **Nominatim** (GPS → city/country lookup). Note: Immich bundles GeoNames locally and
        makes ZERO external geocoding calls. We should consider doing the same (bundle a
        lightweight reverse geocoding DB like `reverse_geocoder` Python package, ~50MB).
        Short-term: document the call. Medium-term: eliminate it.
      - **OSM tiles / ArcGIS** (map rendering — GPS in tile URLs). Note: Immich self-hosts
        tiles at `tiles.immich.cloud`. We can't do that, but we should document clearly
        that map features contact these servers. Client-side only.
      - **LLM endpoint** (base64 JPEG frames — user-configured, opt-in)
      - **MusicGen/ACE-Step** (text prompts — user-configured, opt-in)
      - **Font CDN** (font name requests — cached locally after first download)
      Be clear: disabling maps = no trip detection, no map title screens, no location data
- [ ] Config toggle: `advanced.maps.enabled: true` — when false, all map/geocoding features
      disabled. Clear docs on what you lose (trip detection, map animations, location titles)
- [ ] Investigate replacing Nominatim with local reverse geocoding (`reverse_geocoder` package
      or bundled GeoNames DB like Immich) — eliminates the highest-sensitivity external call
- [ ] Bundle default fonts in Docker image (eliminate jsdelivr CDN calls at runtime)
- [ ] Add `.env.example` to repo root (matches Immich conventions)
- [ ] New docs page: "Secrets Management" — per-deployment-method best practices:
      - **Docker Compose:** `docker-compose.yml` secrets mount (not env vars with `export`)
      - **Docker standalone:** `--env-file .env` with `chmod 600 .env` (never shell export)
      - **Kubernetes:** `Secret` resources, sealed-secrets for GitOps
      - **Terraform:** `sensitive = true` variables, reference from vault/SSM
      - **uvx/pip (dev):** config file with `0o600` permissions (already enforced)
      - Cover: `IMMICH_API_KEY`, `AUTH_PASSWORD`, `AUTH_CLIENT_SECRET`, `LLM__API_KEY`
- [ ] Update all existing deploy templates (docker-compose.yml, deploy/kubernetes/,
      deploy/terraform/) to use secrets mounts by default, not env var exports
- [ ] Add `SECURITY.md` with vulnerability disclosure policy
- [ ] Add `pillow-heif` to Dockerfile (iPhone HEIC photo support)
- [ ] Fix ARCHITECTURE.md: remove ghost files, update counts
- [ ] Fix FAQ: stale test count, output format claims
- [ ] Delete attribute-assignment tests in `test_models.py` and `test_ui_state.py`
- [ ] Fix RunDatabase constructor side effect (migration trigger in wrong place)

**Files:** `docs-site/docs/`, `Dockerfile`, `ARCHITECTURE.md`, various test files

---

## Tier A: "Recommend It"

Post-launch sprint. Each phase is independently valuable.

### Phase 7 — Generic OIDC Auth (#44)

**Problem:** Basic auth is a stopgap. Self-hosters want to reuse their existing IdP
(Auth0, Authelia, Keycloak) without maintaining separate credentials.

**Deliverables:**
- [ ] `authlib` dependency for OIDC client
- [ ] `/.well-known/openid-configuration` autodiscovery — config is just `issuer_url`,
      `client_id`, `client_secret`
- [ ] Standard OIDC redirect flow: login → IdP → callback → session
- [ ] Trusted header SSO: `Remote-User` / `Remote-Email` from reverse proxy (Authelia/Traefik
      forward-auth pattern)
- [ ] Per-session `AppState` via `app.storage.user` (fix global state leak for family deployments)
- [ ] Auth config in YAML:
      ```yaml
      auth:
        enabled: false
        provider: basic | oidc | header
        # basic:
        username: admin
        password: ${AUTH_PASSWORD}
        # oidc:
        issuer_url: https://auth.example.com
        client_id: immich-memories
        client_secret: ${AUTH_CLIENT_SECRET}
        # header (trusted proxy):
        user_header: Remote-User
        email_header: Remote-Email
      ```
- [ ] Works with Auth0, Authelia, Keycloak out of the box
- [ ] Update Docker/K8s docs with OIDC examples

**Files:** new `ui/auth/` package, `ui/app.py`, `config_models.py`, `docs-site/`

### Phase 8 — Coverage & Test Quality (PR I, reworked)

**Problem:** 60% overall coverage target is less important than covering the critical paths.
Some existing tests test Python attribute assignment (violating CLAUDE.md).

**Deliverables:**
- [ ] Target: **80% coverage on critical core** (analysis, processing, cache, API, generate)
- [ ] This naturally drives overall project coverage to 60%+
- [ ] Rewrite mock-only assertion tests (tests that test mocks, not behavior)
- [ ] Add missing tests for: density budget edge cases, photo scoring, cache CLI
- [ ] Remove/rewrite tests that violate "don't test attribute assignment" rule

**Files:** `tests/` broadly

### Phase 9 — UI Feature Parity Gaps + Notifications

**Problem:** 5 CLI features lack UI equivalents. No notification system for completed jobs.

**Deliverables:**
- [ ] Privacy mode toggle in Step 3
- [ ] Title/subtitle editable fields in Step 4
- [ ] Analysis depth selector in Step 1
- [ ] Upload-to-Immich toggle in Step 4
- [ ] Apprise/ntfy.sh notifications on job completion (#47)

**Files:** `ui/pages/step1_config.py`, `ui/pages/step3_options.py`, `ui/pages/_step4_generate.py`

### Phase 10 — OIDC Auth (#44)

*Moved to Phase 7 — see above.*

### Phase 10 — E2E Tests + Auto-Generated Demos (#37 + #46)

**Problem:** Nobody can see what the product does. No screenshots, no demo. Manual
screenshot capture is tedious and goes stale immediately.

**Deliverables:**
- [ ] Playwright E2E test suite covering the 4-step wizard flow
- [ ] Screenshot capture at each wizard step → auto-committed to `docs-site/static/`
- [ ] Demo video/GIF generation from E2E run (screen recording)
- [ ] README reorg: product-first (screenshots, one-liner, demo), architecture second
- [ ] `make screenshots` target that runs E2E + collects artifacts
- [ ] CI job that regenerates screenshots on UI changes

**Files:** new `tests/e2e/` directory, `docs-site/`, `README.md`, `Makefile`

### Phase 11 — Launch Polish (PR L, now trivial)

**Problem:** With auto-generated screenshots from Phase 10, this becomes a docs pass.

**Deliverables:**
- [ ] Wire auto-generated screenshots into docs pages
- [ ] Update all stale references (test counts, feature claims)
- [ ] "Common Setups" docs page (NAS-only, Mac+LLM, Linux+NVIDIA)
- [ ] Final ARCHITECTURE.md refresh

---

## Tier A+: "Rave About It"

Moonshot features. No specific ordering — pick based on user demand.

- [ ] Immich webhook integration (auto-generate on new content)
- [ ] HOLIDAY + THEN_AND_NOW memory types (#38)
- [ ] Shared album support (#48)
- [ ] Streaming pipeline for 10K+ libraries (#36)
- [ ] i18n expansion to 8+ languages
- [ ] Prometheus metrics endpoint + Grafana dashboard
- [ ] Interactive config wizard (`immich-memories init`)
- [ ] All external calls opt-in with per-service toggles, offline mode
- [ ] Immich plugin integration (appear in Immich UI directly)
- [ ] Auto-generated narration via TTS

---

## Issue Tracking Matrix

| Phase | Closes | Opens |
|-------|--------|-------|
| PR #71 (adaptive pipeline) | #71, #69, #68 | — |
| Phase 1 (PR #77 + #78) | #73, #75, #76 | HDR/SDR detection (added to Phase 2) |
| Phase 2 | #72 | — |
| Phase 3 | — | new: basic-auth |
| Phase 4 | #50, #51, #52, #53 | new: atomic-music, busy-timeout, stderr-capture, scheduler-timeout |
| Phase 5 | — | new: config-singleton-kill |
| Phase 6 | — | new: network-docs, geocoding-toggle, env-example, security-md, pillow-heif, arch-fix, faq-fix, rundatabase-fix |
| Phase 7 | #44 | — |
| Phase 8 | — | — |
| Phase 9 | #47 | — |
| Phase 10 | #37, #46, #45 | — |
| Phase 11 | — | — |

## Estimated Effort per Phase

| Phase | Size | Est. PRs |
|-------|------|----------|
| 1 — Scoring calibration | ~~M~~ | ✅ PR #77 + #78 |
| 2 — Video quality | L | 1-2 |
| 3 — Basic auth | M | 1 |
| 4 — Reliability fixes | M | 2-3 (group trivials + small PRs) |
| 5 — Config singleton | L | 1-2 |
| 6 — Docs & hygiene | S-M | 1-2 |
| 7 — OIDC auth | L | 1-2 |
| 8 — Coverage & test quality | M | 1-2 |
| 9 — UI parity + notifications | M | 1-2 |
| 10 — E2E + demos | L | 1-2 |
| 11 — Launch polish | S | 1 |

---

## Session Prompts

Copy-paste these to start a new session for each phase.

### Phase 1 — Scoring Calibration ✅ DONE

*Shipped in PR #77 + #78. See Phase 1 deliverables above.*

### Phase 2 — Video Quality

```
We're working on issue #72 — blocky video quality from dual encoding. The
assembly pipeline re-encodes clips twice: once per segment, once for final
concat. CRF 18 × 2 passes compounds artifacts.

ALSO: Apple Shared Album videos have partial HDR metadata (bt2020nc color
space tag) but are actually SDR. The pipeline tone-maps them as HDR, causing
a red color cast. See memory: project_hdr_sdr_detection.md

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 2)
Read the issue: gh issue view 72

Key files:
- src/immich_memories/processing/video_assembler.py (assembly orchestration)
- src/immich_memories/processing/clip_encoder.py (per-segment encode)
- src/immich_memories/processing/ffmpeg_filter_graph.py (concat_with_inline_trim)
- src/immich_memories/processing/hdr_utilities.py (HDR detection + tone mapping)

Tasks:
1. Make concat_with_inline_trim the default path (single encode from source)
2. Remove pre-encode step for non-xfade transitions
3. Review CRF defaults for single-pass
4. HDR/SDR detection: check actual transfer function, not just color space tag
5. Integration test with SSIM/VMAF quality threshold
6. Verify analysis downscaler files never leak into final assembly

Use TDD. Branch: fix/video-quality. Target: closes #72.
```

### Phase 3 — Basic Auth

```
We're adding basic authentication to the web UI. The CISO flagged this as
CRITICAL — zero auth on 0.0.0.0:8080 means anyone on LAN can browse photos
and see API keys.

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 3)

Key files:
- src/immich_memories/ui/app.py (NiceGUI app, Starlette middleware)
- src/immich_memories/config_models.py (add auth config)

Tasks:
1. Env vars: IMMICH_MEMORIES_AUTH_USERNAME / IMMICH_MEMORIES_AUTH_PASSWORD
2. NiceGUI Starlette middleware redirecting unauthenticated requests to /login
3. Nice login form page (matches app visual style, no serif fonts)
4. When env vars unset: no auth (backwards compatible)
5. /health and /api/* bypass auth
6. Session persistence via app.storage.user
7. Update Docker docs with auth env vars

Use TDD. Branch: feat/basic-auth. Create a new issue first.
```

### Phase 4 — Reliability Quick Fixes

```
We're fixing SRE RED findings from the 5-prism roast. These are mostly
trivial fixes with high reliability impact.

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 4)

Batch 1 (trivial, one PR):
- Atomic _apply_music_file: os.replace() instead of unlink+rename (generate.py)
- SQLite busy_timeout = 5000 on both DB connections (cache/database.py)
- Photo FFmpeg stderr: PIPE instead of DEVNULL (photos/photo_pipeline.py)
- Configurable scheduler timeout, default 60min (cli/scheduler_cmd.py)

Batch 2 (small PRs):
- #50: skip bad clips instead of aborting assembly
- #52: disk space preflight check
- #53: file lock single-instance prevention
- #51: run_id in all log lines

Use TDD. Branch: fix/reliability-quick-wins for batch 1.
```

### Phase 5 — Config Singleton Kill

```
We're completing the config singleton removal that PR #60 started but left
half-done. 50 get_config() calls remain in core code. The hostile reviewer
upgraded this to RED: "declared victory without achieving it."

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 5)

Key file: src/immich_memories/config.py (the singleton module)

Tasks:
1. Grep for all get_config() calls: rg "get_config\(\)" src/
2. For each call site, inject Config via constructor parameter instead
3. get_config() may ONLY remain in CLI/UI entry points (top-level wiring)
4. Update conftest.py — eliminate double-patching
5. Delete config.py singleton module if possible

This touches ~30 files. Work systematically: analysis/ first, then
processing/, then cache/, then API. Test after each package.

Branch: refactor/kill-config-singleton.
```

### Phase 6 — Docs, Hygiene & Security Posture

```
We're fixing documentation gaps and small hygiene issues flagged by the
5-prism roast (CISO, Self-Hoster, Hostile Reviewer).

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 6)

Tasks:
1. New docs page: "Network Requests" — document all outbound calls
   (Nominatim, OSM tiles, LLM, MusicGen, fonts). Be honest about what
   data is sent. Note: Immich bundles GeoNames locally for geocoding and
   self-hosts tiles — we use public APIs instead, document the trade-off.
2. Config toggle: advanced.maps.enabled (disable all geocoding/tiles)
3. Support custom tile URL for users who have their own tile server
4. Investigate reverse_geocoder package as local Nominatim replacement
5. Bundle default fonts in Docker image
6. Add .env.example, SECURITY.md
7. New docs page: "Secrets Management" — per-deployment best practices
   (Docker secrets mount, K8s Secrets, Terraform sensitive vars, config 0o600)
8. Update deploy templates to use secure secret patterns by default
9. Fix ARCHITECTURE.md ghost files, FAQ stale claims
10. Delete attribute-assignment tests in test_models.py and test_ui_state.py
11. Fix RunDatabase constructor side effect
12. Add pillow-heif to Dockerfile

Branch: docs/hygiene-security. Multiple small commits.
```

### Phase 7 — Generic OIDC Auth

```
We're adding generic OIDC authentication. Sam uses Auth0 personally but
this must work with any provider (Authelia, Keycloak, etc.) via
/.well-known/openid-configuration autodiscovery.

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 7)
Read the issue: gh issue view 44
Read memory: feedback_auth_preferences.md

Key decisions already made:
- authlib for OIDC client
- .well-known autodiscovery — config is just issuer_url + client_id + client_secret
- Also support trusted header SSO (Remote-User from Authelia/Traefik forward-auth)
- Per-session AppState via app.storage.user (fix global state leak)
- Three auth providers: basic | oidc | header
- Basic auth from Phase 3 is already in place

Tasks:
1. Add authlib dependency
2. OIDC redirect flow: login → IdP → callback → session
3. Trusted header SSO middleware (Remote-User / Remote-Email)
4. Per-session state (replace global AppState)
5. Auth config in YAML (provider: basic | oidc | header)
6. Update Docker/K8s docs with OIDC examples

Use TDD. Branch: feat/oidc-auth. Target: closes #44.
```

### Phase 8 — Coverage & Test Quality

```
We're improving test coverage and quality. The goal is NOT "hit 60%" — it's
"80% on critical core paths" which naturally drives overall to 60%+.

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 8)

Tasks:
1. Run coverage report: make test PYTEST_ARGS="--cov-report=term-missing"
2. Identify critical core files below 80%: analysis/, processing/, cache/, api/, generate.py
3. Add tests for uncovered paths (behavior tests, not attribute tests)
4. Rewrite mock-only assertion tests (tests that test mocks, not behavior)
5. Remove tests that violate "don't test attribute assignment" rule
6. Add missing tests: density budget edge cases, photo scoring, cache CLI

Branch: test/coverage-quality.
```

### Phase 9 — UI Feature Parity + Notifications

```
We're closing the remaining UI/CLI parity gaps and adding job notifications.

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 9)

UI gaps (5 features):
- Privacy mode toggle in Step 3 (ui/pages/step3_options.py)
- Title/subtitle editable fields in Step 4 (ui/pages/_step4_generate.py)
- Analysis depth selector in Step 1 (ui/pages/step1_config.py)
- Upload-to-Immich toggle in Step 4
- Trip index selection for trip memory type

Notifications:
- Apprise/ntfy.sh integration (#47) — notify on job completion/failure
- Config: advanced.notifications.url (Apprise URL format)

Use TDD. Branch: feat/ui-parity-notifications. Target: closes #47.
```

### Phase 10 — E2E Tests + Auto-Generated Demos

```
We're building Playwright E2E tests that double as a screenshot/demo
generator. This kills two birds: test coverage for the UI AND auto-generated
screenshots for docs/README.

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 10)
Read the issue: gh issue view 37

Tasks:
1. Set up Playwright for Python (playwright + pytest-playwright)
2. E2E test covering the 4-step wizard flow (config → review → options → generate)
3. Screenshot capture at each step → docs-site/static/screenshots/
4. Demo video/GIF recording from E2E run
5. make screenshots target
6. CI job that regenerates screenshots on UI changes
7. README reorg: product-first with real screenshots

Note: auth (Phase 3/7) must be in place first — E2E tests capture
the login page too.

Branch: test/e2e-playwright. Target: closes #37, closes #46, closes #45.
```

### Phase 11 — Launch Polish

```
Final docs pass. With auto-generated screenshots from Phase 10, this is
mostly wiring and cleanup.

Read the plan: docs/superpowers/specs/2026-03-19-post-roast-plan.md (Phase 11)

Tasks:
1. Wire auto-generated screenshots into docs pages
2. Update all stale references (test counts, feature claims, output formats)
3. New docs page: "Common Setups" (NAS-only, Mac+LLM, Linux+NVIDIA)
4. Final ARCHITECTURE.md refresh
5. Verify docs-site build: make docs-build

Branch: docs/launch-polish.
```

---

## Tier A+: Session Prompts (Post-Release)

### Immich Webhook Integration

```
We're adding auto-generation triggered by Immich webhooks. When new content
lands in Immich (upload, sync, shared album update), automatically queue a
memory generation for the relevant time period.

Read memory: project_roadmap_priorities.md

Key decisions:
- Immich sends webhooks on asset.upload events
- We need a lightweight webhook receiver (FastAPI/Starlette endpoint)
- Queue system: simple file-based queue or Redis if available
- Smart dedup: don't re-generate if content hasn't changed significantly
- Configurable: which albums/date ranges trigger auto-gen

Tasks:
1. Webhook receiver endpoint in the existing NiceGUI app
2. Queue with dedup (same date range within cooldown = skip)
3. Config: advanced.webhooks.enabled, cooldown_minutes, trigger_types
4. Integration with scheduler (reuse existing scheduler infrastructure)
5. Docs: webhook setup guide for Immich → immich-memories

Branch: feat/webhook-auto-generate.
```

### Holiday + Then-and-Now Memory Types (#38)

```
We're adding two new memory types: HOLIDAY (auto-detect holidays from
calendar/GPS patterns) and THEN_AND_NOW (same location/person, years apart).

Read memory: project_future_memory_types.md

HOLIDAY:
- Detect clusters around known holidays (Christmas, Easter, national days)
- Use GPS to detect "not at home" periods during holiday windows
- Auto-title: "Christmas 2024", "Easter Weekend"
- Duration: 60-120s per holiday

THEN_AND_NOW:
- Find photos/videos from same GPS location, years apart
- Side-by-side or sequential presentation showing the passage of time
- Needs GPS clustering + temporal spread detection
- Duration: 30-60s per location pair

Tasks:
1. Holiday detection from date + GPS patterns
2. Then-and-Now: GPS location matching across years
3. New memory type presets with scoring profiles
4. Title templates for each type
5. Integration tests with real Immich data

Use TDD. Branch: feat/memory-types-holiday-then-now. Target: closes #38.
```

### Shared Album Support (#48)

```
We're adding support for generating memories from Immich shared albums.
Currently we only search the user's own library.

Read the issue: gh issue view 48

Key challenges:
- Shared albums may contain assets from multiple users
- EXIF metadata may differ (different phones, different HDR capabilities)
- Date ranges may be weird (shared after the fact)
- Need to handle albums the user was INVITED to, not just owns

Tasks:
1. Fetch shared album assets via Immich API
2. CLI flag: --album "Album Name" or --album-id UUID
3. UI: album picker in Step 1
4. Handle multi-user EXIF differences in assembly
5. Integration test with a real shared album

Branch: feat/shared-albums. Target: closes #48.
```

### Streaming Pipeline for Large Libraries (#36)

```
We're optimizing the pipeline for 10K+ asset libraries. Current approach
loads all assets into memory and processes sequentially.

Read the issue: gh issue view 36

Key changes:
- Paginated asset fetching (don't load 50K assets at once)
- Streaming density budget (process month-by-month)
- Parallel clip analysis (thread pool for downloads + analysis)
- Memory-bounded assembly queue
- Progress reporting for long-running jobs

Tasks:
1. Paginated search API calls (Immich supports cursor pagination)
2. Streaming density budget (yield buckets as they're filled)
3. Parallel analysis with configurable concurrency
4. Memory profiling + OOM prevention
5. Load test: benchmark with 10K+ synthetic assets

Branch: perf/streaming-pipeline. Target: closes #36.
```

### Prometheus Metrics + Grafana Dashboard

```
We're adding observability for self-hosters who run immich-memories as a
long-running service (scheduler mode).

Deliverables:
- /metrics endpoint (Prometheus exposition format)
- Metrics: generation_duration_seconds, clips_analyzed_total, cache_hit_ratio,
  assembly_size_bytes, generation_errors_total, active_runs
- Grafana dashboard JSON (import-ready)
- Docs: monitoring setup guide

Tasks:
1. prometheus_client dependency
2. Instrument: pipeline phases, cache hits, assembly, errors
3. /metrics endpoint on the NiceGUI app
4. Grafana dashboard JSON
5. Docs page: monitoring with Prometheus + Grafana

Branch: feat/prometheus-metrics.
```

### Auto-Generated Narration via TTS

```
We're adding optional AI narration overlay on generated memories. Text-to-speech
reads the LLM-generated descriptions over each clip segment.

Key decisions:
- TTS provider: user-configured (OpenAI TTS, local Coqui, macOS say)
- Narration text: from LLM clip descriptions (already exist in cache)
- Audio mixing: narration + background music with ducking
- Must be opt-in, off by default

Tasks:
1. TTS provider abstraction (OpenAI API, local Coqui, macOS say)
2. Generate narration audio from LLM descriptions
3. Audio mixing: narration with music ducking (FFmpeg amix + sidechaincompress)
4. Config: advanced.narration.enabled, provider, voice
5. Integration test with real TTS output

Branch: feat/tts-narration.
```
