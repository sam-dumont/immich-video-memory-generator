# LLM Title Generation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded template titles with LLM-generated titles that adapt to memory type, trip pattern, locale, and clip content.

**Architecture:** A generic `query_llm()` utility sends text prompts to the existing LLM provider. A `generate_title_with_llm()` function builds context from clips, metadata, and overnight stops, then asks the LLM to produce a title, subtitle, trip classification, and map mode recommendation. Results are shown in the UI for user approval before rendering. Template-based titles remain as fallback.

**Tech Stack:** Python 3.13, httpx (async HTTP), existing LLMConfig/Ollama/OpenAI-compatible providers, NiceGUI (UI), pytest (TDD)

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/immich_memories/analysis/llm_query.py` | Generic async text-only LLM query utility |
| Create | `src/immich_memories/titles/llm_titles.py` | Title generation prompt, context building, response parsing |
| Create | `tests/test_llm_query.py` | Tests for LLM query utility |
| Create | `tests/test_llm_titles.py` | Tests for title generation + parsing |
| Modify | `src/immich_memories/api/models.py` | Add `SmartInfo` dataclass, add `smart_info` field to `Asset` |
| Modify | `src/immich_memories/api/client_search.py` | Request smartInfo in search payload |
| Modify | `src/immich_memories/ui/state.py` | Add `title_suggestion` field to `AppState` |
| Modify | `src/immich_memories/processing/assembly_config.py` | Add `title_override`, `subtitle_override` to `TitleScreenSettings` |
| Modify | `src/immich_memories/titles/generator.py` | Use title override when set |
| Modify | `src/immich_memories/ui/pages/_step3_music_preview.py` | Show LLM title, edit fields, locale dropdown, regenerate |
| Modify | `src/immich_memories/ui/pages/_step4_generate.py` | Pass approved title to TitleScreenSettings |

---

## Chunk 1: Core LLM Query Utility + SmartInfo

### Task 1: SmartInfo in Asset Model

**Files:**
- Modify: `src/immich_memories/api/models.py`
- Modify: `src/immich_memories/api/client_search.py`
- Test: `tests/test_api_models.py` (or inline verification)

- [ ] **Step 1: Add SmartInfo dataclass and field to Asset**

In `src/immich_memories/api/models.py`, add before the `Asset` class:

```python
class SmartInfo(BaseModel):
    """Immich object detection results."""
    objects: list[str] | None = None
```

Add to `Asset` class fields:

```python
    smart_info: SmartInfo | None = None
```

- [ ] **Step 2: Verify Asset parses smartInfo from API responses**

Run: `make test`
Expected: All existing tests pass (SmartInfo is optional, defaults to None)

- [ ] **Step 3: Commit**

```bash
git add src/immich_memories/api/models.py
git commit -m "feat(api): add SmartInfo to Asset model for object detection labels"
```

---

### Task 2: Generic LLM Text Query Utility

**Files:**
- Create: `src/immich_memories/analysis/llm_query.py`
- Create: `tests/test_llm_query.py`

- [ ] **Step 1: Write failing test for Ollama text query**

```python
# tests/test_llm_query.py
"""Tests for generic LLM text query utility."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from immich_memories.config_models import LLMConfig


class TestQueryLlmOllama:
    """Ollama provider: text-only query."""

    @pytest.mark.asyncio
    async def test_sends_text_prompt_to_ollama(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="ollama", base_url="http://localhost:11434", model="llama3")
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": '{"title": "Summer 2024"}'}

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await query_llm("Generate a title", config)

        assert result == '{"title": "Summer 2024"}'
        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["prompt"] == "Generate a title"
        assert call_payload["model"] == "llama3"
        assert "images" not in call_payload  # text-only, no images
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test -- tests/test_llm_query.py::TestQueryLlmOllama::test_sends_text_prompt_to_ollama -v`
Expected: FAIL with "No module named 'immich_memories.analysis.llm_query'"

- [ ] **Step 3: Write minimal implementation**

```python
# src/immich_memories/analysis/llm_query.py
"""Generic text-only LLM query utility.

