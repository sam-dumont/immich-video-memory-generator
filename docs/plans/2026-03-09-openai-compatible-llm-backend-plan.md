# OpenAI-Compatible LLM Backend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the split Ollama/OpenAI config and providers with a unified `"ollama"` | `"openai-compatible"` model using flat config fields.

**Architecture:** Two LLM providers — `OllamaContentAnalyzer` (unchanged protocol) and `OpenAICompatibleContentAnalyzer` (renamed, covers OpenAI cloud, Groq, mlx-vlm, vLLM, etc.). Config collapses from 6 prefixed fields to 4 flat ones. No auto-fallback — explicit provider selection.

**Tech Stack:** Python, Pydantic v2, httpx, pytest

**Design doc:** `docs/plans/2026-03-09-openai-compatible-llm-backend-design.md`

---

### Task 1: New LLMConfig with flat fields

**Files:**
- Modify: `src/immich_memories/config_models.py:251-291`
- Test: `tests/test_llm_config.py`

**Step 1: Write failing test for new LLMConfig defaults**

```python
# tests/test_llm_config.py
"""Tests for LLMConfig with flat provider fields."""

from immich_memories.config_models import LLMConfig


class TestLLMConfigDefaults:
    def test_default_provider_is_openai_compatible(self):
        config = LLMConfig()
        assert config.provider == "openai-compatible"

    def test_default_base_url(self):
        config = LLMConfig()
        assert config.base_url == "http://localhost:8080/v1"

    def test_default_model_is_empty(self):
        config = LLMConfig()
        assert config.model == ""

    def test_default_api_key_is_empty(self):
        config = LLMConfig()
        assert config.api_key == ""
```

**Step 2: Run test to verify it fails**

Run: `make test` (or `uv run pytest tests/test_llm_config.py -v`)
Expected: FAIL — old LLMConfig doesn't have `base_url`, `model`, `api_key` fields

**Step 3: Implement new LLMConfig**

Replace LLMConfig in `src/immich_memories/config_models.py:251-291` with:

```python
class LLMConfig(BaseModel):
    """Shared LLM provider settings.

    Two providers: "ollama" (native Ollama API) or "openai-compatible"
    (any server speaking /v1/chat/completions — OpenAI, Groq, mlx-vlm, vLLM, etc.).
    """

    provider: Literal["ollama", "openai-compatible"] = Field(
        default="openai-compatible",
        description="LLM provider: 'ollama' or 'openai-compatible'",
    )
    base_url: str = Field(
        default="http://localhost:8080/v1",
        description="API base URL (e.g. http://localhost:11434 for Ollama, http://localhost:8080/v1 for mlx-vlm)",
    )
    model: str = Field(
        default="",
        description="Model name (e.g. 'llava' for Ollama, 'qwen3.5-9b' for mlx-vlm)",
    )
    api_key: str = Field(
        default="",
        description="API key (optional, only needed for cloud APIs like OpenAI/Groq)",
    )

    @field_validator("api_key", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v
```

**Step 4: Run test to verify it passes**

Run: `make test`
Expected: `test_llm_config.py` PASSES, but other tests that reference old fields will FAIL — that's expected, we fix them in later tasks.

**Step 5: Commit**

```bash
git add tests/test_llm_config.py src/immich_memories/config_models.py
git commit -m "feat(config): replace LLMConfig with flat provider/base_url/model/api_key fields"
```

---

### Task 2: Config backwards compatibility migration

**Files:**
- Modify: `src/immich_memories/config_models.py` (add model_validator to LLMConfig)
- Test: `tests/test_llm_config.py`

**Step 1: Write failing test for old-field migration**

