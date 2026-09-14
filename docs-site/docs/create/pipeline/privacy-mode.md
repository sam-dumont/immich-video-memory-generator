---
sidebar_position: 10
title: Privacy Mode
---

# Privacy Mode

Privacy mode (also called demo mode) blurs footage, distorts clip audio, and replaces the person-name field and locations. It is intended for demonstrations using your own library.

The demos and screenshots on this site no longer use it. They run the real product over a CC0 stock library that tells one made-up household's June, so nothing needs blurring ([how the demo assets are made](../../contribute/demo-assets.md)). Privacy mode stays for the case it was built for: showing the app over your own library to someone who should not see your pictures.

It does not remove all personal information. Custom titles, subtitles and output filenames can still identify people or places. Review the finished video before sharing it.

## How to enable

### UI toggle

If `server.enable_demo_mode` is true in your config, the sidebar shows a "Demo mode" switch. Toggling it on also blurs image and video previews across the UI.

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
| Video content | Whole-frame Gaussian blur plus a noise texture, applied before assembly |
| Audio | Reversal in 200 ms segments and a 300 Hz low-pass filter on clip audio; this reduces intelligibility but does not guarantee that every word or identifying sound is concealed |
| GPS coordinates | The whole memory is moved onto one fake city: its centre lands on the city, every clip keeps its bearing and distance from that centre, and a memory spread wider than about 25 km is scaled down to fit. Home base moves with it |
| Place names | Replaced with the fake city's name wherever the memory carried one. A clip with no place name does not gain one |
| Separate person-name field | Replaced with a consistent fake name; this does not search other text for names |
| Generated template titles | Use the fake person name and city; custom titles and subtitles remain unchanged |
| Map animation | Flies to the fake destination, same visual style |

The relocation is deterministic: the same trip is moved to the same place on repeated renders.

## What stays unblurred

The *graphics* of a title screen are rendered clean: the title text, the animated satellite map
fly-over, the location interstitial cards, the ending screen.

An opening or ending card backed by a frame from your clips receives the privacy blur.
Title text and map graphics drawn on top stay legible.

## What it does not cover

Custom titles and subtitles are copied into the video unchanged, including a custom map title.
Check them for real names and places. The output filename is also built before anonymization;
rename it before sharing if it contains identifying information.