Sends a text prompt to the configured LLM provider (Ollama or OpenAI-compatible)
and returns the raw response string. Caller handles JSON parsing and validation.
"""

from __future__ import annotations

import logging

import httpx

from immich_memories.config_models import LLMConfig

logger = logging.getLogger(__name__)


async def query_llm(
    prompt: str,
    llm_config: LLMConfig,
    temperature: float = 0.3,
    max_tokens: int = 500,
    timeout_seconds: int = 30,
) -> str:
    """Send a text-only prompt to the configured LLM and return the response.

    Supports Ollama and OpenAI-compatible providers. No images, no vision.
    Raises on HTTP errors or empty responses.
    """
    if llm_config.provider == "ollama":
        return await _query_ollama(prompt, llm_config, temperature, timeout_seconds)
    return await _query_openai(prompt, llm_config, temperature, max_tokens, timeout_seconds)


async def _query_ollama(
    prompt: str, config: LLMConfig, temperature: float, timeout: int,
) -> str:
    base_url = config.base_url.rstrip("/")
    payload = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json()["response"]


async def _query_openai(
    prompt: str, config: LLMConfig, temperature: float, max_tokens: int, timeout: int,
) -> str:
    base_url = config.base_url.rstrip("/")
    headers = {}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.post(f"{base_url}/chat/completions", json=payload)
        resp.raise_for_status()
        choices = resp.json()["choices"]
        return choices[0]["message"]["content"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test -- tests/test_llm_query.py::TestQueryLlmOllama -v`
Expected: PASS

- [ ] **Step 5: Write failing test for OpenAI-compatible provider**

```python
class TestQueryLlmOpenAI:
    """OpenAI-compatible provider: text-only query."""

    @pytest.mark.asyncio
    async def test_sends_text_prompt_to_openai(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="openai-compatible", base_url="http://localhost:8080/v1", model="omlx", api_key="sk-test")
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"title": "Cycling 2024"}'}}],
        }

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await query_llm("Generate a title", config)

        assert result == '{"title": "Cycling 2024"}'
        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["messages"][0]["content"] == "Generate a title"
        assert call_payload["model"] == "omlx"

    @pytest.mark.asyncio
    async def test_includes_api_key_header(self):
        from immich_memories.analysis.llm_query import query_llm

        config = LLMConfig(provider="openai-compatible", base_url="http://localhost:8080/v1", model="omlx", api_key="sk-secret")
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
        }

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            await query_llm("test", config)
```

- [ ] **Step 6: Run tests — should pass without code changes**

Run: `make test -- tests/test_llm_query.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/immich_memories/analysis/llm_query.py tests/test_llm_query.py
git commit -m "feat(llm): add generic text-only LLM query utility"
```

---

## Chunk 2: Title Generation with LLM

### Task 3: TitleSuggestion Dataclass + Response Parsing

**Files:**
- Create: `src/immich_memories/titles/llm_titles.py`
- Create: `tests/test_llm_titles.py`

- [ ] **Step 1: Write failing test for JSON response parsing**

```python
# tests/test_llm_titles.py
"""Tests for LLM title generation and response parsing."""

from __future__ import annotations

import pytest


class TestParseTitleResponse:
    """Parse LLM JSON response into TitleSuggestion."""

    def test_parses_valid_json(self):
        from immich_memories.titles.llm_titles import TitleSuggestion, parse_title_response

        raw = '{"title": "A Week in Bretagne", "subtitle": "From Brasparts to Frehel", "trip_type": "multi_base", "map_mode": "excursions", "map_mode_reason": "Two bases"}'
        result = parse_title_response(raw)
        assert isinstance(result, TitleSuggestion)
        assert result.title == "A Week in Bretagne"
        assert result.subtitle == "From Brasparts to Frehel"
        assert result.trip_type == "multi_base"
        assert result.map_mode == "excursions"

    def test_strips_markdown_code_block(self):
        from immich_memories.titles.llm_titles import parse_title_response

        raw = '```json\n{"title": "Summer 2024", "subtitle": null, "trip_type": null, "map_mode": null, "map_mode_reason": null}\n```'
        result = parse_title_response(raw)
        assert result is not None
        assert result.title == "Summer 2024"

    def test_rejects_invalid_trip_type(self):
        from immich_memories.titles.llm_titles import parse_title_response

        raw = '{"title": "Trip", "subtitle": null, "trip_type": "invalid_type", "map_mode": null, "map_mode_reason": null}'
        result = parse_title_response(raw)
        assert result is not None
        assert result.trip_type is None  # invalid value rejected

    def test_truncates_long_title(self):
        from immich_memories.titles.llm_titles import parse_title_response

        raw = '{"title": "' + "A" * 200 + '", "subtitle": null, "trip_type": null, "map_mode": null, "map_mode_reason": null}'
        result = parse_title_response(raw)
        assert result is not None
        assert len(result.title) <= 80

    def test_returns_none_on_malformed_json(self):
        from immich_memories.titles.llm_titles import parse_title_response

        assert parse_title_response("not json at all") is None
        assert parse_title_response("") is None

    def test_returns_none_on_missing_title(self):
        from immich_memories.titles.llm_titles import parse_title_response

        raw = '{"subtitle": "no title field"}'
        assert parse_title_response(raw) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test -- tests/test_llm_titles.py::TestParseTitleResponse -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement TitleSuggestion + parse_title_response**

```python
# src/immich_memories/titles/llm_titles.py
"""LLM-powered title generation for memory videos.

