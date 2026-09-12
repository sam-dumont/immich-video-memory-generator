---
sidebar_position: 6
title: Editorial annotation setup
---

# Editorial annotation setup

This page is the reference: every pin, digest and contract. If you are setting the stack up for
the first time, the [self-hosting guide](../self-hosting.md) puts the same pieces in order.

Story-first selection prepares descriptions, context labels and pixel measurements for the
whole source period. It reuses complete facts from the annotation database. Missing previews,
unavailable providers and incomplete facts stop selection with a count for each missing producer.

This is the default route for UI, CLI and scheduled runs. New runs use the FAMILY audience.
A shirtless baby is ordinary family content. Eight findings are held out of the cut at every
audience: breastfeeding or expressing milk, bathing, toileting or changing, intimate hygiene,
graphic medical procedures, identifying records, sexual content, and adult changing.

Install the inference dependencies:

```bash
pip install "immich-memories[editorial]"
# From a checkout:
uv sync --extra editorial
```

The `all` and `all-mac` extras include `editorial`. This installs ONNX Runtime and Hugging Face
Hub, and nothing else: every model seat here is an ONNX graph, so the extra is torch-free and a
CPU install resolves no `nvidia-*` wheel at all. Model weights and the caption server need
separate setup.

On a CUDA host, install `editorial-cuda` **instead of** `editorial`. It is the same two seats
against `onnxruntime-gpu`, which is the only build that carries the CUDA execution provider:

```bash
pip install "immich-memories[editorial-cuda]"
```

Never install both. `onnxruntime-gpu` already contains the CPU provider, and the two
distributions own the same import name, so `all` and `all-mac` deliberately keep the CPU one.

## Configuration

```yaml
advanced:
  triage:
    encoder: ~/.immich-memories/models/triage/dinov2-small.onnx
    encoder_url: https://github.com/sam-dumont/immich-video-memory-generator/releases/download/models-v1/dinov2-small-478164cd.onnx
  editorial:
    annotation_database: ""  # defaults to annotations.sqlite inside the cache directory
    preparation:
      caption_base_url: http://localhost:8092/v1
      caption_timeout_seconds: 90
      caption_concurrency: 4
      batch_size: 32
      head_bundle: ""        # packaged public six-head bundle
      detector_python: ""    # current Python; may point to a separate detector environment
      detector_cache_dir: "" # normal Hugging Face Hub cache
      marqo_onnx: ~/.immich-memories/models/detectors/nsfw-marqo-384.onnx
      marqo_onnx_url: https://github.com/sam-dumont/immich-video-memory-generator/releases/download/models-v1/nsfw-marqo-384-924658f1.onnx
      allow_model_downloads: false
```

The producer versions remain in `editorial.description_model`, `editorial.head_versions` and
`editorial.pixel_producer_key`. Changing a version names a different fact generation; it does
not teach the preparer how to produce it. The packaged producers fill the current defaults.

## Public context heads

The wheel includes `public-6heads-v3.npz` and its provenance notice. Its four base heads use
`public-v1`; venue and swim use `oi-v3`. It contains public training coefficients, no library
photographs or owner-trained heads.

Fetch the pinned 88 MB DINOv2-small ONNX export, or point `triage.encoder` at an existing copy:

```bash
immich-memories models fetch
```

The command downloads `triage.encoder_url` to a temporary file, hashes it, and only then renames
it into `triage.encoder`; a download that does not match the pin leaves nothing behind. An export
that is already correct is a no-op, and `--force` re-downloads it. Its expected SHA-256 is:

```text
478164cd290ee78e5ddb4fcc474136eec714b4b8253a3609cc7164b592e958af
```

The encoder revision is `facebook/dinov2-small@ed25f3a31f01632728cabb09d1542f84ab7b0056`.
The package verifies the export digest and its match with the head bundle. The export is not
bundled, downloaded automatically, or interchangeable with another ONNX conversion. An
installation without this exact export still needs that artifact before preparing new facts.

## Detectors

Two CPU detectors, both ONNX graphs on ONNX Runtime's CPU provider. The worker reads previews
locally and commits each completed batch. It does not upload images to Hugging Face.