```python
# Add to tests/test_llm_config.py
class TestLLMConfigMigration:
    def test_old_ollama_fields_migrate(self):
        """Old ollama_url/ollama_model fields should map to new flat fields."""
        config = LLMConfig.model_validate({
            "ollama_url": "http://myserver:11434",
            "ollama_model": "moondream",
            "provider": "ollama",
        })
        assert config.base_url == "http://myserver:11434"
        assert config.model == "moondream"
        assert config.provider == "ollama"

    def test_old_openai_fields_migrate(self):
        """Old openai_* fields should map to new flat fields."""
        config = LLMConfig.model_validate({
            "openai_api_key": "sk-test",
            "openai_model": "gpt-4o-mini",
            "openai_base_url": "https://api.openai.com/v1",
            "provider": "openai",
        })
        assert config.api_key == "sk-test"
        assert config.model == "gpt-4o-mini"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.provider == "openai-compatible"

    def test_old_auto_provider_migrates_to_ollama(self):
        """Old 'auto' provider should migrate to 'ollama' (was the primary)."""
        config = LLMConfig.model_validate({
            "provider": "auto",
            "ollama_url": "http://localhost:11434",
            "ollama_model": "llava",
        })
        assert config.provider == "ollama"
        assert config.base_url == "http://localhost:11434"

    def test_new_fields_take_priority(self):
        """If both old and new fields present, new fields win."""
        config = LLMConfig.model_validate({
            "base_url": "http://new:8080/v1",
            "model": "qwen3.5",
            "ollama_url": "http://old:11434",
            "ollama_model": "llava",
        })
        assert config.base_url == "http://new:8080/v1"
        assert config.model == "qwen3.5"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_config.py::TestLLMConfigMigration -v`
Expected: FAIL — no migration logic yet

**Step 3: Add model_validator for backwards compat**

Add to LLMConfig class in `config_models.py`:

```python
    @model_validator(mode="before")
    @classmethod
    def migrate_old_fields(cls, data: dict) -> dict:
        """Migrate old ollama_*/openai_* fields to flat fields."""
        if not isinstance(data, dict):
            return data

        has_old = any(
            k in data
            for k in ("ollama_url", "ollama_model", "openai_api_key", "openai_model", "openai_base_url")
        )
        if not has_old:
            return data

        import logging
        logging.getLogger(__name__).warning(
            "LLM config uses deprecated field names (ollama_url, openai_*, etc.). "
            "Migrate to: provider, base_url, model, api_key"
        )

        # Don't overwrite new-style fields if already present
        old_provider = data.get("provider", "auto")

        if "base_url" not in data or data["base_url"] == "http://localhost:8080/v1":
            if old_provider in ("ollama", "auto"):
                data.setdefault("base_url", data.get("ollama_url", "http://localhost:11434"))
            else:
                data.setdefault("base_url", data.get("openai_base_url", "https://api.openai.com/v1"))

        if "model" not in data or data["model"] == "":
            if old_provider in ("ollama", "auto"):
                data.setdefault("model", data.get("ollama_model", "llava"))
            else:
                data.setdefault("model", data.get("openai_model", "gpt-4.1-nano"))

        if "api_key" not in data or data["api_key"] == "":
            data.setdefault("api_key", data.get("openai_api_key", ""))

        # Migrate provider values
        if old_provider == "auto":
            data["provider"] = "ollama"
        elif old_provider == "openai":
            data["provider"] = "openai-compatible"

        # Clean up old keys so pydantic doesn't complain
        for old_key in ("ollama_url", "ollama_model", "openai_api_key", "openai_model", "openai_base_url"):
            data.pop(old_key, None)

        return data
```

