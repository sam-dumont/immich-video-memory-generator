# Security & Quality Audit Report

**Project:** Immich Video Memory Compiler
**Date:** 2025-12-29
**Auditor:** Claude Opus 4.5
**Methodology:** Multi-pass analysis (automated scanning + deep manual review)

---

## Executive Summary

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0 | - |
| High | 3 | Action Required |
| Medium | 7 | Plan Remediation |
| Low | 12 | Backlog |

**Overall Risk Level:** MEDIUM

The codebase demonstrates good security practices in several areas (environment variable handling, input validation via Pydantic). However, there are security concerns around subprocess execution, insecure temporary file handling, and LLM integration that require attention before production deployment.

---

## Automated Scan Results

### Dependency Vulnerabilities (pip-audit)
✅ **PASS** - No known vulnerabilities in 120+ dependencies

### Static Security Analysis (Bandit)
- **HIGH Severity:** 1 (hash algorithm usage in music_sources.py)
- **MEDIUM Severity:** 2 (insecure mktemp usage)
- **LOW Severity:** 46 (subprocess calls without shell=True)

### Linting (Ruff)
- Import organization issues
- Unused imports
- Deprecated typing imports

### Type Safety (Mypy)
- **70+ type errors** across codebase
- Missing return type annotations
- Incorrect `callable` type hints (should be `Callable`)

---

## Detailed Findings

### HIGH-001: Insecure Temporary File Creation (CWE-377)

**Files:**
- `src/immich_memories/audio/mixer.py:106`
- `src/immich_memories/audio/mixer.py:188`

**Issue:** Use of deprecated `tempfile.mktemp()` which has a race condition vulnerability.

```python
# VULNERABLE CODE
output_path = Path(tempfile.mktemp(suffix=".mp3", prefix="looped_"))
```

**Risk:** An attacker with local access could exploit the race condition between file creation and use, potentially leading to symlink attacks or data tampering.

**Remediation:**
```python
# SECURE CODE
import tempfile
with tempfile.NamedTemporaryFile(suffix=".mp3", prefix="looped_", delete=False) as f:
    output_path = Path(f.name)
```

**Severity:** HIGH
**CVSS:** 5.5 (Local, Low complexity)

---

### HIGH-002: Subprocess Command Injection Risk (CWE-78)

**Files:**
- `src/immich_memories/audio/mixer.py:69,123,148,251`
- `src/immich_memories/audio/mood_analyzer.py:132,157`
- `src/immich_memories/processing/clips.py`
- `src/immich_memories/processing/transforms.py`
- `src/immich_memories/processing/hardware.py`

**Issue:** Multiple subprocess calls pass file paths without sanitization. While `shell=False` mitigates command injection, specially crafted filenames from Immich could still cause issues.

```python
# CURRENT CODE - Paths from external API
cmd = ["ffmpeg", "-i", str(video_path), ...]
subprocess.run(cmd, capture_output=True, check=True)
```

**Risk:** If Immich API returns a malicious filename containing shell metacharacters or null bytes, it could disrupt processing or cause unexpected behavior.

**Remediation:**
```python
# Add path validation
import os

def validate_path(path: Path) -> Path:
    """Validate and sanitize file path."""
    resolved = path.resolve()
    # Ensure path doesn't contain null bytes
    if '\x00' in str(resolved):
        raise ValueError("Path contains null bytes")
    # Ensure path is within expected directory
    if not str(resolved).startswith(str(ALLOWED_BASE_DIR)):
        raise ValueError("Path outside allowed directory")
    return resolved

# Use validated paths
validated_path = validate_path(video_path)
cmd = ["ffmpeg", "-i", str(validated_path), ...]
```

**Severity:** HIGH
**CVSS:** 6.3 (Network, requires specific conditions)

---

### HIGH-003: LLM Output Not Validated (CWE-20)

**File:** `src/immich_memories/audio/mood_analyzer.py:169-204`

**Issue:** LLM responses are parsed as JSON and used directly without content validation. While the current use case is low-risk (music selection), this pattern is dangerous.

