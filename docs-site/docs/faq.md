---
sidebar_position: 99
title: FAQ
---

# FAQ

**Does it modify my Immich library?**

Currently it only reads from Immich: it downloads copies of your videos for analysis and processing. Upload-back to Immich is on the roadmap.

**What video formats does it support?**

Anything FFmpeg can decode, which is basically everything: MP4, MOV, AVI, MKV, WebM, you name it. Output is always MP4 (H.264).

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

The codebase is AI-written (on purpose, as an experiment) with 870+ tests and strict quality gates. The output (music, clip selection, mood analysis) is AI-generated too, so results vary. Review what it produces before showing it at grandma's birthday party.

**Can I generate for multiple people at once?**

Yes. Use `--person "Alice" --person "Bob"` with `--memory-type multi_person`. By default it finds videos where both people appear together. See the CLI docs for all memory type options.

**How much disk space does it need?**

The tool downloads videos temporarily for analysis. A rough estimate: 2x the total size of your source videos (original downloads + processed clips). The temporary files are cleaned up after generation. The final output video is typically 50-200MB for a 5-10 minute 1080p video.

**Does it work on Apple Silicon?**

Yes. VideoToolbox hardware acceleration is auto-detected. For music generation, ACE-Step works via MLX on Apple Silicon. For mood detection LLM, mlx-vlm is the recommended server.
