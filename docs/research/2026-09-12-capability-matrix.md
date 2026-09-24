---
date: 2026-09-12
status: measured source capabilities; UX acceptance and next complete release remain open
---

# Measured reader capabilities

This report contains anonymous benchmark measurements and general quality findings. Capture
dates, personal scenes, people context, private run identifiers and infrastructure names are
excluded. These are observations from one benchmark cohort, not expected performance for every library.

All ten standard products and two allocation controls produced repeatable rules selections.
The full comparison comprises 72 successful CLI invocations across the metadata profile and
two classifier generations, under two hash seeds. None made a language-model request.
Ten metadata-profile films passed complete video and audio decoding. These results establish
working paths and repeatability; they do not establish equal editorial quality or release readiness.

For setup choices, read [Running modes and tradeoffs](../../docs-site/docs/better/measured.md).
The [aggregate data](2026-09-12-capability-matrix.data.json) contains only the public numeric fields.

## Capabilities

| Capability | Rules + metadata | Rules + classifiers | Model reader |
| --- | --- | --- | --- |
| Ten standard memory products | Yes | Yes | Yes |
| Person, album, date and trip constraints | Yes | Yes | Yes |
| Arbitrary semantic custom subject | Refused | Refused | Supported |
| Pixel measurements | Yes | Yes | Depends on preparation tier |
| Image classifiers and detectors | No new facts | Yes | Depends on preparation tier |
| Natural-language interpretation and story thesis | No | No | Yes |
| Model reranking and Live Photo motion observation | No | No | Available |
| Language-model API fee | $0 | $0 | Provider dependent |

Reader choice and preparation tier are independent. `rules` + `metadata_only` requests no
model inference. `rules` + `no_captions` still performs image inference. `model` + `no_captions`
still calls the reader. `rules` + `full` still calls the caption model.

Changing tiers does not erase old facts. The metadata comparison used an empty annotation store;
the classifier comparison excluded prior captions and semantic banks. Reduced evidence does
not prove a picture suitable for sharing. Model decisions are also fallible.

## Selection time, seconds

Workstation runs. First/repeat are actual CLI invocations. The first overlapping route paid
preview and pixel preparation; later routes could reuse it. Classifier runs reused some facts
and rebuilt a changed detector. Historical model references have mixed cache states and are
shown for context, not as the denominator of a speedup claim. No new hosted calls were bought
for this matrix. The two controls test broad-period allocation and a longer content budget.

| Product / anonymous control | Metadata first / repeat | Classifiers first / repeat | Historical model reference |
| --- | ---: | ---: | ---: |
| monthly | 49.2 / 1.8 | 54.5 / 3.4 | 8.3 |
| person | 432.9 / 30.7 | 473.0 / 34.4 | 40.6 |
| multi_person | 32.1 / 31.4 | 35.8 / 34.8 | 282.7 |
| special_day | 5.5 / 0.7 | 6.1 / 1.8 | 191.9 |
| trip | 15.9 / 1.8 | 12.0 / 2.7 | 645.7 |
| year | 39.3 / 1.9 | 35.0 / 3.0 | 1680.6 |
| season | 18.1 / 1.1 | 16.9 / 2.1 | 1507.0 |
| holiday | 17.8 / 1.3 | 17.5 / 2.3 | 1132.7 |
| on_this_day | 12.8 / 1.9 | 12.1 / 2.9 | 540.7 |
| album | 0.8 / 0.8 | 3.1 / 1.6 | 101.4 |
| broad_month_control | 4.9 / 1.8 | 6.0 / 2.8 | 4.3 |
| long_month_control | 2.0 / 2.0 | 3.2 / 3.2 | 33.1 |

## Quality and coverage

