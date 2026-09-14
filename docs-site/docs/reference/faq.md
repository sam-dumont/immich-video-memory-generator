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

The app contacts your configured Immich server. Model endpoints can receive pictures and
annotation text. Place-name lookup uses Nominatim, satellite titles fetch map tiles, and model
installation downloads artifacts. See [Network & Privacy](../deploy/configuration/network-and-privacy.md)
for destinations and settings.

**Do I need a model?**

The rules reader needs no language model, but preparation is a separate choice. Use
`metadata_only` for no model producers, or `no_captions` for local image classifiers without a
caption server. The default native `full` tier needs that server even with rules.

With `reader: rules` (the default when no `llm.model` is set) the ten standard memory types
are cut from dates, places, favourites, people and whatever image facts the tier produced. It is
a simpler editor: no thesis, and it can miss an occasion in a broad recap. What it keeps per
memory type, measured against the model editor, is on [Rules mode](../create/pipeline/rules-mode.md).

**How long does a cut take?**

Preparation scales with the number of candidate assets and the chosen tier. Later runs reuse
compatible saved facts and readings. A model change, changed inputs or a cleared cache can
require new work. [Running modes](../deploy/running-modes.md) has measured timings per host.

**How much disk?**

Default media cache budgets total about 22 GB. Active files can exceed those budgets; add room
for model files, annotations, saved attempts, temporary renders and exports. Use
`immich-memories runs storage` to inspect usage. Kubernetes examples use PVCs, including for
scratch space. See [storage and backups](../deploy/maintenance/health-logs-cache.md).

**Can I generate for several people at once?**

Yes. `--memory-type multi_person` with repeated `--person` means everyone in the same picture;
`--people-expression '"Riley" AND ("Casey" OR "Bob")'` takes a real condition, where `AND` means
the same picture, not the same afternoon. The Memory page has the same field.

**Can I use it without face recognition?**

Yes. A period without a person filter can use untagged assets. Person filters need Immich face
tags; those tags also help the editor group people and frame photos.

**What about Live Photos?**

Included by default. The editor treats a Live Photo as a photograph that carries motion it may
play when the motion earns it; burst-captured Live Photos are merged into one continuous moment.
Tested on iPhones; Samsung and Pixel motion photos should work through Immich's normalisation
but have not been tested first-hand. See [Live Photos](../create/pipeline/live-photos.md).

**Which formats?**

Output is MP4 or MOV with H.264, H.265 or ProRes. HDR export requires H.265; H.264 and ProRes
exports are SDR. ProRes requires MOV. Source decoding depends on your FFmpeg build. See
[HDR](../create/pipeline/hdr.md) for tone mapping and mixed-source behaviour.

**Can I run it headless?**

Yes: `immich-memories generate` works over SSH, in Docker and in CI, and `runs story` prints the
cut in the terminal.

**Is it safe for production?**

It is beta and under heavy rework, and the code was written with AI assistance as a deliberate
experiment ([Built with AI](../welcome/built-with-ai.md)). The output is an editor's judgment, and
judgments vary. Review a cut before showing it at a family party.

**Does it work on Apple Silicon?**

Yes. A native install can use VideoToolbox for encoding and supported local model services.
Docker on macOS runs in a Linux VM and does not expose VideoToolbox or Metal to the app. See
[Apple Silicon](../deploy/hardware/apple-silicon.md).