```python
def _parse_mood_response(self, response_text: str) -> VideoMood:
    # No validation of extracted values
    data = json.loads(text)
    return VideoMood(
        primary_mood=data.get("primary_mood", "calm"),  # Unconstrained string
        genre_suggestions=data.get("genre_suggestions", []),  # List of any strings
        ...
    )
```

**Risk:**
- LLM could return unexpectedly large strings causing memory issues
- Malicious content in video frames could influence LLM to return harmful data
- XSS if values are displayed in UI without encoding

**Remediation:**
```python
VALID_MOODS = {"happy", "sad", "calm", "energetic", "romantic", "dramatic", "playful", "nostalgic"}
VALID_GENRES = {"acoustic", "electronic", "cinematic", "classical", "jazz", "pop", "ambient", "folk", "rock"}

def _parse_mood_response(self, response_text: str) -> VideoMood:
    data = json.loads(text)

    # Validate and constrain mood
    primary_mood = data.get("primary_mood", "calm")
    if primary_mood not in VALID_MOODS:
        primary_mood = "calm"

    # Validate genres
    genres = data.get("genre_suggestions", [])[:5]  # Limit count
    genres = [g for g in genres if g in VALID_GENRES]

    # Limit description length
    description = data.get("description", "")[:500]

    return VideoMood(
        primary_mood=primary_mood,
        genre_suggestions=genres,
        description=description,
        ...
    )
```

**Severity:** HIGH
**CVSS:** 5.4 (Indirect injection vector)

---

### MEDIUM-001: Secrets in Example Files

**Files:**
- `deploy/kubernetes/secret.yaml` (placeholder values)
- `deploy/terraform/examples/*/terraform.tfvars.example`

**Issue:** Secret YAML file contains placeholder values that could be accidentally replaced and committed with real secrets.

**Remediation:**
1. Add `secret.yaml` to `.gitignore`
2. Create `secret.yaml.template` without any values
3. Document use of sealed-secrets or external-secrets operator
4. Add pre-commit hook to detect secret patterns

---

### MEDIUM-002: Missing API Rate Limiting

**File:** `src/immich_memories/audio/music_sources.py`

**Issue:** No rate limiting on Pixabay API calls. Runaway processes could exhaust API quota.

**Remediation:** Implement exponential backoff and request counting.

---

### MEDIUM-003: Type Annotation Inconsistencies

**Files:** Multiple (70+ mypy errors)

**Key Issues:**
- `callable` used instead of `Callable` (src/immich_memories/api/immich.py:334)
- Missing return type annotations
- Incorrect `Any` type usage

**Remediation:** Run `mypy --strict` and fix all errors systematically.

---

### MEDIUM-004: Deprecated Pydantic Patterns

**File:** `src/immich_memories/api/models.py`

**Issue:** Using deprecated `class Config` instead of `model_config = ConfigDict(...)`.

```python
# DEPRECATED
class ExifInfo(BaseModel):
    class Config:
        extra = "allow"

# MODERN
class ExifInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
```

---

### MEDIUM-005: Kubernetes Container Security

**File:** `deploy/kubernetes/deployment.yaml`

**Issues:**
1. Container runs with default capabilities (should drop all)
2. No securityContext for read-only filesystem
3. No PodSecurityPolicy/PodSecurityStandard reference

**Remediation:**
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
  readOnlyRootFilesystem: true
