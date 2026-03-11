---
sidebar_position: 1
title: CLI Overview
---

# CLI Overview

Everything in immich-memories is available through the `immich-memories` command. No subcommand soup — just straightforward verbs.

## Commands

| Command | What it does |
|---------|-------------|
| `immich-memories generate` | Create a video compilation from your Immich library |
| `immich-memories analyze` | Analyze and cache video metadata (scenes, faces, audio) |
| `immich-memories ui` | Launch the web UI on port 8080 |
| `immich-memories hardware` | Show detected GPU, encoder, and decoder capabilities |
| `immich-memories people` | List recognized people from Immich's face recognition |
| `immich-memories years` | List years that have video content |
| `immich-memories config` | Show or edit configuration (Immich URL, API key, etc.) |
| `immich-memories export-project` | Export project state as JSON for external editing |
| `immich-memories music search` | Search local music library by mood/genre |
| `immich-memories music analyze` | Analyze a video's mood for music matching |
| `immich-memories music add` | Add background music to a video with auto-ducking |
| `immich-memories runs list` | List previous generation runs |
| `immich-memories runs show` | Show details of a specific run |
| `immich-memories runs stats` | Aggregate statistics across all runs |
| `immich-memories runs delete` | Delete a run and its output |
| `immich-memories titles test` | Generate a test title screen |
| `immich-memories titles fonts` | Manage title screen fonts |
| `immich-memories preflight` | Validate all provider connections before generating |

## Global options

```bash
immich-memories --version       # Print version
immich-memories --config PATH   # Use a specific config file
immich-memories --help          # Show help
```

Each subcommand has its own `--help` too:

```bash
immich-memories generate --help
immich-memories music search --help
```

## Quick start

```bash
# First time? Configure your Immich connection
immich-memories config

# See what years have videos
immich-memories years

# Generate a 10-minute compilation for 2024
immich-memories generate --year 2024

# Or launch the web UI and do it from there
immich-memories ui
```
