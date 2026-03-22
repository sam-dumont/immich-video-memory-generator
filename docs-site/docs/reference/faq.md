---
title: FAQ
---

# FAQ

**Does it modify my Immich library?**

By default, no. It downloads copies of your videos for analysis and processing. If you enable `--upload-to-immich`, the generated compilation video is uploaded as a new asset (optionally into an album). Your original videos are never modified or deleted.

**What video formats does it support?**

Anything FFmpeg can decode, which is basically everything: MP4, MOV, AVI, MKV, WebM, you name it. Output supports mp4 and mov containers with h264, h265, or ProRes codecs.

**Can I use it without face recognition?**

Yes. Skip the `--person` flag and it'll pull all videos from the selected time period. Face recognition just narrows the selection to videos containing a specific person.

**How long does analysis take?**

Depends on your library size and hardware. Rough numbers:

- CPU only: ~1-2 minutes per video
- GPU analysis enabled: ~15-30 seconds per video
- With downscaling to 480p: roughly half the above

Results are cached, so you only pay this cost once per video.

**Can I run it headless?**

Yes. The CLI works without a display. Use `immich-memories generate` with flags instead of `immich-memories ui`. Works fine over SSH, in Docker containers, and in CI pipelines.

**Is it safe for production?**

The codebase is AI-written (on purpose, as an experiment) with 1,000+ tests and strict quality gates. The output (music, clip selection, mood analysis) is AI-generated too, so results vary. Review what it produces before showing it at grandma's birthday party.

**Can I generate for multiple people at once?**

Yes. Use `--person "Alice" --person "Bob"` with `--memory-type multi_person`. By default it finds videos where both people appear together. See the [generate CLI docs](../create/cli/generate.md) for all memory type options.

**How much disk space does it need?**

The tool downloads videos temporarily for analysis. A rough estimate: 2x the total size of your source videos (original downloads + processed clips). The temporary files are cleaned up after generation. The final output video is typically 50-200MB for a 5-10 minute 1080p video.

**Can it use iPhone Live Photos?**

Yes. Live Photos are included by default (`analysis.include_live_photos: true`). Live Photos are ~3 second video clips captured with every iPhone photo. When you took photos in rapid succession, the tool detects overlapping clips and merges them into longer continuous moments: transitions happen at each shutter press. A burst of 5 Live Photos becomes one ~8 second clip.

Tested on iPhones. Samsung and Google Pixel motion photos should work (Immich normalizes them to the same field), but I only use iOS so it hasn't been tested firsthand. PRs from Android users welcome.

**How big should my PRs be?**

Under 200-300 lines of diff. Smaller PRs get reviewed faster and catch bugs earlier. If your change is bigger, split it into focused chunks. See [CONTRIBUTING.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/CONTRIBUTING.md) for the full guidelines.

**Does it work on Apple Silicon?**

Yes. VideoToolbox hardware acceleration is auto-detected. For music generation, ACE-Step works via MLX on Apple Silicon. For mood detection LLM, mlx-vlm is the recommended server.
