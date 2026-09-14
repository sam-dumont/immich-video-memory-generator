---
sidebar_position: 2
title: music
---

# music

Three subcommands for finding, analyzing, and adding music to your videos.

## music search

Search your local music library by mood, or by any word in a track's title, artist or folder name.
Every flag is in the [CLI reference](../../reference/cli-reference.md#music).

```bash
immich-memories music search [OPTIONS]
```

Example:

```bash
immich-memories music search --mood happy --genre acoustic --limit 5
```

The local music directory defaults to `~/Music/Memories` (configurable via `audio.local_music_dir` in config).

Two things the flags do not tell you: `--tempo` is accepted and does not filter local results, and
the search hard-codes a 10-minute maximum, so longer tracks never appear and there is no flag to
raise it.

## music analyze

Analyzes a video file to determine its mood. Uses your configured LLM to extract keyframes and figure out the overall vibe: energy level, color palette, tempo suggestion, genre recommendations.

```bash
immich-memories music analyze VIDEO_PATH [OPTIONS]
```

`--ollama-url` overrides the configured reader's base URL whatever provider it is set to; the name
is historical and it is not Ollama-specific.

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

Without `--music`, it searches your configured local music library. `--mood` sets the mood;
otherwise it uses calm. No frames are extracted or sent for this default selection.

For a standalone film with no saved cut text, `--analyze-frames` explicitly asks the configured
vision provider to judge sampled frames. A supplied `--mood` takes precedence. `music analyze`
also remains an explicit request to send frames.

Examples:

```bash
# Specific music file
immich-memories music add compilation.mp4 output.mp4 --music ~/Music/track.mp3

# Auto-select with custom fade
immich-memories music add compilation.mp4 output.mp4 --fade-in 3 --fade-out 5

# Override mood for selection
immich-memories music add compilation.mp4 output.mp4 --mood energetic --volume -3
```
