# What the Pass Costs

> **Two conclusions on this page were reversed the same day they were drawn.**
> The measurements are sound; two of the inferences from them were not. §3 was
> shipped and reverted — dropping the written reason halves the call and makes
> the model materially worse. §6's projection did not survive whole-run
> measurement. Both reversals are inline, but if you are reading this page for a
> saving, read `docs/designs/2026-08-27-the-annotation-layer.md` §6b first: it
> lists what was measured, rejected, and why.

> Measured 2026-08-27 against the real library and the production path, on the
> owner's local single-stream endpoint at temperature 0. Every number comes from
> banked answers or timed live calls, not from a model of the model.

Stage B asks a question per pair of neighbours, in two arrangements. On the
dense month that gated at 1312 calls and roughly 35 minutes of wall clock — for
one month, uncached. This page records where that time actually goes, because
the two obvious answers are both wrong.

---

## 1. The endpoint is local, so concurrency is not the lever

One model, one stream. Parallelising the gateway would queue, not overlap. That
removes the usual first answer and makes every remaining option a question of
either **fewer calls** or **cheaper calls**.

## 2. Pixels are not the bill

| tile px | median call | agrees with the 400px answer |
|---|---|---|
| 400 | 1.19s | 30/30 |
| 300 | 1.16s | 29/30 |
| 250 | 1.01s | 29/30 |
| 200 | 1.06s | 28/30 |

Quartering the pixel area moved the median 11% and started moving the answer.
The vision encoder resizes the sheet to its own grid, so the tile size the pass
declares changes what the model can *see* without changing what it *costs*.

**400px stays.** It was chosen on a fidelity measurement and there is no cost
argument against it.

## 3. The written reason is half the runtime — and buying it back

> **REVERSED 2026-08-27, same day.** Everything measured below is true and the
> conclusion drawn from it was wrong. Dropping the written reason does not just
> fail to pay; it makes the model materially worse at the question. The pass is
> back on a reason-carrying contract (`pair-v3`). Read §3a before §3.

### 3a. What the 30-pair sample could not see

| | |
|---|---|
| pairs judged under **both** contracts | 650 |
| agree | **514 (79%)** |
| v1 "same" → v2 **"different"** | **126** |
| v1 "different" → v2 "same" | 10 |

One-directional, not noise. Two of the flipped pairs were looked at: one is a
woman holding a newborn in the same chair, same pose, seconds apart; the other
is the same baby in the same outfit on the same lap. Both are plainly one
picture. `pair-v1` said same in both arrangements; `pair-v2` says different.

**Writing the reason is not overhead on the answer — on this model it is part of
how the answer is arrived at.** The 29/30 agreement was real and 30 pairs was
too small to see a 21% disagreement rate.

Two things this also explains:

- The gate's survival moving 27% → 35% was reported as "more conservative,
  which is the safe direction". It is not conservatism, it is **failure to merge
  genuine duplicates**.
- The cheap band collapsing to zero at every hash resolution was read as the
  hash being too coarse. The recurring counterexample survives every resolution
  because it is **a model error, not a hash collision** — and a band required to
  be unanimous against a noisy ground truth can never open.



| variant | median call | characters written | agrees with the banked answer |
|---|---|---|---|
| verdict + reason, 4000 cap | 1.06s | 484 | 30/30 |
| **verdict only, 4000 cap** | **0.51s** | **49** | 29/30 — **but see below** |
| verdict only, 64 cap | 0.49s | 49 | 18/18 — **12 of 30 calls failed** |

The prose is 484 of 533 characters and half the wall clock.

**It does move the verdict, and 30 pairs was too small a sample to see it.**
Re-gated on the full dense month, the same corpus absorbed 502 frames with the
reason and **388 without** — 23% fewer. Survival went 27% → 35% (sparse, 65% →
73%). Both months still sit inside the 25–50% band, and the new answer is the
more conservative one, which is the safe direction under this project's
asymmetry. But "agrees 29/30" was a statement about a sample, and it was
reported as though it were a statement about the pass.

The third row is a trap of the kind already catalogued here: it reads as the
fastest variant because its failures left the sample. A 64-token cap truncates
mid-JSON and the answer never arrives. **The cap stays at 4000**; generation
stops on its own after ~49 characters.

### What replaces the reason

Murch's rule is real — "avoid writing 'NG' without saying why… what is bad when
you first see it may be exactly what you want two months later" — and it is met
by evidence rather than prose. Every pair records its exact sheet hash, both
arrangements' verdicts and the run it built, so the decision is reopened by
looking at the two tiles that caused it.

That is the stronger record *here* specifically, because of what this project
already measured: every failed question shape returned fluent, specific,
grounded-sounding reasons for answers that were following tile position. A
stored sentence from this model is not evidence that the model looked.

## 4. Short-circuiting the second arrangement

Only the intersection absorbs, so `forward and backward` cannot become true once
`forward` is false. The second arrangement of a pair already called different
changes no outcome.

Measured on the dense month: **121 of 1312 calls, 9.2%**. Exact, not estimated —
the pair is kept whole either way.

## 5. What the two arrangements are worth

| | dense month |
|---|---|
| pairs | 656 |
| swap agreement | **616/656 = 0.939** |
| both arrangements said same | 510 |
| first said different | 121 |

The 40 disagreeing pairs are kept, which is the point. Dropping the second
arrangement would save a further 45% of calls and absorb those 40 on a single
arrangement's word — pushing the dense month's survival under the 25% floor and
removing the only check that the answer is not order-sensitive. Not taken.

## 6. Where the dense month lands

Model time only; the rest of a run is pipeline overhead shared with Pass 0.

| | calls | isolated model time |
|---|---|---|
| as first gated | 1312 | 23 min |
| \+ short-circuit | 1191 | 21 min |
| \+ verdict only | 1191 | 10 min |

**Corrected 2026-08-27 after the second gate ran.** That last row was a
projection from isolated call timing and the whole-run measurement does not
support it. Both months re-gated live on `pair-v2`:

| | calls | wall clock |
|---|---|---|
| v1, reason + both arrangements | 1660 | 51 min |
| v2, verdict only + short-circuit | 1381 | **47 min** |

**8% of wall clock for 17% fewer calls.** The per-call time did not measurably
improve in the real run, though `gateway.ask` measured 1.06s → 0.51s in
isolation. Either per-call overhead outside the model dominates, or both gate
runs were contended — test suites and quality gates were run on the same
machine during each. Not yet separated, and it should be before any further
cost claim is made from a probe.

**The saving that is real is fewer calls, not faster ones.** Dropping the
written reason is still right — it removes tokens nothing read — but it is not
the 2× the isolated timing suggested.

## 7. Probes

| probe | asks | calls |
|---|---|---|
| `probe_cost.py` A | exact per-pair arrangements, from the bank | 0 |
| `probe_cost.py` B | does the answer survive a smaller tile | 120 |
| `probe_tokens.py` | is the written reason the cost | 90 |
