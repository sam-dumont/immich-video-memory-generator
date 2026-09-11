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

If `server.enable_demo_mode` is true in your config, the sidebar shows a "Demo mode" switch. Toggling it on also blurs every image and video the UI renders, on every page, not just the clip review screen (a CSS class on `<body>`), so no preview shows your footage.

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
| Video content | Whole-frame Gaussian blur plus a noise texture (frosted glass, not pixelation) applied via FFmpeg before assembly. Not face detection: every pixel of every clip goes |
| Audio | Segment reversal (200 ms) + 300 Hz lowpass on all clip audio, not just detected speech: you hear people talking but cannot make out words |
| Person names | Replaced with one of twelve fake names, picked by SHA-256 of the real one, so the same person is the same alias every run |
| Home base | Shifted to a fixed European city, offset so the fly-in route stays visible. There are eight in the list, but the picker reseeds itself on every call, so in practice it is always the same one |
| Title screen text | Uses the fake person name |

**What it does not anonymize: the destination.** Clip GPS, place names, location cards and the
trip map all show the real place. That is deliberate: a trip memory with a fake destination is
not a demo of a trip memory. If the place itself is the thing you cannot show, privacy mode is
the wrong tool.

## What stays unblurred

The *graphics* of a title screen are rendered clean: the title text, the animated satellite map
fly-over, the location interstitial cards, the ending screen.

The footage behind them is not. An opening or ending card backed by a frame from your own clips
gets the same privacy blur the clips do, because it is a clip. So nothing unblurred from your
library appears at any point; what stays legible is the text and the map drawn on top.
