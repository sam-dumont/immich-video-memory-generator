---
sidebar_position: 6
title: Custom Music
---

# Custom Music

You don't have to use AI-generated music. There are other options.

## Upload Your Own

In the UI at Step 3 (Generation Options), you can upload your own music file. Any format FFmpeg can read works: MP3, WAV, FLAC, M4A, OGG, etc.

The uploaded track gets used as the background music for your final video. Audio ducking still applies: the music volume will drop automatically when your clips contain speech or laughter (assuming Demucs stem separation is available via MusicGen).

## Disable Music Entirely

If you want a silent video (just the original clip audio), set:

```yaml
audio:
  auto_music: false
```

Or just don't select any music option in the UI. The original audio from your video clips is always preserved regardless of music settings.

## Local Music Library

You can also point at a directory of music files:

```yaml
audio:
  music_source: "local"
  local_music_dir: "~/Music/Memories"
```

The tool will scan the directory and pick a track. It does basic mood matching based on filename and directory structure, but don't expect miracles: it's not Shazam.
