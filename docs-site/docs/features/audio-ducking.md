---
sidebar_position: 7
title: Audio Ducking
---

# Audio Ducking

When background music plays over your clips, it should get quieter when someone's talking or when there's an interesting sound in the original audio. That's audio ducking — the music automatically dips to let the original audio through, then comes back up.

## How it works

1. **Stem separation** — [Demucs](https://github.com/facebookresearch/demucs) splits the clip's audio into vocals and non-vocal stems
2. **Activity detection** — when the vocal/sound energy exceeds the ducking threshold, the music volume drops
3. **Smooth transitions** — fade in/out prevents jarring volume jumps

The result: music plays at full volume during quiet moments and drops when there's something worth hearing in the original clip.

## Configuration

```yaml
audio:
  ducking_threshold: 0.02    # audio energy level that triggers ducking
  ducking_ratio: 6.0         # how much the music drops (higher = more reduction)
  music_volume_db: -6.0      # baseline music volume in dB (before ducking)
  fade_in: 0.3               # seconds to fade music back up
  fade_out: 0.1              # seconds to fade music down
```

### Key parameters

**`ducking_threshold` (0.02)** — the minimum audio energy in the clip that triggers ducking. Lower values make it more sensitive (music ducks for quieter sounds). If your clips have a lot of background noise, you might want to raise this to 0.05 or higher.

**`ducking_ratio` (6.0)** — the amount of volume reduction when ducking activates. A ratio of 6.0 means the music drops significantly. Lower values (e.g., 3.0) give a subtler dip.

**`music_volume_db` (-6.0)** — the baseline music volume *before* any ducking. At -6 dB, the music is already mixed quieter than the clip audio. Set to -12.0 or lower if you want the music to be more of a background texture.

**`fade_in` / `fade_out`** — how quickly the music volume transitions. Short fade-out (0.1s) means the music ducks quickly when speech starts. Longer fade-in (0.3s) means it comes back gradually, which sounds more natural.

## Demucs dependency

Stem separation requires [Demucs](https://github.com/facebookresearch/demucs), which downloads a model on first use (~80 MB). If Demucs isn't available, ducking still works but uses simpler energy detection on the mixed audio, which is less accurate at distinguishing speech from music.
