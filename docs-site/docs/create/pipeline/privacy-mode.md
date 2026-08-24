---
sidebar_position: 10
title: Privacy Mode
---

# Privacy Mode

Privacy mode (also called demo mode) blurs all video content, muffles audio, and anonymizes locations and names in the final output. It's for situations where you want to demo the app or share a screen recording without showing your actual footage.

This feature is how all the demo videos on this site were made. I would never have been able to record shareable demos without it: building a privacy mode specifically for this purpose was one of those things where having AI write the code made it feasible. Without it, I'd have had to either skip demos entirely or manually edit out personal content from every recording.

The result is a video that demonstrates the timing, transitions, music, and structure of the memory without revealing any personal content. [What gets anonymized](#what-gets-anonymized) is the full list.

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
| Video content | Heavy Gaussian blur, applied via FFmpeg before assembly |
| Audio | Segment reversal (200 ms) + 300 Hz lowpass on all clip audio, not just detected speech — you hear people talking but cannot make out words |
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