Builds context from clips, metadata, and overnight stops, sends to LLM,
parses structured response. Falls back to template titles on failure.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

TripType = Literal["multi_base", "base_camp", "road_trip", "hiking_trail"]
MapMode = Literal["title_only", "excursions", "overnight_stops"]

_VALID_TRIP_TYPES: set[str] = {"multi_base", "base_camp", "road_trip", "hiking_trail"}
_VALID_MAP_MODES: set[str] = {"title_only", "excursions", "overnight_stops"}

_MAX_TITLE_LEN = 80
_MAX_SUBTITLE_LEN = 120


@dataclass
class TitleSuggestion:
    """LLM-generated title and trip classification."""

    title: str
    subtitle: str | None = None
    trip_type: TripType | None = None
    map_mode: MapMode | None = None
    map_mode_reason: str | None = None


def parse_title_response(raw: str) -> TitleSuggestion | None:
    """Parse LLM JSON response into TitleSuggestion.

    Strips markdown code blocks, validates fields, sanitizes strings.
    Returns None on parse failure.
    """
    if not raw or not raw.strip():
        return None

    # Strip markdown code block wrappers
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM title response is not valid JSON: %.100s", raw)
        return None

    if not isinstance(data, dict) or "title" not in data:
        logger.warning("LLM title response missing 'title' key")
        return None

    title = _sanitize(str(data["title"]), _MAX_TITLE_LEN)
    if not title:
        return None

    subtitle = _sanitize(str(data["subtitle"]), _MAX_SUBTITLE_LEN) if data.get("subtitle") else None
    trip_type = data.get("trip_type") if data.get("trip_type") in _VALID_TRIP_TYPES else None
    map_mode = data.get("map_mode") if data.get("map_mode") in _VALID_MAP_MODES else None
    map_mode_reason = str(data["map_mode_reason"])[:200] if data.get("map_mode_reason") else None

    return TitleSuggestion(
        title=title,
        subtitle=subtitle,
        trip_type=trip_type,  # type: ignore[arg-type]
        map_mode=map_mode,  # type: ignore[arg-type]
        map_mode_reason=map_mode_reason,
    )


