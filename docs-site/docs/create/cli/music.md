---
sidebar_position: 2
title: music
---

# music

Three standalone tools: search the local library, ask a model what a finished film sounds like,
and mix a track onto a video that already exists. How music gets picked during a normal
`generate` run is on [Audio & Music](../pipeline/audio-and-music.md).

## music search

Search the local music library by mood, or by any word in a track's title, artist or folder name.

```bash
immich-memories music search --mood happy --genre acoustic --limit 5
```

The directory is `audio.local_music_dir`, `~/Music/Memories` by default. Every flag is in the
[CLI reference](../../reference/cli-reference.md#music).

Two things the flags do not tell you: `--tempo` is accepted and does not filter local results, and
the search hard-codes a 10-minute maximum, so longer tracks never appear and there is no flag to
raise it.

## music analyze

```bash
immich-memories music analyze ~/Videos/vacation.mp4
```

Extracts keyframes from a video file and asks the configured vision model what it sounds like:
primary and secondary mood, energy level, a suggested tempo, a colour palette, genre suggestions
and a confidence score. This is an explicit request to send frames.

`--ollama-url` overrides the configured reader's base URL whatever provider it is set to; the name
is historical and it is not Ollama-specific.

## music add

```bash
immich-memories music add VIDEO_PATH OUTPUT_PATH [OPTIONS]
```

Mixes a track under a video that already exists, ducking it when the clip's own audio comes up.

Without `--music` it searches your configured local library at mood `calm`, and no frames are
extracted or sent for that pick. For a standalone film with no saved cut text, `--analyze-frames`
asks the configured vision provider to judge sampled frames instead. A supplied `--mood` beats
both.

```bash
# Specific music file
immich-memories music add compilation.mp4 output.mp4 --music ~/Music/track.mp3

# Auto-select with custom fade
immich-memories music add compilation.mp4 output.mp4 --fade-in 3 --fade-out 5

# Override mood for selection
immich-memories music add compilation.mp4 output.mp4 --mood energetic --volume -3
```
