---
sidebar_position: 10
title: Privacy Mode
---

# Privacy Mode

Privacy mode (also called demo mode) blurs every frame of every clip, makes the audio unintelligible, and replaces person names with fake ones. It's for situations where you want to demo the app or share a screen recording without showing your actual footage.

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
| Video content | Whole-frame Gaussian blur plus a noise texture — frosted glass, not pixelation — applied via FFmpeg before assembly. Not face detection: every pixel of every clip goes |
| Audio | Segment reversal (200 ms) + 300 Hz lowpass on all clip audio, not just detected speech — you hear people talking but cannot make out words |
| Person names | Replaced with one of twelve fake names, picked by SHA-256 of the real one, so the same person is the same alias every run |
| Home base | Shifted to one of eight European cities, offset so the fly-in route stays visible |
| Title screen text | Uses the fake person name |

**What it does not anonymize: the destination.** Clip GPS, place names, location cards and the
trip map all show the real place. That is deliberate — a trip memory with a fake destination is
not a demo of a trip memory. If the place itself is the thing you cannot show, privacy mode is
the wrong tool.

## What stays unblurred

Title screens are always rendered clean:
- The opening title card with your trip name or year
- Animated satellite map fly-over
- Location interstitial cards
- The ending screen

Only the actual video clips get the blur treatment.
