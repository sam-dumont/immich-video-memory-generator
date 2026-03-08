# Tools Reference — Free / OSS Only

Install commands, configuration snippets, and framework-specific guidance.
Every tool here is free and open-source. No paid APIs, no SaaS accounts.
Last updated: March 2026.

## Table of Contents

1. [Ruff — Linting & Formatting](#ruff)
1. [mypy / Pyright — Type Checking](#type-checking)
1. [Vulture + dead — Dead Code](#dead-code)
1. [Coverage.py — Test Coverage](#coveragepy)
1. [Radon + Xenon + Wily — Complexity](#complexity)
1. [Bandit — Python Security Linting](#bandit)
1. [Semgrep OSS — SAST](#semgrep-oss)
1. [CodeQL — Deep Taint Analysis](#codeql)
1. [Gitleaks + TruffleHog — Secret Detection](#secret-detection)
1. [pip-audit + Trivy + GuardDog — Dependencies](#dependencies)
1. [Hadolint + Dockle + Trivy + Grype — Containers](#containers)
1. [ZAP + Nuclei — DAST](#dast)
1. [Scalene + py-spy + memray — Profiling](#profiling)
1. [FastAPI-Specific Security](#fastapi)
1. [NiceGUI-Specific Security](#nicegui)
1. [FFmpeg-Specific Security](#ffmpeg)
1. [GPU/CUDA-Specific Security](#gpu)

-----

## Ruff

**License**: MIT | **Replaces**: Flake8, Black, isort, pydocstyle, pyupgrade, autoflake

```bash
pip install ruff
ruff check .          # lint
ruff check --fix .    # lint + auto-fix
ruff format --check . # format check
ruff format .         # format apply
```

**pyproject.toml**:

```toml
[tool.ruff]
target-version = "py312"
line-length = 120

[tool.ruff.lint]
select = [
    "E", "W",    # pycodestyle
    "F",          # pyflakes
    "I",          # isort
    "N",          # pep8-naming
    "UP",         # pyupgrade
    "S",          # flake8-bandit (security)
    "B",          # bugbear
    "A",          # builtins shadowing
    "C4",         # comprehensions
    "DTZ",        # datetime timezone
    "T10",        # debugger statements
    "ISC",        # string concatenation
    "PIE",        # misc lints
    "PT",         # pytest style
    "RET",        # return statements
    "SIM",        # simplify
    "TCH",        # type checking imports
    "ARG",        # unused arguments
    "ERA",        # commented-out code (dead code detection!)
    "C901",       # McCabe complexity
    "RUF",        # ruff-specific
]
ignore = ["E501"]

[tool.ruff.lint.mccabe]
max-complexity = 15

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]  # allow assert in tests
```

-----

## Type Checking

### mypy (CI)

```bash
pip install mypy
mypy --strict src/
```

```toml
[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
warn_return_any = true
warn_unused_configs = true

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
```

### Pyright (IDE)

```bash
pip install pyright
pyright src/
```

### ty (Astral, beta — 60x faster than mypy)

```bash
pip install ty
ty check src/
```

-----

## Dead Code

### Vulture

```bash
pip install vulture
vulture src/ --min-confidence 80
vulture src/ --make-whitelist > whitelist_vulture.py   # generate whitelist
vulture src/ whitelist_vulture.py --min-confidence 80   # scan with whitelist
```

```toml
[tool.vulture]
min_confidence = 80
paths = ["src/"]
exclude = ["src/migrations/"]
```

**Common false positives to whitelist**: framework callbacks (FastAPI routes, signal handlers),
magic methods, Celery tasks, Click commands, pytest fixtures, variables in f-strings.

### dead

```bash
pip install dead
dead
```

-----

## Coverage.py

```bash
pip install coverage
coverage run --branch -m pytest               # test with branch coverage
coverage report --show-missing --fail-under=80 # report
coverage html                                  # visual HTML report
coverage json -o coverage.json                 # JSON for CI
```

```toml
[tool.coverage.run]
branch = true
source = ["src"]
omit = ["tests/*", "src/migrations/*"]

[tool.coverage.report]
fail_under = 80
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "if __name__ == .__main__.",
    "@overload",
    "raise NotImplementedError",
]
```

-----

## Complexity

### Radon

```bash
pip install radon
radon cc src/ -a -nc          # cyclomatic complexity (skip simple functions)
radon cc src/ -a -nc -j > cc.json  # JSON output
radon mi src/ -nc             # maintainability index
radon raw src/ -s             # raw metrics (LOC, SLOC, comments)
```

Thresholds: CC 1-5=A(simple), 6-10=B(moderate), 11-15=C(complex), 16-20=D(refactor), 21+=F(rewrite)
MI > 40 = good, 20-40 = moderate, < 20 = unmaintainable

### Xenon (CI gate)

```bash
pip install xenon
xenon --max-absolute B --max-modules B --max-average A src/
# Permissive for legacy: --max-absolute C --max-modules B --max-average B
```

### Wily (trends over time)

```bash
pip install wily
wily build src/                    # index git history
wily report src/core/processing.py # report on specific file
wily diff src/ -r HEAD~10          # complexity diff over last 10 commits
wily graph src/core/processing.py cc  # graph trend
```

-----

## Bandit

```bash
pip install bandit
bandit -r src/ -f json -o bandit-report.json   # JSON
bandit -r src/ -f sarif -o bandit.sarif         # SARIF for GitHub
bandit -r src/ -ll                              # medium+ severity only
bandit -r src/ -s B101                          # skip specific rules
```

-----

## Semgrep OSS

Free community edition. 3000+ rules, single-file analysis.

```bash
pip install semgrep
semgrep scan --config auto                                    # auto-detect
semgrep scan --config p/python --config p/owasp-top-ten       # Python + OWASP
semgrep scan --config p/security-audit                        # security focused
semgrep scan --config ./semgrep-rules/                        # custom rules
semgrep scan --config auto --json -o semgrep.json             # JSON output
semgrep scan --config auto --sarif -o semgrep.sarif           # SARIF for GitHub
```

See `references/ai-security-prompts.md` section 4 for custom rule examples
(FFmpeg, FastAPI, general Python security).

-----

## CodeQL

Free for public repos. Free via GitHub Actions for private repos with GHAS.

Enable: repo Settings → Code security → CodeQL. Or add the GitHub Action:

```yaml
- uses: github/codeql-action/init@v3
  with:
    languages: python
- uses: github/codeql-action/analyze@v3
```

-----

## Secret Detection

### Gitleaks (MIT, fastest)

```bash
gitleaks detect --source . --verbose                          # current state
gitleaks detect --source . --verbose --log-opts="--all"       # full git history
gitleaks protect --staged --verbose                           # pre-commit hook
gitleaks detect --source . -f json -r gitleaks-report.json    # JSON output
```

### TruffleHog (AGPL, most thorough)

```bash
pip install trufflehog
trufflehog filesystem . --only-verified                       # verified secrets only
trufflehog git file://. --only-verified                       # git history
trufflehog filesystem . --only-verified --json > trufflehog.json
```

-----

## Dependencies

### pip-audit

```bash
pip install pip-audit
pip-audit --strict --desc                                 # installed packages
pip-audit -r requirements.txt                             # from requirements
pip-audit --format json --output audit.json               # JSON
pip-audit --format sarif --output pip-audit.sarif          # SARIF
pip-audit --fix                                           # auto-fix
```

### Trivy (filesystem)

```bash
trivy fs --scanners vuln,secret,misconfig .
trivy fs --severity HIGH,CRITICAL --exit-code 1 .
```

### GuardDog (typosquatting)

```bash
pip install guarddog
guarddog pypi verify <package-name>
guarddog pypi verify -r requirements.txt
```

### OSV-Scanner

```bash
osv-scanner --lockfile=requirements.txt
osv-scanner --lockfile=uv.lock
```

### SBOM

```bash
syft . -o cyclonedx-json > sbom.json              # container-aware
cyclonedx-py environment -o sbom.json --format json # Python-specific
```

-----

## Containers

### Hadolint (Dockerfile lint)

```bash
hadolint Dockerfile
hadolint --ignore DL3008 --ignore DL3013 Dockerfile
```

### Dockle (CIS benchmark)

```bash
dockle myimage:latest
dockle --exit-code 1 --exit-level warn myimage:latest
```

### Trivy (image scan)

```bash
trivy image --severity HIGH,CRITICAL myimage:latest
trivy image --exit-code 1 --severity CRITICAL myimage:latest
trivy image --format cyclonedx --output sbom.json myimage:latest
```

### Grype (complementary scan)

```bash
grype myimage:latest
grype myimage:latest --fail-on high
```

### Cosign (image signing, free)

```bash
cosign sign --yes myregistry/myimage@sha256:abc123...   # keyless via OIDC
cosign verify myregistry/myimage@sha256:abc123...       # verify
```

-----

## DAST

### ZAP

```bash
docker run -t ghcr.io/zaproxy/zaproxy:stable \
  zap-api-scan.py -t http://host.docker.internal:8000/openapi.json -f openapi
```

### Nuclei

```bash
nuclei -u http://localhost:8000 -t cves/ -t exposures/ -t misconfiguration/
nuclei -u http://localhost:8000 -as   # auto-select based on tech detection
```

-----

## Profiling

### Scalene (CPU + memory + GPU)

```bash
pip install scalene
scalene --cpu --memory --gpu your_app.py
scalene --cpu --memory --gpu --- -m pytest tests/
```

### py-spy (production-safe)

```bash
pip install py-spy
py-spy record -o profile.svg --pid <PID>
py-spy top --pid <PID>
```

### memray (memory)

```bash
pip install memray
memray run your_app.py
memray flamegraph memray-*.bin -o memory.html
pytest --memray  # with pytest-memray plugin
```

### Locust (load testing)

```bash
pip install locust
locust -f locustfile.py --host=http://localhost:8000
```

-----

## FastAPI

- Disable docs in production: `FastAPI(docs_url=None, redoc_url=None)`
- CORS: never `allow_origins=["*"]` in production. Explicit allowlist.
- Rate limiting: `pip install slowapi` — the de facto standard for FastAPI
- Input validation: Pydantic v2 strict mode. Separate read/write schemas.
- Never use `assert` for security (stripped with `-O`)
- Security headers middleware: HSTS, CSP, X-Frame-Options, X-Content-Type-Options
- Middleware order: TrustedHost → HTTPS Redirect → CORS → Security Headers → Rate Limiting

-----

## NiceGUI

- All UI interactions flow over WebSocket (`/_nicegui_ws/socket.io/`)
- **CVE-2025-21618** (CVSS 7.5): Auth bypass in On Air — update to latest
- Reverse proxy must allow WebSocket upgrades on NiceGUI path
- Sticky sessions required for multi-worker deployments
- Auth: implement via FastAPI middleware (NiceGUI sits on top of FastAPI)
- CSP must allow WebSocket connections

-----

## FFmpeg

- **Never** `shell=True` with user input
- Always pass arguments as a list: `subprocess.run(["ffmpeg", "-i", file, out], shell=False, timeout=300)`
- Validate file magic bytes with `python-magic` before processing
- Use `ffprobe` to inspect before FFmpeg touches the file
- Set resource limits: `-fs` (file size), `-t` (duration), `timeout` in subprocess
- For untrusted media: container per job, read-only fs, no network, seccomp, CPU/mem limits

-----

## GPU

- **CVE-2025-23266** (CVSS 9.0): Container escape via NVIDIA Container Toolkit.
  Requires Toolkit v1.17.8+ and GPU Operator 25.3.1+. Patch immediately.
- Never install NVIDIA drivers inside containers — use NVIDIA Container Toolkit on host
- Multi-stage builds: `devel` for compilation, `runtime` for deployment
- Pin CUDA/cuDNN versions explicitly
- Run as non-root
- Use image digests, not tags
- Scan CUDA base images with Trivy (they contain OS packages with CVEs)
- Profile with Scalene + NVIDIA Nsight Systems/Compute
- Annotate code with `@nvtx.annotate()` for Nsight visibility
