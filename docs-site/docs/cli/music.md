---
sidebar_position: 4
title: music
---

# music

Three subcommands for finding, analyzing, and adding music to your videos.

## music search

Search your local music library by mood, genre, and tempo.

```bash
immich-memories music search [OPTIONS]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--mood` | `-m` | string | — | Mood filter (`happy`, `calm`, `energetic`, etc.) |
| `--genre` | `-g` | string | — | Genre filter (`acoustic`, `electronic`, `cinematic`, etc.) |
| `--tempo` | `-t` | choice | — | `slow`, `medium`, or `fast` |
| `--min-duration` | — | float | `60` | Minimum track duration in seconds |
| `--limit` | `-n` | int | `10` | Number of results to return |

Example:

```bash
immich-memories music search --mood happy --genre acoustic --limit 5
```

The local music directory defaults to `~/Music/Memories` (configurable via `audio.local_music_dir` in config).

## music analyze

Analyzes a video file to determine its mood. Uses your configured LLM (Ollama or OpenAI-compatible) to extract keyframes and figure out the overall vibe: energy level, color palette, tempo suggestion, genre recommendations.

```bash
immich-memories music analyze VIDEO_PATH [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--ollama-url` | string | from config | Override Ollama API URL |
| `--ollama-model` | string | from config | Override vision model |

Example:

```bash
immich-memories music analyze ~/Videos/vacation.mp4
```

Output includes primary/secondary mood, energy level, suggested tempo, color palette, genre suggestions, and confidence score.

## music add

Adds background music to an existing video. Includes automatic audio ducking: the music volume drops when speech or other sounds are detected.

```bash
immich-memories music add VIDEO_PATH OUTPUT_PATH [OPTIONS]
```

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--music` | `-m` | path | auto-select | Music file to use |
| `--mood` | — | string | — | Override mood for auto music selection |
| `--genre` | `-g` | string | — | Override genre for auto music selection |
| `--volume` | `-v` | float | `-6.0` | Music volume in dB |
| `--fade-in` | — | float | `2.0` | Fade in duration in seconds |
| `--fade-out` | — | float | `3.0` | Fade out duration in seconds |

If you don't provide `--music`, it auto-selects a track based on the video's mood.

Examples:

```bash
# Specific music file
immich-memories music add compilation.mp4 output.mp4 --music ~/Music/track.mp3

# Auto-select with custom fade
immich-memories music add compilation.mp4 output.mp4 --fade-in 3 --fade-out 5

# Override mood for selection
immich-memories music add compilation.mp4 output.mp4 --mood energetic --volume -3
```
