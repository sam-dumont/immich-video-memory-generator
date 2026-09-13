---
sidebar_position: 10
title: Privacy Mode
---

# Privacy Mode

Privacy mode (also called demo mode) blurs every frame of every clip, makes the audio unintelligible, and replaces person names with fake ones. It's for situations where you want to demo the app or share a screen recording without showing your actual footage.

The demos and screenshots on this site no longer use it. They run the real product over a CC0 stock library that tells one made-up household's June, so nothing needs blurring ([how the demo assets are made](../../contribute/demo-assets.md)). Privacy mode stays for the case it was built for: showing the app over your own library to someone who should not see your pictures.

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
| GPS coordinates | The whole memory is moved onto one fake city: its centre lands on the city, every clip keeps its bearing and distance from that centre, and a memory spread wider than about 25 km is scaled down to fit. Home base moves with it |
| Place names | Replaced with the fake city's name wherever the memory carried one. A clip with no place name does not gain one |
| Person names | Replaced with one of twelve fake names, picked by SHA-256 of the real one, so the same person is the same alias every run |
| Title screen text | Uses the fake person name and the fake city |
| Map animation | Flies to the fake destination, same visual style |

The move is the same every run: two renders of the same trip put it in the same place, and repeated renders give away nothing that could be averaged back to the real one.

## What stays unblurred

The *graphics* of a title screen are rendered clean: the title text, the animated satellite map
fly-over, the location interstitial cards, the ending screen.

The footage behind them is not. An opening or ending card backed by a frame from your own clips
gets the same privacy blur the clips do, because it is a clip. So nothing unblurred from your
library appears at any point; what stays legible is the text and the map drawn on top.

## What it does not cover

The output file name is built before anonymization, so it can still carry the real place or the
real person names. Rename the file before sharing it: what is inside the video is anonymized, the
name on disk is not.
