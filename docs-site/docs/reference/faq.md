---
title: FAQ
---

# FAQ

**Does it modify my Immich library?**

Not unless you ask it to. It reads metadata and previews, and downloads copies of the selected
originals to cut from. With `--upload-to-immich` (or the Export page's upload switch) the finished
video is uploaded as a new asset, optionally into an album. When a re-render of the same memory
lands in the same album, the copy it replaces goes to Immich's trash, so the album does not fill
with identical files. Nothing else is written.

**What leaves my machine?**

By default nothing but requests to your Immich server. The caption server and the reader can
receive pictures and annotation text; both default to `localhost` and only send when you point
them elsewhere. Trip detection geocodes GPS clusters at Nominatim, and satellite title screens
fetch map tiles. [Running modes](../deploy/running-modes.md) has the table per mode and
[Network & Privacy](../deploy/configuration/network-and-privacy.md) the switch for each.

**Do I need a model?**

No. `reader` ships as `auto`, which resolves to the rules reader while `llm.model` is blank, and with it the ten standard memory types
are cut from dates, places, favourites, people and whatever image facts the tier produced. It is
a simpler editor: no thesis, and it can miss an occasion in a broad recap. What it keeps per
memory type, measured against the model editor, is on [Rules mode](../create/pipeline/rules-mode.md).

**How long does a cut take?**

The first cut over a period reads every eligible picture once and banks it; the second is
mostly the render. Measured for a 60-second month of 1,440 pictures, selection only: 55 s cold and
1.4 s warm on a workstation with rules; 279 s cold and 11 s warm on a Celeron NAS; about 16 to
25 minutes with a model reader. The per-host table is on [Running modes](../deploy/running-modes.md).

**How much disk?**

The caches are bounded by config, not by the library: 10 GB of downloaded video (evicted after
7 days), 10 GB of Immich previews, 2 GB of clip previews, plus the annotation store. One measured
62-second 1080p H.264 output was 87 MB, or 30 MB under `preset: fast`.

**Can I generate for several people at once?**

Yes. `--memory-type multi_person` with repeated `--person` means everyone in the same picture;
`--people-expression '"Riley" AND ("Casey" OR "Bob")'` takes a real condition, where `AND` means
the same picture, not the same afternoon. The Memory page has the same field.

**Can I use it without face recognition?**

Yes. Without a person, a period covers everyone. Face recognition only narrows the pool.

**What about Live Photos?**

Included by default. The editor treats a Live Photo as a photograph that carries motion it may
play when the motion earns it; burst-captured Live Photos are merged into one continuous moment.
Tested on iPhones; Samsung and Pixel motion photos should work through Immich's normalisation
but have not been tested first-hand. See [Live Photos](../create/pipeline/live-photos.md).

**Which formats?**

Anything FFmpeg decodes. Output is MP4 or MOV with H.264, H.265 or ProRes. HDR output is H.265
only: `encoding_plan.py` refuses HDR with ProRes and with H.264, so a ProRes render is SDR.

**Can I run it headless?**

Yes: `immich-memories generate` works over SSH, in Docker and in CI, and `runs story` prints the
cut in the terminal.

**Is it safe for production?**

It is beta and under heavy rework, and the code was written with AI assistance as a deliberate
experiment ([Built with AI](../welcome/built-with-ai.md)). The output is an editor's judgment, and
judgments vary. Review a cut before showing it at a family party.

**Does it work on Apple Silicon?**

Yes. VideoToolbox is auto-detected for the encode, ACE-Step runs through MLX, and oMLX is the
tested local server for the caption model and the reader.

**How big should my PR be?**

About 300 lines of diff, one concern per PR. See [CONTRIBUTING.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/CONTRIBUTING.md).