| Product / control | Metadata clips / content seconds | Classifier clips / content seconds | Known reference occasions retained, metadata / classifiers |
| --- | ---: | ---: | ---: |
| monthly | 15 / 52.5 | 15 / 52.5 | 62% / 62% |
| person | 22 / 82.5 | 22 / 82.5 | 94% / 94% |
| multi_person | 22 / 82.4 | 22 / 82.4 | 86% / 86% |
| special_day | 10 / 54.0 | 7 / 40.0 | 100% / 100% |
| trip | 28 / 115.9 | 32 / 122.4 | 67% / 78% |
| year | 32 / 128.7 | 37 / 142.1 | 43% / 49% |
| season | 18 / 74.9 | 33 / 127.0 | 45% / 30% |
| holiday | 13 / 52.2 | 13 / 52.2 | 46% / 55% |
| on_this_day | 8 / 34.0 | 10 / 37.5 | 100% / 100% |
| album | 10 / 37.5 | 10 / 37.5 | 100% / 100% |
| broad_month_control | 15 / 52.5 | 15 / 52.5 | 31% / 31% |
| long_month_control | 45 / 172.5 | 45 / 172.5 | 65% / 70% |

Occasion retention is a **lower bound** when the reference inventory is incomplete. A selected
member proves an occasion survived; missing inventory membership does not prove it was lost.
An alternative image can preserve the same occasion. Exact asset overlap is recorded separately
in the data and is not a quality score. The historical model edit is a reference, not ground truth.

| Scope | What works with rules | What is lost or unreliable |
| --- | --- | --- |
| Person / multiple people | A subject supplies a theme; repeated appearances give useful continuity. | Some interactions and stronger alternatives disappear; subject focus can drift. |
| Album | Owner curation supplies intent and a bounded source pool. | Near-duplicates can survive; rules cannot infer the intended narrative from arbitrary content. |
| Single event | Time bounds keep the edit coherent. | Classifiers can remove useful participant shots and make an already short cut shorter. |
| Trip | Place and time provide structure. | Metadata may over-select repeated portraits; classifiers can over-correct toward scenery. |
| Month / year / season | Basic chronology and some recurring subjects survive. | Mixed material needs triage. Weak objects can displace meaningful moments, and early periods can consume the budget. |
| Recurring holiday / on this day | Date constraints retrieve relevant occasions. | Filling the cut can add weak alternatives; date-range presentation needs UX work. |

The strongest fast-mode candidates are **prefiltered person, album, event and trip requests**.
Broad recaps benefit most from the intelligence layer's interpretation and triage. Classifiers
are not a proven automatic quality upgrade: one seasonal classifier cut filled the target but
was judged less focused than its shorter metadata counterpart. A longer monthly budget improved
coverage but did not recover every preferred moment. Temporal allocation can also be improved
without an LLM; these measurements do not prove every loss is inherent to rules.

Both reduced profiles selected some material excluded by the reference model. That is evidence
to review, not proof that every old model judgement was correct. Equal audience judgement,
complete occasion recall and unattended sharing have not been demonstrated.

## Whole films, seconds

Warm preparation cache, workstation, 1080p H.265 with bundled music. Whole CLI includes
discovery, source transfer/conversion, titles, encoding and audio. Other local work overlapped
some runs. These are not isolated throughput or cold end-to-end measurements.

| Product | Whole CLI | Finished film | Model API fee |
| --- | ---: | ---: | ---: |
| monthly | 104.5 | 54.5 | $0 |
| person | 191.3 | 89.0 | $0 |
| multi_person | 230.9 | 89.0 | $0 |
| special_day | 126.5 | 57.5 | $0 |
| trip | 287.7 | 115.8 | $0 |
| year | 184.6 | 140.6 | $0 |
| season | 168.4 | 75.8 | $0 |
| holiday | 124.2 | 57.7 | $0 |
| on_this_day | 56.9 | 38.5 | $0 |
| album | 71.1 | 41.5 | $0 |

All ten films decoded completely and preserved the ordered selections under review. Rendering
adds titles and overlaps transitions, so film duration differs from selected content duration.

## Other hosts, costs and remaining measurements

- [Host comparison](2026-09-12-phase5-readiness.md): workstation, Celeron NAS, Kubernetes,
  preparation stages, external reading and the limits of the GPU smoke test.
- [Cost and detector notes](2026-09-12-deployment-costs.md): hosted token cost and the NAS
  detector correction.
- All rules rows have $0 in model API fees. Electricity, hardware and storage costs were not metered.
- A historical hosted reader sample took 537.4 seconds and about $0.256 in recorded token cost.
  Other hosted product costs, a full cold caption tier and the full product-by-host matrix remain unmeasured.

UX acceptance remains open. The standalone classifier service is implemented, but the app does
not yet consume its remote facts. Do not describe that topology as working end to end.
