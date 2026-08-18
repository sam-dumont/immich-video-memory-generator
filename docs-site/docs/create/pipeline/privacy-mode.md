---
sidebar_position: 10
title: Privacy Mode
---

# Privacy Mode

Privacy mode (also called demo mode) blurs all video content, muffles audio, and anonymizes locations and names in the final output. It's for situations where you want to demo the app or share a screen recording without showing your actual footage.

This feature is how all the demo videos on this site were made. I would never have been able to record shareable demos without it: building a privacy mode specifically for this purpose was one of those things where having AI write the code made it feasible. Without it, I'd have had to either skip demos entirely or manually edit out personal content from every recording.

## What it does

When privacy mode is on:

- All video clips get a heavy blur filter applied via FFmpeg before assembly
- All clip audio is reversed in 200 ms segments (you hear people talking but can't make out words) and then low-passed at 300 Hz to remove the remaining consonant artifacts. This applies to all audio, not just detected speech.
- GPS coordinates are relocated to a fake city (preserving cluster shape so the map animation still looks real)
- Person names are replaced with deterministic fake names (same real name always maps to the same fake name)
- Title screens stay unblurred: the generated text, map animations, and location cards are unaffected (but show the fake locations/names)

The result is a video that demonstrates the timing, transitions, music, and structure of the memory without revealing any personal content.

## How to enable

### UI toggle

If `server.enable_demo_mode` is true in your config, the sidebar shows a "Demo mode" switch. Toggling it on also blurs thumbnails in the clip review screen (via a CSS class on `<body>`), so even the preview doesn't show your footage.

### CLI flag

Pass `--privacy-mode` to the `generate` command:

```bash
immich-memories generate --privacy-mode --year 2024
```

### Config

```yaml
server:
  enable_demo_mode: true    # Show the Demo mode switch in the sidebar (off by default)
```

## What gets anonymized

| Data | How it's handled |
|------|-----------------|
| Video content | Heavy Gaussian blur filter |
| Audio | Segment reversal (200 ms) + 300 Hz lowpass on all clip audio |
| GPS coordinates | Relocated to a fake city, cluster shape preserved |
| Person names | Replaced with deterministic fake names |
| Title screen text | Uses fake names and locations |
| Map animation | Shows fake destination, same visual style |

## What stays unblurred

Title screens are always rendered clean:
- The opening title card with your trip name or year
- Animated satellite map fly-over
- Location interstitial cards
- The ending screen

Only the actual video clips get the blur treatment.
