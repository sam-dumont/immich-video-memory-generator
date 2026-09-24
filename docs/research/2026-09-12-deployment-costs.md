---
date: 2026-09-12
status: anonymous measurements and explicit gaps
---

# Reading cost and NAS detector correction

## Local versus hosted reading

One monthly selection with warm picture facts took 1,451.9 seconds with a local Qwen reader
and 537.4 seconds with hosted GPT-4.1 mini. Recorded hosted token usage was approximately
**$0.256**. That is a benchmark estimate, not an invoice or a current price quote. Reader
cache states were mixed, so these runs do not establish a controlled provider speed ratio.
The same output duration does not imply the same picture choices or equal quality.

Rules with metadata or local classifiers incur **$0 in language-model API fees**. Local model
execution also has no provider fee, but all modes use hardware and electricity. Those costs
were not metered. For a measured run, electricity is average watts × wall seconds / 3,600,000
× price per kWh; add hardware and storage separately. Do not extrapolate the one hosted monthly
price across albums, trips and year recaps.

Remote readers can receive annotation text and selected image tiles. A LAN server is still
another host. Caption servers receive picture tiles for every newly requested caption.
Choose endpoints and consent deliberately; changing the render GPU does not change these payloads.

See [running modes](../../docs-site/docs/being-rewritten/running-modes.md),
[host measurements](2026-09-12-phase5-readiness.md), and the
[anonymous cost data](2026-09-12-deployment-costs.data.json).

## Follow-up fix: Docling on the NAS CPU

The Celeron J4125's default ONNX layout optimizer collapsed distinct inputs to nearly identical
low-confidence table labels. A synthetic reproducer isolated the failure without library images:

| Input | Default optimizer | Extended optimizer |
| --- | --- | --- |
| White | table, 0.052955 | table, 0.862082 |
| Black | table, 0.052960 | table, 0.943777 |
| Seeded RGB noise | table, 0.052950 | other, 0.798308 |

Docling now uses `ORT_ENABLE_EXTENDED` and a new `det-v2` fact generation. Saved old settings
migrate; the next preparation rebuilds the affected facts and dependent readings. No source or
audience gate was loosened. The same synthetic regression passed on the NAS and workstation.

This is separate from the Marqo ONNX conversion, which removes PyTorch and its dependency
weight. Matched-thread NAS inference was 0.469 seconds per picture with ONNX versus 0.440 with
PyTorch: the evidence supports a packaging and startup improvement, not faster inference.

Full cold caption preparation, hosted cost for every product, and full NAS/cluster film
throughput remain open measurements. The published benchmark contains no personal scene review.
