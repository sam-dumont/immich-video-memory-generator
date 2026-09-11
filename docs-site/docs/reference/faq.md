---
title: FAQ
---

# FAQ

**Does it modify my Immich library?**

By default, no. It downloads copies of your videos for analysis and processing. If you enable `--upload-to-immich`, the generated compilation video is uploaded as a new asset (optionally into an album). Your original videos are never modified or deleted.

**What video formats does it support?**

Anything FFmpeg can decode, which is basically everything: MP4, MOV, AVI, MKV, WebM, you name it. Output supports mp4 and mov containers with h264, h265, or ProRes codecs.

**Can I use it without face recognition?**

Yes. Skip the `--person` flag and it'll pull everything eligible from the selected time period. Face recognition just narrows the pool to assets containing a specific person.

**How long does analysis take?**

Depends on how much of the period has already been prepared, and on where the caption server and the text model run. The first cut over a period captions and measures every eligible picture once and reads the period with the text model; both are cached per producer and per exact request, so a library only pays this once and re-runs over the same period are mostly the render. `preset: fast` is the CPU-only render profile; it does not change what the editor reads. The [README resource table](https://github.com/sam-dumont/immich-video-memory-generator#resource-requirements) has RAM and encoding numbers, and the [NAS-only guide](../deploy/common-setups/nas-only.md) has a measured 4-core run, with a 2-3x multiplier for Celeron-class silicon.

**Can I run it headless?**

Yes. The CLI works without a display. Use `immich-memories generate` with flags instead of `immich-memories ui`. Works fine over SSH, in Docker containers, and in CI pipelines.

**Is it safe for production?**

The codebase is AI-written (on purpose, as an experiment) with 7,461 tests (6,838 unit, 623 integration and E2E) and strict quality gates. The output (music, clip selection, mood analysis) is AI-generated too, so results vary. Review what it produces before showing it at grandma's birthday party.

**Can I generate for multiple people at once?**

Yes. Use `--person "Riley" --person "Bob"` with `--memory-type multi_person`. By default it finds videos where both people appear together. See the [generate CLI docs](../create/cli/generate.md) for all memory type options.

**How much disk space does it need?**

Downloads are a bounded cache, not per-run scratch, so the ceiling is set by config rather than by your library size. The defaults under `cache:` are 10 GB of downloaded video (evicted after 7 days), 10 GB of Immich previews, 2 GB of clip previews, and `cache.db` keeping 30 days of analysis results.

The output is small next to that. One measured run: 62 seconds of 1080p H.264 came out at 87 MB, or 30 MB under `preset: fast`. The [NAS-only guide](../deploy/common-setups/nas-only.md) has the rest of that measurement.

**Can it use iPhone Live Photos?**

Yes. Live Photos are included by default (`analysis.include_live_photos: true`). Live Photos are ~3 second video clips captured with every iPhone photo. When you took photos in rapid succession, the tool detects the overlap and merges them into one continuous moment, cutting at the midpoint between consecutive shutter presses. Two real examples from the [Live Photos page](../create/pipeline/live-photos.md): three Live Photos merged to 4.5 seconds, six merged to 8.4 seconds.

Tested on iPhones. Samsung and Google Pixel motion photos should work (Immich normalizes them to the same field), but I only use iOS so it hasn't been tested firsthand. PRs from Android users welcome.

**How big should my PRs be?**

About 300 lines of diff, excluding generated and lock files, one concern per PR. Smaller PRs get reviewed faster and catch bugs earlier. If your change is bigger, split it into focused chunks. See [CONTRIBUTING.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/CONTRIBUTING.md) for the full guidelines.

**Does it work on Apple Silicon?**

Yes. VideoToolbox hardware acceleration is auto-detected. For music generation, ACE-Step works via MLX on Apple Silicon. For mood detection LLM, mlx-vlm is the recommended server.
