---
date: 2026-08-30
status: verified
issue: none-yet
---

# Fast visual-analysis lane — licensing verdict

Companion to [2026-08-30-bulk-visual-analysis-alternatives.md](2026-08-30-bulk-visual-analysis-alternatives.md).
Context: this repo is MIT, self-hosted by its users. Verified against primary sources
(LICENSE files, model cards, HF metadata), 2026-08-30. **Verdict: the whole planned stack is
permissive — no non-commercial component anywhere.**

## Component table

| Component | License | Covers | MIT-compatible | Source |
|---|---|---|---|---|
| DINOv2 ViT-S/14 (code + `dinov2_vits14` weights) | Apache-2.0 | both | yes | github.com/facebookresearch/dinov2 LICENSE; README states "code and model weights are released under the Apache License 2.0"; huggingface.co/facebook/dinov2-small |
| YuNet `face_detection_yunet_2023mar.onnx` | MIT (© Shiqi Yu, 2020) | both | yes | github.com/opencv/opencv_zoo `models/face_detection_yunet/LICENSE` |
| opencv-python / OpenCV core | MIT (wrapper) / Apache-2.0 (core) | code | yes | opencv-python LICENSE.txt; opencv LICENSE |
| mediapipe pip pkg + `face_landmarker.task` | Apache-2.0 | both | yes | PyPI mediapipe; google-ai-edge/mediapipe LICENSE; FaceMesh V2 + BlazeFace model cards |
| SigLIP 2 (`google/siglip2-*`) + onnx-community conversions | Apache-2.0 | weights | yes | HF `cardData.license: apache-2.0`; ONNX conversions inherit |
| eDifFIQA(T) `ediffiqa_tiny_jun2024.onnx` (OpenCV Zoo) | CC-BY-4.0 | both | yes (attribution) | opencv_zoo `models/face_image_quality_assessment_ediffiqa/LICENSE` |
| torch / scikit-learn / numpy | BSD-3-Clause | code | yes | upstream LICENSE files |

## Hard rules

1. **DINOv2 pin date.** The repo was CC-BY-NC-4.0 until the 2023-08-31 relicensing
   (commit `81b2b64`, "Update license everywhere #182"; the NC text is visible at commit
   `fc49f49`). Never pin `torch.hub.load` / vendored checkpoints to anything older.
2. **InsightFace `buffalo_l` stays excluded.** Code MIT, but its README restricts the
   training data and models trained on it to non-commercial research; commercial use needs
   a contract. An MIT repo cannot pass those weights downstream. Do not re-add.
3. **eDifFIQA channel matters.** The OpenCV Zoo ONNX release is CC-BY-4.0 (attribution
   required); the separate `LSIbabnikz/eDifFIQA` training-code repo is MIT. If we ship the
   (T) ONNX from OpenCV Zoo, CC-BY-4.0 governs.
4. **The trained pairwise head is unencumbered.** A logistic/PCA head over frozen DINOv2
   embeddings is not a Derivative Work of DINOv2 under Apache-2.0's definition — it ships
   under this repo's MIT license with no extra obligation. Obligations attach only to
   redistributing the DINOv2 code/weights themselves.

## Obligations by distribution mode

- **Git repo / pip package** (code + model names/URLs, no weight bytes): no redistribution
  occurs → no legal obligation triggers. Still add entries to `THIRD_PARTY_NOTICES`
  (existing root file, follow its section style) for DINOv2, YuNet, mediapipe, SigLIP 2,
  eDifFIQA(T) — transparency.
- **Runtime first-run download** (current self-hosted behavior): the user's machine pulls
  directly from Meta/Google/opencv_zoo/HF → no obligation on this project. Link the same
  NOTICE entries from docs.
- **Docker image, only if weights ever get baked at build time** (today `docker/Dockerfile`
  bakes none): that is real distribution. Checklist for that PR:
  - [ ] `COPY THIRD_PARTY_NOTICES` into the final stage
  - [ ] add `third_party_licenses/apache-2.0.txt` (full text — covers DINOv2, mediapipe, SigLIP 2)
  - [ ] add `third_party_licenses/cc-by-4.0.txt` (full text — covers eDifFIQA(T))
  - [ ] flag any format-converted weight files in the NOTICE entry (Apache-2.0 §4(b))

## When the components land in code

Extend `THIRD_PARTY_NOTICES` in the same PR that wires each component — not later.
