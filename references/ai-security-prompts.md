# AI Security Prompts & Workflows for Claude Code

All AI-powered security analysis runs locally through Claude Code or GitHub Copilot.
No external APIs, no SaaS dependencies, no paid services.

## Table of Contents

1. [The 7 Security Review Prompts](#1-the-7-security-review-prompts)
1. [OWASP Top 10 Systematic Audit](#2-owasp-top-10-systematic-audit)
1. [Orchestration Workflows](#3-orchestration-workflows)
1. [Custom Semgrep Rules (local, free)](#4-custom-semgrep-rules)
1. [Claude Code + Semgrep MCP Integration](#5-semgrep-mcp-integration)
1. [GitHub Copilot Code Review Setup](#6-github-copilot-code-review)
1. [Optional: Vulnhuntr with Existing API Key](#7-optional-vulnhuntr)
1. [Threat Modeling Prompts](#8-threat-modeling)
1. [Git Diff Security Review](#9-git-diff-security-review)

-----

## 1. The 7 Security Review Prompts

These are the core of the AI security layer. Each prompt targets a vulnerability class
that static analysis fundamentally cannot detect. Run them in Claude Code against your
codebase — Claude has full filesystem access and can follow imports across modules.

The prompts are designed to be copy-pasted directly into a Claude Code session.
They work best when Claude has already read the project structure.

### Prompt 1 — Attack Surface Mapping

```
Review this codebase and map the complete attack surface:

1. List every HTTP endpoint, WebSocket handler, and CLI entry point
2. For each, identify:
   - Authentication requirements (none / token / session / API key)
   - Input parameters and their types
   - Validation applied (Pydantic model, manual checks, none)
   - Response data (what does it expose?)
3. Flag any endpoints that accept user input but lack validation or auth
4. Identify all subprocess/os.system/exec calls and trace their input sources
5. List all file operations that use user-controllable paths
6. List all outbound HTTP requests that use user-controllable URLs
7. Identify all deserialization of external data (pickle, yaml, json with custom decoders)

Output as a structured table with columns:
  Entry Point | Method | Auth | Inputs | Validation | Risk Rating | Notes
```

### Prompt 2 — Authentication & Authorization Deep Review

```
Perform a deep security review focused exclusively on authentication and authorization:

1. AUTHENTICATION FLOW:
   - Trace the complete auth flow: login → token/session creation → storage → validation
   - How are credentials verified? (bcrypt/argon2/scrypt — not SHA-256/MD5)
   - Where are tokens/sessions stored? (httponly cookies, localStorage, memory?)
   - What's the token lifetime? Is there rotation? Revocation on logout?
   - Is there brute-force protection? (rate limiting on login, account lockout)

2. AUTHORIZATION ENFORCEMENT:
   - List all endpoints that should require authentication
   - For each, verify that auth is actually enforced (middleware, dependency injection, decorator)
   - Identify any endpoints that SHOULD require auth but DON'T — compare route definitions
     against the middleware/dependency chain
   - Check for auth bypass: can any path skip the auth middleware? (wrong route ordering,
     missing dependencies, path prefix exclusions)

3. IDOR (Insecure Direct Object Reference):
   - Find all endpoints that take a resource ID from the URL (e.g., /users/{id}, /files/{id})
   - For each, check: does the code verify the authenticated user OWNS or has access to that resource?
   - Specifically check: does it just do `db.get(id)` or does it do `db.get(id, owner=current_user)`?

4. PRIVILEGE ESCALATION:
   - Can a regular user access admin endpoints? (check role/permission enforcement)
   - Can a user modify their own role/permissions via API input?
   - Are admin operations protected by separate auth mechanisms?

5. SESSION MANAGEMENT:
   - Are sessions invalidated on password change?
   - Are sessions bound to IP or user-agent? (optional but noted)
   - Is there protection against session fixation?

For each finding, show the exact code location and a concrete attack scenario.
```

### Prompt 3 — Injection & Data Flow Analysis

```
Trace all user input from entry points to security-sensitive sinks across the entire codebase.
For each flow, determine whether the input is sanitized, escaped, or validated before reaching the sink.

CHECK EACH CATEGORY:

1. SQL INJECTION:
   - Find all database queries. Are any using raw SQL with string formatting/f-strings?
   - Even with ORMs: check for .raw(), .extra(), raw execute(), text() with interpolation
   - Check for ORM filter() with user-controlled field names (allows arbitrary column access)

2. COMMAND INJECTION:
   - Find ALL subprocess.run, subprocess.Popen, os.system, os.popen, os.exec* calls
   - For each: is shell=True? Are arguments from user input? Are they passed as a list?
   - Check for indirect command injection via environment variables or config files

3. SSRF (Server-Side Request Forgery):
   - Find all code that makes HTTP requests (requests, httpx, aiohttp, urllib)
   - For each: can the URL come from user input? Is there URL validation?
   - Check: are internal/cloud metadata URLs blocked? (169.254.169.254, localhost, 10.x, 172.16.x)
   - Check for SSRF via redirects: does the HTTP client follow redirects to internal URLs?

4. PATH TRAVERSAL:
   - Find all file operations using user-controllable paths
   - Check: is the path resolved (os.path.realpath) and validated against a base directory?
   - Check for null byte injection in filenames (Python 3 handles this, but check wrappers)
   - Check for zip slip in archive extraction (zipfile, tarfile)

5. TEMPLATE INJECTION:
   - If using Jinja2: any render_template_string with user input? Any |safe filter on user data?
   - If using NiceGUI: any ui.html() or ui.markdown() with unsanitized user input?

6. DESERIALIZATION:
   - pickle.loads on untrusted data = instant RCE. Flag ALL pickle usage on external data.
   - yaml.load without SafeLoader = code execution. Must be yaml.safe_load.
   - json with custom object_hook on untrusted input — check what the hook does

For each confirmed flow: show source file:line → intermediate transformations → sink file:line
Rate as: CRITICAL (exploitable as-is), HIGH (exploitable with effort), MEDIUM (mitigated but fragile)
```

### Prompt 4 — Race Conditions & Concurrency

```
Review this codebase for race conditions and concurrency vulnerabilities.
This is especially important for async FastAPI/NiceGUI applications.

1. CHECK-THEN-ACT (TOCTOU):
   - Find patterns where code checks a condition then acts on it in separate operations:
     e.g., "if file exists → read file" or "if balance >= amount → deduct"
   - In async code, ANY await between check and act creates a race window
   - In database: SELECT then UPDATE without a transaction or row lock

2. SHARED MUTABLE STATE:
   - Find all module-level mutable variables (dicts, lists, sets, counters)
   - Find all class attributes modified by multiple request handlers
   - Check: are these accessed by async handlers? (concurrent access without locks)
   - FastAPI/NiceGUI: app.state, global dicts, in-memory caches without locks

3. NON-ATOMIC OPERATIONS:
   - Financial operations: is "check balance + deduct" atomic? (must be in a transaction)
   - Rate limiting: is the counter increment atomic? (Redis INCR is, Python dict[key] += 1 is not)
   - Inventory/resource allocation: can two requests allocate the same resource?
   - File operations: can two requests write to the same file?

4. DATABASE TRANSACTIONS:
   - Are critical multi-step operations wrapped in transactions?
   - What isolation level? (READ COMMITTED is usually insufficient for financial ops)
   - Are there SELECT FOR UPDATE or advisory locks where needed?
   - Check for deadlock potential in complex transaction chains

5. ASYNC-SPECIFIC:
   - Are asyncio.Lock() used where needed? Are they per-resource or global?
   - Is there any blocking I/O in async handlers? (time.sleep, synchronous DB calls)
   - Check for task cancellation issues: what happens if a request is cancelled mid-operation?

For each finding: describe the race scenario, estimate the exploitation window,
and suggest the fix (locks, transactions, atomic operations, idempotency keys).
```

### Prompt 5 — Business Logic Flaws

```
Review the business logic of this application for security design flaws.
These are the hardest vulnerabilities to find because they require understanding
what the code is SUPPOSED to do, not just what it DOES.

1. IDEMPOTENCY:
   - Find all state-changing operations (payments, account creation, resource allocation)
   - Can any of them be safely repeated? If a request is retried, does it double-charge?
   - Check for idempotency keys or unique constraints that prevent duplicate processing

2. MASS ASSIGNMENT / OVER-POSTING:
   - When updating user profiles or resources: can the user set fields they shouldn't?
   - Check Pydantic models: do update schemas include fields like is_admin, balance, role?
   - Check ORM update patterns: .update(**request.dict()) passes ALL fields including unexpected ones
   - Fix: use explicit field lists or separate read/write Pydantic models

3. RATE LIMITING:
   - Are expensive operations rate-limited? (AI API calls, email sending, file processing, login attempts)
   - Is rate limiting per-user or per-IP? (per-IP is bypassable via proxies)
   - Are there resource consumption limits? (max file upload size, max query complexity, max pagination size)

4. ERROR HANDLING & INFORMATION LEAKAGE:
   - Do error responses include stack traces, file paths, SQL queries, internal IPs?
   - Is FastAPI debug mode disabled? (docs_url=None, redoc_url=None in production)
   - Do 404/403 responses differ in a way that enables enumeration?
     (e.g., "user not found" vs "permission denied" reveals whether the user exists)

5. SENSITIVE DATA EXPOSURE:
   - Is sensitive data (passwords, tokens, PII, API keys) being logged?
   - Are API responses including more data than needed? (full user objects instead of public profiles)
   - Is sensitive data stored encrypted at rest? (database, files, environment variables)

6. TIMING ATTACKS:
   - Are secret comparisons using constant-time comparison? (hmac.compare_digest, not ==)
   - Can login timing reveal whether a username exists? (hash a dummy password on user-not-found)
   - Can token validation timing reveal token length or prefix?

7. BUSINESS RULE BYPASS:
   - Are there discount/coupon systems that can be stacked or replayed?
   - Can users manipulate quantities, prices, or dates via API input?
   - Are multi-step workflows enforced server-side? (can you skip step 2 and go to step 3?)
```

### Prompt 6 — FFmpeg / Subprocess Hardening

```
Review ALL FFmpeg and subprocess invocations in this codebase:

1. COMMAND INJECTION:
   - For every subprocess.run/Popen/os.system call:
     a. Is shell=True? (CRITICAL if user input involved)
     b. Are arguments passed as a list or a single string?
     c. Trace the input: does any part come from user input (filenames, URLs, parameters)?
   - For FFmpeg specifically: are user-supplied filenames or URLs interpolated into the command?

2. INPUT VALIDATION:
   - Are uploaded/user-supplied files validated before FFmpeg touches them?
   - Validation checklist:
     a. Magic bytes check (python-magic) — don't trust file extensions
     b. ffprobe inspection — verify it's actually a valid media file
     c. File size limits — before processing, not after
     d. Filename sanitization — strip path components, special chars, null bytes

3. RESOURCE LIMITS:
   - Is there a timeout on subprocess calls? (subprocess.run timeout= parameter)
   - FFmpeg-specific limits: -t (duration), -fs (file size), -threads
   - Are CPU/memory limits set? (ulimit, cgroups, container resource limits)
   - What happens if FFmpeg hangs or produces infinite output?

4. SANDBOXING:
   - For untrusted media: is FFmpeg running in an isolated environment?
   - Ideal: container per job with read-only root fs, no network, seccomp, CPU/mem limits
   - Minimum: non-root user, restricted filesystem access, no network
   - Are temporary files in a dedicated tmpdir that gets cleaned up?

5. OUTPUT SAFETY:
   - Are output paths validated? (prevent writing to arbitrary locations)
   - Are output filenames deterministic/server-generated? (not user-controlled)
   - Is there a cleanup mechanism for failed/abandoned processing jobs?

6. ERROR HANDLING:
   - Is FFmpeg stderr captured and logged? (but NOT exposed to users — may contain file paths)
   - Are exit codes checked? (non-zero = failure, don't serve partial/corrupt output)
   - Is there retry logic? (if so, is it bounded?)
```

### Prompt 7 — WebSocket / NiceGUI Security

```
Review the WebSocket and NiceGUI security model of this application:

1. WEBSOCKET AUTHENTICATION:
   - How are WebSocket connections authenticated?
   - Can an unauthenticated client establish a WS connection?
   - Is the auth token validated on connection AND periodically during the session?
   - What happens when the auth token expires during an active WS connection?

2. MESSAGE VALIDATION:
   - NiceGUI sends all UI interactions as WebSocket events
   - Can a client send arbitrary event types that trigger server-side actions?
   - Is there message schema validation on incoming WS frames?
   - Can a malicious client forge UI state updates?

3. SESSION MANAGEMENT:
   - Are sessions bound to WebSocket connections correctly?
   - What happens on WS disconnect + reconnect? (is the session still valid?)
   - For multi-worker deployments: are sticky sessions configured?
   - Check CVE-2025-21618: NiceGUI On Air auth bypass — update to latest version

4. STATE MANIPULATION:
   - NiceGUI maintains server-side state for each client
   - Can a client manipulate another client's state?
   - Is per-client state properly isolated?
   - Can a client trigger expensive server-side operations via rapid WS messages?

5. CROSS-SITE CONCERNS:
   - Is CSRF protection in place for any HTTP endpoints alongside WebSocket handlers?
   - Do CSP headers allow the WebSocket connections NiceGUI needs?
   - Is the WS upgrade path properly filtered by reverse proxy/auth gateway?

6. DENIAL OF SERVICE:
   - Is there rate limiting on WS messages? (a client could flood the server)
   - Are there limits on concurrent WS connections per user/IP?
   - What's the maximum message size?
   - Does the server handle slow clients gracefully? (backpressure, timeout)
```

-----

## 2. OWASP Top 10 Systematic Audit

Run these 10 prompts sequentially for complete OWASP coverage. Each is self-contained.

### A01 — Broken Access Control

```
Review for OWASP A01 Broken Access Control:
1. Find all endpoints and classify as public / authenticated / admin
2. Check that every non-public endpoint has auth enforcement
3. Look for horizontal privilege escalation (user A accessing user B's data)
4. Look for vertical privilege escalation (user accessing admin functions)
5. Check for CORS misconfiguration allowing unauthorized origins
6. Verify CSRF protection on state-changing operations
7. Check that directory listing is disabled and sensitive files aren't served
```

### A02 — Cryptographic Failures

```
Review for OWASP A02 Cryptographic Failures:
1. Find all encryption/hashing usage — verify algorithms (no MD5/SHA1 for security purposes)
2. Check password hashing: must be bcrypt/argon2/scrypt, not SHA-256/plain hash
3. Verify TLS/HTTPS enforcement for all external connections
4. Check for hardcoded secrets, keys, passwords in source code
5. Verify sensitive data at rest is encrypted (DB columns, files)
6. Check that tokens have appropriate entropy (secrets module, not random)
7. Are cryptographic keys properly managed? (not in source, rotated, appropriate length)
```

### A03 — Injection

```
Review for OWASP A03 Injection:
1. Trace ALL user input paths to database queries — any raw SQL with string interpolation?
2. Check for OS command injection via subprocess/os.system
3. Check for LDAP injection, XPath injection if applicable
4. Review ORM usage for unsafe .raw()/.extra()/.text() calls
5. Check for NoSQL injection if using MongoDB/similar
6. Verify all input validation uses allowlists not denylists
7. Check for expression language injection (eval, exec, compile on user input)
```

### A04 — Insecure Design

```
Review for OWASP A04 Insecure Design:
1. Check for missing rate limiting on expensive/sensitive operations
2. Verify resource consumption limits (file upload sizes, query limits, pagination caps)
3. Review error handling — does the system fail securely? (deny by default on errors)
4. Check for business logic flaws in multi-step processes
5. Are security controls at the architecture level? (not just sprinkled in code)
6. Is there separation of concerns? (auth module separate from business logic)
```

### A05 — Security Misconfiguration

```
Review for OWASP A05 Security Misconfiguration:
1. Is debug mode disabled in production? (FastAPI docs/redoc endpoints, NiceGUI debug)
2. Verify security headers: HSTS, CSP, X-Frame-Options, X-Content-Type-Options
3. Check for default credentials or configurations
4. Review CORS settings — no wildcard origins in production
5. Check cookie flags: Secure, HttpOnly, SameSite
6. Verify unnecessary features/endpoints are disabled in production
7. Check for verbose error messages exposing internals
8. Review Dockerfile: non-root user? minimal base image? no unnecessary packages?
```

### A06 — Vulnerable Components

```
Review for OWASP A06 Vulnerable and Outdated Components:
(Run pip-audit, Trivy, and GuardDog from Layer 1)
Additionally:
1. Check for vendored/copied code that bypasses package management
2. Check for pinned versions that are significantly outdated
3. Verify lock file is committed and CI uses --frozen/--locked
4. Check for dependencies that are abandoned (no commits in 2+ years)
```

### A07 — Authentication Failures

```
Review for OWASP A07 Identification and Authentication Failures:
1. Check for weak password policies (min length, complexity)
2. Verify account lockout or throttling on failed login attempts
3. Check session management (secure generation, appropriate expiry, invalidation on logout)
4. Verify credential recovery is secure (no user enumeration via different error messages)
5. Check for session fixation vulnerabilities
6. If multi-factor auth exists, verify it can't be bypassed
7. Are default/test accounts removed from production?
```

### A08 — Data Integrity

```
Review for OWASP A08 Software and Data Integrity Failures:
1. Check for deserialization of untrusted data (pickle, yaml.load without SafeLoader)
2. Review CI/CD pipeline: signed commits? protected branches? pinned action versions?
3. Check for auto-update mechanisms without integrity verification
4. Review any plugin/extension loading — is there trust verification?
5. Are downloaded files (models, configs, data) integrity-checked? (checksum, signature)
```

### A09 — Logging & Monitoring

```
Review for OWASP A09 Security Logging and Monitoring Failures:
1. Are authentication events logged? (login success, failure, logout)
2. Are authorization failures logged? (403s, permission denials)
3. Are input validation failures logged? (potential attack indicators)
4. Is sensitive data EXCLUDED from logs? (passwords, tokens, full credit card numbers, PII)
5. Are logs structured? (JSON, not plain text — enables automated analysis)
6. Is there alerting on suspicious patterns? (multiple failed logins, unusual access patterns)
7. Are logs tamper-protected? (append-only, separate storage)
```

### A10 — SSRF

```
Review for OWASP A10 Server-Side Request Forgery (SSRF):
1. Find ALL code that makes HTTP requests based on user input (direct or indirect)
2. Check for URL validation: is there an allowlist of domains/protocols?
3. Verify internal/cloud metadata URLs are blocked:
   - 169.254.169.254 (AWS/GCP metadata)
   - localhost, 127.0.0.1, ::1
   - 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
4. Check for DNS rebinding: does the app resolve DNS then validate, or validate then resolve?
5. Review redirect handling: can an attacker use redirects to reach internal services?
6. Check for SSRF via file:// protocol, gopher://, dict:// if URL parsing is permissive
```

-----

## 3. Orchestration Workflows

### Workflow A — Full Pre-Release Audit in Claude Code

This is the complete workflow. Execute step by step in a Claude Code session.

**Step 1: Setup and scan** — Run in Claude Code terminal:

```bash
mkdir -p /tmp/audit && cd /path/to/your/project

# Layer 1: All deterministic tools
ruff check . 2>&1 | tee /tmp/audit/ruff.txt
ruff format --check . 2>&1 | tee -a /tmp/audit/ruff.txt
mypy --strict src/ 2>&1 | tee /tmp/audit/mypy.txt
bandit -r src/ -f json -o /tmp/audit/bandit.json 2>&1
semgrep scan --config auto --config p/python --config p/owasp-top-ten --json -o /tmp/audit/semgrep.json 2>&1
pip-audit --format json --output /tmp/audit/pip-audit.json 2>&1
vulture src/ --min-confidence 80 2>&1 | tee /tmp/audit/vulture.txt
dead 2>&1 | tee /tmp/audit/dead.txt
xenon --max-absolute B --max-modules B --max-average A src/ 2>&1 | tee /tmp/audit/xenon.txt
radon cc src/ -a -nc -j > /tmp/audit/radon-cc.json 2>&1
radon mi src/ -nc -j > /tmp/audit/radon-mi.json 2>&1
gitleaks detect --source . -f json -r /tmp/audit/gitleaks.json 2>&1
coverage run --branch -m pytest 2>&1 | tee /tmp/audit/tests.txt
coverage json -o /tmp/audit/coverage.json 2>&1
```

**Step 2: Triage** — Ask Claude Code:

```
Read all files in /tmp/audit/. For each tool's output:
1. Identify true positives — real issues that need fixing
2. Identify false positives — explain why they're false
3. Group findings by severity: CRITICAL / HIGH / MEDIUM / LOW
4. For each true positive, draft a specific fix with code
Focus on actionable findings. Skip informational/style issues.
```

**Step 3: Deep AI review** — Run prompts 1-7 from section 1 above.
Focus extra time on areas where Layer 1 found clusters of issues.

**Step 4: OWASP audit** — Run the 10 OWASP prompts from section 2.

**Step 5: Report** — Ask Claude Code:

```
Based on all the analysis done so far, produce a consolidated security audit report:

1. EXECUTIVE SUMMARY
   - Overall risk rating (CRITICAL / HIGH / MEDIUM / LOW)
   - Count of findings by severity
   - Top 3 most urgent items to fix

2. CRITICAL & HIGH FINDINGS
   For each: description, code location, attack scenario, fix (with code)

3. MEDIUM & LOW FINDINGS
   Grouped by category with brief descriptions and fix recommendations

4. POSITIVE OBSERVATIONS
   Things the codebase does well from a security perspective

5. RECOMMENDATIONS
   - Immediate actions (fix before release)
   - Short-term improvements (next sprint)
   - Long-term hardening (architecture level)
```

### Workflow B — Quick PR Security Check in Claude Code

For reviewing a PR before merge. 5 minutes.

```
Step 1: Identify what changed
  git diff --name-only origin/main...HEAD
  git diff --stat origin/main...HEAD

Step 2: Quick deterministic scan on changed files
  ruff check [changed-files]
  bandit [changed-files]
  semgrep scan --config auto [changed-files]

Step 3: AI review of the diff (paste into Claude Code):
  "Read the output of `git diff origin/main...HEAD` and answer:
   1. Does this change introduce any new user input handling?
   2. Does it modify authentication or authorization logic?
   3. Does it add new subprocess, file, or network operations?
   4. Does it change any security-sensitive configuration?
   5. Are there any new dependencies? If so, check with pip-audit.
   6. Does it introduce any shared mutable state accessed by async handlers?
   7. Rate the security risk of this change: LOW / MEDIUM / HIGH / CRITICAL"

Step 4: If HIGH/CRITICAL, run targeted deep prompts (2, 3, 4) on affected files
```

### Workflow C — Dependency Deep Dive

Monthly or when adding significant new dependencies.

```
Step 1: pip-audit --strict --desc
Step 2: guarddog pypi verify -r requirements.txt
Step 3: For each flagged or new dependency, ask Claude Code:
  "Look at how we use <package> in our codebase:
   - Are we using any of its known vulnerable functions?
   - Could we replace it with a standard library alternative?
   - Does it need network access, filesystem access, or subprocess?
   - When was its last release? Is it actively maintained?"
Step 4: syft . -o cyclonedx-json > sbom.json (generate SBOM)
```

-----

## 4. Custom Semgrep Rules (local, free)

Write project-specific Semgrep rules for patterns that matter to your codebase.
These run locally with Semgrep OSS — no platform needed.

Save these in your repo as `semgrep-rules/` and run: `semgrep scan --config ./semgrep-rules/`

### FFmpeg safety rules

```yaml
rules:
  - id: ffmpeg-shell-true
    patterns:
      - pattern: subprocess.$FUNC(..., shell=True, ...)
    message: |
      subprocess with shell=True enables command injection.
      Use shell=False with an argument list instead.
    severity: ERROR
    languages: [python]

  - id: ffmpeg-fstring-command
    patterns:
      - pattern: subprocess.$FUNC(f"ffmpeg ...", ...)
      - pattern: subprocess.$FUNC(f"ffprobe ...", ...)
    message: |
      FFmpeg/ffprobe command built with f-string. Use argument list
      to prevent command injection: subprocess.run(["ffmpeg", "-i", input_file, ...])
    severity: ERROR
    languages: [python]

  - id: ffmpeg-no-timeout
    patterns:
      - pattern: subprocess.run(["ffmpeg", ...])
      - pattern-not: subprocess.run(["ffmpeg", ...], ..., timeout=..., ...)
    message: |
      FFmpeg call without timeout. Add timeout= parameter to prevent hanging processes.
    severity: WARNING
    languages: [python]
```

### FastAPI safety rules

```yaml
rules:
  - id: fastapi-wildcard-cors
    patterns:
      - pattern: |
          CORSMiddleware(..., allow_origins=["*"], ...)
    message: |
      Wildcard CORS origin in production allows any website to make
      authenticated requests. Use explicit origin allowlist.
    severity: ERROR
    languages: [python]

  - id: fastapi-assert-security
    patterns:
      - pattern: |
          assert $CONDITION, ...
    message: |
      assert statements are stripped when Python runs with -O.
      Never use assert for security checks. Use if/raise instead.
    severity: WARNING
    languages: [python]
    paths:
      exclude:
        - tests/

  - id: fastapi-docs-enabled
    patterns:
      - pattern: FastAPI(...)
      - pattern-not: FastAPI(..., docs_url=None, ...)
    message: |
      FastAPI docs endpoint is enabled. Disable in production:
      FastAPI(docs_url=None, redoc_url=None)
    severity: INFO
    languages: [python]
```

### General Python security rules

```yaml
rules:
  - id: pickle-untrusted
    patterns:
      - pattern: pickle.loads(...)
      - pattern: pickle.load(...)
    message: |
      pickle deserialization of untrusted data leads to arbitrary code execution.
      Use json, msgpack, or protobuf for untrusted data.
    severity: ERROR
    languages: [python]

  - id: yaml-unsafe-load
    patterns:
      - pattern: yaml.load(...)
      - pattern-not: yaml.load(..., Loader=yaml.SafeLoader)
      - pattern-not: yaml.safe_load(...)
    message: |
      yaml.load without SafeLoader allows code execution. Use yaml.safe_load().
    severity: ERROR
    languages: [python]

  - id: secrets-not-random
    patterns:
      - pattern: random.$FUNC(...)
    message: |
      random module is not cryptographically secure. For tokens, keys, or
      security-sensitive values, use the secrets module instead.
    severity: WARNING
    languages: [python]
    paths:
      exclude:
        - tests/
```

-----

## 5. Semgrep MCP Integration

The Semgrep MCP (Model Context Protocol) server lets Claude Code run Semgrep scans
as a tool during your conversation. This closes the loop: write code → Claude scans it
→ Claude fixes issues → Claude re-scans. All local, all free (OSS rules).

### Setup

```bash
pip install semgrep-mcp
```

Add to your Claude Code MCP configuration (`.claude/settings.json` or project config):

```json
{
  "mcpServers": {
    "semgrep": {
      "command": "semgrep-mcp",
      "args": ["serve"]
    }
  }
}
```

Once configured, Claude Code can invoke Semgrep scans inline:

- Scan specific files after editing
- Run targeted rule packs against code it just wrote
- Auto-remediate findings in the same session

### Usage in conversation

Just ask Claude Code things like:

- "Scan src/api/routes.py with Semgrep for security issues"
- "Run OWASP rules against the auth module"
- "Check this code I just wrote for injection vulnerabilities"

Claude Code will call the Semgrep MCP tool automatically.

-----

## 6. GitHub Copilot Code Review

If you have GitHub Copilot (any paid plan), you get AI code review on PRs at no extra cost.

### Setup

1. Enable Copilot code review in your repo or org settings
1. On a PR, request review from `@copilot` (or configure auto-assignment)
1. Copilot integrates with CodeQL — if you have CodeQL enabled (free for public repos,
   included with GitHub Advanced Security for private), Copilot can reference CodeQL
   findings in its review

### What Copilot catches on PRs

- Common security anti-patterns (injection, auth issues)
- Code quality issues (unused variables, dead branches)
- Bug patterns (off-by-one, null checks, error handling)
- Style and naming issues

### Combining with CodeQL (free + free = powerful)

Enable both CodeQL (GitHub Action) and Copilot code review:

```yaml
# .github/workflows/codeql.yml
name: CodeQL
on: [pull_request]
jobs:
  analyze:
    runs-on: ubuntu-latest
    permissions:
      security-events: write
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: python
      - uses: github/codeql-action/analyze@v3
```

CodeQL does deep taint tracking and produces findings. Copilot reads these findings
and explains them in PR comments with fix suggestions. Both are free for public repos.

-----

## 7. Optional: Vulnhuntr

Vulnhuntr (Protect AI, Apache 2.0) is an LLM-powered Python vulnerability agent.
It's optional because the Claude Code prompts above cover the same vulnerability classes.
But Vulnhuntr is systematic — it methodically traces every data flow — while manual
prompts depend on Claude's attention.

**Requires**: An `ANTHROPIC_API_KEY` (or OpenAI key, or local Ollama).
If you use Claude Code's API mode, you already have this key.

```bash
pip install vulnhuntr

# Reuse your existing Anthropic key
export ANTHROPIC_API_KEY=sk-ant-...

# Full repo scan (~$2-5 with Sonnet)
vulnhuntr -r . -a claude -v 2>&1 | tee vulnhuntr-report.txt

# Targeted scan on high-risk directories (cheaper)
vulnhuntr -r . -a claude -f src/api/ src/auth/ src/processing/

# Free alternative: local Ollama (lower quality)
# ollama pull llama3.1:70b
vulnhuntr -r . -a ollama -v
```

**Detected vulnerability classes**: RCE, LFI, SSRF, XSS, IDOR, SQLi, AFO (Arbitrary File Overwrite)

After Vulnhuntr runs, review findings in Claude Code:

```
Read vulnhuntr-report.txt. For each finding:
1. Read the actual source code at the reported locations
2. Verify whether it's a true positive
3. If confirmed, write a fix
4. If false positive, explain why
```

-----

## 8. Threat Modeling

Ask Claude Code to generate a threat model for your application.
No external tools needed — Claude reads the codebase and produces the model.

### STRIDE Threat Model Prompt

```
Read this entire codebase and generate a STRIDE threat model:

1. DATA FLOW DIAGRAM:
   Draw the architecture as a Mermaid diagram showing:
   - External entities (users, third-party APIs)
   - Processes (your application components)
   - Data stores (databases, caches, file systems)
   - Data flows between them (with protocols: HTTP, WS, SQL, etc.)
   - Trust boundaries (internet ↔ reverse proxy ↔ app ↔ database)

2. For each component and data flow, identify threats using STRIDE:
   - Spoofing: Can an attacker impersonate a legitimate entity?
   - Tampering: Can data be modified in transit or at rest?
   - Repudiation: Can actions be denied? (missing audit logs)
   - Information Disclosure: Can sensitive data leak?
   - Denial of Service: Can the component be overwhelmed?
   - Elevation of Privilege: Can a user gain unauthorized access?

3. RISK MATRIX:
   For each threat: Likelihood (1-5) × Impact (1-5) = Risk Score
   Focus on threats with Risk Score ≥ 12

4. EXISTING MITIGATIONS:
   Map current security controls to the threats they address.
   Identify gaps — threats with no mitigation.

5. RECOMMENDATIONS:
   For each unmitigated threat with Risk Score ≥ 12, recommend a specific control.
```

-----

## 9. Git Diff Security Review

A focused prompt for reviewing specific changes. Use in Claude Code before merging.

```
Review this git diff for security implications:

$(git diff origin/main...HEAD)

For each changed file, answer:
1. Does this change introduce new attack surface? (new endpoints, inputs, external calls)
2. Does it weaken existing security controls? (removed validation, relaxed auth, wider CORS)
3. Does it handle errors securely? (no information leakage, fail-closed behavior)
4. Are there any secrets, credentials, or tokens in the change?
5. If new dependencies are added: are they well-known, maintained, and necessary?
6. Does it introduce shared mutable state or race condition risks?

Output a table:
  File | Change Summary | Security Impact | Risk | Action Required
```