Also add `model_validator` to the imports from pydantic at the top of config_models.py.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_config.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/immich_memories/config_models.py tests/test_llm_config.py
git commit -m "feat(config): add backwards-compat migration for old LLM field names"
```

---

### Task 3: Rename OpenAIContentAnalyzer → OpenAICompatibleContentAnalyzer

**Files:**
- Modify: `src/immich_memories/analysis/_content_providers.py:199-323`
- Modify: `src/immich_memories/analysis/content_analyzer.py` (imports + re-exports)
- Test: `tests/test_llm_config.py` (add provider instantiation test)

**Step 1: Write failing test**

```python
# Add to tests/test_llm_config.py
class TestOpenAICompatibleProvider:
    def test_import_new_name(self):
        """OpenAICompatibleContentAnalyzer should be importable."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer
        assert OpenAICompatibleContentAnalyzer is not None

    def test_accepts_empty_api_key(self):
        """Local servers (mlx-vlm) don't need an API key."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer
        analyzer = OpenAICompatibleContentAnalyzer(
            model="qwen3.5-9b",
            base_url="http://localhost:8080/v1",
            api_key="",
        )
        assert analyzer.model == "qwen3.5-9b"
        assert analyzer.api_key == ""

    def test_timeout_is_120s(self):
        """Local vision models need longer timeouts."""
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer
        analyzer = OpenAICompatibleContentAnalyzer(
            model="test", base_url="http://localhost:8080/v1", api_key=""
        )
        client = analyzer.client
        assert client.timeout.read == 120.0
        analyzer.close()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_config.py::TestOpenAICompatibleProvider -v`
Expected: FAIL — no `OpenAICompatibleContentAnalyzer`

**Step 3: Rename in _content_providers.py**

In `src/immich_memories/analysis/_content_providers.py`:
- Rename `class OpenAIContentAnalyzer` → `class OpenAICompatibleContentAnalyzer`
- Change `api_key` parameter to default `""` (was required)
- Only add `Authorization` header if `api_key` is non-empty
- Change timeout from `60.0` to `120.0`
- Update `is_available()` to return `True` always (availability checked by preflight)

```python
class OpenAICompatibleContentAnalyzer(ContentAnalyzer):
    """Content analyzer for any OpenAI-compatible API.

    Works with: OpenAI, Groq, mlx-vlm, vLLM, LM Studio, llama.cpp server.
    """

    def __init__(
        self,
        model: str = "",
        base_url: str = "http://localhost:8080/v1",
        api_key: str = "",
        image_detail: str = "low",
        max_height: int = 480,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.image_detail = image_detail
        self.max_height = max_height
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.Client(timeout=120.0, headers=headers)
        return self._client

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def is_available(self) -> bool:
        return True  # Availability checked by preflight
    # ... rest of analyze_segment stays the same
```

In `content_analyzer.py`, update imports:
```python
from immich_memories.analysis._content_providers import (
    OllamaContentAnalyzer,
    OpenAICompatibleContentAnalyzer,
)
# Backwards compat alias
OpenAIContentAnalyzer = OpenAICompatibleContentAnalyzer
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_config.py::TestOpenAICompatibleProvider -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/immich_memories/analysis/_content_providers.py src/immich_memories/analysis/content_analyzer.py tests/test_llm_config.py
git commit -m "refactor(analysis): rename OpenAIContentAnalyzer to OpenAICompatibleContentAnalyzer"
```

---

### Task 4: Update content analyzer factory to use flat config

**Files:**
- Modify: `src/immich_memories/analysis/content_analyzer.py:34-139`
- Test: `tests/test_llm_config.py`

**Step 1: Write failing test**

```python
# Add to tests/test_llm_config.py
from unittest.mock import patch, MagicMock


class TestGetContentAnalyzer:
    def test_ollama_provider_returns_ollama_analyzer(self):
        from immich_memories.analysis.content_analyzer import get_content_analyzer
        from immich_memories.analysis._content_providers import OllamaContentAnalyzer

        analyzer = get_content_analyzer(
            provider="ollama",
            base_url="http://localhost:11434",
            model="llava",
        )
        assert isinstance(analyzer, OllamaContentAnalyzer)
        assert analyzer.model == "llava"
        assert analyzer.base_url == "http://localhost:11434"

    def test_openai_compatible_provider_returns_compat_analyzer(self):
        from immich_memories.analysis.content_analyzer import get_content_analyzer
        from immich_memories.analysis._content_providers import OpenAICompatibleContentAnalyzer

        analyzer = get_content_analyzer(
            provider="openai-compatible",
            base_url="http://localhost:8080/v1",
            model="qwen3.5-9b",
            api_key="",
        )
        assert isinstance(analyzer, OpenAICompatibleContentAnalyzer)
        assert analyzer.model == "qwen3.5-9b"

    def test_unknown_provider_returns_none(self):
        from immich_memories.analysis.content_analyzer import get_content_analyzer

        analyzer = get_content_analyzer(
            provider="unknown",
            base_url="http://localhost:8080",
            model="test",
        )
        assert analyzer is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_config.py::TestGetContentAnalyzer -v`
Expected: FAIL — old function signature uses `ollama_url`, `openai_api_key`, etc.

**Step 3: Rewrite factory function**

Replace `get_content_analyzer()` in `content_analyzer.py`:

```python
def get_content_analyzer(
    provider: str = "openai-compatible",
    base_url: str = "http://localhost:8080/v1",
    model: str = "",
    api_key: str = "",
    image_detail: str = "low",
    max_height: int = 480,
    num_ctx: int = 4096,
) -> ContentAnalyzer | None:
    """Get content analyzer for the configured provider.

    Args:
        provider: "ollama" or "openai-compatible".
        base_url: API base URL.
        model: Model name.
        api_key: API key (only needed for cloud APIs).
        image_detail: Image detail level for OpenAI-compatible ("low"/"high"/"auto").
        max_height: Maximum frame height in pixels.
        num_ctx: Context window size (Ollama only).

    Returns:
        ContentAnalyzer instance or None if provider is unknown.
    """
    if provider == "ollama":
        analyzer = OllamaContentAnalyzer(
            model=model,
            base_url=base_url,
            max_height=max_height,
            num_ctx=num_ctx,
        )
        logger.info(f"Using Ollama for content analysis (model: {model}, num_ctx: {num_ctx})")
        return analyzer

    if provider == "openai-compatible":
        analyzer = OpenAICompatibleContentAnalyzer(
            model=model,
            base_url=base_url,
            api_key=api_key,
            image_detail=image_detail,
            max_height=max_height,
        )
        logger.info(f"Using OpenAI-compatible for content analysis (model: {model}, url: {base_url})")
        return analyzer

    logger.warning(f"Unknown LLM provider: {provider}")
    return None
```

Also update `get_content_analyzer_from_config()`:

```python
def get_content_analyzer_from_config() -> ContentAnalyzer | None:
    from immich_memories.config import get_config

    config = get_config()
    if not config.content_analysis.enabled:
        logger.info("Content analysis is disabled in config")
        return None

    llm = config.llm
    ca = config.content_analysis

    return get_content_analyzer(
        provider=llm.provider,
        base_url=llm.base_url,
        model=llm.model,
        api_key=llm.api_key,
        image_detail=ca.openai_image_detail,
        max_height=ca.frame_max_height,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_config.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/immich_memories/analysis/content_analyzer.py tests/test_llm_config.py
git commit -m "refactor(analysis): update content analyzer factory to use flat config fields"
```

---

### Task 5: Update all call sites that read config.llm.* old fields

**Files:**
- Modify: `src/immich_memories/analysis/analyzer_factory.py:34-41`
- Modify: `src/immich_memories/analysis/scoring_factory.py:109-112`
- Modify: `src/immich_memories/analysis/pipeline_analysis.py:246-250`
- Modify: `src/immich_memories/cli/music_cmd.py:95-115`
- Modify: `src/immich_memories/config_loader.py:113-114`
- Test: existing tests must pass

**Step 1: Write failing test**

```python
# Add to tests/test_llm_config.py
class TestFromConfigIntegration:
    @patch("immich_memories.analysis.analyzer_factory.get_config")
    def test_analyzer_factory_uses_flat_fields(self, mock_cfg):
        """analyzer_factory should read flat config.llm fields."""
        mock_cfg.return_value.llm.provider = "ollama"
        mock_cfg.return_value.llm.base_url = "http://test:11434"
        mock_cfg.return_value.llm.model = "llava"
        mock_cfg.return_value.llm.api_key = ""
        mock_cfg.return_value.content_analysis.enabled = False
        mock_cfg.return_value.audio_content.enabled = False
        mock_cfg.return_value.analysis.optimal_clip_duration = 5.0
        mock_cfg.return_value.analysis.max_optimal_duration = 15.0
        mock_cfg.return_value.analysis.target_extraction_ratio = 0.25
        mock_cfg.return_value.analysis.min_segment_duration = 2.0
        mock_cfg.return_value.analysis.max_segment_duration = 15.0
        mock_cfg.return_value.analysis.silence_threshold_db = -30.0
        mock_cfg.return_value.analysis.min_silence_duration = 0.3
        mock_cfg.return_value.analysis.cut_point_merge_tolerance = 0.5
        mock_cfg.return_value.analysis.duration_weight = 0.15

        from immich_memories.analysis.analyzer_factory import create_unified_analyzer_from_config
        analyzer = create_unified_analyzer_from_config()
        assert analyzer is not None
        # content_analyzer should be None since enabled=False
        assert analyzer.content_analyzer is None
```

**Step 2: Run to verify it fails**

Expected: FAIL — `AttributeError: 'MagicMock' object has no attribute 'ollama_url'` (old field name still referenced)

**Step 3: Update all call sites**

Each file that reads `config.llm.ollama_url` etc. gets updated to `config.llm.base_url`, `config.llm.model`, `config.llm.api_key`, `config.llm.provider`.

**`analyzer_factory.py`** — change lines 35-40:
```python
content_analyzer = get_content_analyzer(
    provider=config.llm.provider,
    base_url=config.llm.base_url,
    model=config.llm.model,
    api_key=config.llm.api_key,
)
```

**`scoring_factory.py`** — change lines 109-112:
```python
ollama_url=config.llm.base_url,    # → base_url
ollama_model=config.llm.model,     # → model
openai_api_key=config.llm.api_key, # → api_key
openai_model=config.llm.model,     # → model
```
Note: scoring_factory still calls the old `get_content_analyzer` signature. Check if it calls its own factory or imports from `content_analyzer.py`. Update to new signature.

**`pipeline_analysis.py`** — change lines 246-250 to use new flat fields.

**`music_cmd.py`** — change lines 106-115 to use `config.llm.base_url`, `config.llm.model`, `config.llm.api_key`, `config.llm.provider`.

**`config_loader.py`** — change line 114 from `_config.llm.openai_api_key = openai_key` to `_config.llm.api_key = openai_key`.

**Step 4: Run tests**

Run: `make test`
Expected: ALL PASS (including the new test and all existing tests)

**Step 5: Commit**

```bash
git add src/immich_memories/analysis/analyzer_factory.py src/immich_memories/analysis/scoring_factory.py src/immich_memories/analysis/pipeline_analysis.py src/immich_memories/cli/music_cmd.py src/immich_memories/config_loader.py tests/test_llm_config.py
git commit -m "refactor: update all call sites to use flat LLM config fields"
```

---

### Task 6: Rename mood analyzer backends

**Files:**
- Modify: `src/immich_memories/audio/mood_analyzer_backends.py`
- Modify: `src/immich_memories/audio/mood_analyzer.py` (re-exports)
- Test: `tests/test_llm_config.py`

**Step 1: Write failing test**

```python
# Add to tests/test_llm_config.py
class TestOpenAICompatibleMoodAnalyzer:
    def test_import_new_name(self):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer
        assert OpenAICompatibleMoodAnalyzer is not None

    def test_accepts_empty_api_key(self):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer
        analyzer = OpenAICompatibleMoodAnalyzer(
            model="qwen3.5-9b",
            base_url="http://localhost:8080/v1",
            api_key="",
        )
        assert analyzer.model == "qwen3.5-9b"
```

**Step 2: Run test to verify it fails**

**Step 3: Rename and update**

In `mood_analyzer_backends.py`:
- Rename `OpenAIMoodAnalyzer` → `OpenAICompatibleMoodAnalyzer`
- `api_key` defaults to `""`, only set Authorization header if non-empty
- Timeout from `60.0` to `120.0`

Update `get_mood_analyzer()` factory:
```python
async def get_mood_analyzer(
    provider: str = "openai-compatible",
    base_url: str = "http://localhost:8080/v1",
    model: str = "",
    api_key: str = "",
) -> MoodAnalyzer:
    if provider == "ollama":
        ollama = OllamaMoodAnalyzer(model=model, base_url=base_url)
        if await ollama.is_available():
            logger.info(f"Using Ollama ({model}) for mood analysis")
            return ollama
        raise RuntimeError(f"Ollama not available at {base_url}")

    if provider == "openai-compatible":
        logger.info(f"Using OpenAI-compatible ({model}) for mood analysis")
        return OpenAICompatibleMoodAnalyzer(model=model, base_url=base_url, api_key=api_key)

    raise RuntimeError(f"Unknown provider: {provider}")
```

Update `get_mood_analyzer_from_config()`:
```python
async def get_mood_analyzer_from_config() -> MoodAnalyzer:
    from immich_memories.config import get_config
    config = get_config()
    llm = config.llm
    return await get_mood_analyzer(
        provider=llm.provider,
        base_url=llm.base_url,
        model=llm.model,
        api_key=llm.api_key,
    )
```

In `mood_analyzer.py` re-exports, add alias:
```python
from immich_memories.audio.mood_analyzer_backends import (
    OllamaMoodAnalyzer,
    OpenAICompatibleMoodAnalyzer,
    OpenAICompatibleMoodAnalyzer as OpenAIMoodAnalyzer,  # backwards compat
    get_mood_analyzer,
    get_mood_analyzer_from_config,
)
```

Update `music_cmd.py` to use the new factory signature.

**Step 4: Run tests**

Run: `make test`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/immich_memories/audio/mood_analyzer_backends.py src/immich_memories/audio/mood_analyzer.py src/immich_memories/cli/music_cmd.py tests/test_llm_config.py
git commit -m "refactor(audio): rename OpenAIMoodAnalyzer to OpenAICompatibleMoodAnalyzer"
```

---

### Task 7: Update preflight checks

**Files:**
- Modify: `src/immich_memories/preflight.py:112-257`
- Test: `tests/test_llm_config.py`

**Step 1: Write failing test**

```python
# Add to tests/test_llm_config.py
class TestPreflightLLMCheck:
    @patch("immich_memories.preflight.httpx.Client")
    def test_openai_compatible_sends_test_completion(self, mock_client_cls):
        """Preflight for openai-compatible should send a minimal chat completion."""
        from immich_memories.preflight import check_llm, CheckStatus
        from immich_memories.config import Config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}}],
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        config = Config()
        config.llm.provider = "openai-compatible"
        config.llm.base_url = "http://localhost:8080/v1"
        config.llm.model = "test-model"

        result = check_llm(config)
        assert result.status == CheckStatus.OK

        # Verify it sent a POST to chat/completions
        call_args = mock_client.post.call_args
        assert "/v1/chat/completions" in call_args[0][0]

    @patch("immich_memories.preflight.httpx.Client")
    def test_ollama_checks_api_tags(self, mock_client_cls):
        """Preflight for ollama should check /api/tags."""
        from immich_memories.preflight import check_llm, CheckStatus
        from immich_memories.config import Config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [{"name": "llava:latest"}],
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        config = Config()
        config.llm.provider = "ollama"
        config.llm.base_url = "http://localhost:11434"
        config.llm.model = "llava"

        result = check_llm(config)
        assert result.status == CheckStatus.OK

        # Verify it sent a GET to /api/tags
        call_args = mock_client.get.call_args
        assert "/api/tags" in call_args[0][0]
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `check_llm` doesn't exist

**Step 3: Implement unified check_llm**

Replace `check_ollama()` and `check_openai()` with:

```python
def check_llm(config: Config | None = None) -> CheckResult:
    """Check LLM provider availability."""
    if config is None:
        config = get_config()

    provider = config.llm.provider
    base_url = config.llm.base_url
    model = config.llm.model

    if not base_url:
        return CheckResult(
            name="LLM", status=CheckStatus.SKIPPED,
            message="Not configured", details="No base_url set",
        )

    if provider == "ollama":
        return _check_ollama(base_url, model)
    if provider == "openai-compatible":
        return _check_openai_compatible(base_url, model, config.llm.api_key)

    return CheckResult(
        name="LLM", status=CheckStatus.ERROR,
        message=f"Unknown provider: {provider}",
    )


def _check_ollama(base_url: str, model: str) -> CheckResult:
    """Check Ollama server via /api/tags."""
    # (move existing check_ollama logic here, using base_url and model params)
    ...


def _check_openai_compatible(base_url: str, model: str, api_key: str) -> CheckResult:
    """Check OpenAI-compatible server via test completion."""
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        with httpx.Client(timeout=10.0, headers=headers) as client:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            response = client.post(f"{base_url}/chat/completions", json=payload)

            if response.status_code == 401:
                return CheckResult(
                    name="LLM", status=CheckStatus.ERROR,
                    message="Authentication failed",
                    details=f"API key rejected by {base_url}",
                )

            response.raise_for_status()
            return CheckResult(
                name="LLM", status=CheckStatus.OK,
                message=f"Connected ({provider_label})",
                details=f"Server: {base_url}, Model: {model}",
            )

    except httpx.ConnectError:
        return CheckResult(
            name="LLM", status=CheckStatus.WARNING,
            message="Cannot connect",
            details=f"Server not reachable at {base_url}",
        )
    except Exception as e:
        return CheckResult(
            name="LLM", status=CheckStatus.WARNING,
            message="Connection error", details=str(e),
        )
```

Update `run_preflight_checks()` to call `check_llm(config)` instead of separate `check_ollama`/`check_openai`.

**Step 4: Run tests**

Run: `make test`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/immich_memories/preflight.py tests/test_llm_config.py
git commit -m "refactor(preflight): replace check_ollama/check_openai with unified check_llm"
```

---

### Task 8: Update existing test fixtures + fix test_unified_analyzer

**Files:**
- Modify: `tests/test_unified_analyzer.py:459-463`
- Test: run full suite

**Step 1: Fix old field references in test_unified_analyzer**

Lines 459-463 reference `config.llm.ollama_url`, etc. Update to:
```python
mock_cfg.return_value.llm.provider = "ollama"
mock_cfg.return_value.llm.base_url = "http://localhost:11434"
mock_cfg.return_value.llm.model = "llava"
mock_cfg.return_value.llm.api_key = ""
```

**Step 2: Run full test suite**

Run: `make test`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add tests/test_unified_analyzer.py
git commit -m "test: update test fixtures to use new flat LLM config fields"
```

---

### Task 9: Update local config + docker env example

**Files:**
- Modify: `~/.immich-memories/config.yaml`
- Modify: `docker/.env.example`

**Step 1: Update local config**

```yaml
llm:
  provider: ollama
  base_url: http://10.2.254.60:11434
  model: moondream
  api_key: ''
```

**Step 2: Update docker/.env.example if needed**

Add LLM env vars if they should be documented.

**Step 3: Commit**

```bash
git add docker/.env.example
git commit -m "docs: update docker env example for new LLM config fields"
```

---

### Task 10: Run full CI + cleanup

**Step 1: Run full CI**

Run: `make ci`
Expected: ALL PASS (lint, format, typecheck, file-length, complexity, test, dead-code, security-lint)

**Step 2: Fix any issues found**

Likely: dead code detection may flag old `OpenAIContentAnalyzer` alias. Remove or keep based on backwards compat needs.

**Step 3: Final commit if needed**

```bash
git commit -m "chore: cleanup dead code from LLM provider refactor"
```

---

## Summary of all changes

| Task | What | Files |
|------|------|-------|
| 1 | New flat LLMConfig | config_models.py |
| 2 | Backwards compat migration | config_models.py |
| 3 | Rename OpenAI → OpenAICompatible (content) | _content_providers.py, content_analyzer.py |
| 4 | Update content analyzer factory | content_analyzer.py |
| 5 | Update all config.llm.* call sites | analyzer_factory.py, scoring_factory.py, pipeline_analysis.py, music_cmd.py, config_loader.py |
| 6 | Rename mood analyzer + factory | mood_analyzer_backends.py, mood_analyzer.py, music_cmd.py |
| 7 | Unified preflight check | preflight.py |
| 8 | Fix existing test fixtures | test_unified_analyzer.py |
| 9 | Update config files | config.yaml, .env.example |
| 10 | Full CI + cleanup | any remaining |
