---
date: 2026-09-12
status: anonymous host measurements; full deployment matrix incomplete
---

# Host performance comparison

A bounded monthly benchmark requested a 60-second memory and prepared 1,440 pictures.
The following numbers cover selection, with no rendering or music. Model files were already
staged. Cold means fresh previews/pixel facts; installation, model download, image pull and
pod scheduling are excluded. NAS time covers container lifetime; other times cover the CLI.

| Host / mode | First or cold | Repeat | Condition |
| --- | ---: | ---: | --- |
| Workstation, rules + metadata | 55.4 s | 1.4 s | Fresh previews and pixels |
| Celeron J4125 NAS, rules + metadata | 279.0 s | 11.1 s | Fresh previews and pixels |
| Kubernetes, rules + metadata | 65.2 s | 2.1 s | Fresh previews and pixels |
| Kubernetes, rules + classifiers | 301.0 s | Not a matched cold/warm pair | Fresh classifier facts |
| Kubernetes, remote LAN reader | 985.7 s | Not measured | Warm facts, some prior reader cache |
| NAS, remote LAN reader | 1,512.5 s | Not measured | Detector backfill, other facts warm; context differed |

Single observations with different cache and context conditions are not a hardware ranking.
The [aggregate data](2026-09-12-phase5-readiness.data.json) intentionally excludes source dates,
people counts, filenames, content descriptions and machine identifiers.

## Where the time goes

| Fresh preparation stage | Workstation | NAS | Kubernetes |
| --- | ---: | ---: | ---: |
| Preview access | 34.67 s | 19.88 s | 8.53 s |
| Pixel facts | 13.52 s | 190.40 s | 42.11 s |

The separate cold Kubernetes classifier run spent 61.13 seconds on the encoder/heads and
172.06 seconds on the detectors. Stage sums exclude discovery and lifecycle overhead.

The NAS external-reader run spent 1,278.2 of 1,512.5 seconds in reading/selection and logged
198 successful reader responses. Offloading the model does not remove the caller's wait for
those responses. `no_captions` is not a no-inference setting. The cluster external-reader run
logged 188 responses; it is not directly comparable because context and reader banks differed.

## GPU execution evidence

An isolated NVIDIA T1000 8 GB pod demonstrated CUDA DINO inference and a two-second synthetic
720p H.264 NVENC encode at 5.6 times realtime. The original application test used temporary
source/dependency overlays. This proves device availability, not full-film throughput.

A later immutable inference image ran with ONNX Runtime GPU 1.26 and CUDAExecutionProvider,
without PyTorch. Synthetic first/repeat request times, with model files staged beforehand:

| Producer | First request | Repeat |
| --- | ---: | ---: |
| DINO + six heads | 0.961 s | 0.018 s |
| Marqo | 0.299 s | 0.127 s |
| Docling | 1.038 s | 0.035 s |

These timings exclude image pulls and model downloads and do not measure classification quality.
Temporary benchmark pods were removed. Container layers and disk-backed `emptyDir` use node-local
ephemeral storage; a PVC uses its selected storage class. A GPU reservation does not change that.

The [capability matrix](2026-09-12-capability-matrix.md) covers all products on the workstation.
The complete product-by-host render and hosted-cost matrix remains unmeasured.
