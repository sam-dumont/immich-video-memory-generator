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

```yaml
content_analysis:
  enabled: false               # opt-in, off by default
  api_base: "http://localhost:8000/v1"
  api_key: "not-needed"        # for local models
  model: "mlx-community/Qwen2.5-VL-7B-Instruct-8bit"
  weight: 0.3                  # how much LLM score influences final ranking
  frames_per_segment: 2        # 1-4 frames analyzed per segment
  timeout: 30                  # seconds, per API call
```

### Weight

The `weight` parameter (0.0 to 1.0) controls how much the LLM score matters relative to the other scoring factors (faces, motion, stability). At 0.3, it's a meaningful input but won't override a clip that scores well on everything else.

### Frames per segment

More frames = better understanding but slower and more expensive. For most use cases, 2 frames per segment is the sweet spot: one near the start, one near the end.

## Cost considerations

If you're using a cloud provider, every segment analyzed costs API calls. A library with 500 video segments at 2 frames each = 1,000 image API calls. Local models (mlx-vlm, Ollama) have zero marginal cost but are slower.

For large libraries, consider running with LLM analysis disabled first, then enabling it for a curated subset.
