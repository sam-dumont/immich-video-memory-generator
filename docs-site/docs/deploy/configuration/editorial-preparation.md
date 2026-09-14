---
sidebar_position: 6
title: Editorial annotation setup
---

# Editorial annotation setup

The reference for every pin, digest and contract behind preparation. For the stand-up in order,
read the [self-hosting guide](../self-hosting.md); for what each tier costs and loses,
[Running modes](../running-modes.md).

Story-first selection prepares descriptions, context labels and pixel measurements for the whole
period, reuses complete facts from the annotation database, and stops with a count per missing
producer when something the tier demanded is unavailable. A producer the tier does not ask for is
never missing. New runs use the FAMILY audience: when the evidence identifies them, eight findings
are held out at every audience (breastfeeding, bathing, toileting or changing, intimate hygiene,
graphic medical procedures, identifying records, sexual content, adult changing). A shirtless baby
is ordinary family content.

## Install

```bash
pip install "immich-memories[editorial]"      # ONNX Runtime and Hugging Face Hub, nothing else
pip install "immich-memories[editorial-cuda]" # instead of editorial, on a CUDA host
```

`all` and `all-mac` include `editorial`. Never install `editorial` and `editorial-cuda` together:
`onnxruntime-gpu` already contains the CPU provider, the two distributions own the same import
name, and whichever pip wrote last is the one that answers.

The card is for the encoder, its six heads and both detectors. All three are ONNX sessions and all
three open on whatever provider ONNX Runtime has, so `editorial-cuda` puts every seat on the card
and plain `editorial` puts every seat on the CPU. The same holds inside the CUDA inference image.

## Configuration

```yaml
advanced:
  triage:
    encoder: ~/.immich-memories/models/triage/dinov2-small.onnx
    encoder_url: https://github.com/sam-dumont/immich-video-memory-generator/releases/download/models-v1/dinov2-small-478164cd.onnx
  editorial:
    reader: auto             # auto | model | rules (auto = rules when llm.model is blank)
    annotation_database: ""  # annotations.sqlite in the cache directory
    preparation:
      tier: full             # full | no_captions | metadata_only
      caption_base_url: http://localhost:8092/v1
      caption_api_key: ""    # bearer token, if the server asks for one
      caption_timeout_seconds: 90
      caption_concurrency: 4
      batch_size: 32
      head_bundle: ""        # the packaged public six-head bundle
      detector_python: ""    # a separate detector environment, if you want one
      detector_cache_dir: "" # the Hugging Face cache
      marqo_onnx: ~/.immich-memories/models/detectors/nsfw-marqo-384.onnx
      marqo_onnx_url: https://github.com/sam-dumont/immich-video-memory-generator/releases/download/models-v1/nsfw-marqo-384-924658f1.onnx
      allow_model_downloads: false
```

Producer versions live in `editorial.description_model`, `editorial.head_versions` and
`editorial.pixel_producer_key`. Changing a version names a different fact generation; it does not
teach the preparer how to produce it.

## The encoder and the heads

`immich-memories models fetch` downloads `triage.encoder_url` to a temporary file, hashes it, and
only then renames it into `triage.encoder`. Expected SHA-256:

```text
478164cd290ee78e5ddb4fcc474136eec714b4b8253a3609cc7164b592e958af
```

The export is `facebook/dinov2-small@ed25f3a31f01632728cabb09d1542f84ab7b0056`, 88 MB. The digest
is checked at every run and no other ONNX conversion passes. The wheel includes the head bundle
`public-6heads-v3.npz` (four base heads on `public-v1`, venue and swim on `oi-v3`): public
training coefficients, no library photographs, no owner-trained heads.

## Detectors

Two ONNX graphs, opened on the same provider as the encoder. The worker reads previews locally and
commits each batch; nothing is uploaded to Hugging Face.

| Producer | Artifact | Pinned by | Facts |
|---|---|---|---|
| `nsfw_marqo` | a 22.5 MB ONNX export of `Marqo/nsfw-image-detection-384@0c26ec22111b83f106d72a55f611ec35962bcb65` | `marqo_onnx`, SHA-256 `924658f1ac638d96e9126ecb29de047dc8d31c9c9defcab77a26a5c96ed69e11`, checked on load | `det-v2` |
| `doc_docling` | `model.onnx` from `docling-project/DocumentFigureClassifier-v2.0` | revision `2a12e02668b98ca40216eab41cdf19530577cba4` in the Hugging Face cache | `det-v2` |

`models fetch` supplies both (`--no-detectors` fetches only the encoder), so `allow_model_downloads`
can stay `false`. With it `true`, the worker acquires the Docling snapshot itself; the
sensitive-content export is never fetched from inside a run. `preflight` checks both digests. A
producer that cannot load says which model is missing and what supplies it:

