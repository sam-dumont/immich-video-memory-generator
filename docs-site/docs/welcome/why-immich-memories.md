---
sidebar_label: "Why Immich Memories?"
sidebar_position: 2
---

# Why Immich Memories?

## The problem

You have thousands of videos sitting in Immich. Birthdays, first steps, road trips, random Tuesday afternoons that turned out to be great. They're all there, backed up, searchable, safe.

But when was the last time you actually watched any of them?

The gap isn't storage: Immich handles that. The gap is between "having the videos" and "reliving the moments." Nobody scrolls through 400 clips to pick the 25 best ones, figure out transitions, add music, and export a video they'd actually want to watch on the couch with their family.

That's what this tool does.

## How it started

December 28, 2025. End of the year, wanting to make a video of my son's best moments from the past 12 months. Videos spread across iCloud, Google Photos, and Immich (I had migrated to Immich about a year before to get off Google's servers).

I tried the obvious options:
- **Google Photos** made auto-generated recaps, but the music was pre-canned and I couldn't change it. The clip selection felt random.
- **Apple Photos** had nicer beat-synced transitions, but everything stayed locked inside the Apple ecosystem. No way to share a proper video file.
- **Relive** made beautiful GPS flyovers for bike rides and hikes, but it only worked for tracked activities. A week at the beach with the kids? Useless.
- **FamilyAlbum** had a 1-second-per-day feature that was genuinely emotional to watch. But the clip selection was often a wall or a ceiling, and the free tier was limited.

None of them let me: pick my own music, control which clips stay and which go, run it on my own server, and get an actual MP4 file out.

So I built what I wanted.

## What it actually does

Point it at your Immich server. Pick a year, a person, or a trip. It pulls your videos and photos, scores every clip (motion, faces, audio, optionally LLM content analysis), and assembles a polished video with title screens, transitions, and background music.

Your data never leaves your network. The Immich API key stays local. The connection is read-only by default.

**Seven memory types** to pick from:

- **Year in Review**: the big annual recap, with monthly dividers
- **Monthly Highlights**: shorter check-ins, good on a schedule
- **Person Spotlight**: one person's best moments (needs face recognition in Immich)
- **Multi-Person**: couples, siblings, friend groups
- **Season / On This Day**: nostalgia modes
- **Trip**: GPS-detected travel with animated satellite map zooms between locations

Each type has its own clip scoring profile and title screen style.

## Who this is for

You run Immich. You care about your family's privacy. You want to actually DO something with the thousands of videos you've been carefully backing up. You don't want to spend 4 hours in DaVinci Resolve to make a 3-minute video.

You want to hit "generate" and get something you'd be proud to show at dinner.

## What makes it different

Most tools that generate video memories are either cloud-only (Google, Apple), ecosystem-locked (Apple), activity-specific (Relive), or require manual editing (any NLE). This one:

- Runs entirely on your hardware (NAS, Mac, Linux server, K8s cluster)
- Reads from Immich without modifying anything
- Uses AI to pick the best moments (not just "most recent" or "random")
- Generates original music that matches the mood of your clips
- Adds animated title screens, globe fly-overs, satellite map zooms
- Outputs a standard MP4 you can share anywhere
- Runs on a schedule if you want monthly highlights on autopilot

## Honest limitations

- **No mobile app.** Web UI and CLI. Works in mobile browsers, but no native app.
- **Not instant.** 30 clips at 1080p takes 5-15 minutes depending on hardware. This is a batch process.
- **Not a video editor.** You can select/deselect clips and adjust segments, but there's no timeline or frame-precise trimming.
- **Requires Immich.** This is a companion tool, not a standalone photo manager.

:::info Screenshot needed
**What to capture:** A finished memory video playing in the Step 4 video player, showing a title screen frame
**Viewport:** 1280x800
**State:** Generation complete, success banner visible, video loaded in player
**Target file:** `static/screenshots/why-finished-video.png`
:::
