---
sidebar_position: 7
title: LLM Content Analysis
---

# LLM Content Analysis

Optional feature that uses a vision LLM to understand *what's actually happening* in your clips. A birthday party scores differently than a parking lot. Face detection can tell you someone's there; an LLM can tell you they're blowing out candles.

## Any OpenAI-compatible API

This works with anything that speaks the OpenAI chat completions API:

- **[mlx-vlm](https://github.com/Blaizzy/mlx-vlm)**: local on Apple Silicon, no API costs
- **[Ollama](https://ollama.ai)**: local, supports vision models like LLaVA
- **[vLLM](https://vllm.ai)**: self-hosted, great for NVIDIA GPUs
- **[Groq](https://groq.com)**: cloud, fast inference
- **Any other provider** with an OpenAI-compatible endpoint

## How it works

1. For each video segment, 1-4 frames are extracted (configurable)
2. Frames are sent to the vision LLM with a prompt asking for content description and interest rating
3. The LLM response is parsed into a score
4. That score is weighted and added to the overall [interest score](./clip-selection-scoring.md)

## LLM Title Generation

Instead of generic "TWO WEEKS IN SPAIN, SUMMER 2025" template titles, the app feeds your trip's raw GPS data to a local LLM and gets back something like "Sous les falaises de la Saxe" or "Odyssée a travers l'Ombrie et les Marches". It works in any language and classifies your trip pattern too.

### What the LLM gets

After the analysis phase completes, the LLM receives daily GPS clusters: how many photos you took at each location, each day. From that raw data, it figures out the travel pattern (base camp? road trip? hiking trail?) and generates a title + subtitle in your locale. No pre-processing, no clustering algorithm telling it what to think: just the raw photo distribution and the model's own reasoning.

### What it produces

- **Title** and optional **subtitle** in your configured language
- **Trip type**: `base_camp`, `multi_base`, `road_trip`, or `hiking_trail`
- **Map mode** recommendation for the animated map intro
- A one-line **reason** explaining why it picked that classification

You see everything in Step 3 of the UI and can edit before rendering. Hit the regenerate button to try again with the same GPS data.

### Model recommendations

**Qwen3.5-9B-MLX-4bit with thinking disabled** is what you want for titles. 5.5GB, 7-17 seconds per title on Apple Silicon, 100% JSON reliability, and genuinely creative multilingual output.

One catch: you MUST disable thinking mode in the omlx admin panel (`/admin`). Set `chat_template_kwargs` to `{"enable_thinking": false}` for the Qwen3.5 model. With thinking enabled, the model burns 2000-8000 tokens on chain-of-thought before it even starts the JSON, and most requests time out.

| Model | Size | Speed | Quality | Reliability | Notes |
|-------|------|-------|---------|-------------|-------|
| **Qwen3.5-9B (no think)** | 5.5GB | 7-17s | Great | 100% | Best overall |
| Qwen3.5-4B (no think) | 2.9GB | ~10s | Good | ~90% | Lighter alternative |
| Qwen2.5-VL-7B | 4.5GB | 4-5s | OK | T=0.1 only | Vision model doing text: works but generic |
| Qwen3.5-9B (thinking ON) | 5.5GB | 300s+ | Great | ~30% | Don't. Disable thinking. |

## Mood Detection for Music

The music pipeline needs a vision LLM to analyze video keyframes and detect mood. Any server that speaks the OpenAI `/v1/chat/completions` endpoint works: it just needs to handle image inputs.

### LLM Setup

**mlx-vlm (Recommended on Apple Silicon)**:

```bash
uvx --python 3.12 --from mlx-vlm --with torch --with torchvision \
  mlx_vlm.server --port 8080
```

**Ollama**:

```bash
ollama pull llava
ollama serve
```

**Cloud APIs (Groq, OpenAI, etc.)**: any cloud API that supports vision and speaks the OpenAI chat completions format works.

## Configuration

Content analysis needs TWO config sections: `content_analysis` controls the feature itself, and `llm` tells it which model to talk to.

```yaml
# Which LLM to use (shared with title generation)
llm:
  base_url: "http://localhost:8000/v1"
  model: "mlx-community/Qwen2.5-VL-7B-Instruct-8bit"
  api_key: "not-needed"        # for local models
  provider: "openai"           # openai-compatible API
  timeout_seconds: 300

# Content analysis settings
content_analysis:
  enabled: false               # opt-in, off by default
  weight: 0.35                 # how much LLM score influences final ranking
  analyze_frames: 2            # 1-4 frames analyzed per segment
  min_confidence: 0.5          # ignore scores below this threshold
  frame_max_height: 480        # downscale frames before sending (480=fast, 720=balanced)
  openai_image_detail: low     # low=85 tokens/cheap, high=1889 tokens/detailed
```

A separate `title_llm` section can override these for trip title generation (useful if you want a different model for titles vs. content analysis):

```yaml
title_llm:
  base_url: "http://localhost:11434/v1"
  model: "llama3.2"
```

Any field not set in `title_llm` falls back to the `llm` values.

### Weight

The `weight` parameter (0.0 to 1.0) controls how much the LLM score matters relative to the other scoring factors (faces, motion, stability). At 0.35, it's a meaningful input but won't override a clip that scores well on everything else.

### Frames per segment

`analyze_frames` controls how many frames per segment get sent to the LLM. More frames = better understanding but slower and more expensive. 2 is the sweet spot: one near the start, one near the end.

### Frame optimization

`frame_max_height` downscales frames before sending them. At 480px, API costs are low and most vision models still understand the scene. Bump to 720 or 1080 if your model benefits from detail.

`openai_image_detail` maps to the OpenAI `detail` parameter: `low` uses a fixed 85-token budget per image, `high` tiles the image for up to 1889 tokens. For clip scoring, `low` is usually enough.

## Cost considerations

If you're using a cloud provider, every segment analyzed costs API calls. A library with 500 video segments at 2 frames each = 1,000 image API calls. Local models (mlx-vlm, Ollama) have zero marginal cost but are slower.

For large libraries, consider running with LLM analysis disabled first, then enabling it for a curated subset.
