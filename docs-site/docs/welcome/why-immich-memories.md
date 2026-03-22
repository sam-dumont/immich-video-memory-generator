---
sidebar_label: "Why Immich Memories?"
sidebar_position: 2
---

# Why Immich Memories?

## How it started

End of December 2025. My son's birthday was coming up and I wanted to make a yearly recap of his best moments. I had hundreds of clips, all safely stored in Immich after I migrated off Google Photos.

I opened DaVinci Resolve. Imported 40 clips. Started dragging them onto a timeline. Added a few crossfades. Realized I needed music. Found something on YouTube Audio Library. Tried to sync cuts to the beat. Two hours later I had 90 seconds of mediocre output and I still hadn't gone through the other 360 clips.

I closed the laptop and watched TV instead.

That's the actual problem. Not "I can't find my videos" (Immich handles that). Not "I don't have enough storage" (solved). The problem is the 4-8 hours between "I have 400 clips" and "here's a 3-minute video I'd actually show my family."

## What exists out there

Tools that do parts of this exist. Cloud photo services generate annual recaps automatically, which is the right idea, but the music is pre-canned and the clip selection is a black box. Some phone platforms have beat-synced transitions and face awareness, but the output stays locked in their ecosystem. Activity trackers make beautiful GPS flyovers for bike rides and hikes, but they only work for tracked activities (not "my kid's year"). Some family apps have 1-second-per-day compilations that are genuinely moving to watch, but the clip selection is random (I got 1 second of a wall more than once).

Each tool does one piece well. None of them combine smart clip selection, custom music, trip maps, title screens, and video assembly in a single pipeline. And none of them run on your own server or let you control what goes in and what doesn't.

If you're running Immich, you already made the decision to own your data. This tool gives you something to do with it.

## What I built instead

A tool that does the boring part for me. It connects to Immich (read-only), looks at a time period I pick, and figures out which clips are worth keeping. It scores them on motion, faces, audio content, and optionally runs an LLM to understand what's actually happening in the scene. Then it assembles everything into a video with title screens, transitions, and music.

The key: I still get to review what it picked and remove anything I don't want. But I'm reviewing 30 pre-selected clips instead of scrolling through 400.

## Why every feature exists

Every feature traces back to "remove a manual step from making a memory video":

- **Smart clip scoring**: so I don't have to watch 400 clips to find the 25 good ones
- **Face detection + recognition**: so clips with my kid in frame score higher than clips of the parking lot
- **Scene detection**: so the tool picks the interesting 8-second segment, not the full 45-second recording
- **Duplicate detection**: so I don't get three versions of the same moment from burst recordings
- **Live photo stitching**: so those 3-second iPhone live photos get merged into usable clips
- **AI music generation**: so I don't spend 45 minutes on YouTube Audio Library looking for a track that fits
- **Audio ducking**: so music gets quieter when someone's talking or laughing in a clip
- **Title screens**: so the output looks like something, not just "clip 1, clip 2, clip 3"
- **Trip maps**: so a travel video starts with an animated satellite zoom showing where we went
- **Photo support**: so still photos get mixed in with video (Ken Burns, blur background, face-aware pan)
- **Scheduled generation**: so monthly highlights just appear on the 1st without me doing anything

The result is a video that looks like I spent a weekend in Premiere Pro, except I spent 5 minutes reviewing clips and hit "generate."

## Limitations

- **No mobile app.** Web UI and CLI. Works in mobile browsers but there's no native app.
- **Not instant.** 30 clips at 1080p takes 5-15 minutes depending on hardware. Batch process.
- **Not a video editor.** Select/deselect clips and adjust segments, but no timeline.
- **Requires Immich.** Companion tool, not a standalone photo manager.

:::info Screenshot needed
**What to capture:** A finished memory video playing in the Step 4 video player
**Viewport:** 1280x800
**State:** Generation complete, video loaded in player showing a real frame
**Target file:** `static/screenshots/why-finished-video.png`
:::
