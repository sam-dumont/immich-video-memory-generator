---
sidebar_label: "Why Immich Memories?"
sidebar_position: 2
---

# Why Immich Memories?

You already have something that shows you old photos. Every phone does it. The question is whether it does it well, whether you own the data, and whether you get anything beyond a slideshow.

## What you're probably using today

**Camera roll scrolling.** You open your Photos app, scroll back to 2022, watch a few clips, get distracted, close the app. The videos are all there but there's no curation, no music, no story.

**Google Photos Memories.** Decent auto-generated videos, but your data lives on Google's servers. No self-hosting option. The algorithm picks clips for you with no way to adjust. If you're running Immich, you already decided Google shouldn't hold your photos.

**Apple Photos Memories.** Good clip selection, face-aware cropping, nice music. But Apple ecosystem only: no Linux, no NAS, no self-hosting. And the output stays locked in the Photos app.

**Relive.** Subscription-based trip videos from GPS data. Beautiful map animations, but it only does trips (no year-in-review, no person spotlights) and requires a paid subscription.

## Feature comparison

| Feature | Google Photos | Apple Photos | Relive | Immich Memories |
|---------|:---:|:---:|:---:|:---:|
| Self-hosted / private | | | | Yes |
| Smart clip scoring | | partial | | Yes |
| AI-generated music | | | | Yes |
| Cinematic title screens | | | | Yes |
| Face-aware cropping | | Yes | | Yes |
| Trip maps with satellite | | | Yes | Yes |
| Live photo stitching | | | | Yes |
| Read-only source access | n/a | n/a | n/a | Yes |
| Photo + video mixing | Yes | Yes | | Yes |
| Runs on a schedule | Yes | Yes | | Yes |
| Open source | | | | Yes |

:::info Screenshot needed
**What to capture:** Side-by-side comparison: Google Photos memory notification vs Immich Memories final video player
**Viewport:** 1280x800
**State:** Split screen showing both outputs for the same time period
**Target file:** `static/screenshots/comparison-table.png`
:::

## What Immich Memories actually does

It connects to your Immich server (read-only), pulls your videos and photos for a time period, scores every clip using motion analysis, face detection, and optionally LLM content understanding, then assembles a polished video with title screens, transitions, and background music.

The whole thing runs on your hardware. Your data never leaves your network.

Seven memory types: Year in Review, Monthly Highlights, Person Spotlight, Multi-Person, Season, On This Day, and Trip (with animated satellite maps). Each one has its own scoring profile and title screen style.

## What this doesn't do

Honest about the gaps:

- **No mobile app.** It's a web UI and CLI. Works in mobile browsers but there's no native app.
- **No real-time generation.** A 30-clip video at 1080p takes 5-15 minutes depending on your hardware. This is a batch process, not instant.
- **Not a video editor.** You can deselect clips and adjust segments, but there's no timeline, no manual trimming to the frame, no text overlays beyond title screens.
- **No social sharing.** It outputs an MP4 file. What you do with it after that is up to you.
- **Requires Immich.** This is a companion tool for Immich, not a standalone photo manager.
