---
title: FAQ
---

# FAQ

**Does it modify my Immich library?**

Not unless you ask it to. It reads metadata and previews and downloads copies of the selected
originals to cut from. With `--upload-to-immich` (or the Export page's upload switch) the finished
video is uploaded as a new asset, optionally into an album; a re-render into the same album sends
the copy it replaces to Immich's trash. Nothing else is written.

**What leaves my machine?**

By default nothing but requests to your Immich server. The caption server and the reader can
receive pictures and annotation text; both default to `localhost`. Trip detection geocodes GPS
clusters at Nominatim, and satellite title screens fetch map tiles.
[Network & Privacy](../deploy/configuration/network-and-privacy.md) has the switch for each.

**Do I need a model?**

No. `reader` ships as `auto`, which resolves to the rules reader while `llm.model` is blank. The
ten standard memory types are then cut from dates, places, favourites, people and whatever image
facts the tier produced. It is a simpler editor: no thesis, and it can miss an occasion in a broad
recap. What it keeps per memory type, measured against the model editor, is on
[Rules mode](../create/pipeline.md#editing-without-a-language-model).

**How long does a cut take?**

The first cut over a period reads every eligible picture once and banks it; the second is mostly
the render. Measured for a 60-second month of 1,440 pictures, selection only: 55 s cold and 1.4 s
warm on a workstation with rules, 279 s cold and 11 s warm on a Celeron NAS. With a model reader,
a 60-second February 2024 cost 55 model calls and 3.7 minutes cold, and 40 seconds warm with no
model call at all, because a period read once is answered from the bank a month at a time. The
per-host table is on [Running modes](../deploy/running-modes.md).

**How much disk?**

The caches are bounded by config, not by the library: 10 GB of downloaded video (evicted after
7 days), 10 GB of Immich previews, 2 GB of clip previews, plus the annotation store. Finished
films need additional space; their size depends on duration, resolution, codec and quality.

**Can I generate for several people at once?**

Yes. `--memory-type multi_person` with repeated `--person` means everyone in the same picture.
`--people-expression '"Riley" AND ("Casey" OR "Bob")'` takes a real condition, where `AND` means
the same picture, not the same afternoon. The Memory page has the same field.

**Can I use it without face recognition?**

Yes. Without a person, a period covers everyone. Face recognition only narrows the pool.

**What about Live Photos?**

Included by default. The editor treats a Live Photo as a photograph carrying motion it may play
when the motion earns it; burst-captured ones are merged into a single moment. Tested on iPhones;
Samsung and Pixel motion photos are untested.
See [Live Photos](../create/photos-and-live-photos.md#live-photos).

**Which formats?**

Anything FFmpeg decodes. Output is MP4 or MOV with H.264, H.265 or ProRes. HDR output is H.265
only.

**Can I run it headless?**

Yes: `generate` works over SSH, in Docker and in CI, and `runs story` prints the cut in the
terminal.

**Is it safe for production?**

It is beta, and the code was written with AI assistance as a deliberate experiment
([Built with AI](../welcome/built-with-ai.md)). The output is an editor's judgment. Review a cut
before showing it at a family party.

**Does it work on Apple Silicon?**

Yes. VideoToolbox for the encode, MLX for ACE-Step, oMLX for the caption model and the reader.
