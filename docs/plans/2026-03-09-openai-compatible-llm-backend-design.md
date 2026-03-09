# Design: Unified OpenAI-Compatible LLM Backend

**Date**: 2026-03-09
**Status**: Approved

## Problem

The codebase has separate Ollama and OpenAI providers with distinct config fields.
Adding MLX (mlx-vlm) support means yet another provider — but mlx-vlm, vLLM,
LM Studio, llama.cpp server, Groq, and OpenAI all speak the same
`/v1/chat/completions` protocol. Only Ollama is the odd one out with `/api/generate`.

## Decision

Two providers: `"ollama"` and `"openai-compatible"`. Everything that speaks the
OpenAI chat completions API is one provider. No `"auto"` mode — user picks explicitly.

## Config Model

Flat fields replace the current prefixed `ollama_*`/`openai_*` fields:

```yaml
llm:
  provider: openai-compatible  # "ollama" or "openai-compatible"
  base_url: http://localhost:8080/v1
  model: qwen3.5-9b
  api_key: ""  # optional, only needed for cloud APIs
```

- `provider` defaults to `"openai-compatible"`
- `base_url` defaults to `http://localhost:8080/v1` (mlx-vlm default)
- `model` defaults to `""` (preflight catches empty model)
- `api_key` defaults to `""` (empty = not required for local servers)
- Ollama users set `provider: ollama`, `base_url: http://localhost:11434`

### Backwards Compatibility

Config loading detects old fields (`ollama_url`, `openai_base_url`, etc.) and
maps them to the new flat structure with a deprecation log warning.

## Provider Architecture

### ContentAnalyzer hierarchy (unchanged base class)

```
ContentAnalyzer (abstract, from _content_parsing.py)
├── OllamaContentAnalyzer        — /api/generate, images[] array
└── OpenAICompatibleContentAnalyzer  — /v1/chat/completions, data URLs
```

### MoodAnalyzer hierarchy (same pattern)

```
MoodAnalyzer
├── OllamaMoodAnalyzer
└── OpenAICompatibleMoodAnalyzer
```

### Factory function

```python
def get_content_analyzer(provider, base_url, model, api_key, ...) -> ContentAnalyzer | None:
    if provider == "ollama":
        return OllamaContentAnalyzer(base_url, model)
    if provider == "openai-compatible":
        return OpenAICompatibleContentAnalyzer(base_url, model, api_key)
    return None
```

No auto-fallback. If the provider isn't reachable, return `None`.

### Timeouts

- Ollama: 300s (unchanged)
- OpenAI-compatible: 120s (up from 60s — local vision models are slower than cloud)

## Preflight

Replace separate `check_ollama`/`check_openai` with unified `check_llm`:

- **Ollama**: `GET /api/tags` + verify model exists (unchanged)
- **OpenAI-compatible**: Send minimal test completion to `/v1/chat/completions`
  with `max_tokens: 1`. Validates server reachable + model valid + auth works.

## Files to Modify

| File | Change |
|---|---|
| `config_models.py` | Replace `LLMConfig` with flat fields |
| `_content_providers.py` | Rename `OpenAIContentAnalyzer` -> `OpenAICompatibleContentAnalyzer`, flat config, 120s timeout |
| `content_analyzer.py` | Update factory — remove auto-fallback, flat fields |
| `analyzer_factory.py` | Pass flat fields to factory |
| `audio/mood_analyzer_backends.py` | Same rename + flat fields for mood analyzers |
| `audio/mood_analyzer.py` | Update mood analyzer factory |
| `preflight.py` | Unified `check_llm` with test completion for openai-compatible |
| `ui/pages/step2_review.py` | Update LLM config references |
| `docker/.env.example` | Update env var names |
| `~/.immich-memories/config.yaml` | Migrate local config |
| Tests | Update mocks/fixtures, TDD for new behavior |

## What Stays the Same

- `_content_parsing.py` — shared prompt + JSON parsing (untouched)
- Scoring logic in `segment_scoring.py`
- Frame extraction in base `ContentAnalyzer`
- Token tracking

## Development Approach

TDD with vertical slices per `.agents/skills/tdd/SKILL.md`:
ONE test -> ONE implementation -> repeat. No bulk test writing.