```

---

### MEDIUM-006: Missing Input Length Limits

**File:** `src/immich_memories/audio/mood_analyzer.py`

**Issue:** No maximum limit on video file size or keyframe count, could lead to resource exhaustion.

---

### MEDIUM-007: Weak Hash for Cache Keys

**File:** `src/immich_memories/audio/music_sources.py:39`

```python
hash_id = hashlib.md5(f"{self.source}:{self.id}".encode()).hexdigest()[:12]
```

**Issue:** MD5 is cryptographically weak. While used only for cache filenames (not security-critical), it's better practice to use SHA-256.

---

### LOW Findings (12 total)

| ID | File | Issue |
|----|------|-------|
| LOW-001 | Multiple | Unsorted imports (Ruff I001) |
| LOW-002 | apple_vision.py | Unused import `pathlib.Path` |
| LOW-003 | apple_vision.py | Unused method arguments `scaleFactor`, `minNeighbors` |
| LOW-004 | config.py | Missing type stub for `yaml` |
| LOW-005 | scenes.py | OpenCV CUDA attributes not found by mypy |
| LOW-006 | Multiple | `from typing import Callable` should use `collections.abc` |
| LOW-007 | mood_analyzer.py | Missing `-> None` return annotations |
| LOW-008 | music_sources.py | Missing list type annotation |
| LOW-009 | api/immich.py | Generic `Any` return types |
| LOW-010 | Multiple | Subprocess import flagged (expected usage) |
| LOW-011 | processing/* | Complex filter expressions in subprocess |
| LOW-012 | Terraform | Node selector dynamic block incomplete |

---

## Infrastructure Security Assessment

### Kubernetes Deployment

| Check | Status | Notes |
|-------|--------|-------|
| Namespace isolation | ✅ PASS | Dedicated namespace |
| Secret management | ⚠️ WARN | Should use sealed-secrets |
| Resource limits | ✅ PASS | Memory/CPU limits defined |
| GPU resource requests | ✅ PASS | nvidia.com/gpu specified |
| RuntimeClass | ✅ PASS | nvidia RuntimeClass used |
| Health probes | ✅ PASS | Liveness/readiness defined |
| Security context | ❌ FAIL | Missing capability drops |
| Network policy | ❌ FAIL | Not defined |
| Pod security standard | ❌ FAIL | Not enforced |

### Terraform Module

| Check | Status | Notes |
|-------|--------|-------|
| Sensitive variable marking | ✅ PASS | API keys marked sensitive |
| State file security | ⚠️ WARN | Backend not configured |
| Variable validation | ⚠️ WARN | Could add more validation |
| Provider pinning | ✅ PASS | Version constraints defined |

---

## Remediation Roadmap

### Immediate (Before Production)

1. **Fix tempfile.mktemp usage** → Use NamedTemporaryFile
2. **Add LLM output validation** → Whitelist valid values
3. **Add container security context** → Drop capabilities, read-only FS

### Short-term (Sprint 1-2)

4. **Add path validation for subprocess** → Sanitize all file paths
5. **Fix mypy type errors** → Achieve strict compliance
6. **Add Kubernetes network policies** → Restrict pod communication
7. **Implement sealed-secrets** → Remove plaintext secrets

### Medium-term (Backlog)

8. **Add rate limiting** → Protect API integrations
9. **Update Pydantic patterns** → Remove deprecation warnings
10. **Add pre-commit security hooks** → Detect secrets, validate YAML
11. **Implement comprehensive logging** → Audit trail for security events

---

## Testing Recommendations

### Security Tests to Add

```python
# tests/test_security.py

def test_path_traversal_rejected():
    """Ensure path traversal attacks are blocked."""
    malicious_paths = [
        "../../../etc/passwd",
        "/etc/shadow",
        "file\x00.mp4",  # Null byte injection
        "$(whoami).mp4",  # Command substitution
    ]
    for path in malicious_paths:
        with pytest.raises(ValueError):
            validate_path(Path(path))

def test_llm_output_sanitized():
    """Ensure LLM output is validated."""
    malicious_response = '{"primary_mood": "<script>alert(1)</script>"}'
    mood = analyzer._parse_mood_response(malicious_response)
    assert "<script>" not in mood.primary_mood

def test_subprocess_timeout():
    """Ensure subprocess calls have timeouts."""
    # Verify all subprocess.run calls include timeout parameter
```

---

## Compliance Notes

| Standard | Status |
|----------|--------|
| OWASP Top 10 2021 | Partial (A03 Injection needs work) |
| CWE Top 25 | 2 applicable findings addressed |
| OWASP LLM Top 10 2025 | LLM01 (Prompt Injection) - Low risk in current usage |

---

## Appendix: Tool Versions

- Bandit: 1.9.2
- Ruff: 0.14.10
- Mypy: 1.19.1
- pip-audit: 2.10.0
- Python: 3.13.11

---

*Report generated using multi-pass LLM-assisted code auditing methodology.*