| Producer | Artifact | How it is pinned | Facts |
| --- | --- | --- | --- |
| `nsfw_marqo` | a 22.5 MB single-file ONNX export of `Marqo/nsfw-image-detection-384@0c26ec22111b83f106d72a55f611ec35962bcb65` | `marqo_onnx`, SHA-256 `924658f1ac638d96e9126ecb29de047dc8d31c9c9defcab77a26a5c96ed69e11`, checked on load | `det-v2` |
| `doc_docling` | `model.onnx` from `docling-project/DocumentFigureClassifier-v2.0` | revision `2a12e02668b98ca40216eab41cdf19530577cba4` in the Hugging Face cache | `det-v1` |

`immich-memories models fetch` supplies both in one command: it downloads `marqo_onnx_url` to a
temporary file, hashes it and only then renames it into `marqo_onnx`, and it warms the Docling
snapshot at its pinned revision into `detector_cache_dir` when that is set — so
`allow_model_downloads` can stay `false` and mean what it says. `--no-detectors` fetches only the
encoder. Setting `allow_model_downloads: true` instead lets the worker acquire the Docling
snapshot itself; the sensitive-content export is never fetched from inside a run. Neither starts
a caption server.

`immich-memories preflight` checks both digest-pinned exports, so a missing model is named before
a cut starts rather than after preparation has read every picture. A producer that cannot load
says which model is missing and what would supply it, at load:

```text
nsfw_marqo has no model: Marqo/nsfw-image-detection-384@0c26ec22/onnx-384 is not at
~/.immich-memories/models/detectors/nsfw-marqo-384.onnx. Run `immich-memories models fetch`
to download it, or point advanced.editorial.preparation.marqo_onnx at your copy of the export.
```

**Why `nsfw_marqo` produces `det-v2`.** It used to be a timm vision transformer under torch. A
different artifact is a different producer, so the ONNX export carries its own fact version and
re-derives. It is not a speed change: measured on a Celeron J4125 the ONNX graph runs at 0.469 s
a picture against torch's 0.440 s. What it removes is 920 MB of dependencies from a CPU image,
11 s of interpreter start-up before the first detector picture, and the class of failure where
`torch` and `torchvision` resolve from different indexes and the detector dies on import.

A separate `detector_python` needs `onnxruntime`, `huggingface-hub`, `numpy` and `Pillow` — and
no part of the torch family. The worker ships with the main package and runs without importing
the app's UI or configuration dependencies. The versions `uv.lock` currently pins are Hugging
Face Hub 1.30.0 and ONNX Runtime 1.28.0; read the lock rather than this sentence if they matter
to you.

## Compact captions

The configured OpenAI-compatible endpoint must advertise `smolvlm2-500m-base-public` at
`/models` and serve the accepted public SmolVLM2 generation:

| Artifact | Value |
| --- | --- |
| Repository | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` |
| Revision | `fa57db46815177fbdfd65cc85a2b3416a8332268` |
| Weights SHA-256 | `a9839c8f79ecc93e54a00dc73cc0e68ba477debcd065d50c1c289fbb1075f981` |

The caption server is a separate service; installing the extra does not start it. It must
accept the compact description/setting JSON schema, temperature zero, repetition penalty
1.1 and a 140-token output limit. Preparation checks the model inventory and three synthetic
schema controls before sending library previews. The client verifies the advertised alias
and response contract; the server operator must supply the pinned model weights.

Captions use 400 px JPEG tiles at quality 90. Pixel measurements retain their separate
`pixel-facts-v1` quality-85 recipe. Neither recipe silently changes with a caption model setting.

Two actual invalid model completions can produce a recorded `caption unavailable` outcome,
bound to the preview and exact requests. These are counted separately from descriptions.
Timeouts, missing models and transport failures remain incomplete work. A later run reuses
completed facts and resumes missing work.

## Files and cancellation

The annotation database and its SQLite sidecars are restricted to the current user. Downloaded
previews are replaced atomically at mode `0600`; a corrupt preview gets one fresh fetch attempt.
Existing owner flags and older producer generations remain in the database.

Cancellation stops before the next caption request and terminates the detector worker's
process group. Already committed facts remain available to the next run. Worker input,
progress and error files live in a private temporary directory that is removed on completion,
failure or cancellation.
