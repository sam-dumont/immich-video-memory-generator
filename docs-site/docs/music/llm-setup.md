---
sidebar_position: 5
title: LLM Setup for Mood Detection
---

# LLM Setup for Mood Detection

The music pipeline needs a vision LLM to analyze video keyframes and detect mood. Any server that speaks the OpenAI `/v1/chat/completions` endpoint works: it just needs to handle image inputs.

## mlx-vlm (Recommended on Apple Silicon)

Fastest option if you're on a Mac with Apple Silicon. Runs entirely on your GPU with no CUDA needed.

```bash
uvx --python 3.12 --from mlx-vlm --with torch --with torchvision \
  mlx_vlm.server --port 8080
```

Recommended model: `mlx-community/Qwen3.5-9B-MLX-4bit` (~6GB unified memory).

Config:

```yaml
llm:
  provider: "openai-compatible"
  base_url: "http://localhost:8080/v1"
  model: "mlx-community/Qwen3.5-9B-MLX-4bit"
```

## Ollama

The most straightforward setup if you already have Ollama installed.

```bash
ollama pull llava
ollama serve
```

Config:

```yaml
llm:
  provider: "ollama"
  base_url: "http://localhost:11434"
  model: "llava"
```

Ollama uses its own native API format (not OpenAI-compatible), so make sure you set `provider: "ollama"`: not `"openai-compatible"`.

## Cloud APIs (Groq, OpenAI, etc.)

Any cloud API that supports vision and speaks the OpenAI chat completions format works.

**OpenAI:**

```yaml
llm:
  provider: "openai-compatible"
  base_url: "https://api.openai.com/v1"
  model: "gpt-4o-mini"
  api_key: "${OPENAI_API_KEY}"
```

**Groq:**

```yaml
llm:
  provider: "openai-compatible"
  base_url: "https://api.groq.com/openai/v1"
  model: "llava-v1.5-7b-4096-preview"
  api_key: "${GROQ_API_KEY}"
```

## What the LLM Actually Does

It gets 4-5 keyframes from your video and returns a JSON blob with:

- Primary mood (happy, calm, energetic, nostalgic, etc.)
- Energy level (low/medium/high)
- Tempo suggestion (slow/medium/fast)
- Genre suggestions (acoustic, cinematic, ambient, etc.)

This gets translated into generation parameters for ACE-Step or MusicGen. The whole analysis takes a few seconds per video: it's not the bottleneck.
