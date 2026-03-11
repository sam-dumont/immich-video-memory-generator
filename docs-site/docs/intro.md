---
sidebar_position: 1
title: Introduction
---

# Immich Memories

Immich Memories connects to your self-hosted [Immich](https://immich.app) server, picks out the best moments from your videos, and compiles them into shareable memory videos — think Google Photos memories, but running on your own hardware with no cloud dependency.

You point it at your Immich instance, tell it a time period (or a person), and it does the rest: downloads the videos, analyzes them for interesting scenes, throws out the duplicates, picks the highlights, adds music, and renders a final video.

## What It Does

- **Immich integration** — pulls videos directly from your Immich API. No manual exports, no file copying.
- **Smart person selection** — uses Immich's face recognition to build memories around specific people.
- **Flexible time periods** — last week, last month, "summer 2024", or any custom date range.
- **Duplicate detection** — perceptual hashing catches near-identical clips so your final video isn't the same sunset 8 times.
- **Scene detection** — finds shot boundaries automatically. No manual trimming.
- **Intelligent moment selection** — ranks scenes by visual interest so the best stuff makes the cut.
- **LLM content analysis** — optional Ollama/OpenAI integration for mood detection and scene understanding.
- **Hardware acceleration** — NVIDIA (NVENC), Apple (VideoToolbox), Intel (QSV), and AMD (AMF). Falls back to CPU if nothing's available.
- **AI music generation** — generates soundtrack music that matches the mood of your video via MusicGen or ACE-Step.
- **Interactive UI + CLI** — web UI at localhost:8080 for visual configuration, or a CLI if you prefer scripts and cron jobs.

## AI Disclaimer

This project was built primarily with AI assistance. The code, tests, and documentation were generated using LLMs. It works, it has tests, but you should know what you're getting into.

## Next Steps

Ready to try it? Head to the [Quick Start](./quick-start.md) guide.
