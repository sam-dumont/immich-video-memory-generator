---
sidebar_position: 1
title: Introduction
---

# Immich Memories

Turn your Immich photo library into video memories. Automatically.

You point it at your Immich instance, pick a time period (or a person, or a trip), and it does everything: downloads the videos, finds the best scenes, throws out the duplicates, detects where you traveled, generates a title, adds AI music, and renders a final video. No cloud. No subscription. Your hardware, your data.

![Demo video placeholder](./screenshots/demo-hero.gif)

## What comes out

A polished memory video with:

- **Smart cuts** from your best clips, scored by visual interest, motion, faces, and audio
- **Animated satellite map** flying from home to your destination (for trip memories)
- **AI-generated title** that actually describes the trip: "Sous les falaises de la Saxe" instead of "TWO WEEKS IN GERMANY"
- **AI music** that matches the mood of your clips (ACE-Step or MusicGen)
- **Smooth transitions** with crossfades timed to the music

![Trip memory example](./screenshots/trip-memory-example.png)

## What it connects to

Your self-hosted [Immich](https://immich.app) server. That's it. No Google, no Apple, no cloud APIs. Everything runs locally:

- Video analysis on your GPU (NVIDIA, Apple Silicon, Intel, AMD)
- LLM analysis via Ollama or any OpenAI-compatible server (mlx-vlm, vLLM, Groq)
- Music generation via ACE-Step or MusicGen (local or API)

## Memory types

| Type | What it does | Example |
|------|-------------|---------|
| **Year in review** | Best moments from a full year | "2024 : Une année de souvenirs" |
| **Monthly highlights** | Best of a specific month | "Août 2024" |
| **Person spotlight** | Clips featuring a specific person | "Alice & Emile Through the Years" |
| **Trip memory** | GPS-detected trip with map animation | "Vallée d'Aoste, juillet 2021" |
| **Season** | 3-month seasonal highlights | "Summer 2024" |
| **On This Day** | Anniversary compilation | "This Day, 3 Years Ago" |

![Memory type selection](./screenshots/memory-presets.png)

## Trip detection

For trip memories, the system detects where you traveled and what pattern your trip followed:

- **Base camp**: same hotel every night, day trips around (Val d'Aoste)
- **Multi-base**: 2-3 bases with travel between them (Cyprus: Nicosia → Geroskipou)
- **Road trip**: different town each day (Italy 2022: 14 days across Umbria)
- **Hiking trail**: progressive short-distance moves (Saxon Switzerland: Malerweg trail)

The LLM gets your raw daily GPS clusters and figures out the pattern. No pre-processing, no hardcoded rules: just the photo distribution and the model's reasoning.

![Trip overnight detection](./screenshots/trip-detection.png)

## The 4-step wizard

1. **Configure**: pick your memory type, time period, and person
2. **Analyze**: the pipeline downloads clips, scores them, detects duplicates, selects the best
3. **Preview**: see the LLM-generated title, choose your music, adjust settings
4. **Generate**: render the final video with map animation, title screens, and music

![Step 3 preview](./screenshots/step3-preview.png)

## Quality

1200+ tests. 14 pre-commit hooks. Strict type checking. Zero dead code. Zero refurb violations. Cognitive complexity gated. Architectural boundaries enforced. Dependency hygiene checked. Docstring coverage above 80%.

This project was built entirely with AI assistance (Claude). The code quality gates exist because AI-generated code needs tighter guardrails, not fewer. See the [blog post](https://dropbars.be/blog/immich-memories) for the full story.

## Get started

Ready? Head to the [Quick Start](./quick-start.md).
