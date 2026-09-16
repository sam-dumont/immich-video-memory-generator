---
date: 2026-09-16
status: experiment — mechanism verified, steering unproven on the local reader; parked pending phrasing variants and a hosted arm
issue: 996
pr: 1027
---

# House instructions: does free-text operator taste actually steer selection?

> The block perturbs the cut. On the local 30B it does not yet steer it.

## 0. What this measures

#996 ships an operator-authored free-text block appended last to every reader prompt. The standing
rule it deliberately breaks is that constraints belong in structure, not instruction, because small
local models follow structure better than prose. The issue asks that the block be measured before
being trusted. This document is that measurement.

## 1. Setup

- Reader: local `Qwen3-VL-30B-A3B-Instruct-4bit` via `localhost:9999`, `thinking: false`.
- Month: **April 2022**, chosen because it can bite: 3,339 annotated assets, 45 with selfie
  captions — the largest selfie month in the library.
- Target: 60 seconds, `--no-render --no-music`, one instruction delta per arm, prepared banks warm.
- Arms (identical code; the instruction is the only variable):

| arm | instruction |
|---|---|
| A | *(none)* |
| B | `I don't want selfies` |
| C | `I prefer landscapes over people` |

Carrier classes below are a caption heuristic (`selfie` substring; people/landscape word lists),
exact for selfies, directional for the rest.

## 2. Results

| arm | carriers | people | mixed | selfie | overlap with A | contract key | wall time |
|---|---:|---:|---:|---:|---:|---|---:|
| A | 13 | 7 | 6 | 0 | — | `215133e5…` | 27m |
| B | 13 | 7 | 6 | 0 | 6/13 | `cdd773af…` | 17m |
| C | 13 | **8** | 4 | **1** | 7/13 | `19cde4fb…` | 15m |

## 3. Findings

1. **The mechanism is verified.** Each run records its exact instruction bytes under
   `intent.house_instructions`; the three arms produce three distinct contract keys, so the three
   banks cannot poison each other; the block reaches the reader prompts (27 of 65 calls in arm C,
   including weighing, standing and story-pick); blank stays byte-for-byte identical to no-block
   (pinned by test). A variant costs a re-read (15-27 minutes), not a re-prepare.
2. **The block has an effect.** Any two arms share only about half their kept set. It is not a
   no-op on this reader.
3. **The effect is not steerable in the direction asked.** B had nothing to remove: the baseline's
   own priorities picked zero selfies despite 45 in scope, so "I don't want selfies" only rippled
   (7 of 13 picks changed for no visible reason). C actively contradicted: asked for landscapes
   over people it returned *more* people carriers than baseline (8 vs 7) plus a selfie
   (`ab620d25…`) that neither other arm selected.
4. **Cost is real but bounded**: a month re-read on the local 30B ran 15-27 minutes against the
   prepared bank.

## 4. Reading

The reader treats the block as one more voice in a crowded contract, not as an override. Plausible
contributors, untested individually: the built-in `priorities:` line sits inside the product
contract and reads as authoritative structure; the block's rank framing ("these outweigh...") is
prose competing with prose; and a negative instruction ("don't") may be weaker than a positive
preference. The honest summary is the issue's own phrase: a matter of prompting.

## 5. Next arms

1. **Phrasing variants** on the same month and reader: imperative plus explicit target
   ("EXCLUDE selfies from every selection"), positive-only phrasing, and a block that restates the
   rank in the reader's own contract vocabulary.
2. **A hosted reader arm** with the identical three commands; banks are keyed by prompt bytes, so
   results are directly comparable.
3. **A month where the baseline provably picks the targeted class**, so removal can be observed
   rather than inferred.
4. **Structural gates stay the tool for hard exclusions.** Where an owner wants a format or class
   gone (no Live Photos, no screenshots), prose is the wrong instrument even if prompting improves;
   an eligibility gate is checkable code. `--no-live-photos` already works this way.

## 6. Disposition

PR #1027 stays parked, off by default (blank = today's behaviour byte for byte), with the
mechanism, the A/B isolation and the traceability intact. It should not be merged as a trusted
steering surface until arms 1-3 above show a hosted reader (or a rephrased block on the local one)
obeying. All six run attempt directories are retained under
`editorial-runs/all_monthly_highlights_20220401-20220430/attempts/` for re-analysis.