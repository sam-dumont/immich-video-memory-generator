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

This is the default route for UI, CLI and scheduled runs. New runs use the FAMILY audience:
ordinary family baby care is eligible; graphic procedures, sexual content, exposed adult
changing and identifying records remain excluded.

Install the inference dependencies:

```bash
pip install "immich-memories[editorial]"
# From a checkout:
uv sync --extra editorial
```

The `all` and `all-mac` extras include `editorial`. This installs ONNX Runtime, Torch, timm and
Hugging Face Hub. Model weights and the caption server need separate setup.

## Configuration

```yaml
advanced:
  triage:
    encoder: ~/.immich-memories/models/triage/dinov2-small.onnx
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
      allow_model_downloads: false
```

The producer versions remain in `editorial.description_model`, `editorial.head_versions` and
`editorial.pixel_producer_key`. Changing a version names a different fact generation; it does
not teach the preparer how to produce it. The packaged producers fill the current defaults.

## Public context heads

The wheel includes `public-6heads-v3.npz` and its provenance notice. Its four base heads use
`public-v1`; venue and swim use `oi-v3`. It contains public training coefficients, no library
photographs or owner-trained heads.

Place the pinned 88 MB DINOv2-small ONNX export at `triage.encoder`, or point that setting at
an existing copy. Its expected SHA-256 is:

```text
478164cd290ee78e5ddb4fcc474136eec714b4b8253a3609cc7164b592e958af
```

The encoder revision is `facebook/dinov2-small@ed25f3a31f01632728cabb09d1542f84ab7b0056`.
The package verifies the export digest and its match with the head bundle. The export is not
bundled, downloaded automatically, or interchangeable with another ONNX conversion. An
installation without this exact export still needs that artifact before preparing new facts.

## Detectors

Two CPU detectors produce `det-v1` facts. The worker reads previews locally and commits each
completed batch. It does not upload images to Hugging Face.

| Model repository | Pinned revision | Required files |
| --- | --- | --- |
| `Marqo/nsfw-image-detection-384` | `0c26ec22111b83f106d72a55f611ec35962bcb65` | `config.json`, `model.safetensors` |
| `docling-project/DocumentFigureClassifier-v2.0` | `2a12e02668b98ca40216eab41cdf19530577cba4` | `model.onnx` |

By default these files must already be in the Hugging Face Hub cache. Set
`detector_cache_dir` to its cache root if it lives elsewhere. Setting
`allow_model_downloads: true` allows the worker to acquire the pinned files when needed.
It does not download the DINO encoder or start a caption server.

A separate `detector_python` needs `timm`, `torch`, `huggingface-hub`, `onnxruntime`, `numpy`
and `Pillow`. The worker ships with the main package and runs without importing the app's UI
or configuration dependencies. The accepted detector environment used timm 1.0.29, Torch
2.13.0, Hugging Face Hub 1.29.0 and ONNX Runtime 1.29.0.

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
