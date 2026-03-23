---
sidebar_label: "Mac + Local LLM"
---

# Mac + Local LLM Setup

For Mac users who want the full experience: local LLM for smart clip scoring, Apple Silicon hardware acceleration, and native install without Docker.

## Who this is for

You have a Mac with Apple Silicon (M1/M2/M3/M4). You want LLM-powered content analysis running entirely on your machine, no cloud APIs. You're comfortable with the terminal.

## Architecture

```
┌───────────────────────────────────────────────────┐
│ Mac (Apple Silicon)                               │
│                                                   │
│  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  mlx-vlm     │  │   Immich Memories         │  │
│  │  (Qwen2.5-VL)│←─│   (native Python)         │  │
│  │  port 8081   │  │   VideoToolbox encoding   │  │
│  │              │  │   Vision face detection   │  │
│  └──────────────┘  └──────────────────────────┘  │
│                             │                     │
│                    ┌────────┴─────────┐           │
│                    │  Immich server   │           │
│                    │  (local or remote)│           │
│                    └──────────────────┘           │
└───────────────────────────────────────────────────┘
```

![Mac setup diagram](/img/diagrams/setup-mac.png)

## Install

```bash
# Install Immich Memories
uv tool install immich-memories

# Start the UI
immich-memories ui
```

Open [http://localhost:8080](http://localhost:8080).

## Set up mlx-vlm for LLM analysis

[mlx-vlm](https://github.com/Blaizzy/mlx-vlm) runs vision-language models natively on Apple Silicon via MLX. Qwen2.5-VL is the recommended model: fast, accurate, handles video frames well.

```bash
# Install mlx-vlm
pip install mlx-vlm

# Start the server (downloads the model on first run, ~4 GB)
mlx_vlm.server --model mlx-community/Qwen2.5-VL-7B-Instruct-8bit --port 8081
```

Then configure Immich Memories to use it. Add to `~/.immich-memories/config.yaml`:

```yaml
advanced:
  llm:
    provider: openai-compatible
    base_url: http://localhost:8081/v1
    model: mlx-community/Qwen2.5-VL-7B-Instruct-8bit
  content_analysis:
    enabled: true
```

Or set via environment variables:

```bash
export IMMICH_MEMORIES_LLM__BASE_URL=http://localhost:8081/v1
export IMMICH_MEMORIES_LLM__MODEL=mlx-community/Qwen2.5-VL-7B-Instruct-8bit
```

## What works

- **LLM content analysis**: Qwen2.5-VL analyzes video frames and scores clips based on content (birthday cakes, sunsets, kids playing). Adds a content score weighted at 35% in the overall clip ranking.
- **VideoToolbox encoding**: hardware-accelerated H.264/H.265 encoding via Apple's VideoToolbox. 5-10x faster than CPU encoding.
- **Vision framework face detection**: uses macOS native Vision framework for face detection. More accurate than the CPU fallback, no additional model downloads needed.
- **Taichi GPU title renderer**: particle effects, animated globes, and gradient backgrounds rendered on Apple GPU.
- **All memory types and features**: everything works natively on Mac.

## What doesn't work locally

- **AI music generation**: MusicGen needs a GPU server (NVIDIA or a hosted API). ACE-Step can run locally on Mac but requires ~8 GB RAM and takes 5-10 minutes per track. If you have a remote MusicGen/ACE-Step server, configure it in the `musicgen` or `ace_step` config sections.

## Performance expectations

On an M2 Pro (12-core, 32 GB):

| Clips | Resolution | LLM analysis | Total time |
|-------|-----------|-------------|-----------|
| 15 | 1080p | ~3 min | ~5 min |
| 30 | 1080p | ~5 min | ~8 min |
| 30 | 4K | ~5 min | ~14 min |
| 50 | 1080p | ~8 min | ~12 min |

LLM analysis is the slowest phase. The 7B model analyzes 2 frames per clip at ~3 seconds per frame. After the first run, analysis results are cached: subsequent runs for the same clips skip analysis entirely.

Memory usage: ~2 GB for Immich Memories, ~5 GB for mlx-vlm with the 8-bit model. Keep at least 16 GB total RAM.

## Tips

- **Start mlx-vlm before Immich Memories.** If the LLM server isn't running, content analysis silently falls back to metadata-only scoring. You'll still get results, just without the LLM content understanding.
- **The 8-bit quant is the sweet spot.** The 4-bit version is faster but less accurate. The full 16-bit version needs 16 GB+ of unified memory just for the model.
- **Ollama works too.** If you prefer Ollama: `ollama run qwen2.5-vl`, then set `provider: ollama` and `base_url: http://localhost:11434` in config.
- **Use `--analysis-depth thorough`** on the CLI to force LLM analysis even for clips that already have metadata scores. The default `fast` mode only uses the LLM to fill gaps.
