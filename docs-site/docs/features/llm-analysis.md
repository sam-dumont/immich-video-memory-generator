---
sidebar_position: 5
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
4. That score is weighted and added to the overall [interest score](./smart-clip-selection.md)

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
