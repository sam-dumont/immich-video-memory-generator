# LLM Title Generation

Generate context-aware, multilingual titles for your memory videos using a local LLM.

## How It Works

After the analysis phase, the LLM receives:
- **Daily GPS clusters** — photo counts per location per day
- **Clip descriptions** — what the VLM saw in each clip
- **Trip metadata** — dates, duration, country

From this raw data, the LLM detects the travel pattern and generates:
- A **title** and optional **subtitle** in your locale
- **Trip type** classification (base_camp, multi_base, road_trip, hiking_trail)
- **Map mode** recommendation for the animated map intro

## Configuration

The LLM for titles can be configured separately from the vision model:

```yaml
# Vision model (for content analysis)
llm:
  provider: openai-compatible
  base_url: http://localhost:9999/v1
  model: Qwen2.5-VL-7B-Instruct-4bit

# Text model for titles (optional — falls back to llm if not set)
title_llm:
  provider: openai-compatible
  base_url: http://localhost:9999/v1
  model: Qwen3.5-9B-MLX-4bit
```

The locale from `title_screens.locale` is used for title language.

## Tested Models

| Model | Size | Provider | Structured JSON | Title Quality | Notes |
|-------|------|----------|----------------|--------------|-------|
| **Qwen2.5-VL-7B-Instruct-4bit** | ~4.5GB | omlx/mlx-vlm | Reliable at T=0.1 | Good | Vision model doing text — works but formulaic |
| **Qwen3.5-9B-MLX-4bit** | ~5.5GB | omlx/mlx-vlm | Needs thinking parser | Excellent | Thinking model — outputs reasoning chain before JSON |
| **Qwen2.5-7B-Instruct** | ~4.5GB | Ollama/mlx-lm | Reliable | Good | Pure text model, better than VL for this task |
| Ollama models (llama3, etc.) | Varies | Ollama | Varies | Varies | Most instruction-tuned models work |

### Known Issues

- **Qwen2.5-VL** returns `null` content at temperature > 0.1 — the retry logic handles this
- **Qwen3.5** is a thinking model — outputs chain-of-thought before JSON. The parser extracts JSON from the thinking output, but `max_tokens` must be high enough (2000+) to include the JSON after the reasoning
- **Temperature 0.1** is the most reliable for structured JSON output across all models

## Trip Type Detection

The LLM analyzes daily GPS clusters to classify the trip:

| Pattern | Type | Map Mode | Example |
|---------|------|----------|---------|
| Same location every night, varied day locations | `base_camp` | `title_only` | Val d'Aoste: Ville Sur Sarre base + daily hikes |
| 2-3 locations appearing across multiple days | `multi_base` | `excursions` | Cyprus: Nicosia 5 nights → Geroskipou 5 nights |
| Different location each day, large distances | `road_trip` | `overnight_stops` | Italy 2022: 14 days across Umbria → Marche → Emilia |
| Progressive short-distance moves | `hiking_trail` | `overnight_stops` | Saxon Switzerland: daily 5-15km moves |

## Fallback

If the LLM is not configured or fails, the existing template-based title generation is used automatically. You can also edit the title manually in Step 3 of the UI.