def _sanitize(text: str, max_len: int) -> str:
    """Remove control characters and cap length."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", text).strip()
    return cleaned[:max_len]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test -- tests/test_llm_titles.py::TestParseTitleResponse -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/immich_memories/titles/llm_titles.py tests/test_llm_titles.py
git commit -m "feat(titles): add TitleSuggestion dataclass and LLM response parser"
```

---

### Task 4: Prompt Building + Title Generation Function

**Files:**
- Modify: `src/immich_memories/titles/llm_titles.py`
- Modify: `tests/test_llm_titles.py`

- [ ] **Step 1: Write failing test for prompt building**

```python
class TestBuildTitlePrompt:
    """Build context-rich prompt for the LLM."""

    def test_trip_prompt_includes_bases_and_descriptions(self):
        from immich_memories.titles.llm_titles import build_title_prompt

        prompt = build_title_prompt(
            memory_type="trip",
            locale="en",
            start_date="2023-09-23",
            end_date="2023-09-29",
            duration_days=7,
            locations=["Brasparts (4 nights)", "Frehel (3 nights)"],
            country="France",
            clip_descriptions=["hiking along cliffs", "sunset over bay"],
            smart_objects=["person", "beach"],
            overnight_summary="2 home bases, no excursions",
        )
        assert "trip" in prompt.lower()
        assert "Brasparts" in prompt
        assert "French" not in prompt  # locale=en → English
        assert "hiking along cliffs" in prompt

    def test_person_prompt_includes_names(self):
        from immich_memories.titles.llm_titles import build_title_prompt

        prompt = build_title_prompt(
            memory_type="person",
            locale="fr",
            start_date="2019-01-01",
            end_date="2025-12-31",
            duration_days=2556,
            person_names=["Alice", "Emile"],
            clip_descriptions=["playing in park", "birthday party"],
        )
        assert "Alice" in prompt
        assert "Emile" in prompt
        assert "French" in prompt  # locale=fr

    def test_includes_few_shot_examples(self):
        from immich_memories.titles.llm_titles import build_title_prompt

        prompt = build_title_prompt(
            memory_type="year",
            locale="en",
            start_date="2024-01-01",
            end_date="2024-12-31",
            duration_days=366,
        )
        assert "Example" in prompt or "example" in prompt
```

- [ ] **Step 2: Run to verify failure**

Run: `make test -- tests/test_llm_titles.py::TestBuildTitlePrompt -v`
Expected: FAIL — `build_title_prompt` not found

- [ ] **Step 3: Implement build_title_prompt**

Add to `src/immich_memories/titles/llm_titles.py`:

```python
_LOCALE_NAMES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "nl": "Dutch", "pt": "Portuguese", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "ru": "Russian", "pl": "Polish",
    "sv": "Swedish", "da": "Danish", "nb": "Norwegian", "fi": "Finnish",
}


def build_title_prompt(
    memory_type: str,
    locale: str,
    start_date: str,
    end_date: str,
    duration_days: int,
    *,
    locations: list[str] | None = None,
    country: str | None = None,
    person_names: list[str] | None = None,
    clip_descriptions: list[str] | None = None,
    smart_objects: list[str] | None = None,
    overnight_summary: str | None = None,
) -> str:
    """Build a context-rich prompt for title generation."""
    lang = _LOCALE_NAMES.get(locale, locale.capitalize())

    lines = [
        "You are generating a title for a personal memory video.",
        f"Language: {lang}",
        "",
        f"Memory type: {memory_type}",
        f"Dates: {start_date} to {end_date} ({duration_days} days)",
    ]

    if locations:
        lines.append(f"Locations: {', '.join(locations)}")
    if country:
        lines.append(f"Country: {country}")
    if person_names:
        lines.append(f"People: {', '.join(person_names)}")
    if clip_descriptions:
        lines.append(f"Clip descriptions: {', '.join(clip_descriptions[:10])}")
    if smart_objects:
        lines.append(f"Objects detected: {', '.join(smart_objects[:20])}")
    if overnight_summary:
        lines.append(f"Overnight pattern: {overnight_summary}")

    lines.extend([
        "",
        "Return ONLY valid JSON with these keys:",
        '- title: short, evocative title (max 60 chars)',
        '- subtitle: optional second line (max 80 chars), null if not needed',
        '- trip_type: one of "multi_base", "base_camp", "road_trip", "hiking_trail", or null for non-trips',
        '- map_mode: one of "title_only", "excursions", "overnight_stops", or null for non-trips',
        '- map_mode_reason: one sentence explaining why (or null)',
        "",
        "Example for a trip:",
        '{"title": "A Week in Bretagne", "subtitle": "From Brasparts to the Emerald Coast", "trip_type": "multi_base", "map_mode": "excursions", "map_mode_reason": "Two bases with day trips from each"}',
        "",
        "Example for a person memory:",
        '{"title": "Alice & Emile Through the Years", "subtitle": "2019 - 2025", "trip_type": null, "map_mode": null, "map_mode_reason": null}',
    ])

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `make test -- tests/test_llm_titles.py -v`
Expected: All PASS

- [ ] **Step 5: Write failing test for generate_title_with_llm**

```python
class TestGenerateTitleWithLlm:
    """End-to-end: build prompt, query LLM, parse response."""

    @pytest.mark.asyncio
    async def test_returns_title_suggestion_on_success(self):
        from unittest.mock import AsyncMock, patch

        from immich_memories.config_models import LLMConfig
        from immich_memories.titles.llm_titles import TitleSuggestion, generate_title_with_llm

        config = LLMConfig(provider="openai-compatible", base_url="http://localhost:8080/v1", model="omlx")
        llm_response = '{"title": "Summer in Crete", "subtitle": "Chania to Sitia", "trip_type": "multi_base", "map_mode": "excursions", "map_mode_reason": "Two bases"}'

        with patch("immich_memories.titles.llm_titles.query_llm", new_callable=AsyncMock, return_value=llm_response):
            result = await generate_title_with_llm(
                memory_type="trip", locale="en",
                start_date="2019-07-04", end_date="2019-07-14",
                duration_days=11,
                locations=["Platanos (4 nights)", "Sitia (4 nights)"],
                country="Greece",
            )

        assert isinstance(result, TitleSuggestion)
        assert result.title == "Summer in Crete"
        assert result.map_mode == "excursions"

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_failure(self):
        from unittest.mock import AsyncMock, patch

        import httpx

        from immich_memories.config_models import LLMConfig
        from immich_memories.titles.llm_titles import generate_title_with_llm

        config = LLMConfig(provider="openai-compatible", base_url="http://localhost:8080/v1", model="omlx")

        with patch("immich_memories.titles.llm_titles.query_llm", new_callable=AsyncMock, side_effect=httpx.HTTPError("timeout")):
            result = await generate_title_with_llm(
                memory_type="year", locale="en",
                start_date="2024-01-01", end_date="2024-12-31",
                duration_days=366, llm_config=config,
            )

        assert result is None
```

- [ ] **Step 6: Implement generate_title_with_llm**

Add to `src/immich_memories/titles/llm_titles.py`:

```python
from immich_memories.analysis.llm_query import query_llm as _query_llm
from immich_memories.config_models import LLMConfig


async def generate_title_with_llm(
    memory_type: str,
    locale: str,
    start_date: str,
    end_date: str,
    duration_days: int,
    *,
    locations: list[str] | None = None,
    country: str | None = None,
    person_names: list[str] | None = None,
    clip_descriptions: list[str] | None = None,
    smart_objects: list[str] | None = None,
    overnight_summary: str | None = None,
    llm_config: LLMConfig | None = None,
    temperature: float = 0.3,
) -> TitleSuggestion | None:
    """Generate a title using the LLM. Returns None on failure."""
    if llm_config is None:
        return None

    prompt = build_title_prompt(
        memory_type=memory_type, locale=locale,
        start_date=start_date, end_date=end_date,
        duration_days=duration_days, locations=locations,
        country=country, person_names=person_names,
        clip_descriptions=clip_descriptions, smart_objects=smart_objects,
        overnight_summary=overnight_summary,
    )

    try:
        raw = await _query_llm(prompt, llm_config, temperature=temperature)
        return parse_title_response(raw)
    except Exception:
        logger.warning("LLM title generation failed", exc_info=True)
        return None
```

- [ ] **Step 7: Run all title tests**

Run: `make test -- tests/test_llm_titles.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/immich_memories/titles/llm_titles.py tests/test_llm_titles.py
git commit -m "feat(titles): LLM title generation with prompt building and fallback"
```

---

## Chunk 3: Pipeline + UI Integration

### Task 5: Title Override in TitleScreenSettings + Generator

**Files:**
- Modify: `src/immich_memories/processing/assembly_config.py`
- Modify: `src/immich_memories/titles/generator.py`

- [ ] **Step 1: Add title_override fields to TitleScreenSettings**

In `src/immich_memories/processing/assembly_config.py`, add to `TitleScreenSettings`:

```python
    # LLM-generated title override (bypasses template generation)
    title_override: str | None = None
    subtitle_override: str | None = None
```

- [ ] **Step 2: Use override in TitleScreenGenerator**

In `src/immich_memories/titles/generator.py`, in `generate_title_screen()`, before the `generate_title()` call, add:

```python
        # Use LLM-generated title if available
        if self.config.title_override:
            title_info = TitleInfo(
                main_title=self.config.title_override,
                subtitle=self.config.subtitle_override or "",
                selection_type=selection_type or SelectionType.YEARLY,
            )
        else:
            title_info = generate_title(
                # ... existing code
            )
```

- [ ] **Step 3: Run existing tests**

Run: `make test -- tests/test_trip_maps.py tests/test_title_hdr.py -v`
Expected: All PASS (override is None by default, no behavior change)

- [ ] **Step 4: Commit**

```bash
git add src/immich_memories/processing/assembly_config.py src/immich_memories/titles/generator.py
git commit -m "feat(titles): add title_override to bypass template generation"
```

---

### Task 6: AppState + Step 3 UI

**Files:**
- Modify: `src/immich_memories/ui/state.py`
- Modify: `src/immich_memories/ui/pages/_step3_music_preview.py`

- [ ] **Step 1: Add title_suggestion to AppState**

In `src/immich_memories/ui/state.py`, add import and field:

```python
    # LLM-generated title (shown in Step 3, used in Step 4)
    title_suggestion_title: str | None = None
    title_suggestion_subtitle: str | None = None
    title_suggestion_trip_type: str | None = None
    title_suggestion_map_mode: str | None = None
```

(Using flat fields instead of the dataclass to avoid import cycles with the titles module)

- [ ] **Step 2: Add title editing section to Step 3 UI**

In `src/immich_memories/ui/pages/_step3_music_preview.py`, add a title section below the music controls. This should show:
- Title text field (bound to `state.title_suggestion_title`)
- Subtitle text field (bound to `state.title_suggestion_subtitle`)
- Locale dropdown (bound to config, triggers regeneration)
- "Regenerate" button (calls `generate_title_with_llm` with temperature=0.7)
- Trip type + map mode shown as read-only chips if set

Implementation details depend on the existing Step 3 layout — read the file first and follow the NiceGUI patterns already in use.

- [ ] **Step 3: Run existing tests**

Run: `make test`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/immich_memories/ui/state.py src/immich_memories/ui/pages/_step3_music_preview.py
git commit -m "feat(ui): show LLM title in Step 3 with edit + regenerate"
```

---

### Task 7: Wire Title into Step 4 Assembly

**Files:**
- Modify: `src/immich_memories/ui/pages/_step4_generate.py`

- [ ] **Step 1: Pass title override to TitleScreenSettings**

In `_step4_generate.py`, in `_build_assembly_settings()`, after building `title_screen_settings`, add:

```python
        # Use LLM-generated title if available
        if state.title_suggestion_title:
            title_screen_settings.title_override = state.title_suggestion_title
            title_screen_settings.subtitle_override = state.title_suggestion_subtitle
```

- [ ] **Step 2: Pass map_mode to trip settings**

If `state.title_suggestion_map_mode` is set, use it to configure the map animation behavior:

```python
        if state.title_suggestion_map_mode:
            title_screen_settings.map_mode = state.title_suggestion_map_mode
```

(This may require adding `map_mode: str | None = None` to `TitleScreenSettings` if not already present)

- [ ] **Step 3: Run all tests**

Run: `make test`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/immich_memories/ui/pages/_step4_generate.py
git commit -m "feat(ui): wire LLM title + map mode into assembly"
```

---

### Task 8: Trigger Title Generation After Analysis

**Files:**
- Modify: `src/immich_memories/ui/pages/_step3_music_preview.py` (or wherever Step 2→3 transition happens)

- [ ] **Step 1: Call generate_title_with_llm at Step 2→3 transition**

After `SmartPipeline.run()` completes and clips are available, call the title generator with all available context:
- Memory type from `state.memory_type`
- Dates from `state.memory_preset_params`
- Clip descriptions from analysis cache
- Person names from state
- Overnight stops from `detect_overnight_stops()` (if trip)
- SmartInfo objects aggregated from assets

Store result in `state.title_suggestion_*` fields.

- [ ] **Step 2: Handle fallback gracefully**

If `generate_title_with_llm()` returns `None`, leave `state.title_suggestion_title` as `None`. The UI shows empty title fields that the user can fill manually, and Step 4 falls back to template generation.

- [ ] **Step 3: Run full test suite**

Run: `make test`
Expected: All pass

- [ ] **Step 4: Run `make ci`**

Run: `make ci`
Expected: All checks pass (lint, format, typecheck, file-length, complexity, tests)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(titles): trigger LLM title generation after analysis phase"
```

---

## Chunk 4: Clip Distribution by Trip Segment

### Task 9: Tag Clips to Trip Segments

**Files:**
- Modify: `src/immich_memories/analysis/trip_detection.py` (add helper)
- Create or modify test file

- [ ] **Step 1: Write failing test for segment tagging**

```python
def test_tag_clips_to_segments():
    """Each clip gets assigned to the overnight base covering its date."""
    from immich_memories.analysis.trip_detection import OvernightBase, tag_clips_to_segments

    bases = [
        OvernightBase(start_date=date(2023, 9, 23), end_date=date(2023, 9, 26), nights=4,
                      lat=48.3, lon=-4.0, location_name="Brasparts", asset_ids=[]),
        OvernightBase(start_date=date(2023, 9, 27), end_date=date(2023, 9, 29), nights=3,
                      lat=48.7, lon=-2.3, location_name="Frehel", asset_ids=[]),
    ]
    clip_dates = {
        "clip1": date(2023, 9, 23),
        "clip2": date(2023, 9, 25),
        "clip3": date(2023, 9, 27),
        "clip4": date(2023, 9, 29),
    }
    result = tag_clips_to_segments(clip_dates, bases)
    assert result["clip1"] == 0  # Brasparts segment
    assert result["clip2"] == 0
    assert result["clip3"] == 1  # Frehel segment
    assert result["clip4"] == 1
```

- [ ] **Step 2: Implement tag_clips_to_segments**

```python
def tag_clips_to_segments(
    clip_dates: dict[str, date],
    bases: list[OvernightBase],
) -> dict[str, int]:
    """Map each clip ID to its segment index based on date."""
    result: dict[str, int] = {}
    for clip_id, clip_date in clip_dates.items():
        for i, base in enumerate(bases):
            if base.start_date <= clip_date <= base.end_date:
                result[clip_id] = i
                break
        else:
            # Clip outside any segment — assign to nearest
            if bases:
                closest = min(range(len(bases)), key=lambda i: min(
                    abs((clip_date - bases[i].start_date).days),
                    abs((clip_date - bases[i].end_date).days),
                ))
                result[clip_id] = closest
    return result
```

- [ ] **Step 3: Run test**

Run: `make test -- tests/test_trip_detection.py::test_tag_clips_to_segments -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/immich_memories/analysis/trip_detection.py tests/test_trip_detection.py
git commit -m "feat(trips): tag clips to overnight segments for proportional distribution"
```

---

### Task 10: Proportional Clip Budget per Segment

This task modifies the SmartPipeline refinement phase to enforce minimum coverage per segment. The exact integration point depends on the current refinement code — read `smart_pipeline.py` and `segment_scoring.py` before implementing.

**Rule:**
- Each segment gets at least 1 clip
- Remaining budget distributed proportionally by night count
- Within each segment, clips ranked by existing quality score

- [ ] **Step 1: Write failing test for budget distribution**

Test that given 10 clip slots and segments [4 nights, 3 nights, 1 night], the budget splits roughly [5, 4, 1] with each segment having at least 1.

- [ ] **Step 2: Implement budget distribution function**

- [ ] **Step 3: Wire into SmartPipeline refinement**

- [ ] **Step 4: Run all tests**

Run: `make ci`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(pipeline): proportional clip distribution across trip segments"
```

---

## Final Checklist

- [ ] Run `make ci` — all checks pass
- [ ] Test manually: create a trip memory, verify LLM title appears in Step 3
- [ ] Test manually: edit title, regenerate, verify locale switch works
- [ ] Test manually: generate video, verify title override renders correctly
- [ ] Test fallback: disable LLM, verify template titles still work