```text
nsfw_marqo has no model: Marqo/nsfw-image-detection-384@0c26ec22/onnx-384 is not at
~/.immich-memories/models/detectors/nsfw-marqo-384.onnx. Run `immich-memories models fetch`
to download it, or point advanced.editorial.preparation.marqo_onnx at your copy of the export.
```

Both detectors produce `det-v2`. For Docling, the default ONNX layout optimizer produced wrong
labels on a Celeron J4125; the extended-optimization graph is what `det-v2` names, and it is
portable across that CPU and arm64. For `nsfw_marqo`, the ONNX export replaced a timm model under
torch: a different artifact is a different producer. It is not faster (0.469 s a picture against
torch's 0.440 s on that CPU); it removes 920 MB of dependencies and 11 s of start-up from a CPU
image. Saved `det-v1` facts migrate on load and are recomputed on the next run.

A separate `detector_python` needs `onnxruntime`, `huggingface-hub`, `numpy` and `Pillow`, and no
part of the torch family.

## Captions

The endpoint at `caption_base_url` must advertise `smolvlm2-500m-base-public` at `/models` and
serve one of the two accepted artifacts:

| Format | Repository | Revision | Digest |
|---|---|---|---|
| MLX, Apple Silicon | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` | `fa57db46815177fbdfd65cc85a2b3416a8332268` | `a9839c8f79ecc93e54a00dc73cc0e68ba477debcd065d50c1c289fbb1075f981` |
| GGUF Q8_0, llama.cpp | `ggml-org/SmolVLM2-500M-Video-Instruct-GGUF` | `ccd7aae53bcb1997355c2f094959e72b3642ce17` | `6f67b8036b2469fcd71728702720c6b51aebd759b78137a8120733b4d66438bc` plus `921dc7e259f308e5b027111fa185efcbf33db13f6e35749ddf7f5cdb60ef520b` for the projector |

Copy-paste recipes for both, plus a compose profile and a Kubernetes overlay, are on
[Caption server](../installation/caption-server.md).

The app enforces the alias and three synthetic schema controls before it sends a library preview.
It records nothing about which of the two answered: the bank keys on
`smolvlm2-500m-base-public@envelope-v3-compact`, which carries no format and no digest. Swapping
one server for the other therefore re-captions nothing and refuses nothing. The revisions above
record what the banked descriptions came from, and the two builds word `setting` differently, so a
library captioned by both holds a mix with nothing marking the seam. The server must accept the
compact description/setting JSON schema at temperature zero, repetition penalty 1.1 and a 140-token
cap. Captions are sent as 400 px JPEG tiles at quality 90; pixel measurements keep their own
`pixel-facts-v1` quality-85 recipe. Two invalid completions produce a banked `caption unavailable`
outcome; timeouts, missing models and transport failures stay incomplete and a later run resumes
them. On `no_captions` and `metadata_only` none of this applies: no endpoint is contacted and an
absent description is not a missing fact.

A server behind a bearer token gets one from `caption_api_key`. It travels as
`Authorization: Bearer <key>` on the `/models` probe and on every completion; left blank, no such
header is sent, which is what an unauthenticated server on localhost should see. The reader's
`llm.api_key` is never borrowed for this: same machine, different endpoint, and a captioner
pointed at a hosted VLM has no business carrying the reader's credential. `title_llm.api_key`
works the same way. A 401 or 403 names `caption_api_key` rather than reporting the endpoint as
unreachable, because the URL is fine and the credential is what is missing.

`caption_api_key: ${MY_KEY}` reads the value out of the environment, so the file can be committed
and the token cannot. An unset variable leaves the key empty rather than the literal `${MY_KEY}`,
which would otherwise be sent as a bearer token and come back as a 401 that looks like the wrong
key. Save writes the `${MY_KEY}` form back either way.

## Editing without a language model

`reader: rules` with `tier: metadata_only` cuts all ten memory types from dates, places,
favourites, known people and whatever facts the tier produced. It builds no story thesis, does
not rerank with a model and does not choose Live Photo motion; custom free-text subjects are
refused. With `no_captions` the classifiers add evidence but not a guaranteed better cut: the
measured season cut became longer while choosing more household objects. Review the result before
sharing it. The measured shares per memory type are on [Running modes](../running-modes.md).

Preflight follows the choice: rules skip the reader, `no_captions` skips the caption alias,
`metadata_only` skips the model files. An explicit `reader: model` without `llm.model` is an error.

## Files and cancellation

The annotation database and its SQLite sidecars are restricted to the current user. Previews are
replaced atomically at mode `0600`; a corrupt preview gets one fresh fetch. Cancellation stops
before the next caption request and terminates the detector worker's process group; committed
facts stay for the next run.
